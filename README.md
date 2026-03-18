# Azure App Secret Expiry Alerts

Azure App Registration secrets and certificates expire silently. When they do, integrations break with no warning. This script monitors all App Registrations in your tenant and sends alerts via Slack and email before secrets or certificates expire.

## Status

Script in development. Check back soon.

## Planned Features

- Scan all Azure App Registrations for expiring secrets and certificates
- Configurable alert thresholds (30, 14, 7 days before expiry)
- Slack webhook notifications
- Email alerts via SMTP
- Summary report of all app registrations and their expiry dates

## Blog Post

[Never Let an Azure App Registration Secret Expire Again](https://blog.soarsystems.cc/azure-app-secret-expiry-alerts)
