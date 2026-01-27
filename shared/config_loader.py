#!/usr/bin/env python3
"""
Config Loader Module

Smart configuration loader that abstracts cloud vs local environment:
- On GCP: Loads CREDENTIALS from Secret Manager, STRATEGY from bot's config.json
- Locally: Loads everything from config.json (supports both SIM and LIVE)

This ensures the same codebase works seamlessly in both environments,
while allowing each bot to have its own strategy configuration.

Multi-Bot Architecture:
- Shared credentials (Saxo tokens, account keys) come from Secret Manager
- Bot-specific settings (strategy, logging, google_sheets) come from config.json
- Each bot has its own config: bots/<bot_name>/config/config.json
"""

import os
import json
import logging
from typing import Dict, Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

# Global reference for token saving (set when config is loaded)
_config_loader_instance: Optional['ConfigLoader'] = None


class ConfigLoader:
    """
    Smart configuration loader with cloud/local detection and multi-bot support.

    Usage:
        # For a specific bot
        loader = ConfigLoader("bots/delta_neutral/config/config.json")
        config = loader.load_config()

    On GCP:
        - CREDENTIALS loaded from Secret Manager (shared across bots)
        - STRATEGY/LOGGING/etc loaded from bot's config.json (bot-specific)
        - Always uses LIVE environment
        - Tokens persisted to Secret Manager after refresh

    Locally:
        - Everything loaded from bot's config.json
        - Supports both SIM and LIVE (use --live flag)
        - Tokens persisted to config.json after refresh
    """

    def __init__(self, local_config_path: str = "config/config.json"):
        """
        Initialize config loader.

        Args:
            local_config_path: Path to bot's config file (used for strategy settings
                               in both local and cloud modes)
        """
        self.local_config_path = local_config_path
        self._is_cloud = None
        self._config = None

    @property
    def is_cloud(self) -> bool:
        """Check if running on GCP."""
        if self._is_cloud is None:
            from shared.secret_manager import is_running_on_gcp
            self._is_cloud = is_running_on_gcp()
        return self._is_cloud

    def load_config(self) -> Dict[str, Any]:
        """
        Load configuration from appropriate source.

        Returns:
            dict: Full configuration dictionary
        """
        if self._config is not None:
            return self._config

        if self.is_cloud:
            logger.info("=" * 60)
            logger.info("CLOUD ENVIRONMENT DETECTED")
            logger.info("Loading configuration from GCP Secret Manager")
            logger.info("=" * 60)
            self._config = self._load_cloud_config()
        else:
            logger.info("Local environment detected - Loading from config file")
            self._config = self._load_local_config()

        # Store global reference for token saving
        global _config_loader_instance
        _config_loader_instance = self

        return self._config

    def _load_cloud_config(self) -> Dict[str, Any]:
        """
        Load configuration for cloud environment (GCP).

        HYBRID APPROACH:
        - CREDENTIALS: Loaded from Secret Manager (shared across all bots)
        - STRATEGY/SETTINGS: Loaded from bot's config.json (bot-specific)

        This allows multiple bots to share Saxo credentials while having
        their own strategy configurations.

        Always uses LIVE environment when running in cloud.

        Returns:
            dict: Configuration merged from secrets + local config

        Raises:
            ValueError: If required secrets are not found
            FileNotFoundError: If bot's config.json doesn't exist
        """
        from shared.secret_manager import (
            get_saxo_credentials,
            get_google_sheets_credentials,
            get_account_config,
            get_email_config,
        )

        logger.info("=" * 60)
        logger.info("CLOUD ENVIRONMENT - HYBRID CONFIG LOADING")
        logger.info("=" * 60)

        # =====================================================================
        # STEP 1: Load bot-specific settings from local config.json
        # =====================================================================
        if not os.path.exists(self.local_config_path):
            raise FileNotFoundError(
                f"Bot config file not found: {self.local_config_path}\n"
                f"Each bot must have its own config.json with strategy settings.\n"
                f"Copy the config.example.json to config.json and customize."
            )

        with open(self.local_config_path, "r") as f:
            bot_config = json.load(f)

        logger.info(f"Loaded bot-specific config from: {self.local_config_path}")
        bot_name = bot_config.get("strategy", {}).get("name", "Unknown Bot")
        logger.info(f"  Bot/Strategy: {bot_name}")

        # =====================================================================
        # STEP 2: Load shared credentials from Secret Manager
        # =====================================================================
        # Get Saxo credentials (required)
        saxo_creds = get_saxo_credentials()
        if not saxo_creds:
            raise ValueError(
                "Failed to load Saxo credentials from Secret Manager. "
                "Ensure 'calypso-saxo-credentials' secret exists."
            )

        # Get account config (required)
        account_config = get_account_config()
        if not account_config:
            raise ValueError(
                "Failed to load account config from Secret Manager. "
                "Ensure 'calypso-account-config' secret exists."
            )

        # Get Google Sheets credentials (optional but recommended)
        sheets_creds = get_google_sheets_credentials()
        if not sheets_creds:
            logger.warning("Google Sheets credentials not found - Sheets logging disabled")

        # Get email config (optional)
        email_config = get_email_config() or {"enabled": False}

        # =====================================================================
        # STEP 3: Build merged config (credentials + bot-specific settings)
        # =====================================================================
        config = {
            # CREDENTIALS from Secret Manager (LIVE environment in cloud)
            "saxo_api": {
                "sim": {},  # Empty - not used in cloud
                "live": saxo_creds,
                "environment": "live",  # Always live in cloud
                "base_url_sim": "https://gateway.saxobank.com/sim/openapi",
                "base_url_live": "https://gateway.saxobank.com/openapi",
                "streaming_url_sim": "wss://streaming.saxobank.com/sim/openapi/streamingws/connect",
                "streaming_url_live": "wss://streaming.saxobank.com/openapi/streamingws/connect",
                "redirect_uri": "http://localhost:8080/callback",
                "auth_url_sim": "https://sim.logonvalidation.net/authorize",
                "auth_url_live": "https://live.logonvalidation.net/authorize",
                "token_url_sim": "https://sim.logonvalidation.net/token",
                "token_url_live": "https://live.logonvalidation.net/token"
            },

            # ACCOUNT from Secret Manager
            "account": account_config,

            # EMAIL from Secret Manager
            "email_alerts": email_config,

            # BOT-SPECIFIC settings from config.json
            "strategy": bot_config.get("strategy", {}),
            "circuit_breaker": bot_config.get("circuit_breaker", {
                "max_consecutive_errors": 3,
                "max_disconnection_seconds": 60,
                "cooldown_minutes": 15
            }),
            "currency": bot_config.get("currency", {
                "base_currency": "USD",
                "account_currency": "EUR",
                "enabled": True,
                "cache_rate_seconds": 300
            }),
            "filters": bot_config.get("filters", {}),

            # GOOGLE SHEETS: Credentials from Secret Manager, settings from config.json
            "google_sheets": {
                "enabled": sheets_creds is not None and bot_config.get("google_sheets", {}).get("enabled", True),
                "credentials_from_secret_manager": True,
                "spreadsheet_name": bot_config.get("google_sheets", {}).get("spreadsheet_name", "Calypso_Bot_Log"),
                "worksheet_name": bot_config.get("google_sheets", {}).get("worksheet_name", "Trades"),
                "strategy_type": bot_config.get("google_sheets", {}).get("strategy_type", "delta_neutral"),
                "include_opening_range": bot_config.get("google_sheets", {}).get("include_opening_range", False)
            },

            # LOGGING from config.json (bot-specific log files)
            "logging": bot_config.get("logging", {
                "log_file": "/var/log/calypso/bot_log.txt",
                "log_level": "INFO",
                "console_output": True
            }),

            # External price feed (usually disabled in cloud/LIVE)
            "external_price_feed": bot_config.get("external_price_feed", {
                "enabled": False,
                "_note": "External feed disabled in cloud (LIVE mode)"
            }),

            # DRY RUN mode (from bot config - allows cloud testing)
            "dry_run": bot_config.get("dry_run", False),

            # ALERTS config from bot config
            "alerts": bot_config.get("alerts", {})
        }

        # Store sheets credentials for later use by GoogleSheetsLogger
        if sheets_creds:
            config["_google_sheets_credentials"] = sheets_creds

        logger.info("=" * 60)
        logger.info("CLOUD CONFIG LOADED SUCCESSFULLY")
        logger.info("=" * 60)
        logger.info(f"  Environment: LIVE")
        logger.info(f"  Strategy: {config['strategy'].get('name', config['strategy'].get('underlying_symbol', 'Unknown'))}")
        logger.info(f"  Google Sheets: {config['google_sheets'].get('spreadsheet_name', 'N/A')}")
        logger.info(f"  Email Alerts: {'Enabled' if email_config.get('enabled') else 'Disabled'}")

        return config

    def _load_local_config(self) -> Dict[str, Any]:
        """
        Load configuration from local JSON file.

        Supports both SIM and LIVE environments via --live flag.

        Returns:
            dict: Configuration from config.json

        Raises:
            FileNotFoundError: If config file doesn't exist
        """
        if not os.path.exists(self.local_config_path):
            raise FileNotFoundError(
                f"Local config file not found: {self.local_config_path}\n"
                f"Copy config/config.example.json to {self.local_config_path}"
            )

        with open(self.local_config_path, "r") as f:
            config = json.load(f)

        logger.info(f"Loaded local config from: {self.local_config_path}")

        # Add default email_alerts section if not present
        if "email_alerts" not in config:
            config["email_alerts"] = {
                "enabled": False,
                "smtp_server": "smtp.gmail.com",
                "smtp_port": 587,
                "sender_email": "",
                "sender_password": "",
                "recipients": [],
                "use_tls": True
            }

        return config

    def save_tokens(self, access_token: str, refresh_token: str, token_expiry: str) -> bool:
        """
        Save updated OAuth tokens (handles cloud vs local storage).

        Called after token refresh to persist new tokens.

        Args:
            access_token: New access token
            refresh_token: New refresh token
            token_expiry: Token expiry timestamp (ISO format string)

        Returns:
            bool: True if saved successfully
        """
        if self.is_cloud:
            return self._save_tokens_to_secret_manager(access_token, refresh_token, token_expiry)
        else:
            return self._save_tokens_to_file(access_token, refresh_token, token_expiry)

    def _save_tokens_to_secret_manager(
        self,
        access_token: str,
        refresh_token: str,
        token_expiry: str
    ) -> bool:
        """Save tokens to GCP Secret Manager."""
        from shared.secret_manager import update_saxo_tokens

        success = update_saxo_tokens(access_token, refresh_token, token_expiry)
        if success:
            logger.info("Tokens saved to Secret Manager")
        else:
            logger.error("Failed to save tokens to Secret Manager")
        return success

    def _save_tokens_to_file(
        self,
        access_token: str,
        refresh_token: str,
        token_expiry: str
    ) -> bool:
        """Save tokens to local config file."""
        try:
            with open(self.local_config_path, "r") as f:
                config = json.load(f)

            env = config["saxo_api"].get("environment", "sim")
            config["saxo_api"][env]["access_token"] = access_token
            config["saxo_api"][env]["refresh_token"] = refresh_token
            config["saxo_api"][env]["token_expiry"] = token_expiry

            with open(self.local_config_path, "w") as f:
                json.dump(config, f, indent=4)

            logger.info(f"Tokens saved to {self.local_config_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to save tokens to file: {e}")
            return False


