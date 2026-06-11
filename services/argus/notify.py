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
import re
import sys
import time

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

# Cross-run dedup: ARGUS is a oneshot that runs every 15 min, so each invocation
# is a FRESH process with a fresh in-process AlertService gate — that gate can't
# dedup across runs. Without this, a persistent failure (e.g. bot down for 2h)
# re-emails "Health Check Failed" every 15 min (8 identical emails). We persist
# the last alert's fingerprint + timestamp to a small state file and skip the
# send if the SAME failure already alerted within ARGUS_DEDUP_WINDOW_S. A
# genuinely NEW failure (different message) fingerprints differently and sends.
ARGUS_STATE_PATH = os.path.join(_project_root, "data", "argus_alert_state.json")
ARGUS_DEDUP_WINDOW_S = 3600  # re-ping a persistent failure at most once/hour
_VOLATILE_RE = re.compile(r"\$?\s?-?\d[\d,]*(?:\.\d+)?%?")


def _fingerprint(message: str) -> str:
    """Stable identity for a failure message, ignoring volatile numbers/times so
    'CPU 91%' and 'CPU 88%' (same failure) collapse but a different check does not."""
    return re.sub(r"\s+", " ", _VOLATILE_RE.sub("#", message.lower())).strip()


def _recently_alerted(fingerprint: str) -> bool:
    """True if this fingerprint was alerted within the dedup window. Fail-open:
    any read error returns False (send the alert) so dedup never silences a fault."""
    try:
        with open(ARGUS_STATE_PATH) as f:
            state = json.load(f)
        if state.get("fingerprint") == fingerprint:
            return (time.time() - float(state.get("sent_at", 0))) < ARGUS_DEDUP_WINDOW_S
    except (IOError, ValueError, json.JSONDecodeError):
        pass
    return False


def _record_alert(fingerprint: str) -> None:
    """Persist the fingerprint + send time. Non-fatal on failure (best-effort)."""
    try:
        os.makedirs(os.path.dirname(ARGUS_STATE_PATH), exist_ok=True)
        tmp = ARGUS_STATE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"fingerprint": fingerprint, "sent_at": time.time()}, f)
        os.replace(tmp, ARGUS_STATE_PATH)
    except (IOError, OSError) as e:
        logger.warning(f"Could not persist ARGUS alert state: {e}")


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

    # Cross-run dedup: skip if this exact failure already alerted within the hour.
    fingerprint = _fingerprint(message)
    if _recently_alerted(fingerprint):
        logger.info(
            "Same health failure alerted within %ds — suppressing duplicate email. %s",
            ARGUS_DEDUP_WINDOW_S, message,
        )
        print(message)  # still visible in journalctl, just no repeat email
        return

    try:
        from shared.alert_service import AlertService, AlertType, AlertPriority

        alert_service = AlertService(config, "ARGUS")
        alert_service.send_alert(
            alert_type=AlertType.DATA_QUALITY,
            title="Health Check Failed",
            message=message,
            priority=AlertPriority.HIGH,
        )
        _record_alert(fingerprint)
        logger.info("Alert sent successfully")
    except Exception as e:
        logger.error(f"Failed to send alert: {e}")
        # Print to stdout as fallback (captured by journalctl)
        print(f"ALERT DELIVERY FAILED: {message}")


if __name__ == "__main__":
    main()
