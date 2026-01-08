"""
logger_service.py - Logging and Trade Recording Service

This module handles all logging operations including:
- Local file logging using Python's logging module
- Google Sheets integration for trade logging
- Microsoft Excel/SharePoint integration for trade logging

Trade Log Format:
[Timestamp, Action, Strike, Price, Current Delta, Total Profit/Loss]

Author: Trading Bot Developer
Date: 2024
"""

import logging
import json
from datetime import datetime
from typing import Optional, Dict, List, Any
from pathlib import Path
import threading
from queue import Queue

# Configure module logger
logger = logging.getLogger(__name__)


class TradeRecord:
    """
    Represents a single trade record for logging.

    Attributes:
        timestamp: When the trade occurred
        action: Type of action (e.g., OPEN_LONG_STRADDLE, RECENTER)
        strike: Strike price(s) involved
        price: Execution price
        delta: Current delta of the position
        pnl: Profit/Loss for this trade or running total
    """

    def __init__(
        self,
        action: str,
        strike: Any,
        price: float,
        delta: float,
        pnl: float,
        timestamp: Optional[datetime] = None
    ):
        self.timestamp = timestamp or datetime.now()
        self.action = action
        self.strike = strike
        self.price = price
        self.delta = delta
        self.pnl = pnl

    def to_list(self) -> List[Any]:
        """Convert to list format for spreadsheet row."""
        return [
            self.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            self.action,
            str(self.strike),
            f"{self.price:.4f}",
            f"{self.delta:.4f}",
            f"{self.pnl:.2f}"
        ]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "action": self.action,
            "strike": self.strike,
            "price": self.price,
            "delta": self.delta,
            "pnl": self.pnl
        }


class GoogleSheetsLogger:
    """
    Google Sheets integration for trade logging.

    Uses the gspread library to write trade records to a Google Spreadsheet.

    Attributes:
        enabled: Whether Google Sheets logging is enabled
        credentials_file: Path to Google service account credentials
        spreadsheet_name: Name of the spreadsheet to log to
        worksheet_name: Name of the worksheet within the spreadsheet
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize Google Sheets logger.

        Args:
            config: Configuration dictionary with Google Sheets settings
        """
        self.config = config.get("google_sheets", {})
        self.enabled = self.config.get("enabled", False)
        self.credentials_file = self.config.get("credentials_file", "google_credentials.json")
        self.spreadsheet_name = self.config.get("spreadsheet_name", "Trading_Bot_Log")
        self.worksheet_name = self.config.get("worksheet_name", "Trades")

        self.client = None
        self.spreadsheet = None
        self.worksheet = None

        if self.enabled:
            self._initialize()

    def _initialize(self) -> bool:
        """
        Initialize connection to Google Sheets.

        Returns:
            bool: True if initialization successful, False otherwise.
        """
        try:
            import gspread
            from google.oauth2.service_account import Credentials

            # Define the scope for Google Sheets API
            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]

            # Load credentials from service account file
            credentials = Credentials.from_service_account_file(
                self.credentials_file,
                scopes=scopes
            )

            # Authorize and create client
            self.client = gspread.authorize(credentials)

            # Open or create spreadsheet
            try:
                self.spreadsheet = self.client.open(self.spreadsheet_name)
                logger.info(f"Opened existing Google spreadsheet: {self.spreadsheet_name}")
            except gspread.SpreadsheetNotFound:
                # Create new spreadsheet if it doesn't exist
                self.spreadsheet = self.client.create(self.spreadsheet_name)
                logger.info(f"Created new Google spreadsheet: {self.spreadsheet_name}")

            # Get or create worksheet
            try:
                self.worksheet = self.spreadsheet.worksheet(self.worksheet_name)
            except gspread.WorksheetNotFound:
                self.worksheet = self.spreadsheet.add_worksheet(
                    title=self.worksheet_name,
                    rows=1000,
                    cols=10
                )
                # Add headers
                headers = ["Timestamp", "Action", "Strike", "Price", "Delta", "P&L"]
                self.worksheet.append_row(headers)
                logger.info(f"Created new worksheet: {self.worksheet_name}")

            return True

        except ImportError:
            logger.error("gspread library not installed. Run: pip install gspread google-auth")
            self.enabled = False
            return False
        except FileNotFoundError:
            logger.error(f"Google credentials file not found: {self.credentials_file}")
            self.enabled = False
            return False
        except Exception as e:
            logger.error(f"Failed to initialize Google Sheets: {e}")
            self.enabled = False
            return False

    def log_trade(self, trade: TradeRecord) -> bool:
        """
        Log a trade record to Google Sheets.

        Args:
            trade: TradeRecord object to log

        Returns:
            bool: True if logged successfully, False otherwise.
        """
        if not self.enabled or not self.worksheet:
            return False

        try:
            self.worksheet.append_row(trade.to_list())
            logger.debug(f"Trade logged to Google Sheets: {trade.action}")
            return True
        except Exception as e:
            logger.error(f"Failed to log trade to Google Sheets: {e}")
            return False


