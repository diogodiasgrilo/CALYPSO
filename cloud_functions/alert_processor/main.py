#!/usr/bin/env python3
"""
Cloud Function: Alert Processor

Processes trading alerts from Pub/Sub and sends SMS (Twilio) and Email (Gmail SMTP).

Trigger: Pub/Sub topic "calypso-alerts"

Environment Variables (set in Cloud Function deployment):
    TWILIO_ACCOUNT_SID: Twilio account SID
    TWILIO_AUTH_TOKEN: Twilio auth token
    TWILIO_PHONE_NUMBER: Twilio phone number to send from
    GMAIL_ADDRESS: Gmail address to send from
    GMAIL_APP_PASSWORD: Gmail app password (NOT your regular password)
    DEFAULT_PHONE_NUMBER: Default recipient phone number (if not in message)
    DEFAULT_EMAIL: Default recipient email (if not in message)

Secrets (via Secret Manager - preferred):
    calypso-twilio-credentials: {"account_sid": "...", "auth_token": "...", "phone_number": "..."}
    calypso-alert-config: {"phone_number": "...", "email": "...", "gmail_address": "...", "gmail_app_password": "..."}
"""

import base64
import json
import logging
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, Optional

import functions_framework

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
            import requests
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


def get_twilio_credentials() -> Dict[str, str]:
    """Get Twilio credentials from Secret Manager or environment."""
    # Try Secret Manager first
    secret_value = get_secret("calypso-twilio-credentials")
    if secret_value:
        try:
            return json.loads(secret_value)
        except json.JSONDecodeError:
            pass

    # Fall back to environment variables
    return {
        "account_sid": os.environ.get("TWILIO_ACCOUNT_SID", ""),
        "auth_token": os.environ.get("TWILIO_AUTH_TOKEN", ""),
        "phone_number": os.environ.get("TWILIO_PHONE_NUMBER", "")
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
        "gmail_app_password": os.environ.get("GMAIL_APP_PASSWORD", "")
    }


def send_sms(to_number: str, message: str) -> bool:
    """
    Send SMS via Twilio.

    Args:
        to_number: Recipient phone number (E.164 format)
        message: SMS message body

    Returns:
        bool: True if sent successfully
    """
    creds = get_twilio_credentials()

    if not all([creds.get("account_sid"), creds.get("auth_token"), creds.get("phone_number")]):
        logger.error("Twilio credentials not configured")
        return False

    if not to_number:
        logger.error("No recipient phone number provided")
        return False

    try:
        from twilio.rest import Client

        client = Client(creds["account_sid"], creds["auth_token"])

        # Truncate message if too long (SMS limit is 1600 chars for concatenated)
        if len(message) > 1500:
            message = message[:1497] + "..."

        sms = client.messages.create(
            body=message,
            from_=creds["phone_number"],
            to=to_number
        )

        logger.info(f"SMS sent successfully. SID: {sms.sid}")
        return True

    except ImportError:
        logger.error("Twilio library not installed. Run: pip install twilio")
        return False
    except Exception as e:
        logger.error(f"Failed to send SMS: {e}")
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


def format_sms_message(alert: Dict[str, Any]) -> str:
    """Format alert for SMS (concise)."""
    bot_name = alert.get("bot_name", "CALYPSO")
    priority = alert.get("priority", "medium").upper()
    title = alert.get("title", "Alert")
    message = alert.get("message", "")

    # Priority emoji
    emoji_map = {
        "CRITICAL": "🚨",
        "HIGH": "⚠️",
        "MEDIUM": "📧",
        "LOW": "ℹ️"
    }
    emoji = emoji_map.get(priority, "📧")

    # Build SMS (keep it short)
    sms = f"{emoji} {bot_name}\n{title}\n{message}"

    # Add key details if available
    details = alert.get("details", {})
    if "pnl" in details:
        pnl = details["pnl"]
        sms += f"\nP&L: ${pnl:.2f}"

    return sms


