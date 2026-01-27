# Multi-Bot Position Management: Same Underlying Asset

**Last Updated:** 2026-01-27
**Purpose:** Technical analysis of running multiple bots on the same underlying (e.g., Iron Fly + MEIC on SPX)
**Status:** Design Document - Implementation Required Before Multi-SPX Deployment

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [The Problem](#the-problem)
3. [Current Isolation Mechanism](#current-isolation-mechanism)
4. [Saxo Bank API Capabilities](#saxo-bank-api-capabilities)
5. [Conflict Scenarios](#conflict-scenarios)
6. [Solution Options](#solution-options)
7. [Recommended Solution: Position Registry](#recommended-solution-position-registry)
8. [Implementation Specification](#implementation-specification)
9. [Testing Strategy](#testing-strategy)
10. [Migration Path](#migration-path)

---

## Executive Summary

### The Core Problem

**When two bots trade the same underlying (e.g., Iron Fly and MEIC both on SPX 0DTE), they cannot distinguish their own positions from each other's.**

| Current State | Safe? |
|---------------|-------|
| Iron Fly (SPX) + Delta Neutral (SPY) | ✅ YES - Different underlyings |
| Iron Fly (SPX) + Rolling Put Diagonal (QQQ) | ✅ YES - Different underlyings |
| Iron Fly (SPX) + MEIC (SPX) | ❌ **NO - Same underlying!** |
| MEIC (SPX) + METF (SPX) | ❌ **NO - Same underlying!** |

### Key Finding: Saxo API Supports ExternalReference

Saxo Bank's OpenAPI supports an `ExternalReference` field (up to 50 characters) that can be included when placing orders. **However, this field does NOT persist to position data** - it only appears in order data and activities.

### Recommended Solution

Implement a **Position Registry** - a shared file that tracks which bot owns which position, using PositionIds assigned by Saxo after order fills.

---

## The Problem

### Current Position Retrieval

When any bot calls `get_positions()`, Saxo returns **ALL positions** for the account:

```python
# This returns EVERYTHING in the account
positions = client.get_positions()

# Returns:
[
    {"PositionBase": {"PositionId": "123", "Uic": 128, "Symbol": "SPX/27Jan26C5950"}},  # Iron Fly leg 1
    {"PositionBase": {"PositionId": "124", "Uic": 128, "Symbol": "SPX/27Jan26P5950"}},  # Iron Fly leg 2
    {"PositionBase": {"PositionId": "125", "Uic": 128, "Symbol": "SPX/27Jan26C5980"}},  # Iron Fly leg 3
    {"PositionBase": {"PositionId": "126", "Uic": 128, "Symbol": "SPX/27Jan26P5920"}},  # Iron Fly leg 4
    {"PositionBase": {"PositionId": "127", "Uic": 128, "Symbol": "SPX/27Jan26C6000"}},  # MEIC IC leg 1
    {"PositionBase": {"PositionId": "128", "Uic": 128, "Symbol": "SPX/27Jan26C6050"}},  # MEIC IC leg 2
    {"PositionBase": {"PositionId": "129", "Uic": 128, "Symbol": "SPX/27Jan26P5850"}},  # MEIC IC leg 3
    {"PositionBase": {"PositionId": "130", "Uic": 128, "Symbol": "SPX/27Jan26P5800"}},  # MEIC IC leg 4
]
```

**Problem:** There is NO field in position data indicating which bot opened each position.

### Why This Is Dangerous

1. **Accidental Position Closure:**
   - Iron Fly bot sees 8 SPX options
   - Detects "multiple iron flies" (POS-004)
   - Uses price proximity to select which to manage
   - **Could close MEIC's position thinking it's an orphan**

2. **Position Counting Errors:**
   - MEIC expects to manage 6 iron condors throughout the day
   - Sees Iron Fly's positions mixed in
   - **Might think it already has positions when it doesn't**

3. **Stop Loss Confusion:**
   - Both bots monitoring SPX price
   - Iron Fly wing at 5920, MEIC put spread at 5850
   - **One bot might trigger stop for other bot's position**

---

## Current Isolation Mechanism

### How Bots Currently Avoid Conflicts

Each bot filters positions by **underlying symbol**:

```python
# Iron Fly - bots/iron_fly_0dte/strategy.py:1908-1919
def _filter_spx_options(self, positions):
    return [p for p in positions
            if "SPX" in p.get("DisplayAndFormat", {}).get("Symbol", "")
            or "SPXW" in p.get("DisplayAndFormat", {}).get("Symbol", "")]

# Delta Neutral - bots/delta_neutral/strategy.py:3557-3581
def _filter_spy_options(self, positions):
    return [p for p in positions
            if "SPY" in p.get("DisplayAndFormat", {}).get("Symbol", "").upper()]

# Rolling Put Diagonal - bots/rolling_put_diagonal/strategy.py:2758-2780
def _filter_qqq_options(self, positions):
    return [p for p in positions
            if p.get("DisplayAndFormat", {}).get("Symbol", "").upper().startswith("QQQ/")]
```

### Why This Works Today

| Bot | Underlying | Symbol Filter | Conflicts With |
|-----|------------|---------------|----------------|
| Iron Fly | SPX/SPXW | "SPX" in symbol | MEIC, METF, SPX Put Credit |
| Delta Neutral | SPY | "SPY" in symbol | Future SPY bots |
| Rolling Put Diagonal | QQQ | "QQQ/" prefix | Future QQQ bots |

**Current bots use different underlyings** - they never see each other's positions.

### What Breaks When We Add MEIC

MEIC also trades SPX 0DTE. Both Iron Fly and MEIC would pass the "SPX" symbol filter:

```python
# Iron Fly filter
positions = _filter_spx_options(all_positions)
# Returns: [Iron Fly legs] + [MEIC legs] - CAN'T DISTINGUISH!

# MEIC filter (would use same logic)
positions = _filter_spx_options(all_positions)
# Returns: [Iron Fly legs] + [MEIC legs] - CAN'T DISTINGUISH!
```

---

## Saxo Bank API Capabilities

### ExternalReference Field

Saxo Bank's `/trade/v2/orders` endpoint supports an **ExternalReference** field:

| Attribute | Value |
|-----------|-------|
| Field Name | `ExternalReference` |
| Max Length | 50 characters |
| Purpose | Client-defined order identifier |
| Uniqueness | NOT enforced (client responsibility) |
| Persistence | Order data and activities only |

**Source:** [Saxo Bank Support - How do I label orders with a client-defined order ID?](https://openapi.help.saxo/hc/en-us/articles/4418504615057-How-do-I-label-orders-with-a-client-defined-order-ID)

### How to Use ExternalReference

```python
# When placing an order
order_data = {
    "AccountKey": self.account_key,
    "Uic": uic,
    "AssetType": "StockIndexOption",
    "BuySell": "Buy",
    "Amount": 1,
    "OrderType": "Market",
    "ExternalReference": "IRON_FLY_0DTE_20260127_001"  # Bot identifier!
}
```

### Critical Limitation: ExternalReference Does NOT Appear in Positions

| Endpoint | ExternalReference Available? |
|----------|------------------------------|
| `POST /trade/v2/orders` (place order) | ✅ YES - can set |
| `GET /port/v1/orders` (view orders) | ✅ YES - returned |
| `GET /cs/v1/audit/orderactivities` | ✅ YES - returned |
| `GET /port/v1/positions` | ❌ **NO - NOT AVAILABLE** |

**This is the key problem:** Even if we tag orders with `ExternalReference`, the resulting positions do NOT contain that tag. We cannot query positions by ExternalReference.

### What Positions DO Return

```json
{
  "PositionBase": {
    "PositionId": "12345678",      // Unique per position
    "Uic": 128,                     // Instrument code
    "AssetType": "StockIndexOption",
    "Amount": 1,
    "ExecutionTimeOpen": "2026-01-27T15:00:00Z",
    "OpenPrice": 15.50
  },
  "DisplayAndFormat": {
    "Symbol": "SPX:xcbf/27Jan26C5950",
    "Description": "SPX Jan 27, 2026 5950 Call"
  }
}
```

**No ExternalReference, no bot ID, no custom metadata.**

### Activities Endpoint: Linking Orders to Positions

The `/cs/v1/audit/orderactivities` endpoint provides a way to link:

```json
{
  "ActivityTime": "2026-01-27T15:00:00Z",
  "OrderId": "5002749003",
  "ExternalReference": "IRON_FLY_0DTE_20260127_001",
  "Status": "FinalFill",
  "PositionId": "12345678"  // Links order to position!
}
```

**This creates a path:** Order (with ExternalReference) → Activity → PositionId

---

## Conflict Scenarios

### Scenario 1: Startup Race Condition

```
09:30:00 - Market opens
09:30:01 - Iron Fly starts, calls get_positions()
09:30:01 - MEIC starts, calls get_positions()
09:30:02 - Both see 0 SPX positions
09:30:03 - Iron Fly opens 4-leg position
09:30:03 - MEIC opens 4-leg position (entry #1)
09:30:04 - Iron Fly calls get_positions(), sees 8 legs
09:30:04 - DETECTS "multiple iron flies" - CONFUSION!
```

### Scenario 2: One Bot Closes Other Bot's Position

```
10:30:00 - Iron Fly has position at strikes 5920/5950/5950/5980
10:30:01 - MEIC has IC at strikes 5900/5925/5975/6000
10:30:02 - SPX drops to 5922
10:30:03 - Iron Fly wing touch! Initiates close
10:30:04 - Iron Fly queries positions, gets 8 legs
10:30:05 - Identifies positions by strike matching
10:30:06 - ACCIDENTALLY closes MEIC's 5925 put (close strike!)
```

### Scenario 3: Orphan Detection False Positive

```
11:00:00 - MEIC has 6 iron condors open (24 legs total)
11:00:01 - Iron Fly has 1 iron fly open (4 legs)
11:01:00 - Iron Fly bot restarts (crash recovery)
11:01:01 - Calls get_positions(), sees 28 SPX legs
11:01:02 - Tries to identify its position by structure
11:01:03 - Finds 7 possible "iron fly-like" structures
11:01:04 - MARKS 6 of them as orphans for cleanup!
```

---

## Solution Options

### Option A: Different Strike Ranges (Partial Solution)

**Concept:** Configure bots to use non-overlapping strike ranges.

```python
# Iron Fly config
strike_range = "ATM"  # Uses exactly ATM strike

# MEIC config
call_spread_min_distance = 30  # At least 30 points OTM
put_spread_min_distance = 30
```

**Pros:**
- No code changes to shared modules
- Simple configuration

**Cons:**
- Reduces strategy flexibility
- Doesn't solve startup race conditions
- Doesn't prevent orphan detection issues
- **Not a complete solution**

### Option B: Timing Separation (Partial Solution)

**Concept:** Configure bots to trade at different times.

```python
# Iron Fly config
entry_window_start = "10:00"
entry_window_end = "10:30"

# MEIC config
entry_times = ["10:30", "11:00", "11:30", "12:00", "12:30", "13:00"]
```

**Pros:**
- Reduces overlap
- Simple configuration

**Cons:**
- Doesn't prevent position confusion after both have positions
- Limits strategy effectiveness
- **Not a complete solution**

### Option C: Separate Saxo Accounts (Complete but Expensive)

**Concept:** Run each SPX bot on a different Saxo account.

**Pros:**
- Complete isolation - `get_positions()` returns only that account's positions
- No code changes needed
- Zero risk of cross-contamination

**Cons:**
- Requires additional Saxo account(s)
- Separate capital allocation
- Separate OAuth token management
- More complex monitoring
- **Additional account fees**

### Option D: Position Registry (Recommended)

**Concept:** Maintain a shared registry file that tracks which bot owns which position.

```json
// /data/position_registry.json
{
  "version": 1,
  "positions": {
    "12345678": {
      "bot_name": "IRON_FLY_0DTE",
      "opened_at": "2026-01-27T15:00:00Z",
      "strategy_id": "iron_fly_20260127_001",
      "structure": "iron_fly",
      "strikes": [5920, 5950, 5950, 5980]
    },
    "12345679": {
      "bot_name": "MEIC",
      "opened_at": "2026-01-27T15:30:00Z",
      "strategy_id": "meic_20260127_entry1",
      "structure": "iron_condor",
      "strikes": [5850, 5875, 5975, 6000]
    }
  }
}
```

**Pros:**
- Complete isolation with minimal overhead
- Works with existing Saxo account
- Enables future multi-bot scenarios
- Audit trail of position ownership
- Survives bot restarts

**Cons:**
- Requires implementation effort
- Must handle file locking for concurrent access
- Must sync with actual Saxo positions

---

## Recommended Solution: Position Registry

### Why Position Registry?

1. **Scalability:** Works for any number of bots on any underlying
2. **Reliability:** File-based persistence survives restarts
3. **Auditability:** Full history of position ownership
4. **Compatibility:** Works with existing Saxo API (no API changes needed)
5. **Cost:** No additional accounts or fees

### Architecture Overview

```
┌─────────────┐     ┌─────────────┐     ┌──────────────────┐
│  Iron Fly   │     │    MEIC     │     │     METF         │
│    Bot      │     │    Bot      │     │     Bot          │
└──────┬──────┘     └──────┬──────┘     └───────┬──────────┘
       │                   │                     │
       └───────────────────┼─────────────────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │   Position Registry    │
              │  /data/position_       │
              │     registry.json      │
              └───────────┬────────────┘
                          │
                          ▼
              ┌────────────────────────┐
              │    Saxo Bank API       │
              │   get_positions()      │
              └────────────────────────┘
```

### Workflow

**1. On Order Placement:**
```python
# Bot places order with ExternalReference
order_response = client.place_order(
    ...,
    external_reference=f"{bot_name}_{strategy_id}_{timestamp}"
)
order_id = order_response["OrderId"]

# Wait for fill and get PositionId from activities
activity = client.get_order_activity(order_id)
position_id = activity["PositionId"]

# Register position ownership
registry.register_position(
    position_id=position_id,
    bot_name=self.bot_name,
    strategy_id=strategy_id,
    metadata={...}
)
```

**2. On Position Query:**
```python
# Get all positions from Saxo
all_positions = client.get_positions()

# Filter to only MY positions using registry
my_position_ids = registry.get_positions_for_bot(self.bot_name)
my_positions = [p for p in all_positions
                if p["PositionBase"]["PositionId"] in my_position_ids]
```

**3. On Position Close:**
```python
# Close position
client.close_position(position_id)

# Unregister from registry
registry.unregister_position(position_id)
```

**4. On Bot Startup (Reconciliation):**
```python
# Get all positions from Saxo
all_positions = client.get_positions()
all_saxo_ids = {p["PositionBase"]["PositionId"] for p in all_positions}

# Get registered positions for this bot
my_registered_ids = registry.get_positions_for_bot(self.bot_name)

# Find orphans (registered but not in Saxo)
orphans = my_registered_ids - all_saxo_ids
for orphan_id in orphans:
    registry.unregister_position(orphan_id)
    logger.warning(f"Removed orphan from registry: {orphan_id}")

# Find unregistered (in Saxo but not registered) - needs manual review
unregistered = all_saxo_ids - registry.get_all_registered_ids()
if unregistered:
    logger.warning(f"Found unregistered positions: {unregistered}")
```

---

## Implementation Specification

### New Shared Module: `shared/position_registry.py`

```python
"""
Position Registry - Tracks bot ownership of positions.

This module provides thread-safe, file-based tracking of which bot
owns which position. Essential for running multiple bots on the
same underlying (e.g., Iron Fly + MEIC on SPX).

Usage:
    from shared.position_registry import PositionRegistry

    registry = PositionRegistry("/opt/calypso/data/position_registry.json")

    # Register a new position
    registry.register(
        position_id="12345678",
        bot_name="IRON_FLY_0DTE",
        strategy_id="iron_fly_20260127_001",
        metadata={"strikes": [5920, 5950, 5950, 5980]}
    )

    # Get my positions
    my_positions = registry.get_positions("IRON_FLY_0DTE")

    # Unregister on close
    registry.unregister("12345678")
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
    """A position registered to a specific bot."""
    position_id: str
    bot_name: str
    strategy_id: str
    registered_at: str
    metadata: Dict


class PositionRegistry:
    """
    Thread-safe, file-based position ownership registry.

    Uses file locking to prevent race conditions when multiple
    bots access the registry simultaneously.
    """

    def __init__(self, registry_path: str = "/opt/calypso/data/position_registry.json"):
        self.registry_path = registry_path
        self._ensure_registry_exists()

    def _ensure_registry_exists(self):
        """Create registry file if it doesn't exist."""
        if not os.path.exists(self.registry_path):
            os.makedirs(os.path.dirname(self.registry_path), exist_ok=True)
            self._write_registry({"version": 1, "positions": {}})

    def _read_registry(self) -> Dict:
        """Read registry with shared lock."""
        with open(self.registry_path, 'r') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def _write_registry(self, data: Dict):
        """Write registry with exclusive lock."""
        with open(self.registry_path, 'w') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                json.dump(data, f, indent=2)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def register(
        self,
        position_id: str,
        bot_name: str,
        strategy_id: str,
        metadata: Optional[Dict] = None
    ) -> bool:
        """
        Register a position to a bot.

        Args:
            position_id: Saxo PositionId
            bot_name: Name of the bot (e.g., "IRON_FLY_0DTE", "MEIC")
            strategy_id: Unique identifier for this strategy instance
            metadata: Optional additional data (strikes, etc.)

        Returns:
            True if registered, False if already registered to another bot
        """
        registry = self._read_registry()

        if position_id in registry["positions"]:
            existing = registry["positions"][position_id]
            if existing["bot_name"] != bot_name:
                logger.error(
                    f"Position {position_id} already registered to {existing['bot_name']}, "
                    f"cannot register to {bot_name}"
                )
                return False
            logger.debug(f"Position {position_id} already registered to {bot_name}")
            return True

        registry["positions"][position_id] = {
            "bot_name": bot_name,
            "strategy_id": strategy_id,
            "registered_at": datetime.utcnow().isoformat() + "Z",
            "metadata": metadata or {}
        }

        self._write_registry(registry)
        logger.info(f"Registered position {position_id} to {bot_name}")
        return True

    def unregister(self, position_id: str) -> bool:
        """
        Unregister a position (typically on close).

        Args:
            position_id: Saxo PositionId to unregister

        Returns:
            True if unregistered, False if not found
        """
        registry = self._read_registry()

        if position_id not in registry["positions"]:
            logger.warning(f"Position {position_id} not found in registry")
            return False

        del registry["positions"][position_id]
        self._write_registry(registry)
        logger.info(f"Unregistered position {position_id}")
        return True

    def get_positions(self, bot_name: str) -> Set[str]:
        """
        Get all position IDs registered to a specific bot.

        Args:
            bot_name: Name of the bot

        Returns:
            Set of PositionIds owned by this bot
        """
        registry = self._read_registry()
        return {
            pos_id for pos_id, data in registry["positions"].items()
            if data["bot_name"] == bot_name
        }

    def get_all_registered(self) -> Set[str]:
        """Get all registered position IDs across all bots."""
        registry = self._read_registry()
        return set(registry["positions"].keys())

    def get_owner(self, position_id: str) -> Optional[str]:
        """
        Get the bot that owns a position.

        Args:
            position_id: Saxo PositionId

        Returns:
            Bot name or None if not registered
        """
        registry = self._read_registry()
        if position_id in registry["positions"]:
            return registry["positions"][position_id]["bot_name"]
        return None

    def get_position_details(self, position_id: str) -> Optional[Dict]:
        """Get full registration details for a position."""
        registry = self._read_registry()
        return registry["positions"].get(position_id)

    def is_registered(self, position_id: str) -> bool:
        """Check if a position is registered to any bot."""
        registry = self._read_registry()
        return position_id in registry["positions"]

    def cleanup_orphans(self, valid_position_ids: Set[str]) -> List[str]:
        """
        Remove registry entries for positions that no longer exist.

        Args:
            valid_position_ids: Set of PositionIds that currently exist in Saxo

        Returns:
            List of orphaned PositionIds that were removed
        """
        registry = self._read_registry()
        orphans = []

        for pos_id in list(registry["positions"].keys()):
            if pos_id not in valid_position_ids:
                orphans.append(pos_id)
                del registry["positions"][pos_id]

        if orphans:
            self._write_registry(registry)
            logger.info(f"Cleaned up {len(orphans)} orphaned positions: {orphans}")

        return orphans
```

### Modified `shared/saxo_client.py`

Add ExternalReference support to order placement:

```python
def place_order(
    self,
    uic: int,
    asset_type: str,
    buy_sell: BuySell,
    amount: int,
    order_type: OrderType = OrderType.MARKET,
    limit_price: Optional[float] = None,
    duration_type: str = "DayOrder",
    to_open_close: str = "ToOpen",
    external_reference: Optional[str] = None  # NEW PARAMETER
) -> Optional[Dict]:
    """
    Place a single order.

    Args:
        ...
        external_reference: Optional client-defined identifier (max 50 chars).
                           Used to track bot ownership of resulting positions.
    """
    order_data = {
        "AccountKey": self.account_key,
        "Uic": uic,
        "AssetType": asset_type,
        "BuySell": buy_sell.value,
        "Amount": amount,
        "OrderType": order_type.value,
        "OrderRelation": "StandAlone",
        "OrderDuration": {"DurationType": duration_type},
        "ManualOrder": True,
        "ToOpenClose": to_open_close
    }

    # Add ExternalReference if provided
    if external_reference:
        if len(external_reference) > 50:
            logger.warning(f"ExternalReference truncated: {external_reference[:50]}")
            external_reference = external_reference[:50]
        order_data["ExternalReference"] = external_reference

    # ... rest of method
```

### Bot Integration Pattern

Each bot that trades SPX (or any shared underlying) needs to integrate with the registry:

```python
class IronFlyStrategy:
    def __init__(self, config, client):
        self.client = client
        self.bot_name = "IRON_FLY_0DTE"
        self.registry = PositionRegistry()

    def _open_position(self, ...):
        # Generate strategy ID
        strategy_id = f"iron_fly_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        external_ref = f"{self.bot_name}_{strategy_id}"

        # Place order with ExternalReference
        response = self.client.place_order(
            ...,
            external_reference=external_ref
        )

        # Get PositionId from activities
        order_id = response["OrderId"]
        activity = self._wait_for_fill(order_id)
        position_id = activity["PositionId"]

        # Register ownership
        self.registry.register(
            position_id=position_id,
            bot_name=self.bot_name,
            strategy_id=strategy_id,
            metadata={"strikes": [lower_wing, atm, atm, upper_wing]}
        )

    def _filter_my_positions(self, all_positions):
        """Filter to only positions owned by this bot."""
        my_position_ids = self.registry.get_positions(self.bot_name)
        return [
            p for p in all_positions
            if p["PositionBase"]["PositionId"] in my_position_ids
        ]

    def _close_position(self, position_id):
        self.client.close_position(position_id)
        self.registry.unregister(position_id)
```

---

## Testing Strategy

### Unit Tests

1. **Registry Operations:**
   - Register/unregister positions
   - Concurrent access (multiple bots)
   - File locking behavior
   - Orphan cleanup

2. **Bot Integration:**
   - Position filtering with registry
   - ExternalReference in orders
   - Order → Activity → PositionId flow

### Integration Tests (Simulation)

1. **Multi-Bot Scenario:**
   - Start Iron Fly and MEIC simultaneously
   - Both open positions on SPX
   - Verify each only sees its own positions
   - Close positions independently
   - Verify registry stays in sync

2. **Crash Recovery:**
   - Open positions with both bots
   - Kill one bot mid-trade
   - Restart bot
   - Verify it recovers only its own positions

3. **Edge Cases:**
   - Bot restart with stale registry
   - Saxo API returns positions not in registry
   - Registry has positions not in Saxo

---

## Migration Path

### Phase 1: Add Registry Infrastructure (No Breaking Changes)

1. Implement `shared/position_registry.py`
2. Add `external_reference` parameter to `place_order()` (optional)
3. Add registry to `shared/__init__.py` exports
4. Deploy to VM (no bot changes yet)

### Phase 2: Integrate with Iron Fly (Optional for Single-Bot)

1. Add registry integration to Iron Fly
2. Iron Fly still works without registry (backward compatible)
3. Test in dry-run mode
4. Deploy (Iron Fly uses registry, but doesn't require it)

### Phase 3: Build MEIC with Registry (Required)

1. MEIC bot requires registry from day 1
2. MEIC filters positions through registry
3. Test Iron Fly + MEIC together in simulation
4. Deploy both with registry active

### Phase 4: Retrofit Other Bots (Future)

1. Delta Neutral, Rolling Put Diagonal get registry support
2. Enables future SPY/QQQ multi-bot scenarios
3. Provides unified position tracking across all bots

---

## Appendix: Saxo API References

### Sources

- [How do I label orders with a client-defined order ID?](https://openapi.help.saxo/hc/en-us/articles/4418504615057-How-do-I-label-orders-with-a-client-defined-order-ID) - ExternalReference documentation
- [Audit OrderActivities](https://developer.saxobank.com/openapi/learn/audit-orderactivities) - Order → Position linking
- [Positions Endpoint](https://www.developer.saxo/openapi/referencedocs/port/v1/positions/get__port__positionid) - Position data schema

### Key API Endpoints

| Endpoint | Purpose |
|----------|---------|
| `POST /trade/v2/orders` | Place order with ExternalReference |
| `GET /port/v1/orders` | Retrieve orders with ExternalReference |
| `GET /cs/v1/audit/orderactivities` | Link OrderId → PositionId |
| `GET /port/v1/positions` | Get positions (NO ExternalReference) |
| `DELETE /trade/v2/positions/{id}` | Close position |

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-01-27 | Claude | Initial design document |
