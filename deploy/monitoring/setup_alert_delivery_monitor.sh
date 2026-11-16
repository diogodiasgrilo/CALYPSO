#!/usr/bin/env bash
# Create (or refresh) the log-based alert that tells a human when the alert
# Cloud Function fails to DELIVER an alert.
#
# Why this is a script and not a one-off console click: there were ZERO alert
# policies and ZERO notification channels in this project before 2026-11-15, so
# there is no existing convention to follow -- and a monitoring resource created
# by hand is exactly the kind of thing that silently disappears and is never
# noticed (the same failure mode as the DLQ, which was "configured" in four docs
# and wired in none).
#
# Idempotent: re-running reuses the existing channel and updates the policy.
#
# The recipient is read from Secret Manager at runtime, NOT stored in this repo.
set -euo pipefail

PROJECT="${PROJECT:-calypso-trading-bot}"
POLICY_NAME="Alert delivery failure (process-trading-alert)"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "== resolving the alert recipient from Secret Manager =="
EMAIL="$(gcloud secrets versions access latest --secret=calypso-alert-config \
          --project="$PROJECT" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("email",""))')"
if [[ -z "$EMAIL" ]]; then
  echo "FATAL: calypso-alert-config has no 'email' key - nothing to notify." >&2
  exit 1
fi
echo "   recipient resolved (${EMAIL:0:2}***@${EMAIL##*@})"

echo "== ensuring an email notification channel =="
CHANNEL="$(gcloud beta monitoring channels list --project="$PROJECT" \
            --filter="type=email AND labels.email_address=$EMAIL" \
            --format="value(name)" | head -1)"
if [[ -z "$CHANNEL" ]]; then
  CHANNEL="$(gcloud beta monitoring channels create --project="$PROJECT" \
              --display-name="CALYPSO operator email" \
              --type=email \
              --channel-labels="email_address=$EMAIL" \
              --format="value(name)")"
  echo "   created $CHANNEL"
else
  echo "   reusing $CHANNEL"
fi

echo "== rendering the policy =="
TMP="$(mktemp -t calypso-policy)"
python3 - "$HERE/alert_delivery_failure_policy.json" "$CHANNEL" "$TMP" <<'PY'
import json, sys
src, channel, out = sys.argv[1:4]
d = json.load(open(src))
d["notificationChannels"] = [channel]
json.dump(d, open(out, "w"), indent=2)
PY

EXISTING="$(gcloud alpha monitoring policies list --project="$PROJECT" \
             --filter="displayName=\"$POLICY_NAME\"" --format="value(name)" | head -1)"
if [[ -z "$EXISTING" ]]; then
  echo "== creating the policy =="
  gcloud alpha monitoring policies create --project="$PROJECT" --policy-from-file="$TMP"
else
  echo "== updating the existing policy $EXISTING =="
  gcloud alpha monitoring policies update "$EXISTING" --project="$PROJECT" \
    --policy-from-file="$TMP"
fi
rm -f "$TMP"

echo
echo "== verify =="
gcloud alpha monitoring policies list --project="$PROJECT" \
  --filter="displayName=\"$POLICY_NAME\"" \
  --format="table(displayName,enabled,conditions[0].displayName)"
