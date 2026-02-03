#!/usr/bin/env python3
"""
main.py - MEIC (Multiple Entry Iron Condors) Trading Bot Entry Point

This is the main entry point for the MEIC Trading Bot implementing
Tammy Chambless's strategy (Queen of 0DTE).

Strategy Summary:
-----------------
1. Enter 6 iron condors throughout the day (10:00, 10:30, 11:00, 11:30, 12:00, 12:30 AM ET)
2. Each IC: OTM call spread + OTM put spread at 5-15 delta
3. Stop loss per side = total credit received
4. MEIC+ modification: stop = credit - $0.10 for small wins on stop days
5. Expected results: 20.7% CAGR, 4.31% max drawdown

Usage:
------
    python -m bots.meic.main              # Run in SIM environment
    python -m bots.meic.main --live       # Run in LIVE environment
    python -m bots.meic.main --dry-run    # Simulate without orders
    python -m bots.meic.main --status     # Show current status only

Author: Trading Bot Developer
Date: 2026-01-27

Changelog:
----------
1.2.0 (2026-02-02): VIX-adjusted strikes + Code audit fixes
    - Strike distance now scales with VIX for consistent delta targeting
    - Higher VIX = wider strikes (maintains ~8 delta probability)
    - Uses config target_delta value (min_delta, max_delta for validation)
    - OTM distance range expanded from 35-65 to 25-120 points
    - BUGFIX: Fixed send_alert() calls with wrong argument order (would crash)
    - BUGFIX: Added credit validation (min_credit_per_side, max_credit_per_side now used)
    - BUGFIX: Made MEIC+ $1.50 threshold configurable (meic_plus_min_credit)
    - CLEANUP: Removed dead code (models/ package, unused functions)
    - CLEANUP: Removed unused EARLY_CLOSE_ENTRY_TIMES constant

1.1.0 (2026-02-01): REST-only mode + Safety features
    - Disabled WebSocket streaming, use REST API for all price fetching
    - Added USE_WEBSOCKET_STREAMING toggle (default: False)
    - Added Order Size Validation (ORDER-006)
    - Added Emergency Close Max Retries
    - Added Spread Validation Before Emergency Close
    - Added Fill Price Slippage Monitoring (ORDER-007)
    - Added Duplicate Bot Prevention

1.0.0 (2026-01-27): Initial implementation
    - 6 scheduled iron condor entries per day
    - Position Registry integration for multi-bot support
    - Circuit breaker and critical intervention safety

See docs/MEIC_STRATEGY_SPECIFICATION.md for full details.
See docs/MEIC_EDGE_CASES.md for edge case analysis.
"""

import os
import sys
import time
import signal
import argparse
import logging
import subprocess
from datetime import datetime

# Ensure project root is in path for imports when running as script
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# Import shared modules
from shared.saxo_client import SaxoClient
from shared.logger_service import setup_logging
from shared.market_hours import (
    is_market_open, get_market_status_message, calculate_sleep_duration,
    get_holiday_name, get_us_market_time, is_after_hours, is_weekend
)
from shared.config_loader import ConfigLoader
from shared.secret_manager import is_running_on_gcp

# Import bot-specific strategy
from bots.meic.strategy import MEICStrategy

# Configure main logger
logger = logging.getLogger(__name__)

# =============================================================================
# WEBSOCKET STREAMING TOGGLE
# =============================================================================
# Set to False for REST-only mode (more reliable, recommended)
# Set to True to enable WebSocket streaming (only if needed for rate limits)
#
# REST-only mode (USE_WEBSOCKET_STREAMING = False):
#   - All price fetching uses REST API directly
#   - NORMAL mode: ~12 calls/min per bot (5s interval)
#   - VIGILANT mode: ~30 calls/min per bot (2s interval)
#   - Guaranteed fresh prices, no stale cache issues
#
# WebSocket mode (USE_WEBSOCKET_STREAMING = True):
#   - Price fetching checks WebSocket cache first, REST fallback
#   - 0 API calls for cached prices
#   - Risk: Stale cache can cause incorrect stop monitoring
#
# History: Set to False after Delta Neutral WebSocket issues on 2026-01-27.
# WebSocket streaming was buggy and unreliable. REST-only is simpler and
# sufficient for our bot setup.
# =============================================================================
USE_WEBSOCKET_STREAMING = False  # REST-only mode for reliability

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


