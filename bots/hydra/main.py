#!/usr/bin/env python3
"""
main.py - HYDRA 0DTE Trading Bot Entry Point

HYDRA: Multi-entry iron condor bot with credit gates, progressive OTM tightening,
VIX regime adaptation, and buffer decay stops. Based on Tammy Chambless's MEIC
strategy with EMA-based trend detection (informational) and advanced risk controls.

Strategy Summary:
-----------------
1. 2 effective base entries at 10:45, 11:15 ET (E#1 at 10:15 dropped at ALL VIX levels
   via regime max_entries [2,2,2,1]) + conditional E6 at 14:00 firing as put-only on
   up days (Upday-035, +0.25%) or call-only on down days (Downday-035, −0.25%).
   FOMC T+1 days skipped entirely (fomc_t1_skip_enabled).
2. EMA 20/40 trend signal logged per entry (informational only)
3. Credit gate validates minimum credit before each entry (VIX-regime-dependent;
   base fallback $2.00 call / $2.75 put is effectively dead — regime floors apply)
4. Progressive OTM tightening finds optimal strikes (MKT-020/MKT-022)
5. Hold-to-expiry (MKT-018 early close intentionally disabled)
6. Stop loss closes both legs (total_credit + asymmetric buffer: call $0.75, put $1.75,
   with MKT-042 buffer decay 2.50× → 1× over 4h and MKT-046 10-second anti-spike filter)

Usage:
------
    python -m bots.hydra.main              # Connect to IBKR paper, place real paper orders
    python -m bots.hydra.main --dry-run    # Connect to IBKR paper, simulate orders only
    python -m bots.hydra.main --status     # Show current status and exit

This branch is IBKR-paper only. The legacy `--live` flag is a no-op
(retained for CLI back-compat).

See docs/HYDRA_STRATEGY_SPECIFICATION.md for full HYDRA details.
See docs/migration/HYDRA_STANDALONE_REWRITE_PLAN.md for the Saxo→IBKR
migration history.
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
from shared.ib_client import (
    IBClient, IBConfig, IBClientError, IBAuthError, IBConnectionError,
)
from shared.ib_oauth import load_credentials
from shared.broker_client import BrokerClient, BrokerError
from shared.logger_service import setup_logging
from shared.market_hours import (
    is_market_open, get_market_status_message, calculate_sleep_duration,
    get_holiday_name, get_us_market_time, is_weekend
)
from shared.config_loader import ConfigLoader
from shared.secret_manager import is_running_on_gcp
from shared.alert_service import AlertType, AlertPriority
# Polish Item 1: Telegram alert hooks for IBKR-specific failure modes.
from bots.hydra.alert_hooks import IBKRAlertHooks

# Import bot-specific strategy
from bots.hydra.strategy import HydraStrategy

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


def _read_proc_variant_id(pid: int):
    """
    Read another HYDRA process's HYDRA_VARIANT_ID from /proc/<pid>/environ.

    Returns the variant ID (lowercased, stripped, or None for variant A).
    Returns the sentinel "?" if we can't determine the variant — caller
    should treat that as "don't kill" to be safe (skip rather than risk
    cross-variant termination).
    """
    try:
        with open(f"/proc/{pid}/environ", "rb") as f:
            env_bytes = f.read()
    except (FileNotFoundError, PermissionError, OSError):
        return "?"

    for kv in env_bytes.split(b"\0"):
        if kv.startswith(b"HYDRA_VARIANT_ID="):
            value = kv[len(b"HYDRA_VARIANT_ID="):].decode(errors="replace").strip().lower()
            return value or None
    return None  # env var not set → variant A


def kill_existing_instances() -> int:
    """
    DUPLICATE-001: Find and kill any existing HYDRA bot instance with the SAME
    variant ID as this process. Skips other variants so A and B can coexist.
    """
    current_pid = os.getpid()
    my_variant = (os.environ.get("HYDRA_VARIANT_ID", "") or "").strip().lower() or None
    killed_count = 0

    try:
        result = subprocess.run(
            ["pgrep", "-f", "hydra[./]main\\.py"],
            capture_output=True,
            text=True
        )

        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split('\n')

            for pid_str in pids:
                try:
                    pid = int(pid_str.strip())
                    if pid == current_pid:
                        continue

                    other_variant = _read_proc_variant_id(pid)
                    if other_variant == "?":
                        logger.info(
                            f"DUPLICATE-001: Cannot determine variant of PID {pid} — skipping (safe default)"
                        )
                        continue

                    if other_variant != my_variant:
                        logger.info(
                            f"DUPLICATE-001: PID {pid} is variant '{other_variant or 'A'}', "
                            f"we are '{my_variant or 'A'}' — leaving it alone"
                        )
                        continue

                    logger.info(
                        f"DUPLICATE-001: Found same-variant HYDRA (PID: {pid}, variant={my_variant or 'A'}), terminating..."
                    )
                    os.kill(pid, signal.SIGTERM)
                    killed_count += 1
                    time.sleep(1)
                except (ValueError, ProcessLookupError):
                    pass

        if killed_count > 0:
            logger.info(f"DUPLICATE-001: Terminated {killed_count} existing HYDRA instance(s)")
            time.sleep(2)

    except FileNotFoundError:
        logger.warning("DUPLICATE-001: pgrep not available - cannot check for existing instances")
    except Exception as e:
        logger.warning(f"DUPLICATE-001: Error checking for existing instances: {e}")

    return killed_count


def load_config(config_path: str = "bots/hydra/config/config.json") -> dict:
    """Load configuration from appropriate source (cloud or local)."""
    loader = ConfigLoader(config_path)
    config = loader.load_config()
    return config


def _variant_bot_name() -> str:
    """Derive the monitor-log bot_name from HYDRA_VARIANT_ID so the shared
    logs/monitor.log can attribute each line to the variant that emitted it.

    All three variants (A/B/C) append to the SAME logs/monitor.log, where each
    line is tagged with the bot_name column. Variant A leaves HYDRA_VARIANT_ID
    unset (-> "HYDRA"); B/C set it (hydra_variant_b/c.service) -> "HYDRA_B" /
    "HYDRA_C". Mirrors the variant read at kill_existing_instances():125.
    """
    variant = (os.environ.get("HYDRA_VARIANT_ID", "") or "").strip().lower()
    return f"HYDRA_{variant.upper()}" if variant else "HYDRA"


def print_banner():
    """Print the application banner."""
    banner = """
    ╔═══════════════════════════════════════════════════════════════╗
    ║                                                               ║
    ║         HYDRA 0DTE TRADING BOT                                ║
    ║         ══════════════════════                                ║
    ║                                                               ║
    ║         Multi-Entry Iron Condors (SPX 0DTE)                   ║
    ║         3 Entries | Credit Gates | Buffer Decay               ║
    ║                                                               ║
    ║         #1: 10:45 ET (base)                                   ║
    ║         #2: 11:15 ET (base)                                   ║
    ║         #3: 14:00 ET (conditional Up/Down-day)                ║
    ║                                                               ║
    ║         Version: 2.0.0-rc.1 (IBKR-standalone)                 ║
    ║         Broker: Interactive Brokers (paper)                   ║
    ║                                                               ║
    ╚═══════════════════════════════════════════════════════════════╝
    """
    print(banner)


def _build_broker():
    """Construct the broker the strategy talks to.

    If CALYPSO_BROKER_URL is set, return a BrokerClient that proxies to the
    shared calypso-broker service (which owns the ONE IBKR session — see
    docs/migration/BROKER_SESSION_SERVICE_DESIGN.md), so this process opens NO
    IBKR session of its own. This is how A/B/C run concurrently without the
    one-session-per-username eviction war. Otherwise (unset), return a direct
    IBClient that owns its own session (legacy / single-bot mode). Either object
    is a drop-in: it answers connect()/ensure_connected() + the 16 data methods.
    """
    broker_url = os.environ.get("CALYPSO_BROKER_URL")
    if broker_url:
        return BrokerClient(broker_url)
    return IBClient(IBConfig(credentials=load_credentials("paper")))


def run_bot(config: dict, dry_run: bool = False, check_interval: int = 1, config_path: str = "bots/hydra/config/config.json"):
    """Run the main trading bot loop."""
    global shutdown_requested

    # Initialize logging service. bot_name is variant-derived so the shared
    # monitor.log can attribute each line to A/B/C (was hardcoded "HYDRA").
    trade_logger = setup_logging(config, bot_name=_variant_bot_name())
    trade_logger.log_event("=" * 60)
    trade_logger.log_event("HYDRA BOT STARTING")
    trade_logger.log_event(f"Mode: {'DRY RUN (Simulation)' if dry_run else 'LIVE TRADING'}")
    trade_logger.log_event(f"Check Interval: {check_interval} seconds")
    trade_logger.log_event("=" * 60)

    # Initialize the IBKR broker client (paper account — never live).
    # P7-audit H4: IBClient.connect() returns True or RAISES — it never
    # returns False — so a bare `if not broker.connect()` is dead code.
    # Catch the real exception, shut the logger down cleanly, exit.
    trade_logger.log_event("Connecting to Interactive Brokers (paper)...")
    try:
        broker = _build_broker()
    except Exception as e:
        trade_logger.log_error(f"Failed to build broker client: {e}")
        trade_logger.shutdown()
        return

    # 2026-06-03 incident fix: in BROKER mode the bot WAITS for the shared
    # calypso-broker instead of crashing. The broker self-heals in its own
    # maintenance loop after an IBKR session-bridge blip; restarting THIS bot
    # can't fix a broker-side fault and just crash-loops into systemd's
    # StartLimit (which took all bots down today). A BrokerError at startup
    # means the broker isn't holding a session yet — wait + retry rather than
    # exit. LEGACY direct-IBClient mode keeps fail-fast (its connect failure is
    # a genuine auth problem and a fresh-connect restart re-auths).
    _is_broker_mode = isinstance(broker, BrokerClient)
    _connect_attempts = 0
    _CONNECT_MAX_ATTEMPTS = 48  # ~12 min at 15s, then give up (systemd backstop)
    while True:
        try:
            broker.connect()
            break
        except BrokerError as e:
            if not _is_broker_mode or _connect_attempts >= _CONNECT_MAX_ATTEMPTS:
                trade_logger.log_error(f"Failed to connect to IBKR broker: {e}")
                trade_logger.shutdown()
                return
            _connect_attempts += 1
            trade_logger.log_event(
                f"calypso-broker not holding a session yet ({e}) — waiting 15s "
                f"+ retrying (attempt {_connect_attempts}/{_CONNECT_MAX_ATTEMPTS}); not exiting."
            )
            if not interruptible_sleep(15):
                trade_logger.shutdown()
                return
        except (IBAuthError, IBConnectionError, IBClientError) as e:
            trade_logger.log_error(f"Failed to connect to IBKR: {e}")
            trade_logger.shutdown()
            return

    trade_logger.log_event("IBKR connection successful!")

    strategy = None
    try:
        # Item 4a: strategy selected by name via the registry (was a hardcoded
        # if brandon.enabled / else). resolve_strategy_name preserves the legacy
        # precedence (brandon.enabled wins, else strategy.name, else "hydra"), so
        # this is behavior-identical for the current config.
        from bots.hydra.registry import build_strategy, resolve_strategy_name
        selected = resolve_strategy_name(config)
        trade_logger.log_event(f"Loading strategy: {selected}")
        strategy = build_strategy(config, broker, trade_logger, dry_run=dry_run)
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
        # Startup/intraday snapshot → default period "Intraday" (Sheets-throttled).
        # The settlement caller passes period="End of Day" (throttle-exempt); see
        # the after-hours settlement block below.
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

        # AUD5: only claim "logged to Google Sheets" when Sheets is actually
        # enabled (dry-run shadows have it off — the write is a no-op).
        if trade_logger.google_logger.enabled:
            trade_logger.log_event("Dashboard metrics logged to Google Sheets")
        else:
            trade_logger.log_event("Dashboard metrics updated (Sheets disabled — local/DB only)")
    except Exception as e:
        trade_logger.log_error(f"Failed to log startup dashboard metrics: {e}")

    # L-M10: post-connect instrument resolvability probe. _assert_instrument_
    # parameterized only checks truthiness; a stale-but-non-empty symbol (e.g. a
    # leftover Saxo ticker like "US500.I") passes it and then silently fails at
    # IBKR resolution — the bot then runs BLIND and skips every trade. If the
    # underlying price is still 0/None after the startup market-data fetch
    # DURING market hours, treat it as an unresolvable-instrument signal: alert
    # CRITICAL and refuse to enter the trading loop (loud startup failure beats a
    # silent runtime blind-spot). Outside market hours a 0 is normal → warn only.
    try:
        from shared.market_hours import is_market_open as _mkt_open
        _px = getattr(strategy, "current_price", 0) or 0
        _sym = getattr(strategy, "underlying_symbol", "?")
        _exch = getattr(strategy, "exchange", "?")
        if _px <= 0:
            if _mkt_open():
                msg = (
                    f"Underlying {_sym} returned NO price at startup during market "
                    f"hours — the symbol may be stale/unresolvable at the broker "
                    f"(exchange={_exch}). Refusing to trade blind."
                )
                logger.critical(f"L-M10: {msg}")
                try:
                    strategy.alert_service.send_alert(
                        alert_type=AlertType.DATA_QUALITY,
                        title="HYDRA refusing to start — underlying unresolvable",
                        message=msg,
                        priority=AlertPriority.CRITICAL,
                    )
                except Exception:
                    pass
                trade_logger.shutdown()
                return
            else:
                logger.warning(
                    f"L-M10: underlying {_sym} has no price at startup (market "
                    f"closed — normal); will re-verify once the session opens."
                )
        else:
            logger.info(f"L-M10: instrument probe OK — {_sym} priced at {_px:.2f}")
    except Exception as _probe_exc:
        logger.warning(f"L-M10 instrument probe skipped (non-fatal): {_probe_exc}")

    # Send BOT_STARTED alert. The contracts= kwarg auto-prefixes title with
    # [{N}c] when > 1, so the manual prefix used in Phase 1 T-3 is removed —
    # single source of truth at the service layer (avoids double-prefixing).
    try:
        mode = "DRY-RUN" if dry_run else "LIVE"
        status = strategy.get_status_summary()
        contracts = getattr(strategy, 'contracts_per_entry', 1)
        scale_note = (
            f" \u26a0\ufe0f  all P&L/credit/stop figures are scaled \u00d7{contracts}"
            if contracts > 1 else ""
        )
        strategy.alert_service.send_alert(
            alert_type=AlertType.BOT_STARTED,
            title=f"Bot Started ({mode})",
            message=(
                f"HYDRA started in {mode} mode.\n"
                f"Contracts/entry: {contracts}{scale_note}\n"
                f"State: {status.get('state', 'Unknown')}, "
                f"Entries today: {status.get('entries_completed', 0)}"
            ),
            priority=AlertPriority.LOW,
            contracts=contracts,
        )
    except Exception as e:
        trade_logger.log_error(f"Failed to send BOT_STARTED alert: {e}")

    # Initialize Telegram command handler (16 commands incl. /compare)
    from bots.hydra.telegram_commands import TelegramCommandHandler
    telegram_cmd_handler = TelegramCommandHandler()
    try:
        telegram_cmd_handler.start(
            snapshot_callback=strategy.build_telegram_snapshot,
            lastday_callback=strategy.build_telegram_lastday,
            account_callback=strategy.build_telegram_account,
            status_callback=strategy.build_telegram_status,
            hermes_callback=strategy.build_telegram_hermes,
            apollo_callback=strategy.build_telegram_apollo,
            clio_callback=strategy.build_telegram_clio,
            week_callback=strategy.build_telegram_week,
            entry_callback=strategy.build_telegram_entry,
            stops_callback=strategy.build_telegram_stops,
            config_callback=strategy.build_telegram_config,
            compare_callback=strategy.build_telegram_compare,
            config_path=config_path,
            active_positions_callback=lambda: len(strategy.daily_state.active_entries),
        )
    except Exception as e:
        trade_logger.log_error(f"Failed to start Telegram command handler: {e}")

    # REST-only mode
    trade_logger.log_event("REST-only mode: WebSocket streaming disabled")
    trade_logger.log_event("All price fetching will use REST API directly")

    # Main trading loop
    trade_logger.log_event("Entering main trading loop...")
    trade_logger.log_event("Press Ctrl+C to stop the bot gracefully")
    trade_logger.log_event("-" * 60)

    last_status_time = datetime.now()
    # Heartbeat cadence — drives spread_snapshots, account summary, state file
    # write, and cushion bar updates. Variants pace this looser via
    # api_pacing_multiplier (variant A = 1.0 → 10s; B at 2.0 → 20s; C at 2.0 → 20s).
    # Account summary calls the IBKR balance endpoint every cycle so this is
    # one of the API-load levers when running 3+ variants.
    pacing = float(getattr(strategy, "api_pacing_multiplier", 1.0) or 1.0)
    status_interval = max(10, int(round(10 * pacing)))
    last_bot_log_time = datetime.now()
    bot_log_interval = 3600
    last_snapshot_time = None  # Telegram snapshot — starts after first entry
    snapshot_interval = 1800   # 30 minutes
    last_day = get_us_market_time().date()
    consecutive_errors = 0
    daily_summary_sent_date = None
    last_session_check_date = None  # P7: once-per-day IBKR re-auth gate
    last_intraday_session_check = datetime.min  # P7-audit H6: periodic re-check
    INTRADAY_SESSION_CHECK_INTERVAL_S = 15 * 60  # 15 min
    # 2026-06-03: in broker mode, ride out a transiently-down broker (it
    # self-heals) by pausing trading + re-checking, instead of exiting into a
    # StartLimit crash-loop. Track the outage so we alert/log ONCE per blip.
    broker_outage_active = False
    BROKER_OUTAGE_RECHECK_S = 20  # re-check cadence while waiting for the broker

    # Polish Item 1: IBKR-specific Telegram alert bridge. Polls the broker's
    # circuit-breaker state + snapshot warmup exhaustion counter once per
    # iteration. Idempotent per state transition (each CLOSED→OPEN fires
    # ONE alert; stuck-OPEN reminders every 15 min, capped 4/day per
    # family). main.py calls on_ensure_connected_failed() at both
    # ensure_connected() gates so the operator doesn't see the bot
    # vanish silently.
    alert_hooks = IBKRAlertHooks(broker, strategy.alert_service)
    trade_logger.log_event("IBKR alert hooks initialized (breaker / warmup / re-auth)")

    try:
        while not shutdown_requested:
            try:
                # Check for new trading day
                today = get_us_market_time().date()
                if today != last_day:
                    trade_logger.log_event("New trading day detected - resetting strategy")
                    strategy._reset_for_new_day()
                    last_day = today
                    last_snapshot_time = None
                    # Polish Item 1: reset per-day alert flags (first-of-day
                    # warmup-exhaustion alert + 25+ severe alert + per-day
                    # stuck-OPEN reminder counts).
                    alert_hooks.mark_new_day()

                # Polish Item 1: poll IBKR-specific signals → Telegram on
                # state transitions. Cheap when nothing changes (dict +
                # int comparison per family). Wrapped in its own
                # exception-handler inside the hook so a bug here cannot
                # propagate.
                alert_hooks.poll()

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
                    # FIX #39: Check settlement until complete, not just during 4-5 PM window.
                    # 0DTE options settle after the close, so we keep checking until
                    # settlement_complete returns True (all positions cleared).
                    today_date = now_et.date()
                    if not is_weekend() and daily_summary_sent_date != today_date:
                        try:
                            settlement_complete = strategy.check_after_hours_settlement()
                        except Exception as e:
                            trade_logger.log_error(f"Settlement check failed: {e}")
                            settlement_complete = False

                        if not settlement_complete:
                            trade_logger.log_event("Settlement pending - positions still open")
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

                            # PHANTOM-SUMMARY GUARD (06-03 incident): if the bot
                            # was down at the 9:30 open and started mid-session, it
                            # can reach the close still holding the PRIOR day's
                            # in-memory state with no live price captured for today.
                            # A summary built from it records yesterday's entries/P&L
                            # under today's date (spx_close=0) — the phantom row.
                            # Use the RESOLVED close (recovers the day's last
                            # recorded tick when the live current_price has gone
                            # to 0 at a late after-hours write — e.g. C's 0DTE
                            # settlement completing ~6h after the close). Without
                            # this, a legitimate traded day was mis-flagged as a
                            # phantom and its summary+metrics silently skipped
                            # (the 2026-06-05→11 C recording gap).
                            _resolve = getattr(strategy, "_resolve_spx_close", None)
                            _spx_close = _resolve() if callable(_resolve) else strategy.current_price
                            _summary_stale = strategy._daily_summary_is_stale(
                                getattr(strategy.daily_state, "date", "") or "",
                                now_et.strftime("%Y-%m-%d"),
                                _spx_close,
                            )

                            if (had_trading_activity or is_after_market_close) and _summary_stale:
                                # Skip the WHOLE summary (DB + Sheets + metrics) and
                                # lock the gate so we don't retry the stale write all
                                # evening. A clean row records after the next reset.
                                trade_logger.log_error(
                                    "Daily summary SKIPPED — stale prior-day state "
                                    f"(state_date={getattr(strategy.daily_state, 'date', '') or 'unset'}, "
                                    f"today={now_et.strftime('%Y-%m-%d')}, spx_close={strategy.current_price}). "
                                    "Avoids a phantom summary; resets at the next day boundary."
                                )
                                daily_summary_sent_date = today_date
                            elif had_trading_activity or is_after_market_close:
                                trade_logger.log_event("Settlement complete - sending daily summary...")
                                try:
                                    strategy.log_daily_summary()
                                    strategy._record_daily_summary_to_db()
                                    # Fix #65: Also log post-settlement account summary and performance metrics
                                    # These were previously only logged during market hours heartbeat (pre-settlement),
                                    # meaning the final values with settled P&L were never recorded
                                    # I-M1: force=True so the settled-state
                                    # account-summary + position-snapshot writes
                                    # bypass the intraday Sheets-write throttle —
                                    # otherwise the final daily record could be
                                    # silently dropped (DB stays authoritative).
                                    strategy.log_account_summary(force=True)
                                    # AUD5 C-3: pass an EXEMPT period so the
                                    # settled-P&L write bypasses the intraday
                                    # Sheets-write throttle (heartbeat callers
                                    # keep the default "Intraday").
                                    strategy.log_performance_metrics(period="End of Day")
                                    strategy.log_position_snapshot(force=True)
                                except Exception as e:
                                    trade_logger.log_error(f"Failed to log daily summary: {e}")
                                daily_summary_sent_date = today_date
                                # AUD5: accurate event text — dry-run shadows
                                # have Sheets + alerts off (the sends are no-ops).
                                if trade_logger.google_logger.enabled:
                                    trade_logger.log_event("Daily summary sent to Google Sheets and alerts")
                                else:
                                    trade_logger.log_event("Daily summary recorded (Sheets/alerts disabled — local/DB only)")
                            else:
                                # FIX #82: Do NOT lock the settlement gate pre-market when there's no activity.
                                # At midnight ET, _reset_for_new_day() clears the registry, then settlement
                                # finds nothing and returns True. If we set daily_summary_sent_date here,
                                # the gate stays locked ALL DAY, preventing post-market settlement at 4 PM.
                                # Only lock the gate after market close (is_after_market_close handled above).
                                trade_logger.log_event("Settlement complete - no trading activity yet (pre-market), gate stays open")

                    sleep_time = calculate_sleep_duration(max_sleep=900)
                    market_open_time = now_et.replace(hour=9, minute=30, second=0, microsecond=0)

                    if now_et < market_open_time and now_et.weekday() < 5 and not holiday_name:
                        seconds_until_open = (market_open_time - now_et).total_seconds()
                        if seconds_until_open > 0 and seconds_until_open < sleep_time:
                            sleep_time = int(seconds_until_open)
                            trade_logger.log_event(f"Pre-market: will wake at 9:30 AM ({sleep_time}s)")

                    if sleep_time > 0:
                        minutes = sleep_time // 60
                        # IBKR session keepalive is handled by IBClient's
                        # Tickler thread; no manual token refresh needed.
                        trade_logger.log_event(f"HEARTBEAT | Market closed {close_reason} - sleeping for {minutes}m")

                        if not interruptible_sleep(sleep_time):
                            break
                    else:
                        trade_logger.log_event(f"HEARTBEAT | Market closed {close_reason} - rechecking in 60s")
                        if not interruptible_sleep(60):
                            break
                    continue

                # P7: morning IBKR re-auth gate. The brokerage session does
                # NOT survive IBKR's ~01:00 ET daily server reset or the 24h
                # live-session-token TTL — ibind's Tickler only holds off the
                # idle timeout. Verify (and, if stale, re-establish) the
                # session once per trading day before any entry. On failure,
                # break the loop so systemd restarts with a fresh connect().
                if last_session_check_date != today:
                    trade_logger.log_event("Verifying IBKR session for the trading day...")
                    if not broker.ensure_connected():
                        if _is_broker_mode:
                            # 2026-06-03: broker self-heals in its own loop;
                            # restarting THIS bot can't fix it. Pause trading +
                            # wait (re-check each loop) instead of exiting into a
                            # StartLimit crash-loop. Alert/log ONCE per outage.
                            if not broker_outage_active:
                                broker_outage_active = True
                                alert_hooks.on_ensure_connected_failed(
                                    reason="morning daily re-auth gate"
                                )
                                trade_logger.log_error(
                                    "Broker session unavailable (morning re-auth "
                                    "gate) — pausing trading and waiting for the "
                                    "broker to self-heal (NOT exiting)."
                                )
                            interruptible_sleep(BROKER_OUTAGE_RECHECK_S)
                            continue
                        # Legacy direct-IBClient: a fresh-connect restart re-auths.
                        alert_hooks.on_ensure_connected_failed(
                            reason="morning daily re-auth gate"
                        )
                        trade_logger.log_error(
                            "IBKR session could not be re-established — "
                            "exiting for systemd restart (fresh connect)."
                        )
                        break
                    if broker_outage_active:
                        broker_outage_active = False
                        trade_logger.log_event("Broker session recovered — resuming trading.")
                    last_session_check_date = today
                    last_intraday_session_check = datetime.now()
                    trade_logger.log_event("IBKR session verified.")

                # P7-audit H6: intraday session re-check. The daily gate
                # above only fires once/day, so a mid-session 401/410
                # (the 24h LST TTL elapsing mid-day, IBKR-side restart,
                # a competing login) would silently leave the bot
                # trading on a dead session. Re-verify every 15 minutes via
                # ensure_connected(). Behavior depends on the broker object:
                #   - legacy direct IBClient (CALYPSO_BROKER_URL unset):
                #     ensure_connected() itself round-trips auth/status (one
                #     cheap call if healthy) and re-establishes if not.
                #   - deployed BrokerClient (CALYPSO_BROKER_URL set): it GETs the
                #     broker's /health, which is now AUTHORITATIVE — the broker
                #     performs a LIVE check_auth_status() round-trip per call
                #     (audit #13), reporting connected only when the session is
                #     authenticated AND connected AND not competing (5s cached).
                #     Session RE-ESTABLISH still happens in calypso-broker's own
                #     maintenance loop, not here. Either way a dead session
                #     reports connected=False here, so this gate fails CLOSED and
                #     breaks for restart.
                now_ = datetime.now()
                if (now_ - last_intraday_session_check).total_seconds() >= INTRADAY_SESSION_CHECK_INTERVAL_S:
                    if not broker.ensure_connected():
                        if _is_broker_mode:
                            # 2026-06-03: broker self-heals; restarting THIS bot
                            # can't fix it and crash-loops StartLimit. Pause +
                            # wait (re-check each loop), don't exit. Alert ONCE.
                            if not broker_outage_active:
                                broker_outage_active = True
                                alert_hooks.on_ensure_connected_failed(
                                    reason="intraday session re-check (every 15 min)"
                                )
                                trade_logger.log_error(
                                    "Broker session unavailable (intraday re-check) "
                                    "— pausing trading and waiting for the broker to "
                                    "self-heal (NOT exiting)."
                                )
                            interruptible_sleep(BROKER_OUTAGE_RECHECK_S)
                            continue
                        # Legacy direct-IBClient: a fresh-connect restart re-auths.
                        alert_hooks.on_ensure_connected_failed(
                            reason="intraday session re-check (every 15 min)"
                        )
                        trade_logger.log_error(
                            "Intraday IBKR session check failed — "
                            "exiting for systemd restart."
                        )
                        break
                    if broker_outage_active:
                        broker_outage_active = False
                        trade_logger.log_event("Broker session recovered — resuming trading.")
                    last_intraday_session_check = now_

                # Directional-pivot continuous monitor (introduced 2026-05-01 in v1.26.0).
                # Fires BEFORE the strategy state machine on each heartbeat, so a
                # breach detected this tick can close open base entries before the
                # state machine's stop-loss / entry-placement logic runs. Idempotent
                # within the day (won't re-fire after the first trigger). No-op when
                # `directional_pivot.enabled: false` (default — disabled across all
                # variants as of v1.27; new B/C use Brandon GEX-breach exit instead).
                # Wrapped in try/except so a pivot bug never blocks the main loop.
                try:
                    if hasattr(strategy, "_check_directional_pivot_continuous"):
                        strategy._check_directional_pivot_continuous()
                except Exception as e:
                    trade_logger.log_error(f"Directional pivot monitor error (non-fatal): {e}", exception=e)

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
                    # MKT-031: Append scout score to heartbeat if available
                    scout_suffix = ""
                    if 'scout_score' in status:
                        scout_suffix = f" | Scout: {status['scout_score']}/{strategy.scout_score_threshold}"
                    # Use effective count so pre-VIX-regime heartbeats don't show /4
                    _total_entries_display = (
                        strategy._effective_total_entry_count()
                        if hasattr(strategy, "_effective_total_entry_count")
                        else len(strategy.entry_times)
                    )
                    _contracts_display = getattr(strategy, "contracts_per_entry", 1) or 1
                    _contracts_tag = f" | {_contracts_display}c" if _contracts_display > 1 else ""
                    heartbeat_msg = (
                        f"{mode_prefix}HEARTBEAT | {status['state']} | "
                        f"SPX: {status['underlying_price']:.2f} | "
                        f"VIX: {status['vix']:.2f} | "
                        f"Entries: {status['entries_completed']}/{_total_entries_display} | "
                        f"Active: {status['active_entries']} | "
                        f"Trend: {status.get('current_trend', 'N/A')}"
                        f"{_contracts_tag}"
                        f"{scout_suffix}"
                    )
                    trade_logger.log_event(heartbeat_msg)

                    # MKT-031: Detailed scout score breakdown line
                    if 'scout_score' in status:
                        trade_logger.log_event(
                            f"  Scout: {status['scout_score']}/{strategy.scout_score_threshold} "
                            f"[spike={status['scout_spike']}, momentum={status['scout_momentum']}]"
                        )

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
                    pnl_text = f"  {net_sign}${net_pnl:.2f} net (${commission:.2f} comm)  "
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
                        # MKT-023: Hold check preview
                        hc = ec_status.get('hold_check', {})
                        if hc:
                            lean = hc.get('market_lean', '')
                            if lean and lean not in ('ONE_SIDED', 'EQUAL') and 'worst_case_hold_pnl' in hc:
                                decision = "HOLD" if not hc.get('should_close', True) else "CLOSE"
                                wc = hc['worst_case_hold_pnl']
                                cn = hc.get('close_now_pnl', 0)
                                diff = wc - cn
                                diff_sign = "+" if diff >= 0 else ""
                                expire_str = ""
                                if 'all_expire_pnl' in hc:
                                    expire_str = f" | expire=${hc['all_expire_pnl']:.0f}"
                                trade_logger.log_event(
                                    f"  Hold Check: {decision} | close=${cn:.0f} vs hold=${wc:.0f} "
                                    f"({diff_sign}{diff:.0f}){expire_str} | {lean} "
                                    f"(C:{hc.get('avg_call_cushion', 0):.0f}%/P:{hc.get('avg_put_cushion', 0):.0f}%)"
                                )
                    elif ec_status.get('triggered'):
                        trade_logger.log_event(
                            f"  Early Close: TRIGGERED at {ec_status.get('trigger_time', 'N/A')} | "
                            f"Locked P&L: ${ec_status.get('locked_pnl', 0):.2f}"
                        )

                    strategy.log_account_summary()
                    strategy.log_performance_metrics()
                    strategy.log_position_snapshot()
                    strategy._save_state_to_disk()
                    strategy._record_heartbeat_to_db()
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
                            component="HydraStrategy",
                            message=f"Hourly: State={status['state']}, Entries={status['entries_completed']}, Trend={status.get('current_trend', 'N/A')}{ema_info}, P&L=${status['realized_pnl'] + status['unrealized_pnl']:.2f}",
                            spy_price=status['underlying_price'],
                            vix=status['vix'],
                            flush=True
                        )
                        last_bot_log_time = now
                    except Exception as e:
                        trade_logger.log_error(f"Hourly bot log error: {e}")

                # 30-minute Telegram position snapshot
                if (strategy.daily_state.entries_completed > 0 and
                    (last_snapshot_time is None or
                     (now - last_snapshot_time).total_seconds() >= snapshot_interval)):
                    try:
                        snapshot_msg = strategy.build_telegram_snapshot()
                        strategy.alert_service.send_alert(
                            alert_type=AlertType.POSITION_SNAPSHOT,
                            title="Position Snapshot",
                            message=snapshot_msg,
                            priority=AlertPriority.LOW,
                            contracts=getattr(strategy, 'contracts_per_entry', 1),
                        )
                        last_snapshot_time = now
                    except Exception as e:
                        trade_logger.log_error(f"Failed to send Telegram snapshot: {e}")

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
                        "event_type": "HYDRA_CONSECUTIVE_ERRORS",
                        "spy_price": strategy.current_price,
                        "vix": strategy.current_vix,
                        "description": f"Main loop has {consecutive_errors} consecutive errors",
                        "result": "Continuing but system may be unstable"
                    })
                    logger.critical(f"CRITICAL: {consecutive_errors} consecutive errors in main loop!")

                # P7-audit M1: a persistent fault (dead IBKR session, a
                # strategy bug) must not spin forever — break so systemd
                # restarts with a fresh connect(). StartLimitBurst caps
                # the restart loop. 15 ≈ 15s of failures at check_interval=1.
                if consecutive_errors >= 15:
                    trade_logger.log_error(
                        f"{consecutive_errors} consecutive main-loop errors — "
                        f"exiting for a clean systemd restart."
                    )
                    break

                if not interruptible_sleep(check_interval):
                    break

    finally:
        trade_logger.log_event("=" * 60)
        trade_logger.log_event("INITIATING GRACEFUL SHUTDOWN")
        trade_logger.log_event("=" * 60)

        # Stop Telegram command handler
        try:
            telegram_cmd_handler.stop()
        except Exception:
            pass

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
                    # L-M9: the discriminator for "is this a real safety event" is
                    # PLANNED (SIGTERM → shutdown_requested) vs UNEXPECTED (crash/
                    # OOM-kill), NOT dry_run alone. A planned `systemctl restart`
                    # with open positions is normal operation — systemd
                    # (Restart=always) brings the bot back and recovery re-adopts
                    # the positions — so it must not trip the CRITICAL/safety_event
                    # cascade every time (the old code did, on every routine
                    # restart of the live-order paper variant). Only an UNEXPECTED
                    # shutdown of a non-dry-run variant is a genuine safety event.
                    planned = bool(shutdown_requested)
                    is_dry = getattr(strategy, 'dry_run', False)
                    if is_dry:
                        # Dry-run: the "active" positions are SIMULATED — no real
                        # risk regardless of how we stopped. INFO only.
                        logger.info(
                            f"Shutdown with {active} active SIMULATED positions "
                            f"(dry-run, no real risk). P&L: "
                            f"${realized + unrealized:.2f}"
                        )
                    elif planned:
                        # Real positions but a PLANNED restart: systemd restarts +
                        # recovery re-adopts them. Expected — WARNING, no CRITICAL
                        # safety_event, so routine restarts don't cry wolf.
                        logger.warning(
                            f"Planned shutdown with {active} active positions; "
                            f"systemd will restart and recovery will re-adopt them. "
                            f"P&L: ${realized + unrealized:.2f}"
                        )
                        trade_logger.log_event(
                            f"Planned shutdown with {active} active positions "
                            "(systemd restart + recovery expected)."
                        )
                    else:
                        # UNEXPECTED shutdown (crash/kill, no SIGTERM) with real
                        # positions — a genuine safety event: CRITICAL + safety_event.
                        logger.critical(
                            f"CRITICAL: Unexpected shutdown with {active} ACTIVE positions! "
                            f"P&L: ${realized + unrealized:.2f}"
                        )
                        trade_logger.log_event(
                            f"WARNING: Unexpected shutdown with {active} ACTIVE positions! "
                            "Manual intervention may be required."
                        )
                        trade_logger.log_safety_event({
                            "event_type": "HYDRA_SHUTDOWN_WITH_POSITION",
                            "spy_price": spy_price,
                            "vix": vix,
                            "description": f"Unexpected shutdown with {active} active positions",
                            "result": "Positions left open - MANUAL INTERVENTION REQUIRED"
                        })

                # Send BOT_STOPPED alert
                try:
                    reason = "Signal received" if shutdown_requested else "Unexpected shutdown"
                    # L-M9: escalate to HIGH only on an UNEXPECTED shutdown of a
                    # non-dry-run variant with positions open. A planned restart
                    # recovers, and dry-run positions are simulated → LOW (so
                    # routine A/B/C restarts don't fire a HIGH on every stop).
                    escalate = (
                        active > 0
                        and not getattr(strategy, 'dry_run', False)
                        and not bool(shutdown_requested)
                    )
                    priority = AlertPriority.HIGH if escalate else AlertPriority.LOW
                    msg = f"HYDRA stopped. Reason: {reason}\nState: {state}, Entries: {entries}, P&L: ${realized + unrealized:.2f}"
                    if active > 0:
                        msg += f"\n⚠️ {active} ACTIVE positions remaining!"
                    strategy.alert_service.send_alert(
                        alert_type=AlertType.BOT_STOPPED,
                        title="Bot Stopped" if active == 0 else f"Bot Stopped — {active} ACTIVE positions!",
                        message=msg,
                        priority=priority,
                        contracts=getattr(strategy, 'contracts_per_entry', 1),
                    )
                except Exception as e:
                    trade_logger.log_error(f"Failed to send BOT_STOPPED alert: {e}")
            else:
                trade_logger.log_event("Strategy was not initialized - no final status available")
        except Exception as e:
            trade_logger.log_error(f"Error during shutdown status reporting: {e}")

        trade_logger.log_event("Shutdown complete.")
        trade_logger.shutdown()


def show_status(config: dict):
    """Show current status without entering trading loop."""
    trade_logger = setup_logging(config, bot_name=_variant_bot_name())
    # P7-audit H4: connect() raises on failure — it never returns False.
    try:
        broker = _build_broker()
        broker.connect()
    except (IBAuthError, IBConnectionError, IBClientError, BrokerError) as e:
        print(f"Failed to connect to IBKR: {e}")
        trade_logger.shutdown()
        return

    try:
        strategy = HydraStrategy(broker, config, trade_logger)
        strategy.update_market_data()
        status = strategy.get_status_summary()

        print("\n" + "=" * 60)
        print("HYDRA CURRENT STATUS")
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
            time_fmt = '%H:%M:%S' if hasattr(strategy, 'vix_gate_enabled') and strategy.vix_gate_enabled else '%H:%M'
            print(f"    {marker} Entry #{i+1}: {entry_time.strftime(time_fmt)} ET")

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
        description="HYDRA 0DTE Trading Bot — Multi-Entry Iron Condors (IBKR paper)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m bots.hydra.main              Connect to IBKR paper, place real paper orders
  python -m bots.hydra.main --dry-run    Connect to IBKR paper, simulate orders (no placement)
  python -m bots.hydra.main --status     Show current status and exit

The IBKR-standalone branch trades the paper account only — there is no
live-money path. The legacy `--live` flag is retained for CLI back-compat
and is a no-op.
        """
    )

    parser.add_argument(
        "--config", "-c",
        default="bots/hydra/config/config.json",
        help="Path to configuration file"
    )
    parser.add_argument(
        "--dry-run", "-d",
        action="store_true",
        help="Simulate orders against live IBKR paper quotes (no placement)"
    )
    parser.add_argument(
        "--status", "-s",
        action="store_true",
        help="Show current status and exit"
    )
    parser.add_argument(
        "--interval", "-i",
        type=int,
        default=1,
        help="Strategy check interval in seconds (default: 1)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging (DEBUG level)"
    )
    parser.add_argument(
        "--live", "-l",
        action="store_true",
        # P7-audit L10: kept for back-compat (no-op). The IBKR-standalone
        # branch does NOT support live trading — it talks to IBKR paper
        # only. Removing the flag would break any caller still passing
        # it; the runtime emits a NOTE that it has no effect.
        help="(DEPRECATED, no-op) Pre-migration flag retained for CLI back-compat."
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
            # HYDRA trades the IBKR paper account only — there is no
            # live-money path. `--live` is retained for CLI back-compat
            # but has no effect.
            if args.live:
                print("\n  NOTE: --live has no effect — HYDRA trades the "
                      "IBKR paper account only.\n")
            if args.dry_run:
                print("\n  Environment: IBKR PAPER (DRY RUN - No real orders)\n")
            else:
                print("\n  Environment: IBKR PAPER\n")

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
                check_interval=args.interval,
                config_path=args.config,
            )

    except FileNotFoundError as e:
        print(f"\n  Error: {e}")
        print("  Make sure config file exists at: bots/hydra/config/config.json")
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
