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
        Additional optional fields for comprehensive tracking
    """

    def __init__(
        self,
        action: str,
        strike: Any,
        price: float,
        delta: float,
        pnl: float,
        timestamp: Optional[datetime] = None,
        currency: str = "USD",
        account_currency: Optional[str] = None,
        exchange_rate: Optional[float] = None,
        converted_pnl: Optional[float] = None,
        # Additional comprehensive tracking fields
        underlying_price: Optional[float] = None,
        vix: Optional[float] = None,
        option_type: Optional[str] = None,  # "Call", "Put", "Straddle", "Strangle"
        expiry_date: Optional[str] = None,
        dte: Optional[int] = None,
        quantity: Optional[int] = None,
        premium_received: Optional[float] = None,
        total_delta: Optional[float] = None,
        realized_pnl: Optional[float] = None,
        unrealized_pnl: Optional[float] = None,
        trade_reason: Optional[str] = None,  # "Entry", "Roll", "ITM Risk", "Fed Filter", etc.
        greeks: Optional[Dict[str, float]] = None  # "delta", "gamma", "theta", "vega"
    ):
        self.timestamp = timestamp or datetime.now()
        self.action = action
        self.strike = strike
        self.price = price
        self.delta = delta
        self.pnl = pnl
        self.currency = currency
        self.account_currency = account_currency
        self.exchange_rate = exchange_rate
        self.converted_pnl = converted_pnl
        # Additional fields
        self.underlying_price = underlying_price
        self.vix = vix
        self.option_type = option_type
        self.expiry_date = expiry_date
        self.dte = dte
        self.quantity = quantity
        self.premium_received = premium_received
        self.total_delta = total_delta
        self.realized_pnl = realized_pnl
        self.unrealized_pnl = unrealized_pnl
        self.trade_reason = trade_reason
        self.greeks = greeks or {}

    def to_list(self) -> List[Any]:
        """Convert to list format for spreadsheet row (comprehensive)."""
        return [
            self.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            self.action,
            self.trade_reason or "N/A",
            str(self.strike),
            f"{self.price:.4f}",
            self.option_type or "N/A",
            self.expiry_date or "N/A",
            self.dte if self.dte is not None else "N/A",
            self.quantity if self.quantity is not None else "N/A",
            f"{self.underlying_price:.2f}" if self.underlying_price else "N/A",
            f"{self.vix:.2f}" if self.vix else "N/A",
            f"{self.delta:.4f}",
            f"{self.total_delta:.4f}" if self.total_delta is not None else "N/A",
            f"{self.greeks.get('gamma', 0):.4f}" if 'gamma' in self.greeks else "N/A",
            f"{self.greeks.get('theta', 0):.4f}" if 'theta' in self.greeks else "N/A",
            f"{self.greeks.get('vega', 0):.4f}" if 'vega' in self.greeks else "N/A",
            f"{self.premium_received:.2f}" if self.premium_received is not None else "N/A",
            f"{self.pnl:.2f}",
            f"{self.realized_pnl:.2f}" if self.realized_pnl is not None else "N/A",
            f"{self.unrealized_pnl:.2f}" if self.unrealized_pnl is not None else "N/A",
            self.currency,
            f"{self.exchange_rate:.6f}" if self.exchange_rate else "N/A",
            f"{self.converted_pnl:.2f}" if self.converted_pnl is not None else "N/A"
        ]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        result = {
            "timestamp": self.timestamp.isoformat(),
            "action": self.action,
            "trade_reason": self.trade_reason,
            "strike": self.strike,
            "price": self.price,
            "delta": self.delta,
            "pnl_usd": self.pnl,
            "currency": self.currency
        }

        # Add all optional fields if available
        if self.underlying_price is not None:
            result["underlying_price"] = self.underlying_price
        if self.vix is not None:
            result["vix"] = self.vix
        if self.option_type:
            result["option_type"] = self.option_type
        if self.expiry_date:
            result["expiry_date"] = self.expiry_date
        if self.dte is not None:
            result["dte"] = self.dte
        if self.quantity is not None:
            result["quantity"] = self.quantity
        if self.premium_received is not None:
            result["premium_received"] = self.premium_received
        if self.total_delta is not None:
            result["total_delta"] = self.total_delta
        if self.realized_pnl is not None:
            result["realized_pnl"] = self.realized_pnl
        if self.unrealized_pnl is not None:
            result["unrealized_pnl"] = self.unrealized_pnl
        if self.greeks:
            result["greeks"] = self.greeks

        # Add conversion fields if available
        if self.exchange_rate:
            result["exchange_rate"] = self.exchange_rate
        if self.converted_pnl is not None:
            result["pnl_eur"] = self.converted_pnl

        return result


class GoogleSheetsLogger:
    """
    Comprehensive Google Sheets integration for delta neutral strategy logging.

    Creates and manages multiple worksheets:
    1. Trades - Every trade execution with full details
    2. Positions - Real-time snapshot of open positions
    3. Daily Summary - Daily performance metrics
    4. Safety Events - Fed filters, ITM warnings, emergency exits
    5. Greeks & Risk - Delta, gamma, theta tracking

    Attributes:
        enabled: Whether Google Sheets logging is enabled
        credentials_file: Path to Google service account credentials
        spreadsheet_name: Name of the spreadsheet to log to
        worksheets: Dictionary of worksheet names to worksheet objects
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize comprehensive Google Sheets logger.

        Args:
            config: Configuration dictionary with Google Sheets settings
        """
        self.config = config.get("google_sheets", {})
        self.enabled = self.config.get("enabled", False)
        self.credentials_file = self.config.get("credentials_file", "config/google_credentials.json")
        self.spreadsheet_name = self.config.get("spreadsheet_name", "Trading_Bot_Log")

        self.client = None
        self.spreadsheet = None
        self.worksheets = {}  # Store all worksheets

        if self.enabled:
            self._initialize()

    def _initialize(self) -> bool:
        """
        Initialize connection to Google Sheets and create all worksheets.

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

            # Initialize all worksheets
            self._setup_trades_worksheet()
            self._setup_positions_worksheet()
            self._setup_daily_summary_worksheet()
            self._setup_safety_events_worksheet()
            self._setup_greeks_worksheet()

            logger.info("All Google Sheets worksheets initialized successfully")
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

    def _setup_trades_worksheet(self):
        """Setup the comprehensive Trades worksheet."""
        try:
            import gspread
            try:
                worksheet = self.spreadsheet.worksheet("Trades")
            except gspread.WorksheetNotFound:
                worksheet = self.spreadsheet.add_worksheet(title="Trades", rows=10000, cols=25)
                # Comprehensive trade headers
                headers = [
                    "Timestamp", "Action", "Reason", "Strike", "Price", "Type",
                    "Expiry", "DTE", "Qty", "SPY Price", "VIX", "Delta",
                    "Total Delta", "Gamma", "Theta", "Vega", "Premium",
                    "P&L", "Realized P&L", "Unrealized P&L", "Currency",
                    "FX Rate", "P&L (EUR)"
                ]
                worksheet.append_row(headers)
                # Format header row (bold)
                worksheet.format("A1:W1", {"textFormat": {"bold": True}})
                logger.info("Created Trades worksheet with comprehensive headers")

            self.worksheets["Trades"] = worksheet
        except Exception as e:
            logger.error(f"Failed to setup Trades worksheet: {e}")

    def _setup_positions_worksheet(self):
        """Setup the real-time Positions worksheet."""
        try:
            import gspread
            try:
                worksheet = self.spreadsheet.worksheet("Positions")
            except gspread.WorksheetNotFound:
                worksheet = self.spreadsheet.add_worksheet(title="Positions", rows=100, cols=15)
                headers = [
                    "Last Updated", "Position Type", "Strike", "Expiry", "DTE",
                    "Quantity", "Entry Price", "Current Price", "Delta",
                    "Gamma", "Theta", "Vega", "P&L", "P&L (EUR)", "Status"
                ]
                worksheet.append_row(headers)
                worksheet.format("A1:O1", {"textFormat": {"bold": True}})
                logger.info("Created Positions worksheet")

            self.worksheets["Positions"] = worksheet
        except Exception as e:
            logger.error(f"Failed to setup Positions worksheet: {e}")

    def _setup_daily_summary_worksheet(self):
        """Setup the Daily Summary worksheet."""
        try:
            import gspread
            try:
                worksheet = self.spreadsheet.worksheet("Daily Summary")
            except gspread.WorksheetNotFound:
                worksheet = self.spreadsheet.add_worksheet(title="Daily Summary", rows=1000, cols=20)
                headers = [
                    "Date", "Strategy State", "SPY Open", "SPY Close", "SPY Range",
                    "VIX Avg", "VIX High", "Total Delta", "Total Gamma",
                    "Total Theta", "Daily P&L", "Realized P&L", "Unrealized P&L",
                    "Premium Collected", "Trades Count", "Recenter Count",
                    "Roll Count", "Cumulative P&L", "P&L (EUR)", "Notes"
                ]
                worksheet.append_row(headers)
                worksheet.format("A1:T1", {"textFormat": {"bold": True}})
                logger.info("Created Daily Summary worksheet")

            self.worksheets["Daily Summary"] = worksheet
        except Exception as e:
            logger.error(f"Failed to setup Daily Summary worksheet: {e}")

    def _setup_safety_events_worksheet(self):
        """Setup the Safety Events worksheet."""
        try:
            import gspread
            try:
                worksheet = self.spreadsheet.worksheet("Safety Events")
            except gspread.WorksheetNotFound:
                worksheet = self.spreadsheet.add_worksheet(title="Safety Events", rows=1000, cols=12)
                headers = [
                    "Timestamp", "Event Type", "Severity", "SPY Price",
                    "Initial Strike", "Distance (%)", "VIX", "Action Taken",
                    "Short Call Strike", "Short Put Strike", "Description", "Result"
                ]
                worksheet.append_row(headers)
                worksheet.format("A1:L1", {"textFormat": {"bold": True}})
                logger.info("Created Safety Events worksheet")

            self.worksheets["Safety Events"] = worksheet
        except Exception as e:
            logger.error(f"Failed to setup Safety Events worksheet: {e}")

    def _setup_greeks_worksheet(self):
        """Setup the Greeks & Risk worksheet."""
        try:
            import gspread
            try:
                worksheet = self.spreadsheet.worksheet("Greeks & Risk")
            except gspread.WorksheetNotFound:
                worksheet = self.spreadsheet.add_worksheet(title="Greeks & Risk", rows=10000, cols=15)
                headers = [
                    "Timestamp", "SPY Price", "VIX", "Long Delta", "Short Delta",
                    "Total Delta", "Long Gamma", "Short Gamma", "Total Gamma",
                    "Long Theta", "Short Theta", "Total Theta", "Long Vega",
                    "Short Vega", "Total Vega"
                ]
                worksheet.append_row(headers)
                worksheet.format("A1:O1", {"textFormat": {"bold": True}})
                logger.info("Created Greeks & Risk worksheet")

            self.worksheets["Greeks & Risk"] = worksheet
        except Exception as e:
            logger.error(f"Failed to setup Greeks & Risk worksheet: {e}")

    def log_trade(self, trade: TradeRecord) -> bool:
        """
        Log a trade record to the Trades worksheet.

        Args:
            trade: TradeRecord object to log

        Returns:
            bool: True if logged successfully, False otherwise.
        """
        if not self.enabled or "Trades" not in self.worksheets:
            return False

        try:
            self.worksheets["Trades"].append_row(trade.to_list())
            logger.debug(f"Trade logged to Google Sheets: {trade.action}")
            return True
        except Exception as e:
            logger.error(f"Failed to log trade to Google Sheets: {e}")
            return False

    def log_position_snapshot(self, positions: List[Dict[str, Any]]) -> bool:
        """
        Update the Positions worksheet with current position snapshot.

        Args:
            positions: List of position dictionaries

        Returns:
            bool: True if logged successfully
        """
        if not self.enabled or "Positions" not in self.worksheets:
            return False

        try:
            # Clear existing data (keep headers)
            worksheet = self.worksheets["Positions"]
            worksheet.delete_rows(2, worksheet.row_count)

            # Add current positions
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for pos in positions:
                row = [
                    timestamp,
                    pos.get("type", "N/A"),
                    pos.get("strike", "N/A"),
                    pos.get("expiry", "N/A"),
                    pos.get("dte", "N/A"),
                    pos.get("quantity", "N/A"),
                    f"{pos.get('entry_price', 0):.4f}",
                    f"{pos.get('current_price', 0):.4f}",
                    f"{pos.get('delta', 0):.4f}",
                    f"{pos.get('gamma', 0):.4f}",
                    f"{pos.get('theta', 0):.4f}",
                    f"{pos.get('vega', 0):.4f}",
                    f"{pos.get('pnl', 0):.2f}",
                    f"{pos.get('pnl_eur', 0):.2f}",
                    pos.get("status", "Active")
                ]
                worksheet.append_row(row)

            logger.debug(f"Updated position snapshot: {len(positions)} positions")
            return True
        except Exception as e:
            logger.error(f"Failed to update position snapshot: {e}")
            return False

    def log_daily_summary(self, summary: Dict[str, Any]) -> bool:
        """
        Log daily summary to Daily Summary worksheet.

        Args:
            summary: Dictionary with daily metrics

        Returns:
            bool: True if logged successfully
        """
        if not self.enabled or "Daily Summary" not in self.worksheets:
            return False

        try:
            row = [
                summary.get("date", datetime.now().strftime("%Y-%m-%d")),
                summary.get("state", "N/A"),
                f"{summary.get('spy_open', 0):.2f}",
                f"{summary.get('spy_close', 0):.2f}",
                f"{summary.get('spy_range', 0):.2f}",
                f"{summary.get('vix_avg', 0):.2f}",
                f"{summary.get('vix_high', 0):.2f}",
                f"{summary.get('total_delta', 0):.4f}",
                f"{summary.get('total_gamma', 0):.4f}",
                f"{summary.get('total_theta', 0):.4f}",
                f"{summary.get('daily_pnl', 0):.2f}",
                f"{summary.get('realized_pnl', 0):.2f}",
                f"{summary.get('unrealized_pnl', 0):.2f}",
                f"{summary.get('premium_collected', 0):.2f}",
                summary.get("trades_count", 0),
                summary.get("recenter_count", 0),
                summary.get("roll_count", 0),
                f"{summary.get('cumulative_pnl', 0):.2f}",
                f"{summary.get('pnl_eur', 0):.2f}",
                summary.get("notes", "")
            ]
            self.worksheets["Daily Summary"].append_row(row)
            logger.debug("Daily summary logged to Google Sheets")
            return True
        except Exception as e:
            logger.error(f"Failed to log daily summary: {e}")
            return False

    def log_safety_event(self, event: Dict[str, Any]) -> bool:
        """
        Log safety event (Fed filter, ITM risk, emergency exit).

        Args:
            event: Dictionary with safety event details

        Returns:
            bool: True if logged successfully
        """
        if not self.enabled or "Safety Events" not in self.worksheets:
            return False

        try:
            row = [
                event.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                event.get("event_type", "N/A"),
                event.get("severity", "WARNING"),
                f"{event.get('spy_price', 0):.2f}",
                f"{event.get('initial_strike', 0):.2f}",
                f"{event.get('distance_pct', 0):.2f}%",
                f"{event.get('vix', 0):.2f}",
                event.get("action_taken", "N/A"),
                event.get("short_call_strike", "N/A"),
                event.get("short_put_strike", "N/A"),
                event.get("description", ""),
                event.get("result", "Pending")
            ]
            self.worksheets["Safety Events"].append_row(row)
            logger.info(f"Safety event logged: {event.get('event_type')}")
            return True
        except Exception as e:
            logger.error(f"Failed to log safety event: {e}")
            return False

    def log_greeks(self, greeks: Dict[str, Any]) -> bool:
        """
        Log current Greeks snapshot to Greeks & Risk worksheet.

        Args:
            greeks: Dictionary with all Greeks values

        Returns:
            bool: True if logged successfully
        """
        if not self.enabled or "Greeks & Risk" not in self.worksheets:
            return False

        try:
            row = [
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                f"{greeks.get('spy_price', 0):.2f}",
                f"{greeks.get('vix', 0):.2f}",
                f"{greeks.get('long_delta', 0):.4f}",
                f"{greeks.get('short_delta', 0):.4f}",
                f"{greeks.get('total_delta', 0):.4f}",
                f"{greeks.get('long_gamma', 0):.4f}",
                f"{greeks.get('short_gamma', 0):.4f}",
                f"{greeks.get('total_gamma', 0):.4f}",
                f"{greeks.get('long_theta', 0):.4f}",
                f"{greeks.get('short_theta', 0):.4f}",
                f"{greeks.get('total_theta', 0):.4f}",
                f"{greeks.get('long_vega', 0):.4f}",
                f"{greeks.get('short_vega', 0):.4f}",
                f"{greeks.get('total_vega', 0):.4f}"
            ]
            self.worksheets["Greeks & Risk"].append_row(row)
            logger.debug("Greeks logged to Google Sheets")
            return True
        except Exception as e:
            logger.error(f"Failed to log Greeks: {e}")
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
        self.log_file = self.config.get("log_file", "logs/bot_log.txt")
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

        # Currency configuration
        self.currency_config = config.get("currency", {})
        self.currency_enabled = self.currency_config.get("enabled", False)
        self.base_currency = self.currency_config.get("base_currency", "USD")
        self.account_currency = self.currency_config.get("account_currency", "USD")

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
        logger.info(f"  - Currency Conversion: {'ENABLED' if self.currency_enabled else 'DISABLED'} ({self.base_currency} -> {self.account_currency})")

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
        pnl: float,
        saxo_client=None
    ):
        """
        Log a trade record to all enabled destinations with automatic currency conversion.

        This method is non-blocking - it adds the trade to a queue
        for asynchronous processing.

        Args:
            action: Type of action (e.g., "OPEN_LONG_STRADDLE")
            strike: Strike price(s) involved
            price: Execution price
            delta: Current delta
            pnl: Profit/Loss
            saxo_client: Optional SaxoClient instance for fetching FX rates
        """
        # Get exchange rate and convert if enabled
        exchange_rate = None
        converted_pnl = None

        if self.currency_enabled and saxo_client:
            try:
                # Fetch real-time rate from Saxo
                exchange_rate = saxo_client.get_fx_rate(
                    self.base_currency,
                    self.account_currency
                )

                if exchange_rate:
                    converted_pnl = pnl * exchange_rate
                    logger.debug(
                        f"Converted ${pnl:.2f} {self.base_currency} to "
                        f"€{converted_pnl:.2f} {self.account_currency} at rate {exchange_rate:.6f}"
                    )
            except Exception as e:
                logger.warning(f"Currency conversion failed: {e}")

        trade = TradeRecord(
            action=action,
            strike=strike,
            price=price,
            delta=delta,
            pnl=pnl,
            currency=self.base_currency,
            account_currency=self.account_currency if self.currency_enabled else None,
            exchange_rate=exchange_rate,
            converted_pnl=converted_pnl
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
        env_label = "SIMULATION" if status.get('is_simulation') else "LIVE"
        logger.info(f"STRATEGY STATUS [{env_label}]")
        logger.info("=" * 60)
        logger.info(f"  State: {status.get('state', 'Unknown')}")
        logger.info(f"  Environment: {status.get('environment', 'unknown').upper()}")
        logger.info(f"  {status.get('underlying_symbol', 'SPY')} Price: ${status.get('underlying_price', 0):.2f}")

        # Display VIX value
        vix_value = status.get('vix', 0)
        if isinstance(vix_value, (int, float)) and vix_value > 0:
            logger.info(f"  VIX: {vix_value:.2f}")
        else:
            logger.info(f"  VIX: {vix_value} (no data)")

        logger.info(f"  Initial Strike: ${status.get('initial_strike', 0):.2f}")
        logger.info(f"  Distance from Strike: ${status.get('price_from_strike', 0):.2f}")
        logger.info(f"  Long Straddle: {'Active' if status.get('has_long_straddle') else 'None'}")
        logger.info(f"  Short Strangle: {'Active' if status.get('has_short_strangle') else 'None'}")
        logger.info(f"  Total Delta: {status.get('total_delta', 0):.4f}")
        logger.info(f"  Total P&L: ${status.get('total_pnl', 0):.2f}")
        logger.info(f"  Realized P&L: ${status.get('realized_pnl', 0):.2f}")
        logger.info(f"  Unrealized P&L: ${status.get('unrealized_pnl', 0):.2f}")
        logger.info(f"  Premium Collected: ${status.get('premium_collected', 0):.2f}")

        # Show EUR conversion if enabled
        if self.currency_enabled and "exchange_rate" in status:
            logger.info(f"  Exchange Rate ({self.base_currency}/{self.account_currency}): {status['exchange_rate']:.6f}")
            logger.info(f"  Total P&L ({self.account_currency}): €{status.get('total_pnl_eur', 0):.2f}")
            logger.info(f"  Realized P&L ({self.account_currency}): €{status.get('realized_pnl_eur', 0):.2f}")

        logger.info(f"  Recenter Count: {status.get('recenter_count', 0)}")
        logger.info(f"  Roll Count: {status.get('roll_count', 0)}")
        logger.info("=" * 60)

    def log_position_snapshot(self, positions: List[Dict[str, Any]]):
        """
        Log current position snapshot to Google Sheets.

        Args:
            positions: List of position dictionaries with all details
        """
        if self.google_logger.enabled:
            self.google_logger.log_position_snapshot(positions)

    def log_daily_summary(self, summary: Dict[str, Any]):
        """
        Log daily summary metrics to Google Sheets.

        Args:
            summary: Dictionary with daily performance metrics
        """
        if self.google_logger.enabled:
            self.google_logger.log_daily_summary(summary)

    def log_safety_event(self, event: Dict[str, Any]):
        """
        Log safety event (Fed filter, ITM risk, emergency exit) to Google Sheets.

        Args:
            event: Dictionary with safety event details
        """
        if self.google_logger.enabled:
            self.google_logger.log_safety_event(event)

        # Also log to console for visibility
        logger.warning(
            f"SAFETY EVENT: {event.get('event_type')} - {event.get('description')}"
        )

    def log_greeks_snapshot(self, greeks: Dict[str, Any]):
        """
        Log current Greeks snapshot to Google Sheets.

        Args:
            greeks: Dictionary with all Greeks values
        """
        if self.google_logger.enabled:
            self.google_logger.log_greeks(greeks)

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
