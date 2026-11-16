# Monitoring — the alert path watches itself

Before 2026-11-15 this project had **zero** alert policies and **zero**
notification channels. Everything operational went through the bot's own
Telegram/email path — which cannot report its own failure.

## What is here

| File | Purpose |
|---|---|
| `alert_delivery_failure_policy.json` | Log-based alert policy. `__NOTIFICATION_CHANNEL__` is substituted at apply time. |
| `setup_alert_delivery_monitor.sh` | Idempotent apply: resolves the recipient from Secret Manager, ensures the email channel, creates or updates the policy. |

The recipient is **never stored in this repo** — the script reads it from the
`calypso-alert-config` secret at runtime.

## Apply / refresh

```bash
CLOUDSDK_CORE_ACCOUNT=<operator> bash deploy/monitoring/setup_alert_delivery_monitor.sh
```

## Why it matters

Delivery failures now re-raise (total failure only) so Pub/Sub retries with
10s–600s backoff and dead-letters after 50 attempts. But **the DLQ is forensic —
nobody watches it.** Without this policy, exhausting 50 retries would still be
silent, i.e. the fix alone would only move the silence later. This policy is the
part that tells a human.

## Two traps baked into the filter

1. **It matches `textPayload`, not `severity`.** Every application log line from
   `process-trading-alert` lands at severity **DEFAULT** in
   `run.googleapis.com/stderr` with no `severity` field at all. A
   `severity>=WARNING` condition matches **nothing this function logs** — a query
   that produced a false "zero errors in 30 days" reading during the 2026-11-15
   audit before the blank severity column gave it away.
2. **An empty DLQ is not evidence of health** (the 2026-10-07 lesson). Verify the
   wiring, never infer it from an absence of output:
   ```bash
   gcloud alpha monitoring policies list --project=calypso-trading-bot \
     --format="value(displayName,enabled)"
   ```

## Triage when it fires

```bash
# what was lost
gcloud pubsub subscriptions pull calypso-alerts-dlq-sub \
  --project=calypso-trading-bot --limit=10 --auto-ack

# the function's own records (both streams are the same records)
gcloud logging read 'resource.labels.service_name="process-trading-alert"' \
  --project=calypso-trading-bot --limit=20 --freshness=1d \
  --format="value(timestamp,textPayload)"
```
