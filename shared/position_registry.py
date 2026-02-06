"""
Position Registry - Tracks bot ownership of positions.

This module provides thread-safe, file-based tracking of which bot
owns which position. Essential for running multiple bots on the
same underlying (e.g., Iron Fly + MEIC on SPX).

The registry solves the core problem: when calling get_positions(),
Saxo returns ALL account positions with no bot identifier. Without
this registry, two bots trading SPX would see each other's positions
and potentially interfere with each other.

Key features:
- File-based persistence (survives bot restarts)
- File locking for concurrent access (fcntl)
- Automatic orphan cleanup on reconciliation
- Metadata storage for debugging

Usage:
    from shared.position_registry import PositionRegistry

    registry = PositionRegistry("/opt/calypso/data/position_registry.json")

    # Register a new position after order fill
    registry.register(
        position_id="12345678",
        bot_name="IRON_FLY_0DTE",
        strategy_id="iron_fly_20260127_001",
        metadata={"strikes": [5920, 5950, 5950, 5980]}
    )

    # Get only MY positions
    my_positions = registry.get_positions("IRON_FLY_0DTE")

    # Filter Saxo positions to only mine
    all_positions = client.get_positions()
    my_position_ids = registry.get_positions("IRON_FLY_0DTE")
    my_positions = [p for p in all_positions
                    if p["PositionBase"]["PositionId"] in my_position_ids]

    # Unregister on position close
    registry.unregister("12345678")

    # Cleanup orphans (positions in registry but not in Saxo)
    valid_ids = {p["PositionBase"]["PositionId"] for p in client.get_positions()}
    registry.cleanup_orphans(valid_ids)

See: docs/MULTI_BOT_POSITION_MANAGEMENT.md for full design rationale.

Last Updated: 2026-01-27 (Initial implementation)
"""

import json
import fcntl
import os
from datetime import datetime
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, asdict
import logging

logger = logging.getLogger(__name__)


@dataclass
class RegisteredPosition:
    """
    A position registered to a specific bot.

    Attributes:
        position_id: Saxo PositionId (assigned after order fill)
        bot_name: Name of the owning bot (e.g., "IRON_FLY_0DTE", "MEIC")
        strategy_id: Unique identifier for this strategy instance
        registered_at: ISO timestamp when position was registered
        metadata: Optional additional data (strikes, structure type, etc.)
    """
    position_id: str
    bot_name: str
    strategy_id: str
    registered_at: str
    metadata: Dict


