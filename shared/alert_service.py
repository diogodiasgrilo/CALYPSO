#!/usr/bin/env python3
"""
Alert Service Module

Provides SMS and email alerting for CALYPSO trading bots via Google Cloud Pub/Sub.

Architecture:
    Bot → AlertService → Pub/Sub Topic → Cloud Function → Twilio/Gmail → User

IMPORTANT: Alerts are sent AFTER actions complete, with ACTUAL results.
    1. Event occurs (e.g., wing breach detected)
    2. Bot executes the action (e.g., emergency close)
    3. Bot gets real result (e.g., actual P&L)
    4. Bot publishes alert with real data to Pub/Sub (~50ms)
    5. Bot continues to next iteration immediately
    6. Cloud Function delivers SMS/email asynchronously

Benefits:
    - Accurate: Alerts contain real outcomes, not predictions
    - Non-blocking: Bot doesn't wait for Twilio/Gmail API (saves 1-2s per alert)
    - Reliable: Pub/Sub retries for 7 days, dead-letter queue captures failures
    - Auditable: Full trail in Cloud Logging
    - Scalable: Add new alert channels without changing bot code

Timezone:
    All timestamps use US Eastern Time (ET) - the exchange timezone.
    This ensures consistent timestamps regardless of where you travel.
    DST transitions (EST ↔ EDT) are handled automatically via pytz.

Alert Priorities:
    CRITICAL: WhatsApp + Email (circuit breaker, emergency exit, naked positions)
    HIGH: WhatsApp + Email (stop loss, max loss, position issues)
    MEDIUM: WhatsApp + Email (position opened, profit target, daily summaries)
    LOW: WhatsApp + Email (informational, startup/shutdown)

Note: All priority levels now send to WhatsApp for immediate visibility.
      Email provides a permanent record with rich HTML formatting.

Usage:
    from shared.alert_service import AlertService, AlertPriority

    alert_service = AlertService(config)

    # Send a critical alert (SMS + Email)
    alert_service.send_alert(
        bot_name="IRON_FLY",
        alert_type="CIRCUIT_BREAKER",
        priority=AlertPriority.CRITICAL,
        title="Circuit Breaker Triggered",
        message="5 consecutive failures detected. Trading halted.",
        details={"consecutive_failures": 5, "reason": "API timeout"}
    )
"""

import json
import logging
import os
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

import pytz

from shared.secret_manager import is_running_on_gcp, get_project_id

# US Eastern timezone (handles EST/EDT automatically based on DST)
US_EASTERN = pytz.timezone('America/New_York')

logger = logging.getLogger(__name__)


class AlertPriority(Enum):
    """Alert priority levels determining delivery channels."""
    CRITICAL = "critical"  # SMS + Email - requires immediate attention
    HIGH = "high"          # SMS + Email - significant event
    MEDIUM = "medium"      # Email only - important but not urgent
    LOW = "low"            # Email only - informational


class AlertType(Enum):
    """Standardized alert types for all bots."""
    # Circuit Breaker / Safety Events
    CIRCUIT_BREAKER = "circuit_breaker"
    CRITICAL_INTERVENTION = "critical_intervention"
    DAILY_HALT = "daily_halt"

    # Position Events
    POSITION_OPENED = "position_opened"
    POSITION_CLOSED = "position_closed"
    PARTIAL_FILL = "partial_fill"

    # Exit Events
    STOP_LOSS = "stop_loss"
    PROFIT_TARGET = "profit_target"
    MAX_LOSS = "max_loss"
    EMERGENCY_EXIT = "emergency_exit"
    WING_BREACH = "wing_breach"
    TIME_EXIT = "time_exit"
    DTE_EXIT = "dte_exit"

    # Risk Events
    NAKED_POSITION = "naked_position"
    DELTA_BREACH = "delta_breach"
    GAP_WARNING = "gap_warning"
    VIX_THRESHOLD = "vix_threshold"
    PREMARKET_GAP = "premarket_gap"  # Big overnight/premarket move
    VIGILANT_ENTERED = "vigilant_entered"  # Price entering 0.1%-0.3% danger zone
    VIGILANT_EXITED = "vigilant_exited"    # Price back to safe zone (>0.3%)
    ITM_RISK_CLOSE = "itm_risk_close"      # Shorts closed due to ITM risk (0.1% threshold)

    # Roll/Recenter Events
    ROLL_COMPLETED = "roll_completed"
    ROLL_FAILED = "roll_failed"
    RECENTER = "recenter"

    # System Events
    BOT_STARTED = "bot_started"
    BOT_STOPPED = "bot_stopped"
    API_ERROR = "api_error"
    CONNECTION_RESTORED = "connection_restored"

    # Market Status Events (WhatsApp only by default)
    MARKET_OPENING_SOON = "market_opening_soon"  # 1h, 30m, 15m countdown
    MARKET_OPEN = "market_open"                   # Market just opened
    MARKET_CLOSED = "market_closed"               # Market just closed
    MARKET_HOLIDAY = "market_holiday"             # Holiday - market closed
    MARKET_EARLY_CLOSE = "market_early_close"     # Early close day alert

    # Daily Summary
    DAILY_SUMMARY = "daily_summary"


