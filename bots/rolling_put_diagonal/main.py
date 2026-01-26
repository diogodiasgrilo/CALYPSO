#!/usr/bin/env python3
"""
main.py - Rolling Put Diagonal Trading Bot Entry Point

Bill Belt's Rolling Put Diagonal strategy on QQQ.
Generates daily income by selling ATM puts against a longer-dated
OTM put for protection.

Strategy Summary:
-----------------
1. Buy 14 DTE put at 33 delta (protection)
2. Sell daily ATM puts for income
3. Roll short puts daily based on market direction
4. Close campaign 1-2 days before long put expires

Entry Filters:
- Price > 9 EMA (bullish bias)
- MACD histogram rising (momentum)
- CCI < 100 (not overbought)
- No FOMC or major QQQ earnings approaching

Usage:
------
    python main.py                    # Run in SIM environment
    python main.py --live             # Run in LIVE environment
    python main.py --dry-run          # Simulate without placing orders
    python main.py --live --dry-run   # Test with live data, no orders
    python main.py --status           # Show current status only

Author: Trading Bot Developer
Date: 2026
"""

import os
import sys
import json
import time
import signal
import argparse
import logging
import subprocess
from datetime import datetime
from typing import Optional

# Ensure project root is in path for imports
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# Import shared modules
from shared.saxo_client import SaxoClient
from shared.logger_service import TradeLoggerService, setup_logging
from shared.market_hours import (
    is_market_open,
    get_market_status_message,
    calculate_sleep_duration,
    is_weekend,
    is_market_holiday,
    get_us_market_time,
    get_holiday_name,
    is_pre_market,
    is_saxo_price_available,
    get_extended_hours_status_message,
)
from shared.config_loader import ConfigLoader, get_config_loader
from shared.secret_manager import is_running_on_gcp
from shared.event_calendar import get_event_status_message

# Import bot-specific strategy
from bots.rolling_put_diagonal.strategy import RollingPutDiagonalStrategy, RPDState

# Configure main logger
logger = logging.getLogger(__name__)

# Global flag for graceful shutdown
shutdown_requested = False


def signal_handler(signum, frame):
    """Handle shutdown signals (CTRL+C, SIGTERM)."""
    global shutdown_requested
    logger.info(f"\nShutdown signal received ({signum}). Initiating graceful shutdown...")
    shutdown_requested = True


def interruptible_sleep(seconds: int, check_interval: int = 5) -> bool:
    """
    Sleep for the specified duration, checking for shutdown periodically.

    Args:
        seconds: Total seconds to sleep
        check_interval: How often to check for shutdown

    Returns:
        True if sleep completed, False if interrupted
    """
    remaining = seconds
    while remaining > 0 and not shutdown_requested:
        time.sleep(min(check_interval, remaining))
        remaining -= check_interval
    return not shutdown_requested


def kill_existing_bot_instances() -> int:
    """
    Find and kill any existing Rolling Put Diagonal bot instances.

    Returns:
        Number of processes killed
    """
    current_pid = os.getpid()
    killed_count = 0

    try:
        # Find all Python processes running this bot
        result = subprocess.run(
            ["pgrep", "-f", "rolling_put_diagonal.*main.py"],
            capture_output=True,
            text=True
        )

        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split('\n')

            for pid_str in pids:
                try:
                    pid = int(pid_str.strip())
                    if pid != current_pid:
                        logger.info(f"Found existing bot instance (PID: {pid}), terminating...")
                        os.kill(pid, signal.SIGTERM)
                        killed_count += 1
                        time.sleep(1)
                except (ValueError, ProcessLookupError):
                    pass

        if killed_count > 0:
            logger.info(f"Terminated {killed_count} existing bot instance(s)")
            time.sleep(2)

    except FileNotFoundError:
        logger.warning("pgrep not available - cannot check for existing instances")
    except Exception as e:
        logger.warning(f"Error checking for existing instances: {e}")

    return killed_count


def load_config(config_path: str = "config/config.json") -> dict:
    """
    Load configuration from appropriate source.

    Args:
        config_path: Path to local configuration file

    Returns:
        Configuration dictionary
    """
    loader = ConfigLoader(config_path)
    config = loader.load_config()
    return config


