#!/usr/bin/env bash
# ============================================================================
# HYDRA Bot — VM Deployment Script (MEIC-TF → HYDRA Rename)
#
# This script handles the one-time switchover from meic_tf to hydra on the VM:
#   1. Stops the old meic_tf service
#   2. Pulls latest code (with HYDRA rename)
#   3. Updates the live config file
#   4. Renames state/metrics data files
#   5. Creates new log directory
#   6. Installs new hydra systemd service
#   7. Removes old meic_tf service
#   8. Updates Ops Agent config for new log path
#   9. Starts the HYDRA service
#
# Usage:
#   # From your local machine (run as default SSH user who has sudo):
#   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo bash /opt/calypso/deploy/deploy-hydra-rename.sh"
#
# Prerequisites:
#   - Code already pushed to git with HYDRA rename
#   - SSH access to calypso-bot VM
#   - Must run with sudo (script needs systemctl, cp to /etc/systemd/)
#
# Last Updated: 2026-02-28
# ============================================================================

set -euo pipefail

CALYPSO_DIR="/opt/calypso"
OLD_SERVICE="meic_tf"
NEW_SERVICE="hydra"

echo "============================================"
echo "  HYDRA Deployment (MEIC-TF → HYDRA)"
echo "============================================"
echo ""

# ------------------------------------------------------------------
# Safety check: must be run from the calypso directory
# ------------------------------------------------------------------
if [[ ! -d "${CALYPSO_DIR}/bots" ]]; then
    echo "ERROR: ${CALYPSO_DIR}/bots not found. Are you on the right VM?"
    exit 1
fi

cd "${CALYPSO_DIR}"

# ------------------------------------------------------------------
# Step 1: Stop the old meic_tf service
# ------------------------------------------------------------------
echo "--- Step 1: Stopping old ${OLD_SERVICE} service ---"

if sudo systemctl is-active --quiet "${OLD_SERVICE}" 2>/dev/null; then
    sudo systemctl stop "${OLD_SERVICE}"
    echo "  Stopped ${OLD_SERVICE}"
else
    echo "  ${OLD_SERVICE} was not running"
fi
echo ""

# ------------------------------------------------------------------
# Step 2: Pull latest code and clear Python cache
# ------------------------------------------------------------------
echo "--- Step 2: Pulling latest code ---"

sudo -u calypso git pull
sudo -u calypso find bots shared -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
echo "  Code updated, cache cleared"
echo ""

# ------------------------------------------------------------------
# Step 3: Update the live config file
# ------------------------------------------------------------------
echo "--- Step 3: Updating live config ---"

CONFIG_FILE="${CALYPSO_DIR}/bots/hydra/config/config.json"

if [[ -f "${CONFIG_FILE}" ]]; then
    # Use Python to safely update JSON config (run as calypso for file ownership)
    sudo -u calypso .venv/bin/python << 'PYCONFIG'
import json
import shutil

config_path = "/opt/calypso/bots/hydra/config/config.json"

# Backup first
shutil.copy2(config_path, config_path + ".bak")

with open(config_path, "r") as f:
    config = json.load(f)

# Update bot name
config["bot_name"] = "HYDRA"

# Update logging section
if "logging" in config:
    config["logging"]["log_dir"] = "logs/hydra"
    config["logging"]["spreadsheet_name"] = "Calypso_HYDRA_Live_Data"
    config["logging"]["strategy_type"] = "hydra"

with open(config_path, "w") as f:
    json.dump(config, f, indent=4)

print("  Config updated:")
print(f"    bot_name = {config['bot_name']}")
print(f"    log_dir = {config['logging']['log_dir']}")
print(f"    spreadsheet_name = {config['logging']['spreadsheet_name']}")
print(f"    strategy_type = {config['logging']['strategy_type']}")
print(f"    Backup saved: {config_path}.bak")
PYCONFIG
else
    echo "  WARNING: Config file not found at ${CONFIG_FILE}"
    echo "  You may need to create it from the template"
fi
echo ""

# ------------------------------------------------------------------
# Step 4: Rename state and metrics files
# ------------------------------------------------------------------
echo "--- Step 4: Renaming state/metrics files ---"

DATA_DIR="${CALYPSO_DIR}/data"

if [[ -f "${DATA_DIR}/meic_tf_state.json" ]]; then
    cp "${DATA_DIR}/meic_tf_state.json" "${DATA_DIR}/meic_tf_state.json.bak"
    mv "${DATA_DIR}/meic_tf_state.json" "${DATA_DIR}/hydra_state.json"
    echo "  Renamed: meic_tf_state.json → hydra_state.json (backup saved)"
