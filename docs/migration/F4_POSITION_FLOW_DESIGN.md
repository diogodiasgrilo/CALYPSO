# F4 — Position Reconciliation / Recovery / Monitoring Flow: IBKR Design

**Status**: 📋 design — awaiting approval before implementation
**Date**: 2026-05-21
**Predecessor**: F3 (option chain) complete — commits F3.1-F3.7

F4 is the second-hardest flow of the HYDRA IB-only rewrite. It covers
HYDRA's `client.get_positions()` ×8 + the two `_batch_update_entry_prices`
monitoring `get_quotes_batch` calls deferred from F3. Unlike F1/F2/F3
(stateless reads), F4 is **stateful** — it reconciles tracked entries
against the broker's live position list — and the two brokers model
position identity differently. That difference is the core design
question below.

---

## 1. The 8 `get_positions()` call sites (audited 2026-05-21)

| # | Line | Method | What it does with the result |
|---|---|---|---|
| 1 | ~6031 | `_try_sell_long_leg` (fallback) | Builds `{str(PositionId)}` set; checks if one long leg still exists |
| 2 | ~6281 | `_check_long_leg_salvage` (prefetch) | Builds `{str(PositionId)}` set once, passes to `_try_sell_long_leg` |
| 3 | ~6307 | `_get_saxo_pnl_for_entry` | Sums `PositionView.ProfitLossOnTrade` for non-stopped sides |
| 4 | ~7293 | `_build_snapshot_message` (Telegram) | Per-entry P&L for the `/snapshot` command |
| 5 | ~9577 | POS-003 hourly reconciliation | `{str(PositionId)}` set; flags expected-but-missing positions |
| 6 | ~9638 | FIX #82 overnight-position check | `{str(PositionId)}` set; verifies stale registry vs Saxo |
| 7 | ~10202 | POS-004 settlement check | `{str(PositionId)}` set; cleans up settled positions |
| 8 | ~10982 | `_recover_positions_from_saxo` | Full entry reconstruction from `PositionBase.*` |

**Categorisation**:
- **Id-set sites (1, 2, 5, 6, 7)** — only need "which of my tracked
  positions still exist on the broker". 5 sites.
- **P&L sites (3, 4)** — need per-position unrealised P&L. 2 sites.
- **Full reconstruction (8)** — `_recover_positions_from_saxo` rebuilds
  `HydraIronCondorEntry` objects from raw position data after a restart.
  1 site, the heaviest.

## 2. The core problem — Saxo `PositionId` vs IBKR `conid`

Saxo gives **every leg its own stable `PositionId`** (a UUID). HYDRA's
entire reconciliation model is built on it: each entry stores
`short_call_position_id`, `long_call_position_id`, etc., and
reconciliation is set arithmetic — `expected_ids - actual_ids`.

IBKR has **no per-leg position id**. A position IS a `conid` with a
signed net `position` quantity. Two entries both short the 6800 call
aggregate into ONE conid row with `position = -2`. There is no id to
intersect.

`STATE_SCHEMA_DESIGN.md` already recorded the field-level consequence:
`*_position_id` → `*_order_id` (IBKR positions derive from order IDs).
F4 is where that decision becomes executable code.

### Identity model for the IB era

A tracked leg is identified by **`(conid, right, expected_sign)`**, and
reconciliation becomes a **quantity** question, not a set-membership
question:

- *"Is my tracked short call still open?"* → is there a position row at
  `conid` with `quantity` of the expected sign and magnitude ≥ what this
  entry contributed?
- A leg is **gone** when its conid is absent OR the net quantity no
  longer covers this entry's contribution.

This is the same merged-position problem Saxo already had (Fix #45 /
MKT-013/015 — shared strikes merge). On IBKR merging is the *default*,
not an edge case, so the quantity-aware check becomes the primary path.

## 3. Broker-agnostic helper — `_read_open_positions()`

```python
def _read_open_positions(self) -> list[dict]:
    """All open option positions from the active broker, normalized.

    Returns a list of dicts with stable keys:
        instrument_id : int     # IBKR conid / Saxo UIC
        quantity      : int     # signed — negative = short
        side          : str     # "LONG" | "SHORT" | "FLAT"
        strike        : Optional[float]
        right         : Optional[str]   # "C" | "P"
        expiry        : Optional[date]
        unrealized_pnl: Optional[float]
        position_id   : Optional[str]   # Saxo PositionId; None on IB
        raw           : dict
    """
```

