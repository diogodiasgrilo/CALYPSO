# F4 — Position Reconciliation / Recovery / Monitoring Flow: IBKR Design

**Status**: ✅ implemented — F4.1–F4.9 committed (see "Implementation status")
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

**Correction (P7-audit H13, 2026-05-22):** the F4 implementation
keeps the legacy field names (`*_uic` / `*_position_id`); the conid
is stored in `*_uic` and `*_position_id` is always `None` on the
IBKR path. The `*_position_id → *_order_id` rename described in
`STATE_SCHEMA_DESIGN.md` was abandoned and that doc is now marked
SUPERSEDED. F4 is the executable conid→quantity reconciliation
model, not a field rename.

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
# illustrative — see "Implementation status / F4.2" for the final
# signature (right/positions/min_abs_qty are keyword-only).
def _position_is_open(self, instrument_id, *, right=None,
                      positions=None, min_abs_qty=1) -> bool:
    """True if the broker shows an open option position at
    `instrument_id` with |quantity| >= min_abs_qty (right optional)."""
```

On Saxo this still works (a non-merged leg is just `|qty| >= 1` at its
UIC). On IBKR it correctly handles the merged-position default.

The POS-003 / POS-004 / FIX-#82 reconciliation logic is rewritten in
terms of `_position_is_open` instead of `PositionId` set differences.

### 4a. POS-003: native conid→quantity reconciliation (F4.4 decision)

`_position_is_open` answers "is *this* leg open" but has a blind spot:
if two entries share conid X (a merge) and one entry's short closes,
the net quantity is still non-zero, so a per-leg check says "open".
For the hourly reconciliation safety net that blind spot is a real
correctness gap.

**Decision (approved 2026-05-21)**: do NOT shim IBKR into Saxo's
per-leg `PositionId` model. Model positions the way IBKR actually
represents them — `conid → signed net quantity` — and reconcile by
*aggregate quantity*:

- `_expected_position_quantities()` → `{conid: net_qty}` summed across
  every active entry's still-tracked legs (uncleared `*_uic`). Shorts
  negative, longs positive. Two entries on one conid sum naturally, so
  a merge is **not** a discrepancy.
- `_actual_position_quantities(positions)` → `{conid: net_qty}` summed
  from `_read_open_positions()`.
- A discrepancy is any conid where `expected != actual`. Dict
  arithmetic — no `PositionId`, no registry.
- `_handle_position_discrepancies()` cleans up only the *unambiguous*
  case (a conid mapped to exactly one tracked leg, broker quantity
  zero → clear the `*_uic`, mark a vanished short side stopped).
  Ambiguous (multi-leg conid) or partial (non-zero leftover) →
  alert-only, no auto-mutation.
- Defensive: an empty `_read_open_positions()` while legs are expected
  is treated as a fetch failure (skip), never a mass close.

This is strictly more correct than per-leg `_position_is_open`
(catches the merge blind spot), broker-agnostic (Saxo merges too), and
needs no Position Registry — which is vestigial in a single-bot world
and slated for deletion in the dead-code phase. The registry-coupled
`unexpected` half of the old POS-003 is dropped.

### 4b. `_recover_positions_from_saxo`: state-file-authoritative (F4.8)

The old live recovery (`not dry_run`) rebuilt entries by querying the
broker, mapping positions→entries through the Position Registry, and
*guessing* each entry's structure from whatever legs were live
(`_reconstruct_entry_from_positions`). That guess is the documented
root cause of the Fix #65 / #67 recovery-bug history.

**Decision**: recovery must be 100% live-ready (a `dry_run`→live flag
flip must yield a correct bot — "dead in dry-run" ≠ "delete it"). But
the fix is the *correct* design, not a faithful port of the flawed
one. HYDRA's own **state file is the authoritative entry source** — it
already carries every field (strikes, credits, stop levels, status
flags, instrument ids, contracts, fill prices) and
`_load_state_file_history` already rebuilds full entries from it. The
broker is the **reconciliation cross-check**, not the source of truth.

New `_recover_positions_from_saxo` (name kept only so the inherited
MEIC `__init__` dispatches to the override):

1. `_load_state_file_history()` — authoritative reconstruction of
   today's entries + P&L + counters (dry-run and live alike).
2. live only — `_reconcile_recovered_entries_with_broker()`: the F4.4
   conid→quantity cross-check (`_expected/_actual_position_quantities`
   + `_handle_position_discrepancies`). A leg the broker no longer
   shows open is marked closed. `_read_open_positions(strict=True)` so
   a fetch failure can't masquerade as "everything closed" and wipe
   live legs.
3. no Position Registry, no `_group_positions_by_entry`, no
   `_reconstruct_entry_from_positions` guessing.

~680 lines collapse to ~130. **Dead-code consequence** (two distinct
categories — the dead-code phase must handle them differently):
- **HYDRA-local overrides now unreachable** — `_reconstruct_entry_from_positions`
  (`bots/hydra/strategy.py`) and `_recover_from_state_file_uics`
  (`bots/hydra/strategy.py`). HYDRA's `_recover_positions_from_saxo` no
  longer calls either. These are HYDRA's own methods and must be
  explicitly **deleted** from `bots/hydra/strategy.py`.
- **Inherited MEIC methods HYDRA stopped reaching** — `_group_positions_by_entry`
  and `_extract_mid_price` live ONLY in `bots/meic/strategy.py`; HYDRA
  never overrode them. They are still live for MEIC. They are not
  HYDRA dead code — they die when `bots/meic/` is deleted wholesale
  (MEIC is a retired bot). Do NOT look for them in `bots/hydra/`.

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
| F4.3 | MKT-033 salvage path — `_try_sell_long_leg` + `_check_long_salvage` (clean helper swap) |
| F4.4 | POS-003 hourly reconciliation (logic rewrite to quantity-aware) |
| F4.5 | FIX #82 overnight-position check + `_read_open_positions(strict=)` |
| F4.6 | POS-004 settlement check |
| F4.7 | Rewire the 2 P&L sites (`_get_saxo_pnl_for_entry`, snapshot Telegram) |
| F4.8 | Rewire `_recover_positions_from_saxo` (full reconstruction) |
| F4.9 | Rewire `_batch_update_entry_prices` monitoring quotes ×2 |

**Re-scoped from the original 6-commit plan**: the 5 id-set sites do
NOT all collapse into one commit. The MKT-033 salvage path (F4.3) is a
clean I/O swap — `_try_sell_long_leg` just needs the existence check
swapped to `_position_is_open`. But POS-003 / POS-004 / FIX #82 (F4.4,
F4.5) reconcile against `entry.all_position_ids` and the Position
Registry — both `PositionId`-keyed — so they are genuine
**reconciliation-logic rewrites**, not mechanical swaps, and each
earns its own commit.

`_recover_positions_from_saxo` (F4.8) is the heaviest — it may split
further once its internals are mapped in detail.

## 7. Out of scope for F4

- `get_closed_position_price` (settlement close prices) → **F5**.
- `get_fx_rate` (USD/EUR conversion for reporting) → **F5**.
- The `self.registry.get_positions(...)` calls — that's the local
  file-based PositionRegistry, broker-independent, unchanged.

## Implementation status

- `_read_open_positions` broker-agnostic helper: F4.1 ✅ committed (573c5f3)
- `_position_is_open` predicate: F4.2 ✅ committed (2d63619)
- MKT-033 salvage path rewire: F4.3 ✅ committed (7d840ff)
- POS-003 native conid→quantity reconciliation: F4.4 ✅ committed (bb934f5)
- FIX #82 overnight check + `strict` param: F4.5 ✅ committed (e5e07a8)
- POS-004 settlement check: F4.6 ✅ committed (012f057)
- P&L sites: F4.7 ✅ committed (1e82533)
- `_recover_positions_from_saxo` state-file rewrite: F4.8 ✅ committed (03e2406)
- `_batch_update_entry_prices` monitoring quotes: F4.9 ✅ committed (308aaec)

### F4.9 — `_batch_update_entry_prices` monitoring quotes

Both monitoring batch-quote calls (dry-run path + live path) rewired
from Saxo `client.get_quotes_batch` to the broker-agnostic
`_read_option_quotes_batch` (F3.4). New `_quote_mid` staticmethod
derives a mid from the normalized quote shape (broker `mid` →
`(bid+ask)/2` → `last` → `mark` → 0.0), replacing the Saxo-shaped
`_extract_mid_price`. Empty result handling: dry-run falls back to
simulation, live skips the tick (prior prices stand) — no bogus zeros,
no crash. 10 tests (`TestQuoteMid` + `TestBatchUpdateEntryPrices`).

**F4 complete.** Every direct Saxo `get_positions` / `get_quotes_batch`
call in HYDRA's flow code is gone — all position/quote I/O now routes
through the broker-agnostic helpers (`_read_open_positions`,
`_read_option_quotes_batch`, `_read_option_quote`, `_read_option_greeks`).
Dead-code left for the dead-code phase: HYDRA-local `_recover_from_state_file_uics`
and `_reconstruct_entry_from_positions` (delete from `bots/hydra/`);
the inherited MEIC `_group_positions_by_entry` / `_extract_mid_price`
are MEIC-only and die with `bots/meic/` (see the F4.8 dead-code note).

### F4.8 — recovery rewritten state-file-authoritative

See §4b. `_recover_positions_from_saxo` rewritten: state file is the
authoritative entry source (`_load_state_file_history`), broker is the
conid→quantity cross-check (`_reconcile_recovered_entries_with_broker`,
live only). ~680 lines → ~130. No registry, no position-structure
guessing. 14 tests (`TestRecoverPositions` +
`TestReconcileRecoveredEntriesWithBroker`); 2 obsolete Brandon
integration tests rewritten to the new contract. `_recover_from_state_file_uics`
/ `_group_positions_by_entry` / `_reconstruct_entry_from_positions`
left dead for the dead-code phase.

### F4.7 — P&L sites

`_get_saxo_pnl_for_entry` → renamed `_get_broker_pnl_for_entry` and
rewritten to sum `unrealized_pnl` by conid (`instrument_id`) over the
`_read_open_positions` shape, instead of matching Saxo `PositionId`s
and reading `PositionView.ProfitLossOnTrade`. Stopped sides are still
excluded (MKT-025 double-count guard). The snapshot Telegram builder
fetches positions via `_read_open_positions` and passes the list down.
Known limitation documented: a genuinely merged conid attributes its
P&L to each sharing entry (MKT-013/015 deconfliction prevents merges
in practice). 5 `TestGetBrokerPnlForEntry` tests.

### F4.6 — POS-004 settlement check

`check_after_hours_settlement` rewritten in the conid→quantity model:
a tracked leg is settled when the broker shows zero quantity at its
conid. Reuses `_expected_position_quantities` /
`_actual_position_quantities` (F4.4). Uses `_read_open_positions(
strict=True)` so a post-close fetch failure returns False (retry next
heartbeat) rather than being mistaken for "all settled". Settled legs
have their `*_uic` + legacy `*_position_id` cleared; expired credits
processed idempotently. At 0DTE settlement a whole conid expires at
once, so merges settle cleanly. 5 `TestCheckAfterHoursSettlement` tests.

### F4.5 — FIX #82 overnight check + `_read_open_positions(strict=)`

`_read_open_positions` gained a `strict` kwarg: default False swallows
a fetch failure (returns `[]`); `strict=True` re-raises so a caller
can tell a real empty account from a broker outage. The FIX #82
overnight-position check in `_reset_for_new_day` now verifies against
the broker via `_read_open_positions(strict=True)` — any open option
position at the new-day reset is a genuine overnight 0DTE position
(halt); empty means the vestigial registry was just stale (clean it,
proceed); a fetch failure re-raises into the existing conservative
halt. 5 tests (`TestReadOpenPositionsStrict` + `TestFix82OvernightCheck`).

### F4.4 — POS-003 native conid→quantity reconciliation

`_check_hourly_reconciliation` rewritten in the conid→quantity model
(see §4a). Three new helpers: `_expected_position_quantities`,
`_actual_position_quantities`, `_handle_position_discrepancies`. The
old `PositionId` set arithmetic, `_handle_missing_positions` call, and
registry-coupled `unexpected` check are gone. 21 tests across
`TestExpectedPositionQuantities` / `TestActualPositionQuantities` /
`TestHandlePositionDiscrepancies` / `TestHourlyReconciliationBody` —
including an explicit merged-position-is-not-a-discrepancy test.

### F4.3 — MKT-033 salvage path rewired

`_try_sell_long_leg`'s leg-existence check switched from a Saxo
`PositionId` set-membership test to the quantity-aware
`_position_is_open(long_uic, right=…)`. Its `valid_pos_ids` set param
became an `open_positions` list (a `_read_open_positions` result).
`_check_long_salvage` prefetches once via `_read_open_positions` and
hands the list down. 8 tests across `TestTrySellLongLegReconciliation`
+ `TestCheckLongLegSalvageRewire`.

### F4.2 — `_position_is_open` shipped

Quantity-aware reconciliation primitive. `_position_is_open(id, *,
right=None, positions=None, min_abs_qty=1)` → True when the broker
shows `|quantity| >= min_abs_qty` at `id`. Type-tolerant id compare,
optional `right` filter, optional pre-fetched `positions` list to
avoid re-fetching when checking many legs. 12 `TestPositionIsOpen`
tests.

### F4.1 — `_read_open_positions` shipped

Broker-agnostic open-option-position reader on `HydraStrategy`. IB
path runs each raw IBKR row through `_normalize_position_dict` (Phase
A.10) and keeps `asset_type == "OPT"` rows; `position_id` is None.
Saxo path flattens the nested `PositionBase`/`PositionView` shape.
`[]` on fetch failure; unparseable IB rows (no conid) skipped without
aborting the batch. 13 `TestReadOpenPositions` tests cover both paths.

## 8. Decision record (resolved)

Approved 2026-05-21 and implemented:
1. The `(conid, right, expected_sign/quantity)` identity model for the
   IB era (vs Saxo's per-leg `PositionId`) — quantity-aware
   reconciliation as the primary path.
2. The `_read_open_positions` + `_position_is_open` helper pair.
3. The breakdown grew from the original 6 commits to **9 (F4.1–F4.9)**
   — POS-003/POS-004/FIX-#82 turned out to be genuine reconciliation-
   logic rewrites rather than mechanical I/O swaps, and each earned its
   own commit (see §6).

Implemented across F4.1–F4.9; see "Implementation status" for the
per-commit detail. Two follow-up live-readiness gaps surfaced by the
post-implementation audit are tracked in `DEFERRED_WORK.md` (MKT-033
salvage gate, STATE-002 consistency check — both still keyed on the
Saxo-only `*_position_id` / Position Registry).
