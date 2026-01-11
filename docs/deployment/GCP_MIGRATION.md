# Calypso Trading Bot - GCP Migration Guide

## Overview

This guide covers deploying the Calypso Delta Neutral Trading Bot to Google Cloud Platform (GCP) for 24/7 operation.

### Architecture

```
+-----------------------------------------------------------+
|                  GOOGLE CLOUD PLATFORM                     |
+-----------------------------------------------------------+
|                                                            |
|  +------------------+     +----------------------+         |
|  | Compute Engine   |---->| Secret Manager       |         |
|  | (e2-small VM)    |     | - Saxo credentials   |         |
|  |                  |     | - GSheets credentials|         |
|  | Calypso Bot      |     | - Email config       |         |
|  | (systemd)        |     +----------------------+         |
|  +--------+---------+                                      |
|           |                                                |
|           +-------> Google Sheets --> Looker Studio        |
|           |         (5 worksheets)    (Dashboard)          |
|           |                                                |
|           +-------> SMTP --> Email Alerts                  |
|                                                            |
+-----------------------------------------------------------+
            |
            v
     Saxo Bank API (LIVE)
```

### Cost Estimate: ~$15/month

| Resource | Specification | Monthly Cost |
|----------|--------------|--------------|
| Compute Engine | e2-small (2 vCPU, 2GB RAM) | ~$13 |
| Boot Disk | 20GB SSD | ~$2 |
| Secret Manager | 4 secrets | ~$0.12 |
| Network | Minimal egress | ~$0.50 |
| **Total** | | **~$15/month** |

---

## Prerequisites

Before starting:

