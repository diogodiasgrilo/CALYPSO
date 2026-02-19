#!/usr/bin/env python3
"""
main.py - MEIC-TF (Trend Following Hybrid) Trading Bot Entry Point

This is a modified MEIC bot that adds EMA-based trend direction detection.
Based on Tammy Chambless's MEIC strategy with trend filtering inspired by METF.

Strategy Summary:
-----------------
1. Before each entry, check 20 EMA vs 40 EMA on SPX 1-minute bars
2. BULLISH (20 > 40): Place PUT spread only (calls are risky in uptrend)
3. BEARISH (20 < 40): Place CALL spread only (puts are risky in downtrend)
4. NEUTRAL (within 0.1%): Place full iron condor (standard MEIC)
5. Same entry times, strikes, and stop loss rules as MEIC

Why This Helps:
---------------
On strong trend days (like Feb 4, 2026), pure MEIC had ALL 6 put sides stopped
because the market was in a sustained downtrend. MEIC-TF would have detected
the bearish trend and only placed call spreads, avoiding ~$1,500 in losses.

Usage:
------
    python -m bots.meic_tf.main              # Run in SIM environment
    python -m bots.meic_tf.main --live       # Run in LIVE environment
    python -m bots.meic_tf.main --dry-run    # Simulate without orders
    python -m bots.meic_tf.main --status     # Show current status only

Author: Trading Bot Developer
Date: 2026-02-04

See docs/MEIC_STRATEGY_SPECIFICATION.md for base MEIC details.
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
    get_holiday_name, get_us_market_time, is_weekend
)
from shared.config_loader import ConfigLoader
from shared.secret_manager import is_running_on_gcp

# Import bot-specific strategy
from bots.meic_tf.strategy import MEICTFStrategy

# Configure main logger
logger = logging.getLogger(__name__)

# REST-only mode for reliability (same as MEIC)
USE_WEBSOCKET_STREAMING = False

# Global flag for graceful shutdown
shutdown_requested = False


def signal_handler(signum, frame):
    """Handle shutdown signals (CTRL+C, SIGTERM)."""
    global shutdown_requested
    logger.info(f"\nShutdown signal received ({signum}). Initiating graceful shutdown...")
    shutdown_requested = True


def interruptible_sleep(seconds: int, check_interval: int = 1) -> bool:
    """Sleep for the specified duration, but check for shutdown signal periodically."""
    remaining = seconds
    while remaining > 0 and not shutdown_requested:
        time.sleep(min(check_interval, remaining))
        remaining -= check_interval
    return not shutdown_requested


def kill_existing_instances() -> int:
    """
    DUPLICATE-001: Find and kill any existing MEIC-TF bot instances before starting.
    """
    current_pid = os.getpid()
    killed_count = 0

    try:
        result = subprocess.run(
            ["pgrep", "-f", "meic_tf[./]main\\.py"],
            capture_output=True,
            text=True
        )

        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split('\n')

            for pid_str in pids:
                try:
                    pid = int(pid_str.strip())
                    if pid != current_pid:
                        logger.info(f"DUPLICATE-001: Found existing MEIC-TF instance (PID: {pid}), terminating...")
                        os.kill(pid, signal.SIGTERM)
                        killed_count += 1
                        time.sleep(1)
                except (ValueError, ProcessLookupError):
                    pass

        if killed_count > 0:
            logger.info(f"DUPLICATE-001: Terminated {killed_count} existing MEIC-TF instance(s)")
            time.sleep(2)

    except FileNotFoundError:
        logger.warning("DUPLICATE-001: pgrep not available - cannot check for existing instances")
    except Exception as e:
        logger.warning(f"DUPLICATE-001: Error checking for existing instances: {e}")

    return killed_count


def load_config(config_path: str = "bots/meic_tf/config/config.json") -> dict:
    """Load configuration from appropriate source (cloud or local)."""
    loader = ConfigLoader(config_path)
    config = loader.load_config()
    return config


def print_banner():
    """Print the application banner."""
    banner = """
    ╔═══════════════════════════════════════════════════════════════╗
    ║                                                               ║
    ║         MEIC-TF 0DTE TRADING BOT                              ║
    ║         ════════════════════════                              ║
    ║                                                               ║
    ║         Strategy: MEIC + Trend Following Hybrid               ║
    ║         (EMA 20/40 Direction Filter)                          ║
    ║                                                               ║
    ║         BULLISH → PUT spreads only                            ║
    ║         BEARISH → CALL spreads only                           ║
    ║         NEUTRAL → Full iron condor                            ║
    ║                                                               ║
    ║         Entries: 10:05, 10:35, 11:05, 11:35, 12:05            ║
    ║                                                               ║
    ║         Version: 1.3.0                                        ║
    ║         API: Saxo Bank OpenAPI                                ║
    ║                                                               ║
    ╚═══════════════════════════════════════════════════════════════╝
    """
    print(banner)


def run_bot(config: dict, dry_run: bool = False, check_interval: int = 5):
    """Run the main trading bot loop."""
    global shutdown_requested

    # Initialize logging service
    trade_logger = setup_logging(config, bot_name="MEIC-TF")
    trade_logger.log_event("=" * 60)
    trade_logger.log_event("MEIC-TF BOT STARTING")
    trade_logger.log_event(f"Mode: {'DRY RUN (Simulation)' if dry_run else 'LIVE TRADING'}")
    trade_logger.log_event(f"Check Interval: {check_interval} seconds")
    trade_logger.log_event("=" * 60)

    # Initialize Saxo client
    client = SaxoClient(config)

    # Authenticate with Saxo API
    trade_logger.log_event("Authenticating with Saxo Bank API...")
    if not client.authenticate():
        trade_logger.log_error("Failed to authenticate. Please check your credentials.")
        trade_logger.shutdown()
        return

    trade_logger.log_event("Authentication successful!")

    # Initialize strategy
    strategy = None
    try:
        strategy = MEICTFStrategy(client, config, trade_logger, dry_run=dry_run)
    except Exception as e:
        trade_logger.log_error(f"Failed to initialize strategy: {e}")
        logger.exception("Strategy initialization failed")
        trade_logger.shutdown()
        return

    # Log dashboard metrics on startup
    try:
        trade_logger.log_event("Logging dashboard metrics on startup...")
        strategy.update_market_data()
        strategy.log_account_summary()
        strategy.log_performance_metrics()
        strategy.log_position_snapshot()

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

    # REST-only mode
    trade_logger.log_event("REST-only mode: WebSocket streaming disabled")
    trade_logger.log_event("All price fetching will use REST API directly")

    # Main trading loop
    trade_logger.log_event("Entering main trading loop...")
    trade_logger.log_event("Press Ctrl+C to stop the bot gracefully")
    trade_logger.log_event("-" * 60)

    last_status_time = datetime.now()
    status_interval = 10
    last_bot_log_time = datetime.now()
    bot_log_interval = 3600
    last_day = get_us_market_time().date()
    consecutive_errors = 0
    daily_summary_sent_date = None

    try:
        while not shutdown_requested:
            try:
                # Check for new trading day
                today = get_us_market_time().date()
                if today != last_day:
                    trade_logger.log_event("New trading day detected - resetting strategy")
                    strategy._reset_for_new_day()
                    last_day = today

                # Check if market is open
                if not is_market_open():
                    market_status = get_market_status_message()
                    trade_logger.log_event(market_status)

                    holiday_name = get_holiday_name()
                    now_et = get_us_market_time()

                    if holiday_name:
                        close_reason = f"({holiday_name})"
                    elif is_weekend():
                        close_reason = "(weekend)"
                    else:
                        close_reason = ""

                    # After-hours settlement reconciliation
                    # FIX #39: Check settlement until complete, not just during 4-5 PM window
                    # Saxo settles 0DTE options anytime between 5 PM - 2 AM ET, so we need to
                    # keep checking until settlement_complete returns True (all positions cleared)
                    today_date = now_et.date()
                    if not is_weekend() and daily_summary_sent_date != today_date:
                        try:
                            settlement_complete = strategy.check_after_hours_settlement()
                        except Exception as e:
                            trade_logger.log_error(f"Settlement check failed: {e}")
                            settlement_complete = False

                        if not settlement_complete:
                            trade_logger.log_event("Settlement pending - positions still open on Saxo")
                        else:
                            # FIX #48: Don't send empty daily summary on pre-market startup
                            # If settlement is "complete" because there's nothing to settle AND
                            # we're before market open with no trading activity, skip the summary
                            market_open_time = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
                            had_trading_activity = (
                                strategy.daily_state.entries_completed > 0 or
                                strategy.daily_state.total_realized_pnl != 0 or
                                len(strategy.daily_state.entries) > 0
                            )
                            is_after_market_close = now_et.hour >= 16  # 4 PM or later

                            if had_trading_activity or is_after_market_close:
                                trade_logger.log_event("Settlement complete - sending daily summary...")
                                try:
                                    strategy.log_daily_summary()
                                    # Fix #65: Also log post-settlement account summary and performance metrics
                                    # These were previously only logged during market hours heartbeat (pre-settlement),
                                    # meaning the final values with settled P&L were never recorded
                                    strategy.log_account_summary()
                                    strategy.log_performance_metrics()
                                    strategy.log_position_snapshot()
                                except Exception as e:
                                    trade_logger.log_error(f"Failed to log daily summary: {e}")
                                daily_summary_sent_date = today_date
                                trade_logger.log_event("Daily summary sent to Google Sheets and alerts")
                            else:
                                trade_logger.log_event("Settlement complete - no trading activity today, skipping empty summary")
                                daily_summary_sent_date = today_date  # Mark as sent to avoid repeated checks

                    sleep_time = calculate_sleep_duration(max_sleep=900)
                    market_open_time = now_et.replace(hour=9, minute=30, second=0, microsecond=0)

                    if now_et < market_open_time and now_et.weekday() < 5 and not holiday_name:
                        seconds_until_open = (market_open_time - now_et).total_seconds()
                        if seconds_until_open > 0 and seconds_until_open < sleep_time:
                            sleep_time = int(seconds_until_open)
                            trade_logger.log_event(f"Pre-market: will wake at 9:30 AM ({sleep_time}s)")

                    if sleep_time > 0:
                        minutes = sleep_time // 60
                        client.authenticate(force_refresh=True)
                        trade_logger.log_event(f"HEARTBEAT | Market closed {close_reason} - sleeping for {minutes}m")

                        if not interruptible_sleep(sleep_time):
                            break
                    else:
                        trade_logger.log_event(f"HEARTBEAT | Market closed {close_reason} - rechecking in 60s")
                        if not interruptible_sleep(60):
                            break
                    continue

                # Run strategy check
                action = strategy.run_strategy_check()

                consecutive_errors = 0

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

                    total_pnl = status['realized_pnl'] + status['unrealized_pnl']
                    heartbeat_msg = (
                        f"{mode_prefix}HEARTBEAT | {status['state']} | "
                        f"SPX: {status['underlying_price']:.2f} | "
                        f"VIX: {status['vix']:.2f} | "
                        f"Entries: {status['entries_completed']}/{len(strategy.entry_times)} | "
                        f"Active: {status['active_entries']} | "
                        f"Trend: {status.get('current_trend', 'N/A')}"
                    )
                    trade_logger.log_event(heartbeat_msg)

                    try:
                        position_lines = strategy.get_detailed_position_status()
                        for line in position_lines:
                            trade_logger.log_event(line)
                    except Exception as e:
                        trade_logger.log_error(f"Failed to get position status: {e}")

                    # Visual P&L bar
                    bar_width = 50
                    commission = status.get('total_commission', 0)
                    net_pnl = total_pnl - commission
                    net_sign = "+" if net_pnl >= 0 else ""
                    pnl_text = f"  {net_sign}${net_pnl:.2f} net (${commission:.0f} comm)  "
                    pnl_len = len(pnl_text)
                    left_len = (bar_width - pnl_len) // 2
                    right_len = bar_width - pnl_len - left_len
                    trade_logger.log_event(f"[{'▓' * bar_width}]")
                    trade_logger.log_event(f"[{'░' * left_len}{pnl_text}{'░' * right_len}]")
                    trade_logger.log_event(f"[{'▓' * bar_width}]")

                    # Risk & return metrics line
                    capital = status.get('capital_deployed', 0)
                    max_loss_s = status.get('max_loss_stops', 0)
                    max_loss_c = status.get('max_loss_catastrophic', 0)
                    return_pct = (net_pnl / capital * 100) if capital > 0 else 0
                    return_sign = "+" if return_pct >= 0 else ""
                    trade_logger.log_event(
                        f"  Capital: ${capital:,.0f} | "
                        f"Max Loss: ${max_loss_s:,.0f} (stops) / ${max_loss_c:,.0f} (no stops) | "
                        f"Return: {return_sign}{return_pct:.1f}%"
                    )

                    # MKT-018: Early close ROC tracking
                    ec_status = status.get('early_close_status', {})
                    if ec_status.get('tracking'):
                        roc_current = ec_status.get('roc', 0)
                        roc_threshold = ec_status.get('threshold', 0.02)
                        roc_sign = "+" if roc_current >= 0 else ""
                        close_cost = ec_status.get('close_cost', 0)
                        trade_logger.log_event(
                            f"  Early Close: ROC {roc_sign}{roc_current*100:.2f}% / "
                            f"{roc_threshold*100:.1f}% threshold | "
                            f"Close cost: ${close_cost:.0f} ({ec_status.get('active_legs', 0)} legs)"
                        )
                    elif ec_status.get('triggered'):
                        trade_logger.log_event(
                            f"  Early Close: TRIGGERED at {ec_status.get('trigger_time', 'N/A')} | "
                            f"Locked P&L: ${ec_status.get('locked_pnl', 0):.2f}"
                        )

                    strategy.log_account_summary()
                    strategy.log_performance_metrics()
                    strategy.log_position_snapshot()
                    last_status_time = now

                # Hourly Bot Logs
                if (now - last_bot_log_time).total_seconds() >= bot_log_interval:
                    try:
                        status = strategy.get_status_summary()
                        ema_info = ""
                        if status.get('ema_short', 0) > 0:
                            diff_sign = "+" if status.get('ema_diff_pct', 0) >= 0 else ""
                            ema_info = f", EMA20={status['ema_short']:.0f}/EMA40={status['ema_long']:.0f} ({diff_sign}{status['ema_diff_pct']*100:.2f}%)"
                        trade_logger.log_bot_activity(
                            level="INFO",
                            component="MEICTFStrategy",
                            message=f"Hourly: State={status['state']}, Entries={status['entries_completed']}, Trend={status.get('current_trend', 'N/A')}{ema_info}, P&L=${status['realized_pnl'] + status['unrealized_pnl']:.2f}",
                            spy_price=status['underlying_price'],
                            vix=status['vix'],
                            flush=True
                        )
                        last_bot_log_time = now
                    except Exception as e:
                        trade_logger.log_error(f"Hourly bot log error: {e}")

                # Sleep until next check
                status = strategy.get_status_summary()
                if status['state'] == 'DailyComplete' and status['active_entries'] == 0:
                    if not interruptible_sleep(60):
                        break
                elif status['active_entries'] > 0:
                    recommended_interval = strategy.get_recommended_check_interval()
                    if not interruptible_sleep(recommended_interval):
                        break
                else:
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
                        "event_type": "MEIC_TF_CONSECUTIVE_ERRORS",
                        "spy_price": strategy.current_price,
                        "vix": strategy.current_vix,
                        "description": f"Main loop has {consecutive_errors} consecutive errors",
                        "result": "Continuing but system may be unstable"
                    })
                    logger.critical(f"CRITICAL: {consecutive_errors} consecutive errors in main loop!")

                if not interruptible_sleep(check_interval):
                    break

    finally:
        trade_logger.log_event("=" * 60)
        trade_logger.log_event("INITIATING GRACEFUL SHUTDOWN")
        trade_logger.log_event("=" * 60)

        # FIX #75: Wait for async fill corrections before shutdown
        if strategy is not None:
            strategy._wait_for_pending_fill_corrections(timeout=15.0)

        try:
            if strategy is not None:
                status = strategy.get_status_summary()
                state = status.get('state', 'Unknown')
                entries = status.get('entries_completed', 0)
                realized = status.get('realized_pnl', 0)
                unrealized = status.get('unrealized_pnl', 0)
                active = status.get('active_entries', 0)

                trade_logger.log_event(
                    f"Final Status: State={state}, "
                    f"Entries={entries}, "
                    f"P&L=${realized + unrealized:.2f}"
                )

                if active > 0:
                    spy_price = status.get('underlying_price', 0)
                    vix = status.get('vix', 0)
                    logger.critical(
                        f"CRITICAL: Bot shutting down with {active} ACTIVE positions! "
                        f"P&L: ${realized + unrealized:.2f}"
                    )
                    trade_logger.log_event(
                        f"WARNING: Bot shutting down with {active} ACTIVE positions! "
                        "Manual intervention may be required."
                    )
                    trade_logger.log_safety_event({
                        "event_type": "MEIC_TF_SHUTDOWN_WITH_POSITION",
                        "spy_price": spy_price,
                        "vix": vix,
                        "description": f"Bot shutdown with {active} active positions",
                        "result": "Positions left open - MANUAL INTERVENTION REQUIRED"
                    })
            else:
                trade_logger.log_event("Strategy was not initialized - no final status available")
        except Exception as e:
            trade_logger.log_error(f"Error during shutdown status reporting: {e}")

        trade_logger.log_event("Shutdown complete.")
        trade_logger.shutdown()


def show_status(config: dict):
    """Show current status without entering trading loop."""
    trade_logger = setup_logging(config, bot_name="MEIC-TF")
    client = SaxoClient(config)

    if not client.authenticate():
        print("Failed to authenticate. Please check your credentials.")
        trade_logger.shutdown()
        return

    try:
        strategy = MEICTFStrategy(client, config, trade_logger)
        strategy.update_market_data()
        status = strategy.get_status_summary()

        print("\n" + "=" * 60)
        print("MEIC-TF CURRENT STATUS")
        print("=" * 60)
        print(f"  State: {status['state']}")
        print(f"  SPX Price: {status['underlying_price']:.2f}")
        print(f"  VIX: {status['vix']:.2f}")

        # Show trend info with EMA values
        trend = status.get('current_trend', 'N/A').upper()
        ema_short = status.get('ema_short', 0)
        ema_long = status.get('ema_long', 0)
        ema_diff = status.get('ema_diff_pct', 0)
        if ema_short > 0 and ema_long > 0:
            diff_sign = "+" if ema_diff >= 0 else ""
            print(f"  Current Trend: {trend}")
            print(f"    EMA 20: {ema_short:.2f}")
            print(f"    EMA 40: {ema_long:.2f}")
            print(f"    Difference: {diff_sign}{ema_diff*100:.3f}% (threshold: ±{strategy.ema_neutral_threshold*100:.1f}%)")
        else:
            print(f"  Current Trend: {trend} (EMA not yet calculated)")

        print("\n  Entry Schedule:")
        for i, entry_time in enumerate(strategy.entry_times):
            completed = i < status['entries_completed']
            marker = "✓" if completed else "○"
            print(f"    {marker} Entry #{i+1}: {entry_time.strftime('%H:%M')} ET")

        print("\n  Today's Stats:")
        print(f"    Entries Completed: {status['entries_completed']}")
        print(f"    Entries Failed: {status['entries_failed']}")
        print(f"    Active Positions: {status['active_entries']}")
        print(f"    Total Credit: ${status['total_credit']:.2f}")
        print(f"    Realized P&L: ${status['realized_pnl']:.2f}")
        print(f"    Unrealized P&L: ${status['unrealized_pnl']:.2f}")
        print(f"    Total P&L: ${status['realized_pnl'] + status['unrealized_pnl']:.2f}")
        print(f"    Stops Triggered: {status['total_stops']}")

        print("=" * 60 + "\n")
    except Exception as e:
        print(f"Error getting status: {e}")
    finally:
        trade_logger.shutdown()


def main():
    """Main entry point."""
    kill_existing_instances()

    parser = argparse.ArgumentParser(
        description="MEIC-TF 0DTE Trading Bot - Trend Following Hybrid",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m bots.meic_tf.main              Run in SIM environment
  python -m bots.meic_tf.main --live       Run in LIVE environment
  python -m bots.meic_tf.main --dry-run    Simulate without orders
  python -m bots.meic_tf.main --status     Show current status only
        """
    )

    parser.add_argument(
        "--config", "-c",
        default="bots/meic_tf/config/config.json",
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

    print_banner()

    try:
        config = load_config(args.config)

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

        if args.verbose:
            config["logging"]["log_level"] = "DEBUG"

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        dry_run = args.dry_run or config.get("dry_run", False)

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
        print("  Make sure config file exists at: bots/meic_tf/config/config.json")
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
