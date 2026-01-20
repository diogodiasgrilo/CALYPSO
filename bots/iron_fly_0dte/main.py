#!/usr/bin/env python3
"""
main.py - 0DTE Iron Fly Trading Bot Entry Point

This is the main entry point for the 0DTE Iron Fly Trading Bot
implementing Doc Severson's strategy.

Strategy Summary:
-----------------
1. Monitor opening range (9:30-10:00 AM EST)
2. Enter at 10:00 AM if VIX < 20 and price within range
3. Sell ATM Iron Butterfly with wings at expected move
4. Take profit at $50-$100 per contract
5. Stop loss when price touches wing strikes
6. Average hold time: 18 minutes

Usage:
------
    python -m bots.iron_fly_0dte.main              # Run in SIM environment
    python -m bots.iron_fly_0dte.main --live       # Run in LIVE environment
    python -m bots.iron_fly_0dte.main --dry-run    # Simulate without orders
    python -m bots.iron_fly_0dte.main --status     # Show current status only
    python -m bots.iron_fly_0dte.main --calibrate 25  # Manual expected move

Author: Trading Bot Developer
Date: 2025
"""

import os
import sys
import json
import time
import signal
import argparse
import logging
from datetime import datetime
from typing import Optional

# Ensure project root is in path for imports when running as script
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# Import shared modules
from shared.saxo_client import SaxoClient
from shared.logger_service import TradeLoggerService, setup_logging
from shared.market_hours import is_market_open, get_market_status_message, calculate_sleep_duration, get_holiday_name
from shared.config_loader import ConfigLoader, get_config_loader
from shared.secret_manager import is_running_on_gcp

# Import bot-specific strategy
from bots.iron_fly_0dte.strategy import IronFlyStrategy, IronFlyState

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


def interruptible_sleep(seconds: int, check_interval: int = 1) -> bool:
    """
    Sleep for the specified duration, but check for shutdown signal periodically.

    Args:
        seconds: Total seconds to sleep
        check_interval: How often to check for shutdown (default 1 second for 0DTE)

    Returns:
        bool: True if sleep completed, False if interrupted by shutdown
    """
    remaining = seconds
    while remaining > 0 and not shutdown_requested:
        time.sleep(min(check_interval, remaining))
        remaining -= check_interval
    return not shutdown_requested


