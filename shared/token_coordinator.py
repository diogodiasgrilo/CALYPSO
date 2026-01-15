#!/usr/bin/env python3
"""
Token Coordinator Module

Provides coordinated token refresh for multi-bot environments where multiple
processes share the same Saxo API credentials. Uses file-based locking to
prevent race conditions when refreshing tokens.

The Problem:
- Multiple bots share the same Saxo refresh token in Secret Manager
- Refresh tokens are ONE-TIME USE - once used, they're invalidated
- If Bot A refreshes while Bot B has stale tokens, Bot B gets 401 errors

The Solution:
- File-based lock prevents concurrent refresh attempts
- Local token cache file provides fast reads
- Secret Manager remains source of truth for persistence
- Lock timeout prevents deadlocks

Usage:
    coordinator = TokenCoordinator()
    tokens = coordinator.get_valid_tokens()  # Automatically refreshes if needed

    # Or manually check and refresh
    if coordinator.needs_refresh():
        tokens = coordinator.refresh_tokens(refresh_func)

Author: Trading Bot Developer
Date: 2025
"""

import os
import json
import time
import fcntl
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, Callable

logger = logging.getLogger(__name__)

# Default paths for token coordination
DEFAULT_DATA_DIR = "/opt/calypso/data"
TOKEN_CACHE_FILE = "saxo_token_cache.json"
TOKEN_LOCK_FILE = "saxo_token.lock"

# Lock timeout in seconds (prevent deadlocks)
LOCK_TIMEOUT = 30

# Refresh buffer - refresh token this many seconds before expiry
REFRESH_BUFFER_SECONDS = 120  # 2 minutes before expiry


