# F6 — Order Write-Path Flow: IBKR Design

**Status**: 📋 design — awaiting approval before implementation
**Date**: 2026-05-21
**Predecessors**: F1–F5 ✅ (read/reconciliation/settlement), P1 ✅
(reparent), P2-partial ✅.

F6 is the last flow and the only one that places/closes **real
orders**. F1–F5 made HYDRA's read paths broker-agnostic; the inherited
base (`bots/hydra/base_strategy.py`) write paths are still 100% Saxo.
F6 rewires them to IBKR.

---

## 1. The write-path call sites (audited 2026-05-21)

Saxo write-API calls in `bots/hydra/base_strategy.py`:

| Saxo call | line | In method | Role |
|---|---|---|---|
| `place_order` | 3051 | `_place_option_order` | MARKET leg placement |
| `place_limit_order_with_timeout` | 3115 | `_place_option_order` | LIMIT leg placement |
| `check_order_filled_by_activity` | 3067 | `_place_option_order` | MARKET fill verify |
| `check_order_filled_by_activity` | 3566 | `_verify_order_fill` | fill verify |
| `get_order_status` | 3614 | `_verify_order_fill` | order status poll |
| `get_open_orders` | 3272 | `_has_orphaned_orders` | orphan detection |
| `cancel_order` | 3289 | `_add_orphaned_order` cleanup | cancel a zombie |
| `place_emergency_order` | 3807 | `_handle_naked_short` | emergency close |
| `place_emergency_order` | 3863 | `_unwind_partial_entry` | emergency unwind |
| `place_emergency_order` | 4420 | `_close_position_with_retry` | leg close |
| `check_order_filled_by_activity` | 4869, 4939 | `_get_close_fill_price` / deferred lookup | close-fill price |

In `bots/hydra/strategy.py` (HYDRA's own overrides):
| `check_order_filled_by_activity` | 2724 | `_record_entry_to_db` MKT-018 deferred-fill |

These write paths run **only in live mode** — dry-run uses
`_simulate_entry`. They sit behind the 4 SAFETY-DRY gates added in
v1.25, so dry-run can never reach a real order.

## 2. IBKR write primitives (already on IBClient — Phase A.10)

| IBClient method | Use |
|---|---|
| `place_and_wait_for_fill(*, conid, side, quantity, order_type, limit_price, …)` | **The workhorse** — places + polls to a terminal state; returns `{order_id, status, filled_quantity, avg_fill_price, raw}` |
| `place_market_order` / `place_order` | lower-level single placement |
| `cancel_order(order_id)` | cancel; terminal-state-as-success |
| `get_order_status(order_id)` | status poll |
| `get_open_orders()` | live order list (cache-clear preflight built in) |
| `place_iron_condor` / `place_vertical_spread` | combo orders (NOT used by F6 — see §3) |

`place_and_wait_for_fill` collapses Saxo's place + `check_order_filled_by_activity`
two-step into one call — it already polls to fill.

## 3. Design — broker-agnostic write helpers

Mirror the F3/F4/F5 `_read_*` pattern with a write-helper layer on
`HydraStrategy`. The helpers dispatch on `self.broker`; the large
orchestration methods (`_place_option_order`, `_close_position_with_retry`,
`_execute_entry`) keep their structure — only the broker I/O swaps.

```
_place_leg_order(*, instrument_id, side, quantity, order_type,
                 limit_price=None, external_ref) -> dict
    Places ONE option leg, returns a normalized result:
        {success, filled, order_id, fill_price, position_id}
    IB:   broker.place_and_wait_for_fill(conid=, side=, quantity=,
              order_type="LMT"/"MKT", limit_price=)  → mapped to the
          normalized shape; position_id is None (IBKR has none).
    Saxo: the existing place_order / place_limit_order_with_timeout +
          check_order_filled_by_activity path (unchanged).

_close_leg_order(*, instrument_id, side, quantity) -> dict
    Closes ONE leg with a market order, normalized result.
    IB:   broker.place_and_wait_for_fill(order_type="MKT", …)
    Saxo: place_emergency_order(…)

_cancel_order(order_id) -> bool         IB: broker.cancel_order / Saxo
_get_order_status(order_id) -> dict     IB: broker.get_order_status / Saxo
_get_open_orders() -> list              IB: broker.get_open_orders / Saxo
```

**Per-leg, not combo.** IBKR supports atomic IC combo orders
(`place_iron_condor`) — atomic is arguably safer than 4 separate legs.
But HYDRA's whole entry machinery (safe-order longs-first, partial-fill
unwind via `_handle_naked_short` / `_unwind_partial_entry`, per-leg
slippage retry, MKT-013/015 overlap handling) is built on per-leg
placement. Swapping to combo is a redesign, not a migration. F6 keeps
the per-leg structure and only swaps the broker calls — minimal risk.
`place_iron_condor` is noted as a future simplification, out of F6
scope.

