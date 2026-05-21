# HYDRA → MEIC Call Chain Audit

**Date**: 2026-05-19 (Phase NEW-2 commit 1)
**Method**: Static analysis of `bots/hydra/strategy.py` (11,143 LOC) traced through every `super()` call and inherited callback into `bots/meic/strategy.py` (8,777 LOC). 160 total MEIC methods inventoried.

## Conclusion

**HYDRA's runtime call chain reaches 101 of MEIC's 160 methods.** Approximately **4,473 LOC of MEIC code** must be ported into the standalone `HydraStrategy` class. Up from my initial surface-level estimate of "35 methods" — the deeper count is honest because HYDRA's `_handle_idle_state` / `_handle_monitoring` / `_initiate_entry` etc. all chain transitively through MEIC internals.

## Breakdown

> **Note**: the rows below are NOT a clean partition — "HYDRA
> overrides" and "`super()` delegations" overlap (an override can also
> call `super()`), so the sub-counts do not sum exactly to 101/160.
> Read them as overlapping facets of the same method set, not disjoint
> buckets.

| Category | Count | Action |
|---|---|---|
| Reachable from HYDRA via call chain | 101 | Port into standalone HydraStrategy |
| HYDRA overrides (calls own version) | 25 | Already in HydraStrategy — verify still works post-port |
| HYDRA explicit `super()` delegations | 14 | These are the contract — must port the parent versions |
| Pure inherited (HYDRA never touches but framework calls) | 61 | Must port — silent dependencies |
| Unreachable from HYDRA — dies with MEIC | 59 | Leave in MEIC; deleted when MEIC dir is rm'd |

## Top 10 largest methods HYDRA depends on (port-effort heavy hitters)

| Rank | Method | MEIC line | LOC | Port type |
|---|---|---|---|---|
| 1 | `_recover_positions_from_saxo()` | 5348 | 267 | HYDRA reimplements — rename to `_recover_positions_from_broker` |
| 2 | `_close_position_with_retry()` | 4276 | 171 | Inherited; called from HYDRA's `_execute_stop_loss` |
| 3 | `_place_option_order()` | 2934 | 167 | Inherited; called from `_execute_entry` — **highest Saxo coupling** |
| 4 | `_execute_stop_loss()` | 4035 | 141 | HYDRA reimplements + calls super() |
| 5 | `__init__()` | 828 | 125 | HYDRA's __init__ calls super() — port the parent state init |
| 6 | `_initiate_entry()` | 1810 | 117 | HYDRA reimplements |
| 7 | `_execute_entry()` | 2782 | 117 | Inherited |
| 8 | `get_dashboard_metrics()` | 7798 | 101 | HYDRA delegates via super() |
| 9 | `log_daily_summary()` | 7357 | 101 | Inherited |
| 10 | `_reconstruct_entry_from_positions()` | 6056 | 100 | HYDRA reimplements |

## Direct `super()` calls from HYDRA (the strict surface contract)

These 14 sites are where HYDRA explicitly delegates to MEIC — every one of these MUST have its parent ported:

| HYDRA line | HYDRA method | MEIC line called |
|---|---|---|
| 473 | `__init__` | 828 |
| 1072 | `_handle_idle_state` | 1524 |
| 1148 | `_should_attempt_entry` | 1693 |
| 1252 | `_is_entry_time` | 1783 |
| 1568 | `_handle_monitoring` | 1609 |
| 5333 | `_execute_stop_loss` | 4035 |
| 6068 | `_validate_pnl_sanity` | 8444 |
| 6601 | `get_status_summary` | 6704 |
| 6677 | `get_detailed_position_status` | 6730 |
| 7301 | `log_daily_summary` | 7357 |
| 8232 | `get_dashboard_metrics` | 7798 |
| 8293 | `get_daily_summary` | 6656 |
| 8348 | `get_recommended_check_interval` | 7708 |
| 9588 | `_reconcile_positions` | 6202 |

## Full list of 101 reachable MEIC methods (by line number)