def validate_config(config: dict) -> bool:
    """
    Validate required configuration values.

    Args:
        config: Configuration dictionary

    Returns:
        True if valid

    Raises:
        ValueError: If required config is missing
    """
    if "account" not in config:
        raise ValueError("Missing config section: account")

    environment = config.get("saxo_api", {}).get("environment", "sim")
    account_config = config["account"]

    if environment in account_config and isinstance(account_config[environment], dict):
        env_account = account_config[environment]
        if "account_key" not in env_account:
            raise ValueError(f"Missing config key: account.{environment}.account_key")
        if "client_key" not in env_account:
            raise ValueError(f"Missing config key: account.{environment}.client_key")
    else:
        if "account_key" not in account_config:
            raise ValueError("Missing config key: account.account_key")

    if "saxo_api" not in config:
        raise ValueError("Missing config section: saxo_api")

    environment = config["saxo_api"].get("environment", "sim")
    if environment not in config["saxo_api"]:
        raise ValueError(f"Missing config section: saxo_api.{environment}")

    env_config = config["saxo_api"][environment]
    if "app_key" not in env_config or not env_config["app_key"]:
        raise ValueError(f"Missing app_key for {environment} environment")
    if "app_secret" not in env_config or not env_config["app_secret"]:
        raise ValueError(f"Missing app_secret for {environment} environment")

    # Validate strategy section
    if "strategy" not in config:
        raise ValueError("Missing config section: strategy")

    strategy = config["strategy"]
    if "underlying_uic" not in strategy:
        raise ValueError("Missing config key: strategy.underlying_uic")

    # CFG-002: Validate strategy value ranges
    _validate_strategy_value_ranges(strategy)

    return True


def _validate_strategy_value_ranges(strategy: dict) -> None:
    """
    CFG-002: Validate that strategy configuration values are in sensible ranges.

    This prevents silent errors from misconfiguration that could lead to
    bad trades or no trades at all.

    Args:
        strategy: Strategy configuration section

    Raises:
        ValueError: If values are out of range
    """
    warnings = []

    # Long put configuration
    long_put = strategy.get("long_put", {})

    # Target DTE should be 7-60 days for Bill Belt's strategy
    target_dte = long_put.get("target_dte", 14)
    if target_dte < 7 or target_dte > 60:
        raise ValueError(f"CFG-002: long_put.target_dte ({target_dte}) out of range [7, 60]")

    # Target delta should be between -0.15 and -0.50 for OTM to ATM puts
    target_delta = long_put.get("target_delta", -0.33)
    if target_delta > 0:
        raise ValueError(f"CFG-002: long_put.target_delta ({target_delta}) should be negative for puts")
    if target_delta < -0.60 or target_delta > -0.15:
        warnings.append(f"long_put.target_delta ({target_delta}) unusual - typical range is [-0.50, -0.20]")

    # Delta tolerance should be small
    delta_tolerance = long_put.get("delta_tolerance", 0.05)
    if delta_tolerance < 0.01 or delta_tolerance > 0.15:
        warnings.append(f"long_put.delta_tolerance ({delta_tolerance}) unusual - typical range is [0.02, 0.10]")

    # Roll delta threshold (should be less than target delta in absolute terms)
    roll_threshold = long_put.get("roll_delta_threshold", -0.20)
    if roll_threshold > target_delta:  # e.g., -0.20 > -0.33 in absolute value terms
        warnings.append(f"roll_delta_threshold ({roll_threshold}) should be lower than target_delta ({target_delta})")

    # Short put configuration
    short_put = strategy.get("short_put", {})

    # Short DTE should be 0-3 days for daily puts
    short_dte = short_put.get("target_dte", 1)
    if short_dte < 0 or short_dte > 7:
        raise ValueError(f"CFG-002: short_put.target_dte ({short_dte}) out of range [0, 7] for daily puts")

    # Short delta should be around -0.50 for ATM
    short_delta = short_put.get("target_delta", -0.50)
    if short_delta > 0:
        raise ValueError(f"CFG-002: short_put.target_delta ({short_delta}) should be negative for puts")

    # Position size
    position_size = strategy.get("position_size", 1)
    if position_size < 1 or position_size > 10:
        raise ValueError(f"CFG-002: position_size ({position_size}) out of range [1, 10]")

    # Management configuration
    management = strategy.get("management", {})

    # Max unrealized loss should be positive
    max_loss = management.get("max_unrealized_loss", 500)
    if max_loss < 0:
        raise ValueError(f"CFG-002: max_unrealized_loss ({max_loss}) should be positive")
    if max_loss > 5000:
        warnings.append(f"max_unrealized_loss (${max_loss}) is very high - consider if appropriate")

    # Market open delay should be 0-15 minutes
    open_delay = management.get("market_open_delay_minutes", 3)
    if open_delay < 0 or open_delay > 30:
        raise ValueError(f"CFG-002: market_open_delay_minutes ({open_delay}) out of range [0, 30]")

    # Flash crash threshold should be 1-5%
    flash_threshold = management.get("flash_crash_threshold_percent", 2.0)
    if flash_threshold < 0.5 or flash_threshold > 10:
        warnings.append(f"flash_crash_threshold_percent ({flash_threshold}%) unusual - typical range is [1%, 5%]")

    # Print warnings (non-blocking)
    import logging
    logger = logging.getLogger(__name__)
    for warn in warnings:
        logger.warning(f"CFG-002: {warn}")


