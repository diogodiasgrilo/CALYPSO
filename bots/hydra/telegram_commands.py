"""
Telegram Command Handler for HYDRA Bot.

Polls Telegram's getUpdates API for incoming commands and responds directly.
Runs as a background daemon thread — independent of the main trading loop.

Supported commands:
    /status   — Bot state, market data, uptime, filters
    /snapshot — Live position snapshot (market hours only)
    /entry N  — Details for entry #N
    /lastday  — Most recent complete trading day performance breakdown
    /week     — Current week summary
    /account  — Lifetime HYDRA strategy performance summary
    /stops    — Stop loss analysis (today + lifetime)
    /config   — Current configuration (read-only view)
    /set      — Edit config parameter (requires /restart to apply)
    /hermes   — Latest HERMES daily report
    /apollo   — Latest APOLLO morning briefing
    /restart  — Restart the HYDRA service
    /stop     — Stop the HYDRA service (warns if active positions)
    /help     — List all commands

Security: Only responds to messages from the configured chat_id.

Version: 2.0.0 (2026-03-05)
"""

import fcntl
import json
import logging
import os
import re
import subprocess
import threading
import time
from typing import Callable, List, Optional

import requests

from shared.secret_manager import get_secret
from shared.market_hours import is_market_open, get_us_market_time, is_weekend, get_holiday_name

logger = logging.getLogger(__name__)

POLL_INTERVAL = 5       # seconds between getUpdates calls
REQUEST_TIMEOUT = 10    # HTTP timeout for Telegram API calls
MAX_MESSAGE_LENGTH = 4096  # Telegram message limit
# Only log persistent Telegram failures every 60th attempt (every 5 min)
ERROR_LOG_INTERVAL = 60

# =========================================================================
# EDITABLE CONFIG PARAMETERS
# friendly_name → {path, type, min, max, unit, description}
# path uses dot-notation for nested keys: "strategy.key" → config["strategy"]["key"]
# =========================================================================
EDITABLE_PARAMS = {
    "min_credit_call": {
        "path": "strategy.min_viable_credit_per_side",
        "type": "float",
        "min": 0.25, "max": 3.00,
        "unit": "$",
        "description": "Min call credit per side",
    },
    "min_credit_put": {
        "path": "strategy.min_viable_credit_put_side",
        "type": "float",
        "min": 0.50, "max": 5.00,
        "unit": "$",
        "description": "Min put credit per side",
    },
    "max_vix": {
        "path": "strategy.max_vix_entry",
        "type": "float",
        "min": 15, "max": 50,
        "description": "Max VIX for entry",
    },
    "contracts": {
        "path": "strategy.contracts_per_entry",
        "type": "int",
        "min": 1, "max": 5,
        "description": "Contracts per entry",
    },
    "meic_plus": {
        "path": "strategy.meic_plus_reduction",
        "type": "float",
        "min": 0.00, "max": 1.00,
        "unit": "$",
        "description": "MEIC+ commission buffer",
    },
    "smart_entry": {
        "path": "smart_entry.enabled",
        "type": "bool",
        "description": "Smart entry windows (MKT-031)",
    },
    "smart_threshold": {
        "path": "smart_entry.score_threshold",
        "type": "int",
        "min": 30, "max": 100,
        "description": "Smart entry score threshold",
    },
    "trend_filter": {
        "path": "trend_filter.enabled",
        "type": "bool",
        "description": "EMA trend filter",
    },
    "trend_threshold": {
        "path": "trend_filter.ema_neutral_threshold",
        "type": "float",
        "min": 0.0005, "max": 0.01,
        "description": "Trend neutral threshold",
    },
    "early_close": {
        "path": "strategy.early_close_enabled",
        "type": "bool",
        "description": "Early close (MKT-018)",
    },
    "early_close_pct": {
        "path": "strategy.early_close_roc_threshold",
        "type": "float",
        "min": 0.01, "max": 0.10,
        "unit": "%",
        "description": "Early close ROC threshold",
    },
    "hold_check": {
        "path": "strategy.hold_check_enabled",
        "type": "bool",
        "description": "Hold check (MKT-023)",
    },
    "entry_times": {
        "path": "strategy.entry_times",
        "type": "times",
        "description": "Entry schedule (HH:MM,...)",
    },
}


