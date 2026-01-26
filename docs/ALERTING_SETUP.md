# CALYPSO Alert System Setup Guide

This document describes how to deploy and configure the WhatsApp/SMS/Email alerting system for CALYPSO trading bots.

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
              ┌─────────────┼───────────────┐
              ▼             ▼               ▼
    ┌──────────────┐ ┌───────────┐ ┌─────────────┐
    │ WhatsApp     │ │ SMS       │ │ Gmail SMTP  │
    │ (Twilio)     │ │ (Twilio)  │ │             │
    └──────┬───────┘ └─────┬─────┘ └──────┬──────┘
           └───────────────┼──────────────┘
                           ▼
                      Your Devices
```

**Key Benefits:**
- **Non-blocking**: Bot publishes to Pub/Sub (~50ms) and continues immediately
- **Reliable**: Pub/Sub retries for 7 days, dead-letter queue captures failures
- **Accurate**: Alerts sent AFTER actions complete with actual results
- **Global delivery**: WhatsApp works everywhere, no carrier issues
- **Works on WiFi**: Perfect for traveling - no cellular needed for WhatsApp

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

## Step 2: Set Up Twilio (WhatsApp + SMS)

### 2a. Create Twilio Account

1. Sign up at [twilio.com](https://www.twilio.com/try-twilio)
2. Get your Account SID and Auth Token from the Console
3. (Optional) Buy a phone number for SMS (~$1/month) - only needed if you want SMS fallback

### 2b. Set Up WhatsApp Sandbox (Recommended)

WhatsApp is the recommended delivery method - works globally, no carrier issues, works on WiFi.

1. Go to [Twilio Console > Messaging > WhatsApp](https://console.twilio.com/us1/develop/sms/try-it-out/whatsapp-learn)
2. Note the sandbox number: `+1 415 523 8886`
3. **On your phone**: Open WhatsApp and send the join message (e.g., "join your-sandbox-code") to +1 415 523 8886
4. You'll receive a confirmation that you've joined the sandbox

**Note:** The sandbox is free and sufficient for personal alerts. For production apps with multiple recipients, apply for WhatsApp Business API approval.

### 2c. Store Credentials in Secret Manager

```bash
# Create Twilio credentials secret
gcloud secrets create calypso-twilio-credentials \
    --project=calypso-trading-bot \
    --replication-policy="automatic"

# Add the secret value (JSON format)
# whatsapp_number is the Twilio sandbox number (prefix with whatsapp:)
# phone_number is optional - only needed for SMS fallback
echo '{"account_sid": "YOUR_SID", "auth_token": "YOUR_TOKEN", "whatsapp_number": "whatsapp:+14155238886", "phone_number": "+1XXXXXXXXXX"}' | \
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
# whatsapp_number is YOUR phone number (same as phone_number usually)
# prefer_whatsapp: true = use WhatsApp first, SMS as fallback
echo '{"phone_number": "+971XXXXXXXXX", "whatsapp_number": "+971XXXXXXXXX", "email": "your@email.com", "gmail_address": "your@gmail.com", "gmail_app_password": "YOUR_APP_PASSWORD", "prefer_whatsapp": true}' | \
    gcloud secrets versions add calypso-alert-config \
    --project=calypso-trading-bot \
    --data-file=-
```

**Important for UAE/International numbers:**
- Use E.164 format: `+971XXXXXXXXX` for UAE
- WhatsApp works globally - no need for a US phone number
- You can receive alerts anywhere in the world on WiFi

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
| **CRITICAL** | WhatsApp + Email | Circuit breaker, emergency exit, naked position |
| **HIGH** | WhatsApp + Email | Stop loss, max loss, roll failed |
| **MEDIUM** | Email only | Position opened/closed, profit target, roll complete |
| **LOW** | Email only | Bot started/stopped, daily summary |

**Note:** WhatsApp is the primary delivery method for CRITICAL/HIGH alerts. SMS is used as fallback if WhatsApp fails or isn't configured.

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
| Twilio WhatsApp | Sandbox free | ~50 messages | $0.00 |
| Twilio SMS (fallback) | N/A | ~10 SMS @ $0.10 | ~$1.00 |
| Gmail SMTP | Free | ~200 emails | $0.00 |

**Total: ~$0.00 - $1.00/month** (Free if using WhatsApp sandbox only)

**Notes:**
- WhatsApp sandbox is free for personal use (unlimited messages to yourself)
- WhatsApp Business API: ~$0.005-0.05 per message depending on country
- SMS to UAE: ~$0.10/message (use WhatsApp instead!)
- Gmail SMTP allows 500 emails/day for regular accounts

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

### WhatsApp Not Delivered

1. **Sandbox not joined**: Send "join <your-code>" to +1 415 523 8886 from your WhatsApp
2. **24-hour window expired**: For sandbox, you must interact with the bot within 24 hours to receive messages. Send any message to refresh.
3. Check Twilio console for delivery status
4. Verify phone number format (+971XXXXXXXXX for UAE)

### SMS Not Delivered (Fallback)

1. Check Twilio console for delivery status
2. Verify phone number format (E.164: +971XXXXXXXXX)
3. Check Twilio account balance
4. International SMS may be blocked by carriers - prefer WhatsApp

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