class PositionRegistry:
    """
    Thread-safe, file-based position ownership registry.

    Uses file locking (fcntl) to prevent race conditions when multiple
    bots access the registry simultaneously. Each bot runs as a separate
    process on the VM, so file locking is the appropriate synchronization
    mechanism.

    File format (position_registry.json):
    {
        "version": 1,
        "positions": {
            "12345678": {
                "bot_name": "IRON_FLY_0DTE",
                "strategy_id": "iron_fly_20260127_001",
                "registered_at": "2026-01-27T15:00:00Z",
                "metadata": {"strikes": [5920, 5950, 5950, 5980]}
            },
            ...
        }
    }
    """

    # Registry file format version for future migrations
    REGISTRY_VERSION = 1

    def __init__(self, registry_path: str = "/opt/calypso/data/position_registry.json"):
        """
        Initialize the Position Registry.

        Args:
            registry_path: Path to the JSON registry file.
                          Default is the standard VM data directory.
        """
        self.registry_path = registry_path
        self._ensure_registry_exists()

    def _ensure_registry_exists(self):
        """Create registry file if it doesn't exist."""
        if not os.path.exists(self.registry_path):
            # Create directory if needed
            directory = os.path.dirname(self.registry_path)
            if directory and not os.path.exists(directory):
                os.makedirs(directory, exist_ok=True)
                logger.info(f"Created registry directory: {directory}")

            # Create empty registry
            self._write_registry({
                "version": self.REGISTRY_VERSION,
                "positions": {}
            })
            logger.info(f"Created new position registry: {self.registry_path}")

    def _read_registry(self) -> Dict:
        """
        Read registry with shared (read) lock.

        Multiple readers can hold the lock simultaneously,
        but writers will wait for all readers to finish.

        Returns:
            dict: The registry data structure.
        """
        try:
            with open(self.registry_path, 'r') as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)  # Shared lock for reading
                try:
                    data = json.load(f)
                    return data
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except json.JSONDecodeError as e:
            logger.error(f"Registry file corrupted: {e}")
            # Return empty registry - will be fixed on next write
            return {"version": self.REGISTRY_VERSION, "positions": {}}
        except Exception as e:
            logger.error(f"Error reading registry: {e}")
            return {"version": self.REGISTRY_VERSION, "positions": {}}

    def _write_registry(self, data: Dict):
        """
        Write registry with exclusive (write) lock.

        Only one writer can hold the lock at a time.
        Writers wait for all readers and other writers to finish.

        Args:
            data: The complete registry data structure to write.
        """
        try:
            # Open with 'r+' for atomic update, but create if doesn't exist
            mode = 'r+' if os.path.exists(self.registry_path) else 'w'
            with open(self.registry_path, mode) as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)  # Exclusive lock for writing
                try:
                    f.seek(0)
                    json.dump(data, f, indent=2)
                    f.truncate()  # Remove any leftover content
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception as e:
            logger.error(f"Error writing registry: {e}")
            raise

    def register(
        self,
        position_id: str,
        bot_name: str,
        strategy_id: str,
        metadata: Optional[Dict] = None
    ) -> bool:
        """
        Register a position to a bot.

        Called after an order fills and we know the PositionId.
        The ExternalReference on the order links to our strategy_id,
        and we use the PositionId (from activities) as the registry key.

        Args:
            position_id: Saxo PositionId (from order activity after fill)
            bot_name: Name of the bot (e.g., "IRON_FLY_0DTE", "MEIC", "DELTA_NEUTRAL")
            strategy_id: Unique identifier for this strategy instance
                        (e.g., "iron_fly_20260127_001" or "meic_20260127_entry3")
            metadata: Optional additional data for debugging/tracking
                     (e.g., {"strikes": [5920, 5950, 5950, 5980], "structure": "iron_fly"})

        Returns:
            True if registered successfully.
            False if position already registered to a DIFFERENT bot (conflict).

        Raises:
            Exception if file write fails.
        """
        registry = self._read_registry()

        # Check for existing registration
        if position_id in registry["positions"]:
            existing = registry["positions"][position_id]
            if existing["bot_name"] != bot_name:
                # CONFLICT: Another bot owns this position
                logger.error(
                    f"CONFLICT: Position {position_id} already registered to {existing['bot_name']}, "
                    f"cannot register to {bot_name}"
                )
                return False
            # Already registered to same bot - that's fine
            logger.debug(f"Position {position_id} already registered to {bot_name}")
            return True

        # Register new position
        registry["positions"][position_id] = {
            "bot_name": bot_name,
            "strategy_id": strategy_id,
            "registered_at": datetime.utcnow().isoformat() + "Z",
            "metadata": metadata or {}
        }

        self._write_registry(registry)
        logger.info(
            f"Registered position {position_id} to {bot_name} "
            f"(strategy: {strategy_id})"
        )
        return True

    def unregister(self, position_id: str) -> bool:
        """
        Unregister a position (typically after closing it).

        Args:
            position_id: Saxo PositionId to unregister

        Returns:
            True if unregistered successfully.
            False if position was not in registry.
        """
        registry = self._read_registry()

        if position_id not in registry["positions"]:
            logger.warning(f"Position {position_id} not found in registry")
            return False

        # Get details for logging before removal
        details = registry["positions"][position_id]
        del registry["positions"][position_id]

        self._write_registry(registry)
        logger.info(
            f"Unregistered position {position_id} from {details['bot_name']} "
            f"(was strategy: {details['strategy_id']})"
        )
        return True

    def get_positions(self, bot_name: str) -> Set[str]:
        """
        Get all position IDs registered to a specific bot.

        Use this to filter get_positions() results from Saxo:

            my_position_ids = registry.get_positions("IRON_FLY_0DTE")
            all_positions = client.get_positions()
            my_positions = [p for p in all_positions
                          if p["PositionBase"]["PositionId"] in my_position_ids]

        Args:
            bot_name: Name of the bot (e.g., "IRON_FLY_0DTE", "MEIC")

        Returns:
            Set of PositionIds owned by this bot (may be empty).
        """
        registry = self._read_registry()
        return {
            pos_id for pos_id, data in registry["positions"].items()
            if data["bot_name"] == bot_name
        }

    def get_all_registered(self) -> Set[str]:
        """
        Get all registered position IDs across all bots.

        Useful for identifying unregistered positions (positions in Saxo
        but not in any bot's registry - possible manual trades).

        Returns:
            Set of all registered PositionIds.
        """
        registry = self._read_registry()
        return set(registry["positions"].keys())

    def get_owner(self, position_id: str) -> Optional[str]:
        """
        Get the bot that owns a specific position.

        Args:
            position_id: Saxo PositionId

        Returns:
            Bot name (e.g., "IRON_FLY_0DTE") or None if not registered.
        """
        registry = self._read_registry()
        if position_id in registry["positions"]:
            return registry["positions"][position_id]["bot_name"]
        return None

    def get_position_info(self, position_id: str) -> Optional[Dict]:
        """
        Get full registration info for a specific position.

        Fix #45: Added to support merged position handling during stop losses.
        Returns the complete registration data including metadata (which may
        contain shared_entries list for merged positions).

        Args:
            position_id: Saxo PositionId

        Returns:
            Dict with bot_name, strategy_id, registered_at, metadata,
            or None if not registered.
        """
        registry = self._read_registry()
        if position_id in registry["positions"]:
            return registry["positions"][position_id]
        return None

    def get_position_details(self, position_id: str) -> Optional[Dict]:
        """
        Get full registration details for a position.

        Args:
            position_id: Saxo PositionId

        Returns:
            dict with bot_name, strategy_id, registered_at, metadata
            or None if not registered.
        """
        registry = self._read_registry()
        return registry["positions"].get(position_id)

    def is_registered(self, position_id: str) -> bool:
        """
        Check if a position is registered to any bot.

        Args:
            position_id: Saxo PositionId

        Returns:
            True if registered, False otherwise.
        """
        registry = self._read_registry()
        return position_id in registry["positions"]

    def cleanup_orphans(self, valid_position_ids: Set[str]) -> List[str]:
        """
        Remove registry entries for positions that no longer exist in Saxo.

        Call this during bot startup to clean up stale entries from
        positions that were closed while the bot was offline.

        Example:
            all_positions = client.get_positions()
            valid_ids = {p["PositionBase"]["PositionId"] for p in all_positions}
            orphans = registry.cleanup_orphans(valid_ids)
            if orphans:
                logger.warning(f"Cleaned up {len(orphans)} orphaned registrations")

        Args:
            valid_position_ids: Set of PositionIds that currently exist in Saxo

        Returns:
            List of orphaned PositionIds that were removed from registry.
        """
        registry = self._read_registry()
        orphans = []

        for pos_id in list(registry["positions"].keys()):
            if pos_id not in valid_position_ids:
                orphans.append(pos_id)
                details = registry["positions"][pos_id]
                logger.info(
                    f"Removing orphaned registration: {pos_id} "
                    f"(was {details['bot_name']}/{details['strategy_id']})"
                )
                del registry["positions"][pos_id]

        if orphans:
            self._write_registry(registry)
            logger.info(f"Cleaned up {len(orphans)} orphaned positions: {orphans}")

        return orphans

    def get_positions_by_strategy(self, strategy_id: str) -> Set[str]:
        """
        Get all position IDs for a specific strategy instance.

        Useful for closing all legs of a multi-leg position.

        Args:
            strategy_id: Strategy identifier (e.g., "iron_fly_20260127_001")

        Returns:
            Set of PositionIds belonging to this strategy.
        """
        registry = self._read_registry()
        return {
            pos_id for pos_id, data in registry["positions"].items()
            if data["strategy_id"] == strategy_id
        }

    def get_registry_stats(self) -> Dict:
        """
        Get statistics about the registry for monitoring/debugging.

        Returns:
            dict with counts by bot and total positions.
        """
        registry = self._read_registry()
        positions = registry["positions"]

        stats = {
            "total_positions": len(positions),
            "by_bot": {},
            "version": registry.get("version", 0)
        }

        for pos_id, data in positions.items():
            bot_name = data["bot_name"]
            if bot_name not in stats["by_bot"]:
                stats["by_bot"][bot_name] = 0
            stats["by_bot"][bot_name] += 1

        return stats

    def dump_registry(self) -> Dict:
        """
        Get the complete registry data for debugging.

        Returns:
            The raw registry data structure.
        """
        return self._read_registry()
