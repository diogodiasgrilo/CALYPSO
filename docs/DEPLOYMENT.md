# GCP Deployment Guide

Complete guide for deploying Calypso trading bots to Google Cloud Platform.

**Cost Estimate:** ~$15/month

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    GOOGLE CLOUD PLATFORM                     │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────────┐     ┌────────────────────────┐        │
│  │  Compute Engine  │────▶│    Secret Manager      │        │
│  │  (e2-small VM)   │     │  - Saxo credentials    │        │
│  │                  │     │  - Google Sheets creds │        │
│  │  4 bots + 1 svc: │     └────────────────────────┘        │
│  │  - token_keeper  │                                       │
│  │  - delta_neutral │                                       │
│  │  - iron_fly_0dte │────▶ Google Sheets (logging)          │
│  │  - rolling_put   │                                       │
│  │  - meic          │                                       │
│  └────────┬─────────┘                                       │
│           │                                                  │
└───────────┼──────────────────────────────────────────────────┘
            │
            ▼
      Saxo Bank API
```

### Cost Breakdown

| Resource | Spec | Monthly |
|----------|------|---------|
| Compute Engine | e2-small (2 vCPU, 2GB RAM) | ~$13 |
| Boot Disk | 20GB SSD | ~$2 |
| Secret Manager | 4 secrets | ~$0.12 |
| Network | Minimal | ~$0.50 |
| **Total** | | **~$15/month** |

---

## Prerequisites

1. GCP Account with billing enabled
2. `gcloud` CLI installed
3. Saxo Bank LIVE API credentials
4. Google Sheets service account JSON

---

## Step 1: Create GCP Project

```bash
# Create project
gcloud projects create calypso-trading-bot --name="Calypso Trading Bot"

# Set as active
gcloud config set project calypso-trading-bot

# Enable billing in GCP Console:
# https://console.cloud.google.com/billing
```

---

## Step 2: Enable APIs

```bash
gcloud services enable compute.googleapis.com
gcloud services enable secretmanager.googleapis.com
```

---

## Step 3: Create Service Account

```bash
# Create service account
gcloud iam service-accounts create calypso-vm \
    --display-name="Calypso VM Service Account"

# Grant Secret Manager access
gcloud projects add-iam-policy-binding calypso-trading-bot \
    --member="serviceAccount:calypso-vm@calypso-trading-bot.iam.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"
```

---

## Step 4: Store Secrets

### Saxo API Credentials
```bash
# Create secret
gcloud secrets create calypso-saxo-credentials --replication-policy="automatic"

# Add value (create JSON with your credentials first)
gcloud secrets versions add calypso-saxo-credentials --data-file=/tmp/saxo-creds.json
```

### Google Sheets Credentials
```bash
gcloud secrets create calypso-google-sheets-credentials --replication-policy="automatic"
gcloud secrets versions add calypso-google-sheets-credentials --data-file=config/google_credentials.json
```

---

## Step 5: Create VM

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

**Important:** Do NOT use preemptible/spot VMs - they can be terminated during trading!

---

## Step 6: Deploy Code

### SSH into VM
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b
```

### Clone Repository
```bash
sudo git clone https://github.com/diogodiasgrilo/CALYPSO.git /opt/calypso
sudo chown -R calypso:calypso /opt/calypso
```

### Setup Python Environment
```bash
cd /opt/calypso
sudo -u calypso python3 -m venv .venv
sudo -u calypso .venv/bin/pip install -r requirements.txt
```

---

## Step 7: Create systemd Services

Create service files for each bot:

### `/etc/systemd/system/delta_neutral.service`
```ini
[Unit]
Description=Calypso Delta Neutral Trading Bot
After=network.target

[Service]
Type=simple
User=calypso
Group=calypso
WorkingDirectory=/opt/calypso
Environment="GCP_PROJECT=calypso-trading-bot"
Environment="PYTHONUNBUFFERED=1"
Environment="PYTHONPATH=/opt/calypso"
ExecStart=/opt/calypso/.venv/bin/python -m bots.delta_neutral.main --live
Restart=always
RestartSec=30
StartLimitInterval=600
StartLimitBurst=5

[Install]
WantedBy=multi-user.target
```

