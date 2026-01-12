"""
saxo_client.py - Saxo Bank OpenAPI Client Module

This module handles all interactions with the Saxo Bank OpenAPI including:
- OAuth2 authentication flow
- REST API calls for trading operations
- WebSocket streaming for real-time price data
- Circuit breaker pattern for error handling

Author: Trading Bot Developer
Date: 2024
"""

import json
import time
import logging
import threading
import webbrowser
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any, Callable
from urllib.parse import urlencode
from http.server import HTTPServer, BaseHTTPRequestHandler
from dataclasses import dataclass
from enum import Enum

import requests
import websocket

# Import external price feed for simulation fallback
from src.external_price_feed import ExternalPriceFeed

# Configure module logger
logger = logging.getLogger(__name__)


class OrderType(Enum):
    """Enumeration of supported order types."""
    MARKET = "Market"
    LIMIT = "Limit"
    STOP = "Stop"
    STOP_LIMIT = "StopLimit"


class BuySell(Enum):
    """Enumeration for buy/sell direction."""
    BUY = "Buy"
    SELL = "Sell"


class AssetType(Enum):
    """Enumeration of asset types."""
    STOCK = "Stock"
    STOCK_OPTION = "StockOption"
    CFD_ON_STOCK = "CfdOnStock"
    ETF = "Etf"
    FUTURES = "Futures"


@dataclass
class CircuitBreakerState:
    """
    Tracks the state of the circuit breaker for API error handling.

    The circuit breaker prevents cascading failures by stopping trading
    when too many consecutive errors occur.
    """
    consecutive_errors: int = 0
    last_error_time: Optional[datetime] = None
    is_open: bool = False  # True = circuit is OPEN (blocking requests)
    last_successful_connection: datetime = None
    cooldown_until: Optional[datetime] = None

    def __post_init__(self):
        self.last_successful_connection = datetime.now()


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """
    HTTP request handler for OAuth2 callback.

    This handles the redirect from Saxo's authorization server
    and extracts the authorization code from the callback URL.
    """

    authorization_code = None

    def do_GET(self):
        """Handle GET request from OAuth callback."""
        # Extract authorization code from query parameters
        if "code=" in self.path:
            # Parse the authorization code from the URL
            query_string = self.path.split("?")[1] if "?" in self.path else ""
            params = dict(param.split("=") for param in query_string.split("&") if "=" in param)
            OAuthCallbackHandler.authorization_code = params.get("code")

            # Send success response to browser
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            response = b"<html><body><h1>Authorization Successful!</h1><p>You can close this window.</p></body></html>"
            self.wfile.write(response)
            logger.info("OAuth authorization code received successfully")
        else:
            # Handle error or missing code
            self.send_response(400)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            response = b"<html><body><h1>Authorization Failed</h1><p>No authorization code received.</p></body></html>"
            self.wfile.write(response)
            logger.error("OAuth callback received without authorization code")

    def log_message(self, format, *args):
        """Suppress default HTTP server logging."""
        pass


