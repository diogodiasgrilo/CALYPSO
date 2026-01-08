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

        # Authentication state
        self.access_token = self.saxo_config.get("access_token")
        self.refresh_token = self.saxo_config.get("refresh_token")
        self.token_expiry = None

        # Determine environment (simulation or live)
        self.environment = self.saxo_config.get("environment", "sim")
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

        # Circuit breaker for error handling
        self.circuit_breaker = CircuitBreakerState()

        # WebSocket connection state
        self.ws_connection: Optional[websocket.WebSocketApp] = None
        self.ws_thread: Optional[threading.Thread] = None
        self.price_callbacks: Dict[int, Callable] = {}  # UIC -> callback mapping
        self.subscription_context_id = f"ctx_{int(time.time())}"
        self.is_streaming = False

        # Account information
        self.account_key = config["account"].get("account_key")
        self.client_key = config["account"].get("client_key")

        logger.info(f"SaxoClient initialized in {self.environment} environment")

    # =========================================================================
    # AUTHENTICATION METHODS
    # =========================================================================

    def authenticate(self) -> bool:
        """
        Perform OAuth2 authentication with Saxo Bank.

        This method checks for existing valid tokens first, then attempts
        to refresh if needed, or initiates a new OAuth flow if required.

        Returns:
            bool: True if authentication successful, False otherwise.
        """
        logger.info("Starting authentication process...")

        # Check if we have a valid access token
        if self.access_token and self._is_token_valid():
            logger.info("Using existing valid access token")
            return True

        # Try to refresh the token if we have a refresh token
        if self.refresh_token:
            logger.info("Attempting to refresh access token...")
            if self._refresh_access_token():
                return True
            logger.warning("Token refresh failed, initiating new OAuth flow")

        # Initiate new OAuth2 authorization flow
        return self._oauth_authorization_flow()

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
                "client_id": self.saxo_config["app_key"],
                "response_type": "code",
                "redirect_uri": self.saxo_config["redirect_uri"],
                "state": f"state_{int(time.time())}",  # CSRF protection
            }
            auth_url = f"{self.saxo_config['auth_url']}?{urlencode(auth_params)}"

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
                "client_id": self.saxo_config["app_key"],
                "client_secret": self.saxo_config["app_secret"],
            }

            response = requests.post(
                self.saxo_config["token_url"],
                data=token_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )

            if response.status_code == 200:
                token_response = response.json()
                self.access_token = token_response["access_token"]
                self.refresh_token = token_response.get("refresh_token")

                # Calculate token expiry time
                expires_in = token_response.get("expires_in", 1200)  # Default 20 minutes
                self.token_expiry = datetime.now() + timedelta(seconds=expires_in - 60)

                logger.info("Access token obtained successfully")
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
                "client_id": self.saxo_config["app_key"],
                "client_secret": self.saxo_config["app_secret"],
            }

            response = requests.post(
                self.saxo_config["token_url"],
                data=refresh_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )

            if response.status_code == 200:
                token_response = response.json()
                self.access_token = token_response["access_token"]
                self.refresh_token = token_response.get("refresh_token", self.refresh_token)

                expires_in = token_response.get("expires_in", 1200)
                self.token_expiry = datetime.now() + timedelta(seconds=expires_in - 60)

                logger.info("Access token refreshed successfully")
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

            if response.status_code in [200, 201]:
                self._record_success()
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

        Args:
            uic: Unique Instrument Code
            asset_type: Type of asset (Stock, StockOption, etc.)

        Returns:
            dict: Quote data including Bid, Ask, LastTraded prices.
        """
        endpoint = "/trade/v1/infoprices"
        params = {
            "Uic": uic,
            "AssetType": asset_type,
            "FieldGroups": "Quote,PriceInfoDetails"
        }

        response = self._make_request("GET", endpoint, params=params)
        if response:
            logger.debug(f"Got quote for UIC {uic}: {response}")
            return response
        return None

    def get_option_chain(
        self,
        underlying_uic: int,
        expiry_date: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Get option chain for an underlying instrument.

        Args:
            underlying_uic: UIC of the underlying instrument
            expiry_date: Optional specific expiry date (YYYY-MM-DD)

        Returns:
            dict: Option chain data with calls and puts.
        """
        endpoint = "/ref/v1/instruments/contractoptionspaces"
        params = {
            "UnderlyingUic": underlying_uic,
            "AssetType": "StockOption"
        }

        response = self._make_request("GET", endpoint, params=params)
        if response:
            logger.debug(f"Got option chain for underlying UIC {underlying_uic}")
            return response
        return None

    def get_option_expirations(self, underlying_uic: int) -> Optional[List[Dict]]:
        """
        Get available option expiration dates for an underlying.

        Args:
            underlying_uic: UIC of the underlying instrument

        Returns:
            list: List of expiration dates with associated data.
        """
        option_chain = self.get_option_chain(underlying_uic)
        if option_chain and "Data" in option_chain:
            return option_chain["Data"]
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

        # Get strikes for this expiration
        strikes = target_expiration.get("Strikes", [])

        # Find ATM strike (closest to current price)
        atm_strike = None
        min_diff = float('inf')

        for strike_data in strikes:
            strike_price = strike_data.get("Strike", 0)
            diff = abs(strike_price - underlying_price)
            if diff < min_diff:
                min_diff = diff
                atm_strike = strike_data

        if not atm_strike:
            logger.error("Failed to find ATM strike")
            return None

        logger.info(f"ATM strike: {atm_strike.get('Strike')} (underlying: {underlying_price})")

        return {
            "call": {
                "uic": atm_strike.get("CallUic"),
                "strike": atm_strike.get("Strike"),
                "expiry": target_expiration.get("Expiry"),
                "option_type": "Call"
            },
            "put": {
                "uic": atm_strike.get("PutUic"),
                "strike": atm_strike.get("Strike"),
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

        strikes = target_expiration.get("Strikes", [])

        # Calculate target strikes for strangle
        move_distance = expected_move * multiplier
        call_target = underlying_price + move_distance
        put_target = underlying_price - move_distance

        # Find closest strikes to targets
        call_strike = None
        put_strike = None
        min_call_diff = float('inf')
        min_put_diff = float('inf')

        for strike_data in strikes:
            strike_price = strike_data.get("Strike", 0)

            # For call, find strike closest to and above target
            if strike_price >= underlying_price:
                diff = abs(strike_price - call_target)
                if diff < min_call_diff:
                    min_call_diff = diff
                    call_strike = strike_data

            # For put, find strike closest to and below target
            if strike_price <= underlying_price:
                diff = abs(strike_price - put_target)
                if diff < min_put_diff:
                    min_put_diff = diff
                    put_strike = strike_data

        if not call_strike or not put_strike:
            logger.error("Failed to find strangle strikes")
            return None

        logger.info(
            f"Strangle strikes: Put {put_strike.get('Strike')} / "
            f"Call {call_strike.get('Strike')} (underlying: {underlying_price})"
        )

        return {
            "call": {
                "uic": call_strike.get("CallUic"),
                "strike": call_strike.get("Strike"),
                "expiry": target_expiration.get("Expiry"),
                "option_type": "Call"
            },
            "put": {
                "uic": put_strike.get("PutUic"),
                "strike": put_strike.get("Strike"),
                "expiry": target_expiration.get("Expiry"),
                "option_type": "Put"
            }
        }

    def get_vix_price(self, vix_uic: int) -> Optional[float]:
        """
        Get current VIX price.

        Args:
            vix_uic: UIC for VIX instrument

        Returns:
            float: Current VIX value.
        """
        quote = self.get_quote(vix_uic, asset_type="CfdOnIndex")
        if quote and "Quote" in quote:
            return quote["Quote"].get("Mid") or quote["Quote"].get("LastTraded")
        return None

    def check_bid_ask_spread(
        self,
        uic: int,
        asset_type: str = "StockOption",
        max_spread_percent: float = 0.5
    ) -> tuple[bool, float]:
        """
        Check if bid-ask spread is within acceptable threshold.

        Args:
            uic: Instrument UIC
            asset_type: Type of asset
            max_spread_percent: Maximum acceptable spread as percentage

        Returns:
            tuple: (is_acceptable, spread_percent)
        """
        quote = self.get_quote(uic, asset_type)
        if not quote or "Quote" not in quote:
            return False, 0.0

        bid = quote["Quote"].get("Bid", 0)
        ask = quote["Quote"].get("Ask", 0)

        if bid <= 0 or ask <= 0:
            return False, 0.0

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

    def get_positions(self) -> Optional[List[Dict]]:
        """
        Get all current positions for the account.

        Returns:
            list: List of position dictionaries.
        """
        endpoint = f"/port/v1/positions"
        params = {
            "ClientKey": self.client_key,
            "FieldGroups": "DisplayAndFormat,PositionBase,PositionView"
        }

        response = self._make_request("GET", endpoint, params=params)
        if response and "Data" in response:
            return response["Data"]
        return []

    def get_open_orders(self) -> Optional[List[Dict]]:
        """
        Get all open orders for the account.

        Returns:
            list: List of open order dictionaries.
        """
        endpoint = f"/port/v1/orders"
        params = {"ClientKey": self.client_key}

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
    # WEBSOCKET STREAMING METHODS
    # =========================================================================

    def start_price_streaming(
        self,
        uics: List[int],
        callback: Callable[[int, Dict], None],
        asset_type: str = "Stock"
    ) -> bool:
        """
        Start WebSocket streaming for real-time price updates.

        Args:
            uics: List of instrument UICs to subscribe to
            callback: Function to call with price updates (uic, data)
            asset_type: Type of asset

        Returns:
            bool: True if streaming started successfully.
        """
        if self.is_streaming:
            logger.warning("Streaming already active")
            return True

        # Store callbacks for each UIC
        for uic in uics:
            self.price_callbacks[uic] = callback

        # Create subscription request
        subscription_request = {
            "ContextId": self.subscription_context_id,
            "ReferenceId": f"prices_{int(time.time())}",
            "Arguments": {
                "Uics": ",".join(map(str, uics)),
                "AssetType": asset_type,
                "FieldGroups": ["Quote", "PriceInfo"]
            }
        }

        # First, create the subscription via REST
        endpoint = "/trade/v1/prices/subscriptions"
        response = self._make_request("POST", endpoint, data=subscription_request)

        if not response:
            logger.error("Failed to create price subscription")
            return False

        logger.info(f"Price subscription created: {response}")

        # Start WebSocket connection
        self._start_websocket()

        return True

    def _start_websocket(self):
        """Initialize and start the WebSocket connection."""
        def on_message(ws, message):
            """Handle incoming WebSocket messages."""
            try:
                data = json.loads(message)
                self._handle_streaming_message(data)
                self._record_success()
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse WebSocket message: {e}")

        def on_error(ws, error):
            """Handle WebSocket errors."""
            logger.error(f"WebSocket error: {error}")
            self._record_error()

        def on_close(ws, close_status_code, close_msg):
            """Handle WebSocket connection close."""
            logger.warning(f"WebSocket closed: {close_status_code} - {close_msg}")
            self.is_streaming = False

        def on_open(ws):
            """Handle WebSocket connection open."""
            logger.info("WebSocket connection established")
            self.is_streaming = True
            self._record_success()

        # Build WebSocket URL with authentication
        ws_url = f"{self.streaming_url}?contextId={self.subscription_context_id}"

        self.ws_connection = websocket.WebSocketApp(
            ws_url,
            header={"Authorization": f"Bearer {self.access_token}"},
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open
        )

        # Run WebSocket in a separate thread
        self.ws_thread = threading.Thread(
            target=self.ws_connection.run_forever,
            kwargs={"ping_interval": 30, "ping_timeout": 10}
        )
        self.ws_thread.daemon = True
        self.ws_thread.start()

        logger.info("WebSocket thread started")

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
                if uic and uic in self.price_callbacks:
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

    def get_account_info(self) -> Optional[Dict]:
        """
        Get account information.

        Returns:
            dict: Account details including balance.
        """
        endpoint = f"/port/v1/accounts/{self.account_key}"
        return self._make_request("GET", endpoint)

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