def format_email_subject(alert: Dict[str, Any]) -> str:
    """Format email subject line."""
    bot_name = alert.get("bot_name", "CALYPSO")
    priority = alert.get("priority", "medium").upper()
    title = alert.get("title", "Alert")

    # Priority prefix
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
    alert_type = alert.get("alert_type", "unknown")
    priority = alert.get("priority", "medium").upper()
    title = alert.get("title", "Alert")
    message = alert.get("message", "")
    timestamp = alert.get("timestamp", datetime.utcnow().isoformat())
    details = alert.get("details", {})

    # Priority colors
    color_map = {
        "CRITICAL": "#dc3545",  # Red
        "HIGH": "#fd7e14",      # Orange
        "MEDIUM": "#0d6efd",    # Blue
        "LOW": "#6c757d"        # Gray
    }
    color = color_map.get(priority, "#0d6efd")

    # Format details as table rows
    details_html = ""
    details_text = ""
    if details:
        for key, value in details.items():
            if isinstance(value, float):
                value_str = f"{value:.2f}"
            else:
                value_str = str(value)
            details_html += f"<tr><td style='padding: 5px; border-bottom: 1px solid #ddd;'><strong>{key}</strong></td><td style='padding: 5px; border-bottom: 1px solid #ddd;'>{value_str}</td></tr>"
            details_text += f"  {key}: {value_str}\n"

    # HTML body
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 20px; background: #f5f5f5; }}
            .container {{ max-width: 600px; margin: 0 auto; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            .header {{ background: {color}; color: white; padding: 20px; }}
            .header h1 {{ margin: 0; font-size: 18px; }}
            .header .badge {{ display: inline-block; background: rgba(255,255,255,0.2); padding: 4px 8px; border-radius: 4px; font-size: 12px; margin-top: 8px; }}
            .content {{ padding: 20px; }}
            .message {{ font-size: 16px; line-height: 1.5; color: #333; white-space: pre-line; }}
            .details {{ margin-top: 20px; }}
            .details table {{ width: 100%; border-collapse: collapse; }}
            .footer {{ padding: 15px 20px; background: #f8f9fa; font-size: 12px; color: #6c757d; border-top: 1px solid #eee; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>{title}</h1>
                <span class="badge">{bot_name} | {priority}</span>
            </div>
            <div class="content">
                <div class="message">{message}</div>
                {f'<div class="details"><h3 style="margin-bottom: 10px; color: #333;">Details</h3><table>{details_html}</table></div>' if details_html else ''}
            </div>
            <div class="footer">
                <strong>Alert Type:</strong> {alert_type}<br>
                <strong>Timestamp:</strong> {timestamp}<br>
                <em>This is an automated alert from CALYPSO Trading Bot</em>
            </div>
        </div>
    </body>
    </html>
    """

    # Plain text body
    text = f"""
{bot_name} ALERT: {title}
{'=' * 50}
Priority: {priority}
Alert Type: {alert_type}
Timestamp: {timestamp}

{message}

{'Details:' if details_text else ''}
{details_text}
---
This is an automated alert from CALYPSO Trading Bot
"""

    return html, text


@functions_framework.cloud_event
def process_alert(cloud_event):
    """
    Cloud Function entry point - processes Pub/Sub messages.

    Args:
        cloud_event: CloudEvent containing Pub/Sub message
    """
    try:
        # Decode the Pub/Sub message
        pubsub_message = cloud_event.data.get("message", {})
        message_data = pubsub_message.get("data", "")

        if message_data:
            # Base64 decode the message
            decoded = base64.b64decode(message_data).decode("utf-8")
            alert = json.loads(decoded)
        else:
            logger.error("Empty message received")
            return

        logger.info(f"Processing alert: {alert.get('alert_type')} from {alert.get('bot_name')}")

        # Get delivery preferences from message or use defaults
        delivery = alert.get("delivery", {})
        config = get_alert_config()

        phone_number = delivery.get("phone_number") or config.get("phone_number", "")
        email_address = delivery.get("email_address") or config.get("email", "")

        send_sms_flag = delivery.get("sms", False)
        send_email_flag = delivery.get("email", True)

        results = {"sms": None, "email": None}

        # Send SMS if requested and configured
        if send_sms_flag and phone_number:
            sms_message = format_sms_message(alert)
            results["sms"] = send_sms(phone_number, sms_message)
            logger.info(f"SMS delivery: {'success' if results['sms'] else 'failed'}")

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
    import sys

    print("=" * 60)
    print("ALERT PROCESSOR LOCAL TEST")
    print("=" * 60)

    # Sample alert message
    test_alert = {
        "bot_name": "IRON_FLY",
        "alert_type": "circuit_breaker",
        "priority": "critical",
        "title": "Circuit Breaker Triggered",
        "message": "5 consecutive API failures detected.\nTrading halted for safety.",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "details": {
            "consecutive_failures": 5,
            "last_error": "Connection timeout",
            "spy_price": 6025.50
        },
        "delivery": {
            "sms": True,
            "email": True
        }
    }

    print("\nFormatted SMS:")
    print("-" * 40)
    print(format_sms_message(test_alert))

    print("\nFormatted Email Subject:")
    print("-" * 40)
    print(format_email_subject(test_alert))

    print("\nFormatted Email Body (text):")
    print("-" * 40)
    html, text = format_email_body(test_alert)
    print(text)

    print("\n" + "=" * 60)
    print("To test actual sending, set environment variables:")
    print("  TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER")
    print("  GMAIL_ADDRESS, GMAIL_APP_PASSWORD")
    print("  DEFAULT_PHONE_NUMBER, DEFAULT_EMAIL")
    print("=" * 60)
