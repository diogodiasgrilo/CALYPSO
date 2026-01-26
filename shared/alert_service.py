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
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from shared.secret_manager import is_running_on_gcp, get_project_id

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
            "timestamp": datetime.utcnow().isoformat() + "Z",
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
        extra["consecutive_failures"] = consecutive_failures
        extra["reason"] = reason

        return self.send_alert(
            alert_type=AlertType.CIRCUIT_BREAKER,
            title="Circuit Breaker Triggered",
            message=f"{reason}\n{consecutive_failures} failures detected.\nManual review required.",
            priority=AlertPriority.CRITICAL,
            details=extra
        )

    def position_opened(
        self,
        position_summary: str,
        cost_or_credit: float,
        details: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Send position opened alert (MEDIUM - email only)."""
        extra = details or {}
        extra["cost_or_credit"] = cost_or_credit

        return self.send_alert(
            alert_type=AlertType.POSITION_OPENED,
            title="Position Opened",
            message=f"{position_summary}\nCost/Credit: ${cost_or_credit:.2f}",
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
        extra["pnl"] = pnl
        extra["reason"] = reason

        # Determine priority based on exit reason and P&L
        if "emergency" in reason.lower() or "circuit" in reason.lower():
            priority = AlertPriority.CRITICAL
        elif pnl < -100:  # Significant loss
            priority = AlertPriority.HIGH
        else:
            priority = AlertPriority.MEDIUM

        pnl_emoji = "✅" if pnl >= 0 else "❌"

        return self.send_alert(
            alert_type=AlertType.POSITION_CLOSED,
            title=f"Position Closed {pnl_emoji}",
            message=f"{reason}\nP&L: ${pnl:.2f}",
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
        extra["trigger_price"] = trigger_price
        extra["pnl"] = pnl

        return self.send_alert(
            alert_type=AlertType.STOP_LOSS,
            title="Stop Loss Triggered",
            message=f"Price: ${trigger_price:.2f}\nP&L: ${pnl:.2f}\nPosition closed.",
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
        extra["breached_wing"] = breached_wing
        extra["current_price"] = current_price
        extra["wing_strike"] = wing_strike
        extra["pnl"] = pnl

        return self.send_alert(
            alert_type=AlertType.WING_BREACH,
            title=f"{breached_wing.upper()} Wing Breached",
            message=f"Price ${current_price:.2f} touched {breached_wing} wing at ${wing_strike:.2f}\nP&L: ${pnl:.2f}\nPosition closed.",
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
        extra["target_amount"] = target_amount
        extra["actual_pnl"] = actual_pnl

        return self.send_alert(
            alert_type=AlertType.PROFIT_TARGET,
            title="Profit Target Reached",
            message=f"Target: ${target_amount:.2f}\nActual P&L: ${actual_pnl:.2f}\nPosition closed.",
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
        extra["pnl"] = pnl
        extra["reason"] = reason

        return self.send_alert(
            alert_type=AlertType.EMERGENCY_EXIT,
            title="EMERGENCY EXIT",
            message=f"{reason}\nP&L: ${pnl:.2f}\nAll positions closed.",
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
        extra["missing_leg"] = missing_leg

        return self.send_alert(
            alert_type=AlertType.NAKED_POSITION,
            title="NAKED POSITION DETECTED",
            message=f"Missing leg: {missing_leg}\nNo protection in place!\nEmergency close initiated.",
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
        extra["gap_percent"] = gap_percent
        extra["previous_close"] = previous_close
        extra["current_price"] = current_price

        direction = "UP" if gap_percent > 0 else "DOWN"

        return self.send_alert(
            alert_type=AlertType.GAP_WARNING,
            title=f"Pre-Market Gap {direction}",
            message=f"Gap: {abs(gap_percent):.1f}%\nPrev close: ${previous_close:.2f}\nCurrent: ${current_price:.2f}\nEntry may be blocked.",
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
        extra["current_vix"] = current_vix
        extra["threshold"] = threshold

        return self.send_alert(
            alert_type=AlertType.VIX_THRESHOLD,
            title="VIX Threshold Alert",
            message=f"VIX: {current_vix:.2f}\nThreshold: {threshold:.2f}\nAction: {action}",
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
        extra["roll_type"] = roll_type
        extra["old_position"] = old_position
        extra["new_position"] = new_position
        extra["cost"] = cost

        return self.send_alert(
            alert_type=AlertType.ROLL_COMPLETED,
            title=f"{roll_type} Roll Complete",
            message=f"Old: {old_position}\nNew: {new_position}\nCost: ${cost:.2f}",
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
        extra["roll_type"] = roll_type
        extra["reason"] = reason

        return self.send_alert(
            alert_type=AlertType.ROLL_FAILED,
            title=f"{roll_type} Roll FAILED",
            message=f"Failed to roll: {reason}\nManual review may be needed.",
            priority=AlertPriority.HIGH,
            details=extra
        )

    def bot_started(self, environment: str, details: Optional[Dict[str, Any]] = None) -> bool:
        """Send bot started notification (LOW)."""
        extra = details or {}
        extra["environment"] = environment

        return self.send_alert(
            alert_type=AlertType.BOT_STARTED,
            title=f"Bot Started ({environment})",
            message=f"{self.bot_name} is now running in {environment} mode.",
            priority=AlertPriority.LOW,
            details=extra
        )

    def bot_stopped(self, reason: str, details: Optional[Dict[str, Any]] = None) -> bool:
        """Send bot stopped notification (LOW, or HIGH if unexpected)."""
        extra = details or {}
        extra["reason"] = reason

        # Unexpected stops are HIGH priority
        is_unexpected = any(word in reason.lower() for word in ["error", "crash", "exception", "fail"])
        priority = AlertPriority.HIGH if is_unexpected else AlertPriority.LOW

        return self.send_alert(
            alert_type=AlertType.BOT_STOPPED,
            title="Bot Stopped",
            message=f"{self.bot_name} has stopped.\nReason: {reason}",
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
        """Send end-of-day summary (LOW)."""
        extra = details or {}
        extra["trades_count"] = trades_count
        extra["total_pnl"] = total_pnl
        extra["win_rate"] = win_rate

        pnl_emoji = "📈" if total_pnl >= 0 else "📉"

        return self.send_alert(
            alert_type=AlertType.DAILY_SUMMARY,
            title=f"Daily Summary {pnl_emoji}",
            message=f"Trades: {trades_count}\nTotal P&L: ${total_pnl:.2f}\nWin Rate: {win_rate:.1f}%",
            priority=AlertPriority.LOW,
            details=extra
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
        extra["minutes_until_open"] = minutes_until_open

        if minutes_until_open >= 60:
            time_str = f"{minutes_until_open // 60} hour"
        else:
            time_str = f"{minutes_until_open} minutes"

        return self.send_alert(
            alert_type=AlertType.MARKET_OPENING_SOON,
            title=f"Market Opens in {time_str}",
            message=f"NYSE/NASDAQ opens at 9:30 AM ET\nCurrent time: {current_time}\n\nPrepare for trading session.",
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
            extra["vix_level"] = vix_level
        if spy_price is not None:
            extra["spy_price"] = spy_price

        message_parts = [f"Market is now OPEN\nTime: {current_time}"]
        if spy_price is not None:
            message_parts.append(f"SPY: ${spy_price:.2f}")
        if vix_level is not None:
            message_parts.append(f"VIX: {vix_level:.2f}")

        return self.send_alert(
            alert_type=AlertType.MARKET_OPEN,
            title="Market OPEN",
            message="\n".join(message_parts),
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
            extra["day_change_pct"] = day_change_pct

        message_parts = [f"Market is now CLOSED\nTime: {current_time}"]
        if spy_close is not None:
            message_parts.append(f"SPY Close: ${spy_close:.2f}")
        if day_change_pct is not None:
            change_emoji = "📈" if day_change_pct >= 0 else "📉"
            message_parts.append(f"Day Change: {change_emoji} {day_change_pct:+.2f}%")

        return self.send_alert(
            alert_type=AlertType.MARKET_CLOSED,
            title="Market CLOSED",
            message="\n".join(message_parts),
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
        extra["holiday_name"] = holiday_name
        extra["next_open_date"] = next_open_date

        return self.send_alert(
            alert_type=AlertType.MARKET_HOLIDAY,
            title=f"Market Closed - {holiday_name}",
            message=f"US markets are closed today for {holiday_name}.\n\nNext trading day: {next_open_date}",
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
        extra["reason"] = reason
        extra["close_time"] = close_time

        return self.send_alert(
            alert_type=AlertType.MARKET_EARLY_CLOSE,
            title=f"Early Close Today - {close_time}",
            message=f"Market closes early today at {close_time} ET\nReason: {reason}\n\nPlan positions accordingly.",
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
        extra["symbol"] = symbol
        extra["gap_percent"] = gap_percent
        extra["previous_close"] = previous_close
        extra["current_price"] = current_price

        direction = "UP" if gap_percent > 0 else "DOWN"
        gap_emoji = "🚀" if gap_percent > 0 else "📉"

        return self.send_alert(
            alert_type=AlertType.PREMARKET_GAP,
            title=f"{gap_emoji} {symbol} Gap {direction} {abs(gap_percent):.1f}%",
            message=f"Significant pre-market move detected!\n\n{symbol}: ${previous_close:.2f} → ${current_price:.2f}\nGap: {gap_percent:+.1f}%\n\nAffected positions:\n{affected_positions}",
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
