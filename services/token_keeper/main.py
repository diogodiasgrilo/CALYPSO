#!/usr/bin/env python3
"""
Token Keeper Service - Keeps Saxo OAuth Tokens Fresh

This is a dedicated service that runs independently of all trading bots
to ensure the Saxo API OAuth token is always valid. This prevents the
scenario where all bots are stopped and the token expires, requiring
manual re-authentication.

How It Works:
-------------
1. Runs as a lightweight systemd service with Restart=always
2. Checks token expiry every minute
3. Refreshes token when it's within 5 minutes of expiry
4. Uses the same TokenCoordinator as all bots (file-based locking)
5. Saves refreshed tokens to both local cache and Secret Manager

Why It's Needed:
----------------
- Saxo tokens expire every 20 minutes
- If all bots are stopped (e.g., for safety), no one refreshes the token
- Expired tokens require manual OAuth browser flow to re-authenticate
- This service ensures tokens stay fresh 24/7

Usage:
------
    python -m services.token_keeper.main              # Run directly
    systemctl start token_keeper                      # As systemd service
    systemctl status token_keeper                     # Check status

Configuration:
--------------
Uses the same config as other bots (loads from iron_fly_0dte config by default).
Only needs the Saxo credentials section for token refresh.

Author: Trading Bot Developer
Date: 2026-01
"""

import os
import sys
import time
import signal
import logging
from datetime import datetime, timedelta
from typing import Optional

# Ensure project root is in path for imports when running as script
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from shared.token_coordinator import get_token_coordinator, TokenCoordinator
from shared.config_loader import get_config_loader
from shared.secret_manager import is_running_on_gcp

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Configuration
CHECK_INTERVAL_SECONDS = 60  # Check every minute
REFRESH_THRESHOLD_SECONDS = 300  # Refresh when < 5 minutes until expiry
MAX_REFRESH_FAILURES = 5  # Max consecutive failures before alerting

# Global flag for graceful shutdown
shutdown_requested = False


def signal_handler(signum, frame):
    """Handle shutdown signals (CTRL+C, SIGTERM)."""
    global shutdown_requested
    logger.info(f"Shutdown signal received ({signum}). Exiting gracefully...")
    shutdown_requested = True


def get_token_age_info(coordinator: TokenCoordinator) -> dict:
    """
    Get information about the current token's age and expiry.

    Returns:
        dict with keys: valid, expires_at, seconds_until_expiry, needs_refresh
    """
    tokens = coordinator.get_cached_tokens()

    if not tokens:
        return {
            'valid': False,
            'expires_at': None,
            'seconds_until_expiry': 0,
            'needs_refresh': True,
            'reason': 'No cached tokens found'
        }

    expiry_str = tokens.get('token_expiry')
    if not expiry_str:
        return {
            'valid': False,
            'expires_at': None,
            'seconds_until_expiry': 0,
            'needs_refresh': True,
            'reason': 'Token has no expiry timestamp'
        }

    try:
        # Parse expiry timestamp
        expiry = datetime.fromisoformat(expiry_str.replace('Z', '+00:00'))

        # Make timezone-aware comparison
        now = datetime.now(expiry.tzinfo) if expiry.tzinfo else datetime.now()
        seconds_until_expiry = (expiry - now).total_seconds()

        return {
            'valid': seconds_until_expiry > 0,
            'expires_at': expiry,
            'seconds_until_expiry': max(0, seconds_until_expiry),
            'needs_refresh': seconds_until_expiry < REFRESH_THRESHOLD_SECONDS,
            'reason': None if seconds_until_expiry >= REFRESH_THRESHOLD_SECONDS else f'Expires in {int(seconds_until_expiry)}s'
        }
    except (ValueError, TypeError) as e:
        return {
            'valid': False,
            'expires_at': None,
            'seconds_until_expiry': 0,
            'needs_refresh': True,
            'reason': f'Invalid expiry format: {e}'
        }