# Default priority mapping for alert types
DEFAULT_PRIORITIES = {
    AlertType.CIRCUIT_BREAKER: AlertPriority.CRITICAL,
    AlertType.CRITICAL_INTERVENTION: AlertPriority.CRITICAL,
    AlertType.DAILY_HALT: AlertPriority.CRITICAL,
    AlertType.NAKED_POSITION: AlertPriority.CRITICAL,
    AlertType.EMERGENCY_EXIT: AlertPriority.CRITICAL,

    AlertType.STOP_LOSS: AlertPriority.HIGH,
    AlertType.MAX_LOSS: AlertPriority.HIGH,
    AlertType.WING_BREACH: AlertPriority.HIGH,
    AlertType.ROLL_FAILED: AlertPriority.HIGH,
    AlertType.DELTA_BREACH: AlertPriority.HIGH,
    AlertType.API_ERROR: AlertPriority.HIGH,
    AlertType.PREMARKET_GAP: AlertPriority.HIGH,  # Big gap affects positions
    AlertType.VIGILANT_ENTERED: AlertPriority.HIGH,  # Price near short strike
    AlertType.ITM_RISK_CLOSE: AlertPriority.CRITICAL,  # Emergency close of shorts

    AlertType.POSITION_OPENED: AlertPriority.MEDIUM,
    AlertType.POSITION_CLOSED: AlertPriority.MEDIUM,
    AlertType.PROFIT_TARGET: AlertPriority.MEDIUM,
    AlertType.ROLL_COMPLETED: AlertPriority.MEDIUM,
    AlertType.RECENTER: AlertPriority.MEDIUM,
    AlertType.TIME_EXIT: AlertPriority.MEDIUM,
    AlertType.DTE_EXIT: AlertPriority.MEDIUM,
    AlertType.GAP_WARNING: AlertPriority.MEDIUM,
    AlertType.VIX_THRESHOLD: AlertPriority.MEDIUM,
    AlertType.PARTIAL_FILL: AlertPriority.MEDIUM,
    AlertType.CONNECTION_RESTORED: AlertPriority.MEDIUM,

    AlertType.BOT_STARTED: AlertPriority.LOW,
    AlertType.BOT_STOPPED: AlertPriority.LOW,
    AlertType.DAILY_SUMMARY: AlertPriority.LOW,
    AlertType.VIGILANT_EXITED: AlertPriority.LOW,  # Back to safe zone

    # Market status alerts - LOW priority (informational)
    AlertType.MARKET_OPENING_SOON: AlertPriority.LOW,
    AlertType.MARKET_OPEN: AlertPriority.LOW,
    AlertType.MARKET_CLOSED: AlertPriority.LOW,
    AlertType.MARKET_HOLIDAY: AlertPriority.LOW,
    AlertType.MARKET_EARLY_CLOSE: AlertPriority.LOW,
}