class TelegramCommandHandler:
    """
    Handles incoming Telegram commands for HYDRA.

    Polls getUpdates in a daemon thread, routes commands, responds via Bot API.
    """

    def __init__(self):
        self._bot_token: Optional[str] = None
        self._chat_id: Optional[str] = None
        self._enabled = False
        self._offset: Optional[int] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._snapshot_callback: Optional[Callable[[], str]] = None
        self._lastday_callback: Optional[Callable[[], str]] = None
        self._account_callback: Optional[Callable[[], str]] = None
        self._status_callback: Optional[Callable[[], str]] = None
        self._hermes_callback: Optional[Callable[[], str]] = None
        self._apollo_callback: Optional[Callable[[], str]] = None
        self._week_callback: Optional[Callable[[], str]] = None
        self._entry_callback: Optional[Callable[[int], str]] = None
        self._stops_callback: Optional[Callable[[], str]] = None
        self._config_callback: Optional[Callable[[], str]] = None
        self._active_positions_callback: Optional[Callable[[], int]] = None
        self._config_path: Optional[str] = None
        self._consecutive_errors = 0
        # /stop confirmation state
        self._pending_stop_confirm = False
        self._pending_stop_time: Optional[float] = None

        self._load_credentials()

    def _load_credentials(self):
        """Fetch Telegram credentials from Secret Manager."""
        try:
            secret_value = get_secret("calypso-telegram-credentials")
            if not secret_value:
                logger.warning("Telegram command handler: credentials not available (Secret Manager)")
                return

            creds = json.loads(secret_value)
            self._bot_token = creds.get("bot_token", "")
            self._chat_id = str(creds.get("chat_id", ""))

            if self._bot_token and self._chat_id:
                self._enabled = True
            else:
                logger.warning("Telegram command handler: bot_token or chat_id missing in credentials")
        except Exception as e:
            logger.warning(f"Telegram command handler: failed to load credentials: {e}")

    def start(
        self,
        snapshot_callback: Callable[[], str],
        lastday_callback: Optional[Callable[[], str]] = None,
        account_callback: Optional[Callable[[], str]] = None,
        status_callback: Optional[Callable[[], str]] = None,
        hermes_callback: Optional[Callable[[], str]] = None,
        apollo_callback: Optional[Callable[[], str]] = None,
        week_callback: Optional[Callable[[], str]] = None,
        entry_callback: Optional[Callable[[int], str]] = None,
        stops_callback: Optional[Callable[[], str]] = None,
        config_callback: Optional[Callable[[], str]] = None,
        config_path: Optional[str] = None,
        active_positions_callback: Optional[Callable[[], int]] = None,
    ):
        """Start the background polling thread."""
        self._snapshot_callback = snapshot_callback
        self._lastday_callback = lastday_callback
        self._account_callback = account_callback
        self._status_callback = status_callback
        self._hermes_callback = hermes_callback
        self._apollo_callback = apollo_callback
        self._week_callback = week_callback
        self._entry_callback = entry_callback
        self._stops_callback = stops_callback
        self._config_callback = config_callback
        self._config_path = config_path
        self._active_positions_callback = active_positions_callback

        if not self._enabled:
            logger.info("Telegram command handler disabled (no credentials)")
            return

        # Clear any webhook that might block getUpdates
        self._delete_webhook()

        # Flush old updates so we don't process stale commands from downtime
        self._flush_old_updates()

        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="telegram_commands"
        )
        self._thread.start()
        logger.info("Telegram command handler started (polling every %ds)", POLL_INTERVAL)

    def stop(self):
        """Stop the background polling thread."""
        if not self._running:
            return
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        logger.info("Telegram command handler stopped")

    # =========================================================================
    # BACKGROUND POLLING
    # =========================================================================

    def _poll_loop(self):
        """Main polling loop — runs in daemon thread."""
        while self._running:
            try:
                self._check_commands()
                self._consecutive_errors = 0
            except Exception as e:
                self._consecutive_errors += 1
                # Rate-limit error logging to avoid spam during Telegram outages
                if self._consecutive_errors % ERROR_LOG_INTERVAL == 1:
                    logger.warning(
                        "Telegram command poll failed (error #%d): %s",
                        self._consecutive_errors, e
                    )
            time.sleep(POLL_INTERVAL)

    def _check_commands(self):
        """Fetch new updates from Telegram and route commands."""
        params = {"timeout": 0, "allowed_updates": ["message"]}
        if self._offset is not None:
            params["offset"] = self._offset

        url = f"https://api.telegram.org/bot{self._bot_token}/getUpdates"
        resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return

        data = resp.json()
        if not data.get("ok"):
            return

        for update in data.get("result", []):
            self._offset = update["update_id"] + 1

            message = update.get("message", {})
            text = (message.get("text") or "").strip()
            chat_id = str(message.get("chat", {}).get("id", ""))

            # Security: ignore messages from unauthorized chats
            if chat_id != self._chat_id:
                continue

            if text.startswith("/snapshot"):
                self._handle_snapshot(chat_id)
            elif text.startswith("/status"):
                self._handle_status(chat_id)
            elif text.startswith("/entry"):
                self._handle_entry(chat_id, text)
            elif text.startswith("/lastday"):
                self._handle_lastday(chat_id)
            elif text.startswith("/week"):
                self._handle_week(chat_id)
            elif text.startswith("/account"):
                self._handle_account(chat_id)
            elif text.startswith("/stops"):
                self._handle_stops(chat_id)
            elif text.startswith("/stop"):
                self._handle_stop(chat_id, text)
            elif text.startswith("/set"):
                self._handle_set(chat_id, text)
            elif text.startswith("/config"):
                self._handle_config(chat_id)
            elif text.startswith("/hermes"):
                self._handle_hermes(chat_id)
            elif text.startswith("/apollo"):
                self._handle_apollo(chat_id)
            elif text.startswith("/restart"):
                self._handle_restart(chat_id)
            elif text.startswith("/help"):
                self._handle_help(chat_id)

    # =========================================================================
    # COMMAND HANDLERS
    # =========================================================================

    def _handle_snapshot(self, chat_id: str):
        """Handle /snapshot command."""
        now_et = get_us_market_time()

        if not is_market_open():
            # Build market-closed message
            if is_weekend(now_et):
                reason = "weekend"
            elif get_holiday_name(now_et):
                reason = get_holiday_name(now_et)
            elif now_et.hour < 9 or (now_et.hour == 9 and now_et.minute < 30):
                reason = "pre-market"
            else:
                reason = "after hours"

            msg = (
                f"\U0001f4ca *HYDRA* | Market Closed\n"
                f"\n"
                f"Market is currently closed ({reason}).\n"
                f"No live positions to display.\n"
                f"\n"
                f"_Tip: Send /snapshot during market hours (9:30 AM - 4:00 PM ET) for live data._"
            )
            self._send_message(chat_id, msg)
            return

        # Market is open — build live snapshot
        if not self._snapshot_callback:
            self._send_message(chat_id, "Snapshot not available (bot still initializing).")
            return

        try:
            snapshot = self._snapshot_callback()
            time_str = now_et.strftime("%I:%M %p ET")
            msg = f"\U0001f4ca *HYDRA* | Snapshot\n\n{snapshot}\n\n_{time_str}_"
            self._send_message(chat_id, msg)
        except Exception as e:
            logger.error("Failed to build snapshot for /snapshot command: %s", e)
            self._send_message(chat_id, "Snapshot temporarily unavailable. Try again in a minute.")

    def _handle_status(self, chat_id: str):
        """Handle /status command — bot state, market data, filters."""
        if not self._status_callback:
            self._send_message(chat_id, "Status not available (bot still initializing).")
            return

        try:
            msg = self._status_callback()
            self._send_message(chat_id, msg)
        except Exception as e:
            logger.error("Failed to build /status response: %s", e)
            self._send_message(chat_id, "Failed to retrieve status. Try again shortly.")

    def _handle_entry(self, chat_id: str, text: str):
        """Handle /entry N command — details for a specific entry."""
        if not self._entry_callback:
            self._send_message(chat_id, "Entry data not available (bot still initializing).")
            return

        parts = text.split()
        if len(parts) < 2:
            self._send_message(chat_id, "Usage: /entry N (e.g. /entry 1)")
            return

        try:
            entry_num = int(parts[1])
        except ValueError:
            self._send_message(chat_id, "Usage: /entry N (e.g. /entry 1)")
            return

        try:
            msg = self._entry_callback(entry_num)
            self._send_message(chat_id, msg)
        except Exception as e:
            logger.error("Failed to build /entry %d response: %s", entry_num, e)
            self._send_message(chat_id, "Failed to retrieve entry data. Try again shortly.")

    def _handle_lastday(self, chat_id: str):
        """Handle /lastday command — most recent complete trading day."""
        if not self._lastday_callback:
            self._send_message(chat_id, "Last day data not available (bot still initializing).")
            return

        try:
            msg = self._lastday_callback()
            self._send_message(chat_id, msg)
        except Exception as e:
            logger.error("Failed to build /lastday response: %s", e)
            self._send_message(chat_id, "Failed to retrieve last day data. Try again shortly.")

    def _handle_week(self, chat_id: str):
        """Handle /week command — current week summary."""
        if not self._week_callback:
            self._send_message(chat_id, "Week data not available (bot still initializing).")
            return

        try:
            msg = self._week_callback()
            self._send_message(chat_id, msg)
        except Exception as e:
            logger.error("Failed to build /week response: %s", e)
            self._send_message(chat_id, "Failed to retrieve week data. Try again shortly.")

    def _handle_account(self, chat_id: str):
        """Handle /account command — lifetime strategy performance."""
        if not self._account_callback:
            self._send_message(chat_id, "Account data not available (bot still initializing).")
            return

        try:
            msg = self._account_callback()
            self._send_message(chat_id, msg)
        except Exception as e:
            logger.error("Failed to build /account response: %s", e)
            self._send_message(chat_id, "Failed to retrieve account data. Try again shortly.")

    def _handle_stops(self, chat_id: str):
        """Handle /stops command — stop loss analysis."""
        if not self._stops_callback:
            self._send_message(chat_id, "Stop data not available (bot still initializing).")
            return

        try:
            msg = self._stops_callback()
            self._send_message(chat_id, msg)
        except Exception as e:
            logger.error("Failed to build /stops response: %s", e)
            self._send_message(chat_id, "Failed to retrieve stop data. Try again shortly.")

    def _handle_config(self, chat_id: str):
        """Handle /config command — current configuration."""
        if not self._config_callback:
            self._send_message(chat_id, "Config not available (bot still initializing).")
            return

        try:
            msg = self._config_callback()
            self._send_message(chat_id, msg)
        except Exception as e:
            logger.error("Failed to build /config response: %s", e)
            self._send_message(chat_id, "Failed to retrieve config. Try again shortly.")

    def _handle_hermes(self, chat_id: str):
        """Handle /hermes command — latest HERMES daily report."""
        if not self._hermes_callback:
            self._send_message(chat_id, "HERMES report not available.")
            return

        try:
            msg = self._hermes_callback()
            msg = self._sanitize_for_telegram(msg)
            self._send_message(chat_id, msg)
        except Exception as e:
            logger.error("Failed to build /hermes response: %s", e)
            self._send_message(chat_id, "Failed to retrieve HERMES report. Try again shortly.")

    def _handle_apollo(self, chat_id: str):
        """Handle /apollo command — latest APOLLO morning briefing."""
        if not self._apollo_callback:
            self._send_message(chat_id, "APOLLO briefing not available.")
            return

        try:
            msg = self._apollo_callback()
            msg = self._sanitize_for_telegram(msg)
            self._send_message(chat_id, msg)
        except Exception as e:
            logger.error("Failed to build /apollo response: %s", e)
            self._send_message(chat_id, "Failed to retrieve APOLLO briefing. Try again shortly.")

    # =========================================================================
    # /set — CONFIG EDITING
    # =========================================================================

    def _handle_set(self, chat_id: str, text: str):
        """Handle /set command — view or edit config parameters.

        Usage:
            /set                    — List all editable params with current values
            /set param_name         — Show current value + valid range
            /set param_name value   — Update config (requires /restart to apply)
        """
        parts = text.split(maxsplit=2)

        # /set with no args → show all editable params
        if len(parts) == 1:
            self._show_all_params(chat_id)
            return

        param_name = parts[1].lower()

        if param_name not in EDITABLE_PARAMS:
            self._send_message(
                chat_id,
                f"Unknown parameter: {param_name}\n\nSend /set to see all parameters."
            )
            return

        # /set param_name → show current value
        if len(parts) < 3:
            self._show_single_param(chat_id, param_name)
            return

        # /set param_name value → update
        value_str = parts[2].strip()
        param_def = EDITABLE_PARAMS[param_name]

        try:
            validated = self._validate_param_value(param_def, value_str)
        except ValueError as e:
            self._send_message(chat_id, f"Invalid value: {e}")
            return

        if not self._config_path:
            self._send_message(chat_id, "Config path not available.")
            return

        try:
            self._write_config_value(param_def["path"], validated)
            # Format display value
            if param_def["type"] == "bool":
                display = "on" if validated else "off"
            elif param_def["type"] == "times":
                display = ", ".join(validated)
            elif param_def.get("unit") == "$":
                display = f"${validated}"
            elif param_def.get("unit") == "%":
                display = f"{validated * 100:.1f}%"
            else:
                display = str(validated)

            self._send_message(
                chat_id,
                f"\u2705 *Saved:* {param_name} = {display}\n\n"
                f"Restart required to apply.\nSend /restart"
            )
            logger.info("Telegram /set: %s = %s (path: %s)", param_name, validated, param_def["path"])
        except Exception as e:
            logger.error("Failed to write config via /set: %s", e)
            self._send_message(chat_id, f"Failed to save: {e}")

    def _show_all_params(self, chat_id: str):
        """Show all editable parameters with current values from config file."""
        config = self._read_config_file()
        if config is None:
            self._send_message(chat_id, "Cannot read config file.")
            return

        lines = ["\u2699\ufe0f *HYDRA* | Editable Config", ""]
        for name, param_def in EDITABLE_PARAMS.items():
            current = self._get_config_value(config, param_def["path"])
            display = self._format_display_value(current, param_def)
            lines.append(f"`{name}` = {display}")

        lines.append("")
        lines.append("Usage: /set name value")
        lines.append("Example: /set max\\_vix 25")

        self._send_message(chat_id, "\n".join(lines))

    def _show_single_param(self, chat_id: str, param_name: str):
        """Show a single parameter's current value and valid range."""
        param_def = EDITABLE_PARAMS[param_name]
        config = self._read_config_file()

        if config is None:
            self._send_message(chat_id, "Cannot read config file.")
            return

        current = self._get_config_value(config, param_def["path"])
        display = self._format_display_value(current, param_def)

        lines = [
            f"*{param_name}*: {param_def['description']}",
            f"Current: {display}",
        ]

        if param_def["type"] == "bool":
            lines.append("Valid: on / off")
        elif param_def["type"] == "times":
            lines.append("Format: HH:MM,HH:MM,... (09:30-15:30)")
        elif "min" in param_def and "max" in param_def:
            lines.append(f"Range: {param_def['min']} - {param_def['max']}")

        lines.append(f"\nSet: /set {param_name} <value>")

        self._send_message(chat_id, "\n".join(lines))

    def _format_display_value(self, value, param_def: dict) -> str:
        """Format a config value for display."""
        if value is None:
            return "(not set)"
        if param_def["type"] == "bool":
            return "on" if value else "off"
        if param_def["type"] == "times":
            if isinstance(value, list):
                return ", ".join(value)
            return str(value)
        if param_def.get("unit") == "$":
            return f"${value}"
        if param_def.get("unit") == "%":
            if isinstance(value, (int, float)):
                return f"{value * 100:.1f}%"
        return str(value)

    def _validate_param_value(self, param_def: dict, value_str: str):
        """Validate and convert a user-provided parameter value.

        Returns the validated/converted value.
        Raises ValueError if invalid.
        """
        ptype = param_def["type"]

        if ptype == "bool":
            lower = value_str.lower()
            if lower in ("on", "true", "yes", "1"):
                return True
            if lower in ("off", "false", "no", "0"):
                return False
            raise ValueError("Use on/off, true/false, or yes/no")

        if ptype == "int":
            try:
                val = int(value_str)
            except (ValueError, TypeError):
                raise ValueError(f"Must be an integer")
            if "min" in param_def and val < param_def["min"]:
                raise ValueError(f"Minimum is {param_def['min']}")
            if "max" in param_def and val > param_def["max"]:
                raise ValueError(f"Maximum is {param_def['max']}")
            return val

        if ptype == "float":
            try:
                val = float(value_str)
            except (ValueError, TypeError):
                raise ValueError(f"Must be a number")
            if "min" in param_def and val < param_def["min"]:
                raise ValueError(f"Minimum is {param_def['min']}")
            if "max" in param_def and val > param_def["max"]:
                raise ValueError(f"Maximum is {param_def['max']}")
            return val

        if ptype == "times":
            # Parse comma-separated HH:MM values
            raw_times = [t.strip() for t in value_str.split(",") if t.strip()]
            if not raw_times:
                raise ValueError("Provide at least one time in HH:MM format")
            validated = []
            for t in raw_times:
                if not re.match(r'^\d{1,2}:\d{2}$', t):
                    raise ValueError(f"Invalid time format: {t} (use HH:MM)")
                parts = t.split(":")
                hour, minute = int(parts[0]), int(parts[1])
                if hour < 9 or hour > 15 or (hour == 9 and minute < 30):
                    raise ValueError(f"Time {t} outside market hours (09:30-15:30)")
                if hour == 15 and minute > 30:
                    raise ValueError(f"Time {t} outside market hours (09:30-15:30)")
                if minute < 0 or minute > 59:
                    raise ValueError(f"Invalid minutes in {t}")
                validated.append(f"{hour:02d}:{minute:02d}")
            validated.sort()
            return validated

        raise ValueError(f"Unknown parameter type: {ptype}")

    def _read_config_file(self) -> Optional[dict]:
        """Read the config JSON file."""
        if not self._config_path:
            return None
        try:
            with open(self._config_path, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Failed to read config file %s: %s", self._config_path, e)
            return None

    def _get_config_value(self, config: dict, dot_path: str):
        """Get a value from config using dot-notation path."""
        keys = dot_path.split(".")
        node = config
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return None
            node = node[key]
        return node

    def _write_config_value(self, dot_path: str, value):
        """Write a single config value to the JSON file atomically.

        Uses file locking for the entire read-modify-write cycle.
        Auto-creates intermediate dicts if missing.
        """
        lock_path = self._config_path + ".lock"
        keys = dot_path.split(".")

        lock_fd = open(lock_path, "w")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)

            # Read current config
            with open(self._config_path, "r") as f:
                config = json.load(f)

            # Navigate to parent, auto-create intermediate dicts
            node = config
            for key in keys[:-1]:
                node = node.setdefault(key, {})
            node[keys[-1]] = value

            # Atomic write: temp file → os.replace
            tmp_path = self._config_path + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(config, f, indent=2)
                f.write("\n")
            try:
                os.replace(tmp_path, self._config_path)
            except Exception:
                # Clean up orphaned tmp file on failure
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
                raise

        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()

    # =========================================================================
    # /restart — BOT RESTART
    # =========================================================================

    def _handle_restart(self, chat_id: str):
        """Handle /restart command — restart the HYDRA systemd service.

        Feedback flow:
        1. This handler sends "Restarting HYDRA..."
        2. systemctl sends SIGTERM → main.py shutdown sends BOT_STOPPED alert
        3. Service restarts → main.py startup sends BOT_STARTED alert
        """
        if not self._check_sudo(chat_id):
            return

        self._send_message(chat_id, "\u23f3 Restarting HYDRA...")
        logger.info("Telegram /restart command received")
        time.sleep(1)  # Let Telegram deliver the message

        try:
            subprocess.Popen(
                ["sudo", "systemctl", "restart", "hydra"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            logger.error("Failed to execute restart: %s", e)

    # =========================================================================
    # /stop — BOT SHUTDOWN
    # =========================================================================

    def _handle_stop(self, chat_id: str, text: str):
        """Handle /stop command — stop the HYDRA service with position check.

        If active positions exist, requires /stop confirm within 60 seconds.

        Feedback flow:
        1. This handler sends "Stopping HYDRA..."
        2. systemctl sends SIGTERM → main.py shutdown sends BOT_STOPPED alert
        """
        parts = text.split()
        is_confirm = len(parts) > 1 and parts[1].lower() == "confirm"

        # Check for active positions
        active_count = 0
        if self._active_positions_callback:
            try:
                active_count = self._active_positions_callback()
            except Exception:
                pass

        if active_count > 0 and not is_confirm:
            self._pending_stop_confirm = True
            self._pending_stop_time = time.time()
            self._send_message(
                chat_id,
                f"\u26a0\ufe0f *WARNING:* {active_count} active position(s)!\n"
                f"Stopping will leave positions unmanaged.\n\n"
                f"Send /stop confirm within 60s to proceed."
            )
            return

        # Check confirmation validity and expiry
        if is_confirm:
            if not self._pending_stop_time:
                self._send_message(chat_id, "No pending /stop. Send /stop first.")
                return
            if time.time() - self._pending_stop_time > 60:
                self._pending_stop_confirm = False
                self._pending_stop_time = None
                self._send_message(chat_id, "Confirmation expired. Send /stop again.")
                return

        self._pending_stop_confirm = False
        self._pending_stop_time = None

        if not self._check_sudo(chat_id):
            return

        self._send_message(chat_id, "\u23f3 Stopping HYDRA...")
        logger.info("Telegram /stop command received (active_positions=%d)", active_count)
        time.sleep(1)  # Let Telegram deliver the message

        try:
            subprocess.Popen(
                ["sudo", "systemctl", "stop", "hydra"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            logger.error("Failed to execute stop: %s", e)

    def _check_sudo(self, chat_id: str) -> bool:
        """Verify passwordless sudo is available. Returns True if OK."""
        try:
            result = subprocess.run(
                ["sudo", "-n", "true"],
                timeout=2,
                capture_output=True,
            )
            if result.returncode != 0:
                self._send_message(
                    chat_id,
                    "\u274c Failed: sudo not configured for calypso user.\n"
                    "Run sudoers setup on VM first."
                )
                return False
            return True
        except Exception as e:
            self._send_message(chat_id, f"\u274c Failed to verify sudo: {e}")
            return False

    # =========================================================================
    # /help
    # =========================================================================

    def _handle_help(self, chat_id: str):
        """Handle /help command — list all available commands."""
        msg = (
            "\U0001f916 *HYDRA Commands*\n\n"
            "*Monitoring*\n"
            "/status \u2014 Bot state, market data, filters\n"
            "/snapshot \u2014 Live position snapshot\n"
            "/entry N \u2014 Details for entry #N\n"
            "/lastday \u2014 Last complete trading day\n"
            "/week \u2014 Current week summary\n"
            "/account \u2014 Lifetime performance\n"
            "/stops \u2014 Stop loss analysis\n"
            "\n*Configuration*\n"
            "/config \u2014 View current config\n"
            "/set \u2014 Edit config parameter\n"
            "\n*Reports*\n"
            "/hermes \u2014 Latest HERMES report\n"
            "/apollo \u2014 Latest APOLLO briefing\n"
            "\n*Control*\n"
            "/restart \u2014 Restart HYDRA\n"
            "/stop \u2014 Stop HYDRA (warns if positions)\n"
            "\n/help \u2014 This message"
        )
        self._send_message(chat_id, msg)

    # =========================================================================
    # TELEGRAM FORMATTING HELPERS
    # =========================================================================

    def _sanitize_for_telegram(self, text: str) -> str:
        """Strip Markdown syntax that breaks Telegram's legacy parser.

        Converts: # headers → *bold*, ** → *, [links](url) → text, --- → ━ line.
        Used for HERMES/APOLLO reports which contain full Markdown.
        """
        lines = []
        for line in text.split("\n"):
            if line.startswith("#"):
                cleaned = line.lstrip("#").strip()
                lines.append(f"*{cleaned}*")
            else:
                line = line.replace("**", "*")
                line = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', line)
                if line.strip() in ("---", "***", "___"):
                    line = "\u2501" * 20
                lines.append(line)
        return "\n".join(lines)

    # =========================================================================
    # TELEGRAM API HELPERS
    # =========================================================================

    def _split_message(self, text: str, max_length: int = MAX_MESSAGE_LENGTH) -> List[str]:
        """Split a long message into chunks that fit Telegram's 4096 char limit.

        Splitting priority:
        1. Double newline (paragraph/section boundary)
        2. Single newline
        3. Hard character split (last resort)

        Each part gets a "(1/N)" header when there are multiple parts.
        """
        if len(text) <= max_length:
            return [text]

        parts = []
        remaining = text

        while remaining:
            if len(remaining) <= max_length:
                parts.append(remaining)
                break

            # Reserve space for part header like "(1/10)\n"
            effective_max = max_length - 10
            chunk = remaining[:effective_max]

            # Try to split at double newline (paragraph boundary)
            split_pos = chunk.rfind("\n\n")
            if split_pos > effective_max // 3:
                parts.append(remaining[:split_pos])
                remaining = remaining[split_pos + 2:]
                continue

            # Try to split at single newline
            split_pos = chunk.rfind("\n")
            if split_pos > effective_max // 3:
                parts.append(remaining[:split_pos])
                remaining = remaining[split_pos + 1:]
                continue

            # Hard split at character boundary (last resort)
            parts.append(remaining[:effective_max])
            remaining = remaining[effective_max:]

        # Add part headers when multiple parts
        if len(parts) > 1:
            total = len(parts)
            parts = [f"({i + 1}/{total})\n{part}" for i, part in enumerate(parts)]

        return parts

    def _send_message(self, chat_id: str, text: str):
        """Send a message via Telegram Bot API, splitting if it exceeds 4096 chars."""
        parts = self._split_message(text)
        for part in parts:
            self._send_single_message(chat_id, part)
            if len(parts) > 1:
                time.sleep(0.3)  # Rate-limit between parts

    def _send_single_message(self, chat_id: str, text: str):
        """Send a single message via Telegram Bot API with Markdown fallback."""
        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}

        try:
            resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return

            # Markdown parsing failed — retry as plain text
            logger.warning("Telegram Markdown send failed (status %d), retrying plain text", resp.status_code)
            payload.pop("parse_mode")
            requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        except Exception as e:
            logger.error("Failed to send Telegram message: %s", e)

    def _flush_old_updates(self):
        """Skip any commands queued while the bot was down."""
        try:
            url = f"https://api.telegram.org/bot{self._bot_token}/getUpdates"
            resp = requests.get(url, params={"offset": -1, "limit": 1}, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                updates = data.get("result", [])
                if updates:
                    self._offset = updates[-1]["update_id"] + 1
                    logger.info("Telegram commands: flushed old updates (offset=%d)", self._offset)
        except Exception as e:
            logger.warning("Failed to flush old Telegram updates: %s", e)

    def _delete_webhook(self):
        """Remove any existing webhook so getUpdates works."""
        try:
            url = f"https://api.telegram.org/bot{self._bot_token}/deleteWebhook"
            requests.post(url, timeout=REQUEST_TIMEOUT)
        except Exception:
            pass  # Non-critical — if no webhook exists, this is a no-op
