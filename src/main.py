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
4. Roll weekly shorts on Thursday/Friday
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
from datetime import datetime
from typing import Optional

# Import bot modules
from src.saxo_client import SaxoClient
from src.strategy import DeltaNeutralStrategy, StrategyState
from src.logger_service import TradeLoggerService, setup_logging
from src.market_hours import is_market_open, get_market_status_message, calculate_sleep_duration

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


def load_config(config_path: str = "config/config.json") -> dict:
    """
    Load configuration from JSON file.

    Args:
        config_path: Path to the configuration file

    Returns:
        dict: Configuration dictionary

    Raises:
        FileNotFoundError: If config file doesn't exist
        json.JSONDecodeError: If config file is invalid JSON
    """
    if not os.path.exists(config_path):
        raise FileNotFoundError(
            f"Configuration file not found: {config_path}\n"
            f"Please copy config.json.example to {config_path} and fill in your credentials."
        )

    with open(config_path, "r") as f:
        config = json.load(f)

    logger.info(f"Configuration loaded from: {config_path}")
    return config


def validate_config(config: dict) -> bool:
    """
    Validate that required configuration values are present.

    Args:
        config: Configuration dictionary

    Returns:
        bool: True if valid, raises ValueError otherwise
    """
    # Check account keys
    if "account" not in config:
        raise ValueError("Missing config section: account")
    if "account_key" not in config["account"]:
        raise ValueError("Missing config key: account.account_key")
    if "client_key" not in config["account"]:
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
    setup_logging(config)

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
        if account.get('AccountKey') == config["account"].get("account_key"):
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
    trade_logger = setup_logging(config)
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

    # Initialize strategy
    strategy = DeltaNeutralStrategy(client, config, trade_logger)

    # Start real-time price streaming
    # FIXED: Define full subscription details with correct AssetTypes to avoid 404 errors
    subscriptions = [
        {"uic": config["strategy"]["underlying_uic"], "asset_type": "Etf"},
        {"uic": config["strategy"]["vix_uic"], "asset_type": "StockIndex"} # Keep as StockIndex
    ]
    
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

    try:
        while not shutdown_requested:
            try:
                # Check if market is open
                if not is_market_open():
                    market_status = get_market_status_message()
                    trade_logger.log_event(market_status)

                    # Calculate intelligent sleep duration
                    sleep_time = calculate_sleep_duration(max_sleep=3600)  # Max 1 hour

                    if sleep_time > 0:
                        hours = sleep_time // 3600
                        minutes = (sleep_time % 3600) // 60
                        trade_logger.log_event(
                            f"Sleeping for {hours}h {minutes}m. "
                            f"Bot will wake up to recheck market status."
                        )
                        time.sleep(sleep_time)
                    else:
                        time.sleep(60)  # Recheck in 1 minute if close to market open
                    continue

                # Check circuit breaker
                if client.is_circuit_open():
                    trade_logger.log_event("Circuit breaker is OPEN - waiting for cooldown...")
                    time.sleep(check_interval)
                    continue

                # Check connection timeout
                if client.check_connection_timeout():
                    trade_logger.log_error("Connection timeout detected - circuit breaker activated")
                    time.sleep(check_interval)
                    continue

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

                # Sleep until next check
                time.sleep(check_interval)

            except KeyboardInterrupt:
                # This should be caught by signal handler, but just in case
                shutdown_requested = True
                break

            except Exception as e:
                trade_logger.log_error(f"Error in main loop: {e}", exception=e)
                # Continue running unless it's a critical error
                time.sleep(check_interval)

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
        if strategy.state != StrategyState.IDLE:
            trade_logger.log_event(
                "WARNING: Bot shutting down with active positions! "
                "Positions will remain open. Restart the bot to continue managing them, "
                "or close them manually."
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
    trade_logger = setup_logging(config)

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
        default="config/config.json",
        help="Path to configuration file (default: config/config.json)"
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

    try:
        # Load configuration
        config = load_config(args.config)

        # Override environment if --live flag is used
        if args.live:
            config["saxo_api"]["environment"] = "live"
            print("\n⚠️  WARNING: LIVE ENVIRONMENT ENABLED - REAL MONEY TRADING ⚠️\n")

        # Override log level if verbose
        if args.verbose:
            config["logging"]["log_level"] = "DEBUG"

        # Override account if specified
        if args.account:
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
