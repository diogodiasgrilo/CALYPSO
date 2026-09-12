# State File Schema Design — IB-Only HYDRA

> **⚠️ SUPERSEDED — design not adopted.** P7-audit H13 (2026-05-22)
> found this document materially inaccurate: the `*_uic` → `*_instrument_id`
> and `*_position_id` → `*_order_id` renames described below were
> **never implemented**. The actual implementation keeps the legacy
> field names (`*_uic`, `*_position_id`) and stores the IBKR conid in
> the `*_uic` field; `*_position_id` is always `None` on the IBKR
> path. `data_recorder.py`'s SQLite schema (currently v8) was NOT
> renamed either. Save/load are internally symmetric so it is not a
> runtime bug — but the document below describes a design abandoned in
> favour of keeping field names stable. Kept as a record of the
> rejected proposal; **do not rely on it**.

---

**Decision**: No standalone migration script. New HYDRA writes the new
schema natively; old Saxo state files get archived (not converted) at
cutover. Cumulative metrics are preserved via a one-shot copy.

---

## Files in scope

| File | Owner | What it stores |
|---|---|---|
| `data/hydra_state.json` | HYDRA bot | Today's open entries, daily P&L, recent stop events |
| `data/hydra_metrics.json` | HYDRA bot | Cumulative lifetime metrics (P&L, win rate, etc.) |
| `data/position_registry.json` | PositionRegistry shared module | Multi-bot position isolation (single-bot world: largely vestigial but cheap to keep) |
| `data/backtesting.db` | DataRecorder + HOMER | SQLite of trades, ticks, OHLC for backtesting |
| `data/shared/brandon_gex_profile.json` | Brandon variants cross-process cache | GEX snapshot for cross-variant sharing |

## Schema differences (Saxo era → IB era)

### `hydra_state.json` field renames

| Saxo era | IB era | Reason |
|---|---|---|
| `short_call_uic: 12345678` | `short_call_instrument_id: 883539497` | Saxo UIC vs IBKR conid. `instrument_id` is broker-agnostic — preserves the option to swap brokers later without another rename. |
| `long_call_uic` | `long_call_instrument_id` | Same |
| `short_put_uic` | `short_put_instrument_id` | Same |
| `long_put_uic` | `long_put_instrument_id` | Same |
| `position_id: "saxo-position-uuid"` | `order_id: "ibkr-order-id"` | Saxo positions had stable IDs we tracked; IBKR positions are derived from order IDs. Strategy code already uses order IDs primarily — this just makes the field name match reality. |
| `(absent — Saxo activity stream state)` | `(absent)` | Activity-stream state goes away with Saxo. |
| `(absent — session capabilities)` | `(absent)` | Session capability dance goes away. |

Other fields stay identical:
- `call_spread_credit`, `put_spread_credit`
- `call_side_stop`, `put_side_stop`
- `call_side_stopped`, `put_side_stopped`, `call_side_expired`, etc.
- `trend_signal`, `vix_regime_at_entry`, `override_reason`
- `entry_number`, `entry_time`, `contracts`

### `hydra_metrics.json` — NO schema change

Cumulative metrics like `cumulative_pnl`, `total_entries`, `winning_days`,
`losing_days`, `total_commission` are broker-agnostic. The file shape
stays identical across the cutover.

**Migration step**: copy the existing file as-is at cutover time. No
transformation needed. `cp data/hydra_metrics.json data/hydra_metrics.json`
(literally a no-op if we don't archive — but archive recommended).

### `position_registry.json` — schema change negligible

Currently stores `{instrument_id, bot_name, ...}` per registered
position. The `instrument_id` field name is already broker-agnostic.
IB era just stores conids in the same field name. No schema change.

### `backtesting.db` — column rename, soft

DataRecorder schema v7 has columns like `short_call_strike`, `bid_ask_width`,
`spx_at_entry` — all broker-agnostic. No rename needed.

However: schema also has `short_call_uic` and similar columns. These
get renamed to `short_call_instrument_id` via a schema v8 migration.

Migration approach: `ALTER TABLE` statements at DataRecorder startup
detect the v7 → v8 transition and rename columns in place. Existing
rows retain their values (just under new column names). Backtesting
queries against historical Saxo data still work.

## Cutover migration sequence

When we eventually swap Saxo HYDRA for IB HYDRA on the VM:

1. **Stop** Saxo HYDRA via systemctl
2. **Verify** all positions settled (`active_entries` returns 0 in state file, or it's after 4PM ET on a normal trading day)
3. **Archive** `data/hydra_state.json` to `data/archive/hydra_state_saxo_final_YYYYMMDD.json`
4. **Preserve** `data/hydra_metrics.json` as-is (no rename, no schema change)
5. **DataRecorder v8 migration** runs on first start of new HYDRA — renames columns in `backtesting.db` if v7 detected
6. **Start** new IB HYDRA — writes fresh `data/hydra_state.json` in new schema; continues from preserved metrics file

The new HYDRA's first day starts with an empty state file. This is fine
because at the cutover point, all 0DTE positions have already settled.

## Dashboard backend compatibility

The dashboard reads `data/hydra_state.json` for live position display.
After the schema rename:
- Dashboard backend gets updated to read `instrument_id` instead of `uic`
- Field renames are search-and-replace in dashboard/backend/*.py
- Frontend type definitions update accordingly
- Update happens in the same logical commit cluster as HYDRA's state-write
  port (commits 8-N of the rewrite plan)

## What we DON'T need

- ❌ A standalone migration script for `hydra_state.json` — new HYDRA writes
  fresh schema from day 1
- ❌ A back-compat read shim — Saxo HYDRA stops before new HYDRA starts;
  no cross-version concurrent reads of state files
- ❌ Live data conversion at startup — DataRecorder handles its own
  schema migration; that's the only persistent broker-tagged data

## What we DO need (covered in later commits)

- ✅ DataRecorder v8 schema migration (commit later in rewrite)
- ✅ Dashboard backend field rename (commit alongside HYDRA state-port)
- ✅ Cutover runbook documenting steps 1-6 above (commit at Phase NEW-3)
- ✅ Archive script `scripts/archive_saxo_state.sh` for step 3 (small,
  written when we reach cutover phase)