1. **GCP Account** with billing enabled
2. **gcloud CLI** installed ([installation guide](https://cloud.google.com/sdk/docs/install))
3. **Saxo Bank LIVE** API credentials (app_key, app_secret, tokens)
4. **Google Sheets** service account JSON (already created for local setup)

---

## Step 1: Create GCP Project

```bash
# Create new project (skip if using existing)
gcloud projects create calypso-trading-bot --name="Calypso Trading Bot"

# Set as active project
gcloud config set project calypso-trading-bot

# Enable billing (required - do this in GCP Console)
# https://console.cloud.google.com/billing
```

---

## Step 2: Enable Required APIs

```bash
gcloud services enable compute.googleapis.com
gcloud services enable secretmanager.googleapis.com
```

---

## Step 3: Create Service Account

```bash
# Create service account for the VM
gcloud iam service-accounts create calypso-vm \
    --display-name="Calypso VM Service Account"

# Grant Secret Manager access
gcloud projects add-iam-policy-binding calypso-trading-bot \
    --member="serviceAccount:calypso-vm@calypso-trading-bot.iam.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"
```

---

## Step 4: Configure Secrets in Secret Manager

### 4.1 Saxo API Credentials

Create a JSON file with your LIVE credentials:

```bash
cat > /tmp/saxo-creds.json << 'EOF'
{
    "app_key": "YOUR_LIVE_APP_KEY",
    "app_secret": "YOUR_LIVE_APP_SECRET",
    "access_token": "YOUR_CURRENT_ACCESS_TOKEN",
    "refresh_token": "YOUR_REFRESH_TOKEN",
    "token_expiry": "2024-01-01T00:00:00"
}
EOF

# Create secret
gcloud secrets create calypso-saxo-credentials \
    --replication-policy="automatic"

# Add secret value
gcloud secrets versions add calypso-saxo-credentials \
    --data-file=/tmp/saxo-creds.json

# Clean up
rm /tmp/saxo-creds.json
```

### 4.2 Google Sheets Credentials

```bash
# Upload your existing google_credentials.json
gcloud secrets create calypso-google-sheets-credentials \
    --replication-policy="automatic"

gcloud secrets versions add calypso-google-sheets-credentials \
    --data-file=config/google_credentials.json
```

### 4.3 Account Configuration

```bash
cat > /tmp/account-config.json << 'EOF'
{
    "account_key": "YOUR_ACCOUNT_KEY",
    "client_key": "YOUR_CLIENT_KEY"
}
EOF

gcloud secrets create calypso-account-config \
    --replication-policy="automatic"

gcloud secrets versions add calypso-account-config \
    --data-file=/tmp/account-config.json

rm /tmp/account-config.json
```

### 4.4 Email Configuration (Optional)

```bash
cat > /tmp/email-config.json << 'EOF'
{
    "enabled": true,
    "smtp_server": "smtp.gmail.com",
    "smtp_port": 587,
    "sender_email": "your-alert-email@gmail.com",
    "sender_password": "your-gmail-app-password",
    "recipients": ["you@example.com", "dad@example.com"],
    "use_tls": true
}
EOF

gcloud secrets create calypso-email-config \
    --replication-policy="automatic"

gcloud secrets versions add calypso-email-config \
    --data-file=/tmp/email-config.json

rm /tmp/email-config.json
```

**Note**: For Gmail, you need to create an App Password:
1. Go to Google Account > Security > 2-Step Verification
2. Scroll to "App passwords"
3. Generate password for "Mail" on "Other (Calypso Bot)"
4. Use this password in `sender_password`

---

## Step 5: Create Compute Engine VM

```bash
gcloud compute instances create calypso-bot \
    --zone=us-east1-b \
    --machine-type=e2-small \
    --image-family=debian-12 \
    --image-project=debian-cloud \
    --boot-disk-size=20GB \
    --boot-disk-type=pd-standard \
    --service-account=calypso-vm@calypso-trading-bot.iam.gserviceaccount.com \
    --scopes=https://www.googleapis.com/auth/cloud-platform \
    --metadata=enable-oslogin=TRUE \
    --tags=calypso-bot
```

**Important**: Do NOT use preemptible/spot VMs - they can be terminated during active trading!

---

## Step 6: Deploy the Bot

### 6.1 SSH into the VM

```bash
gcloud compute ssh calypso-bot --zone=us-east1-b
```

### 6.2 Upload Code

Option A: Clone from Git
```bash
git clone https://github.com/YOUR_USERNAME/Calypso.git /tmp/calypso
```

Option B: Upload via SCP
```bash
# From your local machine
gcloud compute scp --recurse ~/Desktop/Calypso calypso-bot:/tmp/ --zone=us-east1-b
```

### 6.3 Run Setup Script

```bash
cd /tmp/calypso  # or wherever you uploaded
sudo chmod +x deploy/setup_vm.sh
sudo ./deploy/setup_vm.sh
```

### 6.4 Update Project ID in Service File

```bash
sudo nano /etc/systemd/system/calypso.service
# Change GCP_PROJECT=calypso-trading-bot to your actual project ID
sudo systemctl daemon-reload
```

---

## Step 7: Start the Bot

```bash
# Start the service
sudo systemctl start calypso

# Check status
sudo systemctl status calypso

# View logs (follow mode)
sudo journalctl -u calypso -f
```

---

## Step 8: Setup Looker Studio Dashboard

1. Go to [Looker Studio](https://lookerstudio.google.com)
2. Click "Create" > "Report"
3. Add Data Source > Google Sheets
4. Select your "Calypso_Bot_Log" spreadsheet
5. Add each worksheet as a separate data source

### Recommended Dashboard Layout

**Page 1: Overview**
- Scorecard: Total P&L (USD + EUR)
- Scorecard: Today's P&L
- Time series chart: P&L over time
- Scorecard: Current Delta

**Page 2: Trades**
- Table: Recent trades from "Trades" worksheet
- Bar chart: Trades by action type

**Page 3: Safety & Risk**
- Table: Safety Events
- Time series: VIX over time
- Gauge: Current portfolio delta

**Page 4: Positions**
- Table: Current open positions
- Scorecards: Long/Short position values

---

## Maintenance

### Viewing Logs

```bash
# Real-time logs
sudo journalctl -u calypso -f

# Last 100 lines
sudo journalctl -u calypso -n 100

# Logs from today
sudo journalctl -u calypso --since today

# Logs since specific time
sudo journalctl -u calypso --since "2024-01-01 09:00:00"
```

### Restarting the Bot

```bash
sudo systemctl restart calypso
```

### Stopping the Bot

```bash
sudo systemctl stop calypso
```

### Updating the Bot

```bash
# SSH into VM
gcloud compute ssh calypso-bot --zone=us-east1-b

# Stop the bot
sudo systemctl stop calypso

# Update code (via git pull or SCP)
cd /opt/calypso
sudo -u calypso git pull  # if using git

# Reinstall dependencies if needed
sudo -u calypso /opt/calypso/.venv/bin/pip install -r requirements.txt

# Restart
sudo systemctl start calypso
```

### Updating Secrets (Token Refresh)

When tokens are refreshed, they're automatically saved to Secret Manager. But if you need to manually update:

```bash
# Update Saxo credentials
gcloud secrets versions add calypso-saxo-credentials \
    --data-file=updated-creds.json
```

---

## Troubleshooting

### Bot won't start

1. Check logs: `sudo journalctl -u calypso -n 50`
2. Verify secrets are accessible: Test manually with Python
3. Check service account permissions in GCP Console

### Can't access secrets

1. Verify service account has `secretAccessor` role
2. Check VM is using correct service account
3. Test with: `gcloud secrets versions access latest --secret=calypso-saxo-credentials`

### Connection/Authentication issues

1. Check Saxo API status
2. Verify tokens haven't expired
3. Check VM network connectivity: `ping gateway.saxobank.com`

### High memory usage

1. Check logs for memory leaks
2. Restart the service: `sudo systemctl restart calypso`
3. Consider upgrading VM if persistent

---

## Security Best Practices

1. **Never** store credentials in code or config files on the VM
2. Use Secret Manager for all sensitive data
3. Restrict SSH access (use OS Login)
4. Enable VPC firewall rules if needed
5. Regular token rotation via refresh mechanism
6. Monitor Cloud Audit Logs for suspicious activity

---

## Local Development

The bot still works locally for testing:

```bash
# SIM environment (default)
python src/main.py --dry-run

# LIVE environment
python src/main.py --live --dry-run

# Status check
python src/main.py --status --live
```

Local mode uses `config/config.json` while cloud mode uses Secret Manager.

---

## Files Reference

| File | Purpose |
|------|---------|
| `src/secret_manager.py` | GCP Secret Manager client |
| `src/config_loader.py` | Smart config loading (cloud vs local) |
| `deploy/calypso.service` | Systemd service definition |
| `deploy/setup_vm.sh` | VM setup automation script |

---

## Support

If you encounter issues:
1. Check the logs: `sudo journalctl -u calypso -f`
2. Review this documentation
3. Check Saxo API status
4. Verify GCP service account permissions
