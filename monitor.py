#!/usr/bin/env python3
"""Azure App Registration Secret & Certificate Expiry Monitor.

Queries Microsoft Graph API for all app registrations, checks secrets and
certificates against configurable expiry thresholds, and sends alerts via
Slack and/or email.
"""

import json
import logging
import os
import smtplib
import sys
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from azure.identity import ClientSecretCredential, ManagedIdentityCredential
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


# ---------------------------------------------------------------------------
# Azure auth
# ---------------------------------------------------------------------------

def get_access_token() -> str:
    method = os.getenv("AZURE_AUTH_METHOD", "service_principal").lower()
    scope = "https://graph.microsoft.com/.default"

    if method == "managed_identity":
        credential = ManagedIdentityCredential()
    else:
        tenant = os.environ["AZURE_TENANT_ID"]
        client_id = os.environ["AZURE_CLIENT_ID"]
        client_secret = os.environ["AZURE_CLIENT_SECRET"]
        credential = ClientSecretCredential(tenant, client_id, client_secret)

    token = credential.get_token(scope)
    return token.token


# ---------------------------------------------------------------------------
# Microsoft Graph queries
# ---------------------------------------------------------------------------

def get_applications(token: str) -> list[dict]:
    """Fetch all app registrations (handles paging)."""
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{GRAPH_BASE}/applications?$select=id,displayName,appId,passwordCredentials,keyCredentials&$top=999"
    apps = []

    while url:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        apps.extend(data.get("value", []))
        url = data.get("@odata.nextLink")

    return apps


# ---------------------------------------------------------------------------
# App filtering
# ---------------------------------------------------------------------------

def filter_applications(apps: list[dict]) -> list[dict]:
    """Apply optional include/exclude filters from config."""
    include_ids = _csv_set("FILTER_INCLUDE_APP_IDS")
    include_names = _csv_list("FILTER_INCLUDE_NAMES")
    exclude_ids = _csv_set("FILTER_EXCLUDE_APP_IDS")
    exclude_names = _csv_list("FILTER_EXCLUDE_NAMES")

    filtered = apps

    if include_ids:
        filtered = [a for a in filtered if a.get("appId", "") in include_ids]

    if include_names:
        filtered = [
            a for a in filtered
            if any(p in a.get("displayName", "").lower() for p in include_names)
        ]

    if exclude_ids:
        filtered = [a for a in filtered if a.get("appId", "") not in exclude_ids]

    if exclude_names:
        filtered = [
            a for a in filtered
            if not any(p in a.get("displayName", "").lower() for p in exclude_names)
        ]

    if len(filtered) != len(apps):
        log.info("Filtered to %d app(s) (from %d total)", len(filtered), len(apps))

    return filtered


def _csv_set(env_var: str) -> set[str]:
    raw = os.getenv(env_var, "")
    return {v.strip() for v in raw.split(",") if v.strip()} if raw.strip() else set()


def _csv_list(env_var: str) -> list[str]:
    raw = os.getenv(env_var, "")
    return [v.strip().lower() for v in raw.split(",") if v.strip()] if raw.strip() else []


# ---------------------------------------------------------------------------
# Expiry checking
# ---------------------------------------------------------------------------

def check_expiry(apps: list[dict], threshold_days: list[int]) -> list[dict]:
    """Return a list of expiring/expired credentials."""
    now = datetime.now(timezone.utc)
    max_threshold = max(threshold_days)
    alerts = []

    for app in apps:
        for cred in app.get("passwordCredentials", []):
            end = _parse_date(cred.get("endDateTime"))
            if end is None:
                continue
            days_left = (end - now).days
            if days_left <= max_threshold:
                alerts.append({
                    "app_name": app.get("displayName", "Unknown"),
                    "app_id": app.get("appId", ""),
                    "credential_type": "Secret",
                    "credential_name": cred.get("displayName", "(unnamed)"),
                    "expires": end.strftime("%Y-%m-%d"),
                    "days_left": days_left,
                })

        for cred in app.get("keyCredentials", []):
            end = _parse_date(cred.get("endDateTime"))
            if end is None:
                continue
            days_left = (end - now).days
            if days_left <= max_threshold:
                alerts.append({
                    "app_name": app.get("displayName", "Unknown"),
                    "app_id": app.get("appId", ""),
                    "credential_type": "Certificate",
                    "credential_name": cred.get("displayName", "(unnamed)"),
                    "expires": end.strftime("%Y-%m-%d"),
                    "days_left": days_left,
                })

    alerts.sort(key=lambda a: a["days_left"])
    return alerts


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Slack notification
# ---------------------------------------------------------------------------

