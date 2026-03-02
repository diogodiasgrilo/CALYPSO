#!/usr/bin/env python3
"""
Cloud Function: Alert Processor

Processes trading alerts from Pub/Sub and sends notifications via:
- Telegram (Bot API) - Primary for ALL alerts (rich Markdown formatting, free, no expiry)
- Email (Gmail SMTP) - All alert levels (full HTML formatting, permanent record)

Timezone:
    All timestamps are displayed in US Eastern Time (ET) - the exchange timezone.
    Handles EST ↔ EDT transitions automatically via pytz.

Trigger: Pub/Sub topic "calypso-alerts"

Secrets (via Secret Manager):
    calypso-telegram-credentials: {
        "bot_token": "123456789:ABCdef...",
        "chat_id": "8016648738"
    }
    calypso-alert-config: {
        "phone_number": "...",      # Kept for reference
        "email": "...",
        "gmail_address": "...",
        "gmail_app_password": "...",
        "telegram_chat_id": "..."   # Override chat_id if needed
    }
"""

import base64
import json
import logging
import os
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, Optional

import functions_framework
import pytz
import requests

# US Eastern timezone (handles EST/EDT automatically)
US_EASTERN = pytz.timezone('America/New_York')

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_secret(secret_name: str) -> Optional[str]:
    """Fetch secret from Secret Manager."""
    try:
        from google.cloud import secretmanager

        project_id = os.environ.get("GCP_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")
        if not project_id:
            # Try metadata server
            response = requests.get(
                "http://metadata.google.internal/computeMetadata/v1/project/project-id",
                headers={"Metadata-Flavor": "Google"},
                timeout=2
            )
            if response.status_code == 200:
                project_id = response.text

        if not project_id:
            logger.error("Could not determine project ID")
            return None

        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("UTF-8")

    except Exception as e:
        logger.warning(f"Could not fetch secret {secret_name}: {e}")
        return None


def get_telegram_credentials() -> Dict[str, str]:
    """Get Telegram bot credentials from Secret Manager or environment."""
    # Try Secret Manager first
    secret_value = get_secret("calypso-telegram-credentials")
    if secret_value:
        try:
            return json.loads(secret_value)
        except json.JSONDecodeError:
            pass

    # Fall back to environment variables
    return {
        "bot_token": os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        "chat_id": os.environ.get("TELEGRAM_CHAT_ID", "")
    }


def get_alert_config() -> Dict[str, str]:
    """Get alert configuration from Secret Manager or environment."""
    # Try Secret Manager first
    secret_value = get_secret("calypso-alert-config")
    if secret_value:
        try:
            return json.loads(secret_value)
        except json.JSONDecodeError:
            pass

    # Fall back to environment variables
    return {
        "phone_number": os.environ.get("DEFAULT_PHONE_NUMBER", ""),
        "email": os.environ.get("DEFAULT_EMAIL", ""),
        "gmail_address": os.environ.get("GMAIL_ADDRESS", ""),
        "gmail_app_password": os.environ.get("GMAIL_APP_PASSWORD", ""),
        "telegram_chat_id": os.environ.get("TELEGRAM_CHAT_ID", "")
    }


def send_telegram(message: str, chat_id: str = None) -> bool:
    """
    Send message via Telegram Bot API.

    Args:
        message: Message text (supports Markdown formatting)
        chat_id: Override chat_id (defaults to credentials chat_id)

    Returns:
        bool: True if sent successfully
    """
    creds = get_telegram_credentials()
    bot_token = creds.get("bot_token", "")

    if not bot_token:
        logger.error("Telegram bot token not configured")
        return False

    target_chat_id = chat_id or creds.get("chat_id", "")
    if not target_chat_id:
        logger.error("Telegram chat_id not configured")
        return False

    # Telegram message limit is 4096 characters
    if len(message) > 4096:
        message = message[:4093] + "..."

    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": target_chat_id,
            "text": message,
            "parse_mode": "Markdown"
        }

        response = requests.post(url, json=payload, timeout=10)

        if response.status_code == 200:
            logger.info("Telegram message sent successfully")
            return True
        else:
            # If Markdown parsing fails, retry without parse_mode
            logger.warning(f"Telegram send failed with Markdown (status {response.status_code}), retrying as plain text")
            payload.pop("parse_mode")
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                logger.info("Telegram message sent successfully (plain text fallback)")
                return True
            else:
                logger.error(f"Telegram send failed: {response.status_code} - {response.text}")
                return False

    except Exception as e:
        logger.error(f"Failed to send Telegram message: {e}")
        return False