def load_config(config_path: str = "bots/iron_fly_0dte/config/config.json") -> dict:
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
    ║         0DTE IRON FLY TRADING BOT                             ║
    ║         ═════════════════════════                             ║
    ║                                                               ║
    ║         Strategy: Doc Severson's 0DTE Iron Butterfly          ║
    ║         Entry: 10:00 AM EST after Opening Range               ║
    ║         Target: $50-$100 profit in 18 minutes                 ║
    ║                                                               ║
    ║         Version: 1.0.0                                        ║
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
        check_interval: Seconds between strategy checks (default 5 for 0DTE)
    """
    global shutdown_requested

    # Initialize logging service
    trade_logger = setup_logging(config, bot_name="IRON_FLY_0DTE")
    trade_logger.log_event("=" * 60)
    trade_logger.log_event("0DTE IRON FLY BOT STARTING")
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
    strategy = IronFlyStrategy(client, config, trade_logger, dry_run=dry_run)

    # Log dashboard metrics on startup (always update on restart for fresh data)
    try:
        trade_logger.log_event("Logging dashboard metrics on startup...")

        # Update market data first
        strategy.update_market_data()

        # Log Account Summary
        strategy.log_account_summary()

        # Log Performance Metrics
        strategy.log_performance_metrics()

        # Log bot startup activity (always - so we know when bot started)
        status = strategy.get_status_summary()
        trade_logger.log_bot_activity(
            level="INFO",
            component="Main",
            message=f"Bot started - State: {status['state']}, Trades today: {status['trades_today']}",
            spy_price=status['underlying_price'],
            vix=status['vix'],
            flush=True
        )

        trade_logger.log_event("Dashboard metrics logged to Google Sheets")
    except Exception as e:
        trade_logger.log_error(f"Failed to log startup dashboard metrics: {e}")

    # Start real-time price streaming for underlying and VIX
    subscriptions = []
    underlying_uic = config.get("strategy", {}).get("underlying_uic")
    vix_uic = config.get("strategy", {}).get("vix_uic", 10606)

    if underlying_uic:
        # Determine asset type from symbol:
        # - US500.I is CfdOnIndex (tracks SPX)
        # - SPY is Etf
        # - SPX would be StockIndex
        underlying_symbol = config.get("strategy", {}).get("underlying_symbol", "")
        if "US500" in underlying_symbol or underlying_symbol.endswith(".I"):
            underlying_type = "CfdOnIndex"
        elif "SPX" in underlying_symbol:
            underlying_type = "StockIndex"
        else:
            underlying_type = "Etf"
        subscriptions.append({"uic": underlying_uic, "asset_type": underlying_type})

    if vix_uic:
        subscriptions.append({"uic": vix_uic, "asset_type": "StockIndex"})

    if subscriptions:
        trade_logger.log_event(f"Starting price streaming for {len(subscriptions)} instruments...")

        def price_update_handler(uic: int, data: dict):
            """Handle real-time price updates."""
            strategy.handle_price_update(uic, data)

        streaming_started = client.start_price_streaming(subscriptions, price_update_handler)
        if not streaming_started:
            trade_logger.log_event("Warning: Real-time streaming not started. Using polling mode.")

    # Main trading loop
    trade_logger.log_event("Entering main trading loop...")
    trade_logger.log_event("Press Ctrl+C to stop the bot gracefully")
    trade_logger.log_event("-" * 60)

    last_status_time = datetime.now()
    status_interval = 60  # Log status every minute (more frequent for 0DTE)
    last_bot_log_time = datetime.now()
    bot_log_interval = 3600  # Log to Google Sheets Bot Logs every hour (3600 seconds)
    last_day = datetime.now().date()
    consecutive_errors = 0  # Track consecutive errors for health monitoring

    try:
        while not shutdown_requested:
            try:
                # Check for new trading day
                today = datetime.now().date()
                if today != last_day:
                    trade_logger.log_event("New trading day detected - resetting strategy")
                    strategy.reset_for_new_day()
                    last_day = today

                # Check if market is open
                if not is_market_open():
                    market_status = get_market_status_message()
                    trade_logger.log_event(market_status)

                    # Determine reason for closure (for heartbeat messages)
                    from shared.market_hours import is_weekend
                    holiday_name = get_holiday_name()
                    if holiday_name:
                        close_reason = f"({holiday_name})"
                    elif is_weekend():
                        close_reason = "(weekend)"
                    else:
                        close_reason = ""

                    # Calculate sleep duration (max 15 min to keep token alive)
                    sleep_time = calculate_sleep_duration(max_sleep=900)

                    if sleep_time > 0:
                        minutes = sleep_time // 60

                        # Stop streaming during market close
                        if client.is_streaming:
                            client.stop_price_streaming()

                        # Refresh token before sleeping
                        client.authenticate(force_refresh=True)

                        trade_logger.log_event(f"HEARTBEAT | Market closed {close_reason} - sleeping for {minutes}m")

                        if not interruptible_sleep(sleep_time):
                            break

                        # Reconnect streaming after waking
                        if not shutdown_requested and subscriptions:
                            client.start_price_streaming(subscriptions, price_update_handler)
                    else:
                        trade_logger.log_event(f"HEARTBEAT | Market closed {close_reason} - rechecking in 60s")
                        if not interruptible_sleep(60):
                            break
                    continue

                # Run strategy check
                action = strategy.run_strategy_check()

                # Reset consecutive errors on successful check
                consecutive_errors = 0

                # Log action if something happened
                if action != "No action" and "Waiting" not in action and "Monitoring" not in action:
                    if dry_run:
                        trade_logger.log_event(f"[DRY RUN] {action}")
                    else:
                        trade_logger.log_event(action)

                # Periodic status logging (every 60 seconds to terminal/file logs)
                now = datetime.now()
                if (now - last_status_time).total_seconds() >= status_interval:
                    status = strategy.get_status_summary()
                    mode_prefix = "[DRY RUN] " if dry_run else ""
                    heartbeat_msg = (
                        f"{mode_prefix}HEARTBEAT | State: {status['state']} | "
                        f"{config.get('strategy', {}).get('underlying_symbol', 'SPX')}: {status['underlying_price']:.2f} | "
                        f"VIX: {status['vix']:.2f} | "
                        f"Trades: {status['trades_today']} | P&L: ${status['daily_pnl']:.2f}"
                    )
                    trade_logger.log_event(heartbeat_msg)

                    # Log Account Summary periodically (every 60s for real-time dashboard)
                    strategy.log_account_summary()

                    # Log position if one is open
                    if status.get('position_active'):
                        strategy.log_position_to_sheets()

                    # Log performance metrics
                    strategy.log_performance_metrics()

                    last_status_time = now

                # Hourly Bot Logs to Google Sheets (avoid flooding with hundreds of rows)
                if (now - last_bot_log_time).total_seconds() >= bot_log_interval:
                    try:
                        status = strategy.get_status_summary()
                        trade_logger.log_bot_activity(
                            level="INFO",
                            component="IronFlyStrategy",
                            message=f"Hourly update: State={status['state']}, Trades={status['trades_today']}, P&L=${status['daily_pnl']:.2f}",
                            spy_price=status['underlying_price'],
                            vix=status['vix'],
                            flush=True
                        )
                        last_bot_log_time = now
                    except Exception as e:
                        trade_logger.log_error(f"Hourly bot log error: {e}")

                # Sleep until next check (shorter for 0DTE - need fast reaction)
                if not interruptible_sleep(check_interval):
                    break

            except KeyboardInterrupt:
                shutdown_requested = True
                break

            except Exception as e:
                consecutive_errors += 1
                trade_logger.log_error(f"Error in main loop (#{consecutive_errors}): {e}", exception=e)

                # SAFETY: Log critical if too many consecutive errors
                if consecutive_errors >= 5:
                    trade_logger.log_safety_event({
                        "event_type": "IRON_FLY_CONSECUTIVE_ERRORS",
                        "spy_price": strategy.current_price if strategy else 0,
                        "vix": strategy.current_vix if strategy else 0,
                        "description": f"Main loop has {consecutive_errors} consecutive errors",
                        "result": "Continuing but system may be unstable"
                    })
                    logger.critical(f"CRITICAL: {consecutive_errors} consecutive errors in main loop!")

                # Don't continue if we have an open position and errors
                if strategy and strategy.position and consecutive_errors >= 3:
                    logger.critical(
                        "CRITICAL: Multiple errors with open position! "
                        "Stop-loss protection may be compromised!"
                    )

                if not interruptible_sleep(check_interval):
                    break

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
        trade_logger.log_event(f"Final Status: State={status['state']}, "
                               f"Trades={status['trades_today']}, P&L=${status['daily_pnl']:.2f}")

        # Warning about open positions with option to close
        if status.get('position_active'):
            logger.critical(
                "CRITICAL: Bot shutting down with ACTIVE POSITION! "
                f"ATM={status.get('atm_strike')}, P&L=${status.get('unrealized_pnl', 0):.2f}"
            )
            trade_logger.log_event(
                "WARNING: Bot shutting down with ACTIVE POSITION! "
                "Position will remain open. Manual intervention may be required."
            )
            trade_logger.log_safety_event({
                "event_type": "IRON_FLY_SHUTDOWN_WITH_POSITION",
                "spy_price": status.get('underlying_price', 0),
                "vix": status.get('vix', 0),
                "description": f"Bot shutdown with active position: ATM={status.get('atm_strike')}",
                "result": "Position left open - MANUAL INTERVENTION REQUIRED"
            })

        # Shutdown logger (flush buffers, stop background thread)
        trade_logger.shutdown()

        trade_logger.log_event("Shutdown complete.")


def show_status(config: dict):
    """
    Show current status without entering trading loop.

    Args:
        config: Configuration dictionary
    """
    # Initialize logging
    trade_logger = setup_logging(config, bot_name="IRON_FLY_0DTE")

    # Initialize client
    client = SaxoClient(config)

    if not client.authenticate():
        print("Failed to authenticate. Please check your credentials.")
        return

    # Initialize strategy
    strategy = IronFlyStrategy(client, config, trade_logger)

    # Update market data
    strategy.update_market_data()

    # Get and display status
    status = strategy.get_status_summary()
    symbol = config.get("strategy", {}).get("underlying_symbol", "SPX")

    print("\n" + "=" * 60)
    print("CURRENT STATUS")
    print("=" * 60)
    print(f"  State: {status['state']}")
    print(f"  {symbol} Price: {status['underlying_price']:.2f}")
    print(f"  VIX: {status['vix']:.2f}")
    print(f"  VIX Entry Threshold: < {config.get('strategy', {}).get('max_vix_entry', 20)}")
    print(f"  VIX Spike Threshold: < {config.get('strategy', {}).get('vix_spike_threshold_percent', 5)}%")

    if status.get('opening_range_complete'):
        print(f"\n  Opening Range:")
        print(f"    High: {status['opening_range_high']:.2f}")
        print(f"    Low: {status['opening_range_low']:.2f}")
        print(f"    Width: {status['opening_range_width']:.2f}")
        print(f"    VIX Spike: {status['vix_spike_percent']:.1f}%")

    if status.get('position_active'):
        print(f"\n  Active Position:")
        print(f"    ATM Strike: {status['atm_strike']:.0f}")
        print(f"    Wings: {status['lower_wing']:.0f} / {status['upper_wing']:.0f}")
        print(f"    Credit: ${status['credit_received']:.2f}")
        print(f"    Unrealized P&L: ${status['unrealized_pnl']:.2f}")
        print(f"    Hold Time: {status['hold_time_minutes']} minutes")
        print(f"    Distance to {status['nearest_wing']} wing: {status['distance_to_wing']:.2f} pts")

    print(f"\n  Daily Stats:")
    print(f"    Trades Today: {status['trades_today']}")
    print(f"    Daily P&L: ${status['daily_pnl']:.2f}")

    print("=" * 60 + "\n")


def main():
    """Main entry point."""
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="0DTE Iron Fly Trading Bot - Doc Severson's Strategy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m bots.iron_fly_0dte.main              Run in SIM environment
  python -m bots.iron_fly_0dte.main --live       Run in LIVE environment
  python -m bots.iron_fly_0dte.main --dry-run    Simulate without orders
  python -m bots.iron_fly_0dte.main --status     Show current status only
  python -m bots.iron_fly_0dte.main --calibrate 25   Use 25-point expected move
        """
    )

    parser.add_argument(
        "--config", "-c",
        default="bots/iron_fly_0dte/config/config.json",
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
        help="Strategy check interval in seconds (default: 5 for 0DTE)"
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

    parser.add_argument(
        "--calibrate",
        type=float,
        help="Manual expected move value (points) for calibration mode"
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

        # Apply calibration mode if specified
        if args.calibrate:
            config.setdefault("strategy", {})["manual_expected_move"] = args.calibrate
            print(f"  CALIBRATION MODE: Using manual expected move of {args.calibrate} points\n")

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
        print(f"\n  Error: {e}")
        print("  Make sure config file exists at: bots/iron_fly_0dte/config/config.json")
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
