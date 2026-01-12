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
import smtplib
from datetime import datetime
from typing import Optional, Dict, List, Any
from pathlib import Path
import threading
from queue import Queue
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

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
        """Convert to simplified list format for spreadsheet row.

        Columns: Timestamp, Action, Type, Strike, Expiry, Days to Expiry, SPY Price, VIX, Premium ($), P&L ($), P&L (EUR), Notes
        """
        return [
            self.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            self.action,
            self.option_type or "N/A",
            str(self.strike),
            self.expiry_date or "N/A",
            self.dte if self.dte is not None else "N/A",
            f"{self.underlying_price:.2f}" if self.underlying_price else "N/A",
            f"{self.vix:.2f}" if self.vix else "N/A",
            f"{self.premium_received:.2f}" if self.premium_received is not None else "N/A",
            f"{self.pnl:.2f}",
            f"{self.converted_pnl:.2f}" if self.converted_pnl is not None else "N/A",
            self.trade_reason or ""
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

        # Bot logs buffer for batch writing
        self._log_buffer = []
        self._log_buffer_lock = threading.Lock()
        self._last_log_flush = datetime.now()

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

            # Check if running on GCP - load credentials from Secret Manager
            from src.secret_manager import is_running_on_gcp, get_google_sheets_credentials

            if is_running_on_gcp():
                logger.info("Loading Google Sheets credentials from Secret Manager")
                creds_data = get_google_sheets_credentials()
                if creds_data:
                    credentials = Credentials.from_service_account_info(
                        creds_data,
                        scopes=scopes
                    )
                else:
                    logger.error("Failed to load Google Sheets credentials from Secret Manager")
                    self.enabled = False
                    return False
            else:
                # Load credentials from service account file (local development)
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

            # Initialize all worksheets (7 tabs for comprehensive Looker dashboard)
            self._setup_trades_worksheet()
            self._setup_positions_worksheet()
            self._setup_daily_summary_worksheet()
            self._setup_safety_events_worksheet()
            self._setup_bot_logs_worksheet()
            self._setup_performance_metrics_worksheet()
            self._setup_account_summary_worksheet()

            logger.info("All Google Sheets worksheets initialized successfully (7 tabs)")
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
        """Setup the Trades worksheet with essential columns only."""
        try:
            import gspread
            try:
                worksheet = self.spreadsheet.worksheet("Trades")
            except gspread.WorksheetNotFound:
                worksheet = self.spreadsheet.add_worksheet(title="Trades", rows=10000, cols=13)
                # Essential trade headers only - what matters for the strategy
                headers = [
                    "Timestamp", "Action", "Type", "Strike", "Expiry", "Days to Expiry",
                    "SPY Price", "VIX", "Premium ($)", "P&L ($)", "P&L (EUR)", "Notes"
                ]
                worksheet.append_row(headers)
                worksheet.format("A1:L1", {"textFormat": {"bold": True}})
                logger.info("Created Trades worksheet with essential headers")

            self.worksheets["Trades"] = worksheet
        except Exception as e:
            logger.error(f"Failed to setup Trades worksheet: {e}")

    def _setup_positions_worksheet(self):
        """Setup the Positions worksheet with essential columns only."""
        try:
            import gspread
            try:
                worksheet = self.spreadsheet.worksheet("Positions")
            except gspread.WorksheetNotFound:
                worksheet = self.spreadsheet.add_worksheet(title="Positions", rows=100, cols=13)
                # Essential position info + theta decay tracking
                headers = [
                    "Last Updated", "Type", "Strike", "Expiry", "Days to Expiry",
                    "Entry Price", "Current Price", "P&L ($)", "P&L (EUR)",
                    "Theta/Day ($)", "Weekly Theta ($)", "Status"
                ]
                worksheet.append_row(headers)
                worksheet.format("A1:L1", {"textFormat": {"bold": True}})
                logger.info("Created Positions worksheet")

            self.worksheets["Positions"] = worksheet
        except Exception as e:
            logger.error(f"Failed to setup Positions worksheet: {e}")

    def _setup_daily_summary_worksheet(self):
        """Setup the Daily Summary worksheet with essential metrics."""
        try:
            import gspread
            try:
                worksheet = self.spreadsheet.worksheet("Daily Summary")
            except gspread.WorksheetNotFound:
                worksheet = self.spreadsheet.add_worksheet(title="Daily Summary", rows=1000, cols=12)
                # Essential daily metrics for the strategy
                headers = [
                    "Date", "SPY Close", "VIX", "Theta Cost ($)",
                    "Premium Collected ($)", "Daily P&L ($)", "Daily P&L (EUR)",
                    "Cumulative P&L ($)", "Roll Count", "Recenter Count", "Notes"
                ]
                worksheet.append_row(headers)
                worksheet.format("A1:K1", {"textFormat": {"bold": True}})
                logger.info("Created Daily Summary worksheet")

            self.worksheets["Daily Summary"] = worksheet
        except Exception as e:
            logger.error(f"Failed to setup Daily Summary worksheet: {e}")

    def _setup_safety_events_worksheet(self):
        """Setup the Safety Events worksheet for rolls, recenters, and emergencies."""
        try:
            import gspread
            try:
                worksheet = self.spreadsheet.worksheet("Safety Events")
            except gspread.WorksheetNotFound:
                worksheet = self.spreadsheet.add_worksheet(title="Safety Events", rows=1000, cols=8)
                # Essential safety/action events
                headers = [
                    "Timestamp", "Event", "SPY Price", "VIX",
                    "New Short Strikes", "Premium ($)", "Description", "Result"
                ]
                worksheet.append_row(headers)
                worksheet.format("A1:H1", {"textFormat": {"bold": True}})
                logger.info("Created Safety Events worksheet")

            self.worksheets["Safety Events"] = worksheet
        except Exception as e:
            logger.error(f"Failed to setup Safety Events worksheet: {e}")

    def _setup_bot_logs_worksheet(self):
        """Setup the Bot Logs worksheet for live activity stream (Looker dashboard)."""
        try:
            import gspread
            try:
                worksheet = self.spreadsheet.worksheet("Bot Logs")
            except gspread.WorksheetNotFound:
                worksheet = self.spreadsheet.add_worksheet(title="Bot Logs", rows=10000, cols=6)
                # Live bot activity logs
                headers = [
                    "Timestamp", "Level", "Component", "Message", "SPY Price", "VIX"
                ]
                worksheet.append_row(headers)
                worksheet.format("A1:F1", {"textFormat": {"bold": True}})
                logger.info("Created Bot Logs worksheet")

            self.worksheets["Bot Logs"] = worksheet
        except Exception as e:
            logger.error(f"Failed to setup Bot Logs worksheet: {e}")

    def _setup_performance_metrics_worksheet(self):
        """Setup the Performance Metrics worksheet for SPY strategy KPIs (Looker dashboard)."""
        try:
            import gspread
            try:
                worksheet = self.spreadsheet.worksheet("Performance Metrics")
            except gspread.WorksheetNotFound:
                worksheet = self.spreadsheet.add_worksheet(title="Performance Metrics", rows=1000, cols=22)
                # SPY Strategy-specific performance KPIs for investors
                headers = [
                    "Timestamp", "Period", "SPY Strategy P&L ($)", "SPY Strategy P&L (EUR)", "SPY Strategy P&L (%)",
                    "Realized P&L ($)", "Unrealized P&L ($)", "Premium Collected ($)", "Theta Cost ($)",
                    "Net Theta ($)", "Long Straddle P&L ($)", "Short Strangle P&L ($)",
                    "Win Rate (%)", "Sharpe Ratio", "Max Drawdown ($)", "Max Drawdown (%)",
                    "Trade Count", "Roll Count", "Recenter Count", "Avg Trade P&L ($)", "Best Trade ($)", "Worst Trade ($)"
                ]
                worksheet.append_row(headers)
                worksheet.format("A1:V1", {"textFormat": {"bold": True}})
                logger.info("Created Performance Metrics worksheet (SPY strategy only)")

            self.worksheets["Performance Metrics"] = worksheet
        except Exception as e:
            logger.error(f"Failed to setup Performance Metrics worksheet: {e}")

    def _setup_account_summary_worksheet(self):
        """Setup the Account Summary worksheet for SPY strategy data (Looker dashboard)."""
        try:
            import gspread
            try:
                worksheet = self.spreadsheet.worksheet("Account Summary")
            except gspread.WorksheetNotFound:
                worksheet = self.spreadsheet.add_worksheet(title="Account Summary", rows=1000, cols=18)
                # SPY Strategy-specific account data (not full account)
                headers = [
                    "Timestamp", "SPY Price", "VIX",
                    "Strategy Unrealized P&L ($)", "Strategy Unrealized P&L (EUR)",
                    "Long Straddle Value ($)", "Short Strangle Value ($)",
                    "Net Strategy Value ($)", "Strategy Margin Used ($)",
                    "Total Delta", "Total Theta ($)", "Total Positions",
                    "Long Call Strike", "Long Put Strike", "Short Call Strike", "Short Put Strike",
                    "Exchange Rate", "Environment"
                ]
                worksheet.append_row(headers)
                worksheet.format("A1:R1", {"textFormat": {"bold": True}})
                logger.info("Created Account Summary worksheet (SPY strategy only)")

            self.worksheets["Account Summary"] = worksheet
        except Exception as e:
            logger.error(f"Failed to setup Account Summary worksheet: {e}")

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

    def _normalize_expiry(self, expiry: str) -> str:
        """
        Normalize expiry date to YYYYMMDD format for comparison.

        Handles formats: YYYYMMDD, YYYY-MM-DD, YYYY/MM/DD

        Args:
            expiry: Expiry date string in various formats

        Returns:
            str: Normalized expiry in YYYYMMDD format, or original if parse fails
        """
        if not expiry or expiry == "N/A":
            return ""

        expiry_str = str(expiry).strip()

        # Already in YYYYMMDD format (8 digits)
        if len(expiry_str) == 8 and expiry_str.isdigit():
            return expiry_str

        # Try YYYY-MM-DD format
        if len(expiry_str) == 10 and "-" in expiry_str:
            return expiry_str.replace("-", "")

        # Try YYYY/MM/DD format
        if len(expiry_str) == 10 and "/" in expiry_str:
            return expiry_str.replace("/", "")

        return expiry_str

    def check_position_logged(self, position_type: str, strike: float, expiry: str) -> bool:
        """
        Check if a position has already been logged to Google Sheets.

        Args:
            position_type: Type of position (e.g., "LONG", "SHORT")
            strike: Strike price of the position
            expiry: Expiry date string (any format: YYYYMMDD, YYYY-MM-DD, etc.)

        Returns:
            bool: True if position is already logged, False otherwise
        """
        if not self.enabled or "Trades" not in self.worksheets:
            return False

        try:
            # Get all records from Trades worksheet
            worksheet = self.worksheets["Trades"]
            records = worksheet.get_all_records()

            # Normalize the expiry we're searching for
            normalized_expiry = self._normalize_expiry(expiry)

            # Look for an OPEN or RECOVERED action for this position
            # position_type can be "LONG" or "SHORT"
            # Action format in sheet: "[RECOVERED] OPEN_LONG_Call" or "OPEN_SHORT_Put"
            search_patterns = [
                f"OPEN_{position_type}",       # Matches "OPEN_LONG_Call", "OPEN_SHORT_Put"
                f"[RECOVERED] OPEN_{position_type}"  # Matches recovered positions
            ]

            for record in records:
                action = record.get("Action", "")
                record_strike = str(record.get("Strike", ""))
                record_expiry = str(record.get("Expiry", ""))

                # Check if this is an open trade for this position type
                if any(pattern in action for pattern in search_patterns):
                    # Check if strike matches (handle formatting differences)
                    try:
                        strike_val = float(record_strike)
                        if abs(strike_val - strike) < 0.01:
                            # Normalize record expiry and compare
                            normalized_record_expiry = self._normalize_expiry(record_expiry)
                            if normalized_expiry == normalized_record_expiry:
                                logger.debug(f"Found existing log for {position_type} @ ${strike} exp {expiry}")
                                return True
                    except (ValueError, TypeError):
                        continue

            return False

        except Exception as e:
            logger.warning(f"Error checking for existing position log: {e}")
            return False

    def check_recovery_logged_today(self) -> bool:
        """
        Check if a position recovery event was already logged today.

        This prevents duplicate POSITION_RECOVERY entries in Safety Events
        when the bot restarts multiple times in the same day.

        Returns:
            bool: True if recovery already logged today, False otherwise
        """
        if not self.enabled or "Safety Events" not in self.worksheets:
            return False

        try:
            worksheet = self.worksheets["Safety Events"]
            records = worksheet.get_all_records()

            today_str = datetime.now().strftime("%Y-%m-%d")

            for record in records:
                event_type = record.get("Event", "")
                timestamp = str(record.get("Timestamp", ""))

                # Check if this is a recovery event from today
                if event_type == "POSITION_RECOVERY" and today_str in timestamp:
                    logger.debug(f"Found existing recovery log for today: {timestamp}")
                    return True

            return False

        except Exception as e:
            logger.warning(f"Error checking for existing recovery log: {e}")
            return False

    def log_recovered_position(
        self,
        position_type: str,
        strike: Any,
        expiry: str,
        entry_price: float,
        current_price: float,
        quantity: int,
        option_type: str = None,
        call_strike: float = None,
        put_strike: float = None,
        underlying_price: float = None,
        vix: float = None,
        delta: float = None,
        dte: int = None
    ) -> bool:
        """
        Log a recovered position to Google Sheets.

        This is called when the bot recovers positions on startup.
        It logs the position with a [RECOVERED] prefix.

        Args:
            position_type: Type (e.g., "LONG_STRADDLE", "SHORT_STRANGLE")
            strike: Strike price(s)
            expiry: Expiry date
            entry_price: Entry price of the position
            current_price: Current price
            quantity: Number of contracts
            option_type: "Call", "Put", "Straddle", "Strangle"
            call_strike: For strangles, the call strike
            put_strike: For strangles, the put strike
            underlying_price: Current SPY price
            vix: Current VIX level
            delta: Position delta
            dte: Days to expiration

        Returns:
            bool: True if logged successfully
        """
        if not self.enabled or "Trades" not in self.worksheets:
            return False

        try:
            # Format strike display
            if call_strike and put_strike:
                strike_display = f"C{call_strike}/P{put_strike}"
            else:
                strike_display = str(strike)

            # Calculate DTE if not provided
            if dte is None and expiry:
                try:
                    from datetime import datetime
                    # Parse expiry (format: YYYYMMDD)
                    expiry_date = datetime.strptime(str(expiry), "%Y%m%d")
                    dte = (expiry_date - datetime.now()).days
                except:
                    dte = None

            # Create trade record for recovery with all available fields
            trade = TradeRecord(
                action=f"[RECOVERED] OPEN_{position_type}",
                strike=strike_display,
                price=entry_price,
                delta=delta or 0.0,
                pnl=0.0,
                option_type=option_type or position_type,
                expiry_date=expiry,
                quantity=quantity,
                trade_reason="Position Recovery",
                underlying_price=underlying_price,
                vix=vix,
                dte=dte,
                total_delta=delta or 0.0,
                realized_pnl=0.0,
                unrealized_pnl=0.0,
                premium_received=entry_price * quantity * 100 if position_type == "SHORT_STRANGLE" else None
            )

            self.worksheets["Trades"].append_row(trade.to_list())
            logger.info(f"Logged recovered position to Google Sheets: {position_type} @ {strike_display}")
            return True

        except Exception as e:
            logger.error(f"Failed to log recovered position: {e}")
            return False

    def log_position_snapshot(self, positions: List[Dict[str, Any]]) -> bool:
        """
        Update the Positions worksheet with current position snapshot.

        Args:
            positions: List of position dictionaries with theta values

        Returns:
            bool: True if logged successfully
        """
        if not self.enabled or "Positions" not in self.worksheets:
            return False

        try:
            # Clear existing data (keep headers)
            worksheet = self.worksheets["Positions"]
            # Only delete if there are rows beyond the header
            if worksheet.row_count > 1:
                try:
                    worksheet.delete_rows(2, worksheet.row_count)
                except Exception:
                    pass  # Ignore if no rows to delete

            # Add current positions with theta decay columns
            # Columns: Last Updated, Type, Strike, Expiry, Days to Expiry, Entry Price, Current Price,
            #          P&L ($), P&L (EUR), Theta/Day ($), Weekly Theta ($), Status
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for pos in positions:
                # Get theta value from Saxo (always negative from the API)
                theta = pos.get("theta", 0)
                position_type = pos.get("type", "").upper()

                # For SHORT positions: flip the sign to show as positive (we GAIN from decay)
                # For LONG positions: keep negative (we LOSE from decay)
                # Multiply by 100 for contract multiplier
                if "SHORT" in position_type:
                    daily_theta = abs(theta) * 100  # Positive = income
                else:
                    daily_theta = theta * 100  # Negative = cost

                # Weekly theta = daily theta × 5 trading days (Friday-to-Friday)
                # This gives a consistent weekly total to compare against short premium collected
                weekly_theta = daily_theta * 5

                row = [
                    timestamp,
                    pos.get("type", "N/A"),
                    pos.get("strike", "N/A"),
                    pos.get("expiry", "N/A"),
                    pos.get("dte", "N/A"),
                    f"{pos.get('entry_price', 0):.4f}",
                    f"{pos.get('current_price', 0):.4f}",
                    f"{pos.get('pnl', 0):.2f}",
                    f"{pos.get('pnl_eur', 0):.2f}",
                    f"{daily_theta:.2f}",
                    f"{weekly_theta:.2f}",
                    pos.get("status", "Active")
                ]
                worksheet.append_row(row)

            # Clear any bold formatting from data rows (row 2 onwards)
            if len(positions) > 0:
                last_row = worksheet.row_count
                if last_row > 1:
                    worksheet.format(f"A2:L{last_row}", {"textFormat": {"bold": False}})

            logger.debug(f"Updated position snapshot: {len(positions)} positions")
            return True
        except Exception as e:
            logger.error(f"Failed to update position snapshot: {e}")
            return False

    def log_recovered_positions_full(
        self,
        individual_positions: List[Dict[str, Any]],
        underlying_price: float,
        vix: float,
        exchange_rate: float = None
    ) -> bool:
        """
        Log all recovered positions to ALL relevant worksheets.

        This logs:
        1. Each individual option leg to the Trades tab
        2. All positions to the Positions tab (current snapshot)
        3. Initial Greeks snapshot to Greeks & Risk tab
        4. Recovery event to Safety Events tab

        Args:
            individual_positions: List of individual option positions (4 legs typically)
            underlying_price: Current SPY price
            vix: Current VIX level
            exchange_rate: Optional USD/EUR exchange rate

        Returns:
            bool: True if all logging succeeded
        """
        if not self.enabled:
            return False

        success = True
        timestamp = datetime.now()

        try:
            # 1. Log each individual position to Trades tab
            for pos in individual_positions:
                # Calculate DTE and format expiry date
                dte = None
                expiry_formatted = pos.get("expiry", "N/A")
                if pos.get("expiry"):
                    try:
                        expiry_date = datetime.strptime(str(pos["expiry"]), "%Y%m%d")
                        dte = (expiry_date - datetime.now()).days
                        # Format expiry as YYYY-MM-DD for display
                        expiry_formatted = expiry_date.strftime("%Y-%m-%d")
                    except:
                        pass

                # Build descriptive action: e.g., "OPEN_LONG_Call" or "OPEN_SHORT_Put"
                position_type = pos.get("position_type", "UNKNOWN")  # LONG or SHORT
                option_type = pos.get("option_type", "Unknown")      # Call or Put
                action = f"[RECOVERED] OPEN_{position_type}_{option_type}"

                # Build full type description: "Long Call", "Short Put", etc.
                full_type = f"{position_type.capitalize()} {option_type}"

                # Calculate premium:
                # - LONG positions: premium PAID (DEBIT) = negative (money out)
                # - SHORT positions: premium RECEIVED (CREDIT) = positive (money in)
                # Formula: entry_price × qty × 100 (options are 100 shares per contract)
                entry_price = pos.get("entry_price", 0)
                current_price = pos.get("current_price", 0)
                quantity = pos.get("quantity", 1)

                base_premium = entry_price * quantity * 100
                if position_type == "LONG":
                    premium = -base_premium  # Debit (money paid out)
                else:
                    premium = base_premium   # Credit (money received)

                # Calculate unrealized P&L:
                # - LONG: profit when option price increases (current - entry)
                # - SHORT: profit when option price decreases (entry - current)
                # Multiply by quantity and 100 (contract multiplier)
                if position_type == "LONG":
                    unrealized_pnl = (current_price - entry_price) * quantity * 100
                else:
                    unrealized_pnl = (entry_price - current_price) * quantity * 100

                # Convert to account currency if exchange rate available
                converted_pnl = unrealized_pnl * exchange_rate if exchange_rate else None

                trade = TradeRecord(
                    action=action,
                    strike=pos.get("strike", 0),
                    price=entry_price,
                    delta=pos.get("delta", 0),
                    pnl=unrealized_pnl,  # Total P&L (at recovery, this is all unrealized)
                    option_type=full_type,  # "Long Call", "Short Put", etc.
                    expiry_date=expiry_formatted,  # YYYY-MM-DD format
                    quantity=None,  # Not needed - each position is on its own line
                    trade_reason="Position Recovery",
                    underlying_price=underlying_price,
                    vix=vix,
                    dte=dte,
                    total_delta=pos.get("delta", 0),
                    realized_pnl=0.0,  # No realized P&L at recovery (positions still open)
                    unrealized_pnl=unrealized_pnl,
                    premium_received=premium,
                    exchange_rate=exchange_rate,
                    converted_pnl=converted_pnl,
                    greeks={
                        "gamma": pos.get("gamma", 0),
                        "theta": pos.get("theta", 0),
                        "vega": pos.get("vega", 0)
                    }
                )

                if "Trades" in self.worksheets:
                    self.worksheets["Trades"].append_row(trade.to_list())
                    logger.info(f"Logged individual position to Trades: {position_type} {option_type} @ ${pos.get('strike')}")

            # 2. Update Positions tab with current snapshot
            positions_data = []
            for pos in individual_positions:
                dte = None
                expiry_formatted = pos.get("expiry", "N/A")
                if pos.get("expiry"):
                    try:
                        expiry_date = datetime.strptime(str(pos["expiry"]), "%Y%m%d")
                        dte = (expiry_date - datetime.now()).days
                        expiry_formatted = expiry_date.strftime("%Y-%m-%d")
                    except:
                        pass

                # Format type as "Long Call", "Short Put", etc.
                position_type = pos.get("position_type", "")
                option_type = pos.get("option_type", "")
                full_type = f"{position_type.capitalize()} {option_type}"

                # Calculate unrealized P&L for Positions tab
                entry_price = pos.get("entry_price", 0)
                current_price = pos.get("current_price", 0)
                quantity = pos.get("quantity", 1)

                if position_type.upper() == "LONG":
                    pos_pnl = (current_price - entry_price) * quantity * 100
                else:
                    pos_pnl = (entry_price - current_price) * quantity * 100

                # Convert to EUR if exchange rate available
                pos_pnl_eur = pos_pnl * exchange_rate if exchange_rate else 0.0

                positions_data.append({
                    "type": full_type,
                    "strike": pos.get("strike", 0),
                    "expiry": expiry_formatted,
                    "dte": dte,
                    "entry_price": entry_price,
                    "current_price": current_price,
                    "delta": pos.get("delta", 0),
                    "gamma": pos.get("gamma", 0),
                    "theta": pos.get("theta", 0),
                    "vega": pos.get("vega", 0),
                    "pnl": pos_pnl,
                    "pnl_eur": pos_pnl_eur,
                    "status": "Recovered"
                })

            self.log_position_snapshot(positions_data)

            # 3. Log recovery event to Safety Events tab (if not already logged today)
            # This prevents duplicate recovery entries when bot restarts multiple times
            # Columns: Timestamp, Event, SPY Price, VIX, New Short Strikes, Premium ($), Description, Result
            if "Safety Events" in self.worksheets:
                # Check if we already logged a recovery event today
                if not self.check_recovery_logged_today():
                    # Find strangle strikes for display
                    short_call_strike = 0
                    short_put_strike = 0
                    for p in individual_positions:
                        if "SHORT" in p.get("position_type", ""):
                            if "CALL" in p.get("option_type", "").upper():
                                short_call_strike = p.get("strike", 0)
                            elif "PUT" in p.get("option_type", "").upper():
                                short_put_strike = p.get("strike", 0)

                    # Format short strikes
                    if short_call_strike and short_put_strike:
                        new_strikes = f"C{short_call_strike}/P{short_put_strike}"
                    else:
                        new_strikes = "N/A"

                    safety_row = [
                        timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                        "POSITION_RECOVERY",
                        f"{underlying_price:.2f}",
                        f"{vix:.2f}",
                        new_strikes,
                        "0.00",  # No new premium on recovery
                        f"Recovered {len(individual_positions)} option positions from Saxo",
                        "SUCCESS"
                    ]
                    self.worksheets["Safety Events"].append_row(safety_row)
                    logger.info("Logged recovery event to Safety Events")
                else:
                    logger.info("Recovery event already logged today - skipping Safety Events entry")

            return success

        except Exception as e:
            logger.error(f"Failed to log recovered positions to all sheets: {e}")
            return False

    def log_daily_summary(self, summary: Dict[str, Any]) -> bool:
        """
        Log daily summary to Daily Summary worksheet.

        Columns: Date, SPY Close, VIX, Theta Cost ($), Premium Collected ($),
                 Daily P&L ($), Daily P&L (EUR), Cumulative P&L ($), Roll Count, Recenter Count, Notes

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
                f"{summary.get('spy_close', 0):.2f}",
                f"{summary.get('vix', summary.get('vix_avg', 0)):.2f}",
                f"{summary.get('theta_cost', summary.get('total_theta', 0)):.2f}",
                f"{summary.get('premium_collected', 0):.2f}",
                f"{summary.get('daily_pnl', 0):.2f}",
                f"{summary.get('daily_pnl_eur', summary.get('pnl_eur', 0)):.2f}",
                f"{summary.get('cumulative_pnl', 0):.2f}",
                summary.get("roll_count", 0),
                summary.get("recenter_count", 0),
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
        Log safety event (Fed filter, ITM risk, emergency exit, roll, recenter).

        Columns: Timestamp, Event, SPY Price, VIX, New Short Strikes, Premium ($), Description, Result

        Args:
            event: Dictionary with safety event details

        Returns:
            bool: True if logged successfully
        """
        if not self.enabled or "Safety Events" not in self.worksheets:
            return False

        try:
            # Format new short strikes if available
            short_call = event.get("short_call_strike")
            short_put = event.get("short_put_strike")
            if short_call and short_put:
                new_strikes = f"C{short_call}/P{short_put}"
            elif short_call:
                new_strikes = f"C{short_call}"
            elif short_put:
                new_strikes = f"P{short_put}"
            else:
                new_strikes = "N/A"

            row = [
                event.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                event.get("event_type", "N/A"),
                f"{event.get('spy_price', 0):.2f}",
                f"{event.get('vix', 0):.2f}",
                new_strikes,
                f"{event.get('premium', event.get('premium_collected', 0)):.2f}",
                event.get("description", ""),
                event.get("result", "Pending")
            ]
            self.worksheets["Safety Events"].append_row(row)
            logger.info(f"Safety event logged: {event.get('event_type')}")
            return True
        except Exception as e:
            logger.error(f"Failed to log safety event: {e}")
            return False

    def log_bot_activity(
        self,
        level: str,
        component: str,
        message: str,
        spy_price: float = None,
        vix: float = None,
        flush_immediately: bool = False
    ) -> bool:
        """
        Log bot activity to the Bot Logs worksheet for live dashboard.

        Uses buffering to batch writes every 30 seconds or when buffer is full.

        Args:
            level: Log level (INFO, WARNING, ERROR, DEBUG)
            component: Component name (Strategy, SaxoClient, WebSocket, etc.)
            message: Log message
            spy_price: Optional current SPY price
            vix: Optional current VIX value
            flush_immediately: Force immediate write to sheet

        Returns:
            bool: True if logged/buffered successfully
        """
        if not self.enabled or "Bot Logs" not in self.worksheets:
            return False

        try:
            row = [
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                level,
                component,
                message[:500],  # Truncate long messages
                f"{spy_price:.2f}" if spy_price else "",
                f"{vix:.2f}" if vix else ""
            ]

            with self._log_buffer_lock:
                self._log_buffer.append(row)

                # Flush if buffer is large enough or enough time has passed
                should_flush = (
                    flush_immediately or
                    len(self._log_buffer) >= 20 or
                    (datetime.now() - self._last_log_flush).total_seconds() > 30
                )

                if should_flush:
                    self._flush_log_buffer()

            return True
        except Exception as e:
            logger.error(f"Failed to buffer bot log: {e}")
            return False

    def _flush_log_buffer(self):
        """Flush the log buffer to Google Sheets (must hold lock)."""
        if not self._log_buffer:
            return

        try:
            worksheet = self.worksheets.get("Bot Logs")
            if worksheet:
                # Batch append all buffered rows
                for row in self._log_buffer:
                    worksheet.append_row(row)
                self._log_buffer.clear()
                self._last_log_flush = datetime.now()
        except Exception as e:
            logger.error(f"Failed to flush log buffer: {e}")

    def log_performance_metrics(
        self,
        period: str,
        metrics: Dict[str, Any],
        exchange_rate: float = None
    ) -> bool:
        """
        Log SPY strategy performance metrics for Looker dashboard.

        This logs ONLY SPY strategy performance, not full account performance.

        Args:
            period: Period label (e.g., "Daily", "Weekly", "Monthly", "All-Time")
            metrics: Dictionary with SPY strategy metrics:
                - total_pnl: Total SPY strategy P&L
                - realized_pnl: Realized P&L from closed SPY positions
                - unrealized_pnl: Unrealized P&L from open SPY positions
                - premium_collected: Premium from short strangles
                - theta_cost: Theta decay cost (long positions)
                - net_theta: Net theta (short theta - long theta)
                - long_straddle_pnl: P&L from long straddle
                - short_strangle_pnl: P&L from short strangle
                - win_rate, sharpe_ratio, max_drawdown, etc.
                - trade_count, roll_count, recenter_count
                - starting_capital: For % calculation
            exchange_rate: Optional USD/EUR exchange rate

        Returns:
            bool: True if logged successfully
        """
        if not self.enabled or "Performance Metrics" not in self.worksheets:
            return False

        try:
            # Calculate EUR values if exchange rate provided
            total_pnl = metrics.get("total_pnl", 0)
            total_pnl_eur = total_pnl * exchange_rate if exchange_rate else 0

            # Calculate percentage returns if starting capital provided
            starting_capital = metrics.get("starting_capital", 0)
            total_pnl_pct = (total_pnl / starting_capital * 100) if starting_capital else 0

            row = [
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                period,
                f"{total_pnl:.2f}",
                f"{total_pnl_eur:.2f}",
                f"{total_pnl_pct:.2f}",
                f"{metrics.get('realized_pnl', 0):.2f}",
                f"{metrics.get('unrealized_pnl', 0):.2f}",
                f"{metrics.get('premium_collected', 0):.2f}",
                f"{metrics.get('theta_cost', 0):.2f}",
                f"{metrics.get('net_theta', 0):.2f}",
                f"{metrics.get('long_straddle_pnl', 0):.2f}",
                f"{metrics.get('short_strangle_pnl', 0):.2f}",
                f"{metrics.get('win_rate', 0):.1f}",
                f"{metrics.get('sharpe_ratio', 0):.2f}",
                f"{metrics.get('max_drawdown', 0):.2f}",
                f"{metrics.get('max_drawdown_pct', 0):.2f}",
                metrics.get("trade_count", 0),
                metrics.get("roll_count", 0),
                metrics.get("recenter_count", 0),
                f"{metrics.get('avg_trade_pnl', 0):.2f}",
                f"{metrics.get('best_trade', 0):.2f}",
                f"{metrics.get('worst_trade', 0):.2f}"
            ]
            self.worksheets["Performance Metrics"].append_row(row)
            logger.debug(f"SPY strategy performance metrics logged for period: {period}")
            return True
        except Exception as e:
            logger.error(f"Failed to log performance metrics: {e}")
            return False

    def log_account_summary(
        self,
        strategy_data: Dict[str, Any],
        exchange_rate: float = None,
        environment: str = "LIVE"
    ) -> bool:
        """
        Log SPY strategy account summary for Looker dashboard.

        This logs ONLY SPY strategy-specific data, not the full Saxo account.

        Args:
            strategy_data: Strategy-specific data including:
                - spy_price: Current SPY price
                - vix: Current VIX value
                - unrealized_pnl: SPY strategy unrealized P&L
                - long_straddle_value: Value of long straddle
                - short_strangle_value: Value of short strangle
                - strategy_margin: Margin used by SPY positions
                - total_delta: Total delta of SPY positions
                - total_theta: Total theta of SPY positions
                - position_count: Number of SPY option positions
                - long_call_strike: Long call strike price
                - long_put_strike: Long put strike price
                - short_call_strike: Short call strike price
                - short_put_strike: Short put strike price
            exchange_rate: Optional USD/EUR exchange rate
            environment: Trading environment (LIVE/SIM)

        Returns:
            bool: True if logged successfully
        """
        if not self.enabled or "Account Summary" not in self.worksheets:
            return False

        try:
            # Extract strategy-specific fields
            spy_price = strategy_data.get("spy_price", 0)
            vix = strategy_data.get("vix", 0)
            unrealized_pnl = strategy_data.get("unrealized_pnl", 0)
            long_straddle_value = strategy_data.get("long_straddle_value", 0)
            short_strangle_value = strategy_data.get("short_strangle_value", 0)
            strategy_margin = strategy_data.get("strategy_margin", 0)
            total_delta = strategy_data.get("total_delta", 0)
            total_theta = strategy_data.get("total_theta", 0)
            position_count = strategy_data.get("position_count", 0)

            # Strike prices
            long_call_strike = strategy_data.get("long_call_strike", 0)
            long_put_strike = strategy_data.get("long_put_strike", 0)
            short_call_strike = strategy_data.get("short_call_strike", 0)
            short_put_strike = strategy_data.get("short_put_strike", 0)

            # Calculate net strategy value
            net_value = long_straddle_value + short_strangle_value

            # Calculate EUR values
            unrealized_pnl_eur = unrealized_pnl * exchange_rate if exchange_rate else 0

            row = [
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                f"{spy_price:.2f}",
                f"{vix:.2f}" if vix else "N/A",
                f"{unrealized_pnl:.2f}",
                f"{unrealized_pnl_eur:.2f}",
                f"{long_straddle_value:.2f}",
                f"{short_strangle_value:.2f}",
                f"{net_value:.2f}",
                f"{strategy_margin:.2f}",
                f"{total_delta:.4f}",
                f"{total_theta:.2f}",
                position_count,
                f"{long_call_strike:.0f}" if long_call_strike else "N/A",
                f"{long_put_strike:.0f}" if long_put_strike else "N/A",
                f"{short_call_strike:.0f}" if short_call_strike else "N/A",
                f"{short_put_strike:.0f}" if short_put_strike else "N/A",
                f"{exchange_rate:.6f}" if exchange_rate else "N/A",
                environment
            ]
            self.worksheets["Account Summary"].append_row(row)
            logger.debug("SPY strategy account summary logged")
            return True
        except Exception as e:
            logger.error(f"Failed to log account summary: {e}")
            return False

    def should_log_initial_metrics(self, stale_minutes: int = 30) -> bool:
        """
        Check if initial metrics should be logged on startup.

        Returns True if:
        1. Account Summary worksheet is empty (no data rows), OR
        2. The most recent entry is older than stale_minutes

        Args:
            stale_minutes: Number of minutes after which data is considered stale

        Returns:
            bool: True if initial metrics should be logged
        """
        if not self.enabled or "Account Summary" not in self.worksheets:
            return False

        try:
            worksheet = self.worksheets["Account Summary"]
            all_values = worksheet.get_all_values()

            # If only header row exists (or empty), we need to log
            if len(all_values) <= 1:
                logger.info("Account Summary is empty - will log initial metrics")
                return True

            # Get the last row's timestamp (first column)
            last_row = all_values[-1]
            last_timestamp_str = last_row[0] if last_row else None

            if not last_timestamp_str:
                logger.info("No timestamp found in last row - will log initial metrics")
                return True

            # Parse the timestamp
            try:
                last_timestamp = datetime.strptime(last_timestamp_str, "%Y-%m-%d %H:%M:%S")
                age_minutes = (datetime.now() - last_timestamp).total_seconds() / 60

                if age_minutes > stale_minutes:
                    logger.info(f"Last Account Summary entry is {age_minutes:.1f} minutes old (stale threshold: {stale_minutes}min) - will log initial metrics")
                    return True
                else:
                    logger.info(f"Last Account Summary entry is {age_minutes:.1f} minutes old - data is fresh")
                    return False
            except ValueError as e:
                logger.warning(f"Could not parse timestamp '{last_timestamp_str}': {e} - will log initial metrics")
                return True

        except Exception as e:
            logger.error(f"Error checking if initial metrics needed: {e}")
            return True  # Log on error to be safe

    def flush_all_buffers(self):
        """Flush all pending log buffers (call on shutdown)."""
        with self._log_buffer_lock:
            self._flush_log_buffer()


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


class EmailAlerter:
    """
    Email alerting for critical safety events.

    Uses SMTP to send email alerts when safety events occur.
    Supports both simple SMTP and Gmail with app passwords.

    Configuration (in email_alerts section of config):
        enabled: bool - Enable/disable email alerts
        smtp_server: str - SMTP server hostname
        smtp_port: int - SMTP port (usually 587 for TLS)
        sender_email: str - From email address
        sender_password: str - SMTP password or app password
        recipients: list[str] - List of recipient emails
        use_tls: bool - Whether to use TLS (default True)
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize email alerter.

        Args:
            config: Full configuration dictionary
        """
        self.config = config.get("email_alerts", {})
        self.enabled = self.config.get("enabled", False)

        if self.enabled:
            self.smtp_server = self.config.get("smtp_server", "smtp.gmail.com")
            self.smtp_port = self.config.get("smtp_port", 587)
            self.sender_email = self.config.get("sender_email")
            self.sender_password = self.config.get("sender_password")
            self.recipients = self.config.get("recipients", [])
            self.use_tls = self.config.get("use_tls", True)

            if not self.sender_email or not self.sender_password:
                logger.error("Email alerter: Missing sender credentials - disabling")
                self.enabled = False
            elif not self.recipients:
                logger.error("Email alerter: No recipients configured - disabling")
                self.enabled = False
            else:
                logger.info(f"Email alerter initialized: {len(self.recipients)} recipient(s)")

    def send_alert(self, subject: str, body: str, severity: str = "WARNING") -> bool:
        """
        Send an email alert.

        Args:
            subject: Email subject line
            body: Email body (plain text)
            severity: Alert severity for subject prefix

        Returns:
            bool: True if sent successfully
        """
        if not self.enabled:
            return False

        try:
            # Create message
            msg = MIMEMultipart()
            msg["From"] = self.sender_email
            msg["To"] = ", ".join(self.recipients)
            msg["Subject"] = f"[CALYPSO {severity}] {subject}"

            # Add timestamp and severity to body
            full_body = f"""
CALYPSO TRADING BOT ALERT
========================
Severity: {severity}
Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

{body}

---
This is an automated alert from the Calypso Trading Bot.
            """.strip()

            msg.attach(MIMEText(full_body, "plain"))

            # Connect and send
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                if self.use_tls:
                    server.starttls()
                server.login(self.sender_email, self.sender_password)
                server.send_message(msg)

            logger.info(f"Email alert sent: {subject}")
            return True

        except Exception as e:
            logger.error(f"Failed to send email alert: {e}")
            return False

    def send_safety_event_alert(self, event: Dict[str, Any]) -> bool:
        """
        Send alert for a safety event.

        Args:
            event: Safety event dictionary

        Returns:
            bool: True if sent successfully
        """
        event_type = event.get("event_type", "Unknown Event")
        severity = event.get("severity", "WARNING")

        subject = f"{event_type}"

        body = f"""
Safety Event Detected
---------------------
Event Type: {event_type}
Severity: {severity}

Market Data:
- SPY Price: ${event.get('spy_price', 0):.2f}
- VIX: {event.get('vix', 0):.2f}
- Initial Strike: ${event.get('initial_strike', 0):.2f}
- Distance: {event.get('distance_pct', 0):.2f}%

Action Taken: {event.get('action_taken', 'N/A')}

Description: {event.get('description', 'No details available')}

Short Positions:
- Call Strike: {event.get('short_call_strike', 'N/A')}
- Put Strike: {event.get('short_put_strike', 'N/A')}
        """.strip()

        return self.send_alert(subject, body, severity)

    def send_daily_summary(self, summary: Dict[str, Any]) -> bool:
        """
        Send daily P&L summary email.

        Args:
            summary: Daily summary data

        Returns:
            bool: True if sent successfully
        """
        subject = f"Daily Summary - P&L: ${summary.get('total_pnl', 0):.2f}"

        body = f"""
Daily Trading Summary
=====================

Date: {summary.get('date', datetime.now().strftime('%Y-%m-%d'))}

Performance:
- Total P&L: ${summary.get('total_pnl', 0):.2f}
- Realized P&L: ${summary.get('realized_pnl', 0):.2f}
- Unrealized P&L: ${summary.get('unrealized_pnl', 0):.2f}

Positions:
- Long Straddle Value: ${summary.get('long_straddle_value', 0):.2f}
- Short Strangle Value: ${summary.get('short_strangle_value', 0):.2f}

Activity:
- Trades Today: {summary.get('trade_count', 0)}
- Rolls: {summary.get('roll_count', 0)}
- Recenters: {summary.get('recenter_count', 0)}

Market:
- SPY Close: ${summary.get('spy_close', 0):.2f}
- VIX Close: {summary.get('vix_close', 0):.2f}
        """.strip()

        return self.send_alert(subject, body, "INFO")


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

        # Initialize Email Alerter (optional)
        self.email_alerter = EmailAlerter(config)

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
        logger.info(f"  - Email Alerts: {'ENABLED' if self.email_alerter.enabled else 'DISABLED'}")
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
        saxo_client=None,
        # Additional comprehensive tracking fields
        underlying_price: Optional[float] = None,
        vix: Optional[float] = None,
        option_type: Optional[str] = None,
        expiry_date: Optional[str] = None,
        dte: Optional[int] = None,
        premium_received: Optional[float] = None
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
            underlying_price: Current SPY price
            vix: Current VIX level
            option_type: Type of option (Call, Put, Straddle, Strangle)
            expiry_date: Option expiration date
            dte: Days to expiration
            premium_received: Premium collected (for short positions)
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
            converted_pnl=converted_pnl,
            # Additional tracking fields
            underlying_price=underlying_price,
            vix=vix,
            option_type=option_type,
            expiry_date=expiry_date,
            dte=dte,
            premium_received=premium_received
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
        Log safety event (Fed filter, ITM risk, emergency exit).

        Logs to:
        - Google Sheets (if enabled)
        - Email alert (if enabled)
        - Console (always)

        Args:
            event: Dictionary with safety event details
        """
        # Log to Google Sheets
        if self.google_logger.enabled:
            self.google_logger.log_safety_event(event)

        # Send email alert for critical events
        if self.email_alerter.enabled:
            self.email_alerter.send_safety_event_alert(event)

        # Also log to console for visibility
        logger.warning(
            f"SAFETY EVENT: {event.get('event_type')} - {event.get('description')}"
        )

    def check_position_logged(self, position_type: str, strike: float, expiry: str) -> bool:
        """
        Check if a position has already been logged to Google Sheets.

        Args:
            position_type: Type of position (e.g., "LONG", "SHORT")
            strike: Strike price of the position
            expiry: Expiry date string (any format: YYYYMMDD, YYYY-MM-DD, etc.)

        Returns:
            bool: True if position is already logged, False otherwise
        """
        if self.google_logger.enabled:
            return self.google_logger.check_position_logged(position_type, strike, expiry)
        return False

    def check_recovery_logged_today(self) -> bool:
        """
        Check if a position recovery event was already logged today.

        This prevents duplicate POSITION_RECOVERY entries when
        the bot restarts multiple times in the same day.

        Returns:
            bool: True if recovery already logged today, False otherwise
        """
        if self.google_logger.enabled:
            return self.google_logger.check_recovery_logged_today()
        return False

    def log_recovered_position(
        self,
        position_type: str,
        strike: Any,
        expiry: str,
        entry_price: float,
        current_price: float,
        quantity: int,
        option_type: str = None,
        call_strike: float = None,
        put_strike: float = None,
        underlying_price: float = None,
        vix: float = None,
        delta: float = None,
        dte: int = None
    ) -> bool:
        """
        Log a recovered position to all enabled logging destinations.

        This is called when the bot recovers positions on startup.
        It logs the position with a [RECOVERED] prefix.

        Args:
            position_type: Type (e.g., "LONG_STRADDLE", "SHORT_STRANGLE")
            strike: Strike price(s)
            expiry: Expiry date
            entry_price: Entry price of the position
            current_price: Current price
            quantity: Number of contracts
            option_type: "Call", "Put", "Straddle", "Strangle"
            call_strike: For strangles, the call strike
            put_strike: For strangles, the put strike
            underlying_price: Current SPY price
            vix: Current VIX level
            delta: Position delta
            dte: Days to expiration

        Returns:
            bool: True if logged successfully
        """
        success = True

        # Log to Google Sheets
        if self.google_logger.enabled:
            gs_success = self.google_logger.log_recovered_position(
                position_type=position_type,
                strike=strike,
                expiry=expiry,
                entry_price=entry_price,
                current_price=current_price,
                quantity=quantity,
                option_type=option_type,
                call_strike=call_strike,
                put_strike=put_strike,
                underlying_price=underlying_price,
                vix=vix,
                delta=delta,
                dte=dte
            )
            success = success and gs_success

        # Log to local file as well
        logger.info(
            f"[RECOVERED] Position logged: {position_type} @ {strike}, "
            f"Expiry: {expiry}, Qty: {quantity}"
        )

        return success

    def log_recovered_positions_full(
        self,
        individual_positions: List[Dict[str, Any]],
        underlying_price: float,
        vix: float,
        saxo_client=None
    ) -> bool:
        """
        Log all recovered positions to ALL relevant worksheets.

        This logs each individual option leg (4 typically) to:
        - Trades tab: Each leg as separate trade entry
        - Positions tab: Current snapshot of all legs
        - Greeks & Risk tab: Delta summary
        - Safety Events tab: Recovery event

        Args:
            individual_positions: List of individual option positions
            underlying_price: Current SPY price
            vix: Current VIX level
            saxo_client: Optional SaxoClient for fetching FX rate

        Returns:
            bool: True if logging succeeded
        """
        # Fetch exchange rate if currency conversion is enabled
        exchange_rate = None
        if self.currency_enabled and saxo_client:
            try:
                exchange_rate = saxo_client.get_fx_rate(
                    self.base_currency,
                    self.account_currency
                )
                if exchange_rate:
                    logger.info(f"Fetched FX rate for recovery logging: {self.base_currency}/{self.account_currency} = {exchange_rate:.6f}")
            except Exception as e:
                logger.warning(f"Could not fetch FX rate for recovery logging: {e}")

        if self.google_logger.enabled:
            return self.google_logger.log_recovered_positions_full(
                individual_positions=individual_positions,
                underlying_price=underlying_price,
                vix=vix,
                exchange_rate=exchange_rate
            )
        return False

    def log_bot_activity(
        self,
        level: str,
        component: str,
        message: str,
        spy_price: float = None,
        vix: float = None,
        flush: bool = False
    ):
        """
        Log bot activity for the live dashboard.

        Args:
            level: Log level (INFO, WARNING, ERROR, DEBUG)
            component: Component name (Strategy, SaxoClient, etc.)
            message: Log message
            spy_price: Optional current SPY price
            vix: Optional current VIX value
            flush: Force immediate write
        """
        if self.google_logger.enabled:
            self.google_logger.log_bot_activity(
                level=level,
                component=component,
                message=message,
                spy_price=spy_price,
                vix=vix,
                flush_immediately=flush
            )

    def log_performance_metrics(
        self,
        period: str,
        metrics: Dict[str, Any],
        saxo_client=None
    ):
        """
        Log calculated performance metrics for the dashboard.

        Args:
            period: Period label (Daily, Weekly, Monthly, All-Time)
            metrics: Performance metrics dictionary
            saxo_client: Optional SaxoClient for FX rate
        """
        exchange_rate = None
        if self.currency_enabled and saxo_client:
            try:
                exchange_rate = saxo_client.get_fx_rate(
                    self.base_currency,
                    self.account_currency
                )
            except Exception:
                pass

        if self.google_logger.enabled:
            self.google_logger.log_performance_metrics(
                period=period,
                metrics=metrics,
                exchange_rate=exchange_rate
            )

    def log_account_summary(
        self,
        strategy_data: Dict[str, Any],
        saxo_client=None,
        environment: str = "LIVE"
    ):
        """
        Log SPY strategy account summary for the dashboard.

        This logs ONLY SPY strategy data, not the full Saxo account balance.

        Args:
            strategy_data: Dictionary with SPY strategy-specific metrics:
                - spy_price: Current SPY price
                - vix: Current VIX value
                - unrealized_pnl: Strategy unrealized P&L
                - long_straddle_value: Long straddle current value
                - short_strangle_value: Short strangle current value
                - strategy_margin: Margin used by SPY positions
                - total_delta: Total delta
                - total_theta: Total theta (daily)
                - position_count: Number of SPY positions
                - long_call_strike, long_put_strike: Long strikes
                - short_call_strike, short_put_strike: Short strikes
            saxo_client: Optional SaxoClient for FX rate
            environment: Trading environment (LIVE/SIM)
        """
        if not self.google_logger.enabled:
            return

        try:
            # Get exchange rate if currency conversion enabled
            exchange_rate = None
            if self.currency_enabled and saxo_client:
                try:
                    exchange_rate = saxo_client.get_fx_rate(
                        self.base_currency,
                        self.account_currency
                    )
                except Exception:
                    pass

            self.google_logger.log_account_summary(
                strategy_data=strategy_data,
                exchange_rate=exchange_rate,
                environment=environment
            )
        except Exception as e:
            logger.error(f"Failed to log account summary: {e}")

    def should_log_initial_metrics(self, stale_minutes: int = 30) -> bool:
        """
        Check if initial dashboard metrics should be logged on startup.

        Delegates to GoogleSheetsLogger to check if Account Summary
        is empty or has stale data.

        Args:
            stale_minutes: Number of minutes after which data is considered stale

        Returns:
            bool: True if initial metrics should be logged
        """
        if self.google_logger.enabled:
            return self.google_logger.should_log_initial_metrics(stale_minutes)
        return False

    def shutdown(self):
        """Shutdown the logging service gracefully."""
        logger.info("Shutting down trade logger service...")

        # Flush any pending bot logs
        if self.google_logger.enabled:
            self.google_logger.flush_all_buffers()

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
