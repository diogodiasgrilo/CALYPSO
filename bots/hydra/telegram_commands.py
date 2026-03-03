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
    /config   — Current configuration
    /hermes   — Latest HERMES daily report
    /apollo   — Latest APOLLO morning briefing
    /help     — List all commands

Security: Only responds to messages from the configured chat_id.

Version: 1.2.0 (2026-03-03)
"""

import json
import logging
import re
import threading
import time
from typing import Callable, Optional

import requests

from shared.secret_manager import get_secret
from shared.market_hours import is_market_open, get_us_market_time, is_weekend, get_holiday_name

logger = logging.getLogger(__name__)

POLL_INTERVAL = 5       # seconds between getUpdates calls
REQUEST_TIMEOUT = 10    # HTTP timeout for Telegram API calls
MAX_MESSAGE_LENGTH = 4096  # Telegram message limit
# Only log persistent Telegram failures every 60th attempt (every 5 min)
ERROR_LOG_INTERVAL = 60


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
        self._consecutive_errors = 0

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
            elif text.startswith("/config"):
                self._handle_config(chat_id)
            elif text.startswith("/hermes"):
                self._handle_hermes(chat_id)
            elif text.startswith("/apollo"):
                self._handle_apollo(chat_id)
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

    def _handle_help(self, chat_id: str):
        """Handle /help command — list all available commands."""
        msg = (
            "\U0001f916 *HYDRA Commands*\n\n"
            "/status \u2014 Bot state, market data, filters\n"
            "/snapshot \u2014 Live position snapshot\n"
            "/entry N \u2014 Details for entry #N\n"
            "/lastday \u2014 Last complete trading day\n"
            "/week \u2014 Current week summary\n"
            "/account \u2014 Lifetime performance\n"
            "/stops \u2014 Stop loss analysis\n"
            "/config \u2014 Current configuration\n"
            "/hermes \u2014 Latest HERMES report\n"
            "/apollo \u2014 Latest APOLLO briefing\n"
            "/help \u2014 This message"
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

    def _send_message(self, chat_id: str, text: str):
        """Send a message via Telegram Bot API with Markdown fallback."""
        if len(text) > MAX_MESSAGE_LENGTH:
            text = text[:MAX_MESSAGE_LENGTH - 3] + "..."

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
