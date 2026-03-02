"""
Telegram Command Handler for HYDRA Bot.

Polls Telegram's getUpdates API for incoming commands and responds directly.
Runs as a background daemon thread — independent of the main trading loop.

Currently supports:
    /snapshot — Returns live position snapshot (market hours) or "market closed" message

Security: Only responds to messages from the configured chat_id.

Version: 1.0.0 (2026-03-02)
"""

import json
import logging
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

    def start(self, snapshot_callback: Callable[[], str]):
        """Start the background polling thread."""
        self._snapshot_callback = snapshot_callback

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
                f"📊 *HYDRA* | Market Closed\n"
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
            msg = f"📊 *HYDRA* | Snapshot\n\n{snapshot}\n\n_{time_str}_"
            self._send_message(chat_id, msg)
        except Exception as e:
            logger.error("Failed to build snapshot for /snapshot command: %s", e)
            self._send_message(chat_id, "Snapshot temporarily unavailable. Try again in a minute.")

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