def get_config_loader() -> Optional[ConfigLoader]:
    """
    Get the global ConfigLoader instance.

    Returns:
        ConfigLoader: The loader instance, or None if not initialized
    """
    return _config_loader_instance


def load_config(config_path: str = "config/config.json") -> Dict[str, Any]:
    """
    Convenience function for loading configuration.

    This is the main entry point for loading config. It automatically
    detects the environment and loads from the appropriate source.

    Args:
        config_path: Path to local config (used only in local mode)

    Returns:
        dict: Configuration dictionary
    """
    loader = ConfigLoader(config_path)
    return loader.load_config()


# Test function
if __name__ == "__main__":
    print("=" * 60)
    print("CONFIG LOADER TEST")
    print("=" * 60)

    loader = ConfigLoader()
    print(f"\nRunning on GCP: {loader.is_cloud}")

    try:
        config = loader.load_config()
        print(f"\nConfiguration loaded successfully!")
        print(f"  Environment: {config['saxo_api'].get('environment', 'unknown')}")
        print(f"  Google Sheets: {'Enabled' if config.get('google_sheets', {}).get('enabled') else 'Disabled'}")
        print(f"  Email Alerts: {'Enabled' if config.get('email_alerts', {}).get('enabled') else 'Disabled'}")
        print(f"  External Feed: {'Enabled' if config.get('external_price_feed', {}).get('enabled') else 'Disabled'}")
    except Exception as e:
        print(f"\nError loading config: {e}")

    print("\n" + "=" * 60)
