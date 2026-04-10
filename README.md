# Azure App Secret & Certificate Expiry Alerts

Monitors Azure App Registration secrets and certificates for upcoming expiration. Sends alerts via Slack and/or email before they expire.

## Prerequisites

- Python 3.10+
- An Azure AD App Registration with **Application.Read.All** (application permission) granted and admin-consented, OR a Managed Identity with the same permission.

## Setup

1. **Clone the repo**

   ```bash
   git clone https://github.com/erv718/azure-app-secret-expiry-alerts.git
   cd azure-app-secret-expiry-alerts
   ```

2. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

3. **Configure**

   ```bash
   cp .env.template .env
   ```

   Edit `.env` and fill in your values. See the template comments for details.

4. **Run**

   ```bash
   python monitor.py
   ```

## Configuration

All configuration is done via the `.env` file (copied from `.env.template`).

| Variable | Required | Description |
|---|---|---|
| `AZURE_AUTH_METHOD` | Yes | `service_principal` or `managed_identity` |
| `AZURE_TENANT_ID` | SP only | Azure AD tenant ID |
| `AZURE_CLIENT_ID` | SP only | App registration client ID |
| `AZURE_CLIENT_SECRET` | SP only | App registration client secret |
| `FILTER_INCLUDE_APP_IDS` | No | Only monitor these App (client) IDs (comma-separated) |
| `FILTER_INCLUDE_NAMES` | No | Only monitor apps whose name contains these patterns (comma-separated, case-insensitive) |
| `FILTER_EXCLUDE_APP_IDS` | No | Skip these App (client) IDs |
| `FILTER_EXCLUDE_NAMES` | No | Skip apps whose name contains these patterns |
| `ALERT_THRESHOLD_DAYS` | No | Comma-separated days (default: `90,60,30,14,7`) |
| `SLACK_ENABLED` | No | `true` to enable Slack alerts |
| `SLACK_WEBHOOK_URL` | Slack | Slack incoming webhook URL |
| `EMAIL_ENABLED` | No | `true` to enable email alerts |
| `SMTP_HOST` | Email | SMTP server hostname |
| `SMTP_PORT` | Email | SMTP port (default: `587`) |
| `SMTP_USERNAME` | Email | SMTP username |
| `SMTP_PASSWORD` | Email | SMTP password |
| `SMTP_USE_TLS` | Email | `true` to use STARTTLS (default: `true`) |
| `EMAIL_FROM` | Email | Sender email address |
| `EMAIL_TO` | Email | Comma-separated recipient addresses |

## Deployment

### Local / On-Premises Server

Run the monitor on any machine with Python 3.10+ and network access to Microsoft Graph.

**Linux (cron)** — run daily at 8 AM:

```bash
0 8 * * * cd /path/to/azure-app-secret-expiry-alerts && /path/to/python monitor.py >> /var/log/secret-expiry.log 2>&1
```

**Windows (Task Scheduler)**:

1. Open Task Scheduler and create a new task
2. Set the trigger to daily at your preferred time
3. Set the action to run:
   ```
   python C:\path\to\azure-app-secret-expiry-alerts\monitor.py
   ```
4. Under "General", select "Run whether user is logged on or not"

**Considerations**: The machine must remain powered on and reachable. You are responsible for OS updates, Python version management, and credential rotation for the service principal stored in `.env`.

---

### Azure

#### Option A: Azure Automation Account (Recommended)

Azure Automation provides serverless scheduling with native managed identity support, eliminating the need to store credentials.

1. **Create an Automation Account** in the Azure Portal

2. **Enable a System-Assigned Managed Identity** on the Automation Account

3. **Grant Microsoft Graph permissions** to the managed identity:

   ```bash
   # Install the Microsoft Graph PowerShell module if needed
   # Connect-MgGraph -Scopes "Application.Read.All, AppRoleAssignment.ReadWrite.All"

   $managedIdentityObjectId = "<your-automation-account-identity-object-id>"
   $graphAppId = "00000003-0000-0000-c000-000000000000"
   $graphSp = Get-MgServicePrincipal -Filter "appId eq '$graphAppId'"
   $appRole = $graphSp.AppRoles | Where-Object { $_.Value -eq "Application.Read.All" }

   New-MgServicePrincipalAppRoleAssignment `
     -ServicePrincipalId $managedIdentityObjectId `
     -PrincipalId $managedIdentityObjectId `
     -ResourceId $graphSp.Id `
     -AppRoleId $appRole.Id
   ```

4. **Import Python packages** in the Automation Account under "Python packages":
   - `azure-identity`
   - `requests`
   - `python-dotenv`

5. **Create a Python Runbook**, paste the contents of `monitor.py`, and set environment variables in the Automation Account's variable assets or directly in the Runbook configuration. Set `AZURE_AUTH_METHOD=managed_identity`.

6. **Link a Schedule** (e.g., daily at 8:00 AM UTC) to the Runbook.

#### Option B: Azure Functions (Timer Trigger)

Suitable if you prefer a code-first deployment model or already use Azure Functions.

1. Create a Function App with Python 3.10+ runtime
2. Add a timer trigger function (e.g., `0 0 8 * * *` for daily at 8 AM UTC)
3. Add `monitor.py` logic to the function entry point
4. Configure application settings with the environment variables from `.env.template`
5. Assign a managed identity and grant `Application.Read.All` as described above
6. Deploy via Azure CLI, VS Code, or GitHub Actions

---

### AWS

#### Option A: AWS Lambda + EventBridge (Recommended)

1. **Create a Lambda function** with Python 3.10+ runtime

2. **Package the code**:
   ```bash
   pip install -r requirements.txt -t package/
   cp monitor.py package/
   cd package && zip -r ../function.zip .
   ```

3. **Create a Lambda handler**. Add a `lambda_function.py` wrapper:
   ```python
   import monitor

   def lambda_handler(event, context):
       monitor.main()
       return {"statusCode": 200, "body": "Expiry check complete"}
   ```
   Include this file in the zip package.

4. **Upload the zip** to your Lambda function

5. **Set environment variables** in the Lambda configuration using the values from `.env.template`. Use AWS Secrets Manager or Parameter Store for sensitive values like `AZURE_CLIENT_SECRET` and `SMTP_PASSWORD`.

6. **Create an EventBridge (CloudWatch Events) rule** to trigger the Lambda on a schedule:
   ```
   cron(0 8 * * ? *)
   ```
   This runs the function daily at 8 AM UTC.

7. **Set the Lambda timeout** to at least 60 seconds (default 3 seconds is not enough for Graph API calls).

#### Option B: AWS ECS Scheduled Task

For environments already using ECS:

1. Containerize the script with a `Dockerfile`
2. Push the image to ECR
3. Create an ECS Scheduled Task using EventBridge to run the container on a cron schedule
4. Pass environment variables via ECS task definition or AWS Secrets Manager

## Azure Permissions

The monitoring identity needs the Microsoft Graph **Application.Read.All** application permission.

### Service Principal setup

1. Create an App Registration in Azure AD
2. Add API permission: Microsoft Graph > Application permissions > `Application.Read.All`
3. Grant admin consent
4. Create a client secret and note the tenant ID, client ID, and secret value

## License

MIT