class TokenCoordinator:
    """
    Coordinates token refresh across multiple bot processes.

    Uses file-based locking to ensure only one process refreshes tokens at a time,
    preventing race conditions that invalidate refresh tokens.
    """

    def __init__(self, data_dir: str = None):
        """
        Initialize the token coordinator.

        Args:
            data_dir: Directory for token cache and lock files.
                     Defaults to /opt/calypso/data or ./data for local dev.
        """
        # Determine data directory
        if data_dir:
            self.data_dir = Path(data_dir)
        elif os.path.exists(DEFAULT_DATA_DIR):
            self.data_dir = Path(DEFAULT_DATA_DIR)
        else:
            # Local development fallback
            self.data_dir = Path("data")

        # Ensure directory exists
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.cache_file = self.data_dir / TOKEN_CACHE_FILE
        self.lock_file = self.data_dir / TOKEN_LOCK_FILE

        # In-memory token cache
        self._cached_tokens: Optional[Dict[str, Any]] = None
        self._cache_loaded_at: Optional[datetime] = None

        logger.info(f"TokenCoordinator initialized with data_dir: {self.data_dir}")

    def _acquire_lock(self, timeout: int = LOCK_TIMEOUT) -> Optional[int]:
        """
        Acquire exclusive lock for token refresh.

        Args:
            timeout: Maximum seconds to wait for lock

        Returns:
            File descriptor if lock acquired, None if timeout
        """
        start_time = time.time()

        # Create lock file if it doesn't exist
        lock_fd = os.open(str(self.lock_file), os.O_CREAT | os.O_RDWR)

        while True:
            try:
                # Try to acquire exclusive lock (non-blocking)
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                logger.debug("Token lock acquired")
                return lock_fd
            except (IOError, OSError):
                # Lock held by another process
                elapsed = time.time() - start_time
                if elapsed >= timeout:
                    logger.warning(f"Failed to acquire token lock after {timeout}s")
                    os.close(lock_fd)
                    return None
                # Wait and retry
                time.sleep(0.1)

    def _release_lock(self, lock_fd: int):
        """Release the token refresh lock."""
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
            logger.debug("Token lock released")
        except Exception as e:
            logger.warning(f"Error releasing token lock: {e}")

    def _read_cache_file(self) -> Optional[Dict[str, Any]]:
        """Read tokens from local cache file."""
        if not self.cache_file.exists():
            return None

        try:
            with open(self.cache_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Error reading token cache file: {e}")
            return None

    def _write_cache_file(self, tokens: Dict[str, Any]):
        """Write tokens to local cache file."""
        try:
            # Write atomically using temp file
            temp_file = self.cache_file.with_suffix('.tmp')
            with open(temp_file, 'w') as f:
                json.dump(tokens, f, indent=2, default=str)

            # Atomic rename
            temp_file.rename(self.cache_file)
            logger.debug("Token cache file updated")
        except Exception as e:
            logger.error(f"Error writing token cache file: {e}")

    def get_cached_tokens(self) -> Optional[Dict[str, Any]]:
        """
        Get tokens from local cache (fast path).

        Returns cached tokens from memory if recently loaded,
        otherwise reads from cache file.
        """
        # Use in-memory cache if fresh (within 10 seconds)
        if (self._cached_tokens and self._cache_loaded_at and
            datetime.now() - self._cache_loaded_at < timedelta(seconds=10)):
            return self._cached_tokens

        # Read from file
        tokens = self._read_cache_file()
        if tokens:
            self._cached_tokens = tokens
            self._cache_loaded_at = datetime.now()

        return tokens

    def is_token_valid(self, tokens: Dict[str, Any] = None) -> bool:
        """
        Check if access token is still valid (with buffer).

        Args:
            tokens: Token dict to check, or None to use cached

        Returns:
            True if token is valid for at least REFRESH_BUFFER_SECONDS
        """
        if tokens is None:
            tokens = self.get_cached_tokens()

        if not tokens:
            return False

        expiry = tokens.get('token_expiry')
        if not expiry:
            return False

        # Parse expiry if it's a string
        if isinstance(expiry, str):
            try:
                expiry = datetime.fromisoformat(expiry.replace('Z', '+00:00'))
            except ValueError:
                return False

        # Check if token expires soon (within buffer)
        now = datetime.now(expiry.tzinfo) if expiry.tzinfo else datetime.now()
        time_until_expiry = (expiry - now).total_seconds()

        return time_until_expiry > REFRESH_BUFFER_SECONDS

    def refresh_with_lock(
        self,
        refresh_func: Callable[[], Optional[Dict[str, Any]]],
        save_to_secret_manager: Callable[[Dict[str, Any]], bool] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Refresh tokens with exclusive lock to prevent race conditions.

        This is the key method that prevents multiple bots from simultaneously
        trying to refresh tokens (which would invalidate each other's refresh tokens).

        Args:
            refresh_func: Function that performs the actual token refresh.
                         Should return new tokens dict or None on failure.
            save_to_secret_manager: Optional function to persist tokens to Secret Manager.

        Returns:
            New tokens dict if refresh successful, None otherwise
        """
        # Acquire exclusive lock
        lock_fd = self._acquire_lock()
        if lock_fd is None:
            logger.error("Could not acquire token lock - another process may be refreshing")
            return None

        try:
            # Re-check cache after acquiring lock (another process may have refreshed)
            cached_tokens = self._read_cache_file()
            if cached_tokens and self.is_token_valid(cached_tokens):
                logger.info("Another process already refreshed tokens - using cached")
                self._cached_tokens = cached_tokens
                self._cache_loaded_at = datetime.now()
                return cached_tokens

            # Perform the actual refresh
            logger.info("Performing token refresh (holding lock)...")
            new_tokens = refresh_func()

            if not new_tokens:
                logger.error("Token refresh function returned None")
                return None

            # Update local cache file
            self._write_cache_file(new_tokens)
            self._cached_tokens = new_tokens
            self._cache_loaded_at = datetime.now()

            # Persist to Secret Manager if provided
            if save_to_secret_manager:
                try:
                    save_to_secret_manager(new_tokens)
                    logger.info("Tokens saved to Secret Manager")
                except Exception as e:
                    logger.error(f"Failed to save tokens to Secret Manager: {e}")

            logger.info("Token refresh completed successfully")
            return new_tokens

        finally:
            self._release_lock(lock_fd)

    def update_cache(self, tokens: Dict[str, Any]):
        """
        Update the token cache with new tokens.

        Call this after successfully loading tokens from Secret Manager
        or after a refresh to ensure all processes see the latest tokens.

        Args:
            tokens: Token dict with access_token, refresh_token, token_expiry
        """
        lock_fd = self._acquire_lock(timeout=5)  # Short timeout for cache update
        if lock_fd is None:
            # Still try to update even without lock
            logger.warning("Could not acquire lock for cache update - updating anyway")

        try:
            self._write_cache_file(tokens)
            self._cached_tokens = tokens
            self._cache_loaded_at = datetime.now()
        finally:
            if lock_fd is not None:
                self._release_lock(lock_fd)

    def clear_cache(self):
        """Clear the local token cache (e.g., after auth failure)."""
        self._cached_tokens = None
        self._cache_loaded_at = None

        if self.cache_file.exists():
            try:
                self.cache_file.unlink()
                logger.info("Token cache cleared")
            except Exception as e:
                logger.warning(f"Error clearing token cache: {e}")


# Global coordinator instance (singleton pattern for shared state)
_coordinator: Optional[TokenCoordinator] = None


def get_token_coordinator(data_dir: str = None) -> TokenCoordinator:
    """
    Get the global token coordinator instance.

    Uses singleton pattern to ensure all code in a process shares
    the same coordinator state.

    Args:
        data_dir: Optional data directory override

    Returns:
        TokenCoordinator instance
    """
    global _coordinator

    if _coordinator is None:
        _coordinator = TokenCoordinator(data_dir)

    return _coordinator
