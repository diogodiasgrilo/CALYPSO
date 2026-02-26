#!/usr/bin/env bash
# ============================================================================
# MEIC-TF Bot — Google Cloud Monitoring Dashboard Setup
#
# This script:
#   1. Grants the VM service account permissions to write logs & metrics
#   2. Deploys the Ops Agent config to collect meic_tf journald + file logs
#   3. Creates a Cloud Monitoring dashboard with live heartbeat feed
#   4. Optionally grants a viewer (e.g., Dad) read-only access
#
# Usage:
#   chmod +x deploy/setup-monitoring-dashboard.sh
#   ./deploy/setup-monitoring-dashboard.sh [VIEWER_EMAIL]
#
# Example:
#   ./deploy/setup-monitoring-dashboard.sh dad@gmail.com
#
# Prerequisites:
#   - gcloud CLI authenticated with project owner/editor access
#   - SSH access to calypso-bot VM
#
# Last Updated: 2026-02-26
# ============================================================================

set -euo pipefail

PROJECT="calypso-trading-bot"
VM_NAME="calypso-bot"
ZONE="us-east1-b"
SA_EMAIL="calypso-vm@${PROJECT}.iam.gserviceaccount.com"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VIEWER_EMAIL="${1:-}"

echo "============================================"
echo "  MEIC-TF Monitoring Dashboard Setup"
echo "============================================"
echo ""
echo "Project:  ${PROJECT}"
echo "VM:       ${VM_NAME} (${ZONE})"
echo "SA:       ${SA_EMAIL}"
if [[ -n "${VIEWER_EMAIL}" ]]; then
    echo "Viewer:   ${VIEWER_EMAIL}"
fi
echo ""

# ------------------------------------------------------------------
# Step 1: Grant IAM roles for Ops Agent to write logs & metrics
# ------------------------------------------------------------------
echo "--- Step 1: Granting IAM roles to VM service account ---"

for ROLE in "roles/logging.logWriter" "roles/monitoring.metricWriter"; do
    echo "  Granting ${ROLE}..."
    gcloud projects add-iam-policy-binding "${PROJECT}" \
        --member="serviceAccount:${SA_EMAIL}" \
        --role="${ROLE}" \
        --condition=None \
        --quiet \
        > /dev/null 2>&1
    echo "  OK: ${ROLE}"
done
echo ""

# ------------------------------------------------------------------
# Step 2: Deploy Ops Agent config to VM
# ------------------------------------------------------------------
echo "--- Step 2: Deploying Ops Agent config to VM ---"

# Push the config file and restart the agent
gcloud compute ssh "${VM_NAME}" --zone="${ZONE}" --command="
    sudo cp /opt/calypso/deploy/ops-agent-config.yaml /etc/google-cloud-ops-agent/config.yaml
    sudo systemctl restart google-cloud-ops-agent
    sleep 3
    sudo systemctl is-active google-cloud-ops-agent-fluent-bit && echo 'Fluent Bit: RUNNING' || echo 'Fluent Bit: FAILED'
    sudo systemctl is-active google-cloud-ops-agent-opentelemetry-collector && echo 'OpenTelemetry: RUNNING' || echo 'OpenTelemetry: FAILED'
"
echo ""

# ------------------------------------------------------------------
# Step 3: Wait for logs to appear, then verify
# ------------------------------------------------------------------
echo "--- Step 3: Waiting 15s for first logs to arrive in Cloud Logging ---"
sleep 15

echo "  Checking for meic_tf logs..."
LOG_COUNT=$(gcloud logging read \
    "resource.type=\"gce_instance\" AND log_id(\"meic_tf_logfile\")" \
    --limit=3 \
    --project="${PROJECT}" \
    --format="value(jsonPayload.message)" 2>/dev/null | wc -l || echo "0")

if [[ "${LOG_COUNT}" -gt 0 ]]; then
    echo "  SUCCESS: Found ${LOG_COUNT} log entries in Cloud Logging"