def send_slack_alert(alerts: list[dict]) -> None:
    webhook_url = os.getenv("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        log.warning("SLACK_WEBHOOK_URL not set, skipping Slack notification")
        return

    # Build per-alert blocks
    alert_blocks = []
    for a in alerts:
        if a["days_left"] < 0:
            status = "EXPIRED"
            urgency = "[Critical]"
        elif a["days_left"] <= 7:
            status = f"{a['days_left']} days remaining"
            urgency = "[Critical]"
        elif a["days_left"] <= 30:
            status = f"{a['days_left']} days remaining"
            urgency = "[Warning]"
        else:
            status = f"{a['days_left']} days remaining"
            urgency = "[Notice]"

        alert_blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{urgency} {a['app_name']}*\n"
                    f"Type: {a['credential_type']}  |  Name: {a['credential_name']}\n"
                    f"App ID: `{a['app_id']}`\n"
                    f"Expires: {a['expires']}  |  *{status}*"
                ),
            },
        })

    # Slack limits messages to 50 blocks — chunk into multiple messages
    max_alert_blocks = 45  # leave room for header, summary, dividers, footer
    chunks = [alert_blocks[i:i + max_alert_blocks] for i in range(0, len(alert_blocks), max_alert_blocks)]
    total_chunks = len(chunks)

    for idx, chunk in enumerate(chunks):
        part_label = f" (part {idx + 1}/{total_chunks})" if total_chunks > 1 else ""
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"Azure Credential Expiry Report{part_label}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"{len(alerts)} credential(s) approaching expiration.",
                },
            },
            {"type": "divider"},
        ]
        blocks.extend(chunk)
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Generated by Azure App Secret Expiry Monitor | {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
                }
            ],
        })

        payload = {"blocks": blocks}
        resp = requests.post(webhook_url, json=payload, timeout=15)
        resp.raise_for_status()
    log.info("Slack alert sent successfully")


# ---------------------------------------------------------------------------
# Email notification
# ---------------------------------------------------------------------------

def send_email_alert(alerts: list[dict]) -> None:
    smtp_host = os.getenv("SMTP_HOST", "")
    if not smtp_host:
        log.warning("SMTP_HOST not set, skipping email notification")
        return

    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USERNAME", "")
    smtp_pass = os.getenv("SMTP_PASSWORD", "")
    use_tls = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
    email_from = os.getenv("EMAIL_FROM", smtp_user)
    email_to = [e.strip() for e in os.getenv("EMAIL_TO", "").split(",") if e.strip()]

    if not email_to:
        log.warning("EMAIL_TO not set, skipping email notification")
        return

    subject = f"Azure Secret/Cert Expiry Alert - {len(alerts)} credential(s)"

    rows = ""
    for a in alerts:
        status = "EXPIRED" if a["days_left"] < 0 else f"{a['days_left']} days"
        color = "#dc3545" if a["days_left"] <= 7 else "#ffc107" if a["days_left"] <= 30 else "#17a2b8"
        rows += (
            f"<tr>"
            f"<td style='padding:8px;border:1px solid #ddd'>{a['app_name']}</td>"
            f"<td style='padding:8px;border:1px solid #ddd'><code>{a['app_id']}</code></td>"
            f"<td style='padding:8px;border:1px solid #ddd'>{a['credential_type']}</td>"
            f"<td style='padding:8px;border:1px solid #ddd'>{a['credential_name']}</td>"
            f"<td style='padding:8px;border:1px solid #ddd'>{a['expires']}</td>"
            f"<td style='padding:8px;border:1px solid #ddd;color:{color};font-weight:bold'>{status}</td>"
            f"</tr>"
        )

    html = (
        f"<h2>Azure App Secret &amp; Certificate Expiry Alert</h2>"
        f"<p>{len(alerts)} credential(s) expiring within threshold.</p>"
        f"<table style='border-collapse:collapse;width:100%'>"
        f"<tr style='background:#f8f9fa'>"
        f"<th style='padding:8px;border:1px solid #ddd;text-align:left'>App Name</th>"
        f"<th style='padding:8px;border:1px solid #ddd;text-align:left'>App ID</th>"
        f"<th style='padding:8px;border:1px solid #ddd;text-align:left'>Type</th>"
        f"<th style='padding:8px;border:1px solid #ddd;text-align:left'>Credential</th>"
        f"<th style='padding:8px;border:1px solid #ddd;text-align:left'>Expires</th>"
        f"<th style='padding:8px;border:1px solid #ddd;text-align:left'>Remaining</th>"
        f"</tr>{rows}</table>"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = ", ".join(email_to)
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        if use_tls:
            server.starttls()
        if smtp_user:
            server.login(smtp_user, smtp_pass)
        server.sendmail(email_from, email_to, msg.as_string())

    log.info("Email alert sent to %s", ", ".join(email_to))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    threshold_str = os.getenv("ALERT_THRESHOLD_DAYS", "90,60,30,14,7")
    threshold_days = [int(d.strip()) for d in threshold_str.split(",")]

    log.info("Starting expiry check (thresholds: %s days)", threshold_days)

    token = get_access_token()
    apps = get_applications(token)
    log.info("Fetched %d app registration(s)", len(apps))

    apps = filter_applications(apps)
    alerts = check_expiry(apps, threshold_days)

    if not alerts:
        log.info("No credentials expiring within threshold. All clear!")
        return

    log.info("Found %d expiring credential(s):", len(alerts))
    for a in alerts:
        status = "EXPIRED" if a["days_left"] < 0 else f"{a['days_left']} days left"
        log.info(
            "  %s | %s | %s | %s | %s",
            a["app_name"], a["credential_type"], a["credential_name"],
            a["expires"], status,
        )

    slack_enabled = os.getenv("SLACK_ENABLED", "false").lower() == "true"
    email_enabled = os.getenv("EMAIL_ENABLED", "false").lower() == "true"

    if slack_enabled:
        try:
            send_slack_alert(alerts)
        except Exception:
            log.exception("Failed to send Slack alert")

    if email_enabled:
        try:
            send_email_alert(alerts)
        except Exception:
            log.exception("Failed to send email alert")

    if not slack_enabled and not email_enabled:
        log.warning("No notification channels enabled. Set SLACK_ENABLED=true or EMAIL_ENABLED=true in .env")


if __name__ == "__main__":
    main()
