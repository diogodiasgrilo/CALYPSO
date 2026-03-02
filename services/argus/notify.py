#!/usr/bin/env python3
"""
ARGUS alert notifier — sends health check failures via Telegram/Email.

Reads failure message from stdin (piped from health_check.sh).
Loads agents_config.json and sends via AlertService.

Usage (from health_check.sh):
    echo "ARGUS FAIL: ..." | python notify.py
"""

import json
import logging
import os
import sys

# Ensure project root is on path
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("argus.notify")

# Config path (on VM)
CONFIG_PATH = os.path.join(_project_root, "services", "agents_config.json")
# Fallback: use any bot config that has alert settings
FALLBACK_CONFIG_PATH = os.path.join(_project_root, "bots", "hydra", "config", "config.json")


def load_config() -> dict:
    """Load agent config, falling back to HYDRA config for alert credentials."""
    for path in [CONFIG_PATH, FALLBACK_CONFIG_PATH]:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    config = json.load(f)
                logger.debug(f"Loaded config from {path}")
                return config
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to load {path}: {e}")
    return {}


def main():
    """Read failure message from stdin and send alert."""
    message = sys.stdin.read().strip()
    if not message:
        logger.warning("No message on stdin — nothing to send")
        return

    config = load_config()
    if not config.get("alerts", {}).get("enabled", False):
        logger.info("Alerts disabled in config — printing to stdout only")
        print(message)
        return

    try:
        from shared.alert_service import AlertService, AlertType, AlertPriority

        alert_service = AlertService(config, "ARGUS")
        alert_service.send_alert(
            alert_type=AlertType.DATA_QUALITY,
            title="ARGUS Health Check Failed",
            message=message,
            priority=AlertPriority.HIGH,
        )
        logger.info("Alert sent successfully")
    except Exception as e:
        logger.error(f"Failed to send alert: {e}")
        # Print to stdout as fallback (captured by journalctl)
        print(f"ALERT DELIVERY FAILED: {message}")


if __name__ == "__main__":
    main()