### `/etc/systemd/system/iron_fly_0dte.service`
```ini
[Unit]
Description=Calypso Iron Fly 0DTE Trading Bot
After=network.target

[Service]
Type=simple
User=calypso
Group=calypso
WorkingDirectory=/opt/calypso
Environment="GCP_PROJECT=calypso-trading-bot"
Environment="PYTHONUNBUFFERED=1"
Environment="PYTHONPATH=/opt/calypso"
ExecStart=/opt/calypso/.venv/bin/python -m bots.iron_fly_0dte.main --live --dry-run
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

### `/etc/systemd/system/rolling_put_diagonal.service`
```ini
[Unit]
Description=Calypso Rolling Put Diagonal Trading Bot
After=network.target

[Service]
Type=simple
User=calypso
Group=calypso
WorkingDirectory=/opt/calypso
Environment="GCP_PROJECT=calypso-trading-bot"
Environment="PYTHONUNBUFFERED=1"
Environment="PYTHONPATH=/opt/calypso"
ExecStart=/opt/calypso/.venv/bin/python -m bots.rolling_put_diagonal.main --live --dry-run
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

### `/etc/systemd/system/meic.service`
```ini
[Unit]
Description=Calypso MEIC Trading Bot
After=network.target token_keeper.service

[Service]
Type=simple
User=calypso
Group=calypso
WorkingDirectory=/opt/calypso
Environment="GCP_PROJECT=calypso-trading-bot"
Environment="PYTHONUNBUFFERED=1"
Environment="PYTHONPATH=/opt/calypso"
ExecStart=/opt/calypso/.venv/bin/python -m bots.meic.main --live --dry-run
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

### `/etc/systemd/system/token_keeper.service`
```ini
[Unit]
Description=Calypso Token Keeper - Keeps Saxo OAuth Tokens Fresh
After=network.target network-online.target
Wants=network-online.target
Before=iron_fly_0dte.service delta_neutral.service rolling_put_diagonal.service meic.service

[Service]
Type=simple
User=calypso
Group=calypso
WorkingDirectory=/opt/calypso
Environment="GCP_PROJECT=calypso-trading-bot"
Environment="PYTHONUNBUFFERED=1"
Environment="PYTHONPATH=/opt/calypso"
ExecStart=/opt/calypso/.venv/bin/python -m services.token_keeper.main
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### Enable and Start Services
```bash
sudo systemctl daemon-reload
# Start token keeper first (ensures fresh token)
sudo systemctl enable token_keeper
sudo systemctl start token_keeper
# Then enable and start trading bots
sudo systemctl enable delta_neutral iron_fly_0dte rolling_put_diagonal meic
sudo systemctl start delta_neutral iron_fly_0dte rolling_put_diagonal meic
```

---

## Step 8: Verify Deployment

```bash
# Check all bots are running
/opt/calypso/scripts/bot_status.sh

# View logs
tail -f /opt/calypso/logs/monitor.log

# Check individual bot
sudo systemctl status delta_neutral
```

---

## Maintenance

### Update Code
```bash
cd /opt/calypso
sudo -u calypso git pull
# Clear Python cache to ensure new code runs
find bots shared services -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null
sudo systemctl restart delta_neutral iron_fly_0dte rolling_put_diagonal meic
# Token keeper rarely needs restart (only if token_keeper code changed)
```

### View Logs
```bash
# Combined monitor
tail -f /opt/calypso/logs/monitor.log

# Individual bot
sudo journalctl -u delta_neutral -f
```

### Update Secrets
```bash
gcloud secrets versions add calypso-saxo-credentials --data-file=updated-creds.json
```

---

## Troubleshooting

### Bot won't start
```bash
sudo journalctl -u delta_neutral -n 50
```

### Can't access secrets
```bash
# Test secret access
gcloud secrets versions access latest --secret=calypso-saxo-credentials
```

### High memory usage
```bash
# Check memory
free -h

# Restart bot
sudo systemctl restart delta_neutral
```

---

## Security Best Practices

1. Never store credentials in code
2. Use Secret Manager for all sensitive data
3. Enable OS Login for SSH
4. Regular token rotation via refresh mechanism
5. Monitor Cloud Audit Logs

---

**Last Updated:** 2026-01-27
