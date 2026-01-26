# CALYPSO Alert System Setup Guide

This document describes how to deploy and configure the SMS/Email alerting system for CALYPSO trading bots.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         calypso-bot VM                              │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────┐    │
│  │  Iron Fly    │  │Delta Neutral │  │ Rolling Put Diagonal   │    │
│  └──────┬───────┘  └──────┬───────┘  └───────────┬────────────┘    │
│         └─────────────────┼──────────────────────┘                  │
│                  ┌────────▼────────┐                               │
│                  │  AlertService   │ (shared/alert_service.py)     │
│                  └────────┬────────┘                               │
└───────────────────────────┼─────────────────────────────────────────┘
                            │ Pub/Sub publish (~50ms)
                            ▼
              ┌─────────────────────────────┐
              │   Cloud Pub/Sub Topic       │
              │   "calypso-alerts"          │
              └─────────────┬───────────────┘
                            │ Push subscription
                            ▼
              ┌─────────────────────────────┐
              │   Cloud Function            │
              │   "process-trading-alert"   │
              └─────────────┬───────────────┘
              ┌─────────────┴───────────────┐
              ▼                             ▼
    ┌─────────────────┐          ┌─────────────────┐
    │ Twilio (GCP)    │          │  Gmail SMTP     │
    └────────┬────────┘          └────────┬────────┘
             ▼                            ▼
         Your Phone                  Your Email
```

**Key Benefits:**
- **Non-blocking**: Bot publishes to Pub/Sub (~50ms) and continues immediately
- **Reliable**: Pub/Sub retries for 7 days, dead-letter queue captures failures
- **Accurate**: Alerts sent AFTER actions complete with actual results
- **Unified billing**: All costs on Google Cloud billing

---

## Step 1: Create Pub/Sub Infrastructure

Run these commands from your local machine with `gcloud` configured:

```bash
# Create main alerts topic
gcloud pubsub topics create calypso-alerts \
    --project=calypso-trading-bot

# Create dead-letter topic for failed messages
gcloud pubsub topics create calypso-alerts-dlq \
    --project=calypso-trading-bot

# Create subscription for dead-letter monitoring
gcloud pubsub subscriptions create calypso-alerts-dlq-sub \
    --topic=calypso-alerts-dlq \
    --project=calypso-trading-bot
```

---

## Step 2: Set Up Twilio (via GCP Marketplace)

1. Go to [GCP Marketplace](https://console.cloud.google.com/marketplace)
2. Search for "Twilio"
3. Subscribe to Twilio via marketplace (billing goes to GCP)
4. Get your Account SID, Auth Token, and purchase a phone number
5. Store credentials in Secret Manager:

```bash
# Create Twilio credentials secret
gcloud secrets create calypso-twilio-credentials \
    --project=calypso-trading-bot \
    --replication-policy="automatic"

# Add the secret value (JSON format)
echo '{"account_sid": "YOUR_SID", "auth_token": "YOUR_TOKEN", "phone_number": "+1XXXXXXXXXX"}' | \
    gcloud secrets versions add calypso-twilio-credentials \
    --project=calypso-trading-bot \
    --data-file=-
```

---

## Step 3: Set Up Gmail App Password

For email alerts, use a Gmail App Password (not your regular password):

1. Go to [Google Account Security](https://myaccount.google.com/security)
2. Enable 2-Factor Authentication if not already enabled
3. Go to "App passwords" and create one for "Mail"
4. Store in Secret Manager:

```bash
# Create alert configuration secret
gcloud secrets create calypso-alert-config \
    --project=calypso-trading-bot \
    --replication-policy="automatic"

# Add the secret value
echo '{"phone_number": "+1XXXXXXXXXX", "email": "your@email.com", "gmail_address": "your@gmail.com", "gmail_app_password": "YOUR_APP_PASSWORD"}' | \
    gcloud secrets versions add calypso-alert-config \
    --project=calypso-trading-bot \
    --data-file=-
```

---

## Step 4: Deploy the Cloud Function

```bash
cd /Users/ddias/Desktop/CALYPSO/Git\ Repo/cloud_functions/alert_processor

# Deploy the function
gcloud functions deploy process-trading-alert \
    --gen2 \
    --runtime=python311 \
    --region=us-east1 \
    --source=. \
    --entry-point=process_alert \
    --trigger-topic=calypso-alerts \
    --project=calypso-trading-bot \
    --memory=256MB \
    --timeout=60s \
    --service-account=calypso-bot@calypso-trading-bot.iam.gserviceaccount.com
```

---

## Step 5: Configure Bot Alerts

Add the following to each bot's `config.json`:

```json
{
    "alerts": {
        "enabled": true,
        "phone_number": "+1XXXXXXXXXX",
        "email": "your@email.com"
    }
}
```

**Note:** Phone number and email can also be stored in Secret Manager (`calypso-alert-config`) and will be used as defaults if not specified in the message.

---

## Step 6: Grant IAM Permissions

The bot's service account needs permission to publish to Pub/Sub:

```bash
# Grant Pub/Sub Publisher role
gcloud projects add-iam-policy-binding calypso-trading-bot \
    --member="serviceAccount:calypso-bot@calypso-trading-bot.iam.gserviceaccount.com" \
    --role="roles/pubsub.publisher"

# Grant Secret Manager access
gcloud projects add-iam-policy-binding calypso-trading-bot \
    --member="serviceAccount:calypso-bot@calypso-trading-bot.iam.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"
