#!/usr/bin/env python3
"""
main.py - Delta Neutral Trading Bot Entry Point

This is the main entry point for the Delta Neutral Trading Bot.
It orchestrates all components and runs the main trading loop.

Strategy Summary:
-----------------
1. Buy ATM Long Straddle (90-120 DTE) when VIX < 18
2. Sell weekly Short Strangles at 1.5-2x expected move
3. Recenter if SPY moves 5 points from initial strike
4. Roll weekly shorts on Friday
5. Exit when 30-60 DTE remains on Longs

Usage:
------
    python main.py                    # Run in SIM environment (paper trading)
    python main.py --live             # Run in LIVE environment (real money)
    python main.py --dry-run          # Simulate without placing orders
    python main.py --live --dry-run   # Test with live data, no order execution
    python main.py --status           # Show current status only
    python main.py --config my.json   # Use custom config file

Author: Trading Bot Developer
Date: 2024
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

# Ensure project root is in path for imports when running as script
# This allows both `python bots/delta_neutral/main.py` and `python -m bots.delta_neutral.main` to work
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# Import shared modules
from shared.saxo_client import SaxoClient
from shared.logger_service import TradeLoggerService, setup_logging
from shared.market_hours import is_market_open, get_market_status_message, calculate_sleep_duration, is_weekend, is_market_holiday, get_us_market_time, get_holiday_name
from shared.config_loader import ConfigLoader, get_config_loader
from shared.secret_manager import is_running_on_gcp

# Import bot-specific strategy
from bots.delta_neutral.strategy import DeltaNeutralStrategy, StrategyState

# Configure main logger
logger = logging.getLogger(__name__)

# Global flag for graceful shutdown
shutdown_requested = False


def signal_handler(signum, frame):
    """
    Handle shutdown signals (CTRL+C, SIGTERM).

    Sets the global shutdown flag to gracefully exit the main loop.
    """
    global shutdown_requested
    logger.info(f"\nShutdown signal received ({signum}). Initiating graceful shutdown...")
    shutdown_requested = True


def interruptible_sleep(seconds: int, check_interval: int = 5) -> bool:
    """
    Sleep for the specified duration, but check for shutdown signal periodically.

    Args:
        seconds: Total seconds to sleep
        check_interval: How often to check for shutdown (default 5 seconds)

    Returns:
        bool: True if sleep completed, False if interrupted by shutdown
    """
    remaining = seconds
    while remaining > 0 and not shutdown_requested:
        time.sleep(min(check_interval, remaining))
        remaining -= check_interval
    return not shutdown_requested


def kill_existing_bot_instances() -> int:
    """
    Find and kill any existing bot instances before starting a new one.

    This prevents multiple bot instances from running simultaneously,
    which could cause duplicate trades and circuit breaker issues.

    Returns:
        int: Number of processes killed
    """
    current_pid = os.getpid()
    killed_count = 0

    try:
        # Find all Python processes running main.py
        # Use broader pattern to catch python, python3, and full paths
        result = subprocess.run(
            ["pgrep", "-f", "main.py"],
            capture_output=True,
            text=True
        )

        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split('\n')

            for pid_str in pids:
                try:
                    pid = int(pid_str.strip())
                    # Don't kill ourselves
                    if pid != current_pid:
                        logger.info(f"Found existing bot instance (PID: {pid}), terminating...")
                        os.kill(pid, signal.SIGTERM)
                        killed_count += 1
                        # Give it a moment to shut down gracefully
                        time.sleep(1)
                except (ValueError, ProcessLookupError):
                    pass  # Process already terminated or invalid PID

        if killed_count > 0:
            logger.info(f"Terminated {killed_count} existing bot instance(s)")
            # Extra wait for graceful cleanup
            time.sleep(2)

    except FileNotFoundError:
        # pgrep not available (Windows or unusual system)
        logger.warning("pgrep not available - cannot check for existing instances")
    except Exception as e:
        logger.warning(f"Error checking for existing instances: {e}")

    return killed_count


def load_config(config_path: str = "config/config.json") -> dict:
    """
    Load configuration from appropriate source (cloud or local).

    On GCP: Loads from Secret Manager (always LIVE environment)
    Locally: Loads from config.json (supports SIM or LIVE via --live flag)

    Args:
        config_path: Path to local configuration file (used in local mode only)

    Returns:
        dict: Configuration dictionary

    Raises:
        FileNotFoundError: If local config file doesn't exist (local mode)
        ValueError: If required secrets not found (cloud mode)
    """
    # Use smart config loader that auto-detects environment
    loader = ConfigLoader(config_path)
    config = loader.load_config()

    return config


def validate_config(config: dict) -> bool:
    """
    Validate that required configuration values are present.

    Args:
        config: Configuration dictionary

    Returns:
        bool: True if valid, raises ValueError otherwise
    """
    # Check account keys (supports both old and new structure)
    if "account" not in config:
        raise ValueError("Missing config section: account")

    # Get the environment to validate the correct account keys
    environment = config.get("saxo_api", {}).get("environment", "sim")
    account_config = config["account"]

    # Check for new structure (account.sim / account.live) or legacy (account.account_key)
    if environment in account_config and isinstance(account_config[environment], dict):
        # New structure
        env_account = account_config[environment]
        if "account_key" not in env_account:
            raise ValueError(f"Missing config key: account.{environment}.account_key")
        if "client_key" not in env_account:
            raise ValueError(f"Missing config key: account.{environment}.client_key")
    else:
        # Legacy structure
        if "account_key" not in account_config:
            raise ValueError("Missing config key: account.account_key")
        if "client_key" not in account_config:
            raise ValueError("Missing config key: account.client_key")

    # Check saxo_api section exists
    if "saxo_api" not in config:
        raise ValueError("Missing config section: saxo_api")

    # Validate environment-specific credentials
    environment = config["saxo_api"].get("environment", "sim")
    if environment not in config["saxo_api"]:
        raise ValueError(f"Missing config section: saxo_api.{environment}")

    env_config = config["saxo_api"][environment]
    if "app_key" not in env_config or not env_config["app_key"]:
        raise ValueError(f"Missing app_key for {environment} environment")
    if "app_secret" not in env_config or not env_config["app_secret"]:
        raise ValueError(f"Missing app_secret for {environment} environment")

    return True


def print_banner():
    """Print the application banner."""
    banner = """
    ╔═══════════════════════════════════════════════════════════════╗
    ║                                                               ║
    ║         DELTA NEUTRAL TRADING BOT                             ║
    ║         ═══════════════════════════                           ║
    ║                                                               ║
    ║         Strategy: SPY Long Straddle + Weekly Short Strangles  ║
    ║         5-Point Recentering Rule                              ║
    ║                                                               ║
    ║         Version: 1.0.0                                        ║
    ║         API: Saxo Bank OpenAPI                                ║
    ║                                                               ║
    ╚═══════════════════════════════════════════════════════════════╝
    """
    print(banner)


def list_accounts(config: dict):
    """
    List all available accounts for the authenticated user.

    Args:
        config: Configuration dictionary
    """
    # Initialize logging (required for SaxoClient)
    setup_logging(config, bot_name="DELTA_NEUTRAL")

    # Initialize Saxo client
    client = SaxoClient(config)

    # Authenticate with Saxo API
    print("\nAuthenticating...")
    if not client.authenticate():
        print("❌ Failed to authenticate. Please check your credentials.")
        return

    print("✅ Authentication successful!\n")

    # Get list of accounts
    print("Fetching accounts...\n")
    accounts = client.get_accounts()

    if not accounts:
        print("❌ No accounts found or failed to fetch accounts.")
        return

    print("=" * 80)
    print("AVAILABLE ACCOUNTS")
    print("=" * 80)

    for idx, account in enumerate(accounts, 1):
        print(f"\n{idx}. Account Key: {account.get('AccountKey')}")
        print(f"   Account Type: {account.get('AccountType')}")
        print(f"   Currency: {account.get('Currency')}")
        print(f"   Account ID: {account.get('AccountId')}")

        # Show if this is the currently configured account
        env = config["saxo_api"].get("environment", "sim")
        account_cfg = config["account"]
        if env in account_cfg and isinstance(account_cfg[env], dict):
            configured_key = account_cfg[env].get("account_key")
        else:
            configured_key = account_cfg.get("account_key")
        if account.get('AccountKey') == configured_key:
            print(f"   ⭐ CURRENTLY CONFIGURED")

        # Show account balance if available
        client_temp = SaxoClient(config)
        client_temp.access_token = client.access_token
        client_temp.account_key = account.get('AccountKey')
        client_temp.client_key = account.get('ClientKey', account.get('AccountKey'))

        balance = client_temp.get_balance()
        if balance:
            total = balance.get('TotalValue', 0)
            currency = balance.get('Currency', 'USD')
            print(f"   Balance: {total:,.2f} {currency}")

    print("\n" + "=" * 80)
    print("\nTo use a specific account, run:")
    print(f"  python main.py --account <ACCOUNT_KEY>")
    print(f"\nExample:")
    print(f"  python main.py --live --account {accounts[0].get('AccountKey')}")
    print("=" * 80 + "\n")


def run_bot(config: dict, dry_run: bool = False, check_interval: int = 30):
    """
    Run the main trading bot loop.

    Args:
        config: Configuration dictionary
        dry_run: If True, simulate without placing real trades
        check_interval: Seconds between strategy checks
    """
    global shutdown_requested

    # Initialize logging service
    trade_logger = setup_logging(config, bot_name="DELTA_NEUTRAL")
    trade_logger.log_event("=" * 60)
    trade_logger.log_event("TRADING BOT STARTING")
    trade_logger.log_event(f"Mode: {'DRY RUN (Simulation)' if dry_run else 'LIVE TRADING'}")
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

    # Initialize strategy (pass dry_run flag)
    strategy = DeltaNeutralStrategy(client, config, trade_logger, dry_run=dry_run)

    # CRITICAL: Attempt to recover existing positions on startup
    # This handles bot restarts, GCP VM reboots, and crash recovery
    trade_logger.log_event("Checking for existing positions to recover...")
    positions_recovered = strategy.recover_positions()
    if positions_recovered:
        trade_logger.log_event(f"Position recovery complete - resuming with state: {strategy.state.value}")
    else:
        trade_logger.log_event("No existing positions found - starting fresh")

    # Sync Positions sheet with actual state (clears stale data, adds current positions)
    strategy.sync_positions_sheet()

    # Log dashboard metrics on startup (always update on restart for fresh data)
    try:
        trade_logger.log_event("Logging dashboard metrics on startup...")

        # Update market data first to ensure we have current prices
        strategy.update_market_data()

        # Refresh position prices from Saxo (in case they weren't populated during recovery)
        strategy.refresh_position_prices()

        # Use safe metrics that correct for stale data when market is closed
        dashboard_metrics = strategy.get_dashboard_metrics_safe()
        environment = "SIM" if client.is_simulation else "LIVE"

        # Log to Account Summary worksheet
        trade_logger.log_account_summary(
            strategy_data=dashboard_metrics,
            saxo_client=client,
            environment=environment
        )

        # Log initial performance metrics
        trade_logger.log_performance_metrics(
            period="Startup",
            metrics=dashboard_metrics,
            saxo_client=client
        )

        # Log bot startup activity
        trade_logger.log_bot_activity(
            level="INFO",
            component="Main",
            message=f"Bot started - State: {strategy.state.value}, Positions: {dashboard_metrics['position_count']}",
            spy_price=dashboard_metrics['spy_price'],
            vix=dashboard_metrics['vix'],
            flush=True
        )

        trade_logger.log_event("Dashboard metrics logged to Google Sheets")
    except Exception as e:
        trade_logger.log_error(f"Failed to log dashboard metrics: {e}")

    # Start real-time price streaming
    # FIXED: Define full subscription details with correct AssetTypes to avoid 404 errors
    subscriptions = [
        {"uic": config["strategy"]["underlying_uic"], "asset_type": "Etf"},
        {"uic": config["strategy"]["vix_uic"], "asset_type": "StockIndex"} # Keep as StockIndex
    ]

    # Also subscribe to existing position option UICs for real-time quotes
    # This ensures close operations have streaming data available
    if strategy.long_straddle:
        if strategy.long_straddle.call and strategy.long_straddle.call.uic:
            subscriptions.append({"uic": strategy.long_straddle.call.uic, "asset_type": "StockOption"})
        if strategy.long_straddle.put and strategy.long_straddle.put.uic:
            subscriptions.append({"uic": strategy.long_straddle.put.uic, "asset_type": "StockOption"})

    if strategy.short_strangle:
        if strategy.short_strangle.call and strategy.short_strangle.call.uic:
            subscriptions.append({"uic": strategy.short_strangle.call.uic, "asset_type": "StockOption"})
        if strategy.short_strangle.put and strategy.short_strangle.put.uic:
            subscriptions.append({"uic": strategy.short_strangle.put.uic, "asset_type": "StockOption"})

    trade_logger.log_event(f"Starting price streaming for {len(subscriptions)} instruments...")

    def price_update_handler(uic: int, data: dict):
        """Handle real-time price updates."""
        strategy.handle_price_update(uic, data)

    # FIXED: Pass the list of dicts to the new start_price_streaming method
    streaming_started = client.start_price_streaming(subscriptions, price_update_handler)
    if not streaming_started:
        trade_logger.log_event("Warning: Real-time streaming not started. Using polling mode.")

    # Main trading loop
    trade_logger.log_event("Entering main trading loop...")
    trade_logger.log_event(f"Press Ctrl+C to stop the bot gracefully")
    trade_logger.log_event("-" * 60)

    last_status_time = datetime.now()
    status_interval = 300  # Log status every 5 minutes
    last_daily_summary_date = None  # Track last daily summary logged (trading days only)
    last_performance_metrics_date = None  # Track last performance metrics logged (every day)
    trading_day_started = False  # Track if we've started tracking for today
    last_dashboard_log_time = datetime.now()
    dashboard_log_interval = 900  # Log dashboard metrics every 15 minutes
    last_bot_log_time = datetime.now()
    bot_log_interval = 3600  # Log to Google Sheets Bot Logs every hour (3600 seconds)
    last_position_sync_time = datetime.now()
    position_sync_interval = 600  # Sync positions with Saxo every 10 minutes

    try:
        while not shutdown_requested:
            try:
                # Check if market is open
                if not is_market_open():
                    # Daily Summary & Performance Metrics: Update EVERY day (including weekends)
                    # Theta decays every calendar day, so we log daily using last known theta
                    # This ensures Cumulative Net Theta accurately tracks all days
                    market_time = get_us_market_time()
                    today = market_time.strftime("%Y-%m-%d")  # Use ET date, not UTC
                    is_after_close = market_time.hour >= 16  # 4 PM ET or later
                    is_trading_day = not is_weekend() and not is_market_holiday()

                    # Determine day type for logging
                    holiday_name = get_holiday_name()
                    if is_weekend():
                        day_type = "weekend"
                    elif holiday_name:
                        day_type = f"holiday - {holiday_name}"
                    else:
                        day_type = "trading day"

                    # Log Daily Summary every day (uses last known Net Theta on weekends/holidays)
                    if last_daily_summary_date != today and is_after_close:
                        trade_logger.log_event(f"Logging daily summary ({day_type})...")
                        strategy.log_daily_summary()
                        last_daily_summary_date = today
                        if is_trading_day:
                            trading_day_started = False

                    # Log Performance Metrics every day
                    if last_performance_metrics_date != today and is_after_close:
                        # Use safe metrics that correct for stale data when market is closed
                        dashboard_metrics = strategy.get_dashboard_metrics_safe()
                        period = "End of Day" if is_trading_day else day_type.title()
                        trade_logger.log_performance_metrics(
                            period=period,
                            metrics=dashboard_metrics,
                            saxo_client=client
                        )
                        last_performance_metrics_date = today
                        trade_logger.log_event(f"Performance metrics updated ({period})")

                    market_status = get_market_status_message()
                    trade_logger.log_event(market_status)

                    # Calculate intelligent sleep duration
                    # Max 15 minutes to ensure token stays alive (Saxo tokens expire in 20 min)
                    sleep_time = calculate_sleep_duration(max_sleep=900)

                    if sleep_time > 0:
                        minutes = sleep_time // 60

                        # Disconnect WebSocket before sleeping to avoid timeout errors
                        # Saxo closes idle connections anyway, so disconnect cleanly
                        if client.is_streaming:
                            client.stop_price_streaming()

                        # Force refresh token BEFORE sleeping to get fresh expiry
                        # This ensures we have a full token lifetime after waking up
                        # force_refresh=True ensures we refresh even if current token is still valid
                        client.authenticate(force_refresh=True)

                        # Log heartbeat LAST, right before sleeping
                        reason = f"({holiday_name})" if holiday_name else "(weekend)" if is_weekend() else ""
                        trade_logger.log_event(
                            f"HEARTBEAT | Market closed {reason} - sleeping for {minutes}m"
                        )

                        if not interruptible_sleep(sleep_time):
                            break  # Shutdown requested

                        # Reset connection timeout after waking from sleep
                        # This prevents false circuit breaker triggers from long sleep periods
                        client.circuit_breaker.last_successful_connection = datetime.now()

                        # Reconnect WebSocket after waking (will reconnect when market opens)
                        if not shutdown_requested and not client.is_streaming:
                            logger.debug("Reconnecting WebSocket after sleep")
                            client.start_price_streaming(subscriptions, price_update_handler)
                    else:
                        reason = f"({holiday_name})" if holiday_name else "(weekend)" if is_weekend() else ""
                        trade_logger.log_event(f"HEARTBEAT | Market closed {reason} - rechecking in 60s")
                        if not interruptible_sleep(60):
                            break  # Shutdown requested
                        # Reset connection timeout after any sleep to prevent false triggers
                        client.circuit_breaker.last_successful_connection = datetime.now()
                    continue

                # Check circuit breaker
                if client.is_circuit_open():
                    cooldown_remaining = ""
                    if client.circuit_breaker.cooldown_until:
                        remaining_secs = (client.circuit_breaker.cooldown_until - datetime.now()).total_seconds()
                        if remaining_secs > 0:
                            cooldown_remaining = f" (~{int(remaining_secs)}s remaining)"
                    trade_logger.log_event(f"HEARTBEAT | Circuit breaker OPEN - waiting for cooldown{cooldown_remaining}")
                    if not interruptible_sleep(check_interval):
                        break  # Shutdown requested
                    continue

                # Check connection timeout
                # But first, reset the timestamp if we're about to do a successful iteration
                # This prevents false triggers when WebSocket is quiet but bot is healthy
                client.circuit_breaker.last_successful_connection = datetime.now()

                if client.check_connection_timeout():
                    trade_logger.log_error("Connection timeout detected - circuit breaker activated")
                    if not interruptible_sleep(check_interval):
                        break  # Shutdown requested
                    continue

                # Start daily tracking if this is first check of the trading day
                if not trading_day_started:
                    strategy.start_new_trading_day()
                    trading_day_started = True
                    trade_logger.log_event("Daily tracking initialized")

                # Update intraday tracking (SPY high/low, VIX high, etc.)
                strategy.update_intraday_tracking()

                # Run strategy check (works in both live and dry-run)
                action = strategy.run_strategy_check()

                if dry_run:
                    # In dry-run, prefix all actions with [DRY RUN]
                    status = strategy.get_status_summary()
                    trade_logger.log_event(
                        f"[DRY RUN] SPY: ${status['underlying_price']:.2f} | "
                        f"VIX: {status['vix']:.2f} | State: {status['state']}"
                    )
                    if action != "No action":
                        trade_logger.log_event(f"[DRY RUN] ACTION: {action}")
                else:
                    # Live mode
                    if action != "No action":
                        trade_logger.log_event(f"ACTION: {action}")

                # Periodic status logging
                now = datetime.now()
                if (now - last_status_time).total_seconds() >= status_interval:
                    status = strategy.get_status_summary()
                    trade_logger.log_status(status)
                    last_status_time = now

                # Periodic dashboard logging (every 15 min for Looker Studio)
                if (now - last_dashboard_log_time).total_seconds() >= dashboard_log_interval:
                    try:
                        # Refresh position prices before logging
                        strategy.refresh_position_prices()
                        # Use safe metrics that correct for stale data when market is closed
                        dashboard_metrics = strategy.get_dashboard_metrics_safe()
                        environment = "SIM" if client.is_simulation else "LIVE"

                        # Log to Account Summary worksheet
                        trade_logger.log_account_summary(
                            strategy_data=dashboard_metrics,
                            saxo_client=client,
                            environment=environment
                        )

                        # Log to Performance Metrics worksheet
                        trade_logger.log_performance_metrics(
                            period="15-min",
                            metrics=dashboard_metrics,
                            saxo_client=client
                        )

                        # Update Positions sheet with current prices/P&L
                        positions = strategy.get_current_positions_for_sync()
                        if positions:
                            trade_logger.log_position_snapshot(positions)

                        last_dashboard_log_time = now
                    except Exception as e:
                        trade_logger.log_error(f"Dashboard logging error: {e}")

                # Periodic position sync with Saxo (every 10 minutes)
                # This ensures local state stays in sync with actual Saxo positions
                # Catches any discrepancies from partial fills, manual trades, etc.
                if (now - last_position_sync_time).total_seconds() >= position_sync_interval:
                    try:
                        trade_logger.log_event("Periodic position sync with Saxo...")
                        strategy.recover_positions()
                        last_position_sync_time = now
                    except Exception as e:
                        trade_logger.log_error(f"Position sync error: {e}")

                # Hourly Bot Logs to Google Sheets (avoid flooding with hundreds of rows)
                if (now - last_bot_log_time).total_seconds() >= bot_log_interval:
                    try:
                        # Use safe metrics for accurate P&L when market is closed
                        dashboard_metrics = strategy.get_dashboard_metrics_safe()
                        trade_logger.log_bot_activity(
                            level="INFO",
                            component="Strategy",
                            message=f"Hourly update: Delta={dashboard_metrics['total_delta']:.4f}, P&L=${dashboard_metrics['total_pnl']:.2f}",
                            spy_price=dashboard_metrics['spy_price'],
                            vix=dashboard_metrics['vix'],
                            flush=True
                        )
                        last_bot_log_time = now
                    except Exception as e:
                        trade_logger.log_error(f"Hourly bot log error: {e}")

                # Log bot heartbeat - this is the last message before sleeping
                # Shows bot is alive and what state it's in
                status = strategy.get_status_summary()
                mode_prefix = "[DRY RUN] " if dry_run else ""

                # CRITICAL: If action indicates a failure that needs immediate retry,
                # use a much shorter interval to avoid leaving positions in bad state
                needs_immediate_retry = (
                    "FAILED" in action.upper() or
                    "failed" in action.lower() or
                    "SLIPPAGE" in action.upper() or
                    "TIMEOUT" in action.upper() or
                    strategy.has_pending_retry()
                )

                if needs_immediate_retry:
                    # Use 5-second interval for quick retry
                    retry_interval = 5
                    trade_logger.log_event(
                        f"{mode_prefix}⚡ FAST RETRY | State: {status['state']} | "
                        f"SPY: ${status['underlying_price']:.2f} | VIX: {status['vix']:.2f} | "
                        f"Next check in {retry_interval}s (quick retry mode)"
                    )
                    if not interruptible_sleep(retry_interval):
                        break  # Shutdown requested
                else:
                    trade_logger.log_event(
                        f"{mode_prefix}HEARTBEAT | State: {status['state']} | "
                        f"SPY: ${status['underlying_price']:.2f} | VIX: {status['vix']:.2f} | "
                        f"Next check in {check_interval}s"
                    )

                    # Sleep until next check (interruptible for fast shutdown)
                    if not interruptible_sleep(check_interval):
                        break  # Shutdown requested

            except KeyboardInterrupt:
                # This should be caught by signal handler, but just in case
                shutdown_requested = True
                break

            except Exception as e:
                trade_logger.log_error(f"Error in main loop: {e}", exception=e)
                # Continue running unless it's a critical error
                if not interruptible_sleep(check_interval):
                    break  # Shutdown requested

    finally:
        # Graceful shutdown
        trade_logger.log_event("=" * 60)
        trade_logger.log_event("INITIATING GRACEFUL SHUTDOWN")
        trade_logger.log_event("=" * 60)

        # Stop price streaming
        trade_logger.log_event("Stopping price streaming...")
        client.stop_price_streaming()

        # Log final status
        status = strategy.get_status_summary()
        trade_logger.log_status(status)

        # Note: We don't auto-close positions on shutdown
        # This allows the user to restart the bot without losing positions
        # Position recovery will automatically restore state on next startup
        if strategy.state != StrategyState.IDLE:
            trade_logger.log_event(
                "NOTE: Bot shutting down with active positions. "
                "Positions will remain open on Saxo. On next startup, the bot will "
                "automatically recover and resume managing these positions."
            )

        # Shutdown logger
        trade_logger.shutdown()

        trade_logger.log_event("Shutdown complete.")


def show_status(config: dict):
    """
    Show current status without entering trading loop.

    Args:
        config: Configuration dictionary
    """
    # Initialize logging
    trade_logger = setup_logging(config, bot_name="DELTA_NEUTRAL")

    # Initialize client
    client = SaxoClient(config)

    if not client.authenticate():
        print("Failed to authenticate. Please check your credentials.")
        return

    # Initialize strategy (without trading)
    strategy = DeltaNeutralStrategy(client, config, trade_logger)

    # Update market data
    strategy.update_market_data()

    # Get and display status
    status = strategy.get_status_summary()

    print("\n" + "=" * 60)
    print("CURRENT STATUS")
    print("=" * 60)
    print(f"  State: {status['state']}")
    print(f"  SPY Price: ${status['underlying_price']:.2f}")
    print(f"  VIX: {status['vix']:.2f}")
    print(f"  VIX Entry Threshold: < {config['strategy']['max_vix_entry']}")
    print(f"  Can Enter Trade: {'Yes' if status['vix'] < config['strategy']['max_vix_entry'] else 'No'}")
    print("=" * 60)

    # Get positions
    positions = client.get_positions()
    if positions:
        print(f"\nOpen Positions: {len(positions)}")
        for pos in positions:
            print(f"  - {pos.get('DisplayAndFormat', {}).get('Symbol', 'Unknown')}: "
                  f"{pos.get('PositionBase', {}).get('Amount', 0)} @ "
                  f"${pos.get('PositionView', {}).get('CurrentPrice', 0):.2f}")
    else:
        print("\nNo open positions")

    print()


def main():
    """Main entry point."""
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="Delta Neutral Trading Bot - SPY Options Strategy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                     Run in SIM environment (paper trading)
  python main.py --live              Run in LIVE environment (real money)
  python main.py --dry-run           Simulate without placing orders
  python main.py --live --dry-run    Test with live data, no orders
  python main.py --status            Show current status only
  python main.py --status --live     Show status on live account
  python main.py --config prod.json  Use custom config file
  python main.py --interval 30       Check every 30 seconds
        """
    )

    parser.add_argument(
        "--config", "-c",
        default="bots/delta_neutral/config/config.json",
        help="Path to configuration file (default: bots/delta_neutral/config/config.json)"
    )

    parser.add_argument(
        "--dry-run", "-d",
        action="store_true",
        help="Run in simulation mode without placing real trades"
    )

    parser.add_argument(
        "--status", "-s",
        action="store_true",
        help="Show current status and exit"
    )

    parser.add_argument(
        "--interval", "-i",
        type=int,
        default=60,
        help="Strategy check interval in seconds (default: 60)"
    )

    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging (DEBUG level)"
    )

    parser.add_argument(
        "--live", "-l",
        action="store_true",
        help="Use LIVE environment (real money trading) instead of SIM"
    )

    parser.add_argument(
        "--account",
        type=str,
        help="Account key to use (overrides config.json). Use --list-accounts to see available accounts."
    )

    parser.add_argument(
        "--list-accounts",
        action="store_true",
        help="List all available accounts and exit"
    )

    args = parser.parse_args()

    # Print banner
    print_banner()

    # Kill any existing bot instances before starting
    # This prevents duplicate trades and circuit breaker issues from zombie processes
    killed = kill_existing_bot_instances()
    if killed > 0:
        print(f"  Terminated {killed} existing bot instance(s)\n")

    try:
        # Load configuration (auto-detects cloud vs local)
        config = load_config(args.config)

        # Show environment info
        running_on_cloud = is_running_on_gcp()
        if running_on_cloud:
            # Cloud always uses LIVE
            print("\n" + "=" * 60)
            print("  RUNNING ON GOOGLE CLOUD PLATFORM")
            print("  Environment: LIVE (Cloud deployment)")
            print("  Credentials: Loaded from Secret Manager")
            print("=" * 60 + "\n")
        else:
            # Local mode - respect --live flag
            if args.live:
                config["saxo_api"]["environment"] = "live"
                if args.dry_run:
                    print("\n" + "=" * 60)
                    print("  DRY RUN MODE - LIVE DATA, NO REAL ORDERS")
                    print("  Using LIVE market data for realistic simulation")
                    print("  All trades will be SIMULATED (logged but not executed)")
                    print("=" * 60 + "\n")
                else:
                    print("\n⚠️  WARNING: LIVE ENVIRONMENT ENABLED - REAL MONEY TRADING ⚠️\n")
            else:
                env_name = config['saxo_api'].get('environment', 'sim').upper()
                if args.dry_run:
                    print(f"\n  Environment: {env_name} (DRY RUN - No real orders)\n")
                else:
                    print(f"\n  Environment: {env_name}\n")

        # Override log level if verbose
        if args.verbose:
            config["logging"]["log_level"] = "DEBUG"

        # Override account if specified (handles both old and new config structure)
        if args.account:
            env = config["saxo_api"].get("environment", "sim")
            if env in config["account"] and isinstance(config["account"][env], dict):
                config["account"][env]["account_key"] = args.account
                config["account"][env]["client_key"] = args.account
            else:
                config["account"]["account_key"] = args.account
                config["account"]["client_key"] = args.account
            print(f"Using account: {args.account[:8]}...")

        # Validate configuration
        validate_config(config)

        # Handle list-accounts mode
        if args.list_accounts:
            list_accounts(config)
            return

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Execute requested mode
        if args.status:
            show_status(config)
        else:
            run_bot(
                config=config,
                dry_run=args.dry_run,
                check_interval=args.interval
            )

    except FileNotFoundError as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)

    except ValueError as e:
        print(f"\n❌ Configuration Error: {e}")
        sys.exit(1)

    except Exception as e:
        print(f"\n❌ Unexpected Error: {e}")
        logger.exception("Unexpected error in main()")
        sys.exit(1)


if __name__ == "__main__":
    main()