def print_banner():
    """Print the application banner."""
    banner = """
    +-----------------------------------------------------------------+
    |                                                                 |
    |         ROLLING PUT DIAGONAL TRADING BOT                        |
    |         ====================================                    |
    |                                                                 |
    |         Strategy: Bill Belt's Rolling Put Diagonal on QQQ       |
    |         Daily ATM put sales against 14 DTE protective put       |
    |                                                                 |
    |         Version: 1.0.0                                          |
    |         API: Saxo Bank OpenAPI                                  |
    |                                                                 |
    +-----------------------------------------------------------------+
    """
    print(banner)


def run_bot(config: dict, dry_run: bool = False, check_interval: int = 60):
    """
    Run the main trading bot loop.

    Args:
        config: Configuration dictionary
        dry_run: If True, simulate without placing real trades
        check_interval: Seconds between strategy checks
    """
    global shutdown_requested

    # Initialize logging service
    trade_logger = setup_logging(config, bot_name="ROLLING_PUT_DIAGONAL")
    trade_logger.log_event("=" * 60)
    trade_logger.log_event("ROLLING PUT DIAGONAL BOT STARTING")
    trade_logger.log_event(f"Mode: {'DRY RUN (Simulation)' if dry_run else 'LIVE TRADING'}")
    trade_logger.log_event(f"Environment: {config['saxo_api'].get('environment', 'sim').upper()}")
    trade_logger.log_event(f"Check Interval: {check_interval} seconds")
    trade_logger.log_event("=" * 60)

    # Initialize Saxo client
    client = SaxoClient(config)

    # Authenticate with Saxo API
    trade_logger.log_event("Authenticating with Saxo Bank API...")
    if not client.authenticate():
        trade_logger.log_error("Failed to authenticate. Please check your credentials.")
        return

    trade_logger.log_event("Authentication successful!")

    # Initialize strategy
    strategy = RollingPutDiagonalStrategy(
        client=client,
        config=config,
        trade_logger=trade_logger,
        dry_run=dry_run
    )

    # Attempt to recover existing positions on startup
    trade_logger.log_event("Checking for existing positions to recover...")
    positions_recovered = strategy.recover_positions()
    if positions_recovered:
        trade_logger.log_event(f"Position recovery complete - state: {strategy.state.value}")
    else:
        trade_logger.log_event("No existing positions found - starting fresh")

    # Check for stuck states from previous run
    if strategy._check_stuck_state():
        trade_logger.log_event("Recovered from stuck state")

    # Log initial status
    trade_logger.log_event(get_market_status_message())
    trade_logger.log_event(get_event_status_message())

    # Log dashboard metrics on startup (always update on restart for fresh data)
    try:
        trade_logger.log_event("Logging dashboard metrics on startup...")

        # Log Account Summary
        strategy.log_account_summary()

        # Log Position snapshot if we have an active diagonal
        status = strategy.get_status_summary()
        if status.get('position'):
            strategy.log_position_to_sheets()

        # Log Performance Metrics
        strategy.log_performance_metrics()

        # Log bot startup activity (always - so we know when bot started)
        trade_logger.log_bot_activity(
            level="INFO",
            component="Main",
            message=f"Bot started - State: {status['state']}, Campaign: #{status['campaign_count']}",
            spy_price=status['qqq_price'],  # RPD uses QQQ
            vix=0,  # RPD doesn't track VIX
            flush=True
        )

        trade_logger.log_event("Dashboard metrics logged to Google Sheets")
    except Exception as e:
        trade_logger.log_error(f"Failed to log startup dashboard metrics: {e}")

    # Track daily activities
    last_daily_reset = None
    last_dashboard_log = None
    dashboard_interval = 900  # 15 minutes
    last_bot_log_time = get_us_market_time()  # Use ET market time for consistency when traveling
    bot_log_interval = 3600  # Log to Google Sheets Bot Logs every hour
    market_open_gap_checked = False  # MKT-001: Track if we've checked the gap at market open today

    # CONN-003: Setup WebSocket streaming for real-time price updates
    # This provides faster price updates than REST polling, useful for:
    # - MKT-002 flash crash detection
    # - Real-time P&L tracking
    # - Faster response to market moves
    subscriptions = strategy.get_streaming_subscriptions()
    trade_logger.log_event(f"Starting price streaming for {len(subscriptions)} instruments...")

    def price_update_handler(uic: int, data: dict):
        """Handle real-time price updates from WebSocket."""
        strategy.handle_price_update(uic, data)

    streaming_started = client.start_price_streaming(subscriptions, price_update_handler)
    if streaming_started:
        trade_logger.log_event("WebSocket streaming started successfully")
    else:
        trade_logger.log_event("Warning: WebSocket streaming not started - using REST polling fallback")

    # Main loop
    iteration = 0
    while not shutdown_requested:
        iteration += 1
        now = get_us_market_time()
        today = now.date()

        # Daily reset
        if last_daily_reset != today:
            trade_logger.log_event(f"=== New Trading Day: {today} ===")
            strategy.metrics.reset_daily_tracking(
                current_pnl=strategy.metrics.total_pnl,
                qqq_price=strategy.current_price if strategy.current_price > 0 else 0
            )
            last_daily_reset = today
            market_open_gap_checked = False  # MKT-001: Reset gap check flag for new day

        # Check market status
        if not is_market_open():
            # Market is closed
            holiday_name = get_holiday_name(now)
            if is_weekend(now):
                reason = "Weekend"
            elif holiday_name:
                reason = f"Holiday ({holiday_name})"
            elif now.hour < 9 or (now.hour == 9 and now.minute < 30):
                reason = "Pre-market"
            else:
                reason = "After-hours"

            # Calculate smart sleep duration (max 15 min to keep token alive - Saxo tokens expire in ~20 min)
            sleep_seconds = calculate_sleep_duration(max_sleep=900)

            # PRECISE WAKE-UP: Calculate exact time until 9:30 AM for trading days
            market_open_time = now.replace(hour=9, minute=30, second=0, microsecond=0)

            if now < market_open_time and now.weekday() < 5 and not holiday_name:
                # We're in pre-market on a trading day - calculate precise wake time
                seconds_until_open = (market_open_time - now).total_seconds()

                if seconds_until_open > 0 and seconds_until_open < sleep_seconds:
                    # Wake at exactly 9:30 AM instead of generic sleep
                    sleep_seconds = int(seconds_until_open)
                    trade_logger.log_event(f"Pre-market: will wake at exactly 9:30 AM ({sleep_seconds}s)")

            if sleep_seconds > 0:
                sleep_minutes = sleep_seconds // 60

                # MKT-001: During pre-market on trading days, log comprehensive market analysis
                # IMPORTANT: Only fetch prices when Saxo can provide them (7:00 AM - 5:00 PM ET)
                # Before 7:00 AM, Saxo has no pre-market data available
                is_premarket_session = is_pre_market(now)  # True if 7:00-9:30 AM on trading day
                saxo_has_prices = is_saxo_price_available(now)  # True if 7:00 AM - 5:00 PM

                if is_premarket_session and saxo_has_prices:
                    try:
                        # Get current QQQ price from pre-market session
                        # Saxo provides extended hours data starting at 7:00 AM ET
                        qqq_price = 0.0
                        prev_close = 0.0
                        quote_response = client.get_quote(strategy.underlying_uic, asset_type="Etf")
                        if quote_response and "Quote" in quote_response:
                            quote = quote_response["Quote"]
                            qqq_price = quote.get("Mid") or ((quote.get("Bid", 0) + quote.get("Ask", 0)) / 2)
                            # Extract previous close from PriceInfoDetails (Saxo provides this)
                            price_details = quote_response.get("PriceInfoDetails", {})
                            prev_close = price_details.get("LastClose", 0.0)

                        if qqq_price > 0:
                            # Get comprehensive pre-market analysis with position impact warnings
                            # Pass prev_close from Saxo's PriceInfoDetails.LastClose
                            analysis = strategy.get_premarket_analysis(qqq_price, prev_close)
                            min_to_open = int(seconds_until_open / 60) if seconds_until_open > 0 else 0

                            # Build the pre-market message based on warning level
                            warning_prefix = ""
                            if analysis["warning_level"] == "CRITICAL":
                                warning_prefix = "🚨 CRITICAL "
                            elif analysis["warning_level"] == "WARNING":
                                warning_prefix = "⚠️ WARNING "
                            elif analysis["warning_level"] == "CAUTION":
                                warning_prefix = "⚡ CAUTION "

                            gap_type = "Weekend" if analysis["is_monday"] else "Overnight"

                            # Main status line with gap info
                            trade_logger.log_event(
                                f"{warning_prefix}PRE-MARKET | {analysis['message']} | "
                                f"{min_to_open} min to 9:30 AM"
                            )

                            # Log position impact warnings if any
                            if analysis["position_impacts"]:
                                for impact in analysis["position_impacts"]:
                                    trade_logger.log_event(f"  → {impact}")

                            # If warning or critical, log extra visibility with separator lines
                            if analysis["warning_level"] in ["WARNING", "CRITICAL"]:
                                trade_logger.log_event("=" * 60)
                                trade_logger.log_event(
                                    f"  {gap_type.upper()} GAP ALERT: "
                                    f"${abs(analysis['gap_points']):.2f} ({abs(analysis['gap_percent']):.2f}%) move"
                                )
                                trade_logger.log_event(
                                    f"  Previous close: ${analysis['prev_close']:.2f} → "
                                    f"Current: ${analysis['current_price']:.2f}"
                                )
                                trade_logger.log_event("=" * 60)
                        else:
                            trade_logger.log_event(f"PRE-MARKET | QQQ: No quote yet | Wake in {sleep_minutes} min")
                    except Exception as e:
                        logger.warning(f"Pre-market analysis error: {e}")
                        trade_logger.log_event(f"Market closed ({reason}). Sleeping {sleep_minutes} minutes...")
                elif not saxo_has_prices and not is_weekend(now) and not holiday_name:
                    # Before 7:00 AM on a trading day - Saxo has no prices yet
                    trade_logger.log_event(
                        f"HEARTBEAT | Pre-market not yet open (starts 7:00 AM ET) | Sleeping {sleep_minutes}m"
                    )
                else:
                    trade_logger.log_event(f"Market closed ({reason}). Sleeping {sleep_minutes} minutes...")

                # CONN-003: Disconnect WebSocket before sleeping to avoid timeout errors
                if client.is_streaming:
                    client.stop_price_streaming()

                # Force refresh token before sleeping to get fresh expiry
                client.authenticate(force_refresh=True)

                if not interruptible_sleep(sleep_seconds):
                    break  # Shutdown requested

                # Reset connection timeout after waking from sleep
                client.circuit_breaker.last_successful_connection = datetime.now()
            else:
                # FAST INTERVAL: Within 5 min of 9:30 AM, use fast checking to catch market open precisely
                seconds_until_930 = (market_open_time - now).total_seconds()
                if 0 < seconds_until_930 <= 300:
                    trade_logger.log_event(f"Pre-market: {int(seconds_until_930)}s until 9:30 AM - using fast {check_interval}s interval")
                    if not interruptible_sleep(check_interval):
                        break
                else:
                    # Market about to open or just after close
                    if not interruptible_sleep(30):
                        break

            continue

        # CONN-003: Reconnect WebSocket if disconnected during market hours
        if subscriptions and not client.is_streaming:
            trade_logger.log_event("WebSocket disconnected during market hours - reconnecting...")
            try:
                # Clean up any stale subscriptions first
                client.stop_price_streaming()
                import time
                time.sleep(1)  # Brief pause before reconnecting

                # Get fresh subscriptions (may have new option UICs)
                subscriptions = strategy.get_streaming_subscriptions()

                if client.start_price_streaming(subscriptions, price_update_handler):
                    trade_logger.log_event("WebSocket reconnected successfully")
                else:
                    trade_logger.log_event("Warning: WebSocket reconnection failed - using REST polling")
            except Exception as e:
                trade_logger.log_error(f"WebSocket reconnection error: {e}")

        # MKT-001: Check for overnight/weekend gaps at first market open check of the day
        if not market_open_gap_checked:
            try:
                strategy.check_premarket_gap()  # This logs the gap info
                market_open_gap_checked = True
            except Exception as e:
                trade_logger.log_error(f"Pre-market gap check error: {e}")
                market_open_gap_checked = True  # Don't keep retrying if it fails

        # Market is open - run strategy iteration
        try:
            strategy.run_iteration()
        except Exception as e:
            logger.error(f"Error in strategy iteration: {e}")
            trade_logger.log_error(f"Strategy error: {e}")

        # Google Sheets logging (every 15 minutes for dashboard)
        current_minute = now.hour * 60 + now.minute
        if last_dashboard_log is None or (current_minute - last_dashboard_log) >= 15:
            status = strategy.get_status_summary()

            # Log heartbeat message
            heartbeat_msg = (
                f"HEARTBEAT | State: {status['state']} | "
                f"QQQ: ${status['qqq_price']:.2f} | "
                f"Campaigns: {status['campaign_count']} | "
                f"Rolls: {status['roll_count']} | "
                f"P&L: ${status['total_pnl']:.2f}"
            )
            trade_logger.log_event(heartbeat_msg)

            # Log Account Summary (real-time position snapshot for Looker dashboard)
            strategy.log_account_summary()

            # Log Position snapshot if we have an active diagonal
            if status.get('position'):
                strategy.log_position_to_sheets()

            # Log Performance Metrics
            strategy.log_performance_metrics()

            last_dashboard_log = current_minute

        # Hourly Bot Logs to Google Sheets (avoid flooding with hundreds of rows)
        if (now - last_bot_log_time).total_seconds() >= bot_log_interval:
            try:
                status = strategy.get_status_summary()
                trade_logger.log_bot_activity(
                    level="INFO",
                    component="Strategy",
                    message=f"Hourly update: State={status['state']}, Campaigns={status['campaign_count']}, P&L=${status['total_pnl']:.2f}",
                    spy_price=status['qqq_price'],  # RPD uses QQQ
                    vix=0,  # RPD doesn't track VIX
                    flush=True
                )
                last_bot_log_time = now
            except Exception as e:
                trade_logger.log_error(f"Hourly bot log error: {e}")

        # Token refresh before long sleep
        if client.token_expiry:
            time_to_expiry = (client.token_expiry - datetime.now()).total_seconds()
            if time_to_expiry < 3600:  # Less than 1 hour
                logger.info("Token expiring soon - refreshing...")
                client.authenticate(force_refresh=True)

        # Sleep until next check
        if not interruptible_sleep(check_interval):
            break

    # Graceful shutdown
    trade_logger.log_event("=" * 60)
    trade_logger.log_event("BOT SHUTTING DOWN")
    trade_logger.log_event("=" * 60)

    # CONN-003: Stop WebSocket streaming before shutdown
    if client.is_streaming:
        client.stop_price_streaming()
        trade_logger.log_event("WebSocket streaming stopped")

    # Log final daily summary to Google Sheets
    strategy.log_daily_summary()
    trade_logger.log_event("Daily summary logged")

    # Save metrics
    strategy.metrics.save_to_file()
    trade_logger.log_event("Metrics saved")

    # Final status
    status = strategy.get_status_summary()
    trade_logger.log_event(f"Final State: {status['state']}")
    trade_logger.log_event(f"Total P&L: ${status['total_pnl']:.2f}")
    trade_logger.log_event(f"Campaigns: {status['campaign_count']}")
    trade_logger.log_event(f"Rolls: {status['roll_count']}")

    # Shutdown logger (flush buffers, stop background thread)
    trade_logger.shutdown()

    trade_logger.log_event("Shutdown complete")


