"""
HOMER data collector — gathers trading data from Google Sheets and local files.
"""

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def collect_all_data(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Collect all data HOMER needs for journal updates.

    Returns:
        Dict with keys: daily_summary_rows, positions_rows, metrics,
        version_history, hermes_reports.
    """
    data = {}

    data["daily_summary_rows"] = _read_sheets_daily_summary_all(config)
    data["positions_rows"] = _read_sheets_positions_all(config)
    data["metrics"] = _read_metrics_file(config)
    data["version_history"] = _read_version_history()

    collected = [k for k, v in data.items() if v]
    missing = [k for k, v in data.items() if not v]
    logger.info(f"Collected: {', '.join(collected)}")
    if missing:
        logger.warning(f"Missing: {', '.join(missing)}")

    return data


def collect_day_data(
    all_data: Dict[str, Any], date_str: str
) -> Optional[Dict[str, Any]]:
    """
    Extract data for a specific trading day from the full dataset.

    Args:
        all_data: Full dataset from collect_all_data().
        date_str: Date string "YYYY-MM-DD".

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

    # Find this day's entries in Positions tab
    day["entries"] = []
    if all_data.get("positions_rows"):
        for row in all_data["positions_rows"]:
            row_date = str(row.get("Date", "")).strip()
            if row_date == date_str:
                day["entries"].append(row)

    # Include cumulative metrics
    day["metrics"] = all_data.get("metrics", {})

    # Include version history
    day["version_history"] = all_data.get("version_history", [])

    # Context chaining: include HERMES daily report if available
    day["hermes_report"] = _read_hermes_report(config, date_str)

    logger.info(
        f"Day {date_str}: summary found, {len(day['entries'])} entries"
        f"{', HERMES report found' if day['hermes_report'] else ''}"
    )
    return day


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
