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
    python main.py                    # Run with default config
    python main.py --config my.json   # Run with custom config
    python main.py --dry-run          # Simulate without trading
    python main.py --status           # Show current status only

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
from saxo_client import SaxoClient
from strategy import DeltaNeutralStrategy, StrategyState
from logger_service import TradeLoggerService, setup_logging

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


def load_config(config_path: str = "config.json") -> dict:
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
    required_keys = [
        ("saxo_api", "app_key"),
        ("saxo_api", "app_secret"),
        ("account", "account_key"),
        ("account", "client_key"),
    ]

    for section, key in required_keys:
        if section not in config:
            raise ValueError(f"Missing config section: {section}")
        if key not in config[section]:
            raise ValueError(f"Missing config key: {section}.{key}")
        if config[section][key].startswith("YOUR_"):
            logger.warning(f"Config placeholder detected: {section}.{key} - Please update with real values")

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
    underlying_uic = config["strategy"]["underlying_uic"]
    trade_logger.log_event(f"Starting price streaming for UIC {underlying_uic}...")

    def price_update_handler(uic: int, data: dict):
        """Handle real-time price updates."""
        strategy.handle_price_update(uic, data)

    streaming_started = client.start_price_streaming(
        uics=[underlying_uic],
        callback=price_update_handler,
        asset_type="Etf"
    )

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

                # Run strategy check
                if not dry_run:
                    action = strategy.run_strategy_check()
                    if action != "No action":
                        trade_logger.log_event(f"ACTION: {action}")
                else:
                    # Dry run - just update market data and report
                    strategy.update_market_data()
                    status = strategy.get_status_summary()
                    trade_logger.log_event(
                        f"[DRY RUN] SPY: ${status['underlying_price']:.2f} | "
                        f"VIX: {status['vix']:.2f} | State: {status['state']}"
                    )

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
  python main.py                     Run bot with default config
  python main.py --dry-run           Simulate without trading
  python main.py --status            Show current status only
  python main.py --config prod.json  Use custom config file
  python main.py --interval 30       Check every 30 seconds
        """
    )

    parser.add_argument(
        "--config", "-c",
        default="config.json",
        help="Path to configuration file (default: config.json)"
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

    args = parser.parse_args()

    # Print banner
    print_banner()

    try:
        # Load configuration
        config = load_config(args.config)

        # Override log level if verbose
        if args.verbose:
            config["logging"]["log_level"] = "DEBUG"

        # Validate configuration
        validate_config(config)

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