```
  828  __init__
 1071  _parse_entry_times
 1524  _handle_idle_state
 1609  _handle_monitoring
 1693  _should_attempt_entry
 1721  _get_next_entry_time
 1727  _skip_missed_entries
 1770  _minutes_until
 1783  _is_entry_time
 1810  _initiate_entry
 1986  _get_vix_adjusted_spread_width
 2024  _calculate_strikes
 2168  _get_occupied_short_strikes
 2191  _adjust_for_strike_conflicts
 2231  _adjust_for_same_strike_overlap
 2319  _adjust_for_long_strike_overlap
 2388  _warn_if_strike_illiquid
 2464  _calculate_stop_levels
 2524  _validate_entry_credit
 2554  _estimate_entry_credit
 2650  _check_minimum_credit_gate
 2725  _simulate_entry
 2782  _execute_entry
 2934  _place_option_order
 3172  _verify_position_exists
 3215  _add_orphaned_order
 3236  _has_orphaned_orders
 3298  _validate_order_size
 3356  _get_current_position_size
 3386  _monitor_fill_slippage
 3478  _get_option_uic
 3527  _get_position_id_from_order
 3566  _get_fill_price
 3617  _verify_entry_fill_prices
 3715  _get_todays_expiry
 3719  _register_position
 3756  _handle_naked_short
 3822  _unwind_partial_entry
 3877  _check_stop_losses
 3964  _batch_update_entry_prices
 4008  _extract_mid_price
 4018  _simulate_entry_prices
 4035  _execute_stop_loss
 4276  _close_position_with_retry
 4515  _check_spread_for_emergency_close
 4560  _is_position_shared
 4592  _get_position_amount
 4613  _update_registry_for_partial_close
 4682  _verify_position_closed
 4795  _wait_for_spread_normalization
 4838  _get_close_fill_price
 4980  _spawn_async_fill_correction
 5055  _wait_for_pending_fill_corrections
 5228  _record_api_result
 5250  _open_circuit_breaker
 5299  _trigger_critical_intervention
 5321  _is_daily_loss_limit_reached
 5348  _recover_positions_from_saxo
 5836  _recover_from_state_file_uics
 5944  _group_positions_by_entry
 6004  _parse_spx_option_position
 6056  _reconstruct_entry_from_positions
 6202  _reconcile_positions
 6282  _reset_for_new_day
 6545  _log_entry
 6576  _log_stop_loss
 6614  _log_safety_event
 6643  _send_daily_summary
 6656  get_daily_summary
 6704  get_status_summary
 6730  get_detailed_position_status
 6897  _get_effective_stop_level
 6902  _get_saxo_pnl_for_entry
 6949  _get_total_saxo_pnl
 6974  _load_cumulative_metrics
 6995  _save_cumulative_metrics
 7020  _calculate_max_loss_with_stops
 7054  _calculate_max_loss_catastrophic
 7095  _calculate_capital_deployed
 7237  _calculate_sortino_ratio
 7357  log_daily_summary
 7513  _save_state_to_disk
 7672  get_monitoring_mode
 7708  get_recommended_check_interval
 7725  _entry_is_win
 7755  _entry_is_breakeven
 7776  _entry_is_loss
 7798  get_dashboard_metrics
 7965  _check_buying_power
 8080  _check_market_halt
 8128  _adjust_strike_for_liquidity
 8192  _adjust_long_wing_for_liquidity
 8295  _validate_system_clock
 8327  _is_clock_reliable
 8349  _validate_config
 8444  _validate_pnl_sanity
 8530  _check_pnl_sanity
 8607  _should_batch_alert
 8630  _send_batched_stop_alert
 8674  _queue_stop_alert
 8755  _flush_batched_alerts
```

## Uncertainty flags (verify during port)

1. `_deferred_stop_fill_lookup()` (71 LOC) — only called if orphaned fills detected post-stop. May be edge case.
2. `_handle_missing_positions` / `_check_if_position_merged` — reachable only when reconciliation finds gaps.
3. Some MEIC methods (e.g., `_handle_waiting_first_entry`) appear unreachable because HYDRA overrides state machine — verify HYDRA's overrides are complete.

## Implications for rewrite plan

- **LOC estimate revised**: HYDRA strategy.py grows from 11,143 → ~15,500 LOC (gains ~4,500 from MEIC port).
- **Days estimate revised**: original plan said 9 working days. With 101 methods to port + Saxo→IBKR translations in each, realistic estimate is **12-15 working days**.
- **Repo net deletion still substantial**: ~38K LOC deleted (4 dead bots + saxo_client + broker abstraction + tests), ~4,500 LOC added to HYDRA → **~33K LOC net repo shrinkage**.

## Source-of-truth

This audit is committed to source control as the basis for which methods to port. If during the rewrite a 102nd method turns out to be reachable, surface it as a discovery and update this doc.