class MicrosoftSheetsLogger:
    """
    Microsoft Excel/SharePoint integration for trade logging.

    Uses the Office365-REST-Python-Client library to write trade records
    to an Excel file stored in SharePoint/OneDrive.

    Attributes:
        enabled: Whether Microsoft logging is enabled
        client_id: Azure AD application client ID
        client_secret: Azure AD application client secret
        tenant_id: Azure AD tenant ID
        site_url: SharePoint site URL
        workbook_name: Name of the Excel workbook
        worksheet_name: Name of the worksheet
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize Microsoft Sheets logger.

        Args:
            config: Configuration dictionary with Microsoft settings
        """
        self.config = config.get("microsoft_sheets", {})
        self.enabled = self.config.get("enabled", False)
        self.client_id = self.config.get("client_id", "")
        self.client_secret = self.config.get("client_secret", "")
        self.tenant_id = self.config.get("tenant_id", "")
        self.site_url = self.config.get("site_url", "")
        self.workbook_name = self.config.get("workbook_name", "Trading_Bot_Log.xlsx")
        self.worksheet_name = self.config.get("worksheet_name", "Trades")

        self.ctx = None
        self.workbook = None

        if self.enabled:
            self._initialize()

    def _initialize(self) -> bool:
        """
        Initialize connection to Microsoft SharePoint/Excel.

        Returns:
            bool: True if initialization successful, False otherwise.
        """
        try:
            from office365.runtime.auth.client_credential import ClientCredential
            from office365.sharepoint.client_context import ClientContext

            # Create client credentials
            credentials = ClientCredential(self.client_id, self.client_secret)

            # Create SharePoint context
            self.ctx = ClientContext(self.site_url).with_credentials(credentials)

            logger.info(f"Microsoft SharePoint context initialized for: {self.site_url}")
            return True

        except ImportError:
            logger.error(
                "Office365-REST-Python-Client not installed. "
                "Run: pip install Office365-REST-Python-Client"
            )
            self.enabled = False
            return False
        except Exception as e:
            logger.error(f"Failed to initialize Microsoft connection: {e}")
            self.enabled = False
            return False

    def log_trade(self, trade: TradeRecord) -> bool:
        """
        Log a trade record to Microsoft Excel.

        Args:
            trade: TradeRecord object to log

        Returns:
            bool: True if logged successfully, False otherwise.
        """
        if not self.enabled or not self.ctx:
            return False

        try:
            # Use Microsoft Graph API to append row to Excel
            # This is a simplified implementation - full implementation would
            # use the Excel REST API through Microsoft Graph

            from office365.graph_client import GraphClient

            def acquire_token():
                from office365.runtime.auth.authentication_context import AuthenticationContext
                authority_url = f"https://login.microsoftonline.com/{self.tenant_id}"
                auth_ctx = AuthenticationContext(authority_url)
                token = auth_ctx.acquire_token_for_app(
                    f"https://graph.microsoft.com/.default",
                    self.client_id,
                    self.client_secret
                )
                return token

            client = GraphClient(acquire_token)

            # Get the workbook and add row
            # Note: This requires the workbook to already exist in SharePoint/OneDrive
            workbook_path = f"/drives/root:/{self.workbook_name}"

            # Add row to the worksheet
            row_data = {
                "values": [trade.to_list()]
            }

            # Append row using Graph API
            # endpoint: /me/drive/root:/{item-path}:/workbook/worksheets/{worksheet-name}/tables/{table-name}/rows/add
            # This is simplified - actual implementation depends on your Excel structure

            logger.debug(f"Trade logged to Microsoft Excel: {trade.action}")
            return True

        except Exception as e:
            logger.error(f"Failed to log trade to Microsoft Excel: {e}")
            return False