def send_email(to_email: str, subject: str, body_html: str, body_text: str) -> bool:
    """
    Send email via Gmail SMTP.

    Args:
        to_email: Recipient email address
        subject: Email subject
        body_html: HTML body
        body_text: Plain text body (fallback)

    Returns:
        bool: True if sent successfully
    """
    config = get_alert_config()

    gmail_address = config.get("gmail_address", "")
    gmail_password = config.get("gmail_app_password", "")

    if not all([gmail_address, gmail_password]):
        logger.error("Gmail credentials not configured")
        return False

    if not to_email:
        logger.error("No recipient email provided")
        return False

    try:
        # Create message
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"CALYPSO Trading Bot <{gmail_address}>"
        msg["To"] = to_email

        # Attach both plain text and HTML versions
        part1 = MIMEText(body_text, "plain")
        part2 = MIMEText(body_html, "html")
        msg.attach(part1)
        msg.attach(part2)

        # Send via Gmail SMTP
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_address, gmail_password)
            server.sendmail(gmail_address, to_email, msg.as_string())

        logger.info(f"Email sent successfully to {to_email}")
        return True

    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False


def format_telegram_message(alert: Dict[str, Any]) -> str:
    """
    Format alert for Telegram (rich Markdown formatting).

    Telegram supports:
    - *bold* for emphasis
    - _italic_ for secondary info
    - `monospace` for code/data
    - ```pre``` for code blocks

    Design principles:
    - No redundant information (don't repeat what's in the message)
    - Concise but complete
    - Action-oriented for urgent alerts
    """
    bot_name = alert.get("bot_name", "CALYPSO")
    priority = alert.get("priority", "medium").upper()
    title = alert.get("title", "Alert")
    message = alert.get("message", "")
    timestamp = alert.get("timestamp", "")
    details = alert.get("details") or {}

    # Priority emoji
    priority_emoji = {
        "CRITICAL": "🚨",
        "HIGH": "⚠️",
        "MEDIUM": "📊",
        "LOW": "ℹ️"
    }
    emoji = priority_emoji.get(priority, "📊")

    # Format timestamp to readable ET format
    time_str = ""
    try:
        if timestamp:
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            dt_et = dt.astimezone(US_EASTERN)
            time_str = dt_et.strftime("%I:%M %p ET")
    except Exception:
        pass

    # Build Telegram message
    lines = []

    # Compact header: emoji + bot name + title
    lines.append(f"{emoji} *{bot_name}* | {title}")
    lines.append("")

    # Main message (already contains the key info)
    lines.append(message)

    # Add supplementary details (only info NOT already in message)
    message_lower = message.lower()
    filtered_details = {}

    for key, value in details.items():
        # Skip internal keys
        if key.startswith("_"):
            continue

        # Skip boolean False values
        if isinstance(value, bool) and not value:
            continue

        # Skip if this info is likely already in the message
        key_lower = key.lower()
        skip_keys = ["reason", "pnl", "trigger_price", "cost_or_credit"]
        if key_lower in skip_keys:
            if isinstance(value, (int, float)):
                if f"{value:.2f}" in message or str(int(value)) in message:
                    continue
            elif str(value).lower() in message_lower:
                continue

        filtered_details[key] = value

    # Only show details section if we have non-redundant info
    if filtered_details:
        lines.append("")

        for key, value in filtered_details.items():
            key_lower = key.lower()

            # Format key nicely
            if key_lower == "is_early_close":
                display_key = "Early close today"
            else:
                display_key = key.replace("_", " ").title()

            # Format value based on type
            if isinstance(value, float):
                if any(word in key_lower for word in ["pnl", "cost", "credit", "price"]):
                    value_str = f"${value:+.2f}" if "pnl" in key_lower else f"${value:.2f}"
                elif any(word in key_lower for word in ["percent", "rate", "gap"]):
                    value_str = f"{value:+.1f}%" if "gap" in key_lower else f"{value:.1f}%"
                else:
                    value_str = f"{value:.2f}"
            elif isinstance(value, bool):
                value_str = "Yes"
            elif isinstance(value, (list, dict)):
                value_str = str(value)[:80]
            else:
                value_str = str(value)

            lines.append(f"• {display_key}: {value_str}")

    # Minimal footer with timestamp only
    if time_str:
        lines.append("")
        lines.append(f"_{time_str}_")

    return "\n".join(lines)


