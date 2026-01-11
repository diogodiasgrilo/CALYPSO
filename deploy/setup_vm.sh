#!/bin/bash
# =============================================================================
# Calypso Trading Bot - GCP VM Setup Script
# =============================================================================
# This script sets up a fresh GCP Compute Engine VM for running the
# Calypso Delta Neutral Trading Bot.
#
# Prerequisites:
# - GCP project with Secret Manager secrets configured
# - Service account with Secret Manager accessor role
# - VM with at least 2GB RAM (e2-small or larger)
#
# Usage:
#   chmod +x setup_vm.sh
#   sudo ./setup_vm.sh
#
# After setup:
#   sudo systemctl start calypso
#   sudo journalctl -u calypso -f
# =============================================================================

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${GREEN}=============================================${NC}"
echo -e "${GREEN} Calypso Trading Bot - VM Setup${NC}"
echo -e "${GREEN}=============================================${NC}"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Please run as root (sudo ./setup_vm.sh)${NC}"
    exit 1
fi

# =============================================================================
# Step 1: Update system packages
# =============================================================================
echo -e "${YELLOW}[1/9] Updating system packages...${NC}"
apt-get update && apt-get upgrade -y
echo -e "${GREEN}Done.${NC}"
echo ""

# =============================================================================
# Step 2: Install Python 3.11+ and dependencies
# =============================================================================
echo -e "${YELLOW}[2/9] Installing Python and dependencies...${NC}"
apt-get install -y python3 python3-pip python3-venv git curl wget
echo -e "${GREEN}Done.${NC}"
echo ""

# =============================================================================
# Step 3: Create calypso user
# =============================================================================
echo -e "${YELLOW}[3/9] Creating calypso user...${NC}"
if ! id "calypso" &>/dev/null; then
    useradd -r -s /bin/bash -d /opt/calypso -m calypso
    echo -e "${GREEN}User 'calypso' created.${NC}"
else
    echo -e "${BLUE}User 'calypso' already exists.${NC}"
fi
echo ""

# =============================================================================
# Step 4: Create directories
# =============================================================================
echo -e "${YELLOW}[4/9] Creating directories...${NC}"
mkdir -p /opt/calypso
mkdir -p /opt/calypso/logs
mkdir -p /var/log/calypso
echo -e "${GREEN}Done.${NC}"
echo ""

# =============================================================================
# Step 5: Copy application files
# =============================================================================
echo -e "${YELLOW}[5/9] Setting up application...${NC}"

# Check if we're running from the repo
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

if [ -f "$REPO_DIR/src/main.py" ]; then
    echo "Copying application files from $REPO_DIR..."
    cp -r "$REPO_DIR/src" /opt/calypso/
    cp -r "$REPO_DIR/config" /opt/calypso/
    cp "$REPO_DIR/requirements.txt" /opt/calypso/

    # Remove sensitive local config (will use Secret Manager)
    rm -f /opt/calypso/config/config.json
    rm -f /opt/calypso/config/google_credentials.json

    echo -e "${GREEN}Application files copied.${NC}"
else
    echo -e "${RED}Application files not found!${NC}"
    echo "Please copy the application files to /opt/calypso manually:"
    echo "  - src/ directory"
    echo "  - requirements.txt"
    echo ""
    echo "Or clone from your git repository:"
    echo "  git clone https://github.com/your-repo/calypso.git /opt/calypso"
fi
echo ""

# =============================================================================
# Step 6: Create Python virtual environment
# =============================================================================
echo -e "${YELLOW}[6/9] Setting up Python virtual environment...${NC}"
cd /opt/calypso

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo "Virtual environment created."
fi

source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate

echo -e "${GREEN}Done.${NC}"
echo ""

# =============================================================================
# Step 7: Set permissions
# =============================================================================
echo -e "${YELLOW}[7/9] Setting permissions...${NC}"
chown -R calypso:calypso /opt/calypso
chown -R calypso:calypso /var/log/calypso
chmod 755 /opt/calypso
chmod 755 /var/log/calypso
echo -e "${GREEN}Done.${NC}"
echo ""

# =============================================================================
# Step 8: Install systemd service
# =============================================================================
echo -e "${YELLOW}[8/9] Installing systemd service...${NC}"
if [ -f "/opt/calypso/deploy/calypso.service" ]; then
    cp /opt/calypso/deploy/calypso.service /etc/systemd/system/
elif [ -f "$SCRIPT_DIR/calypso.service" ]; then
    cp "$SCRIPT_DIR/calypso.service" /etc/systemd/system/
else
    echo -e "${RED}calypso.service not found!${NC}"
    echo "Please copy it to /etc/systemd/system/ manually."
fi

systemctl daemon-reload
systemctl enable calypso.service
echo -e "${GREEN}Done.${NC}"
echo ""

# =============================================================================
# Step 9: Install Google Cloud Ops Agent (optional but recommended)
# =============================================================================
echo -e "${YELLOW}[9/9] Installing Google Cloud Ops Agent...${NC}"
if command -v google_cloud_ops_agent_diagnostics &> /dev/null; then
    echo -e "${BLUE}Ops Agent already installed.${NC}"
else
    curl -sSO https://dl.google.com/cloudagents/add-google-cloud-ops-agent-repo.sh
    bash add-google-cloud-ops-agent-repo.sh --also-install
    rm add-google-cloud-ops-agent-repo.sh
    echo -e "${GREEN}Ops Agent installed.${NC}"
fi
echo ""

# =============================================================================
# Summary
# =============================================================================
echo -e "${GREEN}=============================================${NC}"
echo -e "${GREEN} Setup Complete!${NC}"
echo -e "${GREEN}=============================================${NC}"
echo ""
echo -e "${BLUE}Next steps:${NC}"
echo ""
echo "1. Configure secrets in GCP Secret Manager:"
echo "   - calypso-saxo-credentials"
echo "   - calypso-google-sheets-credentials"
echo "   - calypso-account-config"
echo "   - calypso-email-config"
echo ""
echo "2. Update the GCP_PROJECT in /etc/systemd/system/calypso.service"
echo "   sudo nano /etc/systemd/system/calypso.service"
echo ""
echo "3. Start the service:"
echo "   sudo systemctl start calypso"
echo ""
echo "4. Check status:"
echo "   sudo systemctl status calypso"
echo ""
echo "5. View logs:"
echo "   sudo journalctl -u calypso -f"
echo ""
echo -e "${GREEN}=============================================${NC}"