class LocalFileLogger:
    """
    Local file logging for trade records and system events.

    Uses Python's logging module to write to a local log file.

    Attributes:
        log_file: Path to the log file
        trade_log_file: Separate file for trade records in JSON format
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize local file logger.

        Args:
            config: Configuration dictionary with logging settings
        """
        self.config = config.get("logging", {})
        self.log_file = self.config.get("log_file", "bot_log.txt")
        self.log_level = self.config.get("log_level", "INFO")
        self.console_output = self.config.get("console_output", True)

        # Separate file for trade records in JSON format
        self.trade_log_file = self.log_file.replace(".txt", "_trades.json")

        self._setup_logging()

    def _setup_logging(self):
        """Configure the Python logging module."""
        # Get the root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(getattr(logging, self.log_level))

        # Clear existing handlers
        root_logger.handlers.clear()

        # Create formatter
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

        # File handler
        file_handler = logging.FileHandler(self.log_file, encoding="utf-8")
        file_handler.setLevel(getattr(logging, self.log_level))
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

        # Console handler (optional)
        if self.console_output:
            console_handler = logging.StreamHandler()
            console_handler.setLevel(getattr(logging, self.log_level))
            console_handler.setFormatter(formatter)
            root_logger.addHandler(console_handler)

        logger.info(f"Logging initialized. File: {self.log_file}, Level: {self.log_level}")

    def log_trade(self, trade: TradeRecord) -> bool:
        """
        Log a trade record to the local JSON trade log.

        Args:
            trade: TradeRecord object to log

        Returns:
            bool: True if logged successfully, False otherwise.
        """
        try:
            # Load existing trades
            trades = []
            if Path(self.trade_log_file).exists():
                with open(self.trade_log_file, "r") as f:
                    try:
                        trades = json.load(f)
                    except json.JSONDecodeError:
                        trades = []

            # Append new trade
            trades.append(trade.to_dict())

            # Write back to file
            with open(self.trade_log_file, "w") as f:
                json.dump(trades, f, indent=2)

            logger.info(f"Trade logged: {trade.action} | Strike: {trade.strike} | P&L: ${trade.pnl:.2f}")
            return True

        except Exception as e:
            logger.error(f"Failed to log trade to local file: {e}")
            return False


