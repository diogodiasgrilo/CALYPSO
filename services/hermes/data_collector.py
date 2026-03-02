"""
HERMES data collector — gathers today's trading data from all sources.
"""

import json
import logging
import os
import subprocess
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def collect_daily_data(config: Dict[str, Any], today_str: str) -> Dict[str, Any]:
    """
    Collect all data HERMES needs for daily analysis.

    Args:
        config: Agent config dict.
        today_str: Date string "YYYY-MM-DD".

    Returns:
        Dict with all collected data (keys: apollo_report, daily_summary,
        positions, state, metrics, journal_logs).
    """
    data = {}

    # 1. Apollo's morning report
    data["apollo_report"] = _read_apollo_report(config, today_str)

    # 2. Google Sheets data
    data["daily_summary"] = _read_sheets_daily_summary(config)
    data["positions"] = _read_sheets_positions(config)

    # 3. State file
    data["state"] = _read_json_file(
        config.get("hermes", {}).get("state_file", "data/hydra_state.json")
    )

    # 4. Metrics file
    data["metrics"] = _read_json_file(
        config.get("hermes", {}).get("metrics_file", "data/hydra_metrics.json")
    )

    # 5. Journal logs
    journal_lines = config.get("hermes", {}).get("journal_lines", 200)
    data["journal_logs"] = _read_journal_logs(journal_lines)

    # Summarize what we collected
    collected = [k for k, v in data.items() if v]
    missing = [k for k, v in data.items() if not v]
    logger.info(f"Collected: {', '.join(collected)}")
    if missing:
        logger.warning(f"Missing: {', '.join(missing)}")

    return data


def _read_apollo_report(config: Dict[str, Any], today_str: str) -> Optional[str]:
    """Read today's Apollo morning report if it exists."""
    apollo_dir = config.get("apollo", {}).get("report_dir", "intel/apollo")
    report_path = os.path.join(apollo_dir, f"{today_str}.md")

    if os.path.exists(report_path):
        try:
            with open(report_path) as f:
                content = f.read()
            logger.info(f"Read Apollo report: {report_path} ({len(content)} chars)")
            return content
        except IOError as e:
            logger.warning(f"Failed to read Apollo report: {e}")

    logger.info("No Apollo report for today (Apollo may not have run yet)")
    return None


def _read_sheets_daily_summary(config: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Read today's daily summary row from Google Sheets."""
    try:
        from shared.sheets_reader import SheetsReader

        spreadsheet = config.get("google_sheets", {}).get(
            "spreadsheet_name", "Calypso_HYDRA_Live_Data"
        )
        reader = SheetsReader(config)
        return reader.get_last_row_as_dict(spreadsheet, "Daily Summary")
    except Exception as e:
        logger.warning(f"Failed to read Daily Summary from Sheets: {e}")
        return None


def _read_sheets_positions(config: Dict[str, Any]) -> Optional[List[Dict[str, str]]]:
    """Read today's position entries from Google Sheets."""
    try:
        from shared.sheets_reader import SheetsReader

        spreadsheet = config.get("google_sheets", {}).get(
            "spreadsheet_name", "Calypso_HYDRA_Live_Data"
        )
        reader = SheetsReader(config)
        # Read last 20 rows (max 6 entries × ~3 rows each for a typical day)
        return reader.read_tab_as_dicts(spreadsheet, "Positions", limit_rows=20)
    except Exception as e:
        logger.warning(f"Failed to read Positions from Sheets: {e}")
        return None


def _read_json_file(path: str) -> Optional[Dict]:
    """Read a JSON file, returning None on error."""
    if not os.path.exists(path):
        logger.info(f"File not found: {path}")
        return None

    try:
        with open(path) as f:
            data = json.load(f)
        logger.info(f"Read {path}")
        return data
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Failed to read {path}: {e}")
        return None


def _read_journal_logs(lines: int = 200) -> Optional[str]:
    """Read recent HYDRA journal logs via journalctl."""
    try:
        result = subprocess.run(
            ["journalctl", "-u", "hydra", "--since", "today", "-n", str(lines),
             "--no-pager"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            logger.info(f"Read {len(result.stdout.splitlines())} journal log lines")
            return result.stdout
        logger.info("No HYDRA journal logs for today")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("journalctl timed out")
        return None
    except FileNotFoundError:
        logger.info("journalctl not available (running locally?)")
        return None
