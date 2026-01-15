"""
Shared infrastructure modules for CALYPSO trading bots.

This package contains common utilities used by all trading strategies:
- saxo_client: Saxo Bank API client for trading operations
- logger_service: Google Sheets and local file logging
- config_loader: Smart config loading (cloud vs local)
- market_hours: US market hours utilities
- secret_manager: GCP Secret Manager interface
- external_price_feed: Yahoo Finance fallback for prices
"""

from shared.saxo_client import SaxoClient, BuySell, OrderType, AssetType
from shared.logger_service import TradeLoggerService, setup_logging, TradeRecord
from shared.config_loader import ConfigLoader, get_config_loader
from shared.market_hours import (
    is_market_open,
    get_market_status_message,
    calculate_sleep_duration,
)
from shared.secret_manager import is_running_on_gcp
from shared.external_price_feed import ExternalPriceFeed

__all__ = [
    # Saxo Client
    'SaxoClient', 'BuySell', 'OrderType', 'AssetType',
    # Logging
    'TradeLoggerService', 'setup_logging', 'TradeRecord',
    # Config
    'ConfigLoader', 'get_config_loader',
    # Market Hours
    'is_market_open', 'get_market_status_message', 'calculate_sleep_duration',
    # Cloud
    'is_running_on_gcp',
    # Price Feed
    'ExternalPriceFeed',
]
