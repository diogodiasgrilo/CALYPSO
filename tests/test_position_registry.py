"""
Unit tests for the Position Registry module.

The Position Registry tracks which bot owns which position, enabling
multiple bots to trade the same underlying without conflicts.

Run tests with: python -m pytest tests/test_position_registry.py -v

Last Updated: 2026-01-27 (Initial implementation)
"""

import os
import json
import tempfile
import pytest
from datetime import datetime
from unittest.mock import patch

# Add parent directory to path for imports
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.position_registry import PositionRegistry


class TestPositionRegistry:
    """Test suite for PositionRegistry class."""

    @pytest.fixture
    def temp_registry_path(self):
        """Create a temporary file path for the registry."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            temp_path = f.name
        yield temp_path
        # Cleanup
        if os.path.exists(temp_path):
            os.remove(temp_path)

    @pytest.fixture
    def registry(self, temp_registry_path):
        """Create a fresh registry instance for each test."""
        return PositionRegistry(temp_registry_path)

    # === Basic Operations ===

    def test_create_new_registry(self, temp_registry_path):
        """Test that a new registry file is created if it doesn't exist."""
        # Remove file if it exists
        if os.path.exists(temp_registry_path):
            os.remove(temp_registry_path)

        # Create registry - should create the file
        registry = PositionRegistry(temp_registry_path)

        assert os.path.exists(temp_registry_path)
        with open(temp_registry_path, 'r') as f:
            data = json.load(f)
        assert data["version"] == 1
        assert data["positions"] == {}

    def test_register_position(self, registry):
        """Test registering a new position."""
        result = registry.register(
            position_id="12345678",
            bot_name="IRON_FLY_0DTE",
            strategy_id="iron_fly_20260127_001",
            metadata={"strikes": [5920, 5950, 5950, 5980]}
        )

        assert result is True
        assert registry.is_registered("12345678")
        assert registry.get_owner("12345678") == "IRON_FLY_0DTE"

    def test_register_position_no_metadata(self, registry):
        """Test registering a position without metadata."""
        result = registry.register(
            position_id="12345678",
            bot_name="MEIC",
            strategy_id="meic_entry1"
        )

        assert result is True
        details = registry.get_position_details("12345678")
        assert details["metadata"] == {}

    def test_register_duplicate_same_bot(self, registry):
        """Test that re-registering the same position to the same bot succeeds."""
        registry.register("12345678", "IRON_FLY_0DTE", "strategy1")

        # Register again with same bot
        result = registry.register("12345678", "IRON_FLY_0DTE", "strategy1")

        assert result is True  # Should succeed

    def test_register_duplicate_different_bot(self, registry):
        """Test that registering a position to a different bot fails."""
        registry.register("12345678", "IRON_FLY_0DTE", "strategy1")

        # Try to register same position to different bot
        result = registry.register("12345678", "MEIC", "strategy2")

        assert result is False  # Should fail - conflict
        assert registry.get_owner("12345678") == "IRON_FLY_0DTE"  # Original owner unchanged

    def test_unregister_position(self, registry):
        """Test unregistering a position."""
        registry.register("12345678", "IRON_FLY_0DTE", "strategy1")

        result = registry.unregister("12345678")

        assert result is True
        assert not registry.is_registered("12345678")
        assert registry.get_owner("12345678") is None

    def test_unregister_nonexistent(self, registry):
        """Test unregistering a position that doesn't exist."""
        result = registry.unregister("nonexistent")

        assert result is False

    # === Query Operations ===

    def test_get_positions_single_bot(self, registry):
        """Test getting positions for a specific bot."""
        registry.register("pos1", "IRON_FLY_0DTE", "strategy1")
        registry.register("pos2", "IRON_FLY_0DTE", "strategy1")
        registry.register("pos3", "MEIC", "meic_strategy")

        iron_fly_positions = registry.get_positions("IRON_FLY_0DTE")

        assert iron_fly_positions == {"pos1", "pos2"}

    def test_get_positions_empty(self, registry):
        """Test getting positions for a bot with no positions."""
        registry.register("pos1", "MEIC", "strategy1")

        positions = registry.get_positions("IRON_FLY_0DTE")

        assert positions == set()

    def test_get_all_registered(self, registry):
        """Test getting all registered positions across all bots."""
        registry.register("pos1", "IRON_FLY_0DTE", "strategy1")
        registry.register("pos2", "MEIC", "strategy2")
        registry.register("pos3", "DELTA_NEUTRAL", "strategy3")

        all_positions = registry.get_all_registered()

        assert all_positions == {"pos1", "pos2", "pos3"}

    def test_get_position_details(self, registry):
        """Test getting full details for a position."""
        registry.register(
            position_id="12345678",
            bot_name="IRON_FLY_0DTE",
            strategy_id="iron_fly_20260127_001",
            metadata={"strikes": [5920, 5950, 5950, 5980], "structure": "iron_fly"}
        )

        details = registry.get_position_details("12345678")

        assert details["bot_name"] == "IRON_FLY_0DTE"
        assert details["strategy_id"] == "iron_fly_20260127_001"
        assert details["metadata"]["strikes"] == [5920, 5950, 5950, 5980]
        assert "registered_at" in details
        # Verify timestamp format
        datetime.fromisoformat(details["registered_at"].replace("Z", "+00:00"))

    def test_get_positions_by_strategy(self, registry):
        """Test getting positions by strategy ID."""
        # One strategy with 4 legs
        registry.register("leg1", "IRON_FLY_0DTE", "iron_fly_20260127_001")
        registry.register("leg2", "IRON_FLY_0DTE", "iron_fly_20260127_001")
        registry.register("leg3", "IRON_FLY_0DTE", "iron_fly_20260127_001")
        registry.register("leg4", "IRON_FLY_0DTE", "iron_fly_20260127_001")
        # Different strategy
        registry.register("other", "IRON_FLY_0DTE", "iron_fly_20260127_002")

        positions = registry.get_positions_by_strategy("iron_fly_20260127_001")

        assert positions == {"leg1", "leg2", "leg3", "leg4"}

    # === Cleanup Operations ===

    def test_cleanup_orphans(self, registry):
        """Test cleaning up positions that no longer exist in Saxo."""
        registry.register("still_exists", "IRON_FLY_0DTE", "strategy1")
        registry.register("closed_position", "IRON_FLY_0DTE", "strategy1")
        registry.register("also_closed", "MEIC", "strategy2")

        # Simulate that only "still_exists" is in Saxo now
        valid_ids = {"still_exists", "new_position"}

        orphans = registry.cleanup_orphans(valid_ids)

        assert set(orphans) == {"closed_position", "also_closed"}
        assert registry.is_registered("still_exists")
        assert not registry.is_registered("closed_position")
        assert not registry.is_registered("also_closed")

    def test_cleanup_no_orphans(self, registry):
        """Test cleanup when there are no orphans."""
        registry.register("pos1", "IRON_FLY_0DTE", "strategy1")
        registry.register("pos2", "MEIC", "strategy2")

        valid_ids = {"pos1", "pos2", "pos3"}  # All registered positions are valid

        orphans = registry.cleanup_orphans(valid_ids)

        assert orphans == []
        assert registry.is_registered("pos1")
        assert registry.is_registered("pos2")

    # === Statistics ===

    def test_get_registry_stats(self, registry):
        """Test getting registry statistics."""
        registry.register("pos1", "IRON_FLY_0DTE", "strategy1")
        registry.register("pos2", "IRON_FLY_0DTE", "strategy1")
        registry.register("pos3", "MEIC", "strategy2")
        registry.register("pos4", "DELTA_NEUTRAL", "strategy3")

        stats = registry.get_registry_stats()

        assert stats["total_positions"] == 4
        assert stats["by_bot"]["IRON_FLY_0DTE"] == 2
        assert stats["by_bot"]["MEIC"] == 1
        assert stats["by_bot"]["DELTA_NEUTRAL"] == 1
        assert stats["version"] == 1

    def test_dump_registry(self, registry):
        """Test dumping the raw registry data."""
        registry.register("pos1", "IRON_FLY_0DTE", "strategy1", {"key": "value"})

        dump = registry.dump_registry()

        assert "version" in dump
        assert "positions" in dump
        assert "pos1" in dump["positions"]
        assert dump["positions"]["pos1"]["metadata"]["key"] == "value"

    # === Persistence ===

    def test_persistence_across_instances(self, temp_registry_path):
        """Test that registry data persists across instances."""
        # First instance registers positions
        registry1 = PositionRegistry(temp_registry_path)
        registry1.register("pos1", "IRON_FLY_0DTE", "strategy1")
        registry1.register("pos2", "MEIC", "strategy2")

        # Second instance should see the same data
        registry2 = PositionRegistry(temp_registry_path)

        assert registry2.is_registered("pos1")
        assert registry2.is_registered("pos2")
        assert registry2.get_owner("pos1") == "IRON_FLY_0DTE"
        assert registry2.get_owner("pos2") == "MEIC"

    def test_corrupted_registry_recovery(self, temp_registry_path):
        """Test that a corrupted registry is handled gracefully."""
        # Write invalid JSON
        with open(temp_registry_path, 'w') as f:
            f.write("{ invalid json }")

        # Registry should handle this gracefully
        registry = PositionRegistry(temp_registry_path)

        # Should be able to register new positions (empty registry)
        result = registry.register("pos1", "IRON_FLY_0DTE", "strategy1")
        assert result is True

    # === Multi-Bot Scenarios ===

    def test_multi_bot_isolation(self, registry):
        """Test that multiple bots can operate independently."""
        # Iron Fly registers 4 legs
        registry.register("if_leg1", "IRON_FLY_0DTE", "iron_fly_001")
        registry.register("if_leg2", "IRON_FLY_0DTE", "iron_fly_001")
        registry.register("if_leg3", "IRON_FLY_0DTE", "iron_fly_001")
        registry.register("if_leg4", "IRON_FLY_0DTE", "iron_fly_001")

        # MEIC registers 4 legs for first IC
        registry.register("meic_leg1", "MEIC", "meic_entry1")
        registry.register("meic_leg2", "MEIC", "meic_entry1")
        registry.register("meic_leg3", "MEIC", "meic_entry1")
        registry.register("meic_leg4", "MEIC", "meic_entry1")

        # Each bot should only see its own positions
        iron_fly_positions = registry.get_positions("IRON_FLY_0DTE")
        meic_positions = registry.get_positions("MEIC")

        assert len(iron_fly_positions) == 4
        assert len(meic_positions) == 4
        assert iron_fly_positions.isdisjoint(meic_positions)  # No overlap

    def test_simulated_position_lifecycle(self, registry):
        """Test a complete position lifecycle: open, monitor, close."""
        # 1. Open position (after order fill)
        registry.register(
            position_id="12345678",
            bot_name="IRON_FLY_0DTE",
            strategy_id="iron_fly_20260127_001",
            metadata={"strikes": [5920, 5950, 5950, 5980]}
        )

        # 2. Check ownership during monitoring
        assert registry.get_owner("12345678") == "IRON_FLY_0DTE"
        assert "12345678" in registry.get_positions("IRON_FLY_0DTE")

        # 3. Close position
        registry.unregister("12345678")

        # 4. Verify cleanup
        assert not registry.is_registered("12345678")
        assert "12345678" not in registry.get_positions("IRON_FLY_0DTE")

    def test_bot_restart_reconciliation(self, registry):
        """Test reconciliation after a bot restart."""
        # Setup: Bot had positions before restart
        registry.register("pos1", "IRON_FLY_0DTE", "strategy1")
        registry.register("pos2", "IRON_FLY_0DTE", "strategy1")
        registry.register("pos3", "MEIC", "meic_strategy")

        # Simulate: pos1 was closed while bot was offline
        # Current Saxo positions after restart
        valid_saxo_ids = {"pos2", "pos3", "new_unregistered_pos"}

        # Bot reconciles on startup
        orphans = registry.cleanup_orphans(valid_saxo_ids)

        # pos1 should be cleaned up
        assert "pos1" in orphans
        assert not registry.is_registered("pos1")

        # pos2 and pos3 should still be registered
        assert registry.is_registered("pos2")
        assert registry.is_registered("pos3")