def format_email_subject(alert: Dict[str, Any]) -> str:
    """Format email subject line."""
    bot_name = alert.get("bot_name", "CALYPSO")
    priority = alert.get("priority", "medium").upper()
    title = alert.get("title", "Alert")

    prefix_map = {
        "CRITICAL": "[CRITICAL]",
        "HIGH": "[HIGH]",
        "MEDIUM": "",
        "LOW": "[INFO]"
    }
    prefix = prefix_map.get(priority, "")

    if prefix:
        return f"{prefix} {bot_name}: {title}"
    return f"{bot_name}: {title}"


def format_email_body(alert: Dict[str, Any]) -> tuple:
    """
    Format alert for email (detailed HTML + plain text).

    Returns:
        tuple: (html_body, text_body)
    """
    bot_name = alert.get("bot_name", "CALYPSO")
    priority = alert.get("priority", "medium").upper()
    title = alert.get("title", "Alert")
    message = alert.get("message", "")
    timestamp = alert.get("timestamp", "")
    details = alert.get("details") or {}

    # Format timestamp to human-readable ET
    time_display = ""
    try:
        if timestamp:
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            dt_et = dt.astimezone(US_EASTERN)
            time_display = dt_et.strftime("%B %d, %Y at %I:%M %p ET")
        else:
            dt_et = datetime.now(US_EASTERN)
            time_display = dt_et.strftime("%B %d, %Y at %I:%M %p ET")
    except Exception:
        time_display = timestamp[:19] if timestamp else ""

    # Priority colors
    color_map = {
        "CRITICAL": "#dc3545",  # Red
        "HIGH": "#fd7e14",      # Orange
        "MEDIUM": "#0d6efd",    # Blue
        "LOW": "#6c757d"        # Gray
    }
    color = color_map.get(priority, "#0d6efd")

    # Filter details to remove redundant info already in message
    message_lower = message.lower()
    filtered_details = {}

    for key, value in details.items():
        if key.startswith("_"):
            continue

        if isinstance(value, bool) and not value:
            continue

        key_lower = key.lower()
        value_str = ""

        if isinstance(value, float):
            value_str = f"{value:.2f}"
            if value_str in message or f"${value_str}" in message:
                continue
        elif isinstance(value, bool):
            value_str = "Yes"
        elif isinstance(value, (list, dict)):
            value_str = str(value)[:100]
        else:
            value_str = str(value)
            if value_str.lower() in message_lower:
                continue

        if key_lower == "is_early_close":
            display_key = "Early close today"
        else:
            display_key = key.replace("_", " ").title()

        if isinstance(value, float):
            if any(word in key_lower for word in ["pnl", "cost", "credit", "price"]):
                value_str = f"${value:,.2f}"
            elif any(word in key_lower for word in ["percent", "rate", "gap"]):
                value_str = f"{value:.1f}%"

        filtered_details[display_key] = value_str

    # Build details HTML table
    details_html = ""
    details_text = ""
    if filtered_details:
        for key, value in filtered_details.items():
            details_html += f"<tr><td style='padding: 8px 12px; border-bottom: 1px solid #eee; color: #666;'>{key}</td><td style='padding: 8px 12px; border-bottom: 1px solid #eee; font-weight: 500;'>{value}</td></tr>"
            details_text += f"  {key}: {value}\n"

    # HTML body
    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 20px; background: #f5f5f5;">
    <div style="max-width: 600px; margin: 0 auto; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
        <div style="background: {color}; color: white; padding: 24px;">
            <div style="font-size: 13px; opacity: 0.9; margin-bottom: 8px;">{bot_name} • {priority}</div>
            <h1 style="margin: 0; font-size: 22px; font-weight: 600;">{title}</h1>
        </div>
        <div style="padding: 24px;">
            <div style="font-size: 16px; line-height: 1.6; color: #333; white-space: pre-line;">{message}</div>
            {f'<div style="margin-top: 24px;"><table style="width: 100%; border-collapse: collapse; background: #fafafa; border-radius: 6px;">{details_html}</table></div>' if details_html else ''}
        </div>
        <div style="padding: 16px 24px; background: #f8f9fa; font-size: 13px; color: #6c757d; border-top: 1px solid #eee;">
            {time_display}
        </div>
    </div>