def perform_token_refresh(coordinator: TokenCoordinator, config: dict) -> bool:
    """
    Perform a token refresh using the coordinator.

    This replicates the refresh logic from SaxoClient but in a standalone way.

    Args:
        coordinator: The TokenCoordinator instance
        config: Configuration dict with Saxo credentials

    Returns:
        bool: True if refresh successful, False otherwise
    """
    import requests

    # Get current tokens
    tokens = coordinator.get_cached_tokens()
    if not tokens:
        logger.error("Cannot refresh: no existing tokens in cache")
        return False

    refresh_token = tokens.get('refresh_token')
    app_key = tokens.get('app_key')
    app_secret = tokens.get('app_secret')

    if not all([refresh_token, app_key, app_secret]):
        logger.error("Cannot refresh: missing refresh_token, app_key, or app_secret in cache")
        return False

    # Determine token URL based on environment
    saxo_config = config.get("saxo", {})
    environment = saxo_config.get("environment", "SIM")

    if environment.upper() == "LIVE":
        token_url = "https://live.logonvalidation.net/token"
    else:
        token_url = "https://sim.logonvalidation.net/token"

    def do_refresh():
        """Actual refresh logic wrapped for coordinator."""
        try:
            refresh_data = {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": app_key,
                "client_secret": app_secret,
            }

            response = requests.post(
                token_url,
                data=refresh_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30
            )

            if response.status_code in [200, 201]:
                token_response = response.json()
                new_access_token = token_response["access_token"]
                new_refresh_token = token_response.get("refresh_token", refresh_token)

                expires_in = token_response.get("expires_in", 1200)
                new_expiry = datetime.now() + timedelta(seconds=expires_in - 60)

                logger.info(f"Token refreshed successfully (expires in {expires_in // 60} min)")

                return {
                    'access_token': new_access_token,
                    'refresh_token': new_refresh_token,
                    'token_expiry': new_expiry.isoformat(),
                    'app_key': app_key,
                    'app_secret': app_secret,
                }
            else:
                logger.error(f"Token refresh API failed: {response.status_code} - {response.text}")
                return None

        except Exception as e:
            logger.error(f"Token refresh error: {e}")
            return None

    def save_to_secret_manager(new_tokens):
        """Save tokens to Secret Manager after refresh."""
        try:
            from shared.config_loader import get_config_loader
            loader = get_config_loader()
            if loader:
                success = loader.save_tokens(
                    new_tokens.get('access_token', ''),
                    new_tokens.get('refresh_token', ''),
                    new_tokens.get('token_expiry')
                )
                if success:
                    logger.info("Tokens saved to Secret Manager")
                return success
        except Exception as e:
            logger.warning(f"Failed to save to Secret Manager: {e}")
        return False

    # Use coordinator with lock
    new_tokens = coordinator.refresh_with_lock(do_refresh, save_to_secret_manager)
    return new_tokens is not None


def run_token_keeper(config: dict):
    """
    Main loop for the token keeper service.

    Args:
        config: Configuration dict with Saxo credentials
    """
    global shutdown_requested

    coordinator = get_token_coordinator()
    consecutive_failures = 0
    last_status_log = datetime.now() - timedelta(hours=1)  # Force initial status log

    logger.info("=" * 60)
    logger.info("TOKEN KEEPER SERVICE STARTING")
    logger.info(f"Check interval: {CHECK_INTERVAL_SECONDS}s")
    logger.info(f"Refresh threshold: {REFRESH_THRESHOLD_SECONDS}s before expiry")
    logger.info(f"Data directory: {coordinator.data_dir}")
    logger.info("=" * 60)

    # Initial status check
    token_info = get_token_age_info(coordinator)
    if token_info['valid']:
        minutes_left = int(token_info['seconds_until_expiry'] / 60)
        logger.info(f"Current token valid, expires in {minutes_left} minutes")
    else:
        logger.warning(f"Token invalid or missing: {token_info.get('reason', 'unknown')}")

    while not shutdown_requested:
        try:
            # Check token status
            token_info = get_token_age_info(coordinator)

            # Log status every 15 minutes
            now = datetime.now()
            if (now - last_status_log).total_seconds() >= 900:  # 15 minutes
                if token_info['valid']:
                    minutes_left = int(token_info['seconds_until_expiry'] / 60)
                    logger.info(f"Token status: valid, {minutes_left} minutes until expiry")
                else:
                    logger.warning(f"Token status: invalid - {token_info.get('reason', 'unknown')}")
                last_status_log = now

            # Refresh if needed
            if token_info['needs_refresh']:
                logger.info(f"Token needs refresh: {token_info.get('reason', 'threshold reached')}")

                if perform_token_refresh(coordinator, config):
                    consecutive_failures = 0
                    # Log new token info
                    new_info = get_token_age_info(coordinator)
                    if new_info['valid']:
                        minutes_left = int(new_info['seconds_until_expiry'] / 60)
                        logger.info(f"New token valid for {minutes_left} minutes")
                else:
                    consecutive_failures += 1
                    logger.error(f"Token refresh failed (attempt {consecutive_failures}/{MAX_REFRESH_FAILURES})")

                    if consecutive_failures >= MAX_REFRESH_FAILURES:
                        logger.critical(
                            f"ALERT: Token refresh failed {MAX_REFRESH_FAILURES} consecutive times! "
                            "Manual intervention may be required."
                        )
                        # Don't reset counter - keep alerting until fixed

            # Sleep until next check
            time.sleep(CHECK_INTERVAL_SECONDS)

        except Exception as e:
            logger.error(f"Error in main loop: {e}", exc_info=True)
            time.sleep(CHECK_INTERVAL_SECONDS)

    logger.info("Token Keeper service stopped")


def main():
    """Entry point for the token keeper service."""
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Load configuration
    # Use iron_fly config as it has the Saxo credentials
    # (all bots share the same credentials)
    config_path = "bots/iron_fly_0dte/config"

    try:
        loader = get_config_loader(config_path)
        config = loader.load_config()
        logger.info(f"Configuration loaded from {config_path}")
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        logger.error("Token Keeper requires valid Saxo credentials in config")
        sys.exit(1)

    # Verify we have the necessary credentials
    saxo_config = config.get("saxo", {})
    if not saxo_config.get("app_key") or not saxo_config.get("app_secret"):
        logger.error("Missing app_key or app_secret in Saxo configuration")
        sys.exit(1)

    environment = saxo_config.get("environment", "SIM")
    logger.info(f"Saxo environment: {environment}")
    logger.info(f"Running on GCP: {is_running_on_gcp()}")

    # Run the main loop
    run_token_keeper(config)


if __name__ == "__main__":
    main()