def show_status(config: dict):
    """Show current bot and position status."""
    print("\n" + "=" * 60)
    print("ROLLING PUT DIAGONAL BOT STATUS")
    print("=" * 60)

    # Initialize client
    setup_logging(config, bot_name="ROLLING_PUT_DIAGONAL")
    client = SaxoClient(config)

    print("\nAuthenticating...")
    if not client.authenticate():
        print("Failed to authenticate")
        return

    print("Authenticated!\n")

    # Show market status
    print(get_market_status_message())
    print(get_event_status_message())
    print()

    # Get QQQ price
    quote = client.get_quote(config["strategy"]["underlying_uic"], asset_type="Etf")
    if quote and "Quote" in quote:
        price = quote["Quote"].get("Mid") or quote["Quote"].get("LastTraded", 0)
        print(f"QQQ Price: ${price:.2f}")

    # Show positions
    print("\n" + "-" * 40)
    print("POSITIONS")
    print("-" * 40)

    positions = client.get_positions()
    if positions:
        qqq_positions = [p for p in positions
                        if "QQQ" in p.get("DisplayAndFormat", {}).get("Symbol", "")]
        if qqq_positions:
            for pos in qqq_positions:
                symbol = pos.get("DisplayAndFormat", {}).get("Symbol", "Unknown")
                amount = pos.get("PositionBase", {}).get("Amount", 0)
                pnl = pos.get("PositionView", {}).get("ProfitLossOnTrade", 0)
                print(f"  {symbol}: {amount} contracts, P&L: ${pnl:.2f}")
        else:
            print("  No QQQ positions found")
    else:
        print("  No positions")

    # Show saved metrics
    print("\n" + "-" * 40)
    print("SAVED METRICS")
    print("-" * 40)

    from bots.rolling_put_diagonal.strategy import StrategyMetrics
    metrics = StrategyMetrics.load_from_file()
    if metrics:
        print(f"  Total P&L: ${metrics.total_pnl:.2f}")
        print(f"  Campaigns: {metrics.campaign_count}")
        print(f"  Total Rolls: {metrics.roll_count}")
        print(f"    Vertical: {metrics.vertical_rolls}")
        print(f"    Horizontal: {metrics.horizontal_rolls}")
        print(f"  Premium Collected: ${metrics.total_premium_collected:.2f}")
        print(f"  Win Rate: {metrics.win_rate:.1%}")
    else:
        print("  No saved metrics found")

    print("\n" + "=" * 60)