else
    echo "  No meic_tf_state.json found (OK if no active session)"
fi

if [[ -f "${DATA_DIR}/meic_metrics.json" ]]; then
    # Note: HYDRA was using meic_metrics.json via the parent class (pre-audit bug, now fixed)
    cp "${DATA_DIR}/meic_metrics.json" "${DATA_DIR}/meic_metrics.json.bak"
    cp "${DATA_DIR}/meic_metrics.json" "${DATA_DIR}/hydra_metrics.json"
    echo "  Copied: meic_metrics.json → hydra_metrics.json (original preserved for base MEIC)"
else
    echo "  No meic_metrics.json found (OK if fresh start)"
fi
echo ""

# ------------------------------------------------------------------
# Step 5: Create new log directory
# ------------------------------------------------------------------
echo "--- Step 5: Creating log directory ---"

mkdir -p "${CALYPSO_DIR}/logs/hydra"
echo "  Created: logs/hydra/"

# Optionally symlink old logs for reference
if [[ -d "${CALYPSO_DIR}/logs/meic_tf" ]]; then
    echo "  Old logs preserved at: logs/meic_tf/"
fi
echo ""

# ------------------------------------------------------------------
# Step 6: Install new hydra systemd service
# ------------------------------------------------------------------
echo "--- Step 6: Installing HYDRA systemd service ---"

sudo cp "${CALYPSO_DIR}/bots/hydra/hydra.service" /etc/systemd/system/hydra.service
sudo systemctl daemon-reload
sudo systemctl enable hydra
echo "  Installed and enabled: hydra.service"
echo ""

# ------------------------------------------------------------------
# Step 7: Remove old meic_tf service
# ------------------------------------------------------------------
echo "--- Step 7: Removing old meic_tf service ---"

sudo systemctl disable "${OLD_SERVICE}" 2>/dev/null || true
if [[ -f "/etc/systemd/system/${OLD_SERVICE}.service" ]]; then
    sudo rm "/etc/systemd/system/${OLD_SERVICE}.service"
    sudo systemctl daemon-reload
    echo "  Removed: ${OLD_SERVICE}.service"
else
    echo "  ${OLD_SERVICE}.service not found (already removed?)"
fi
echo ""

# ------------------------------------------------------------------
# Step 8: Update Ops Agent config for new log path
# ------------------------------------------------------------------
echo "--- Step 8: Updating Ops Agent config ---"

if [[ -f "${CALYPSO_DIR}/deploy/ops-agent-config.yaml" ]]; then
    sudo cp "${CALYPSO_DIR}/deploy/ops-agent-config.yaml" /etc/google-cloud-ops-agent/config.yaml
    sudo systemctl restart google-cloud-ops-agent 2>/dev/null || true
    echo "  Ops Agent config updated (hydra log path)"
else
    echo "  WARNING: ops-agent-config.yaml not found"
fi
echo ""

# ------------------------------------------------------------------
# Step 9: Start HYDRA service
# ------------------------------------------------------------------
echo "--- Step 9: Starting HYDRA service ---"

sudo systemctl start hydra
sleep 3

if sudo systemctl is-active --quiet hydra; then
    echo "  HYDRA is RUNNING"
else
    echo "  WARNING: HYDRA failed to start!"
    echo "  Check logs: sudo journalctl -u hydra -n 30 --no-pager"
fi
echo ""

# ------------------------------------------------------------------
# Verification
# ------------------------------------------------------------------
echo "============================================"
echo "  Deployment Complete — Verification"
echo "============================================"
echo ""

echo "Service status:"
sudo systemctl status hydra --no-pager -l 2>/dev/null | head -15
echo ""

echo "Recent logs:"
sudo journalctl -u hydra -n 10 --no-pager 2>/dev/null || echo "  (no logs yet)"
echo ""

echo "Data files:"
ls -la "${DATA_DIR}"/hydra_*.json 2>/dev/null || echo "  (none yet)"
echo ""

echo "Old service removed:"
if sudo systemctl is-enabled "${OLD_SERVICE}" 2>/dev/null; then
    echo "  WARNING: ${OLD_SERVICE} is still enabled!"
else
    echo "  OK: ${OLD_SERVICE} is disabled/removed"
fi
echo ""

echo "============================================"
echo "  Quick Reference"
echo "============================================"
echo ""
echo "  Status:   sudo systemctl status hydra"
echo "  Logs:     sudo journalctl -u hydra -f"
echo "  Stop:     sudo systemctl stop hydra"
echo "  Restart:  sudo systemctl restart hydra"
echo ""
echo "  Rollback: Restore .bak files in data/ and re-enable meic_tf service"
echo ""
