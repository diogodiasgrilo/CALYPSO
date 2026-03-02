"""
Read-only Google Sheets access for CALYPSO agents.

Provides read-only access to Google Sheets data using gspread.
Uses the same authentication pattern as logger_service.py (Secret Manager on GCP,
local credentials file for development) but with READ-ONLY scopes.

All API calls are wrapped in daemon threads with timeout protection (Fix #64 pattern)
to prevent agent freeze when Google Sheets API hangs.

Usage:
    from shared.sheets_reader import SheetsReader

    reader = SheetsReader(config)
    rows = reader.read_tab_as_dicts("Calypso_HYDRA_Live_Data", "Daily Summary")
    raw = reader.read_tab_raw("Calypso_HYDRA_Live_Data", "Positions", limit_rows=50)
"""

import logging
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Read-only scopes — cannot modify spreadsheets
READONLY_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

# Default timeout for Sheets API calls (seconds)
SHEETS_API_TIMEOUT = 15


class SheetsReader:
    """Read-only Google Sheets client for CALYPSO agents."""

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize SheetsReader.

        Args:
            config: Agent config dict. Uses config["google_sheets"] if present,
                    otherwise uses config directly for credentials_file.
        """
        sheets_config = config.get("google_sheets", {})
        self.credentials_file = sheets_config.get(
            "credentials_file", "config/google_credentials.json"
        )
        self.timeout = sheets_config.get("timeout", SHEETS_API_TIMEOUT)
        self.client = None
        self._initialize()

    def _initialize(self) -> bool:
        """Authenticate with Google Sheets API."""
        try:
            import gspread
            from google.oauth2.service_account import Credentials
        except ImportError:
            logger.error("gspread or google-auth not installed")
            return False

        try:
            from shared.secret_manager import (
                get_google_sheets_credentials,
                is_running_on_gcp,
            )

            if is_running_on_gcp():
                creds_data = get_google_sheets_credentials()
                if not creds_data:
                    logger.error("Failed to get Sheets credentials from Secret Manager")
                    return False
                credentials = Credentials.from_service_account_info(
                    creds_data, scopes=READONLY_SCOPES
                )
            else:
                credentials = Credentials.from_service_account_file(
                    self.credentials_file, scopes=READONLY_SCOPES
                )

            self.client = gspread.authorize(credentials)
            logger.info("SheetsReader initialized (read-only)")
            return True

        except FileNotFoundError:
            logger.error(f"Credentials file not found: {self.credentials_file}")
            return False
        except Exception as e:
            logger.error(f"SheetsReader initialization failed: {e}")
            return False

    def _call_with_timeout(self, func, *args, timeout: float = None, **kwargs):
        """
        Execute a Google Sheets API call with timeout protection.

        Runs the call in a daemon thread. Returns None if timeout exceeded
        or an error occurs (graceful degradation — agents continue without data).

        Same pattern as logger_service.py Fix #64.
        """
        if timeout is None:
            timeout = self.timeout

        result = [None]
        exception = [None]

        def target():
            try:
                result[0] = func(*args, **kwargs)
            except Exception as e:
                exception[0] = e

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        thread.join(timeout=timeout)

        if thread.is_alive():
            logger.warning(
                f"Sheets API call timed out after {timeout}s: {func.__name__}"
            )
            return None

        if exception[0] is not None:
            logger.warning(f"Sheets API call failed: {exception[0]}")
            return None

        return result[0]

    def read_tab_as_dicts(
        self,
        spreadsheet_name: str,
        tab_name: str,
        limit_rows: int = 0,
    ) -> Optional[List[Dict[str, str]]]:
        """
        Read a worksheet tab as a list of dicts (header row becomes keys).

        Args:
            spreadsheet_name: Name of the Google Spreadsheet.
            tab_name: Name of the worksheet tab.
            limit_rows: If > 0, return only the last N rows. 0 = all rows.

        Returns:
            List of dicts (one per row), or None on error/timeout.
        """
        if not self.client:
            logger.warning("SheetsReader not initialized")
            return None

        try:
            spreadsheet = self._call_with_timeout(
                self.client.open, spreadsheet_name
            )
            if spreadsheet is None:
                return None

            worksheet = self._call_with_timeout(spreadsheet.worksheet, tab_name)
            if worksheet is None:
                logger.warning(f"Worksheet not found or timed out: {tab_name}")
                return None

            records = self._call_with_timeout(worksheet.get_all_records)
            if records is None:
                return None

            if limit_rows > 0 and len(records) > limit_rows:
                records = records[-limit_rows:]

            return records

        except Exception as e:
            logger.error(f"Failed to read {spreadsheet_name}/{tab_name}: {e}")
            return None

    def read_tab_raw(
        self,
        spreadsheet_name: str,
        tab_name: str,
        limit_rows: int = 0,
    ) -> Optional[List[List[str]]]:
        """
        Read a worksheet tab as raw list of lists (including header row).

        Args:
            spreadsheet_name: Name of the Google Spreadsheet.
            tab_name: Name of the worksheet tab.
            limit_rows: If > 0, return header + last N data rows. 0 = all rows.

        Returns:
            List of lists (first row = headers), or None on error/timeout.
        """
        if not self.client:
            logger.warning("SheetsReader not initialized")
            return None

        try:
            spreadsheet = self._call_with_timeout(
                self.client.open, spreadsheet_name
            )
            if spreadsheet is None:
                return None

            worksheet = self._call_with_timeout(spreadsheet.worksheet, tab_name)
            if worksheet is None:
                logger.warning(f"Worksheet not found or timed out: {tab_name}")
                return None

            all_data = self._call_with_timeout(worksheet.get_all_values)
            if all_data is None or len(all_data) == 0:
                return all_data

            if limit_rows > 0 and len(all_data) > limit_rows + 1:
                # Keep header row + last N data rows
                all_data = [all_data[0]] + all_data[-(limit_rows):]

            return all_data

        except Exception as e:
            logger.error(f"Failed to read {spreadsheet_name}/{tab_name}: {e}")
            return None

    def get_last_row_as_dict(
        self,
        spreadsheet_name: str,
        tab_name: str,
    ) -> Optional[Dict[str, str]]:
        """
        Read the last (most recent) row from a worksheet tab as a dict.

        Args:
            spreadsheet_name: Name of the Google Spreadsheet.
            tab_name: Name of the worksheet tab.

        Returns:
            Dict with header keys and last row values, or None on error.
        """
        records = self.read_tab_as_dicts(spreadsheet_name, tab_name, limit_rows=1)
        if records and len(records) > 0:
            return records[-1]
        return None