else
    echo "  WARNING: No logs found yet. This may take up to 60 seconds."
    echo "  Verify manually:"
    echo "    gcloud logging read 'log_id(\"meic_tf_logfile\")' --limit=5 --project=${PROJECT}"
fi
echo ""

# ------------------------------------------------------------------
# Step 4: Create Cloud Monitoring Dashboard
# ------------------------------------------------------------------
echo "--- Step 4: Creating Cloud Monitoring Dashboard ---"

# Check if dashboard already exists
EXISTING=$(gcloud monitoring dashboards list \
    --project="${PROJECT}" \
    --format="value(name)" \
    --filter="displayName='MEIC-TF Bot - Live Dashboard'" 2>/dev/null || true)

if [[ -n "${EXISTING}" ]]; then
    echo "  Dashboard already exists. Deleting old version..."
    gcloud monitoring dashboards delete "${EXISTING}" \
        --project="${PROJECT}" \
        --quiet 2>/dev/null
fi

# Create the dashboard
DASHBOARD_ID=$(gcloud monitoring dashboards create \
    --config-from-file="${SCRIPT_DIR}/dashboard-meic-tf.json" \
    --project="${PROJECT}" \
    --format="value(name)" 2>/dev/null)

echo "  Dashboard created: ${DASHBOARD_ID}"

# Extract the dashboard ID for URL
DASH_SHORT_ID=$(echo "${DASHBOARD_ID}" | sed 's|projects/.*/dashboards/||')
DASHBOARD_URL="https://console.cloud.google.com/monitoring/dashboards/builder/${DASH_SHORT_ID}?project=${PROJECT}"

echo ""
echo "  Dashboard URL:"
echo "  ${DASHBOARD_URL}"
echo ""

# ------------------------------------------------------------------
# Step 5: Grant viewer access (optional)
# ------------------------------------------------------------------
if [[ -n "${VIEWER_EMAIL}" ]]; then
    echo "--- Step 5: Granting Monitoring Viewer access to ${VIEWER_EMAIL} ---"

    gcloud projects add-iam-policy-binding "${PROJECT}" \
        --member="user:${VIEWER_EMAIL}" \
        --role="roles/monitoring.viewer" \
        --condition=None \
        --quiet \
        > /dev/null 2>&1

    # Also grant logs viewer so the logsPanel works
    gcloud projects add-iam-policy-binding "${PROJECT}" \
        --member="user:${VIEWER_EMAIL}" \
        --role="roles/logging.viewer" \
        --condition=None \
        --quiet \
        > /dev/null 2>&1

    echo "  OK: ${VIEWER_EMAIL} can now view the dashboard"
    echo ""
    echo "  Send this link to ${VIEWER_EMAIL}:"
    echo "  ${DASHBOARD_URL}"
    echo ""
    echo "  They'll need to:"
    echo "  1. Sign in with their Google account (${VIEWER_EMAIL})"
    echo "  2. Accept the project invitation (first time only)"
    echo "  3. Bookmark the URL — logs scroll automatically"
else
    echo "--- Step 5: Skipped (no viewer email provided) ---"
    echo "  To add a viewer later:"
    echo "    ./deploy/setup-monitoring-dashboard.sh dad@gmail.com"
fi

echo ""
echo "============================================"
echo "  Setup Complete!"
echo "============================================"
echo ""
echo "Dashboard URL:"
echo "  ${DASHBOARD_URL}"
echo ""
echo "Verify logs manually:"
echo "  gcloud logging read 'log_id(\"meic_tf_logfile\")' --limit=5 --project=${PROJECT}"
echo ""
echo "View live heartbeats via CLI:"
echo "  gcloud compute ssh ${VM_NAME} --zone=${ZONE} --command=\"sudo journalctl -u meic_tf -f\" | grep HEARTBEAT"
echo ""