**Enum translation.** Saxo `BuySell.BUY/SELL` + `OrderType.MARKET/LIMIT`
vs IBKR's `"BUY"/"SELL"` + `"MKT"/"LMT"` strings. The helpers take
broker-neutral args (`side: str` = "BUY"/"SELL", `order_type: str`)
and translate per-broker internally.

**Fill verification.** `check_order_filled_by_activity` is Saxo
activity-stream specific. On IB, `place_and_wait_for_fill` polls to a
terminal status, so the separate fill-check disappears — the helper
returns `filled` + `fill_price` directly. The deferred-fill lookups
(`_get_close_fill_price`, MKT-018) collapse similarly: IB's
`get_order_status` / the fill result already carries `avg_fill_price`.

## 4. Per-site adaptation

- `_place_option_order` — the progressive-slippage retry LOOP stays
  (the strategy owns "what retry level next"); each attempt's
  place+verify becomes one `_place_leg_order` call. `place_and_wait_for_fill`
  owns the place→poll mechanics of one attempt.
- `_close_position_with_retry` — `place_emergency_order` →
  `_close_leg_order`; the retry/zombie-cancel loop stays.
- `_handle_naked_short` / `_unwind_partial_entry` — `place_emergency_order`
  → `_close_leg_order`.
- `_execute_stop_loss` (HYDRA override) — already calls
  `_close_position_with_retry`; inherits the rewire for free.
- `_has_orphaned_orders` / orphan cleanup — `get_open_orders` /
  `cancel_order` → the thin dispatch helpers.
- MKT-018 deferred-fill `check_order_filled_by_activity` (hydra:2724)
  → `_get_order_status`-based check.

## 5. Commit breakdown for F6

| Commit | Scope |
|---|---|
| F6.1 | `_place_leg_order` + `_close_leg_order` + `_cancel_order` / `_get_order_status` / `_get_open_orders` dispatch helpers + unit tests (both paths) |
| F6.2 | Rewire `_place_option_order` (entry leg placement) |
| F6.3 | Rewire `_close_position_with_retry` (leg close) |
| F6.4 | Rewire `_handle_naked_short` / `_unwind_partial_entry` (partial-fill recovery) |
| F6.5 | Rewire `_verify_order_fill` / `_has_orphaned_orders` / MKT-018 deferred-fill |
| F6.6 | Close DEF-3 (MKT-033 salvage gated on `*_uic`) + DEF-5 (`_process_expired_credits` conid keying) — now unblocked by the IBKR write path |

Each commit is independently committable + test-green. The dry-run
`_simulate_entry` fork is untouched throughout.

## 6. Out of scope / closed

- IC combo orders (`place_iron_condor`) — future simplification.
- The 4 SAFETY-DRY gates stay — defense-in-depth, untouched.
- F6.6 closes audit deferrals DEF-3 and DEF-5 (both were deferred
  explicitly *to* the write-path rewrite).

## 7. Decision requested

1. The broker-agnostic write-helper layer (`_place_leg_order` /
   `_close_leg_order` / dispatch helpers).
2. Per-leg placement retained (not combo).
3. The 6-commit F6.1–F6.6 breakdown.

Once approved, F6.1 (the write helpers) starts.
