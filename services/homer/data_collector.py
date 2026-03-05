"""
HOMER data collector — gathers trading data from Google Sheets and local files.
"""

import json
import logging
import os
import re
import subprocess
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

    Parses journalctl output (same approach as HERMES data_collector).
    Returns dict keyed by entry number: {"3": {"stop_time": "12:22 PM ET", "pnl": -150.0}}
    """
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
        if result.returncode != 0 or not result.stdout.strip():
            return {}
    except subprocess.TimeoutExpired:
        logger.warning("journalctl timed out reading HYDRA logs")
        return {}
    except FileNotFoundError:
        logger.info("journalctl not available (running locally?)")
        return {}

    stops: Dict[str, Dict[str, Any]] = {}
    for line in result.stdout.splitlines():
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