class SaxoClient:
    """
    Saxo Bank OpenAPI Client.

    This class provides a complete interface to the Saxo Bank OpenAPI,
    including authentication, trading operations, and real-time streaming.

    Attributes:
        config (dict): Configuration dictionary loaded from config.json
        access_token (str): Current OAuth2 access token
        refresh_token (str): OAuth2 refresh token for token renewal
        circuit_breaker (CircuitBreakerState): Circuit breaker state for error handling

    Example:
        >>> client = SaxoClient(config)
        >>> client.authenticate()
        >>> quote = client.get_quote("SPY")
        >>> print(f"SPY Price: {quote['LastTraded']}")
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the Saxo API client.

        Args:
            config: Configuration dictionary containing API credentials
                   and trading parameters.
        """
        self.config = config
        self.saxo_config = config["saxo_api"]
        self.circuit_config = config["circuit_breaker"]

        # Determine environment (simulation or live)
        self.environment = self.saxo_config.get("environment", "sim")

        # Get environment-specific credentials
        env_config = self.saxo_config.get(self.environment, {})

        # Authentication state
        self.app_key = env_config.get("app_key")
        self.app_secret = env_config.get("app_secret")
        self.access_token = env_config.get("access_token")
        self.refresh_token = env_config.get("refresh_token")

        # Load token expiry from config if available
        token_expiry_str = env_config.get("token_expiry")
        if token_expiry_str:
            try:
                self.token_expiry = datetime.fromisoformat(token_expiry_str)
            except (ValueError, TypeError):
                self.token_expiry = None
        else:
            self.token_expiry = None

        # Set URLs based on environment
        self.base_url = (
            self.saxo_config["base_url_sim"]
            if self.environment == "sim"
            else self.saxo_config["base_url_live"]
        )
        self.streaming_url = (
            self.saxo_config["streaming_url_sim"]
            if self.environment == "sim"
            else self.saxo_config["streaming_url_live"]
        )
        self.auth_url = (
            self.saxo_config["auth_url_sim"]
            if self.environment == "sim"
            else self.saxo_config["auth_url_live"]
        )
        self.token_url = (
            self.saxo_config["token_url_sim"]
            if self.environment == "sim"
            else self.saxo_config["token_url_live"]
        )

        # Circuit breaker for error handling
        self.circuit_breaker = CircuitBreakerState()

        # WebSocket connection state
        self.ws_connection: Optional[websocket.WebSocketApp] = None
        self.ws_thread: Optional[threading.Thread] = None
        self.price_callbacks: Dict[int, Callable] = {}  # UIC -> callback mapping
        self.subscription_context_id = f"ctx_{int(time.time())}"
        self.is_streaming = False

        # Price cache for subscription snapshots (UIC -> latest price data)
        self._price_cache: Dict[int, Dict] = {}

        # Account information - auto-select based on environment
        account_config = config.get("account", {})
        if self.environment in account_config and isinstance(account_config[self.environment], dict):
            # New structure: account.sim / account.live
            env_account = account_config[self.environment]
            self.account_key = env_account.get("account_key")
            self.client_key = env_account.get("client_key")
        else:
            # Legacy structure: account.account_key / account.client_key
            self.account_key = account_config.get("account_key")
            self.client_key = account_config.get("client_key")

        # Currency configuration (for FX rate lookups)
        self.currency_config = config.get("currency", {})

        # External price feed (fallback when Saxo returns NoAccess)
        # Note: Can be used in LIVE if you don't have market data API subscriptions
        external_feed_enabled = config.get("external_price_feed", {}).get("enabled", True)
        self.external_feed = ExternalPriceFeed(enabled=external_feed_enabled)

        logger.info(f"SaxoClient initialized in {self.environment} environment")

    @property
    def is_simulation(self) -> bool:
        """Check if running in simulation environment."""
        return self.environment == "sim"

    @property
    def is_live(self) -> bool:
        """Check if running in live environment."""
        return self.environment == "live"

    # =========================================================================
    # AUTHENTICATION METHODS
    # =========================================================================

    def authenticate(self) -> bool:
        """
        Perform OAuth2 authentication with Saxo Bank.

        This method checks for existing valid tokens first, then attempts
        to refresh if needed, or initiates a new OAuth flow if required.

        After successful authentication, it upgrades the session to
        FullTradingAndChat for real-time market data access.

        Returns:
            bool: True if authentication successful, False otherwise.
        """
        logger.info("Starting authentication process...")

        # Check if we have a valid access token
        if self.access_token and self._is_token_valid():
            logger.info("Using existing valid access token")
            # Upgrade session for real-time data
            self._upgrade_session_for_realtime_data()
            return True

        # Try to refresh the token if we have a refresh token
        if self.refresh_token:
            logger.info("Attempting to refresh access token...")
            if self._refresh_access_token():
                # Upgrade session for real-time data
                self._upgrade_session_for_realtime_data()
                return True
            logger.warning("Token refresh failed, initiating new OAuth flow")

        # Initiate new OAuth2 authorization flow
        success = self._oauth_authorization_flow()
        if success:
            # Upgrade session for real-time data
            self._upgrade_session_for_realtime_data()
        return success

    def _oauth_authorization_flow(self) -> bool:
        """
        Execute the full OAuth2 authorization code flow.

        This opens a browser for user authorization and runs a local
        HTTP server to receive the callback with the authorization code.

        Returns:
            bool: True if authorization successful, False otherwise.
        """
        try:
            # Build authorization URL with required parameters
            auth_params = {
                "client_id": self.app_key,
                "response_type": "code",
                "redirect_uri": self.saxo_config["redirect_uri"],
                "state": f"state_{int(time.time())}",  # CSRF protection
            }
            auth_url = f"{self.auth_url}?{urlencode(auth_params)}"

            logger.info("Opening browser for OAuth authorization...")
            logger.info(f"Authorization URL: {auth_url}")

            # Start local HTTP server for callback
            server_address = ("localhost", 8080)
            httpd = HTTPServer(server_address, OAuthCallbackHandler)
            httpd.timeout = 120  # 2 minute timeout for user to authorize

            # Open browser for user authorization
            webbrowser.open(auth_url)

            # Wait for callback with authorization code
            logger.info("Waiting for authorization callback...")
            httpd.handle_request()
            httpd.server_close()

            # Check if we received the authorization code
            if not OAuthCallbackHandler.authorization_code:
                logger.error("No authorization code received from callback")
                return False

            # Exchange authorization code for access token
            return self._exchange_code_for_token(OAuthCallbackHandler.authorization_code)

        except Exception as e:
            logger.error(f"OAuth authorization flow failed: {e}")
            self._record_error()
            return False

    def _exchange_code_for_token(self, auth_code: str) -> bool:
        """
        Exchange authorization code for access and refresh tokens.

        Args:
            auth_code: The authorization code from OAuth callback.

        Returns:
            bool: True if token exchange successful, False otherwise.
        """
        try:
            token_data = {
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": self.saxo_config["redirect_uri"],
                "client_id": self.app_key,
                "client_secret": self.app_secret,
            }

            response = requests.post(
                self.token_url,
                data=token_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )

            if response.status_code in [200, 201]:
                token_response = response.json()
                self.access_token = token_response["access_token"]
                self.refresh_token = token_response.get("refresh_token")

                # Calculate token expiry time
                expires_in = token_response.get("expires_in", 1200)  # Default 20 minutes
                self.token_expiry = datetime.now() + timedelta(seconds=expires_in - 60)

                logger.info("Access token obtained successfully")

                # Save tokens to config.json for persistence
                self._save_tokens_to_config()

                self._record_success()
                return True
            else:
                logger.error(f"Token exchange failed: {response.status_code} - {response.text}")
                self._record_error()
                return False

        except Exception as e:
            logger.error(f"Token exchange error: {e}")
            self._record_error()
            return False

    def _refresh_access_token(self) -> bool:
        """
        Refresh the access token using the refresh token.

        Returns:
            bool: True if refresh successful, False otherwise.
        """
        try:
            refresh_data = {
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "client_id": self.app_key,
                "client_secret": self.app_secret,
            }

            response = requests.post(
                self.token_url,
                data=refresh_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )

            if response.status_code in [200, 201]:
                token_response = response.json()
                self.access_token = token_response["access_token"]
                self.refresh_token = token_response.get("refresh_token", self.refresh_token)

                expires_in = token_response.get("expires_in", 1200)
                self.token_expiry = datetime.now() + timedelta(seconds=expires_in - 60)

                logger.info("Access token refreshed successfully")

                # Save tokens to config.json for persistence
                self._save_tokens_to_config()

                self._record_success()
                return True
            else:
                logger.warning(f"Token refresh failed: {response.status_code}")
                return False

        except Exception as e:
            logger.error(f"Token refresh error: {e}")
            return False

    def _is_token_valid(self) -> bool:
        """
        Check if the current access token is still valid.

        Returns:
            bool: True if token is valid and not expired, False otherwise.
        """
        if not self.access_token:
            return False
        if self.token_expiry and datetime.now() >= self.token_expiry:
            return False
        return True

    def _save_tokens_to_config(self):
        """
        Save current access and refresh tokens.

        Uses ConfigLoader to handle cloud (Secret Manager) vs local (file) storage.
        This allows tokens to persist between bot runs, avoiding the need
        to re-authenticate every time.
        """
        try:
            # Try to use ConfigLoader if available (handles cloud vs local)
            from src.config_loader import get_config_loader
            loader = get_config_loader()

            if loader:
                # Use ConfigLoader's save_tokens method
                token_expiry_str = self.token_expiry.isoformat() if self.token_expiry else None
                success = loader.save_tokens(
                    self.access_token or "",
                    self.refresh_token or "",
                    token_expiry_str
                )
                if success:
                    logger.info("Tokens saved successfully via ConfigLoader")
                    return
                else:
                    logger.warning("ConfigLoader save failed, falling back to direct file save")

            # Fallback: Direct file save (for backwards compatibility)
            self._save_tokens_to_file_direct()

        except Exception as e:
            logger.warning(f"Failed to save tokens: {e}")
            # Try direct file save as last resort
            self._save_tokens_to_file_direct()

    def _save_tokens_to_file_direct(self):
        """
        Direct file save fallback for tokens.

        Used when ConfigLoader is not available or fails.
        """
        try:
            with open("config/config.json", "r") as f:
                config_data = json.load(f)

            # Update tokens in the appropriate environment section
            env_key = self.environment  # "sim" or "live"
            config_data["saxo_api"][env_key]["access_token"] = self.access_token or ""
            config_data["saxo_api"][env_key]["refresh_token"] = self.refresh_token or ""

            if self.token_expiry:
                config_data["saxo_api"][env_key]["token_expiry"] = self.token_expiry.isoformat()

            with open("config/config.json", "w") as f:
                json.dump(config_data, f, indent=4)

            logger.info("Tokens saved to config/config.json (direct write)")

        except Exception as e:
            logger.error(f"Direct token save failed: {e}")

    def _get_auth_headers(self) -> Dict[str, str]:
        """
        Get HTTP headers with authentication.

        Returns:
            dict: Headers dictionary with Bearer token authorization.
        """
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def _upgrade_session_for_realtime_data(self) -> bool:
        """
        Upgrade session capabilities to FullTradingAndChat for real-time market data.

        By default, Saxo OpenAPI sessions have TradeLevel=OrdersOnly which only
        provides delayed market data (5-15 min). Upgrading to FullTradingAndChat
        enables real-time prices from your account's market data subscriptions.

        Note: Only one session per user can have FullTradingAndChat at a time.
        Upgrading this session will demote other active sessions.

        Returns:
            bool: True if upgrade successful or already upgraded, False otherwise.
        """
        try:
            # First, check current capabilities
            response = requests.get(
                f"{self.base_url}/root/v1/sessions/capabilities",
                headers=self._get_auth_headers(),
                timeout=10
            )

            if response.status_code == 200:
                caps = response.json()
                current_trade_level = caps.get("TradeLevel", "Unknown")
                current_data_level = caps.get("DataLevel", "Unknown")

                logger.info(f"Current session: TradeLevel={current_trade_level}, DataLevel={current_data_level}")

                # Already upgraded
                if current_trade_level == "FullTradingAndChat":
                    logger.info("Session already has FullTradingAndChat - real-time data enabled")
                    return True

            # Upgrade to FullTradingAndChat
            logger.info("Upgrading session to FullTradingAndChat for real-time market data...")

            upgrade_response = requests.patch(
                f"{self.base_url}/root/v1/sessions/capabilities",
                headers=self._get_auth_headers(),
                json={"TradeLevel": "FullTradingAndChat"},
                timeout=10
            )

            if upgrade_response.status_code == 202:
                logger.info("Session upgraded to FullTradingAndChat - real-time data enabled!")

                # Verify the upgrade (wait briefly for it to take effect)
                time.sleep(1)
                verify_response = requests.get(
                    f"{self.base_url}/root/v1/sessions/capabilities",
                    headers=self._get_auth_headers(),
                    timeout=10
                )
                if verify_response.status_code == 200:
                    new_caps = verify_response.json()
                    logger.info(
                        f"Verified: TradeLevel={new_caps.get('TradeLevel')}, "
                        f"DataLevel={new_caps.get('DataLevel')}"
                    )
                return True
            else:
                logger.warning(
                    f"Session upgrade returned {upgrade_response.status_code}: "
                    f"{upgrade_response.text}"
                )
                return False

        except Exception as e:
            logger.warning(f"Failed to upgrade session for real-time data: {e}")
            # Don't fail authentication just because upgrade failed
            return False

    # =========================================================================
    # CIRCUIT BREAKER METHODS
    # =========================================================================

    def _record_error(self):
        """
        Record an API error for circuit breaker tracking.

        Increments the consecutive error counter and opens the circuit
        if the threshold is reached.
        """
        self.circuit_breaker.consecutive_errors += 1
        self.circuit_breaker.last_error_time = datetime.now()

        max_errors = self.circuit_config["max_consecutive_errors"]

        if self.circuit_breaker.consecutive_errors >= max_errors:
            self._open_circuit()

    def _record_success(self):
        """
        Record a successful API call.

        Resets the consecutive error counter and updates last successful
        connection time.
        """
        self.circuit_breaker.consecutive_errors = 0
        self.circuit_breaker.last_successful_connection = datetime.now()

    def _open_circuit(self):
        """
        Open the circuit breaker to stop all trading.

        When the circuit is open, all trading operations are blocked
        until the cooldown period expires.
        """
        self.circuit_breaker.is_open = True
        cooldown_minutes = self.circuit_config["cooldown_minutes"]
        self.circuit_breaker.cooldown_until = datetime.now() + timedelta(minutes=cooldown_minutes)

        logger.critical(
            f"CIRCUIT BREAKER OPENED! Trading halted for {cooldown_minutes} minutes. "
            f"Consecutive errors: {self.circuit_breaker.consecutive_errors}"
        )

    def _close_circuit(self):
        """Close the circuit breaker and resume normal operations."""
        self.circuit_breaker.is_open = False
        self.circuit_breaker.consecutive_errors = 0
        self.circuit_breaker.cooldown_until = None
        logger.info("Circuit breaker closed. Trading resumed.")

    def is_circuit_open(self) -> bool:
        """
        Check if the circuit breaker is currently open.

        Also checks if the cooldown period has expired and automatically
        closes the circuit if so.

        Returns:
            bool: True if circuit is open (trading blocked), False otherwise.
        """
        if not self.circuit_breaker.is_open:
            return False

        # Check if cooldown has expired
        if (self.circuit_breaker.cooldown_until and
            datetime.now() >= self.circuit_breaker.cooldown_until):
            self._close_circuit()
            return False

        return True

    def check_connection_timeout(self) -> bool:
        """
        Check if connection has been lost for too long.

        Returns:
            bool: True if connection timeout exceeded, False otherwise.
        """
        max_disconnection = self.circuit_config["max_disconnection_seconds"]
        last_success = self.circuit_breaker.last_successful_connection

        if last_success:
            elapsed = (datetime.now() - last_success).total_seconds()
            if elapsed > max_disconnection:
                logger.warning(f"Connection timeout: {elapsed:.0f} seconds without successful connection")
                self._open_circuit()
                return True

        return False

    # =========================================================================
    # REST API METHODS - MARKET DATA
    # =========================================================================

    def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        data: Optional[Dict] = None
    ) -> Optional[Dict]:
        """
        Make an authenticated API request with circuit breaker protection.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            endpoint: API endpoint path
            params: Query parameters
            data: Request body data

        Returns:
            dict: Response JSON if successful, None if failed.
        """
        # Check circuit breaker
        if self.is_circuit_open():
            logger.warning("Circuit breaker is open. Request blocked.")
            return None

        # Ensure token is valid
        if not self._is_token_valid():
            if not self.authenticate():
                logger.error("Failed to authenticate before request")
                return None

        url = f"{self.base_url}{endpoint}"

        try:
            response = requests.request(
                method=method,
                url=url,
                headers=self._get_auth_headers(),
                params=params,
                json=data,
                timeout=30
            )

            # Added 202 to the success list
            if response.status_code in [200, 201, 202]:
                self._record_success()
                # 202 might not have body, safe parsing
                return response.json() if response.text else {}
            elif response.status_code == 204:
                self._record_success()
                return {}
            else:
                logger.error(f"API request failed: {response.status_code} - {response.text}")
                self._record_error()
                return None

        except requests.exceptions.Timeout:
            logger.error(f"Request timeout for {endpoint}")
            self._record_error()
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error for {endpoint}: {e}")
            self._record_error()
            return None

    def get_quote(self, uic: int, asset_type: str = "Stock") -> Optional[Dict]:
        """
        Get current quote for an instrument.

        First checks the streaming price cache for real-time data, then
        falls back to /trade/v1/infoprices/list endpoint.

        Args:
            uic: Unique Instrument Code
            asset_type: Type of asset (Stock, StockOption, Etf, StockIndex, FxSpot, etc.)

        Returns:
            dict: Quote data including Bid, Ask, LastTraded prices.
        """
        # First check streaming cache for real-time data
        uic_int = int(uic)
        if uic_int in self._price_cache:
            cached = self._price_cache[uic_int]
            if cached and "Quote" in cached:
                bid = cached["Quote"].get("Bid", 0)
                ask = cached["Quote"].get("Ask", 0)
                if bid > 0 and ask > 0:
                    logger.debug(f"get_quote: Using cached streaming data for UIC {uic}")
                    return cached

        # Fallback to infoprices REST API
        # Use /infoprices/list with AccountKey - required for sim environment
        endpoint = "/trade/v1/infoprices/list"
        params = {
            "AccountKey": self.account_key,
            "Uics": str(uic),
            "AssetType": asset_type,
            "Amount": 1,  # Always include Amount (number of units)
            "FieldGroups": "DisplayAndFormat,Quote,PriceInfo"
        }

        logger.debug(f"get_quote: Calling {endpoint} for UIC {uic}")

        response = self._make_request("GET", endpoint, params=params)
        logger.debug(f"get_quote: Response = {response}")

        # /infoprices/list returns a "Data" array - extract the first item
        if response and "Data" in response and len(response["Data"]) > 0:
            quote_data = response["Data"][0]
            logger.debug(f"get_quote: Extracted quote for UIC {uic}: Quote={quote_data.get('Quote')}")
            return quote_data

        # Fallback to single-instrument endpoint if list fails
        logger.debug(f"get_quote: List endpoint returned no Data for UIC {uic}, trying single endpoint")
        endpoint = "/trade/v1/infoprices"
        params = {
            "AccountKey": self.account_key,
            "Uic": uic,
            "AssetType": asset_type,
            "FieldGroups": "Quote,PriceInfoDetails"
        }

        response = self._make_request("GET", endpoint, params=params)
        if response:
            logger.debug(f"get_quote: Single endpoint response for UIC {uic}: {response}")
            return response
        return None

    def get_option_root_id(self, underlying_uic: int) -> Optional[int]:
        """
        Get the OptionRootId for an underlying instrument.

        This is required before fetching options chains. The OptionRootId is different
        from the instrument's UIC and is used specifically for options chain queries.

        Args:
            underlying_uic: UIC of the underlying (e.g., 36590 for SPY)

        Returns:
            int: OptionRootId, or None if not found
        """
        endpoint = "/ref/v1/instruments/details"
        params = {"Uics": underlying_uic}

        response = self._make_request("GET", endpoint, params=params)

        if not response or "Data" not in response or len(response["Data"]) == 0:
            logger.error(f"No instrument details found for UIC {underlying_uic}")
            return None

        instrument = response["Data"][0]
        related_options = instrument.get("RelatedOptionRootsEnhanced", [])

        # Look for StockOption type
        for option_root in related_options:
            if option_root.get("AssetType") == "StockOption":
                option_root_id = option_root.get("OptionRootId")
                logger.info(f"Found OptionRootId {option_root_id} for UIC {underlying_uic}")
                return option_root_id

        logger.error(f"No StockOption root found for UIC {underlying_uic}")
        return None

    def get_option_chain(
        self,
        option_root_id: int,
        expiry_dates: Optional[List[str]] = None,
        option_space_segment: str = "AllDates"
    ) -> Optional[Dict]:
        """
        Get option chain for an OptionRootId.

        NOTE: You must first call get_option_root_id() to get the option_root_id
        for your underlying instrument before calling this method.

        Args:
            option_root_id: Option root ID (get from get_option_root_id())
            expiry_dates: Optional list of specific expiry dates ["2024-02-16", ...]
            option_space_segment: "AllDates" (default) or "SpecificDates"

        Returns:
            dict: Response containing "OptionSpace" array with expiries and strikes
        """
        # OptionRootId goes in the URL path, not as a query parameter
        endpoint = f"/ref/v1/instruments/contractoptionspaces/{option_root_id}"

        params = {}

        # Filter by specific expiry dates if provided
        if expiry_dates:
            params["OptionSpaceSegment"] = "SpecificDates"
            params["ExpiryDates"] = expiry_dates
        elif option_space_segment:
            params["OptionSpaceSegment"] = option_space_segment

        response = self._make_request("GET", endpoint, params=params)

        if response:
            logger.debug(f"Got option chain for OptionRootId {option_root_id}")
            return response

        logger.error(f"Failed to get option chain for OptionRootId {option_root_id}")
        return None

    def get_option_expirations(self, underlying_uic: int) -> Optional[List[Dict]]:
        """
        Get available option expiration dates for an underlying.

        Internally calls get_option_root_id() then get_option_chain()
        to handle the two-step API process.

        Args:
            underlying_uic: UIC of the underlying instrument

        Returns:
            list: OptionSpace array with expiry information and strikes
        """
        # Step 1: Get OptionRootId
        option_root_id = self.get_option_root_id(underlying_uic)
        if not option_root_id:
            logger.error(f"Could not find OptionRootId for UIC {underlying_uic}")
            return None

        # Step 2: Get option chain
        option_chain = self.get_option_chain(option_root_id)

        # Step 3: Extract OptionSpace (not "Data" - that was the old incorrect field)
        if option_chain and "OptionSpace" in option_chain:
            return option_chain["OptionSpace"]

        logger.error(f"No OptionSpace found in response for OptionRootId {option_root_id}")
        return None

    def find_atm_options(
        self,
        underlying_uic: int,
        underlying_price: float,
        target_dte_min: int,
        target_dte_max: int
    ) -> Optional[Dict[str, Dict]]:
        """
        Find ATM (At-The-Money) call and put options.

        Args:
            underlying_uic: UIC of the underlying instrument
            underlying_price: Current price of the underlying
            target_dte_min: Minimum days to expiration
            target_dte_max: Maximum days to expiration

        Returns:
            dict: Dictionary with 'call' and 'put' option data.
        """
        expirations = self.get_option_expirations(underlying_uic)
        if not expirations:
            logger.error("Failed to get option expirations")
            return None

        # Find expiration within target DTE range
        today = datetime.now().date()
        target_expiration = None

        for exp_data in expirations:
            exp_date_str = exp_data.get("Expiry")
            if not exp_date_str:
                continue

            exp_date = datetime.strptime(exp_date_str[:10], "%Y-%m-%d").date()
            dte = (exp_date - today).days

            if target_dte_min <= dte <= target_dte_max:
                target_expiration = exp_data
                logger.info(f"Found expiration: {exp_date_str} with {dte} DTE")
                break

        if not target_expiration:
            logger.warning(f"No expiration found within {target_dte_min}-{target_dte_max} DTE range")
            return None

        # Get strikes for this expiration (SpecificOptions array, not "Strikes")
        specific_options = target_expiration.get("SpecificOptions", [])

        if not specific_options:
            logger.error("No SpecificOptions in target expiration")
            return None

        # Find ATM strike (closest to current price)
        # First pass: find the strike price closest to underlying price
        atm_strike_price = None
        min_diff = float('inf')

        for option in specific_options:
            strike_price = option.get("StrikePrice", 0)
            diff = abs(strike_price - underlying_price)
            if diff < min_diff:
                min_diff = diff
                atm_strike_price = strike_price

        if atm_strike_price is None or atm_strike_price == 0:
            logger.error("Failed to find ATM strike")
            return None

        logger.info(f"ATM strike: {atm_strike_price} (underlying: {underlying_price})")

        # Second pass: find Call and Put UICs at ATM strike
        call_uic = None
        put_uic = None

        for option in specific_options:
            if option.get("StrikePrice") == atm_strike_price:
                if option.get("PutCall") == "Call":
                    call_uic = option.get("Uic")
                elif option.get("PutCall") == "Put":
                    put_uic = option.get("Uic")

        if not call_uic or not put_uic:
            logger.error(f"Failed to find Call or Put UIC at strike {atm_strike_price}")
            return None

        return {
            "call": {
                "uic": call_uic,
                "strike": atm_strike_price,
                "expiry": target_expiration.get("Expiry"),
                "option_type": "Call"
            },
            "put": {
                "uic": put_uic,
                "strike": atm_strike_price,
                "expiry": target_expiration.get("Expiry"),
                "option_type": "Put"
            }
        }

    def find_strangle_options(
        self,
        underlying_uic: int,
        underlying_price: float,
        expected_move: float,
        multiplier: float = 1.5,
        weekly: bool = True
    ) -> Optional[Dict[str, Dict]]:
        """
        Find OTM options for a short strangle at specified distance.

        Args:
            underlying_uic: UIC of the underlying
            underlying_price: Current underlying price
            expected_move: Expected weekly move in dollars
            multiplier: Multiplier for the expected move (1.5-2.0x)
            weekly: If True, find weekly options

        Returns:
            dict: Dictionary with 'call' and 'put' option data for strangle.
        """
        expirations = self.get_option_expirations(underlying_uic)
        if not expirations:
            return None

        # For weekly, find nearest Friday expiration
        today = datetime.now().date()
        target_expiration = None

        if weekly:
            # Find nearest Friday within 7 days
            for exp_data in expirations:
                exp_date_str = exp_data.get("Expiry")
                if not exp_date_str:
                    continue
                exp_date = datetime.strptime(exp_date_str[:10], "%Y-%m-%d").date()
                dte = (exp_date - today).days

                if 0 < dte <= 7:
                    target_expiration = exp_data
                    logger.info(f"Found weekly expiration: {exp_date_str} with {dte} DTE")
                    break

        if not target_expiration:
            logger.warning("No suitable weekly expiration found")
            return None

        # Get strikes for this expiration (SpecificOptions array, not "Strikes")
        specific_options = target_expiration.get("SpecificOptions", [])

        if not specific_options:
            logger.error("No SpecificOptions in target expiration")
            return None

        # Calculate target strikes for strangle
        move_distance = expected_move * multiplier
        call_target = underlying_price + move_distance
        put_target = underlying_price - move_distance

        # Find closest strikes to targets
        call_strike_price = None
        put_strike_price = None
        min_call_diff = float('inf')
        min_put_diff = float('inf')

        # First pass: find closest strike prices to targets
        for option in specific_options:
            strike_price = option.get("StrikePrice", 0)

            # For call, find strike closest to and above target
            if strike_price >= underlying_price:
                diff = abs(strike_price - call_target)
                if diff < min_call_diff:
                    min_call_diff = diff
                    call_strike_price = strike_price

            # For put, find strike closest to and below target
            if strike_price <= underlying_price:
                diff = abs(strike_price - put_target)
                if diff < min_put_diff:
                    min_put_diff = diff
                    put_strike_price = strike_price

        if call_strike_price is None or put_strike_price is None:
            logger.error("Failed to find strangle strike prices")
            return None

        # Second pass: find UICs for the selected strikes
        call_uic = None
        put_uic = None

        for option in specific_options:
            if option.get("StrikePrice") == call_strike_price and option.get("PutCall") == "Call":
                call_uic = option.get("Uic")
            elif option.get("StrikePrice") == put_strike_price and option.get("PutCall") == "Put":
                put_uic = option.get("Uic")

        if not call_uic or not put_uic:
            logger.error(f"Failed to find strangle option UICs")
            return None

        logger.info(
            f"Strangle strikes: Put {put_strike_price} / "
            f"Call {call_strike_price} (underlying: {underlying_price})"
        )

        return {
            "call": {
                "uic": call_uic,
                "strike": call_strike_price,
                "expiry": target_expiration.get("Expiry"),
                "option_type": "Call"
            },
            "put": {
                "uic": put_uic,
                "strike": put_strike_price,
                "expiry": target_expiration.get("Expiry"),
                "option_type": "Put"
            }
        }

    def get_spy_price(self, spy_uic: int, symbol: str = "SPY") -> Optional[Dict]:
        """
        Get SPY quote from Saxo with Yahoo Finance as last resort fallback.

        Priority:
        1. Saxo real-time price (during market hours)
        2. Saxo last traded price (after hours / market closed)
        3. Yahoo Finance (only if Saxo completely fails)

        Args:
            spy_uic: SPY UIC
            symbol: Symbol name for external feed fallback

        Returns:
            dict: Quote data with price information, or None if unavailable
        """
        # Try Saxo API first with extended fields
        endpoint = "/trade/v1/infoprices/list"
        params = {
            "AccountKey": self.account_key,
            "Uics": str(spy_uic),
            "AssetType": "Etf",
            "FieldGroups": "DisplayAndFormat,Quote,PriceInfo,PriceInfoDetails"
        }

        response = self._make_request("GET", endpoint, params=params)

        if response and "Data" in response and len(response["Data"]) > 0:
            quote_data = response["Data"][0]
            quote = quote_data.get("Quote", {})
            price_info = quote_data.get("PriceInfo", {})
            price_info_details = quote_data.get("PriceInfoDetails", {})

            # Try to get price from various fields (handles both live and after-hours)
            price = (
                quote.get("Mid") or
                quote.get("LastTraded") or
                quote.get("Bid") or
                quote.get("Ask") or
                price_info_details.get("LastTraded") or
                price_info.get("Last")
            )

            if price:
                # Ensure Mid/LastTraded are set for downstream code
                if not quote.get("Mid"):
                    quote_data["Quote"]["Mid"] = price
                if not quote.get("LastTraded"):
                    quote_data["Quote"]["LastTraded"] = price

                logger.debug(f"{symbol}: Saxo price ${price} (MarketState: {quote.get('MarketState', 'Unknown')})")
                return quote_data

            # Check if NoAccess (shouldn't happen with FullTradingAndChat, but handle it)
            if quote.get("PriceTypeAsk") == "NoAccess" or quote.get("PriceTypeBid") == "NoAccess":
                logger.warning(f"{symbol}: Saxo API returned NoAccess - falling back to Yahoo")
            else:
                logger.warning(f"{symbol}: Saxo returned no price data")

        # Last resort: Yahoo Finance fallback
        if self.external_feed.enabled:
            logger.info(f"{symbol}: Using Yahoo Finance fallback")
            external_price = self.external_feed.get_price(symbol)

            if external_price:
                # Create a quote structure with the external price
                quote_data = {
                    "Quote": {
                        "Mid": external_price,
                        "LastTraded": external_price,
                        "_external_source": True
                    },
                    "DisplayAndFormat": {"Symbol": symbol}
                }
                logger.info(f"{symbol}: Yahoo price ${external_price:.2f}")
                return quote_data

        logger.error(f"{symbol}: All price sources failed")
        return None

    def get_vix_price(self, vix_uic: int) -> Optional[float]:
        """
        Get VIX price from Saxo with Yahoo Finance as last resort fallback.

        Priority:
        1. Saxo price cache (from streaming subscription)
        2. Saxo REST API (real-time or last traded)
        3. Yahoo Finance (only if Saxo completely fails)

        Args:
            vix_uic: VIX UIC (typically 10606)

        Returns:
            float: VIX price, or None if all sources fail
        """
        # Ensure UIC is int for consistent cache lookup
        vix_uic = int(vix_uic)
        logger.debug(f"get_vix_price called: looking for UIC {vix_uic}")

        # 1. Check the price cache (from subscription snapshots)
        if vix_uic in self._price_cache:
            cached_data = self._price_cache[vix_uic]
            price = self._extract_price_from_data(cached_data, "VIX cache")
            if price:
                return price

        # 2. REST API - Try Saxo with extended fields for index data
        endpoint = "/trade/v1/infoprices/list"
        params = {
            "AccountKey": self.account_key,
            "Uics": str(vix_uic),
            "AssetType": "StockIndex",
            "FieldGroups": "DisplayAndFormat,Quote,PriceInfo,PriceInfoDetails"
        }

        response = self._make_request("GET", endpoint, params=params)

        if response and "Data" in response and len(response["Data"]) > 0:
            data = response["Data"][0]
            price = self._extract_price_from_data(data, "VIX API")
            if price:
                return price

        # 3. Last resort: Yahoo Finance fallback
        if self.external_feed.enabled:
            logger.info("VIX: Saxo failed, using Yahoo Finance fallback")
            external_price = self.external_feed.get_vix_price()
            if external_price:
                logger.info(f"VIX: Yahoo price {external_price}")
                return external_price

        logger.error(f"VIX: All price sources failed for UIC {vix_uic}")
        return None

    def _extract_price_from_data(self, data: Dict, source: str) -> Optional[float]:
        """
        Extract price from Saxo quote data structure.

        Handles various data structures returned by Saxo for different asset types
        and market states (open, closed, after-hours).

        Args:
            data: Quote data dictionary from Saxo
            source: Source name for logging

        Returns:
            float: Extracted price, or None if not found
        """
        price = None

        # Try Quote block first (standard for tradable instruments)
        if "Quote" in data and isinstance(data["Quote"], dict):
            quote = data["Quote"]
            price = (
                quote.get("Mid") or
                quote.get("LastTraded") or
                quote.get("Bid") or
                quote.get("Ask")
            )

        # Try PriceInfoDetails (used for indices like VIX)
        if price is None and "PriceInfoDetails" in data:
            price = data["PriceInfoDetails"].get("LastTraded")

        # Try PriceInfo
        if price is None and "PriceInfo" in data:
            p_info = data["PriceInfo"]
            price = p_info.get("LastTraded") or p_info.get("Last")

        # Try top-level LastTraded
        if price is None:
            price = data.get("LastTraded")

        if price:
            logger.debug(f"{source}: price {price}")
            return float(price)

        return None

    def check_bid_ask_spread(
        self,
        uic: int,
        asset_type: str = "StockOption",
        max_spread_percent: float = 0.5
    ) -> tuple[bool, float]:
        """
        Check if bid-ask spread is within acceptable threshold.

        For StockOptions, uses streaming /trade/v1/prices endpoint for real-time
        tradable quotes. Falls back to infoprices if streaming fails.

        Args:
            uic: Instrument UIC
            asset_type: Type of asset
            max_spread_percent: Maximum acceptable spread as percentage

        Returns:
            tuple: (is_acceptable, spread_percent)
        """
        quote = None
        bid = 0
        ask = 0

        # For options, prefer streaming quotes (real-time tradable prices)
        if asset_type == "StockOption" and self.is_streaming:
            logger.debug(f"Using streaming subscription for option UIC {uic}")
            quote = self.get_streaming_option_quote(uic, max_wait_seconds=5.0)

            if quote and "Quote" in quote:
                bid = quote["Quote"].get("Bid", 0)
                ask = quote["Quote"].get("Ask", 0)

                if bid > 0 and ask > 0:
                    logger.info(f"Got streaming quote for UIC {uic}: Bid={bid:.2f}, Ask={ask:.2f}")
                else:
                    logger.warning(f"Streaming quote for UIC {uic} has invalid bid/ask: Bid={bid}, Ask={ask}")
                    quote = None  # Fall through to infoprices

        # Fallback to infoprices (or for non-options)
        if not quote or bid <= 0 or ask <= 0:
            logger.debug(f"Using infoprices fallback for UIC {uic}")

            # Retry logic for pending quotes from infoprices
            max_retries = 3
            retry_delay = 2  # seconds

            for attempt in range(max_retries):
                quote = self.get_quote(uic, asset_type)
                if not quote or "Quote" not in quote:
                    logger.warning(f"No quote data for UIC {uic} (attempt {attempt + 1}/{max_retries})")
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        continue
                    return False, 0.0

                bid = quote["Quote"].get("Bid", 0)
                ask = quote["Quote"].get("Ask", 0)
                price_type_bid = quote["Quote"].get("PriceTypeBid", "")
                price_type_ask = quote["Quote"].get("PriceTypeAsk", "")

                # Check if quotes are pending
                if (bid <= 0 or ask <= 0) and (price_type_bid == "Pending" or price_type_ask == "Pending"):
                    logger.warning(f"Quotes pending for UIC {uic} (attempt {attempt + 1}/{max_retries}): Bid={bid}, Ask={ask}")
                    if attempt < max_retries - 1:
                        logger.info(f"Waiting {retry_delay}s for quotes to become available...")
                        time.sleep(retry_delay)
                        continue
                    return False, 0.0

                # Check if bid/ask are valid
                if bid <= 0 or ask <= 0:
                    logger.warning(f"Invalid bid/ask for UIC {uic}: Bid={bid}, Ask={ask}, Quote={quote.get('Quote')}")
                    return False, 0.0

                # Valid quotes received, break retry loop
                break

        mid = (bid + ask) / 2
        spread = ask - bid
        spread_percent = (spread / mid) * 100

        is_acceptable = spread_percent <= max_spread_percent

        if not is_acceptable:
            logger.warning(
                f"Bid-ask spread {spread_percent:.2f}% exceeds threshold {max_spread_percent}%"
            )

        return is_acceptable, spread_percent

    # =========================================================================
    # REST API METHODS - TRADING
    # =========================================================================

    def get_positions(self, include_greeks: bool = True) -> Optional[List[Dict]]:
        """
        Get all current positions for the account.

        Args:
            include_greeks: If True, request Greeks data (Delta, Gamma, Theta, Vega)
                           for options positions. Default True.

        Returns:
            list: List of position dictionaries with Greeks if available.

        Note:
            Greeks FieldGroup returns: Delta, Gamma, Theta, Vega, Rho, Phi,
            TheoreticalPrice, MidVol, and currency-specific variants.
        """
        endpoint = f"/port/v1/positions"

        # Build FieldGroups - always include base fields, optionally add Greeks
        field_groups = ["DisplayAndFormat", "PositionBase", "PositionView"]
        if include_greeks:
            field_groups.append("Greeks")

        params = {
            "ClientKey": self.client_key,
            "FieldGroups": ",".join(field_groups)
        }

        response = self._make_request("GET", endpoint, params=params)
        if response and "Data" in response:
            positions = response["Data"]

            # Log if Greeks were returned for debugging
            if include_greeks and positions:
                for pos in positions:
                    greeks = pos.get("Greeks", {})
                    if greeks:
                        symbol = pos.get("DisplayAndFormat", {}).get("Symbol", "Unknown")
                        logger.debug(
                            f"Greeks for {symbol}: Delta={greeks.get('Delta', 'N/A')}, "
                            f"Gamma={greeks.get('Gamma', 'N/A')}, "
                            f"Theta={greeks.get('Theta', 'N/A')}, "
                            f"Vega={greeks.get('Vega', 'N/A')}"
                        )

            return positions
        return []

    def get_open_orders(self) -> Optional[List[Dict]]:
        """
        Get all open orders for the account.

        Returns:
            list: List of open order dictionaries.
        """
        endpoint = f"/port/v1/orders"
        params = {
            "ClientKey": self.client_key,
            "FieldGroups": "DisplayAndFormat"
        }

        response = self._make_request("GET", endpoint, params=params)
        if response and "Data" in response:
            return response["Data"]
        return []

    def place_order(
        self,
        uic: int,
        asset_type: str,
        buy_sell: BuySell,
        amount: int,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Optional[float] = None,
        duration_type: str = "DayOrder"
    ) -> Optional[Dict]:
        """
        Place a single order.

        Args:
            uic: Instrument UIC
            asset_type: Type of asset
            buy_sell: Buy or Sell direction
            amount: Number of contracts/shares
            order_type: Type of order (Market, Limit, etc.)
            limit_price: Limit price for limit orders
            duration_type: Order duration (DayOrder, GoodTillCancel, etc.)

        Returns:
            dict: Order response with OrderId if successful.
        """
        endpoint = "/trade/v2/orders"

        order_data = {
            "AccountKey": self.account_key,
            "Uic": uic,
            "AssetType": asset_type,
            "BuySell": buy_sell.value,
            "Amount": amount,
            "OrderType": order_type.value,
            "OrderRelation": "StandAlone",
            "OrderDuration": {
                "DurationType": duration_type
            },
            "ManualOrder": True
        }

        if order_type == OrderType.LIMIT and limit_price:
            order_data["OrderPrice"] = limit_price

        logger.info(f"Placing order: {buy_sell.value} {amount} x UIC {uic}")

        response = self._make_request("POST", endpoint, data=order_data)
        if response:
            logger.info(f"Order placed successfully: {response.get('OrderId')}")
            return response

        logger.error("Failed to place order")
        return None

    def place_multi_leg_order(
        self,
        legs: List[Dict],
        order_type: OrderType = OrderType.MARKET,
        duration_type: str = "DayOrder"
    ) -> Optional[Dict]:
        """
        Place a multi-leg order (e.g., straddle, strangle).

        Args:
            legs: List of leg dictionaries with uic, asset_type, buy_sell, amount
            order_type: Type of order
            duration_type: Order duration

        Returns:
            dict: Order response if successful.

        Example:
            >>> legs = [
            ...     {"uic": 123, "asset_type": "StockOption", "buy_sell": "Buy", "amount": 1},
            ...     {"uic": 456, "asset_type": "StockOption", "buy_sell": "Buy", "amount": 1}
            ... ]
            >>> client.place_multi_leg_order(legs)
        """
        endpoint = "/trade/v2/orders"

        order_data = {
            "AccountKey": self.account_key,
            "OrderType": order_type.value,
            "OrderDuration": {
                "DurationType": duration_type
            },
            "ManualOrder": True,
            "Orders": []
        }

        for leg in legs:
            leg_order = {
                "Uic": leg["uic"],
                "AssetType": leg["asset_type"],
                "BuySell": leg["buy_sell"],
                "Amount": leg["amount"]
            }
            order_data["Orders"].append(leg_order)

        logger.info(f"Placing multi-leg order with {len(legs)} legs")

        response = self._make_request("POST", endpoint, data=order_data)
        if response:
            logger.info(f"Multi-leg order placed successfully")
            return response

        logger.error("Failed to place multi-leg order")
        return None

    def close_position(self, position_id: str) -> Optional[Dict]:
        """
        Close a specific position.

        Args:
            position_id: The position ID to close

        Returns:
            dict: Response if successful.
        """
        endpoint = f"/trade/v2/positions/{position_id}"

        logger.info(f"Closing position: {position_id}")

        response = self._make_request("DELETE", endpoint)
        if response is not None:
            logger.info(f"Position {position_id} closed successfully")
            return response

        logger.error(f"Failed to close position {position_id}")
        return None

    def cancel_order(self, order_id: str) -> Optional[Dict]:
        """
        Cancel an open order.

        Args:
            order_id: The order ID to cancel

        Returns:
            dict: Response if successful.
        """
        endpoint = f"/trade/v2/orders/{order_id}"

        logger.info(f"Cancelling order: {order_id}")

        response = self._make_request("DELETE", endpoint)
        if response is not None:
            logger.info(f"Order {order_id} cancelled successfully")
            return response

        logger.error(f"Failed to cancel order {order_id}")
        return None

    # =========================================================================
    # WEBSOCKET STREAMING METHODS (FIXED)
    # =========================================================================

    def start_price_streaming(
        self,
        subscriptions: List[Dict[str, Any]],
        callback: Callable[[int, Dict], None]
    ) -> bool:
        """
        Start WebSocket streaming for real-time price updates.
        
        FIXED: Now handles multiple AssetTypes correctly by creating 
        individual subscriptions for each instrument.

        Args:
            subscriptions: List of dicts, e.g. [{"uic": 211, "asset_type": "Stock"}, ...]
            callback: Function to call with price updates (uic, data)

        Returns:
            bool: True if all subscriptions were attempted.
        """
        if self.is_streaming:
            logger.warning("Streaming already active. Adding new subscriptions...")
        
        # 1. Start the WebSocket thread if it's not running
        if not self.ws_connection:
            self._start_websocket()
            # Give the socket a moment to connect
            time.sleep(2)

        success_count = 0

        # 2. Loop through each instrument and subscribe individually
        for item in subscriptions:
            uic = int(item["uic"])  # Ensure UIC is always int for consistent cache keys
            asset_type = item["asset_type"]

            # Store callback
            self.price_callbacks[uic] = callback

            # Create individual subscription request
            # CRITICAL FIX: Use "Uic" (singular), not "Uics"
            # Include AccountKey for proper sim environment access
            subscription_request = {
                "ContextId": self.subscription_context_id,
                "ReferenceId": f"ref_{uic}",
                "Arguments": {
                    "AccountKey": self.account_key,
                    "Uic": int(uic),           # Fix: Must be Int, singular
                    "AssetType": asset_type,   # Fix: Specific type for this UIC
                    "FieldGroups": ["DisplayAndFormat", "Quote", "PriceInfo"]
                }
            }

            endpoint = "/trade/v1/prices/subscriptions"
            response = self._make_request("POST", endpoint, data=subscription_request)

            if response and "Snapshot" in response:
                logger.info(f"✓ Subscribed to UIC {uic} ({asset_type})")
                success_count += 1

                # Cache the snapshot price data for later retrieval
                snapshot = response["Snapshot"]
                self._price_cache[uic] = snapshot

                # Log the snapshot structure for debugging
                snapshot_keys = list(snapshot.keys()) if isinstance(snapshot, dict) else str(type(snapshot))
                logger.debug(f"  Cached UIC {uic} (type={type(uic).__name__}), keys: {snapshot_keys}")
                logger.debug(f"  Cache now contains UICs: {list(self._price_cache.keys())}")
                # Show price data if available
                if isinstance(snapshot, dict):
                    if "Quote" in snapshot:
                        logger.debug(f"  Quote data: {snapshot['Quote']}")

                # Immediately process the snapshot so we don't have to wait for a tick
                if callback:
                    callback(uic, snapshot)
            else:
                logger.error(f"✗ Failed to subscribe to UIC {uic} ({asset_type})")

        return success_count > 0

    def _start_websocket(self):
        """Initialize and start the WebSocket connection."""
        def on_message(ws, message):
            """Handle incoming WebSocket messages."""
            try:
                # Saxo sends bytes or string; decode if needed
                if isinstance(message, bytes):
                    message = message.decode('utf-8')
                    
                data = json.loads(message)
                
                # heartbeat checks
                if "ReferenceId" in data and data["ReferenceId"] == "_heartbeat":
                    return

                self._handle_streaming_message(data)
                self._record_success()
            except Exception as e:
                # Don't log every decode error (keep logs clean)
                pass

        def on_error(ws, error):
            logger.error(f"WebSocket error: {error}")

        def on_close(ws, close_status_code, close_msg):
            logger.warning(f"WebSocket closed: {close_status_code} - {close_msg}")
            self.is_streaming = False

        def on_open(ws):
            logger.info("WebSocket connection established")
            self.is_streaming = True
            
        # FIXED: Surgical URL construction for Saxo Sim
        # 1. Clean the base URL
        base_url = self.streaming_url.split('?')[0].rstrip('/')
        
        # 2. Saxo Sim usually uses /connect. 
        # Only append /connect if it's not already in the URL from config.
        if "/connect" not in base_url:
             base_url = f"{base_url}/connect"
            
        # 3. Final Assembly
        ws_url = f"{base_url}?contextId={self.subscription_context_id}"
        
        logger.info(f"Attempting WebSocket connection to: {ws_url}")
        
        # CRITICAL: Saxo requires the Authorization header in the handshake
        self.ws_connection = websocket.WebSocketApp(
            ws_url,
            header={"Authorization": f"Bearer {self.access_token}"},
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open
        )

        self.ws_thread = threading.Thread(
            target=self.ws_connection.run_forever,
            # Increased timeout and interval for stability in sim environment
            kwargs={"ping_interval": 30, "ping_timeout": 10}
        )
        self.ws_thread.daemon = True
        self.ws_thread.start()
        
        logger.info("WebSocket thread initialized")

    def _handle_streaming_message(self, data: Dict):
        """
        Handle incoming streaming message and dispatch to callbacks.

        Args:
            data: The parsed message data
        """
        # Extract UIC and quote data from the message
        if "Data" in data:
            for item in data["Data"]:
                uic = item.get("Uic")
                if uic:
                    # Ensure UIC is int for consistent cache keys
                    uic = int(uic)

                    # Update price cache with latest data
                    self._price_cache[uic] = item

                    # Call the callback if registered
                    if uic in self.price_callbacks:
                        self.price_callbacks[uic](uic, item)

    def stop_price_streaming(self):
        """Stop WebSocket streaming and clean up subscriptions."""
        if self.ws_connection:
            self.ws_connection.close()
            self.ws_connection = None

        self.is_streaming = False
        self.price_callbacks.clear()

        # Delete subscription via REST
        endpoint = f"/trade/v1/prices/subscriptions/{self.subscription_context_id}"
        self._make_request("DELETE", endpoint)

        logger.info("Price streaming stopped")

    def subscribe_to_option(self, uic: int, callback: Callable[[int, Dict], None] = None) -> bool:
        """
        Subscribe to real-time price streaming for a specific option.

        This uses /trade/v1/prices/subscriptions to get real-time tradable prices
        instead of the indicative prices from /trade/v1/infoprices.

        Args:
            uic: The option's Unique Instrument Code
            callback: Optional callback for price updates

        Returns:
            bool: True if subscription successful and we have valid quote data
        """
        if not self.is_streaming:
            logger.warning("WebSocket not connected. Starting streaming first...")
            # We need an existing streaming connection
            return False

        # Check if already subscribed
        if uic in self._price_cache:
            cached = self._price_cache[uic]
            if cached and "Quote" in cached:
                bid = cached["Quote"].get("Bid", 0)
                ask = cached["Quote"].get("Ask", 0)
                if bid > 0 and ask > 0:
                    logger.debug(f"Option UIC {uic} already subscribed with valid quotes")
                    return True

        # Create subscription for this option
        subscription_request = {
            "ContextId": self.subscription_context_id,
            "ReferenceId": f"opt_{uic}",
            "Arguments": {
                "AccountKey": self.account_key,
                "Uic": int(uic),
                "AssetType": "StockOption",
                "FieldGroups": ["DisplayAndFormat", "Quote", "PriceInfo"]
            }
        }

        endpoint = "/trade/v1/prices/subscriptions"
        response = self._make_request("POST", endpoint, data=subscription_request)

        if response and "Snapshot" in response:
            snapshot = response["Snapshot"]
            self._price_cache[uic] = snapshot

            # Store callback if provided
            if callback:
                self.price_callbacks[uic] = callback

            # Check if we got valid quote data
            if "Quote" in snapshot:
                bid = snapshot["Quote"].get("Bid", 0)
                ask = snapshot["Quote"].get("Ask", 0)
                if bid > 0 and ask > 0:
                    logger.info(f"✓ Subscribed to option UIC {uic}: Bid={bid}, Ask={ask}")
                    return True
                else:
                    # Quotes might be pending, wait a moment and check cache
                    logger.info(f"Option UIC {uic} subscribed, waiting for quotes...")
                    time.sleep(1)

                    # Check if streaming updated the cache
                    if uic in self._price_cache:
                        cached = self._price_cache[uic]
                        if "Quote" in cached:
                            bid = cached["Quote"].get("Bid", 0)
                            ask = cached["Quote"].get("Ask", 0)
                            if bid > 0 and ask > 0:
                                logger.info(f"✓ Option UIC {uic} quotes received: Bid={bid}, Ask={ask}")
                                return True

                    logger.warning(f"Option UIC {uic} subscribed but quotes still pending")
                    return True  # Return True since subscription worked, quotes may come via stream

            logger.warning(f"Option UIC {uic} subscribed but no Quote in snapshot")
            return True  # Subscription worked, data may come via stream
        else:
            logger.error(f"✗ Failed to subscribe to option UIC {uic}")
            return False

    def get_streaming_option_quote(self, uic: int, max_wait_seconds: float = 3.0) -> Optional[Dict]:
        """
        Get option quote from streaming cache, subscribing if needed.

        This is the preferred method for getting real-time tradable option quotes.
        It uses the streaming /trade/v1/prices endpoint instead of infoprices.

        Args:
            uic: The option's Unique Instrument Code
            max_wait_seconds: Maximum time to wait for quotes after subscribing

        Returns:
            dict: Quote data with Bid/Ask, or None if unavailable
        """
        # First check if we already have valid cached data
        if uic in self._price_cache:
            cached = self._price_cache[uic]
            if cached and "Quote" in cached:
                bid = cached["Quote"].get("Bid", 0)
                ask = cached["Quote"].get("Ask", 0)
                if bid > 0 and ask > 0:
                    return cached

        # Subscribe to the option if not already
        if not self.subscribe_to_option(uic):
            logger.warning(f"Could not subscribe to option UIC {uic}")
            return None

        # Wait for valid quotes to arrive via stream
        start_time = time.time()
        poll_interval = 0.5

        while time.time() - start_time < max_wait_seconds:
            if uic in self._price_cache:
                cached = self._price_cache[uic]
                if cached and "Quote" in cached:
                    bid = cached["Quote"].get("Bid", 0)
                    ask = cached["Quote"].get("Ask", 0)
                    if bid > 0 and ask > 0:
                        logger.debug(f"Got streaming quote for UIC {uic}: Bid={bid}, Ask={ask}")
                        return cached

            time.sleep(poll_interval)

        logger.warning(f"Timeout waiting for streaming quote for UIC {uic}")

        # Return whatever we have, even if quotes are 0
        return self._price_cache.get(uic)

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================

    def search_instrument(
        self,
        symbol: str,
        asset_type: str = "Stock"
    ) -> Optional[Dict]:
        """
        Search for an instrument by symbol.

        Args:
            symbol: The ticker symbol to search for
            asset_type: Type of asset

        Returns:
            dict: Instrument data including UIC.
        """
        endpoint = "/ref/v1/instruments"
        params = {
            "Keywords": symbol,
            "AssetTypes": asset_type
        }

        response = self._make_request("GET", endpoint, params=params)
        if response and "Data" in response and len(response["Data"]) > 0:
            # Return first matching instrument
            return response["Data"][0]
        return None

    def get_accounts(self) -> Optional[List[Dict]]:
        """
        Get list of all accounts accessible to the user.

        Returns:
            list: List of account dictionaries with AccountKey, AccountType, Currency, etc.
        """
        endpoint = "/port/v1/accounts/me"
        response = self._make_request("GET", endpoint)
        if response and "Data" in response:
            return response["Data"]
        return None

    def get_account_info(self) -> Optional[Dict]:
        """
        Get account information.

        Returns:
            dict: Account details including balance.
        """
        endpoint = f"/port/v1/accounts/{self.account_key}"
        return self._make_request("GET", endpoint)

    def get_balance(self) -> Optional[Dict]:
        """
        Get account balance information.

        Returns:
            dict: Balance details including cash, margin, etc.
        """
        endpoint = "/port/v1/balances"
        params = {
            "AccountKey": self.account_key,
            "ClientKey": self.client_key
        }
        return self._make_request("GET", endpoint, params=params)

    def get_fx_rate(
        self,
        from_currency: str = "USD",
        to_currency: str = "EUR"
    ) -> Optional[float]:
        """
        Get foreign exchange rate from Saxo API.

        Uses the FxSpot instrument to get real-time rates.
        Example: USD/EUR rate for converting USD profits to EUR.

        Args:
            from_currency: Source currency (e.g., "USD")
            to_currency: Target currency (e.g., "EUR")

        Returns:
            float: Exchange rate, or None if failed

        Example:
            >>> rate = client.get_fx_rate("USD", "EUR")  # Returns ~0.92
            >>> eur_value = usd_value * rate
        """
        fx_pair = f"{to_currency}{from_currency}"  # "EURUSD"
        fx_uic = None

        # First, try to use hardcoded UIC from config (more reliable in sim)
        if fx_pair == "EURUSD" and self.currency_config.get("eur_usd_uic"):
            fx_uic = self.currency_config["eur_usd_uic"]
            logger.debug(f"Using hardcoded EUR/USD UIC: {fx_uic}")

        # If no hardcoded UIC, try to search for it
        if fx_uic is None:
            search_endpoint = "/ref/v1/instruments"
            params = {
                "Keywords": fx_pair,
                "AssetTypes": "FxSpot",
                "limit": 1
            }

            instrument = self._make_request("GET", search_endpoint, params=params)
            if instrument and "Data" in instrument and len(instrument["Data"]) > 0:
                fx_uic = instrument["Data"][0]["Identifier"]
            else:
                logger.warning(f"Could not find FX pair {fx_pair} via search")
                return None

        # Get current quote using FxSpot asset type
        quote = self.get_quote(fx_uic, asset_type="FxSpot")
        if not quote or "Quote" not in quote:
            logger.warning(f"Could not get FxSpot quote for {fx_pair}, UIC {fx_uic}")
            return None

        # Get mid price
        rate = quote["Quote"].get("Mid") or quote["Quote"].get("LastTraded")

        if not rate:
            logger.warning(f"No price available for {fx_pair}")
            return None

        # EURUSD quote is EUR/USD (e.g., 1.08)
        # We need USD/EUR (e.g., 0.92), so invert
        if fx_pair == "EURUSD":
            rate = 1.0 / rate

        logger.debug(f"FX Rate {from_currency}/{to_currency}: {rate:.6f}")
        return rate

    def calculate_expected_move(
        self,
        underlying_price: float,
        iv: float,
        days: int = 7
    ) -> float:
        """
        Calculate expected move based on implied volatility.

        Uses the formula: Expected Move = Price * IV * sqrt(Days/365)

        Args:
            underlying_price: Current price of the underlying
            iv: Implied volatility as decimal (e.g., 0.20 for 20%)
            days: Number of days for the calculation

        Returns:
            float: Expected move in dollars
        """
        import math
        expected_move = underlying_price * iv * math.sqrt(days / 365)
        return expected_move