def interruptible_sleep(seconds: int, check_interval: int = 1) -> bool:
    """
    Sleep for the specified duration, but check for shutdown signal periodically.

    Args:
        seconds: Total seconds to sleep
        check_interval: How often to check for shutdown (default 1 second)

    Returns:
        bool: True if sleep completed, False if interrupted by shutdown
    """
    remaining = seconds
    while remaining > 0 and not shutdown_requested:
        time.sleep(min(check_interval, remaining))
        remaining -= check_interval
    return not shutdown_requested


def kill_existing_meic_instances() -> int:
    """
    DUPLICATE-001: Find and kill any existing MEIC bot instances before starting a new one.

    This prevents multiple MEIC bot instances from running simultaneously,
    which could cause duplicate trades, position conflicts, and circuit breaker issues.

    Uses a specific pattern to match only MEIC bot processes (not Delta Neutral
    or other bots).

    Returns:
        int: Number of processes killed
    """
    current_pid = os.getpid()
    killed_count = 0

    try:
        # Find all Python processes running meic/main.py specifically
        # This pattern is more specific than just "main.py" to avoid killing other bots
        result = subprocess.run(
            ["pgrep", "-f", "meic[./]main\\.py"],
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
                        logger.info(f"DUPLICATE-001: Found existing MEIC instance (PID: {pid}), terminating...")
                        os.kill(pid, signal.SIGTERM)
                        killed_count += 1
                        # Give it a moment to shut down gracefully
                        time.sleep(1)
                except (ValueError, ProcessLookupError):
                    pass  # Process already terminated or invalid PID

        if killed_count > 0:
            logger.info(f"DUPLICATE-001: Terminated {killed_count} existing MEIC instance(s)")
            # Extra wait for graceful cleanup
            time.sleep(2)

    except FileNotFoundError:
        # pgrep not available (Windows or unusual system)
        logger.warning("DUPLICATE-001: pgrep not available - cannot check for existing instances")
    except Exception as e:
        logger.warning(f"DUPLICATE-001: Error checking for existing instances: {e}")

    return killed_count


def load_config(config_path: str = "bots/meic/config/config.json") -> dict:
    """
    Load configuration from appropriate source (cloud or local).

    Args:
        config_path: Path to local configuration file

    Returns:
        dict: Configuration dictionary
    """
    loader = ConfigLoader(config_path)
    config = loader.load_config()
    return config


def print_banner():
    """Print the application banner."""
    banner = """
    ╔═══════════════════════════════════════════════════════════════╗
    ║                                                               ║
    ║         MEIC 0DTE TRADING BOT                                 ║
    ║         ═════════════════════                                 ║
    ║                                                               ║
    ║         Strategy: Tammy Chambless's MEIC                      ║
    ║         (Multiple Entry Iron Condors - Queen of 0DTE)         ║
    ║                                                               ║
    ║         Entries: 10:00, 10:30, 11:00, 11:30, 12:00, 12:30     ║
    ║         Expected: 20.7% CAGR, 4.31% max drawdown              ║
    ║                                                               ║
    ║         Version: 1.1.0                                        ║
    ║         API: Saxo Bank OpenAPI                                ║
    ║                                                               ║
    ╚═══════════════════════════════════════════════════════════════╝
    """
    print(banner)


def run_bot(config: dict, dry_run: bool = False, check_interval: int = 5):
    """
    Run the main trading bot loop.

    Args:
        config: Configuration dictionary
        dry_run: If True, simulate without placing real trades
        check_interval: Seconds between strategy checks
    """
    global shutdown_requested

    # Initialize logging service
    trade_logger = setup_logging(config, bot_name="MEIC")
    trade_logger.log_event("=" * 60)
    trade_logger.log_event("MEIC BOT STARTING")
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

    # Initialize strategy
    strategy = MEICStrategy(client, config, trade_logger, dry_run=dry_run)

    # Log dashboard metrics on startup
    try:
        trade_logger.log_event("Logging dashboard metrics on startup...")

        # Update market data first
        strategy.update_market_data()

        # Log Account Summary
        strategy.log_account_summary()

        # Log Performance Metrics
        strategy.log_performance_metrics()

        # Log bot startup activity
        status = strategy.get_status_summary()
        trade_logger.log_bot_activity(
            level="INFO",
            component="Main",
            message=f"Bot started - State: {status['state']}, Entries today: {status['entries_completed']}",
            spy_price=status['underlying_price'],
            vix=status['vix'],
            flush=True
        )

        trade_logger.log_event("Dashboard metrics logged to Google Sheets")
    except Exception as e:
        trade_logger.log_error(f"Failed to log startup dashboard metrics: {e}")

    # ==========================================================================
    # WEBSOCKET STREAMING SETUP (disabled by default - see USE_WEBSOCKET_STREAMING)
    # ==========================================================================
    # This code is preserved for future use if we hit rate limits with many bots.
    # Currently disabled because REST-only is more reliable.
    subscriptions = []
    streaming_started = False

    if USE_WEBSOCKET_STREAMING:
        underlying_uic = config.get("strategy", {}).get("underlying_uic")
        vix_uic = config.get("strategy", {}).get("vix_spot_uic", 10606)

        if underlying_uic:
            # US500.I is CfdOnIndex (tracks SPX)
            underlying_symbol = config.get("strategy", {}).get("underlying_symbol", "")
            if "US500" in underlying_symbol or underlying_symbol.endswith(".I"):
                underlying_type = "CfdOnIndex"
            else:
                underlying_type = "StockIndex"
            subscriptions.append({"uic": underlying_uic, "asset_type": underlying_type})

        if vix_uic:
            subscriptions.append({"uic": vix_uic, "asset_type": "StockIndex"})

        if subscriptions:
            trade_logger.log_event(f"Starting price streaming for {len(subscriptions)} instruments...")

            def price_update_handler(uic: int, data: dict):
                """Handle real-time price updates."""
                strategy.handle_price_update(uic, data)

                # Update WebSocket price cache for fast stop monitoring
                quote = data.get("Quote", {})
                bid = quote.get("Bid")
                ask = quote.get("Ask")
                if bid and ask:
                    mid_price = (bid + ask) / 2
                    strategy.update_ws_price_cache(uic, mid_price)
                elif quote.get("LastTraded"):
                    strategy.update_ws_price_cache(uic, quote["LastTraded"])

            streaming_started = client.start_price_streaming(subscriptions, price_update_handler)
            if not streaming_started:
                trade_logger.log_event("Warning: Real-time streaming not started. Using polling mode.")
    else:
        trade_logger.log_event("REST-only mode: WebSocket streaming disabled (USE_WEBSOCKET_STREAMING=False)")
        trade_logger.log_event("All price fetching will use REST API directly (more reliable)")

    # Main trading loop
    trade_logger.log_event("Entering main trading loop...")
    trade_logger.log_event("Press Ctrl+C to stop the bot gracefully")
    trade_logger.log_event("-" * 60)

    last_status_time = datetime.now()
    status_interval = 15  # Log status every 15 seconds
    last_bot_log_time = datetime.now()
    bot_log_interval = 3600  # Log to Google Sheets Bot Logs every hour
    last_day = datetime.now().date()
    consecutive_errors = 0
    daily_summary_sent_date = None

    try:
        while not shutdown_requested:
            try:
                # Check for new trading day
                today = datetime.now().date()
                if today != last_day:
                    trade_logger.log_event("New trading day detected - resetting strategy")
                    strategy._reset_for_new_day()
                    last_day = today

                # Check if market is open
                if not is_market_open():
                    market_status = get_market_status_message()
                    trade_logger.log_event(market_status)

                    # Determine reason for closure
                    holiday_name = get_holiday_name()
                    now_et = get_us_market_time()

                    if holiday_name:
                        close_reason = f"({holiday_name})"
                    elif is_weekend():
                        close_reason = "(weekend)"
                    else:
                        close_reason = ""

                    # Send daily summary once at market close
                    today_date = now_et.date()
                    if is_after_hours() and daily_summary_sent_date != today_date:
                        trade_logger.log_event("Market closed - sending daily summary...")
                        strategy.log_daily_summary()
                        daily_summary_sent_date = today_date
                        trade_logger.log_event("Daily summary sent")

                    # Calculate sleep duration (max 15 min to keep token alive)
                    sleep_time = calculate_sleep_duration(max_sleep=900)

                    # Wake up at 9:30 AM for market open
                    market_open_time = now_et.replace(hour=9, minute=30, second=0, microsecond=0)

                    if now_et < market_open_time and now_et.weekday() < 5 and not holiday_name:
                        seconds_until_open = (market_open_time - now_et).total_seconds()
                        if seconds_until_open > 0 and seconds_until_open < sleep_time:
                            sleep_time = int(seconds_until_open)
                            trade_logger.log_event(f"Pre-market: will wake at 9:30 AM ({sleep_time}s)")

                    if sleep_time > 0:
                        minutes = sleep_time // 60

                        # Stop streaming during market close (only if WebSocket mode enabled)
                        if USE_WEBSOCKET_STREAMING and client.is_streaming:
                            client.stop_price_streaming()

                        # Refresh token before sleeping
                        client.authenticate(force_refresh=True)

                        trade_logger.log_event(f"HEARTBEAT | Market closed {close_reason} - sleeping for {minutes}m")

                        if not interruptible_sleep(sleep_time):
                            break

                        # Reconnect streaming after waking (only if WebSocket mode enabled)
                        if USE_WEBSOCKET_STREAMING and not shutdown_requested and subscriptions:
                            client.start_price_streaming(subscriptions, price_update_handler)
                    else:
                        trade_logger.log_event(f"HEARTBEAT | Market closed {close_reason} - rechecking in 60s")
                        if not interruptible_sleep(60):
                            break
                    continue

                # Reconnect WebSocket if disconnected during market hours (only if WebSocket mode enabled)
                if USE_WEBSOCKET_STREAMING and subscriptions and not client.is_streaming:
                    trade_logger.log_event("WebSocket disconnected - reconnecting...")
                    try:
                        client.stop_price_streaming()
                        time.sleep(1)
                        streaming_started = client.start_price_streaming(subscriptions, price_update_handler)
                        if streaming_started:
                            trade_logger.log_event("WebSocket reconnected successfully")
                        else:
                            trade_logger.log_event("Warning: WebSocket reconnection failed - using REST polling")
                    except Exception as e:
                        trade_logger.log_error(f"WebSocket reconnection error: {e}")

                # Run strategy check
                action = strategy.run_strategy_check()

                # Reset consecutive errors on successful check
                consecutive_errors = 0

                # Log action if something meaningful happened (filter out status messages)
                skip_logging = (
                    action == "No action" or
                    "Waiting" in action or
                    "Monitoring" in action[:10]
                )
                if not skip_logging:
                    if dry_run:
                        trade_logger.log_event(f"[DRY RUN] {action}")
                    else:
                        trade_logger.log_event(action)

                # Periodic status logging
                now = datetime.now()
                if (now - last_status_time).total_seconds() >= status_interval:
                    status = strategy.get_status_summary()
                    mode_prefix = "[DRY RUN] " if dry_run else ""

                    # Main heartbeat line
                    total_pnl = status['realized_pnl'] + status['unrealized_pnl']
                    pnl_sign = "+" if total_pnl >= 0 else ""
                    heartbeat_msg = (
                        f"{mode_prefix}HEARTBEAT | {status['state']} | "
                        f"SPX: {status['underlying_price']:.2f} | "
                        f"VIX: {status['vix']:.2f} | "
                        f"Entries: {status['entries_completed']}/{len(strategy.entry_times)} | "
                        f"Active ICs: {status['active_entries']} | "
                        f"P&L: {pnl_sign}${total_pnl:.2f}"
                    )
                    trade_logger.log_event(heartbeat_msg)

                    # Detailed position lines (like Delta Neutral)
                    position_lines = strategy.get_detailed_position_status()
                    for line in position_lines:
                        trade_logger.log_event(line)

                    # Visual divider after heartbeat for log readability
                    trade_logger.log_event("[▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓]")
                    trade_logger.log_event("[░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░]")
                    trade_logger.log_event("[▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓]")

                    # Log to Google Sheets
                    strategy.log_account_summary()
                    strategy.log_performance_metrics()

                    last_status_time = now

                # Hourly Bot Logs
                if (now - last_bot_log_time).total_seconds() >= bot_log_interval:
                    try:
                        status = strategy.get_status_summary()
                        trade_logger.log_bot_activity(
                            level="INFO",
                            component="MEICStrategy",
                            message=f"Hourly: State={status['state']}, Entries={status['entries_completed']}, P&L=${status['realized_pnl'] + status['unrealized_pnl']:.2f}",
                            spy_price=status['underlying_price'],
                            vix=status['vix'],
                            flush=True
                        )
                        last_bot_log_time = now
                    except Exception as e:
                        trade_logger.log_error(f"Hourly bot log error: {e}")

                # Sleep until next check - P2: Use dynamic monitoring interval
                status = strategy.get_status_summary()
                if status['state'] == 'DailyComplete' and status['active_entries'] == 0:
                    # All done - check less frequently
                    if not interruptible_sleep(60):
                        break
                elif status['active_entries'] > 0:
                    # Active positions - use strategy's recommended interval
                    # P2: Vigilant mode (2s) when near stops, normal (5s) otherwise
                    recommended_interval = strategy.get_recommended_check_interval()
                    if not interruptible_sleep(recommended_interval):
                        break
                else:
                    # Standard interval
                    if not interruptible_sleep(check_interval):
                        break

            except KeyboardInterrupt:
                shutdown_requested = True
                break

            except Exception as e:
                consecutive_errors += 1
                trade_logger.log_error(f"Error in main loop (#{consecutive_errors}): {e}", exception=e)

                if consecutive_errors >= 5:
                    trade_logger.log_safety_event({
                        "event_type": "MEIC_CONSECUTIVE_ERRORS",
                        "spy_price": strategy.current_price,
                        "vix": strategy.current_vix,
                        "description": f"Main loop has {consecutive_errors} consecutive errors",
                        "result": "Continuing but system may be unstable"
                    })
                    logger.critical(f"CRITICAL: {consecutive_errors} consecutive errors in main loop!")

                if not interruptible_sleep(check_interval):
                    break

    finally:
        # Graceful shutdown
        trade_logger.log_event("=" * 60)
        trade_logger.log_event("INITIATING GRACEFUL SHUTDOWN")
        trade_logger.log_event("=" * 60)

        # Stop price streaming (only if WebSocket mode was enabled)
        if USE_WEBSOCKET_STREAMING:
            trade_logger.log_event("Stopping price streaming...")
            client.stop_price_streaming()

        # Log final status
        status = strategy.get_status_summary()
        trade_logger.log_event(
            f"Final Status: State={status['state']}, "
            f"Entries={status['entries_completed']}, "
            f"P&L=${status['realized_pnl'] + status['unrealized_pnl']:.2f}"
        )

        # Warning about active positions
        if status['active_entries'] > 0:
            logger.critical(
                f"CRITICAL: Bot shutting down with {status['active_entries']} ACTIVE ICs! "
                f"P&L: ${status['realized_pnl'] + status['unrealized_pnl']:.2f}"
            )
            trade_logger.log_event(
                f"WARNING: Bot shutting down with {status['active_entries']} ACTIVE ICs! "
                "Positions will remain open. Manual intervention may be required."
            )
            trade_logger.log_safety_event({
                "event_type": "MEIC_SHUTDOWN_WITH_POSITION",
                "spy_price": status['underlying_price'],
                "vix": status['vix'],
                "description": f"Bot shutdown with {status['active_entries']} active ICs",
                "result": "Positions left open - MANUAL INTERVENTION REQUIRED"
            })

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
    trade_logger = setup_logging(config, bot_name="MEIC")

    # Initialize client
    client = SaxoClient(config)

    if not client.authenticate():
        print("Failed to authenticate. Please check your credentials.")
        return

    # Initialize strategy
    strategy = MEICStrategy(client, config, trade_logger)

    # Update market data
    strategy.update_market_data()

    # Get and display status
    status = strategy.get_status_summary()

    print("\n" + "=" * 60)
    print("MEIC CURRENT STATUS")
    print("=" * 60)
    print(f"  State: {status['state']}")
    print(f"  SPX Price: {status['underlying_price']:.2f}")
    print(f"  VIX: {status['vix']:.2f}")
    print("\n  Entry Schedule:")
    for i, entry_time in enumerate(strategy.entry_times):
        completed = i < status['entries_completed']
        marker = "✓" if completed else "○"
        print(f"    {marker} Entry #{i+1}: {entry_time.strftime('%H:%M')} ET")

    print("\n  Today's Stats:")
    print(f"    Entries Completed: {status['entries_completed']}")
    print(f"    Entries Failed: {status['entries_failed']}")
    print(f"    Active ICs: {status['active_entries']}")
    print(f"    Total Credit: ${status['total_credit']:.2f}")
    print(f"    Realized P&L: ${status['realized_pnl']:.2f}")
    print(f"    Unrealized P&L: ${status['unrealized_pnl']:.2f}")
    print(f"    Total P&L: ${status['realized_pnl'] + status['unrealized_pnl']:.2f}")
    print(f"    Stops Triggered: {status['total_stops']}")

    print("=" * 60 + "\n")


def main():
    """Main entry point."""
    # DUPLICATE-001: Kill any existing MEIC instances before starting
    # This prevents duplicate trades and circuit breaker issues from zombie processes
    kill_existing_meic_instances()

    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="MEIC 0DTE Trading Bot - Tammy Chambless's Strategy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m bots.meic.main              Run in SIM environment
  python -m bots.meic.main --live       Run in LIVE environment
  python -m bots.meic.main --dry-run    Simulate without orders
  python -m bots.meic.main --status     Show current status only
        """
    )

    parser.add_argument(
        "--config", "-c",
        default="bots/meic/config/config.json",
        help="Path to configuration file"
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
        default=5,
        help="Strategy check interval in seconds (default: 5)"
    )

    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging (DEBUG level)"
    )

    parser.add_argument(
        "--live", "-l",
        action="store_true",
        help="Use LIVE environment (real money trading)"
    )

    args = parser.parse_args()

    # Print banner
    print_banner()

    try:
        # Load configuration
        config = load_config(args.config)

        # Show environment info
        running_on_cloud = is_running_on_gcp()
        if running_on_cloud:
            print("\n" + "=" * 60)
            print("  RUNNING ON GOOGLE CLOUD PLATFORM")
            print("  Environment: LIVE (Cloud deployment)")
            print("  Credentials: Loaded from Secret Manager")
            print("=" * 60 + "\n")
        else:
            if args.live:
                config["saxo_api"]["environment"] = "live"
                if args.dry_run:
                    print("\n" + "=" * 60)
                    print("  DRY RUN MODE - LIVE DATA, NO REAL ORDERS")
                    print("  Using LIVE market data for realistic simulation")
                    print("=" * 60 + "\n")
                else:
                    print("\n  WARNING: LIVE ENVIRONMENT ENABLED - REAL MONEY TRADING\n")
            else:
                env_name = config.get('saxo_api', {}).get('environment', 'sim').upper()
                if args.dry_run:
                    print(f"\n  Environment: {env_name} (DRY RUN - No real orders)\n")
                else:
                    print(f"\n  Environment: {env_name}\n")

        # Override log level if verbose
        if args.verbose:
            config["logging"]["log_level"] = "DEBUG"

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Get dry_run from config if not specified on command line
        dry_run = args.dry_run or config.get("dry_run", False)

        # Execute requested mode
        if args.status:
            show_status(config)
        else:
            run_bot(
                config=config,
                dry_run=dry_run,
                check_interval=args.interval
            )

    except FileNotFoundError as e:
        print(f"\n  Error: {e}")
        print("  Make sure config file exists at: bots/meic/config/config.json")
        sys.exit(1)

    except ValueError as e:
        print(f"\n  Configuration Error: {e}")
        sys.exit(1)

    except Exception as e:
        print(f"\n  Unexpected Error: {e}")
        logger.exception("Unexpected error in main()")
        sys.exit(1)


if __name__ == "__main__":
    main()
