"""
saxo_client.py - Saxo Bank OpenAPI Client Module

This module handles all interactions with the Saxo Bank OpenAPI including:
- OAuth2 authentication flow
- REST API calls for trading operations
- WebSocket streaming for real-time price data
- Circuit breaker pattern for error handling
- Token refresh on 401 errors (CONN-004)
- Rate limiting with exponential backoff on 429 errors (CONN-006)
- Multi-bot token coordination via TokenCoordinator

Author: Trading Bot Developer
Date: 2024
Last Updated: 2026-01-22
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
from shared.external_price_feed import ExternalPriceFeed

# Import token coordinator for multi-bot environments
from shared.token_coordinator import get_token_coordinator, TokenCoordinator

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

        # Heartbeat tracking for debugging
        self._heartbeat_count = 0

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

        # Token coordinator for multi-bot environments
        # Prevents race conditions when multiple bots share the same refresh token
        self.token_coordinator = get_token_coordinator()

        # CONN-006: Rate limiting state for exponential backoff
        self._rate_limit_backoff_until: Optional[datetime] = None
        self._rate_limit_retry_count: int = 0
        self._rate_limit_max_retries: int = 5
        self._rate_limit_base_delay: float = 1.0  # Start with 1 second

        # Update coordinator cache with current tokens if we have them
        if self.access_token and self.refresh_token:
            self.token_coordinator.update_cache({
                'access_token': self.access_token,
                'refresh_token': self.refresh_token,
                'token_expiry': self.token_expiry.isoformat() if self.token_expiry else None,
                'app_key': self.app_key,
                'app_secret': self.app_secret,
            })

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

    def authenticate(self, force_refresh: bool = False) -> bool:
        """
        Perform OAuth2 authentication with Saxo Bank.

        This method checks for existing valid tokens first, then attempts
        to refresh if needed, or initiates a new OAuth flow if required.

        Uses TokenCoordinator to prevent race conditions in multi-bot environments
        where multiple processes share the same refresh token.

        After successful authentication, it upgrades the session to
        FullTradingAndChat for real-time market data access.

        Args:
            force_refresh: If True, refresh the token even if current one is valid.
                          Use this before long sleep periods to ensure fresh tokens.

        Returns:
            bool: True if authentication successful, False otherwise.
        """
        logger.info("Starting authentication process...")

        # MULTI-BOT COORDINATION: Check coordinator cache first
        # Another bot may have refreshed tokens since we started
        cached_tokens = self.token_coordinator.get_cached_tokens()
        if cached_tokens and not force_refresh:
            # Check if cached tokens are newer than ours
            cached_expiry_str = cached_tokens.get('token_expiry')
            if cached_expiry_str:
                try:
                    cached_expiry = datetime.fromisoformat(cached_expiry_str.replace('Z', '+00:00'))
                    # If cached tokens are valid and newer, use them
                    if self.token_coordinator.is_token_valid(cached_tokens):
                        if not self.token_expiry or cached_expiry > self.token_expiry:
                            logger.info("Using fresher tokens from coordinator cache")
                            self._apply_tokens_from_cache(cached_tokens)
                            self._upgrade_session_for_realtime_data()
                            return True
                except (ValueError, TypeError):
                    pass

        # Check if we have a valid access token (unless force_refresh is requested)
        if self.access_token and self._is_token_valid() and not force_refresh:
            logger.info("Using existing valid access token")
            # Upgrade session for real-time data
            self._upgrade_session_for_realtime_data()
            return True

        # Try to refresh the token using coordinator (with lock to prevent race conditions)
        if self.refresh_token:
            logger.info("Attempting to refresh access token...")
            if self._coordinated_token_refresh():
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

    def _apply_tokens_from_cache(self, cached_tokens: Dict[str, Any]):
        """Apply tokens from coordinator cache to this client instance."""
        self.access_token = cached_tokens.get('access_token')
        self.refresh_token = cached_tokens.get('refresh_token')
        expiry_str = cached_tokens.get('token_expiry')
        if expiry_str:
            try:
                self.token_expiry = datetime.fromisoformat(expiry_str.replace('Z', '+00:00'))
            except (ValueError, TypeError):
                self.token_expiry = None

    def _coordinated_token_refresh(self) -> bool:
        """
        Refresh tokens using coordinator to prevent race conditions.

        This wraps the actual refresh in a file lock to ensure only one
        bot process refreshes at a time. Other processes will pick up
        the refreshed tokens from the cache.

        Returns:
            bool: True if refresh successful (by us or another process), False otherwise.
        """
        def do_refresh() -> Optional[Dict[str, Any]]:
            """Actual refresh logic wrapped for coordinator."""
            if self._refresh_access_token_internal():
                return {
                    'access_token': self.access_token,
                    'refresh_token': self.refresh_token,
                    'token_expiry': self.token_expiry.isoformat() if self.token_expiry else None,
                    'app_key': self.app_key,
                    'app_secret': self.app_secret,
                }
            return None

        def save_to_secret_manager(tokens: Dict[str, Any]) -> bool:
            """Save tokens to Secret Manager after refresh."""
            self._save_tokens_to_config()
            return True

        # Use coordinator with lock
        new_tokens = self.token_coordinator.refresh_with_lock(
            do_refresh,
            save_to_secret_manager
        )

        if new_tokens:
            # Apply tokens (either from our refresh or from cache if another process refreshed)
            self._apply_tokens_from_cache(new_tokens)
            return True

        return False

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
        Refresh the access token using coordinated approach.

        This is the public method that uses TokenCoordinator to prevent
        race conditions in multi-bot environments.

        Returns:
            bool: True if refresh successful, False otherwise.
        """
        return self._coordinated_token_refresh()

    def _refresh_access_token_internal(self) -> bool:
        """
        Internal token refresh - performs actual API call.

        Called by the coordinator with lock held. Do NOT call directly
        from outside - use _refresh_access_token() or _coordinated_token_refresh().

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

                logger.info(f"Access token refreshed successfully (expires in {expires_in//60} min)")

                # Note: Do NOT call _save_tokens_to_config() here
                # The coordinator handles saving after successful refresh

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
            from shared.config_loader import get_config_loader
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

        # CONN-006: Check rate limit backoff
        if self._rate_limit_backoff_until:
            if datetime.now() < self._rate_limit_backoff_until:
                wait_seconds = (self._rate_limit_backoff_until - datetime.now()).total_seconds()
                logger.warning(f"CONN-006: Rate limit backoff active, waiting {wait_seconds:.1f}s")
                time.sleep(wait_seconds)
            # Clear backoff after waiting
            self._rate_limit_backoff_until = None

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
                # Reset rate limit retry count on success
                self._rate_limit_retry_count = 0
                # 202 might not have body, safe parsing
                return response.json() if response.text else {}
            elif response.status_code == 204:
                self._record_success()
                self._rate_limit_retry_count = 0
                return {}
            elif response.status_code == 429:
                # CONN-006: Rate limiting - exponential backoff
                self._rate_limit_retry_count += 1
                if self._rate_limit_retry_count <= self._rate_limit_max_retries:
                    # Exponential backoff: 1s, 2s, 4s, 8s, 16s
                    delay = self._rate_limit_base_delay * (2 ** (self._rate_limit_retry_count - 1))
                    # Check for Retry-After header
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            delay = max(delay, float(retry_after))
                        except ValueError:
                            pass
                    logger.warning(f"CONN-006: Rate limited (429). Retry {self._rate_limit_retry_count}/{self._rate_limit_max_retries}, waiting {delay:.1f}s")
                    self._rate_limit_backoff_until = datetime.now() + timedelta(seconds=delay)
                    # Retry the request after backoff
                    time.sleep(delay)
                    return self._make_request(method, endpoint, params, data)
                else:
                    logger.error(f"CONN-006: Rate limit retries exhausted ({self._rate_limit_max_retries})")
                    self._record_error()
                    return None
            elif response.status_code == 401:
                # CONN-004: Token expired mid-request - try refreshing
                logger.warning("CONN-004: 401 Unauthorized - attempting token refresh")
                if self.authenticate(force_refresh=True):
                    logger.info("CONN-004: Token refreshed, retrying request")
                    return self._make_request(method, endpoint, params, data)
                else:
                    logger.error("CONN-004: Token refresh failed")
                    self._record_error()
                    return None
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

    def get_quote(self, uic: int, asset_type: str = "Stock", skip_cache: bool = False) -> Optional[Dict]:
        """
        Get current quote for an instrument.

        First checks the streaming price cache for real-time data, then
        falls back to /trade/v1/infoprices/list endpoint.

        Args:
            uic: Unique Instrument Code
            asset_type: Type of asset (Stock, StockOption, Etf, StockIndex, FxSpot, etc.)
            skip_cache: If True, bypass streaming cache and always use REST API

        Returns:
            dict: Quote data including Bid, Ask, LastTraded prices.
        """
        # First check streaming cache for real-time data (unless skip_cache is True)
        uic_int = int(uic)
        if not skip_cache and uic_int in self._price_cache:
            cached = self._price_cache[uic_int]
            if cached and "Quote" in cached:
                bid = cached["Quote"].get("Bid", 0)
                ask = cached["Quote"].get("Ask", 0)
                mid = cached["Quote"].get("Mid", 0)
                # Accept cache if we have Bid/Ask OR just Mid (for CFDs/indices like US500.I)
                if (bid > 0 and ask > 0) or mid > 0:
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

    def get_option_greeks(self, uic: int) -> Optional[Dict]:
        """
        Get Greeks (Delta, Gamma, Theta, Vega) for an option.

        Args:
            uic: Unique Instrument Code for the option

        Returns:
            dict: Greeks data including Theta, or None if not available.
                  Example: {"Delta": 0.5, "Gamma": 0.02, "Theta": -0.15, "Vega": 0.25}
        """
        endpoint = "/trade/v1/infoprices"
        params = {
            "AccountKey": self.account_key,
            "Uic": uic,
            "AssetType": "StockOption",
            "FieldGroups": "Quote,Greeks"
        }

        response = self._make_request("GET", endpoint, params=params)
        if response:
            greeks = response.get("Greeks", {})
            if greeks:
                logger.debug(
                    f"Greeks for UIC {uic}: Delta={greeks.get('Delta', 'N/A')}, "
                    f"Theta={greeks.get('Theta', 'N/A')}"
                )
                return greeks
            else:
                logger.warning(f"No Greeks returned for option UIC {uic}")
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

    def get_option_expirations(self, underlying_uic: int, option_root_uic: int = None) -> Optional[List[Dict]]:
        """
        Get available option expiration dates for an underlying.

        For StockOptions (e.g., SPY): Uses underlying_uic to find OptionRootId
        For StockIndexOptions (e.g., SPXW): Use option_root_uic directly (the UIC IS the OptionRootId)

        Args:
            underlying_uic: UIC of the underlying instrument
            option_root_uic: Optional UIC of the option root (for StockIndexOptions like SPXW)

        Returns:
            list: OptionSpace array with expiry information and strikes
        """
        # For StockIndexOptions, try using the option_root_uic directly first
        if option_root_uic:
            option_chain = self.get_option_chain(option_root_uic)
            if option_chain and "OptionSpace" in option_chain:
                logger.info(f"Got option chain directly for OptionRootUIC {option_root_uic}")
                return option_chain["OptionSpace"]

        # Step 1: Get OptionRootId from underlying
        option_root_id = self.get_option_root_id(underlying_uic)
        if not option_root_id:
            # For StockIndexOptions, try the underlying_uic directly as it may BE the OptionRootId
            logger.info(f"Trying underlying_uic {underlying_uic} directly as OptionRootId (StockIndexOption)")
            option_chain = self.get_option_chain(underlying_uic)
            if option_chain and "OptionSpace" in option_chain:
                return option_chain["OptionSpace"]
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
        target_dte_min: int = None,
        target_dte_max: int = None,
        target_dte: int = None
    ) -> Optional[Dict[str, Dict]]:
        """
        Find ATM (At-The-Money) call and put options.

        Args:
            underlying_uic: UIC of the underlying instrument
            underlying_price: Current price of the underlying
            target_dte_min: Minimum days to expiration (range mode)
            target_dte_max: Maximum days to expiration (range mode)
            target_dte: Target DTE to find closest expiration to (closest mode)
                        If provided, ignores min/max and finds closest available expiration

        Returns:
            dict: Dictionary with 'call' and 'put' option data.
        """
        expirations = self.get_option_expirations(underlying_uic)
        if not expirations:
            logger.error("Failed to get option expirations")
            return None

        today = datetime.now().date()
        target_expiration = None

        if target_dte is not None:
            # Closest mode: find expiration closest to target_dte
            closest_diff = float('inf')
            selected_dte = None
            selected_date = None

            for exp_data in expirations:
                exp_date_str = exp_data.get("Expiry")
                if not exp_date_str:
                    continue

                exp_date = datetime.strptime(exp_date_str[:10], "%Y-%m-%d").date()
                dte = (exp_date - today).days
                diff = abs(dte - target_dte)

                if diff < closest_diff:
                    closest_diff = diff
                    target_expiration = exp_data
                    selected_dte = dte
                    selected_date = exp_date_str

            if target_expiration:
                logger.info(f"Found closest expiration to {target_dte} DTE: {selected_date} with {selected_dte} DTE (diff: {closest_diff} days)")
            else:
                logger.warning(f"No expiration found close to {target_dte} DTE")
                return None
        else:
            # Range mode: find first expiration within target DTE range
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
        weekly: bool = True,
        for_roll: bool = False
    ) -> Optional[Dict[str, Dict]]:
        """
        Find OTM options for a short strangle at specified distance.

        Args:
            underlying_uic: UIC of the underlying
            underlying_price: Current underlying price
            expected_move: Expected weekly move in dollars
            multiplier: Multiplier for the expected move (1.5-2.0x)
            weekly: If True, find weekly options
            for_roll: If True, find next week's expiry (5-12 DTE) for rolling.
                     If False, find current week's expiry (0-7 DTE) for initial entry.

        Returns:
            dict: Dictionary with 'call' and 'put' option data for strangle.
        """
        expirations = self.get_option_expirations(underlying_uic)
        if not expirations:
            return None

        # For weekly, find appropriate Friday expiration based on context
        today = datetime.now().date()
        target_expiration = None

        if weekly:
            # When rolling: find NEXT Friday (5-12 DTE) per Brian Terry's strategy
            # When entering fresh: find CURRENT Friday (0-7 DTE)
            if for_roll:
                # Rolling shorts: look for next week's expiry (5-12 DTE)
                for exp_data in expirations:
                    exp_date_str = exp_data.get("Expiry")
                    if not exp_date_str:
                        continue
                    exp_date = datetime.strptime(exp_date_str[:10], "%Y-%m-%d").date()
                    dte = (exp_date - today).days

                    if 5 <= dte <= 12:
                        target_expiration = exp_data
                        logger.info(f"Found next weekly expiration for roll: {exp_date_str} with {dte} DTE")
                        break
            else:
                # Initial entry: find nearest Friday within 7 days
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

    def find_iron_fly_options(
        self,
        underlying_uic: int,
        atm_strike: float,
        upper_wing_strike: float,
        lower_wing_strike: float,
        target_dte_min: int = 0,
        target_dte_max: int = 1,
        option_root_uic: int = None
    ) -> Optional[Dict[str, Dict]]:
        """
        Find all 4 options for an Iron Fly (Iron Butterfly) position.

        Iron Fly Structure:
        - Short ATM Call (sell)
        - Short ATM Put (sell)
        - Long OTM Call at upper wing (buy for protection)
        - Long OTM Put at lower wing (buy for protection)

        Args:
            underlying_uic: UIC of the underlying instrument
            atm_strike: ATM strike price for short straddle (call + put at same strike)
            upper_wing_strike: Strike for long call (above ATM)
            lower_wing_strike: Strike for long put (below ATM)
            target_dte_min: Minimum days to expiration (0 for 0DTE)
            target_dte_max: Maximum days to expiration (1 for 0DTE)
            option_root_uic: Optional UIC of the option root (for StockIndexOptions like SPXW)

        Returns:
            dict: Dictionary with 'short_call', 'short_put', 'long_call', 'long_put' option data,
                  or None if any leg cannot be found.
        """
        expirations = self.get_option_expirations(underlying_uic, option_root_uic=option_root_uic)
        if not expirations:
            logger.error("Failed to get option expirations for iron fly")
            return None

        # Find appropriate expiration (0DTE for iron fly)
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
                logger.info(f"Found 0DTE expiration for iron fly: {exp_date_str} with {dte} DTE")
                break

        if not target_expiration:
            logger.warning(f"No expiration found within {target_dte_min}-{target_dte_max} DTE range for iron fly")
            return None

        # Get all options for this expiration
        specific_options = target_expiration.get("SpecificOptions", [])

        if not specific_options:
            logger.error("No SpecificOptions in target expiration for iron fly")
            return None

        # Find UICs for all 4 legs
        short_call_uic = None  # ATM Call (sell)
        short_put_uic = None   # ATM Put (sell)
        long_call_uic = None   # OTM Call at upper wing (buy)
        long_put_uic = None    # OTM Put at lower wing (buy)

        # Build strike -> options mapping for efficient lookup
        calls_by_strike = {}
        puts_by_strike = {}

        for option in specific_options:
            strike = option.get("StrikePrice", 0)
            uic = option.get("Uic")
            put_call = option.get("PutCall")

            if put_call == "Call":
                calls_by_strike[strike] = uic
            elif put_call == "Put":
                puts_by_strike[strike] = uic

        # Find ATM options (short straddle at same strike)
        if atm_strike in calls_by_strike:
            short_call_uic = calls_by_strike[atm_strike]
        else:
            # Find closest call strike to ATM
            closest_strike = min(calls_by_strike.keys(), key=lambda x: abs(x - atm_strike), default=None)
            if closest_strike:
                short_call_uic = calls_by_strike[closest_strike]
                logger.warning(f"ATM call strike {atm_strike} not found, using closest: {closest_strike}")
                atm_strike = closest_strike  # Update for consistency

        if atm_strike in puts_by_strike:
            short_put_uic = puts_by_strike[atm_strike]
        else:
            # Find closest put strike to ATM
            closest_strike = min(puts_by_strike.keys(), key=lambda x: abs(x - atm_strike), default=None)
            if closest_strike:
                short_put_uic = puts_by_strike[closest_strike]
                logger.warning(f"ATM put strike {atm_strike} not found, using closest: {closest_strike}")

        # Find wing options (long positions for protection)
        if upper_wing_strike in calls_by_strike:
            long_call_uic = calls_by_strike[upper_wing_strike]
        else:
            # Find closest call strike to upper wing
            closest_strike = min(calls_by_strike.keys(), key=lambda x: abs(x - upper_wing_strike), default=None)
            if closest_strike:
                long_call_uic = calls_by_strike[closest_strike]
                logger.warning(f"Upper wing strike {upper_wing_strike} not found, using closest: {closest_strike}")
                upper_wing_strike = closest_strike

        if lower_wing_strike in puts_by_strike:
            long_put_uic = puts_by_strike[lower_wing_strike]
        else:
            # Find closest put strike to lower wing
            closest_strike = min(puts_by_strike.keys(), key=lambda x: abs(x - lower_wing_strike), default=None)
            if closest_strike:
                long_put_uic = puts_by_strike[closest_strike]
                logger.warning(f"Lower wing strike {lower_wing_strike} not found, using closest: {closest_strike}")
                lower_wing_strike = closest_strike

        # Verify all legs found
        if not all([short_call_uic, short_put_uic, long_call_uic, long_put_uic]):
            logger.error(
                f"Failed to find all iron fly legs. "
                f"Short Call: {short_call_uic}, Short Put: {short_put_uic}, "
                f"Long Call: {long_call_uic}, Long Put: {long_put_uic}"
            )
            return None

        expiry_str = target_expiration.get("Expiry")

        logger.info(
            f"Iron fly options found: ATM={atm_strike}, Upper={upper_wing_strike}, Lower={lower_wing_strike}, "
            f"Expiry={expiry_str}"
        )

        return {
            "short_call": {
                "uic": short_call_uic,
                "strike": atm_strike,
                "expiry": expiry_str,
                "option_type": "Call",
                "position_type": "short"
            },
            "short_put": {
                "uic": short_put_uic,
                "strike": atm_strike,
                "expiry": expiry_str,
                "option_type": "Put",
                "position_type": "short"
            },
            "long_call": {
                "uic": long_call_uic,
                "strike": upper_wing_strike,
                "expiry": expiry_str,
                "option_type": "Call",
                "position_type": "long"
            },
            "long_put": {
                "uic": long_put_uic,
                "strike": lower_wing_strike,
                "expiry": expiry_str,
                "option_type": "Put",
                "position_type": "long"
            },
            "expiry": expiry_str,
            "atm_strike": atm_strike,
            "upper_wing": upper_wing_strike,
            "lower_wing": lower_wing_strike
        }

    def find_strangle_by_target_premium(
        self,
        underlying_uic: int,
        underlying_price: float,
        target_premium: float,
        weekly: bool = True,
        for_roll: bool = False
    ) -> Optional[Dict[str, Dict]]:
        """
        Find OTM options for a short strangle that meets target premium.

        Instead of using expected move * multiplier, this finds the furthest OTM
        strikes that still provide the target premium.

        Args:
            underlying_uic: UIC of the underlying
            underlying_price: Current underlying price
            target_premium: Target premium in dollars (total for both legs, per contract)
            weekly: If True, find weekly options
            for_roll: If True, find next week's expiry (5-12 DTE) for rolling.
                     If False, find current week's expiry (0-7 DTE) for initial entry.

        Returns:
            dict: Dictionary with 'call' and 'put' option data for strangle,
                  or None if no valid combination found.
        """
        import time

        expirations = self.get_option_expirations(underlying_uic)
        if not expirations:
            return None

        # Find weekly expiration based on context (rolling vs initial entry)
        today = datetime.now().date()
        target_expiration = None
        weekly_dte = 7

        if weekly:
            if for_roll:
                # Rolling shorts: look for next week's expiry (5-12 DTE)
                for exp_data in expirations:
                    exp_date_str = exp_data.get("Expiry")
                    if not exp_date_str:
                        continue
                    exp_date = datetime.strptime(exp_date_str[:10], "%Y-%m-%d").date()
                    dte = (exp_date - today).days

                    if 5 <= dte <= 12:
                        target_expiration = exp_data
                        weekly_dte = dte
                        logger.info(f"Found next weekly expiration for roll: {exp_date_str} with {dte} DTE")
                        break
            else:
                # Initial entry: find nearest Friday within 7 days
                for exp_data in expirations:
                    exp_date_str = exp_data.get("Expiry")
                    if not exp_date_str:
                        continue
                    exp_date = datetime.strptime(exp_date_str[:10], "%Y-%m-%d").date()
                    dte = (exp_date - today).days

                    if 0 < dte <= 7:
                        target_expiration = exp_data
                        weekly_dte = dte
                        logger.info(f"Found weekly expiration: {exp_date_str} with {dte} DTE")
                        break

        if not target_expiration:
            logger.warning("No suitable weekly expiration found")
            return None

        specific_options = target_expiration.get("SpecificOptions", [])
        if not specific_options:
            logger.error("No SpecificOptions in target expiration")
            return None

        # Filter to options within 5% of current price to reduce API calls
        min_strike = underlying_price * 0.95
        max_strike = underlying_price * 1.05

        # Collect calls and puts with their prices
        calls = []
        puts = []

        relevant_options = [
            opt for opt in specific_options
            if min_strike <= opt.get("StrikePrice", 0) <= max_strike
        ]

        logger.info(f"Fetching prices for {len(relevant_options)} options within 5% of underlying")

        for i, option in enumerate(relevant_options):
            strike = option.get("StrikePrice", 0)
            uic = option.get("Uic")
            put_call = option.get("PutCall")

            if not uic or not strike:
                continue

            # Rate limiting
            if i > 0 and i % 5 == 0:
                time.sleep(0.3)

            quote = self.get_quote(uic, "StockOption")
            if not quote:
                continue

            bid = quote["Quote"].get("Bid", 0)
            ask = quote["Quote"].get("Ask", 0)

            if bid <= 0:  # Skip options with no bid
                continue

            option_data = {
                "strike": strike,
                "uic": uic,
                "bid": bid,
                "ask": ask,
                "expiry": target_expiration.get("Expiry")
            }

            if put_call == "Call" and strike > underlying_price:
                calls.append(option_data)
            elif put_call == "Put" and strike < underlying_price:
                puts.append(option_data)

        if not calls or not puts:
            logger.error("No valid OTM options found with prices")
            return None

        # Sort: calls ascending (closest to ATM first), puts descending (closest to ATM first)
        calls.sort(key=lambda x: x["strike"])
        puts.sort(key=lambda x: x["strike"], reverse=True)

        # Find the furthest OTM combination that still meets target premium
        best_combination = None

        # Start from furthest OTM and move inward
        for call in reversed(calls):
            for put in reversed(puts):
                total_premium = (call["bid"] + put["bid"]) * 100

                if total_premium >= target_premium:
                    call_distance = call["strike"] - underlying_price
                    put_distance = underlying_price - put["strike"]
                    min_distance = min(call_distance, put_distance)

                    if best_combination is None or min_distance > best_combination["min_distance"]:
                        best_combination = {
                            "call": call,
                            "put": put,
                            "total_premium": total_premium,
                            "min_distance": min_distance
                        }

        if not best_combination:
            logger.warning(f"No strike combination meets target premium of ${target_premium:.2f}")
            # Show what's available
            if calls and puts:
                max_premium = (calls[0]["bid"] + puts[0]["bid"]) * 100
                logger.warning(f"Maximum available premium at tightest strikes: ${max_premium:.2f}")
                logger.warning(f"Tightest strikes: Put ${puts[0]['strike']:.0f} / Call ${calls[0]['strike']:.0f}")
            return None

        call = best_combination["call"]
        put = best_combination["put"]

        # Calculate distances for logging
        call_distance = call["strike"] - underlying_price
        put_distance = underlying_price - put["strike"]
        call_pct = (call_distance / underlying_price) * 100
        put_pct = (put_distance / underlying_price) * 100

        logger.info(f"Found strikes for target premium ${target_premium:.2f}:")
        logger.info(f"  Call: ${call['strike']:.0f} (+${call_distance:.2f}, {call_pct:.2f}% OTM) bid=${call['bid']:.2f}")
        logger.info(f"  Put:  ${put['strike']:.0f} (-${put_distance:.2f}, {put_pct:.2f}% OTM) bid=${put['bid']:.2f}")
        logger.info(f"  Total premium: ${best_combination['total_premium']:.2f}")

        return {
            "call": {
                "uic": call["uic"],
                "strike": call["strike"],
                "expiry": call["expiry"],
                "option_type": "Call",
                "bid": call["bid"],
                "ask": call["ask"]
            },
            "put": {
                "uic": put["uic"],
                "strike": put["strike"],
                "expiry": put["expiry"],
                "option_type": "Put",
                "bid": put["bid"],
                "ask": put["ask"]
            },
            "total_premium": best_combination["total_premium"],
            "target_premium": target_premium
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

        # Track which sources we tried for detailed error logging
        sources_tried = []

        # 1. Check the price cache (from subscription snapshots)
        if vix_uic in self._price_cache:
            cached_data = self._price_cache[vix_uic]
            price = self._extract_price_from_data(cached_data, "VIX cache")
            if price:
                return price
            sources_tried.append("cache(no valid price)")
        else:
            sources_tried.append("cache(not in cache)")

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
            # Log the actual data for debugging
            logger.debug(f"VIX REST response had no extractable price. Keys: {list(data.keys())}, PriceInfoDetails: {data.get('PriceInfoDetails')}")
            sources_tried.append("REST(no valid price in response)")
        else:
            sources_tried.append(f"REST(empty response: {response})" if response else "REST(request failed)")

        # 3. Last resort: Yahoo Finance fallback with retry
        if self.external_feed.enabled:
            logger.info(f"VIX: Saxo failed ({', '.join(sources_tried)}), using Yahoo fallback")
            for attempt in range(2):  # Try twice
                external_price = self.external_feed.get_vix_price()
                if external_price:
                    logger.info(f"VIX: Yahoo price {external_price}" + (f" (attempt {attempt + 1})" if attempt > 0 else ""))
                    return external_price
                if attempt == 0:
                    # Wait 1 second before retry
                    time.sleep(1)
                    logger.debug("VIX: Yahoo Finance retry after 1s delay")
            sources_tried.append("Yahoo(failed after 2 attempts)")
        else:
            sources_tried.append("Yahoo(disabled)")

        logger.error(f"VIX: All price sources failed for UIC {vix_uic} - tried: {', '.join(sources_tried)}")
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
        duration_type: str = "DayOrder",
        to_open_close: str = "ToOpen"
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
            to_open_close: ToOpen or ToClose (required for options)

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
            "ManualOrder": True,
            "ToOpenClose": to_open_close
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
                "Amount": leg["amount"],
                "ManualOrder": True  # Required for live trading
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

        # AccountKey is REQUIRED for order cancellation per Saxo API docs
        params = {"AccountKey": self.account_key}

        logger.info(f"Cancelling order: {order_id}")

        response = self._make_request("DELETE", endpoint, params=params)
        if response is not None:
            logger.info(f"Order {order_id} cancelled successfully")
            return response

        logger.error(f"Failed to cancel order {order_id}")
        return None

    # =========================================================================
    # EMERGENCY STOP-LOSS METHODS (BYPASS CIRCUIT BREAKER)
    # =========================================================================

    def place_emergency_order(
        self,
        uic: int,
        asset_type: str,
        buy_sell: BuySell,
        amount: int,
        order_type: OrderType = OrderType.MARKET,
        to_open_close: str = "ToClose",
        max_retries: int = 3
    ) -> Optional[Dict]:
        """
        Place an emergency stop-loss order, BYPASSING the circuit breaker.

        This method should ONLY be used for protective stop-loss orders that
        MUST be placed even when the circuit breaker is open due to API errors.

        SAFETY: This bypasses circuit breaker protection because a stop-loss
        order is more important than preventing additional API load during
        an outage. We'd rather risk API errors than leave a position unprotected.

        Args:
            uic: Instrument UIC
            asset_type: Type of asset
            buy_sell: Buy or Sell direction
            amount: Number of contracts/shares
            order_type: Type of order (default Market for stop-loss)
            to_open_close: ToOpen or ToClose (default ToClose for exits)
            max_retries: Number of retry attempts

        Returns:
            dict: Order response with OrderId if successful, None otherwise.
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
            "OrderDuration": {"DurationType": "DayOrder"},
            "ManualOrder": True,
            "ToOpenClose": to_open_close
        }

        logger.warning(
            f"EMERGENCY ORDER (bypassing circuit breaker): "
            f"{buy_sell.value} {amount} x UIC {uic}"
        )

        # Retry loop with exponential backoff
        for attempt in range(max_retries):
            try:
                # BYPASS circuit breaker - call API directly
                response = self._make_emergency_request("POST", endpoint, data=order_data)
                if response:
                    logger.info(f"EMERGENCY order placed successfully: {response.get('OrderId')}")
                    return response

                logger.warning(f"Emergency order attempt {attempt + 1}/{max_retries} failed")

            except Exception as e:
                logger.error(f"Emergency order attempt {attempt + 1}/{max_retries} error: {e}")

            # Exponential backoff: 1s, 2s, 4s
            if attempt < max_retries - 1:
                import time
                backoff = 2 ** attempt
                logger.info(f"Retrying emergency order in {backoff}s...")
                time.sleep(backoff)

        logger.critical(f"EMERGENCY ORDER FAILED after {max_retries} attempts!")
        return None

    def _make_emergency_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        data: Optional[Dict] = None
    ) -> Optional[Dict]:
        """
        Make an API request WITHOUT circuit breaker protection.

        This is used only for emergency stop-loss orders that must go through
        even when the circuit breaker is open.
        """
        import requests

        # Ensure token is valid (but don't fail if auth fails)
        if not self._is_token_valid():
            self.authenticate()

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

            if response.status_code in [200, 201, 202]:
                return response.json() if response.text else {}
            elif response.status_code == 204:
                return {}
            else:
                logger.error(f"Emergency request failed: {response.status_code} - {response.text}")
                return None

        except requests.exceptions.Timeout:
            logger.error(f"Emergency request timeout for {endpoint}")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Emergency request error for {endpoint}: {e}")
            return None

    def place_order_with_retry(
        self,
        uic: int,
        asset_type: str,
        buy_sell: BuySell,
        amount: int,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Optional[float] = None,
        duration_type: str = "DayOrder",
        to_open_close: str = "ToOpen",
        max_retries: int = 3
    ) -> Optional[Dict]:
        """
        Place an order with automatic retry on failure.

        This provides resilience against transient network issues or
        temporary API unavailability.

        Args:
            uic: Instrument UIC
            asset_type: Type of asset
            buy_sell: Buy or Sell direction
            amount: Number of contracts/shares
            order_type: Type of order
            limit_price: Limit price for limit orders
            duration_type: Order duration
            to_open_close: ToOpen or ToClose
            max_retries: Maximum retry attempts

        Returns:
            dict: Order response with OrderId if successful, None otherwise.
        """
        for attempt in range(max_retries):
            result = self.place_order(
                uic=uic,
                asset_type=asset_type,
                buy_sell=buy_sell,
                amount=amount,
                order_type=order_type,
                limit_price=limit_price,
                duration_type=duration_type,
                to_open_close=to_open_close
            )

            if result:
                return result

            logger.warning(f"Order attempt {attempt + 1}/{max_retries} failed, retrying...")

            if attempt < max_retries - 1:
                import time
                backoff = 2 ** attempt  # 1s, 2s, 4s
                time.sleep(backoff)

        logger.error(f"Order failed after {max_retries} attempts")
        return None

    # =========================================================================
    # SLIPPAGE PROTECTION METHODS
    # =========================================================================

    def get_order_status(self, order_id: str) -> Optional[Dict]:
        """
        Get the current status of an order.

        Args:
            order_id: The order ID to check

        Returns:
            dict: Order details including status, or None if not found.
        """
        endpoint = f"/port/v1/orders/{self.client_key}/{order_id}"

        response = self._make_request("GET", endpoint)
        if response:
            return response

        return None

    def get_open_orders(self) -> List[Dict]:
        """
        Get all open orders for the account.

        Returns:
            list: List of open orders.
        """
        endpoint = f"/port/v1/orders/me"

        response = self._make_request("GET", endpoint)
        if response and "Data" in response:
            return response["Data"]

        return []

    def place_limit_order_with_timeout(
        self,
        uic: int,
        asset_type: str,
        buy_sell: BuySell,
        amount: int,
        limit_price: float,
        timeout_seconds: int = 60,
        to_open_close: str = "ToOpen"
    ) -> Dict:
        """
        Place a limit order and wait for fill with timeout.

        Per strategy spec: "Use Limit Orders only, and if a 'Recenter' or 'Roll'
        isn't filled within 60 seconds, it should alert rather than chasing the price."

        Args:
            uic: Instrument UIC
            asset_type: Type of asset
            buy_sell: Buy or Sell direction
            amount: Number of contracts
            limit_price: Limit price for the order
            timeout_seconds: Maximum time to wait for fill (default 60s)
            to_open_close: ToOpen or ToClose (required for options)

        Returns:
            dict: {
                "success": bool,
                "filled": bool,
                "order_id": str or None,
                "message": str,
                "fill_price": float or None
            }
        """
        import time

        logger.info(f"Placing LIMIT order with {timeout_seconds}s timeout")
        logger.info(f"  {buy_sell.value} {amount} x UIC {uic} @ ${limit_price:.2f} ({to_open_close})")

        # Place the limit order
        order_response = self.place_order(
            uic=uic,
            asset_type=asset_type,
            buy_sell=buy_sell,
            amount=amount,
            order_type=OrderType.LIMIT,
            limit_price=limit_price,
            duration_type="DayOrder",
            to_open_close=to_open_close
        )

        if not order_response or "OrderId" not in order_response:
            return {
                "success": False,
                "filled": False,
                "order_id": None,
                "message": "Failed to place limit order",
                "fill_price": None
            }

        order_id = order_response["OrderId"]
        logger.info(f"Limit order placed: {order_id}")

        # Poll for fill status
        start_time = time.time()
        check_interval = 2  # Check every 2 seconds

        while (time.time() - start_time) < timeout_seconds:
            # Check order status
            open_orders = self.get_open_orders()
            order_still_open = any(o.get("OrderId") == order_id for o in open_orders)

            if not order_still_open:
                # Order is no longer open - assume filled
                elapsed = time.time() - start_time
                logger.info(f"✓ Limit order filled in {elapsed:.1f}s")
                return {
                    "success": True,
                    "filled": True,
                    "order_id": order_id,
                    "message": f"Order filled in {elapsed:.1f}s",
                    "fill_price": limit_price
                }

            time.sleep(check_interval)

        # Timeout reached - order not filled
        elapsed = time.time() - start_time
        logger.warning(f"⚠ Limit order NOT filled after {elapsed:.1f}s - cancelling")

        # Try to cancel the unfilled order with retry logic
        cancel_success = False
        for attempt in range(3):
            cancel_result = self.cancel_order(order_id)
            if cancel_result is not None:
                cancel_success = True
                break
            logger.warning(f"Cancel attempt {attempt + 1}/3 failed, retrying...")
            time.sleep(1)

        # Verify cancellation by checking if order is still in open orders
        time.sleep(1)  # Brief delay to let Saxo process the cancel
        open_orders = self.get_open_orders()
        order_still_open = any(o.get("OrderId") == order_id for o in open_orders)

        if order_still_open:
            # CRITICAL: Order is still open - cancel failed
            # This is a serious issue that requires manual intervention
            logger.critical(f"⚠️ CRITICAL: Order {order_id} is STILL OPEN after cancel attempts!")
            logger.critical("Manual cancellation required in SaxoTraderGO before bot continues!")
            return {
                "success": False,
                "filled": False,
                "order_id": order_id,
                "message": f"CRITICAL: Order {order_id} still open after timeout. Cancel failed. MANUAL CANCELLATION REQUIRED.",
                "fill_price": None,
                "cancel_failed": True  # Flag to indicate cancel failure
            }

        if not cancel_success:
            # Cancel returned error but order is no longer in open orders
            # It might have filled at the last moment or been rejected
            logger.warning(f"Order {order_id} not found after cancel - may have filled or been rejected")

        return {
            "success": False,
            "filled": False,
            "order_id": order_id,
            "message": f"TIMEOUT: Order not filled within {timeout_seconds}s. Order cancelled.",
            "fill_price": None
        }

    def place_market_order_immediate(
        self,
        uic: int,
        asset_type: str,
        buy_sell: BuySell,
        amount: int,
        to_open_close: str = "ToOpen"
    ) -> Dict:
        """
        Place a MARKET order for immediate fill.

        Use this for emergency situations (ITM risk, circuit breaker closure)
        where we need guaranteed execution over price.

        IMPORTANT: Market orders on options can have significant slippage!
        Only use for urgent risk management scenarios.

        Args:
            uic: Instrument UIC
            asset_type: Type of asset
            buy_sell: Buy or Sell direction
            amount: Number of contracts
            to_open_close: ToOpen or ToClose (required for options)

        Returns:
            dict: {
                "success": bool,
                "filled": bool,
                "order_id": str or None,
                "message": str,
                "fill_price": float or None (unknown for market orders)
            }
        """
        logger.warning(f"🚨 EMERGENCY MARKET ORDER: {buy_sell.value} {amount} x UIC {uic}")
        logger.warning("   Market orders execute immediately but may have significant slippage!")

        # Place market order
        order_response = self.place_order(
            uic=uic,
            asset_type=asset_type,
            buy_sell=buy_sell,
            amount=amount,
            order_type=OrderType.MARKET,
            duration_type="DayOrder",
            to_open_close=to_open_close
        )

        if not order_response or "OrderId" not in order_response:
            return {
                "success": False,
                "filled": False,
                "order_id": None,
                "message": "Failed to place market order",
                "fill_price": None
            }

        order_id = order_response["OrderId"]

        # Market orders should fill immediately, but verify
        import time
        time.sleep(1)  # Brief delay to let order process

        # Check if order is still open (shouldn't be for market order)
        open_orders = self.get_open_orders()
        order_still_open = any(o.get("OrderId") == order_id for o in open_orders)

        if order_still_open:
            # Very unusual for market order - might be a halt or issue
            logger.error(f"⚠ Market order {order_id} still open after 1s - unusual!")
            return {
                "success": True,
                "filled": False,
                "order_id": order_id,
                "message": "Market order placed but may not have filled yet",
                "fill_price": None
            }

        logger.info(f"✓ Market order {order_id} executed")
        return {
            "success": True,
            "filled": True,
            "order_id": order_id,
            "message": "Market order executed",
            "fill_price": None  # Unknown, would need to check trade history
        }

    def place_aggressive_limit_order(
        self,
        uic: int,
        asset_type: str,
        buy_sell: BuySell,
        amount: int,
        to_open_close: str = "ToOpen",
        slippage_percent: float = 5.0,
        timeout_seconds: int = 30
    ) -> Dict:
        """
        Place an aggressive limit order that crosses the spread.

        This provides better fill probability than regular limit orders
        while still having some price protection (unlike market orders).

        For BUYS: Use Ask + slippage%
        For SELLS: Use Bid - slippage%

        Args:
            uic: Instrument UIC
            asset_type: Type of asset
            buy_sell: Buy or Sell direction
            amount: Number of contracts
            to_open_close: ToOpen or ToClose
            slippage_percent: How much above Ask (buys) or below Bid (sells) to place limit
            timeout_seconds: Shorter timeout since we're being aggressive (default 30s)

        Returns:
            dict: Same as place_limit_order_with_timeout
        """
        # Get current quote
        quote = self.get_quote(uic, asset_type)
        if not quote or "Quote" not in quote:
            logger.error(f"Failed to get quote for UIC {uic}")
            return {
                "success": False,
                "filled": False,
                "order_id": None,
                "message": "Failed to get quote for aggressive limit",
                "fill_price": None
            }

        bid = quote["Quote"].get("Bid", 0) or 0
        ask = quote["Quote"].get("Ask", 0) or 0

        if buy_sell == BuySell.BUY:
            # For buys, we want to pay MORE to ensure fill
            aggressive_price = ask * (1 + slippage_percent / 100)
            logger.info(f"Aggressive BUY: Ask ${ask:.2f} + {slippage_percent}% = ${aggressive_price:.2f}")
        else:
            # For sells, we want to accept LESS to ensure fill
            aggressive_price = bid * (1 - slippage_percent / 100)
            logger.info(f"Aggressive SELL: Bid ${bid:.2f} - {slippage_percent}% = ${aggressive_price:.2f}")

        # Round to 2 decimal places
        aggressive_price = round(aggressive_price, 2)

        logger.warning(f"⚡ AGGRESSIVE LIMIT ORDER: {buy_sell.value} {amount} x UIC {uic} @ ${aggressive_price:.2f}")

        return self.place_limit_order_with_timeout(
            uic=uic,
            asset_type=asset_type,
            buy_sell=buy_sell,
            amount=amount,
            limit_price=aggressive_price,
            timeout_seconds=timeout_seconds,
            to_open_close=to_open_close
        )

    def place_multi_leg_limit_order_with_timeout(
        self,
        legs: List[Dict],
        total_limit_price: float,
        timeout_seconds: int = 60
    ) -> Dict:
        """
        Place a multi-leg limit order and wait for fill with timeout.

        For straddles/strangles, this places the entire combo as a limit order.

        Args:
            legs: List of leg dictionaries with uic, asset_type, buy_sell, amount
            total_limit_price: Total limit price for the combo
            timeout_seconds: Maximum time to wait for fill (default 60s)

        Returns:
            dict: {
                "success": bool,
                "filled": bool,
                "order_id": str or None,
                "message": str
            }
        """
        import time

        logger.info(f"Placing multi-leg LIMIT order with {timeout_seconds}s timeout")
        logger.info(f"  {len(legs)} legs @ total limit ${total_limit_price:.2f}")

        endpoint = "/trade/v2/orders"

        order_data = {
            "AccountKey": self.account_key,
            "OrderType": "Limit",
            "OrderPrice": total_limit_price,
            "OrderDuration": {
                "DurationType": "DayOrder"
            },
            "ManualOrder": True,
            "Orders": []
        }

        # For multi-leg orders, each leg needs complete order details for live trading
        per_leg_price = total_limit_price / len(legs) if legs else 0

        for leg in legs:
            leg_order = {
                "Uic": leg["uic"],
                "AssetType": leg["asset_type"],
                "BuySell": leg["buy_sell"],
                "Amount": leg["amount"],
                "OrderType": "Limit",
                "OrderPrice": leg.get("price", per_leg_price),  # Use leg price if provided
                "ToOpenClose": leg.get("to_open_close", "ToOpen"),
                "OrderDuration": {
                    "DurationType": "DayOrder"
                },
                "ManualOrder": True  # Required for live trading
            }
            order_data["Orders"].append(leg_order)

        response = self._make_request("POST", endpoint, data=order_data)

        if not response or "OrderId" not in response:
            return {
                "success": False,
                "filled": False,
                "order_id": None,
                "message": "Failed to place multi-leg limit order"
            }

        order_id = response["OrderId"]
        logger.info(f"Multi-leg limit order placed: {order_id}")

        # Poll for fill status
        start_time = time.time()
        check_interval = 2

        while (time.time() - start_time) < timeout_seconds:
            open_orders = self.get_open_orders()
            order_still_open = any(o.get("OrderId") == order_id for o in open_orders)

            if not order_still_open:
                elapsed = time.time() - start_time
                logger.info(f"✓ Multi-leg limit order filled in {elapsed:.1f}s")
                return {
                    "success": True,
                    "filled": True,
                    "order_id": order_id,
                    "message": f"Order filled in {elapsed:.1f}s"
                }

            time.sleep(check_interval)

        # Timeout - cancel
        elapsed = time.time() - start_time
        logger.warning(f"⚠ Multi-leg order NOT filled after {elapsed:.1f}s - cancelling")
        self.cancel_order(order_id)

        return {
            "success": False,
            "filled": False,
            "order_id": order_id,
            "message": f"TIMEOUT: Order not filled within {timeout_seconds}s. Order cancelled. ALERT: Manual review required."
        }

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
            # CRITICAL FIX (2026-01-23): Ensure token is fresh BEFORE starting WebSocket
            # This prevents 401 Unauthorized errors when waking from sleep.
            # The WebSocket handshake uses self.access_token directly (not _make_request),
            # so we must refresh here to get the latest token from the coordinator cache.
            # Without this, a stale token can cause handshake failures if another bot
            # refreshed the shared token while this bot was sleeping.
            # See: CONN-008 in IRON_FLY_EDGE_CASES.md
            if not self.authenticate():
                logger.warning("Token refresh failed before WebSocket connection - proceeding with existing token")

            # Generate fresh context ID to avoid "Subscription Key already in use" errors on reconnect
            self.subscription_context_id = f"ctx_{int(time.time())}"
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
                
                # heartbeat checks - still record success to keep circuit breaker happy
                if "ReferenceId" in data and data["ReferenceId"] == "_heartbeat":
                    self._heartbeat_count += 1
                    self._record_success()  # Heartbeat proves WebSocket is alive
                    # Log every 10th heartbeat to confirm they're being received
                    if self._heartbeat_count % 10 == 0:
                        logger.debug(f"WebSocket heartbeat #{self._heartbeat_count} received - connection healthy")
                    return

                self._handle_streaming_message(data)
                self._record_success()
            except Exception as e:
                # Don't log every decode error (keep logs clean)
                pass

        def on_error(ws, error):
            logger.error(f"WebSocket error: {error}")

        def on_close(ws, close_status_code, close_msg):
            # Only log warning if this was an unexpected close
            if getattr(self, '_intentional_ws_close', False):
                logger.debug("WebSocket closed (intentional)")
                self._intentional_ws_close = False
            else:
                logger.warning(f"WebSocket closed unexpectedly: code={close_status_code}, reason={close_msg}")
            self.is_streaming = False

        def on_open(ws):
            logger.info("WebSocket connection established")
            self.is_streaming = True
            # Reset circuit breaker on successful WebSocket connection
            self._record_success()
            
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
            # More aggressive keepalive to prevent Saxo server-side disconnections
            kwargs={"ping_interval": 15, "ping_timeout": 10}
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
        self._intentional_ws_close = True  # Flag to suppress warning in on_close
        if self.ws_connection:
            self.ws_connection.close()
            self.ws_connection = None

        self.is_streaming = False
        self.price_callbacks.clear()

        # Delete subscription via REST
        endpoint = f"/trade/v1/prices/subscriptions/{self.subscription_context_id}"
        self._make_request("DELETE", endpoint)

        logger.info("Price streaming stopped")

    def subscribe_to_option(
        self,
        uic: int,
        callback: Callable[[int, Dict], None] = None,
        asset_type: str = "StockOption"
    ) -> bool:
        """
        Subscribe to real-time price streaming for a specific option.

        This uses /trade/v1/prices/subscriptions to get real-time tradable prices
        instead of the indicative prices from /trade/v1/infoprices.

        Args:
            uic: The option's Unique Instrument Code
            callback: Optional callback for price updates
            asset_type: Asset type - "StockOption" for SPY, "StockIndexOption" for SPX/SPXW

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
        # LIVE-001: Use correct asset type (StockIndexOption for SPX/SPXW, StockOption for SPY)
        subscription_request = {
            "ContextId": self.subscription_context_id,
            "ReferenceId": f"opt_{uic}",
            "Arguments": {
                "AccountKey": self.account_key,
                "Uic": int(uic),
                "AssetType": asset_type,
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

    def get_closed_spy_positions(
        self,
        from_date: str,
        to_date: str = None
    ) -> Optional[List[Dict]]:
        """
        Get closed SPY option positions within a date range.

        Uses the Saxo Client Services historical report endpoint to retrieve
        all SPY option positions that were closed between the specified dates.

        Args:
            from_date: Start date in YYYY-MM-DD format
            to_date: End date in YYYY-MM-DD format (defaults to today)

        Returns:
            List of closed SPY option position records, or None if request failed.
            Each record includes:
            - ClosedPositionId
            - AssetType
            - Uic
            - Amount
            - OpenPrice
            - ClosePrice
            - ProfitLoss (realized P&L)
            - ExecutionTimeOpen
            - ExecutionTimeClose
            - Symbol/Description

        Example:
            >>> closed = client.get_closed_spy_positions("2025-12-26")
            >>> for pos in closed:
            ...     print(f"{pos['Symbol']}: P&L ${pos.get('ProfitLoss', 0):.2f}")
        """
        from datetime import datetime

        if not to_date:
            to_date = datetime.now().strftime("%Y-%m-%d")

        # Endpoint: /cs/v1/reports/closedPositions/{ClientKey}/{FromDate}/{ToDate}
        endpoint = f"/cs/v1/reports/closedPositions/{self.client_key}/{from_date}/{to_date}"

        params = {
            "AccountKey": self.account_key
        }

        try:
            response = self._make_request("GET", endpoint, params=params)
            positions = []

            if response and "Data" in response:
                positions = response["Data"]
            elif response and isinstance(response, list):
                positions = response

            logger.info(f"Total closed positions (all assets): {len(positions)}")

            # Filter to only SPY stock options
            # Note: Saxo uses InstrumentDescription and InstrumentSymbol fields
            spy_positions = []
            for pos in positions:
                asset_type = pos.get("AssetType", "")
                description = pos.get("InstrumentDescription", "") or ""
                symbol = pos.get("InstrumentSymbol", "") or ""

                if asset_type == "StockOption" and ("SPY" in description.upper() or "SPY" in symbol.upper()):
                    spy_positions.append(pos)

            logger.info(f"Retrieved {len(spy_positions)} closed SPY positions from {from_date} to {to_date}")
            return spy_positions

        except Exception as e:
            logger.error(f"Failed to get closed SPY positions: {e}")
            return None

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

    def get_expected_move_from_straddle(
        self,
        underlying_uic: int,
        underlying_price: float,
        target_dte_min: int = 0,
        target_dte_max: int = 7,
        for_roll: bool = False,
        option_root_uic: int = None,
        option_asset_type: str = "StockOption"
    ) -> Optional[float]:
        """
        Get expected move by pricing the ATM straddle for a given expiration.

        The ATM straddle price IS the market's expected move - this is the most
        accurate way to calculate expected move as it uses actual option prices
        rather than VIX or theoretical IV calculations.

        Args:
            underlying_uic: UIC of the underlying instrument
            underlying_price: Current price of the underlying
            target_dte_min: Minimum DTE for expiration search
            target_dte_max: Maximum DTE for expiration search
            for_roll: If True, look for next week's expiry (5-12 DTE)
            option_root_uic: Optional UIC of the option root (for StockIndexOptions like SPXW)

        Returns:
            float: Expected move in dollars, or None if unable to calculate
        """
        # Get option expirations
        expirations = self.get_option_expirations(underlying_uic, option_root_uic=option_root_uic)
        if not expirations:
            logger.error("Failed to get option expirations for expected move")
            return None

        # Find the target expiration
        today = datetime.now().date()
        target_expiration = None

        if for_roll:
            # Rolling: look for next week (5-12 DTE)
            dte_min, dte_max = 5, 12
        else:
            # Normal: use provided range
            dte_min, dte_max = target_dte_min, target_dte_max

        for exp_data in expirations:
            exp_date_str = exp_data.get("Expiry")
            if not exp_date_str:
                continue
            exp_date = datetime.strptime(exp_date_str[:10], "%Y-%m-%d").date()
            dte = (exp_date - today).days

            if dte_min <= dte <= dte_max:
                target_expiration = exp_data
                logger.info(f"Expected move: using expiration {exp_date_str[:10]} ({dte} DTE)")
                break

        if not target_expiration:
            logger.warning(f"No expiration found for expected move ({dte_min}-{dte_max} DTE)")
            return None

        # Find ATM strike
        specific_options = target_expiration.get("SpecificOptions", [])
        if not specific_options:
            logger.error("No options available at target expiration")
            return None

        # Find closest strike to current price
        atm_strike = None
        min_diff = float('inf')
        for option in specific_options:
            strike = option.get("StrikePrice", 0)
            diff = abs(strike - underlying_price)
            if diff < min_diff:
                min_diff = diff
                atm_strike = strike

        if not atm_strike:
            logger.error("Could not find ATM strike")
            return None

        # Get ATM call and put UICs
        call_uic = None
        put_uic = None
        for option in specific_options:
            if option.get("StrikePrice") == atm_strike:
                if option.get("PutCall") == "Call":
                    call_uic = option.get("Uic")
                elif option.get("PutCall") == "Put":
                    put_uic = option.get("Uic")

        if not call_uic or not put_uic:
            logger.error(f"Could not find ATM call/put at strike {atm_strike}")
            return None

        # Get quotes for ATM options
        call_quote = self.get_quote(call_uic, option_asset_type)
        put_quote = self.get_quote(put_uic, option_asset_type)

        if not call_quote or not put_quote:
            logger.error("Failed to get ATM option quotes")
            return None

        # Use mid prices
        call_mid = call_quote.get("Quote", {}).get("Mid", 0)
        put_mid = put_quote.get("Quote", {}).get("Mid", 0)

        if call_mid <= 0 or put_mid <= 0:
            # Fall back to bid/ask average
            call_bid = call_quote.get("Quote", {}).get("Bid", 0)
            call_ask = call_quote.get("Quote", {}).get("Ask", 0)
            put_bid = put_quote.get("Quote", {}).get("Bid", 0)
            put_ask = put_quote.get("Quote", {}).get("Ask", 0)
            call_mid = (call_bid + call_ask) / 2 if call_bid > 0 and call_ask > 0 else 0
            put_mid = (put_bid + put_ask) / 2 if put_bid > 0 and put_ask > 0 else 0

        if call_mid <= 0 or put_mid <= 0:
            logger.error("Invalid ATM option prices")
            return None

        # ATM straddle price = expected move
        expected_move = call_mid + put_mid

        logger.info(f"Expected move from ATM straddle: ${expected_move:.2f} "
                    f"(Call ${call_mid:.2f} + Put ${put_mid:.2f} at strike {atm_strike})")

        return expected_move

    def calculate_expected_move(
        self,
        underlying_price: float,
        iv: float,
        days: int = 7
    ) -> float:
        """
        DEPRECATED: Calculate expected move based on implied volatility.

        NOTE: This method uses a theoretical formula that may not match market prices.
        Prefer get_expected_move_from_straddle() for accurate expected move.

        Uses the formula: Expected Move = Price * IV * sqrt(Days/365)

        Args:
            underlying_price: Current price of the underlying
            iv: Implied volatility as decimal (e.g., 0.20 for 20%)
            days: Number of days for the calculation

        Returns:
            float: Expected move in dollars
        """
        import math
        logger.warning("calculate_expected_move is deprecated - use get_expected_move_from_straddle instead")
        expected_move = underlying_price * iv * math.sqrt(days / 365)
        return expected_move

    # =========================================================================
    # CHART DATA METHODS (for technical indicators)
    # =========================================================================

    def get_chart_data(
        self,
        uic: int,
        asset_type: str = "Stock",
        horizon: int = 60,
        count: int = 50,
        field_groups: str = "ChartInfo,Data"
    ) -> Optional[Dict]:
        """
        Get historical OHLC chart data for an instrument.

        Uses the Saxo Chart API to fetch historical price bars.
        Useful for calculating technical indicators like EMA, MACD, CCI.

        Args:
            uic: UIC of the instrument
            asset_type: Asset type (Stock, Etf, StockIndex, etc.)
            horizon: Time horizon for each bar in MINUTES:
                     1 = 1 minute, 5 = 5 minutes, 60 = 1 hour, 1440 = 1 day
            count: Number of bars to fetch (max 1200)
            field_groups: Data fields to include

        Returns:
            dict: Chart data including OHLC bars, or None if failed.
                  Structure: {
                      "ChartInfo": {...},
                      "Data": [
                          {"Time": "...", "Open": ..., "High": ..., "Low": ..., "Close": ...},
                          ...
                      ]
                  }
        """
        endpoint = f"/chart/v3/charts"
        params = {
            "Uic": uic,
            "AssetType": asset_type,
            "Horizon": horizon,
            "Count": count,
            "FieldGroups": field_groups
        }

        try:
            result = self._make_request("GET", endpoint, params=params)
            if result and "Data" in result:
                logger.debug(f"Got {len(result['Data'])} chart bars for UIC {uic}")
                return result
            logger.warning(f"No chart data returned for UIC {uic}")
            return None
        except Exception as e:
            logger.error(f"Failed to get chart data for UIC {uic}: {e}")
            return None

    def get_daily_ohlc(
        self,
        uic: int,
        asset_type: str = "Stock",
        days: int = 50
    ) -> Optional[List[Dict]]:
        """
        Get daily OHLC bars for an instrument.

        Convenience method for get_chart_data with daily horizon.

        Args:
            uic: UIC of the instrument
            asset_type: Asset type
            days: Number of days of data to fetch

        Returns:
            list: List of OHLC bars [{Time, Open, High, Low, Close}, ...]
                  or None if failed.
        """
        result = self.get_chart_data(
            uic=uic,
            asset_type=asset_type,
            horizon=1440,  # 1440 minutes = 1 day
            count=days
        )

        if result and "Data" in result:
            return result["Data"]
        return None

    # =========================================================================
    # DELTA-BASED OPTION FINDING
    # =========================================================================

    def find_put_by_delta(
        self,
        underlying_uic: int,
        underlying_price: float,
        target_delta: float,
        target_dte: int,
        delta_tolerance: float = 0.05
    ) -> Optional[Dict]:
        """
        Find a put option with a specific target delta.

        Used for strategies that require specific delta positioning,
        such as the Rolling Put Diagonal (33 delta long put).

        Args:
            underlying_uic: UIC of the underlying instrument
            underlying_price: Current price of the underlying
            target_delta: Target delta (negative for puts, e.g., -0.33)
                          Will be converted to absolute value for comparison.
            target_dte: Target days to expiration
            delta_tolerance: Acceptable deviation from target delta (default 0.05)

        Returns:
            dict: Put option data with UIC, strike, expiry, delta, or None if not found.
                  Structure: {
                      "uic": int,
                      "strike": float,
                      "expiry": str,
                      "delta": float,
                      "dte": int,
                      "option_type": "Put"
                  }
        """
        # Ensure target_delta is positive for comparison
        target_delta_abs = abs(target_delta)

        expirations = self.get_option_expirations(underlying_uic)
        if not expirations:
            logger.error("Failed to get option expirations")
            return None

        # Find closest expiration to target DTE
        today = datetime.now().date()
        target_expiration = None
        selected_dte = None
        closest_diff = float('inf')

        for exp_data in expirations:
            exp_date_str = exp_data.get("Expiry")
            if not exp_date_str:
                continue

            exp_date = datetime.strptime(exp_date_str[:10], "%Y-%m-%d").date()
            dte = (exp_date - today).days
            diff = abs(dte - target_dte)

            if diff < closest_diff:
                closest_diff = diff
                target_expiration = exp_data
                selected_dte = dte

        if not target_expiration:
            logger.warning(f"No expiration found close to {target_dte} DTE")
            return None

        logger.info(f"Using expiration with {selected_dte} DTE (target: {target_dte})")

        # Get all puts for this expiration
        specific_options = target_expiration.get("SpecificOptions", [])
        if not specific_options:
            logger.error("No SpecificOptions in target expiration")
            return None

        # Filter to puts only
        puts = [opt for opt in specific_options if opt.get("PutCall") == "Put"]
        if not puts:
            logger.error("No puts found in expiration")
            return None

        # For each put, get the Greeks and find the one closest to target delta
        best_match = None
        best_delta_diff = float('inf')

        for put in puts:
            put_uic = put.get("Uic")
            strike = put.get("StrikePrice")

            if not put_uic:
                continue

            # Get Greeks for this option
            greeks = self.get_option_greeks(put_uic)
            if not greeks:
                continue

            delta = greeks.get("Delta", 0)
            delta_abs = abs(delta)

            # Check if this delta is within tolerance and closer to target
            delta_diff = abs(delta_abs - target_delta_abs)
            if delta_diff <= delta_tolerance and delta_diff < best_delta_diff:
                best_delta_diff = delta_diff
                best_match = {
                    "uic": put_uic,
                    "strike": strike,
                    "expiry": target_expiration.get("Expiry"),
                    "delta": delta,
                    "theta": greeks.get("Theta", 0),
                    "gamma": greeks.get("Gamma", 0),
                    "vega": greeks.get("Vega", 0),
                    "dte": selected_dte,
                    "option_type": "Put"
                }

        if best_match:
            logger.info(f"Found put at strike {best_match['strike']} with delta {best_match['delta']:.3f} "
                        f"(target: {-target_delta_abs:.3f}, diff: {best_delta_diff:.3f})")
            return best_match
        else:
            logger.warning(f"No put found with delta close to {-target_delta_abs:.3f} "
                          f"(tolerance: {delta_tolerance})")
            return None

    def find_next_trading_day_expiry(
        self,
        underlying_uic: int
    ) -> Optional[Dict]:
        """
        Find the next trading day's expiration (1 DTE options).

        Used for strategies that sell daily options like the Rolling Put Diagonal.

        Args:
            underlying_uic: UIC of the underlying instrument

        Returns:
            dict: Expiration data with expiry date and available strikes, or None.
                  Structure: {
                      "expiry": str,
                      "dte": int,
                      "options": list  # SpecificOptions from the expiration
                  }
        """
        expirations = self.get_option_expirations(underlying_uic)
        if not expirations:
            logger.error("Failed to get option expirations")
            return None

        today = datetime.now().date()

        # Find the first expiration that is 1 or more days away
        for exp_data in expirations:
            exp_date_str = exp_data.get("Expiry")
            if not exp_date_str:
                continue

            exp_date = datetime.strptime(exp_date_str[:10], "%Y-%m-%d").date()
            dte = (exp_date - today).days

            # We want 1 DTE (tomorrow) or 2 DTE (day after) for next trading day
            if dte >= 1:
                logger.info(f"Found next trading day expiry: {exp_date_str} ({dte} DTE)")
                return {
                    "expiry": exp_date_str,
                    "dte": dte,
                    "options": exp_data.get("SpecificOptions", [])
                }

        logger.warning("No next trading day expiration found")
        return None

    def find_atm_put_for_expiry(
        self,
        underlying_uic: int,
        underlying_price: float,
        expiry_date: str
    ) -> Optional[Dict]:
        """
        Find the ATM put for a specific expiry date.

        Used for selling daily ATM puts in strategies like Rolling Put Diagonal.

        Args:
            underlying_uic: UIC of the underlying instrument
            underlying_price: Current price of the underlying
            expiry_date: Target expiry date string (e.g., "2026-01-20T00:00:00Z")

        Returns:
            dict: ATM put data with UIC, strike, expiry, or None if not found.
        """
        expirations = self.get_option_expirations(underlying_uic)
        if not expirations:
            return None

        # Find the matching expiration
        target_expiration = None
        target_date = expiry_date[:10]  # Just the date part

        for exp_data in expirations:
            exp_date_str = exp_data.get("Expiry", "")
            if exp_date_str[:10] == target_date:
                target_expiration = exp_data
                break

        if not target_expiration:
            logger.error(f"No expiration found matching {target_date}")
            return None

        # Find ATM strike
        specific_options = target_expiration.get("SpecificOptions", [])
        if not specific_options:
            return None

        # Find strike closest to underlying price
        atm_strike = None
        min_diff = float('inf')

        for option in specific_options:
            strike = option.get("StrikePrice", 0)
            diff = abs(strike - underlying_price)
            if diff < min_diff:
                min_diff = diff
                atm_strike = strike

        if atm_strike is None:
            return None

        # Find the put at ATM strike
        for option in specific_options:
            if option.get("StrikePrice") == atm_strike and option.get("PutCall") == "Put":
                today = datetime.now().date()
                exp_date = datetime.strptime(target_date, "%Y-%m-%d").date()
                dte = (exp_date - today).days

                return {
                    "uic": option.get("Uic"),
                    "strike": atm_strike,
                    "expiry": target_expiration.get("Expiry"),
                    "dte": dte,
                    "option_type": "Put"
                }

        return None