```

---

## Step 7: Install Python Dependencies on VM

```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/pip install google-cloud-pubsub'"
```

---

## Alert Priority Levels

| Priority | Delivery | Examples |
|----------|----------|----------|
| **CRITICAL** | SMS + Email | Circuit breaker, emergency exit, naked position |
| **HIGH** | SMS + Email | Stop loss, max loss, roll failed |
| **MEDIUM** | Email only | Position opened/closed, profit target, roll complete |
| **LOW** | Email only | Bot started/stopped, daily summary |

---

## Alert Types by Bot

### Iron Fly (0DTE)

| Alert | Priority | When Sent |
|-------|----------|-----------|
| Circuit Breaker | CRITICAL | After 5 consecutive failures or daily halt escalation |
| Critical Intervention | CRITICAL | After emergency close fails, manual reset required |
| Wing Breach / Stop Loss | HIGH | After position closed due to price touching wing |
| Profit Target | MEDIUM | After position closed with target P&L |
| Position Opened | MEDIUM | After all 4 legs filled successfully |
| Time Exit | MEDIUM | After max hold time reached |
| Emergency Exit | CRITICAL | After circuit breaker triggers emergency close |

### Delta Neutral (SPY)

| Alert | Priority | When Sent |
|-------|----------|-----------|
| Circuit Breaker | CRITICAL | After consecutive failures + emergency actions |
| Emergency Close All | CRITICAL | After detecting unprotected position and closing |
| Full Position Open | MEDIUM | After straddle + strangle both filled |
| Position Closed | MEDIUM/HIGH | After exit (priority based on P&L) |
| Roll Completed | MEDIUM | After shorts rolled successfully |

### Rolling Put Diagonal (QQQ)

| Alert | Priority | When Sent |
|-------|----------|-----------|
| Circuit Breaker | CRITICAL | After failures + emergency position check |
| Naked Short Detected | CRITICAL | After detecting and closing naked short put |
| Campaign Opened | MEDIUM | After long + short puts both filled |
| Campaign Closed | MEDIUM | After campaign closed for DTE or event risk |

---

## Testing Alerts

### Dry Run Mode (Logs Only)

Set environment variable to test formatting without sending:

```bash
export ALERT_DRY_RUN=true
python -c "
from shared.alert_service import AlertService
svc = AlertService({'alerts': {'enabled': True}}, 'TEST')
svc.circuit_breaker('Test reason', 3)
"
```

### Test Cloud Function Locally

```bash
cd cloud_functions/alert_processor
python main.py
```

### Test End-to-End

Publish a test message to Pub/Sub:

```bash
gcloud pubsub topics publish calypso-alerts \
    --project=calypso-trading-bot \
    --message='{"bot_name":"TEST","alert_type":"circuit_breaker","priority":"critical","title":"Test Alert","message":"Testing alert system","timestamp":"2026-01-26T12:00:00Z","details":{},"delivery":{"sms":true,"email":true}}'
```

---

## Monitoring

### View Cloud Function Logs

```bash
gcloud functions logs read process-trading-alert \
    --region=us-east1 \
    --project=calypso-trading-bot \
    --limit=50
```

### Check Dead Letter Queue

```bash
gcloud pubsub subscriptions pull calypso-alerts-dlq-sub \
    --project=calypso-trading-bot \
    --limit=10 \
    --auto-ack
```

---

## Cost Estimate

| Service | Free Tier | Estimated Usage | Monthly Cost |
|---------|-----------|-----------------|--------------|
| Pub/Sub | 10GB/month | ~1MB | $0.00 |
| Cloud Functions | 2M invocations | ~1000 | $0.00 |
| Twilio SMS | N/A | ~50 SMS @ $0.0079 | ~$0.40 |
| Gmail SMTP | Free | ~200 emails | $0.00 |

**Total: ~$0.40 - $1.00/month**

**Note:** Gmail SMTP allows 500 emails/day for regular accounts. Twilio SMS pricing varies by region (~$0.0079 per SMS to US numbers).

---

## Security Best Practices

1. **Never hardcode credentials** - Always use Secret Manager or environment variables
2. **Use service accounts** - Follow principle of least privilege (only Pub/Sub Publisher + Secret Accessor roles)
3. **Phone number format** - Always use E.164 format (+1XXXXXXXXXX for US)
4. **Gmail App Password** - Never use your regular Gmail password; always use App Password with 2FA enabled
5. **Monitor dead-letter queue** - Check regularly for failed alerts that may indicate configuration issues

---

## Troubleshooting

### Alerts Not Sending

1. Check if `alerts.enabled` is `true` in config
2. Verify Pub/Sub topic exists: `gcloud pubsub topics list --project=calypso-trading-bot`
3. Check Cloud Function logs for errors
4. Verify secrets exist in Secret Manager

### SMS Not Delivered

1. Check Twilio console for delivery status
2. Verify phone number format (+1XXXXXXXXXX)
3. Check Twilio account balance

### Email Not Delivered

1. Check Gmail "Sent" folder
2. Verify app password is correct
3. Check spam folder on recipient
4. Ensure 2FA is enabled on Gmail account

---

## Files Reference

| File | Purpose |
|------|---------|
| `shared/alert_service.py` | AlertService class used by bots |
| `cloud_functions/alert_processor/main.py` | Cloud Function that sends SMS/email |
| `cloud_functions/alert_processor/requirements.txt` | Cloud Function dependencies |
| `docs/ALERTING_SETUP.md` | This file |
