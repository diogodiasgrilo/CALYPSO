"""
HOMER data collector — gathers trading data from Google Sheets and local files.

Also provides functions for populating the backtesting SQLite database:
  - parse_heartbeat_logs(): Extract SPX/VIX ticks from bot log files
  - compute_ohlc_from_ticks(): Compute 1-minute OHLC bars from tick data
  - build_db_records(): Transform Sheets data into DB-ready dicts
"""

import json
import logging
import math
import os
import re
import subprocess
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def collect_all_data(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Collect all data HOMER needs for journal updates.

    Returns:
        Dict with keys: daily_summary_rows, positions_rows, trades_rows,
        metrics, version_history.
    """
    data = {}

    data["daily_summary_rows"] = _read_sheets_daily_summary_all(config)
    data["positions_rows"] = _read_sheets_positions_all(config)
    data["trades_rows"] = _read_sheets_trades_all(config)
    data["metrics"] = _read_metrics_file(config)
    data["version_history"] = _read_version_history()

    collected = [k for k, v in data.items() if v]
    missing = [k for k, v in data.items() if not v]
    logger.info(f"Collected: {', '.join(collected)}")
    if missing:
        logger.warning(f"Missing: {', '.join(missing)}")

    return data


def collect_day_data(
    all_data: Dict[str, Any], date_str: str, config: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """
    Extract data for a specific trading day from the full dataset.

    Args:
        all_data: Full dataset from collect_all_data().
        date_str: Date string "YYYY-MM-DD".
        config: Agent config (needed for HERMES report lookup).

    Returns:
        Dict with day-specific data, or None if date not found.
    """
    day = {}

    # Find this day's row in Daily Summary
    if all_data.get("daily_summary_rows"):
        for row in all_data["daily_summary_rows"]:
            row_date = str(row.get("Date", "")).strip()
            if row_date == date_str:
                day["summary"] = row
                break
        if "summary" not in day:
            logger.warning(f"No Daily Summary row found for {date_str}")
            return None
    else:
        logger.warning("No Daily Summary data available")
        return None

    # Build per-entry data from Trades tab (primary, historical) + Positions tab (supplementary)
    day["entries"] = _build_entries_for_day(
        all_data.get("trades_rows"),
        all_data.get("positions_rows"),
        date_str,
    )

    # Include cumulative metrics
    day["metrics"] = all_data.get("metrics", {})

    # Include version history
    day["version_history"] = all_data.get("version_history", [])

    # Fill missing stop data from fallback sources (logs, P&L identity)
    if day["entries"] and day.get("summary"):
        _fill_missing_stop_data(day["entries"], day["summary"], date_str)

    # Context chaining: include HERMES daily report if available
    day["hermes_report"] = _read_hermes_report(config or {}, date_str)

    logger.info(
        f"Day {date_str}: summary found, {len(day['entries'])} entries"
        f"{', HERMES report found' if day['hermes_report'] else ''}"
    )
    return day


def _build_entries_for_day(
    trades_rows: Optional[List[Dict]],
    positions_rows: Optional[List[Dict]],
    date_str: str,
) -> List[Dict[str, Any]]:
    """
    Build per-entry data by merging Trades tab (per-entry rows) and
    Positions tab (per-side rows).

    Trades tab is the primary source (historical, has per-side credits).
    Positions tab supplements with outcome/stop data (today only, overwritten daily).
    """
    entries_by_num: Dict[str, Dict[str, Any]] = {}

    # 1. Parse Trades tab for per-entry data
    if trades_rows:
        for row in trades_rows:
            action = str(row.get("Action", "")).strip()
            if not action.startswith("HYDRA Entry"):
                continue

            # Filter by date: check Expiry (0DTE) or Timestamp
            row_date = str(row.get("Expiry", "")).strip()
            if row_date != date_str:
                ts = str(row.get("Timestamp", "")).strip()
                if not ts.startswith(date_str):
                    continue

            # Parse entry number: "HYDRA Entry #1 [NEUTRAL]"
            match = re.match(r"HYDRA Entry #(\d+)\s*\[(\w+(?:-\d+)?)\]", action)
            if not match:
                continue
            entry_num = match.group(1)
            signal = match.group(2)

            # Parse entry time from Timestamp
            entry_time = ""
            ts = str(row.get("Timestamp", "")).strip()
            if ts:
                try:
                    dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                    entry_time = dt.strftime("%I:%M %p ET")
                except ValueError:
                    entry_time = ts

            entry_type = str(row.get("Type", "Iron Condor")).strip()

            # Parse short strikes from Strike field: "C:6850/6925 P:6630/6555"
            strike_str = str(row.get("Strike", "")).strip()
            short_call = ""
            short_put = ""
            call_match = re.search(r"C:(\d+)", strike_str)
            put_match = re.search(r"P:(\d+)", strike_str)
            if call_match:
                short_call = call_match.group(1)
            if put_match:
                short_put = put_match.group(1)

            entries_by_num[entry_num] = {
                "Entry #": entry_num,
                "Entry Time": entry_time,
                "Trend Signal": signal,
                "Entry Type": entry_type,
                "Short Call Strike": short_call,
                "Short Put Strike": short_put,
                "Total Credit": str(row.get("Premium ($)", "0")).strip(),
                "Call Credit": str(row.get("Call Credit ($)", "")).strip(),
                "Put Credit": str(row.get("Put Credit ($)", "")).strip(),
                "Outcome": "",
                "P&L Impact": "",
            }

    # 1b. Parse Trades tab for stop timing data ("HYDRA Stop #N (CALL/PUT)")
    if trades_rows:
        for row in trades_rows:
            action = str(row.get("Action", "")).strip()
            if "Stop #" not in action:
                continue

            # Filter by date
            row_date = str(row.get("Expiry", "")).strip()
            if row_date != date_str:
                ts = str(row.get("Timestamp", "")).strip()
                if not ts.startswith(date_str):
                    continue

            # Parse: "HYDRA Stop #1 (PUT)" or "HYDRA Stop #3 (CALL)"
            stop_match = re.match(r".*Stop\s*#(\d+)\s*\((\w+)\)", action)
            if not stop_match:
                continue
            entry_num = stop_match.group(1)
            side = stop_match.group(2).lower()

            if entry_num in entries_by_num:
                # Extract stop time from Timestamp
                ts = str(row.get("Timestamp", "")).strip()
                if ts:
                    try:
                        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                        stop_time = dt.strftime("%I:%M %p ET")
                    except ValueError:
                        stop_time = ts

                    # Store per-side stop time; use first stop time as entry's Stop Time
                    key = f"{side.title()} Stop Time"
                    entries_by_num[entry_num][key] = stop_time
                    if "Stop Time" not in entries_by_num[entry_num]:
                        entries_by_num[entry_num]["Stop Time"] = stop_time

                # Extract stop P&L (negative = loss)
                stop_pnl = _safe_float(row.get("P&L ($)", 0))
                if stop_pnl:
                    existing = _safe_float(entries_by_num[entry_num].get("P&L Impact", 0))
                    entries_by_num[entry_num]["P&L Impact"] = str(existing + stop_pnl)

    # 2. Merge Positions tab data (per-side rows → outcome/stop/spread width data)
    if positions_rows:
        for row in positions_rows:
            # Positions tab uses "Expiry" for date (no "Date" column)
            row_date = str(row.get("Expiry", row.get("Date", ""))).strip()
            if row_date != date_str:
                continue

            entry_num = str(row.get("Entry #", "")).strip()
            if not entry_num:
                continue

            side = str(row.get("Side", "")).strip().lower()
            if side not in ("call", "put"):
                continue

            # Create entry if not from Trades tab
            if entry_num not in entries_by_num:
                entries_by_num[entry_num] = {
                    "Entry #": entry_num,
                    "Entry Time": "",
                    "Trend Signal": str(row.get("Trend Signal", "NEUTRAL")).strip(),
                    "Entry Type": "",
                    "Short Call Strike": "",
                    "Short Put Strike": "",
                    "Total Credit": "0",
                    "Call Credit": "",
                    "Put Credit": "",
                    "Outcome": "",
                    "P&L Impact": "",
                }

            entry = entries_by_num[entry_num]

            if side == "call":
                if not entry.get("Short Call Strike"):
                    entry["Short Call Strike"] = str(row.get("Strike", "")).strip()
                if not entry.get("Call Credit"):
                    entry["Call Credit"] = str(row.get("Entry Credit", "")).strip()
                entry["Call Status"] = str(row.get("Status", "")).strip().upper()
                entry["Call Stop Triggered"] = str(row.get("Stop Triggered", "No")).strip()
                entry["Call Spread Width"] = str(row.get("Spread Width", "")).strip()
            elif side == "put":
                if not entry.get("Short Put Strike"):
                    entry["Short Put Strike"] = str(row.get("Strike", "")).strip()
                if not entry.get("Put Credit"):
                    entry["Put Credit"] = str(row.get("Entry Credit", "")).strip()
                entry["Put Status"] = str(row.get("Status", "")).strip().upper()
                entry["Put Stop Triggered"] = str(row.get("Stop Triggered", "No")).strip()
                entry["Put Spread Width"] = str(row.get("Spread Width", "")).strip()

    # 3. Post-process: determine entry type, outcome, total credit
    for entry in entries_by_num.values():
        has_call = bool(entry.get("Short Call Strike"))
        has_put = bool(entry.get("Short Put Strike"))

        # Set entry type if not from Trades tab
        if not entry.get("Entry Type"):
            if has_call and has_put:
                entry["Entry Type"] = "Full IC"
            elif has_call:
                entry["Entry Type"] = "Call Only"
            elif has_put:
                entry["Entry Type"] = "Put Only"

        # Calculate total credit from per-side if needed
        if not _safe_float(entry.get("Total Credit", 0)):
            call_credit = _safe_float(entry.get("Call Credit", 0))
            put_credit = _safe_float(entry.get("Put Credit", 0))
            if call_credit or put_credit:
                entry["Total Credit"] = str(call_credit + put_credit)

        # Determine outcome from Positions status
        if not entry.get("Outcome"):
            call_stopped = str(entry.get("Call Stop Triggered", "No")).strip().lower() == "yes"
            put_stopped = str(entry.get("Put Stop Triggered", "No")).strip().lower() == "yes"
            call_status = entry.get("Call Status", "")
            put_status = entry.get("Put Status", "")

            if call_stopped and put_stopped:
                entry["Outcome"] = "Double Stop"
            elif call_stopped:
                entry["Outcome"] = "Call Stopped"
            elif put_stopped:
                entry["Outcome"] = "Put Stopped"
            elif "EARLY_CLOSED" in call_status or "EARLY_CLOSED" in put_status:
                entry["Outcome"] = "Early Closed"
            elif "EXPIRED" in call_status or "EXPIRED" in put_status:
                entry["Outcome"] = "Expired"

    # Sort by entry number
    result = sorted(
        entries_by_num.values(),
        key=lambda e: int(e.get("Entry #", 0) or 0),
    )
    return result


def _fill_missing_stop_data(
    entries: List[Dict], summary: Dict, date_str: str
) -> None:
    """
    Fill missing stop data from fallback sources when Trades tab has gaps.

    Fallback 1: HYDRA service logs (journalctl) for stop time and P&L.
    Fallback 2: P&L identity derivation from Daily Summary totals.
    """
    stopped_entries = [
        e for e in entries if "STOP" in str(e.get("Outcome", "")).upper()
    ]
    if not stopped_entries:
        return

    missing_time = [e for e in stopped_entries if not e.get("Stop Time")]
    missing_pnl = [
        e for e in stopped_entries if not _safe_float(e.get("P&L Impact", 0))
    ]

    if not missing_time and not missing_pnl:
        return

    logger.info(
        f"Missing stop data for {date_str}: "
        f"{len(missing_time)} missing times, {len(missing_pnl)} missing P&L"
    )

    # Fallback 1: Parse HYDRA logs for MKT-025 stop events
    log_stops = _read_hydra_logs_for_stops(date_str)
    if log_stops:
        for entry in stopped_entries:
            entry_num = str(entry.get("Entry #", ""))
            if entry_num not in log_stops:
                continue
            stop_data = log_stops[entry_num]
            if not entry.get("Stop Time") and stop_data.get("stop_time"):
                entry["Stop Time"] = stop_data["stop_time"]
                logger.info(
                    f"Entry #{entry_num}: stop time from logs: {stop_data['stop_time']}"
                )
            if not _safe_float(entry.get("P&L Impact", 0)) and stop_data.get("pnl"):
                entry["P&L Impact"] = str(stop_data["pnl"])
                logger.info(
                    f"Entry #{entry_num}: stop P&L from logs: "
                    f"${stop_data['pnl']:.2f}"
                )

    # Fallback 2: Derive missing P&L from Daily Summary identity
    still_missing = [
        e for e in stopped_entries if not _safe_float(e.get("P&L Impact", 0))
    ]
    if still_missing:
        _derive_missing_stop_pnl(entries, summary)


def _read_hydra_logs_for_stops(date_str: str) -> Dict[str, Dict[str, Any]]:
    """
    Read HYDRA service logs for MKT-025 stop events on a given date.

    Tries log file first (readable by calypso user), then journalctl as fallback.
    Returns dict keyed by entry number: {"3": {"stop_time": "12:22 PM ET", "pnl": -150.0}}
    """
    lines = _read_log_lines_for_date(date_str)
    if not lines:
        return {}

    stops: Dict[str, Dict[str, Any]] = {}
    for line in lines:
        # "2026-03-04 12:22:39 | WARNING | ... | MKT-025 STOP TRIGGERED: Entry #3 put side"
        trigger = re.search(
            r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*"
            r"MKT-025 STOP TRIGGERED: Entry #(\d+) (\w+) side",
            line,
        )
        if trigger:
            ts_str, entry_num, side = trigger.group(1), trigger.group(2), trigger.group(3)
            try:
                dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                stop_time = dt.strftime("%I:%M %p ET")
            except ValueError:
                stop_time = ts_str
            stops.setdefault(entry_num, {})
            stops[entry_num]["stop_time"] = stop_time
            stops[entry_num]["side"] = side.lower()

        # "MKT-025: Actual P&L for Entry #3 put: ... net_loss=$150.00"
        # "MKT-025: Using theoretical P&L ... net_loss=$50.00"
        pnl_match = re.search(
            r"MKT-025.*Entry #(\d+).*net_loss=\$(\d+\.?\d*)", line
        )
        if pnl_match:
            entry_num = pnl_match.group(1)
            loss = float(pnl_match.group(2))
            stops.setdefault(entry_num, {})
            stops[entry_num]["pnl"] = -loss

    if stops:
        logger.info(
            f"Parsed {len(stops)} MKT-025 stop events from HYDRA logs for {date_str}"
        )
    return stops


def _read_log_lines_for_date(date_str: str) -> List[str]:
    """
    Read MKT-025 log lines for a date. Tries log file first, journalctl second.

    Log file at logs/hydra/bot.log is readable by calypso user.
    journalctl requires systemd-journal group membership.
    """
    # Try 1: Log file (calypso-readable, most reliable)
    log_path = os.path.join("logs", "hydra", "bot.log")
    if os.path.exists(log_path):
        try:
            matching = []
            with open(log_path) as f:
                for line in f:
                    if date_str in line and "MKT-025" in line:
                        matching.append(line)
            if matching:
                logger.info(f"Read {len(matching)} MKT-025 lines from {log_path}")
                return matching
        except IOError as e:
            logger.warning(f"Failed to read {log_path}: {e}")

    # Try 2: journalctl (needs systemd-journal group)
    try:
        result = subprocess.run(
            [
                "journalctl", "-u", "hydra",
                "--since", date_str, "--until", f"{date_str} 23:59:59",
                "--no-pager", "--grep", "MKT-025",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            lines = result.stdout.splitlines()
            logger.info(f"Read {len(lines)} MKT-025 lines from journalctl")
            return lines
    except subprocess.TimeoutExpired:
        logger.warning("journalctl timed out reading HYDRA logs")
    except FileNotFoundError:
        logger.info("journalctl not available (running locally?)")

    return []


def _derive_missing_stop_pnl(entries: List[Dict], summary: Dict) -> None:
    """
    Derive missing individual stop P&L from Daily Summary total.

    P&L identity: Expired Credits - Stop Loss Debits - Commission = Net P&L
    If exactly one stopped entry is missing P&L, derive it from the total.
    """
    total_debits = _safe_float(summary.get("Stop Loss Debits ($)", 0))
    if total_debits <= 0:
        return

    stopped = [e for e in entries if "STOP" in str(e.get("Outcome", "")).upper()]
    if not stopped:
        return

    known_debits = 0.0
    missing = []
    for entry in stopped:
        pnl = _safe_float(entry.get("P&L Impact", 0))
        if pnl:
            known_debits += abs(pnl)
        else:
            missing.append(entry)

    if len(missing) == 1:
        derived_debit = total_debits - known_debits
        if derived_debit > 0:
            missing[0]["P&L Impact"] = str(-derived_debit)
            entry_num = missing[0].get("Entry #", "?")
            logger.info(
                f"Derived Entry #{entry_num} stop P&L: -${derived_debit:.2f} "
                f"(total debits ${total_debits:.2f} - known ${known_debits:.2f})"
            )
    elif len(missing) > 1:
        logger.warning(
            f"{len(missing)} entries missing stop P&L — cannot derive individually "
            f"(total debits: ${total_debits:.2f}, known: ${known_debits:.2f})"
        )


def _safe_float(value) -> float:
    """Convert value to float, returning 0.0 on failure."""
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def get_all_trading_dates(all_data: Dict[str, Any]) -> List[str]:
    """
    Get all trading dates from the Daily Summary data.

    Returns:
        List of date strings "YYYY-MM-DD" in chronological order.
    """
    if not all_data.get("daily_summary_rows"):
        return []

    dates = []
    for row in all_data["daily_summary_rows"]:
        date_str = str(row.get("Date", "")).strip()
        if date_str and re.match(r"\d{4}-\d{2}-\d{2}", date_str):
            dates.append(date_str)

    dates.sort()
    return dates


def _read_sheets_daily_summary_all(config: Dict[str, Any]) -> Optional[List[Dict[str, str]]]:
    """Read ALL daily summary rows from Google Sheets."""
    try:
        from shared.sheets_reader import SheetsReader

        spreadsheet = config.get("google_sheets", {}).get(
            "spreadsheet_name", "Calypso_HYDRA_Live_Data"
        )
        reader = SheetsReader(config)
        rows = reader.read_tab_as_dicts(spreadsheet, "Daily Summary")
        if rows:
            logger.info(f"Read {len(rows)} Daily Summary rows from Sheets")
        return rows
    except Exception as e:
        logger.warning(f"Failed to read Daily Summary from Sheets: {e}")
        return None


def _read_sheets_positions_all(config: Dict[str, Any]) -> Optional[List[Dict[str, str]]]:
    """Read ALL position entries from Google Sheets."""
    try:
        from shared.sheets_reader import SheetsReader

        spreadsheet = config.get("google_sheets", {}).get(
            "spreadsheet_name", "Calypso_HYDRA_Live_Data"
        )
        reader = SheetsReader(config)
        rows = reader.read_tab_as_dicts(spreadsheet, "Positions")
        if rows:
            logger.info(f"Read {len(rows)} Positions rows from Sheets")
        return rows
    except Exception as e:
        logger.warning(f"Failed to read Positions from Sheets: {e}")
        return None


def _read_sheets_trades_all(config: Dict[str, Any]) -> Optional[List[Dict[str, str]]]:
    """Read ALL trades from Google Sheets Trades tab."""
    try:
        from shared.sheets_reader import SheetsReader

        spreadsheet = config.get("google_sheets", {}).get(
            "spreadsheet_name", "Calypso_HYDRA_Live_Data"
        )
        reader = SheetsReader(config)
        rows = reader.read_tab_as_dicts(spreadsheet, "Trades")
        if rows:
            logger.info(f"Read {len(rows)} Trades rows from Sheets")
        return rows
    except Exception as e:
        logger.warning(f"Failed to read Trades from Sheets: {e}")
        return None


def _read_metrics_file(config: Dict[str, Any]) -> Optional[Dict]:
    """Read cumulative metrics from hydra_metrics.json."""
    path = config.get("homer", {}).get("metrics_file", "data/hydra_metrics.json")
    if not os.path.exists(path):
        logger.info(f"Metrics file not found: {path}")
        return None

    try:
        with open(path) as f:
            data = json.load(f)
        logger.info(f"Read metrics from {path}")
        return data
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Failed to read {path}: {e}")
        return None


def _read_hermes_report(config: Dict[str, Any], date_str: str) -> Optional[str]:
    """
    Read HERMES daily analysis report for context chaining.

    Context chaining: HOMER reads HERMES's analysis of the trading day
    to provide richer context to Claude API for narrative generation.

    Args:
        config: Agent config.
        date_str: Date string "YYYY-MM-DD".

    Returns:
        Report content as string, or None if not available.
    """
    report_dir = config.get("hermes", {}).get("report_dir", "intel/hermes")
    report_path = os.path.join(report_dir, f"{date_str}.md")

    if not os.path.exists(report_path):
        logger.info(f"No HERMES report for {date_str} at {report_path}")
        return None

    try:
        with open(report_path) as f:
            content = f.read()
        logger.info(f"Read HERMES report for {date_str} ({len(content)} chars)")
        return content
    except IOError as e:
        logger.warning(f"Failed to read HERMES report {report_path}: {e}")
        return None


def _read_version_history() -> List[Dict[str, str]]:
    """
    Parse version history from bots/hydra/__init__.py.

    Returns:
        List of dicts: [{"version": "1.5.1", "date": "2026-03-02", "description": "..."}]
    """
    init_path = os.path.join("bots", "hydra", "__init__.py")
    if not os.path.exists(init_path):
        logger.info(f"HYDRA __init__.py not found: {init_path}")
        return []

    try:
        with open(init_path) as f:
            content = f.read()

        versions = []
        # Match lines like: - 1.5.1 (2026-03-02): Description here
        pattern = r"-\s+([\d.]+)\s+\((\d{4}-\d{2}-\d{2})\):\s+(.+)"
        for match in re.finditer(pattern, content):
            versions.append({
                "version": match.group(1),
                "date": match.group(2),
                "description": match.group(3).strip(),
            })

        logger.info(f"Parsed {len(versions)} versions from __init__.py")
        return versions
    except IOError as e:
        logger.warning(f"Failed to read {init_path}: {e}")
        return []


# =========================================================================
# BACKTESTING DATABASE — Data extraction and transformation
# =========================================================================

# Regex for parsing heartbeat log lines (handles both meic_tf and hydra format)
# Example: "2026-02-10 09:30:24 | INFO | shared.logger_service | HEARTBEAT | WaitingFirstEntry | SPX: 6970.55 | VIX: 17.35 | Entries: 0/6 | Active: 0 | Trend: neutral"
_HEARTBEAT_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*"
    r"HEARTBEAT \| (\w+) \| "
    r"SPX: ([\d.]+) \| "
    r"VIX: ([\d.]+) \| "
    r"Entries: (\d+)/\d+ \| "
    r"Active: (\d+) \| "
    r"Trend: (\w+)"
)

# Default log file paths (relative to project root)
DEFAULT_LOG_PATHS = [
    os.path.join("logs", "meic_tf", "bot.log"),  # Feb 5-27 (pre-rename)
    os.path.join("logs", "hydra", "bot.log"),     # Feb 28+ (post-rename)
]


def parse_heartbeat_logs(
    date_str: str,
    log_paths: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Parse heartbeat log lines for a specific date from bot log files.

    Reads line-by-line to avoid loading 37MB+ files into memory.

    Args:
        date_str: Date to extract ("YYYY-MM-DD").
        log_paths: List of log file paths to search. Defaults to both
                   meic_tf and hydra log files.

    Returns:
        List of dicts matching market_ticks schema, sorted by timestamp.
    """
    if log_paths is None:
        log_paths = DEFAULT_LOG_PATHS

    ticks = {}  # timestamp -> tick dict (dedup by timestamp)

    for path in log_paths:
        if not os.path.exists(path):
            continue

        try:
            with open(path) as f:
                for line in f:
                    # Quick filter before regex (performance)
                    if date_str not in line or "HEARTBEAT" not in line or "SPX:" not in line:
                        continue

                    match = _HEARTBEAT_RE.search(line)
                    if not match:
                        continue

                    ts = match.group(1)
                    # Verify date matches (line might contain date_str elsewhere)
                    if not ts.startswith(date_str):
                        continue

                    ticks[ts] = {
                        "timestamp": ts,
                        "spx_price": float(match.group(3)),
                        "vix_level": float(match.group(4)),
                        "bot_state": match.group(2),
                        "entry_count": int(match.group(5)),
                        "active_count": int(match.group(6)),
                        "trend_signal": match.group(7),
                    }
        except IOError as e:
            logger.warning(f"Failed to read {path}: {e}")

    result = sorted(ticks.values(), key=lambda t: t["timestamp"])
    if result:
        logger.info(f"Parsed {len(result)} heartbeat ticks for {date_str} from log files")
    return result


def parse_all_heartbeat_logs(
    log_paths: Optional[List[str]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Parse ALL heartbeat log lines from log files, grouped by date.

    Used for backfill mode — reads entire log files once instead of
    per-date scanning.

    Returns:
        Dict mapping date string -> list of tick dicts.
    """
    if log_paths is None:
        log_paths = DEFAULT_LOG_PATHS

    ticks_by_date: Dict[str, Dict[str, Dict]] = defaultdict(dict)

    for path in log_paths:
        if not os.path.exists(path):
            logger.info(f"Log file not found (skipping): {path}")
            continue

        count = 0
        try:
            with open(path) as f:
                for line in f:
                    if "HEARTBEAT" not in line or "SPX:" not in line:
                        continue

                    match = _HEARTBEAT_RE.search(line)
                    if not match:
                        continue

                    ts = match.group(1)
                    date = ts[:10]
                    ticks_by_date[date][ts] = {
                        "timestamp": ts,
                        "spx_price": float(match.group(3)),
                        "vix_level": float(match.group(4)),
                        "bot_state": match.group(2),
                        "entry_count": int(match.group(5)),
                        "active_count": int(match.group(6)),
                        "trend_signal": match.group(7),
                    }
                    count += 1
        except IOError as e:
            logger.warning(f"Failed to read {path}: {e}")

        logger.info(f"Parsed {count} heartbeat ticks from {path}")

    # Convert to sorted lists
    result = {}
    for date, tick_dict in sorted(ticks_by_date.items()):
        result[date] = sorted(tick_dict.values(), key=lambda t: t["timestamp"])

    logger.info(
        f"Total: {sum(len(v) for v in result.values())} ticks across {len(result)} dates"
    )
    return result


def compute_ohlc_from_ticks(ticks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Compute 1-minute OHLC bars from heartbeat ticks.

    Groups ticks by minute, computes Open (first), High (max), Low (min),
    Close (last) from spx_price. VIX uses last reading in the minute.

    Note: Heartbeats fire ~every 11 seconds, giving ~5 samples/minute.
    During order placement or stop processing, gaps may occur — those
    minutes will simply have no OHLC bar.

    Args:
        ticks: List of tick dicts with 'timestamp' and 'spx_price' fields.

    Returns:
        List of OHLC bar dicts matching market_ohlc_1min schema.
    """
    if not ticks:
        return []

    minutes: Dict[str, List[Dict]] = defaultdict(list)
    for tick in ticks:
        # Truncate to minute: "2026-02-10 09:30:24" -> "2026-02-10 09:30:00"
        minute_key = tick["timestamp"][:16] + ":00"
        minutes[minute_key].append(tick)

    bars = []
    for minute_ts in sorted(minutes.keys()):
        group = minutes[minute_ts]
        prices = [t["spx_price"] for t in group if t.get("spx_price")]
        if not prices:
            continue
        bars.append({
            "timestamp": minute_ts,
            "open": prices[0],
            "high": max(prices),
            "low": min(prices),
            "close": prices[-1],
            "vix": group[-1].get("vix_level"),
        })

    return bars


def build_db_records(
    day_data: Optional[Dict[str, Any]],
    date_str: str,
    ticks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Transform HOMER's existing day_data (from Sheets) into DB-ready dicts.

    Args:
        day_data: Day data from collect_day_data(), or None if no Sheets data.
        date_str: Date string "YYYY-MM-DD".
        ticks: Heartbeat ticks for the day (for SPX lookups).

    Returns:
        Dict with keys: 'trade_entries', 'trade_stops', 'daily_summary'.
        Each value is a list of dicts (or a single dict for daily_summary).
        Any key may be empty/None if source data is unavailable.
    """
    result: Dict[str, Any] = {
        "trade_entries": [],
        "trade_stops": [],
        "daily_summary": None,
    }

    if not day_data:
        return result

    entries = day_data.get("entries", [])
    summary = day_data.get("summary", {})

    # Build trade_entries records
    result["trade_entries"] = _build_entry_records(entries, date_str, ticks)

    # Build trade_stops records
    result["trade_stops"] = _build_stop_records(entries, date_str, ticks)

    # Build daily_summary record
    if summary:
        result["daily_summary"] = _build_summary_record(summary, date_str, ticks)

    return result


def _find_nearest_tick(ticks: List[Dict], target_time: str) -> Optional[Dict]:
    """Find the tick with timestamp closest to target_time (HH:MM:SS or HH:MM format)."""
    if not ticks or not target_time:
        return None

    # Normalize target to "HH:MM:SS" for comparison
    # Input might be "11:05 AM ET", "11:05:24", "2026-02-10 11:05:24", etc.
    time_match = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM)?", target_time, re.IGNORECASE)
    if not time_match:
        return None

    hour = int(time_match.group(1))
    minute = int(time_match.group(2))
    second = int(time_match.group(3) or 0)
    ampm = time_match.group(4)

    # Convert 12-hour to 24-hour if AM/PM present
    if ampm:
        if ampm.upper() == "PM" and hour != 12:
            hour += 12
        elif ampm.upper() == "AM" and hour == 12:
            hour = 0

    target_seconds = hour * 3600 + minute * 60 + second

    best = None
    best_diff = float("inf")
    for tick in ticks:
        ts = tick["timestamp"]
        # Extract HH:MM:SS from "YYYY-MM-DD HH:MM:SS"
        try:
            h, m, s = int(ts[11:13]), int(ts[14:16]), int(ts[17:19])
            tick_seconds = h * 3600 + m * 60 + s
            diff = abs(tick_seconds - target_seconds)
            if diff < best_diff:
                best_diff = diff
                best = tick
        except (ValueError, IndexError):
            continue

    return best


def _build_entry_records(
    entries: List[Dict], date_str: str, ticks: List[Dict]
) -> List[Dict[str, Any]]:
    """Transform Sheets entry data into trade_entries DB records."""
    records = []
    for entry in entries:
        entry_num = int(entry.get("Entry #", 0) or 0)
        if entry_num <= 0:
            continue

        entry_time = entry.get("Entry Time", "")

        # Look up SPX/VIX at entry time from ticks
        nearest = _find_nearest_tick(ticks, entry_time)
        spx_at_entry = nearest["spx_price"] if nearest else None
        vix_at_entry = nearest["vix_level"] if nearest else None

        # Compute expected move from VIX (0DTE: 1 day)
        expected_move = None
        if spx_at_entry and vix_at_entry:
            expected_move = round(spx_at_entry * (vix_at_entry / 100) * math.sqrt(1 / 365), 2)

        # Parse strikes
        short_call = _safe_float(entry.get("Short Call Strike"))
        short_put = _safe_float(entry.get("Short Put Strike"))
        call_spread_width = _safe_float(entry.get("Call Spread Width"))
        put_spread_width = _safe_float(entry.get("Put Spread Width"))

        # Compute long strikes from short + spread width
        long_call = (short_call + call_spread_width) if short_call and call_spread_width else None
        long_put = (short_put - put_spread_width) if short_put and put_spread_width else None

        # OTM distances
        otm_call = abs(spx_at_entry - short_call) if spx_at_entry and short_call else None
        otm_put = abs(spx_at_entry - short_put) if spx_at_entry and short_put else None

        # Entry type
        entry_type = entry.get("Entry Type", "")
        if not entry_type:
            if short_call and short_put:
                entry_type = "Full IC"
            elif short_call:
                entry_type = "Call Only"
            elif short_put:
                entry_type = "Put Only"

        # Credits
        call_credit = _safe_float(entry.get("Call Credit")) or None
        put_credit = _safe_float(entry.get("Put Credit")) or None
        total_credit = _safe_float(entry.get("Total Credit")) or None

        records.append({
            "date": date_str,
            "entry_number": entry_num,
            "entry_time": entry_time or None,
            "spx_at_entry": spx_at_entry,
            "vix_at_entry": vix_at_entry,
            "expected_move": expected_move,
            "trend_signal": entry.get("Trend Signal"),
            "entry_type": entry_type or None,
            "override_reason": entry.get("Override Reason"),
            "short_call_strike": short_call or None,
            "long_call_strike": long_call,
            "short_put_strike": short_put or None,
            "long_put_strike": long_put,
            "call_credit": call_credit,
            "put_credit": put_credit,
            "total_credit": total_credit,
            "call_spread_width": call_spread_width or None,
            "put_spread_width": put_spread_width or None,
            "mkt031_score": None,  # Only available from v1.8.0+ (Mar 4)
            "mkt031_early": None,
            "otm_distance_call": otm_call,
            "otm_distance_put": otm_put,
        })

    return records


def _build_stop_records(
    entries: List[Dict], date_str: str, ticks: List[Dict]
) -> List[Dict[str, Any]]:
    """Transform Sheets entry data into trade_stops DB records."""
    records = []
    for entry in entries:
        entry_num = int(entry.get("Entry #", 0) or 0)
        if entry_num <= 0:
            continue

        outcome = str(entry.get("Outcome", "")).upper()
        if "STOP" not in outcome:
            continue

        # Determine which sides were stopped
        sides_stopped = []
        if "CALL" in outcome or "DOUBLE" in outcome:
            sides_stopped.append("call")
        if "PUT" in outcome or "DOUBLE" in outcome:
            sides_stopped.append("put")
        # If just "Stop" without side, check status flags
        if not sides_stopped:
            if str(entry.get("Call Stop Triggered", "")).lower() == "yes":
                sides_stopped.append("call")
            if str(entry.get("Put Stop Triggered", "")).lower() == "yes":
                sides_stopped.append("put")

        pnl_impact = _safe_float(entry.get("P&L Impact", 0))

        for side in sides_stopped:
            stop_time_key = f"{side.title()} Stop Time"
            stop_time = entry.get(stop_time_key) or entry.get("Stop Time", "")

            # SPX at stop time
            nearest = _find_nearest_tick(ticks, stop_time)
            spx_at_stop = nearest["spx_price"] if nearest else None

            # Per-side P&L: if double stop, split evenly (approximation)
            side_pnl = pnl_impact / len(sides_stopped) if pnl_impact and sides_stopped else None

            records.append({
                "date": date_str,
                "entry_number": entry_num,
                "side": side,
                "stop_time": stop_time or None,
                "spx_at_stop": spx_at_stop,
                "trigger_level": None,  # Not available from Sheets
                "actual_debit": None,   # Not available from Sheets
                "net_pnl": side_pnl,
            })

    return records


def _build_summary_record(
    summary: Dict, date_str: str, ticks: List[Dict]
) -> Dict[str, Any]:
    """Transform Sheets Daily Summary row into daily_summaries DB record."""
    # Try Sheets SPX OHLC first, fall back to computing from ticks
    spx_open = _safe_float(summary.get("SPX Open")) or None
    spx_close = _safe_float(summary.get("SPX Close")) or None
    spx_high = _safe_float(summary.get("SPX High")) or None
    spx_low = _safe_float(summary.get("SPX Low")) or None

    # If Sheets doesn't have SPX OHLC, derive from ticks
    if not spx_open and ticks:
        # Filter to market hours only (9:30 - 16:00 ET)
        market_ticks = [
            t for t in ticks
            if "09:30" <= t["timestamp"][11:16] <= "16:00"
        ]
        if market_ticks:
            spx_open = market_ticks[0]["spx_price"]
            spx_close = market_ticks[-1]["spx_price"]
            prices = [t["spx_price"] for t in market_ticks]
            spx_high = max(prices)
            spx_low = min(prices)

    # VIX OHLC
    vix_open = _safe_float(summary.get("VIX Open")) or None
    vix_close = _safe_float(summary.get("VIX Close")) or None
    if not vix_open and ticks:
        market_ticks = [
            t for t in ticks
            if "09:30" <= t["timestamp"][11:16] <= "16:00"
        ]
        if market_ticks:
            vix_open = market_ticks[0].get("vix_level")
            vix_close = market_ticks[-1].get("vix_level")

    # P&L
    gross_pnl = _safe_float(summary.get("Daily P&L ($)")) or None
    commission = _safe_float(summary.get("Commission ($)")) or None
    net_pnl = None
    if gross_pnl is not None:
        net_pnl = gross_pnl - (commission or 0)

    # Entry/stop counts
    entries_placed = None
    entries_completed = summary.get("Entries Completed")
    if entries_completed:
        entries_placed = int(_safe_float(entries_completed))

    entries_stopped = None
    call_stops = _safe_float(summary.get("Call Stops", 0))
    put_stops = _safe_float(summary.get("Put Stops", 0))
    if call_stops or put_stops:
        entries_stopped = int(call_stops + put_stops)

    entries_expired = None
    if entries_placed is not None and entries_stopped is not None:
        entries_expired = max(0, entries_placed - entries_stopped)

    # Day range
    day_range = None
    if spx_high and spx_low:
        day_range = round(spx_high - spx_low, 2)

    # Day of week
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        day_of_week = dt.strftime("%A")
    except ValueError:
        day_of_week = None

    # Day type
    notes = str(summary.get("Notes", "")).strip().lower()
    day_type = "normal"
    if "fomc" in notes:
        day_type = "fomc"
    elif "opex" in notes or "expir" in notes:
        day_type = "opex"
    elif "early close" in notes or "early_close" in notes:
        day_type = "early_close"

    return {
        "date": date_str,
        "spx_open": spx_open,
        "spx_close": spx_close,
        "spx_high": spx_high,
        "spx_low": spx_low,
        "day_range": day_range,
        "vix_open": vix_open,
        "vix_close": vix_close,
        "entries_placed": entries_placed,
        "entries_stopped": entries_stopped,
        "entries_expired": entries_expired,
        "gross_pnl": gross_pnl,
        "net_pnl": net_pnl,
        "commission": commission,
        "day_type": day_type,
        "day_of_week": day_of_week,
    }