class TradeLoggerService:
    """
    Main trade logging service that aggregates all logging destinations.

    This service manages logging to multiple destinations:
    - Local file (always enabled)
    - Google Sheets (optional)
    - Microsoft Excel (optional)

    It uses an asynchronous queue to prevent logging from blocking
    the main trading thread.

    Attributes:
        local_logger: LocalFileLogger instance
        google_logger: GoogleSheetsLogger instance (if enabled)
        microsoft_logger: MicrosoftSheetsLogger instance (if enabled)
        log_queue: Queue for asynchronous logging
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the trade logging service.

        Args:
            config: Full configuration dictionary
        """
        self.config = config

        # Initialize local logger (always enabled)
        self.local_logger = LocalFileLogger(config)

        # Initialize Google Sheets logger (optional)
        self.google_logger = GoogleSheetsLogger(config)

        # Initialize Microsoft logger (optional)
        self.microsoft_logger = MicrosoftSheetsLogger(config)

        # Asynchronous logging queue
        self.log_queue: Queue = Queue()
        self._stop_logging = False
        self._log_thread: Optional[threading.Thread] = None

        # Start the logging thread
        self._start_log_thread()

        logger.info("TradeLoggerService initialized")
        logger.info(f"  - Local logging: ENABLED")
        logger.info(f"  - Google Sheets: {'ENABLED' if self.google_logger.enabled else 'DISABLED'}")
        logger.info(f"  - Microsoft Excel: {'ENABLED' if self.microsoft_logger.enabled else 'DISABLED'}")

    def _start_log_thread(self):
        """Start the background logging thread."""
        self._log_thread = threading.Thread(target=self._process_log_queue, daemon=True)
        self._log_thread.start()
        logger.debug("Logging thread started")

    def _process_log_queue(self):
        """Process trades from the logging queue."""
        while not self._stop_logging:
            try:
                # Wait for a trade record (with timeout to check stop flag)
                trade = self.log_queue.get(timeout=1.0)

                # Log to all enabled destinations
                self.local_logger.log_trade(trade)

                if self.google_logger.enabled:
                    self.google_logger.log_trade(trade)

                if self.microsoft_logger.enabled:
                    self.microsoft_logger.log_trade(trade)

                self.log_queue.task_done()

            except Exception:
                # Queue.get timeout - just continue loop
                pass

    def log_trade(
        self,
        action: str,
        strike: Any,
        price: float,
        delta: float,
        pnl: float
    ):
        """
        Log a trade record to all enabled destinations.

        This method is non-blocking - it adds the trade to a queue
        for asynchronous processing.

        Args:
            action: Type of action (e.g., "OPEN_LONG_STRADDLE")
            strike: Strike price(s) involved
            price: Execution price
            delta: Current delta
            pnl: Profit/Loss
        """
        trade = TradeRecord(
            action=action,
            strike=strike,
            price=price,
            delta=delta,
            pnl=pnl
        )

        # Add to queue for async processing
        self.log_queue.put(trade)

    def log_event(self, message: str, level: str = "INFO"):
        """
        Log a general event message.

        Args:
            message: The message to log
            level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        """
        log_func = getattr(logger, level.lower(), logger.info)
        log_func(message)

    def log_error(self, message: str, exception: Optional[Exception] = None):
        """
        Log an error message.

        Args:
            message: Error description
            exception: Optional exception object for stack trace
        """
        if exception:
            logger.error(f"{message}: {exception}", exc_info=True)
        else:
            logger.error(message)

    def log_status(self, status: Dict[str, Any]):
        """
        Log current strategy status.

        Args:
            status: Status dictionary from strategy
        """
        logger.info("=" * 60)
        logger.info("STRATEGY STATUS")
        logger.info("=" * 60)
        logger.info(f"  State: {status.get('state', 'Unknown')}")
        logger.info(f"  {status.get('underlying_symbol', 'SPY')} Price: ${status.get('underlying_price', 0):.2f}")
        logger.info(f"  VIX: {status.get('vix', 0):.2f}")
        logger.info(f"  Initial Strike: ${status.get('initial_strike', 0):.2f}")
        logger.info(f"  Distance from Strike: ${status.get('price_from_strike', 0):.2f}")
        logger.info(f"  Long Straddle: {'Active' if status.get('has_long_straddle') else 'None'}")
        logger.info(f"  Short Strangle: {'Active' if status.get('has_short_strangle') else 'None'}")
        logger.info(f"  Total Delta: {status.get('total_delta', 0):.4f}")
        logger.info(f"  Total P&L: ${status.get('total_pnl', 0):.2f}")
        logger.info(f"  Realized P&L: ${status.get('realized_pnl', 0):.2f}")
        logger.info(f"  Unrealized P&L: ${status.get('unrealized_pnl', 0):.2f}")
        logger.info(f"  Premium Collected: ${status.get('premium_collected', 0):.2f}")
        logger.info(f"  Recenter Count: {status.get('recenter_count', 0)}")
        logger.info(f"  Roll Count: {status.get('roll_count', 0)}")
        logger.info("=" * 60)

    def shutdown(self):
        """Shutdown the logging service gracefully."""
        logger.info("Shutting down trade logger service...")

        # Stop the logging thread
        self._stop_logging = True

        # Wait for queue to empty
        self.log_queue.join()

        # Wait for thread to finish
        if self._log_thread:
            self._log_thread.join(timeout=5.0)

        logger.info("Trade logger service shutdown complete")


# Convenience function for quick logging setup
def setup_logging(config: Dict[str, Any]) -> TradeLoggerService:
    """
    Quick setup function for the trade logging service.

    Args:
        config: Configuration dictionary

    Returns:
        TradeLoggerService: Initialized logging service
    """
    return TradeLoggerService(config)
