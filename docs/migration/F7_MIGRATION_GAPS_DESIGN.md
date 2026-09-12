# F7 — Migration Gaps: the methods F1–F6 missed

**Status**: ✅ implemented — F7.1–F7.7 committed. P4 unblocked.
**Date**: 2026-05-21
**Source**: the P4 Saxo-reference audit (2026-05-21) found these.

---

## 1. The discovery

F1–F6 rewired the broker flows I had **traced and listed**. But the
P4 audit found ~8 *inherited base-class methods* that call
`self.client` (the SaxoClient) directly, have **no `self.broker`
path**, and are on the **live entry / monitoring path** — they were
never on any flow's call-site list.

They are masked today because `bots/hydra/main.py` constructs HYDRA
with `broker=None` — so the **IBKR path has never actually run
end-to-end**. Every dry-run test/regression exercised the Saxo branch.
The moment P4 makes `broker` mandatory, these 8 methods break.

So F1–F6 was not the whole migration. F7 closes the gaps. **P4 cannot
purge Saxo until F7 is done.**

## 2. The gaps

| Gap | Method (in `base_strategy.py` unless noted) | Saxo call | Live caller | Fix |
|---|---|---|---|---|
| GAP-B | `_estimate_entry_credit` (2563) | `get_quote` ×4 | credit gate, every entry | use `_read_option_quotes_batch` |
| GAP-C | `_update_market_data` (5453) | `get_quote` (CfdOnIndex) + `get_vix_price` | MKT-043 calm-entry loop; **sets `current_price`/VIX** | new `_read_index_price` helper → IB `get_quote` on SPX/VIX conids |
| GAP-E | `_check_market_halt` (8450) | `get_quote` (CfdOnIndex) | MKT-005, every entry | `_read_index_price` |
| GAP-F | `_check_buying_power` (8335) | `get_balance` | ORDER-004, every entry | IB `get_balance` (field-map; see Fix #85 field names) |
| GAP-G | `_verify_entry_fill_prices` (3819) | `get_positions` (`PositionBase.OpenPrice`) | post-fill verification | `_read_open_positions` (`avg_cost`) |
| GAP-H | `_adjust_strike_for_liquidity` (8498) | `get_quote` | `_calculate_strikes`, every entry | `_read_option_quote` |
| GAP-I | `_adjust_long_wing_for_liquidity` (8562) | `get_quote` | `_calculate_strikes`, every entry | `_read_option_quote` |
| GAP-A | `_spawn_async_early_close_fill_correction` (`strategy.py` ~2832) | `check_order_filled_by_activity` | MKT-018 (DISABLED) | gate on broker / leave dormant — MKT-018 is off |

Plus: verify `_get_total_saxo_pnl` callers (`strategy.py:1144, 2380`)
— confirm HYDRA's unrealized-P&L path has an IB route (it should, via
`_get_broker_pnl_for_entry` / F4.7 — confirm during F7).

GAP-C is the most fundamental: `_update_market_data` is what sets
`self.current_price` (SPX spot — used in *every* strike calc) and the
VIX. On the IBKR path it currently never runs.

## 3. The pattern

Every gap is the same shape as F1–F6: a method does `self.client.<X>`;
F7 makes it broker-agnostic. Most fixes just point the method at a
**helper that already exists** (`_read_option_quote`,
`_read_option_quotes_batch`, `_read_open_positions`). Two need a new
thin helper:

- `_read_index_price(symbol)` → SPX / VIX spot. IB: `broker.get_quote`
  on the index conid (`broker.get_vix_price` already exists for VIX);
  Saxo: `client.get_quote(CfdOnIndex)`. Used by GAP-C and GAP-E.
- `_read_account_balance()` → broker-agnostic balance dict for
  ORDER-004. IB: `broker.get_balance()` mapped to the fields
  `_check_buying_power` reads.

## 4. Commit breakdown for F7

| Commit | Scope |
|---|---|
| F7.1 | `_read_index_price` + `_read_account_balance` helpers + tests |
| F7.2 | GAP-C `_update_market_data` — SPX/VIX via `_read_index_price` |
| F7.3 | GAP-E `_check_market_halt` |
| F7.4 | GAP-F `_check_buying_power` — `_read_account_balance` |
| F7.5 | GAP-B `_estimate_entry_credit` — `_read_option_quotes_batch` |
| F7.6 | GAP-H/I `_adjust_strike_for_liquidity` / `_adjust_long_wing_for_liquidity` |
| F7.7 | GAP-G `_verify_entry_fill_prices` + GAP-A gate + `_get_total_saxo_pnl` check |

Each broker-branches `if self.broker is not None:` like F1–F6; the
Saxo branch is then deleted by P4. After F7, P4 can purge cleanly.

### F7.7 resolution (2026-05-21)

- **GAP-G** `_verify_entry_fill_prices` — broker-branched: the IB path
  reads `_read_open_positions`, keys `price_lookup` by `str(conid)` and
  the legs by `*_uic`, and uses `avg_cost` as the actual fill price
  (the IBKR equivalent of Saxo `PositionBase.OpenPrice`).
- **GAP-A** `_spawn_async_early_close_fill_correction` — gated
  `if self.broker is not None: return`. IBKR closes route through
  `place_and_wait_for_fill`, which polls to a terminal state and
  returns the actual `avg_fill_price` synchronously — there is no
  Saxo activity-stream sync lag, so no deferred correction is needed.
  (MKT-018 early close is also disabled.)
- `_get_total_saxo_pnl` — broker-branched: the IB path sums
  `_get_broker_pnl_for_entry` (conid-keyed, MKT-025 aware) per active
  entry off a single `_read_open_positions` fetch. Both callers are
  inside MKT-018 (disabled) — a correctness-preservation rewire so
  P4's `self.client` removal cannot break it.

All Saxo branches above stay inline (dormant) and are deleted by P4.

## 5. Why F1–F6 missed these

The flow-by-flow rewrite traced the call chains I *enumerated* (chart,
quote, chain, positions, settlement, write). These 8 methods are
inherited MEIC-base helpers called from *inside* `_initiate_entry` /
`_calculate_strikes` / the monitoring loop — branches of the call
graph I didn't expand into. The honest lesson: the flow audits should
have been call-graph-complete from `_initiate_entry` down. The P4
audit (a full Saxo-reference sweep) caught what the flow audits
missed. F7 + the P4 audit's exhaustive `self.client` list together
ARE now call-graph-complete.

## 6. Decision requested

1. Insert F7 before P4 (it is a hard blocker — confirmed).
2. The `_read_index_price` / `_read_account_balance` helper pair.
3. The 7-commit F7.1–F7.7 breakdown.