</body>
</html>"""

    # Plain text body
    text = f"""{bot_name} | {priority}
{'=' * 50}
{title}
{'=' * 50}

{message}
{f'''
Details:
{details_text}''' if details_text else ''}
---
{time_display}
"""

    return html, text


@functions_framework.cloud_event
def process_alert(cloud_event):
    """
    Cloud Function entry point - processes Pub/Sub messages.

    Sends alerts via Telegram + Email. Supports both legacy "sms" key
    and new "telegram" key in delivery payload for backwards compatibility.
    """
    try:
        # Decode the Pub/Sub message
        pubsub_message = cloud_event.data.get("message", {})
        message_data = pubsub_message.get("data", "")

        if message_data:
            decoded = base64.b64decode(message_data).decode("utf-8")
            alert = json.loads(decoded)
        else:
            logger.error("Empty message received")
            return

        logger.info(f"Processing alert: {alert.get('alert_type')} from {alert.get('bot_name')}")

        # Get delivery preferences from message or use defaults
        delivery = alert.get("delivery", {})
        config = get_alert_config()

        email_address = delivery.get("email_address") or config.get("email", "")

        # Support both "telegram" (new) and "sms" (legacy) keys for backwards compatibility
        send_telegram_flag = delivery.get("telegram", False) or delivery.get("sms", False)
        send_email_flag = delivery.get("email", True)

        # Override chat_id from config if available
        telegram_chat_id = config.get("telegram_chat_id", "")

        results = {"telegram": None, "email": None}

        # Send Telegram if requested
        if send_telegram_flag:
            telegram_message = format_telegram_message(alert)
            results["telegram"] = send_telegram(telegram_message, chat_id=telegram_chat_id or None)
            logger.info(f"Telegram delivery: {'success' if results['telegram'] else 'failed'}")

        # Send email if requested and configured
        if send_email_flag and email_address:
            subject = format_email_subject(alert)
            html_body, text_body = format_email_body(alert)
            results["email"] = send_email(email_address, subject, html_body, text_body)
            logger.info(f"Email delivery: {'success' if results['email'] else 'failed'}")

        # Log final result
        logger.info(f"Alert processed: {json.dumps(results)}")

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse alert JSON: {e}")
    except Exception as e:
        logger.error(f"Error processing alert: {e}")
        raise  # Re-raise to trigger Pub/Sub retry


# For local testing
if __name__ == "__main__":
    print("=" * 60)
    print("ALERT PROCESSOR LOCAL TEST")
    print("=" * 60)

    # Sample alert message (using ET timestamp)
    test_alert = {
        "bot_name": "HYDRA",
        "alert_type": "circuit_breaker",
        "priority": "critical",
        "title": "Circuit Breaker Triggered",
        "message": "5 consecutive API failures detected.\nTrading halted for safety.",
        "timestamp": datetime.now(US_EASTERN).isoformat(),
        "details": {
            "consecutive_failures": 5,
            "last_error": "Connection timeout",
            "spx_price": 6025.50
        },
        "delivery": {
            "telegram": True,
            "email": True
        }
    }

    print("\nFormatted Telegram Message:")
    print("-" * 40)
    print(format_telegram_message(test_alert))

    print("\n" + "-" * 40)
    print("\nFormatted Email Subject:")
    print("-" * 40)
    print(format_email_subject(test_alert))

    print("\nFormatted Email Body (text):")
    print("-" * 40)
    html, text = format_email_body(test_alert)
    print(text)

    print("\n" + "=" * 60)
    print("To test actual sending, set environment variables:")
    print("  TELEGRAM_BOT_TOKEN: Bot token from @BotFather")
    print("  TELEGRAM_CHAT_ID: Your Telegram chat ID")
    print("  GMAIL_ADDRESS, GMAIL_APP_PASSWORD")
    print("  DEFAULT_EMAIL")
    print("=" * 60)