class AlertService:
    """
    Alert service that publishes trading alerts to Google Cloud Pub/Sub.

    On GCP:
        - Publishes to Pub/Sub topic "calypso-alerts"
        - Cloud Function processes messages and sends SMS/email

    Locally:
        - Logs alerts but doesn't publish (no Pub/Sub available)
        - Set ALERT_DRY_RUN=true to test alert formatting
    """

    PUBSUB_TOPIC = "calypso-alerts"

    def __init__(self, config: Dict[str, Any], bot_name: str):
        """
        Initialize the alert service.

        Args:
            config: Bot configuration dictionary
            bot_name: Name of the bot (e.g., "IRON_FLY", "DELTA_NEUTRAL", "ROLLING_PUT_DIAGONAL")
        """
        self.config = config
        self.bot_name = bot_name
        self._publisher = None
        self._topic_path = None
        self._initialized = False
        self._dry_run = os.environ.get("ALERT_DRY_RUN", "").lower() == "true"

        # Alert configuration from config file
        alert_config = config.get("alerts", {})
        self._enabled = alert_config.get("enabled", True)
        self._phone_number = alert_config.get("phone_number", "")
        self._email = alert_config.get("email", "")

        self._initialize()

    def _initialize(self) -> None:
        """Initialize Pub/Sub publisher if running on GCP."""
        if not self._enabled:
            logger.info("Alert service disabled in config")
            return

        if self._dry_run:
            logger.info("Alert service running in DRY RUN mode (ALERT_DRY_RUN=true)")
            self._initialized = True
            return

        if not is_running_on_gcp():
            logger.info("Not running on GCP - alerts will be logged only")
            return

        try:
            from google.cloud import pubsub_v1

            project_id = get_project_id()
            if not project_id:
                logger.error("Could not determine GCP project ID for Pub/Sub")
                return

            self._publisher = pubsub_v1.PublisherClient()
            self._topic_path = self._publisher.topic_path(project_id, self.PUBSUB_TOPIC)
            self._initialized = True

            logger.info(f"Alert service initialized with Pub/Sub topic: {self._topic_path}")

        except ImportError:
            logger.warning(
                "google-cloud-pubsub not installed. Run: pip install google-cloud-pubsub"
            )
        except Exception as e:
            logger.error(f"Failed to initialize Pub/Sub publisher: {e}")

    def send_alert(
        self,
        alert_type: AlertType,
        title: str,
        message: str,
        priority: Optional[AlertPriority] = None,
        details: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Send an alert via Pub/Sub.

        Args:
            alert_type: Type of alert (from AlertType enum)
            title: Short alert title (shown in SMS subject)
            message: Alert message body
            priority: Priority level (defaults to DEFAULT_PRIORITIES mapping)
            details: Additional structured data (e.g., prices, P&L)

        Returns:
            bool: True if alert was published successfully
        """
        if not self._enabled:
            logger.debug(f"Alert skipped (disabled): {alert_type.value} - {title}")
            return False

        # Get priority from mapping if not specified
        if priority is None:
            priority = DEFAULT_PRIORITIES.get(alert_type, AlertPriority.MEDIUM)

        # Build alert payload
        payload = {
            "bot_name": self.bot_name,
            "alert_type": alert_type.value,
            "priority": priority.value,
            "title": title,
            "message": message,
            "timestamp": datetime.now(US_EASTERN).isoformat(),
            "details": details or {},
            "delivery": {
                "sms": True,  # All priorities get WhatsApp (Cloud Function handles WhatsApp vs SMS)
                "email": True,  # All priorities get email
                "phone_number": self._phone_number,
                "email_address": self._email
            }
        }

        # Format log message
        priority_emoji = {
            AlertPriority.CRITICAL: "🚨",
            AlertPriority.HIGH: "⚠️",
            AlertPriority.MEDIUM: "📧",
            AlertPriority.LOW: "ℹ️"
        }
        emoji = priority_emoji.get(priority, "📧")

        log_msg = (
            f"{emoji} ALERT [{self.bot_name}] [{priority.value.upper()}] "
            f"{alert_type.value}: {title}"
        )

        if priority in [AlertPriority.CRITICAL, AlertPriority.HIGH]:
            logger.warning(log_msg)
        else:
            logger.info(log_msg)

        # Dry run mode - just log
        if self._dry_run:
            logger.info(f"DRY RUN - Would publish: {json.dumps(payload, indent=2)}")
            return True

        # Local mode - just log
        if not self._initialized or not self._publisher:
            logger.info(f"Alert logged (Pub/Sub not available): {json.dumps(payload)}")
            return False

        # Publish to Pub/Sub
        try:
            data = json.dumps(payload).encode("utf-8")
            future = self._publisher.publish(self._topic_path, data)
            message_id = future.result(timeout=5)  # Wait up to 5 seconds

            logger.debug(f"Alert published to Pub/Sub with message ID: {message_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to publish alert to Pub/Sub: {e}")
            # Still log the alert locally
            logger.warning(f"Alert content (failed to publish): {json.dumps(payload)}")
            return False

    # =========================================================================
    # CONVENIENCE METHODS FOR COMMON ALERTS
    # =========================================================================

    def circuit_breaker(
        self,
        reason: str,
        consecutive_failures: int = 0,
        details: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Send circuit breaker alert (CRITICAL)."""
        extra = details or {}
        extra["failures"] = consecutive_failures

        return self.send_alert(
            alert_type=AlertType.CIRCUIT_BREAKER,
            title="Circuit Breaker Triggered",
            message=f"Trading halted after {consecutive_failures} failures.\n\n{reason}\n\nManual review required before restart.",
            priority=AlertPriority.CRITICAL,
            details=extra
        )

    def position_opened(
        self,
        position_summary: str,
        cost_or_credit: float,
        details: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Send position opened alert (MEDIUM)."""
        extra = details or {}

        credit_label = "Credit received" if cost_or_credit > 0 else "Cost"
        return self.send_alert(
            alert_type=AlertType.POSITION_OPENED,
            title="Position Opened",
            message=f"{position_summary}\n\n{credit_label}: ${abs(cost_or_credit):.2f}",
            priority=AlertPriority.MEDIUM,
            details=extra
        )

    def position_closed(
        self,
        reason: str,
        pnl: float,
        details: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Send position closed alert (priority based on P&L)."""
        extra = details or {}

        # Determine priority based on exit reason and P&L
        if "emergency" in reason.lower() or "circuit" in reason.lower():
            priority = AlertPriority.CRITICAL
        elif pnl < -100:  # Significant loss
            priority = AlertPriority.HIGH
        else:
            priority = AlertPriority.MEDIUM

        pnl_emoji = "✅" if pnl >= 0 else "❌"
        pnl_sign = "+" if pnl >= 0 else ""

        return self.send_alert(
            alert_type=AlertType.POSITION_CLOSED,
            title=f"Position Closed {pnl_emoji}",
            message=f"Exit: {reason}\n\nP&L: {pnl_sign}${pnl:.2f}",
            priority=priority,
            details=extra
        )

    def stop_loss(
        self,
        trigger_price: float,
        pnl: float,
        details: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Send stop loss triggered alert (HIGH)."""
        extra = details or {}

        return self.send_alert(
            alert_type=AlertType.STOP_LOSS,
            title="Stop Loss Hit",
            message=f"Trigger price: ${trigger_price:.2f}\nRealized P&L: ${pnl:.2f}\n\nPosition closed automatically.",
            priority=AlertPriority.HIGH,
            details=extra
        )

    def wing_breach(
        self,
        breached_wing: str,
        current_price: float,
        wing_strike: float,
        pnl: float,
        details: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Send wing breach alert for Iron Fly (HIGH)."""
        extra = details or {}

        wing_label = breached_wing.capitalize()
        direction = "below" if breached_wing.lower() == "lower" else "above"

        return self.send_alert(
            alert_type=AlertType.WING_BREACH,
            title=f"{wing_label} Wing Breached",
            message=f"Price moved {direction} {wing_label.lower()} wing.\n\nPrice: ${current_price:.2f}\nWing strike: ${wing_strike:.2f}\nP&L: ${pnl:.2f}\n\nPosition closed automatically.",
            priority=AlertPriority.HIGH,
            details=extra
        )

    def profit_target(
        self,
        target_amount: float,
        actual_pnl: float,
        details: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Send profit target reached alert (MEDIUM)."""
        extra = details or {}

        return self.send_alert(
            alert_type=AlertType.PROFIT_TARGET,
            title="Profit Target Hit",
            message=f"Target: ${target_amount:.2f}\nRealized: +${actual_pnl:.2f}\n\nPosition closed with profit.",
            priority=AlertPriority.MEDIUM,
            details=extra
        )

    def emergency_exit(
        self,
        reason: str,
        pnl: float,
        details: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Send emergency exit alert (CRITICAL)."""
        extra = details or {}

        return self.send_alert(
            alert_type=AlertType.EMERGENCY_EXIT,
            title="Emergency Exit Executed",
            message=f"All positions closed immediately.\n\nReason: {reason}\nRealized P&L: ${pnl:.2f}",
            priority=AlertPriority.CRITICAL,
            details=extra
        )

    def naked_position(
        self,
        missing_leg: str,
        details: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Send naked position warning (CRITICAL)."""
        extra = details or {}

        return self.send_alert(
            alert_type=AlertType.NAKED_POSITION,
            title="Naked Position Detected",
            message=f"RISK: Position has no protection!\n\nMissing: {missing_leg}\n\nEmergency close initiated.",
            priority=AlertPriority.CRITICAL,
            details=extra
        )

    def gap_warning(
        self,
        gap_percent: float,
        previous_close: float,
        current_price: float,
        details: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Send pre-market gap warning (MEDIUM)."""
        extra = details or {}

        direction = "up" if gap_percent > 0 else "down"
        emoji = "📈" if gap_percent > 0 else "📉"

        return self.send_alert(
            alert_type=AlertType.GAP_WARNING,
            title=f"{emoji} Gap {direction.capitalize()} {abs(gap_percent):.1f}%",
            message=f"Pre-market gap detected.\n\nPrev close: ${previous_close:.2f}\nCurrent: ${current_price:.2f}\nGap: {gap_percent:+.1f}%\n\nEntry filters may block trading.",
            priority=AlertPriority.MEDIUM,
            details=extra
        )

    def vix_threshold(
        self,
        current_vix: float,
        threshold: float,
        action: str,
        details: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Send VIX threshold alert (MEDIUM)."""
        extra = details or {}

        over_under = "above" if current_vix > threshold else "at"

        return self.send_alert(
            alert_type=AlertType.VIX_THRESHOLD,
            title=f"VIX {over_under.capitalize()} Threshold",
            message=f"VIX currently {over_under} entry threshold.\n\nVIX: {current_vix:.2f}\nThreshold: {threshold:.2f}\n\nAction: {action}",
            priority=AlertPriority.MEDIUM,
            details=extra
        )

    def roll_completed(
        self,
        roll_type: str,
        old_position: str,
        new_position: str,
        cost: float,
        details: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Send roll completed alert (MEDIUM)."""
        extra = details or {}

        cost_label = "Credit" if cost > 0 else "Debit"

        return self.send_alert(
            alert_type=AlertType.ROLL_COMPLETED,
            title=f"{roll_type} Roll Complete",
            message=f"Successfully rolled position.\n\nClosed: {old_position}\nOpened: {new_position}\n{cost_label}: ${abs(cost):.2f}",
            priority=AlertPriority.MEDIUM,
            details=extra
        )

    def roll_failed(
        self,
        roll_type: str,
        reason: str,
        details: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Send roll failed alert (HIGH)."""
        extra = details or {}

        return self.send_alert(
            alert_type=AlertType.ROLL_FAILED,
            title=f"{roll_type} Roll Failed",
            message=f"Unable to complete roll.\n\nReason: {reason}\n\nManual intervention may be required.",
            priority=AlertPriority.HIGH,
            details=extra
        )

    def bot_started(self, environment: str, details: Optional[Dict[str, Any]] = None) -> bool:
        """Send bot started notification (LOW)."""
        extra = details or {}
        extra["mode"] = environment

        return self.send_alert(
            alert_type=AlertType.BOT_STARTED,
            title="Bot Started",
            message=f"{self.bot_name} is now running.\n\nMode: {environment.upper()}",
            priority=AlertPriority.LOW,
            details=extra
        )

    def bot_stopped(self, reason: str, details: Optional[Dict[str, Any]] = None) -> bool:
        """Send bot stopped notification (LOW, or HIGH if unexpected)."""
        extra = details or {}

        # Unexpected stops are HIGH priority
        is_unexpected = any(word in reason.lower() for word in ["error", "crash", "exception", "fail"])
        priority = AlertPriority.HIGH if is_unexpected else AlertPriority.LOW
        title = "Bot Stopped Unexpectedly" if is_unexpected else "Bot Stopped"

        return self.send_alert(
            alert_type=AlertType.BOT_STOPPED,
            title=title,
            message=f"{self.bot_name} has stopped.\n\nReason: {reason}",
            priority=priority,
            details=extra
        )

    def daily_summary(
        self,
        trades_count: int,
        total_pnl: float,
        win_rate: float,
        details: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Send end-of-day summary (LOW) - generic version."""
        extra = details or {}

        pnl_emoji = "📈" if total_pnl >= 0 else "📉"
        pnl_sign = "+" if total_pnl >= 0 else ""

        return self.send_alert(
            alert_type=AlertType.DAILY_SUMMARY,
            title=f"Daily Summary {pnl_emoji}",
            message=f"Today's trading complete.\n\nTrades: {trades_count}\nP&L: {pnl_sign}${total_pnl:.2f}\nWin rate: {win_rate:.0f}%",
            priority=AlertPriority.LOW,
            details=extra
        )

    def daily_summary_delta_neutral(
        self,
        summary: Dict[str, Any]
    ) -> bool:
        """
        Send comprehensive daily summary for Delta Neutral bot (LOW).

        Args:
            summary: Dictionary with daily summary data from log_daily_summary()
        """
        daily_pnl = summary.get("daily_pnl", 0)
        cumulative_pnl = summary.get("cumulative_pnl", 0)
        state = summary.get("state", "Unknown")
        spy_close = summary.get("spy_close", 0)
        vix_avg = summary.get("vix_avg", 0)
        net_theta = summary.get("total_theta", 0)
        roll_count = summary.get("roll_count", 0)
        recenter_count = summary.get("recenter_count", 0)
        rolled_today = summary.get("rolled_today", False)
        recentered_today = summary.get("recentered_today", False)
        dry_run = summary.get("dry_run", False)

        pnl_emoji = "📈" if daily_pnl >= 0 else "📉"
        pnl_sign = "+" if daily_pnl >= 0 else ""
        cum_sign = "+" if cumulative_pnl >= 0 else ""
        mode = "[DRY RUN] " if dry_run else "[LIVE] "

        # Build activity summary
        activities = []
        if rolled_today:
            activities.append("Shorts rolled")
        if recentered_today:
            activities.append("Position recentered")
        activity_str = ", ".join(activities) if activities else "No adjustments"

        message = (
            f"{mode}Delta Neutral - End of Day\n\n"
            f"State: {state}\n"
            f"SPY Close: ${spy_close:.2f}\n"
            f"VIX: {vix_avg:.2f}\n\n"
            f"Daily P&L: {pnl_sign}${daily_pnl:.2f}\n"
            f"Cumulative P&L: {cum_sign}${cumulative_pnl:.2f}\n"
            f"Net Theta: ${net_theta:.2f}/day\n\n"
            f"Today's Activity: {activity_str}\n"
            f"Total Rolls: {roll_count} | Recenters: {recenter_count}"
        )

        return self.send_alert(
            alert_type=AlertType.DAILY_SUMMARY,
            title=f"Delta Neutral Daily {pnl_emoji}",
            message=message,
            priority=AlertPriority.LOW,
            details=summary
        )

    def daily_summary_iron_fly(
        self,
        summary: Dict[str, Any]
    ) -> bool:
        """
        Send comprehensive daily summary for Iron Fly bot (LOW).

        Args:
            summary: Dictionary with daily summary data from log_daily_summary()
        """
        daily_pnl = summary.get("daily_pnl", 0)
        cumulative_pnl = summary.get("cumulative_pnl", 0)
        trades_today = summary.get("trades_today", 0)
        win_rate = summary.get("win_rate", 0)
        premium_collected = summary.get("premium_collected", 0)
        underlying_close = summary.get("underlying_close", 0)
        vix = summary.get("vix", 0)
        dry_run = summary.get("dry_run", False)
        notes = summary.get("notes", "")

        pnl_emoji = "📈" if daily_pnl >= 0 else "📉"
        pnl_sign = "+" if daily_pnl >= 0 else ""
        cum_sign = "+" if cumulative_pnl >= 0 else ""
        mode = "[DRY RUN] " if dry_run else "[LIVE] "

        # Determine trade outcome
        if trades_today == 0:
            outcome = "No trades (filters blocked or outside hours)"
        elif daily_pnl > 0:
            outcome = "Profit target hit"
        elif daily_pnl < 0:
            outcome = "Stop loss triggered"
        else:
            outcome = "Breakeven"

        message = (
            f"{mode}Iron Fly 0DTE - End of Day\n\n"
            f"SPX Close: ${underlying_close:.2f}\n"
            f"VIX: {vix:.2f}\n\n"
            f"Trades Today: {trades_today}\n"
            f"Outcome: {outcome}\n\n"
            f"Daily P&L: {pnl_sign}${daily_pnl:.2f}\n"
            f"Cumulative P&L: {cum_sign}${cumulative_pnl:.2f}\n"
            f"Premium Collected: ${premium_collected:.2f}\n"
            f"Win Rate: {win_rate:.0f}%"
        )

        return self.send_alert(
            alert_type=AlertType.DAILY_SUMMARY,
            title=f"Iron Fly Daily {pnl_emoji}",
            message=message,
            priority=AlertPriority.LOW,
            details=summary
        )

    def daily_summary_rolling_put_diagonal(
        self,
        summary: Dict[str, Any]
    ) -> bool:
        """
        Send comprehensive daily summary for Rolling Put Diagonal bot (LOW).

        Args:
            summary: Dictionary with daily summary data from log_daily_summary()
        """
        daily_pnl = summary.get("daily_pnl", 0)
        cumulative_pnl = summary.get("cumulative_pnl", 0)
        qqq_close = summary.get("qqq_close", 0)
        ema_9 = summary.get("ema_9", 0)
        macd_histogram = summary.get("macd_histogram", 0)
        cci = summary.get("cci", 0)
        roll_type = summary.get("roll_type", "")
        campaign_number = summary.get("campaign_number", 0)
        entry_conditions = summary.get("entry_conditions_met", "No")
        long_delta = summary.get("long_put_delta", 0)
        dry_run = summary.get("dry_run", False)
        notes = summary.get("notes", "")

        pnl_emoji = "📈" if daily_pnl >= 0 else "📉"
        pnl_sign = "+" if daily_pnl >= 0 else ""
        cum_sign = "+" if cumulative_pnl >= 0 else ""
        mode = "[DRY RUN] " if dry_run else "[LIVE] "

        # Activity description
        if roll_type:
            activity = f"Roll: {roll_type}"
        elif campaign_number > 0:
            activity = f"Campaign #{campaign_number} active"
        else:
            activity = "Waiting for entry"

        message = (
            f"{mode}Rolling Put Diagonal - End of Day\n\n"
            f"QQQ Close: ${qqq_close:.2f}\n"
            f"9 EMA: ${ema_9:.2f}\n"
            f"MACD Hist: {macd_histogram:.4f}\n"
            f"CCI: {cci:.1f}\n\n"
            f"Daily P&L: {pnl_sign}${daily_pnl:.2f}\n"
            f"Cumulative P&L: {cum_sign}${cumulative_pnl:.2f}\n\n"
            f"Activity: {activity}\n"
            f"Entry Conditions: {entry_conditions}\n"
            f"Long Put Delta: {long_delta:.2f}"
        )

        return self.send_alert(
            alert_type=AlertType.DAILY_SUMMARY,
            title=f"Rolling Put Diagonal Daily {pnl_emoji}",
            message=message,
            priority=AlertPriority.LOW,
            details=summary
        )

    # =========================================================================
    # MARKET STATUS ALERTS (WhatsApp + Email)
    # =========================================================================

    def market_opening_soon(
        self,
        minutes_until_open: int,
        current_time: str,
        details: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Send market opening countdown alert (LOW - WhatsApp + Email).

        Called at 1h, 30m, 15m before market open.
        """
        extra = details or {}

        if minutes_until_open >= 60:
            time_str = f"{minutes_until_open // 60} hour"
        else:
            time_str = f"{minutes_until_open} min"

        # Check if early close day
        is_early = extra.get("is_early_close", False)
        close_note = "\nNote: Early close today (1:00 PM)" if is_early else ""

        return self.send_alert(
            alert_type=AlertType.MARKET_OPENING_SOON,
            title=f"Market Opens in {time_str}",
            message=f"US markets open at 9:30 AM ET.{close_note}",
            priority=AlertPriority.LOW,
            details=extra
        )

    def market_open(
        self,
        current_time: str,
        vix_level: Optional[float] = None,
        spy_price: Optional[float] = None,
        details: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Send market open notification (LOW - WhatsApp + Email)."""
        extra = details or {}
        if vix_level is not None:
            extra["vix"] = vix_level
        if spy_price is not None:
            extra["spy"] = spy_price

        # Check close time from details
        close_time = extra.get("close_time", "4:00 PM ET")

        return self.send_alert(
            alert_type=AlertType.MARKET_OPEN,
            title="Market Open",
            message=f"US markets are now open.\n\nClose: {close_time}",
            priority=AlertPriority.LOW,
            details=extra
        )

    def market_closed(
        self,
        current_time: str,
        spy_close: Optional[float] = None,
        day_change_pct: Optional[float] = None,
        details: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Send market closed notification (LOW - WhatsApp + Email)."""
        extra = details or {}
        if spy_close is not None:
            extra["spy_close"] = spy_close
        if day_change_pct is not None:
            extra["day_change"] = f"{day_change_pct:+.2f}%"

        # Get next open from details
        next_open = extra.get("next_open", "Tomorrow 9:30 AM ET")
        close_type = extra.get("close_type", "Regular close")

        return self.send_alert(
            alert_type=AlertType.MARKET_CLOSED,
            title="Market Closed",
            message=f"US markets are now closed.\n\n{close_type}\nNext open: {next_open}",
            priority=AlertPriority.LOW,
            details=extra
        )

    def market_holiday(
        self,
        holiday_name: str,
        next_open_date: str,
        details: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Send market holiday notification (LOW - WhatsApp + Email)."""
        extra = details or {}

        return self.send_alert(
            alert_type=AlertType.MARKET_HOLIDAY,
            title=f"Holiday: {holiday_name}",
            message=f"US markets closed today.\n\nNext open: {next_open_date}",
            priority=AlertPriority.LOW,
            details=extra
        )

    def market_early_close(
        self,
        reason: str,
        close_time: str,
        details: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Send early close day warning (LOW - WhatsApp + Email)."""
        extra = details or {}

        return self.send_alert(
            alert_type=AlertType.MARKET_EARLY_CLOSE,
            title=f"Early Close: {close_time}",
            message=f"Markets close early today.\n\nClose time: {close_time} ET\nReason: {reason}",
            priority=AlertPriority.LOW,
            details=extra
        )

    def premarket_gap(
        self,
        symbol: str,
        gap_percent: float,
        previous_close: float,
        current_price: float,
        affected_positions: str,
        details: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Send big pre-market gap alert (HIGH - WhatsApp + Email).

        This is for significant overnight/premarket moves that will affect positions.
        """
        extra = details or {}

        direction = "up" if gap_percent > 0 else "down"
        emoji = "🚀" if gap_percent > 0 else "📉"

        return self.send_alert(
            alert_type=AlertType.PREMARKET_GAP,
            title=f"{emoji} {symbol} Gap {direction.capitalize()} {abs(gap_percent):.1f}%",
            message=f"Large overnight move detected.\n\n{symbol}: ${previous_close:.2f} → ${current_price:.2f}\n\nAffected: {affected_positions}",
            priority=AlertPriority.HIGH,
            details=extra
        )


# Test function
if __name__ == "__main__":
    import sys

    print("=" * 60)
    print("ALERT SERVICE TEST")
    print("=" * 60)

    # Set dry run mode for testing
    os.environ["ALERT_DRY_RUN"] = "true"

    # Create mock config
    test_config = {
        "alerts": {
            "enabled": True,
            "phone_number": "+1234567890",
            "email": "test@example.com"
        }
    }

    alert_service = AlertService(test_config, "IRON_FLY")

    print("\nTesting alert methods...\n")

    # Test various alert types
    alert_service.circuit_breaker(
        reason="5 consecutive API failures",
        consecutive_failures=5,
        details={"last_error": "Connection timeout"}
    )

    alert_service.position_opened(
        position_summary="Iron Fly @ 6020 (wings: 5990/6050)",
        cost_or_credit=-245.50,
        details={"strike": 6020, "wings": [5990, 6050]}
    )

    alert_service.wing_breach(
        breached_wing="lower",
        current_price=5989.50,
        wing_strike=5990.00,
        pnl=-320.00,
        details={"exit_time": "10:45:30"}
    )

    alert_service.profit_target(
        target_amount=75.00,
        actual_pnl=78.50,
        details={"hold_time_minutes": 25}
    )

    alert_service.emergency_exit(
        reason="5% loss threshold exceeded",
        pnl=-523.00,
        details={"threshold_percent": 5.0}
    )

    alert_service.daily_summary(
        trades_count=3,
        total_pnl=125.50,
        win_rate=66.7,
        details={"best_trade": 78.50, "worst_trade": -45.00}
    )

    print("\n" + "=" * 60)
    print("All tests completed!")
    print("=" * 60)