class TestEdgeCases:
    """Test edge cases and error handling."""

    @pytest.fixture
    def temp_registry_path(self):
        """Create a temporary file path for the registry."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            temp_path = f.name
        yield temp_path
        if os.path.exists(temp_path):
            os.remove(temp_path)

    @pytest.fixture
    def registry(self, temp_registry_path):
        """Create a fresh registry instance for each test."""
        return PositionRegistry(temp_registry_path)

    def test_special_characters_in_position_id(self, registry):
        """Test handling of special characters in position IDs."""
        # Saxo position IDs are typically numeric, but test edge case
        result = registry.register("pos-123_test", "IRON_FLY_0DTE", "strategy1")
        assert result is True
        assert registry.get_owner("pos-123_test") == "IRON_FLY_0DTE"

    def test_unicode_in_metadata(self, registry):
        """Test handling of unicode characters in metadata."""
        result = registry.register(
            position_id="12345678",
            bot_name="IRON_FLY_0DTE",
            strategy_id="strategy1",
            metadata={"note": "Preisueberschreitung", "emoji": "📈"}
        )
        assert result is True
        details = registry.get_position_details("12345678")
        assert "📈" in details["metadata"]["emoji"]

    def test_large_metadata(self, registry):
        """Test handling of large metadata objects."""
        large_metadata = {
            "strikes": [5920, 5950, 5950, 5980],
            "greeks": {"delta": 0.5, "gamma": 0.01, "theta": -0.05, "vega": 0.1},
            "fill_prices": [15.50, 20.00, 20.00, 25.00],
            "notes": "x" * 1000  # Large string
        }
        result = registry.register(
            position_id="12345678",
            bot_name="IRON_FLY_0DTE",
            strategy_id="strategy1",
            metadata=large_metadata
        )
        assert result is True

    def test_many_positions(self, registry):
        """Test handling of many positions (MEIC has 6 ICs = 24 legs)."""
        # Register 100 positions
        for i in range(100):
            registry.register(f"pos_{i:04d}", "MEIC", f"meic_entry_{i // 4}")

        positions = registry.get_positions("MEIC")
        assert len(positions) == 100

        stats = registry.get_registry_stats()
        assert stats["total_positions"] == 100

    def test_empty_bot_name(self, registry):
        """Test behavior with empty bot name."""
        # Should still work, though not recommended
        result = registry.register("pos1", "", "strategy1")
        assert result is True
        assert registry.get_owner("pos1") == ""

    def test_get_positions_for_nonexistent_bot(self, registry):
        """Test getting positions for a bot that has never registered anything."""
        positions = registry.get_positions("NONEXISTENT_BOT")
        assert positions == set()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
