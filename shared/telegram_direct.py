#!/usr/bin/env python3
"""
Direct Telegram send — a Pub/Sub-INDEPENDENT last-resort delivery channel.

Normal alert flow: Bot -> AlertService -> Pub/Sub -> Cloud Function -> Telegram/Email.
That entire chain depends on Pub/Sub. If Pub/Sub itself has an outage, a
CRITICAL/HIGH alert (naked position, emergency-close failure, circuit
breaker) goes completely dark — shared/alert_service.py's own retry +
dead-letter fallback (data/failed_alerts.jsonl) is a passive local record,
not a notification anyone is actively watching.

This module is the bypass: send_telegram_direct() talks to the Telegram Bot
API directly (no Pub/Sub, no Cloud Function), called by AlertService.send_alert
ONLY after its own Pub/Sub publish + retry have both already failed, and only
for CRITICAL/HIGH priority. Deliberately simple — one bounded attempt, no
internal retry loop (the caller already exhausted its own retry budget
before reaching here); if this also fails, the dead-letter file remains the
final record.

2026-08-04 audit found the codebase already talks to this same API in THREE
independent places (bots/hydra/telegram_commands.py's command-poller reply,
services/homer/main.py's journal-push notices, cloud_functions/
alert_processor/main.py's Pub/Sub-delivered send) — none reusable as a
bypass (variant-gated, or IS the Pub/Sub path itself) and none sharing code
with each other. This is deliberately a NEW, small, standalone module rather
than a 4th duplicate — existing call sites are NOT refactored to use it
(out of scope; see the plan/version-history for the explicit reasoning).
"""

import logging
from typing import Any, Dict, Optional

from shared.alert_service import describe_exception
from shared.secret_manager import get_telegram_credentials

logger = logging.getLogger(__name__)

TELEGRAM_API_TIMEOUT_S = 5.0


def send_telegram_direct(
    message: str, title: str = "", credentials: Optional[Dict[str, Any]] = None
) -> bool:
    """Send one Telegram message directly, bypassing Pub/Sub entirely.

    Never raises. Returns True only on a confirmed 200 from Telegram's API.
    Mirrors services/homer/main.py's send_telegram_alert's Markdown-then-
    plain-text-on-non-200 fallback (the closest existing precedent), but as
    a standalone function with no config-dict/HOMER coupling.

    `credentials`: pass a pre-fetched {"bot_token", "chat_id"} dict to skip
    the Secret Manager round-trip (AlertService caches this across repeated
    bypass triggers on the same process). Omit to fetch fresh — this keeps
    the function fully self-contained for any other/future caller.
    """
    # Kept in scope across the whole function (not just the try block) so
    # the except handler below can redact it — every Telegram API call
    # embeds the token in the URL path, so a `requests` connection/timeout
    # exception can stringify the token straight into the message we'd
    # otherwise log. Same risk bots/hydra/telegram_commands.py's
    # _TokenRedactingFilter was built to close (see its docstring) — that
    # filter is instance-based (attached once a TelegramCommandHandler's
    # token is known) and doesn't cover this module's stateless function, so
    # redact inline here instead rather than adding filter-lifecycle
    # machinery for a single call site.
    bot_token = ""
    try:
        import requests

        creds = credentials if credentials is not None else get_telegram_credentials()
        if not creds:
            logger.warning("Telegram bypass: no credentials available")
            return False

        bot_token = creds.get("bot_token", "")
        chat_id = str(creds.get("chat_id", ""))
        if not bot_token or not chat_id:
            logger.warning("Telegram bypass: missing bot_token or chat_id")
            return False

        text = f"*{title}*\n{message}" if title else message
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=TELEGRAM_API_TIMEOUT_S,
        )
        if resp.status_code == 200:
            return True

        # Retry once, plain text (a Markdown-escaping issue in the message
        # body is a common cause of a 400 here — don't let that lose the
        # alert entirely).
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": text},
            timeout=TELEGRAM_API_TIMEOUT_S,
        )
        return resp.status_code == 200

    except Exception as e:
        error_desc = describe_exception(e)
        if bot_token:
            error_desc = error_desc.replace(bot_token, "***REDACTED***")
        logger.warning(f"Telegram bypass send failed: {error_desc}")
        return False