def main():
    """Main entry point."""
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="Rolling Put Diagonal Trading Bot"
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run in LIVE environment (real money!)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate without placing real trades"
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show current status and exit"
    )
    parser.add_argument(
        "--config",
        default="config/config.json",
        help="Path to configuration file"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Check interval in seconds (default: 60)"
    )

    args = parser.parse_args()

    # Print banner
    print_banner()

    # Determine config path
    bot_dir = os.path.dirname(os.path.abspath(__file__))
    if not os.path.isabs(args.config):
        config_path = os.path.join(bot_dir, args.config)
    else:
        config_path = args.config

    # Load configuration
    try:
        config = load_config(config_path)
    except FileNotFoundError:
        print(f"\nError: Config file not found: {config_path}")
        print("\nTo get started:")
        print("1. Copy config/config.example.json to config/config.json")
        print("2. Edit config.json with your Saxo API credentials")
        print("3. Run the bot again")
        sys.exit(1)
    except Exception as e:
        print(f"\nError loading config: {e}")
        sys.exit(1)

    # Override environment if --live flag is passed
    if args.live:
        config["saxo_api"]["environment"] = "live"
        print("\n*** LIVE TRADING MODE ***")
        print("Real money will be used!")
        print()

    # Validate configuration
    try:
        validate_config(config)
    except ValueError as e:
        print(f"\nConfiguration error: {e}")
        sys.exit(1)

    # Get dry_run from config if not specified on command line
    dry_run = args.dry_run or config.get("strategy", {}).get("dry_run", False)

    # Show environment info
    env = config["saxo_api"].get("environment", "sim")
    print(f"Environment: {env.upper()}")
    print(f"Dry Run: {dry_run}")
    print(f"Underlying: {config['strategy'].get('underlying_symbol', 'QQQ')}")
    print()

    # Kill any existing instances before starting
    kill_existing_bot_instances()

    # Status mode
    if args.status:
        show_status(config)
        return

    # Run the bot
    try:
        run_bot(config, dry_run=dry_run, check_interval=args.interval)
    except KeyboardInterrupt:
        print("\n\nBot stopped by user")
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        print(f"\nFatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