- **IB path**: `broker.get_positions()` (raw IBKR rows) → each through
  `_normalize_position_dict` (already built + tested, Phase A.10) →
  filter to `asset_type == "OPT"`. `position_id` is `None` (IBKR has
  none). `unrealized_pnl` comes from the normalized `unrealized_pnl`.
- **Saxo path**: legacy raw dicts re-shaped — `PositionId`,
  `PositionBase.Uic/Amount`, `PositionView.ProfitLossOnTrade`,
  `PositionBase.OptionsData.{Strike,PutCall,ExpiryDate}`. Kept until
  MEIC inheritance is removed.

Both brokers converge on the same list-of-dicts shape, so the call
sites stop caring which broker is live.

## 4. Reconciliation helper — `_position_is_open(entry, leg)`

The 5 id-set sites currently do `str(pos_id) in actual_ids`. They get
replaced by a single quantity-aware predicate:

```python
def _position_is_open(self, conid, right, min_abs_qty=1) -> bool:
    """True if the broker shows an open option position at `conid`
    with `right` and |quantity| >= min_abs_qty."""
```

On Saxo this still works (a non-merged leg is just `|qty| >= 1` at its
UIC). On IBKR it correctly handles the merged-position default.

The POS-003 / POS-004 / FIX-#82 reconciliation logic is rewritten in
terms of `_position_is_open` instead of `PositionId` set differences.

## 5. `_batch_update_entry_prices` monitoring quotes (deferred from F3)

Two `get_quotes_batch` calls (dry-run path + live path) refresh per-leg
mid prices each monitoring tick. They are NOT chain-coupled credit
estimation — they belong here in F4. Both rewire to
`_read_option_quotes_batch` (F3.4). The Saxo-specific
`_extract_mid_price(quote)` is replaced by the normalized `mid` field
(IB) with a `(bid+ask)/2` fallback (Saxo, which delivers no `mid`).

## 6. Commit breakdown for F4

| Commit | Scope |
|---|---|
| F4.1 | `_read_open_positions` broker-agnostic helper + unit tests (both paths) |
| F4.2 | `_position_is_open` quantity-aware predicate + unit tests |
| F4.3 | Rewire the 5 id-set sites (`_try_sell_long_leg`, salvage prefetch, POS-003, FIX #82, POS-004) |
| F4.4 | Rewire the 2 P&L sites (`_get_saxo_pnl_for_entry`, snapshot Telegram) |
| F4.5 | Rewire `_recover_positions_from_saxo` (full reconstruction) |
| F4.6 | Rewire `_batch_update_entry_prices` monitoring quotes ×2 |

`_recover_positions_from_saxo` (F4.5) is the heaviest — it may split
further once its internals are mapped in detail.

## 7. Out of scope for F4

- `get_closed_position_price` (settlement close prices) → **F5**.
- `get_fx_rate` (USD/EUR conversion for reporting) → **F5**.
- The `self.registry.get_positions(...)` calls — that's the local
  file-based PositionRegistry, broker-independent, unchanged.

## Implementation status

- `_read_open_positions` broker-agnostic helper: F4.1 ✅ committed
- `_position_is_open` predicate: F4.2 (pending)
- id-set / P&L / recovery / monitoring rewires: F4.3-F4.6 (pending)

### F4.1 — `_read_open_positions` shipped

Broker-agnostic open-option-position reader on `HydraStrategy`. IB
path runs each raw IBKR row through `_normalize_position_dict` (Phase
A.10) and keeps `asset_type == "OPT"` rows; `position_id` is None.
Saxo path flattens the nested `PositionBase`/`PositionView` shape.
`[]` on fetch failure; unparseable IB rows (no conid) skipped without
aborting the batch. 13 `TestReadOpenPositions` tests cover both paths.

## 8. Decision requested

1. The `(conid, right, expected_sign/quantity)` identity model for the
   IB era (vs Saxo's per-leg `PositionId`) — quantity-aware
   reconciliation as the primary path.
2. The `_read_open_positions` + `_position_is_open` helper pair.
3. The 6-commit F4.1-F4.6 breakdown.

Once approved, F4.1 (`_read_open_positions`) starts.
