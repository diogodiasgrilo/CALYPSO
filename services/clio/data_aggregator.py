"""
CLIO data aggregator — collects the week's data from all sources.
"""

import json
import logging
import os
from datetime import timedelta
from glob import glob
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def aggregate_weekly_data(config: Dict[str, Any], week_end_date) -> Dict[str, Any]:
    """
    Collect all data needed for weekly analysis.

    Args:
        config: Agent config dict.
        week_end_date: Saturday date (end of the analysis week).

    Returns:
        Dict with all aggregated data.
    """
    data = {}

    # Week boundaries: Monday through Friday
    friday = week_end_date - timedelta(days=1)
    monday = friday - timedelta(days=4)

    logger.info(f"Aggregating data for week: {monday} to {friday}")

    # 1. All HERMES reports from the past week
    hermes_dir = config.get("hermes", {}).get("report_dir", "intel/hermes")
    data["hermes_reports"] = _read_week_reports(hermes_dir, monday, friday)

    # 2. All APOLLO reports from the past week
    apollo_dir = config.get("apollo", {}).get("report_dir", "intel/apollo")
    data["apollo_reports"] = _read_week_reports(apollo_dir, monday, friday)

    # 3. Cumulative metrics
    metrics_file = config.get("hermes", {}).get(
        "metrics_file", "data/hydra_metrics.json"
    )
    data["metrics"] = _read_json_file(metrics_file)

    # 4. Full Daily Summary from Google Sheets
    data["daily_summary_history"] = _read_sheets_history(config)

    # 5. Most recent previous CLIO report
    clio_dir = config.get("clio", {}).get("report_dir", "intel/clio")
    data["previous_clio"] = _read_latest_clio_report(clio_dir)

    # 6. Strategy memory
    memory_file = config.get("clio", {}).get(
        "strategy_memory_file", "intel/strategy_memory.md"
    )
    data["strategy_memory"] = _read_text_file(memory_file)

    # Summary
    collected = [k for k, v in data.items() if v]
    logger.info(f"Collected: {', '.join(collected)}")

    return data


def _read_week_reports(
    report_dir: str, monday, friday
) -> List[Dict[str, str]]:
    """Read all reports from Monday through Friday in a directory."""
    reports = []
    current = monday
    while current <= friday:
        date_str = current.strftime("%Y-%m-%d")
        path = os.path.join(report_dir, f"{date_str}.md")
        if os.path.exists(path):
            try:
                with open(path) as f:
                    reports.append({"date": date_str, "content": f.read()})
            except IOError as e:
                logger.warning(f"Failed to read {path}: {e}")
        current += timedelta(days=1)

    logger.info(f"Read {len(reports)} reports from {report_dir}")
    return reports


def _read_json_file(path: str) -> Optional[Dict]:
    """Read a JSON file."""
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Failed to read {path}: {e}")
        return None


def _read_text_file(path: str) -> Optional[str]:
    """Read a text file."""
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            content = f.read().strip()
        return content if content else None
    except IOError as e:
        logger.warning(f"Failed to read {path}: {e}")
        return None


def _read_sheets_history(config: Dict[str, Any]) -> Optional[List[Dict[str, str]]]:
    """Read full Daily Summary history from Google Sheets."""
    try:
        from shared.sheets_reader import SheetsReader

        spreadsheet = config.get("google_sheets", {}).get(
            "spreadsheet_name", "Calypso_HYDRA_Live_Data"
        )
        reader = SheetsReader(config)
        return reader.read_tab_as_dicts(spreadsheet, "Daily Summary")
    except Exception as e:
        logger.warning(f"Failed to read Daily Summary history: {e}")
        return None


def _read_latest_clio_report(clio_dir: str) -> Optional[str]:
    """Read the most recent CLIO weekly report."""
    if not os.path.exists(clio_dir):
        return None

    reports = sorted(glob(os.path.join(clio_dir, "week_*.md")))
    if not reports:
        return None

    try:
        with open(reports[-1]) as f:
            content = f.read()
        logger.info(f"Read previous Clio report: {os.path.basename(reports[-1])}")
        return content
    except IOError as e:
        logger.warning(f"Failed to read previous Clio report: {e}")
        return None
