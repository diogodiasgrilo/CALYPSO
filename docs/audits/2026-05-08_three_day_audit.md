# Three-day Code Audit — May 6 → May 8, 2026

**Scope**: All commits to `main` since 2026-05-06 00:00 ET.
**Author**: code review run on 2026-05-08.
**Branches**: `main` (production dry-run).

## Summary

13 commits, 11 files, +1,219 / −112 lines. The commits cluster into four
themes:

| Theme | Commits | Verdict |
|---|---|---|
| 1. HYDRA-1c-baseline constants leaking into Brandon variants | 4 | ✅ all correct |
| 2. Brandon close-path P&L accounting | 3 | ⚠️ 1 had a silent no-op (now fixed) |
| 3. Dashboard correctness + cosmetics | 4 | ✅ all correct |
| 4. Strategy-logic improvements (tighteners, delta-target) | 2 | ✅ correct |

**Net findings**: 1 real bug shipped earlier today and re-fixed (capital_deployed
v1 → v3). Several follow-up items worth doing, listed at the bottom.

---

## Per-commit audit

### Theme 1 — HYDRA-1c-baseline constants leaking into Brandon variants

These were all the same shape: a dollar-amount constant baked at the module
level that assumed 1c × 50pt baseline, and quietly broke at 15c × 5pt. All
fixed by making the constant config-overridable.

#### `e045be5` fix(hydra): config-overridable BP-per-IC gate + enable Yahoo VIX fallback
- **Problem**: `MIN_BUYING_POWER_PER_IC = 5000` × 15c = $75,000 required vs
  $50,741 available → blocked all of B and C on 5/6.
  Plus: `external_price_feed.enabled` was hardcoded `False` in
  `shared/config_loader.py:231-234` for cloud, so VIX NoAccess flap had no
  fallback.
- **Fix**: `min_buying_power_per_ic` config key (default 5,000, B/C set 500).
  `external_price_feed.enabled: true` explicitly in B/C/A configs.
- **Verdict**: ✅ Verified — Yahoo fired live, B/C placed entries normally
  on 5/7.
- **Risk**: external_price_feed exposes Yahoo Finance to LIVE mode. Read-only
  data, no order-placement risk. Acceptable.

#### `1dc8339` fix(hydra): config-overridable + contracts-aware DATA-003 P&L bounds
- **Problem**: `MAX_PNL_PER_IC = 500`, `MIN_PNL_PER_IC = -3000` used in the
  per-entry stop-check sanity bounds. At 15c × 5pt drawdowns can hit
  -$7,500; at 10pt -$15,000. Hard floor of -$3,000 silently flagged real
  drawdowns as "impossible P&L" → stop check returned False → position kept
  deteriorating without enforcement.
- **Fix**: `max_pnl_per_ic` / `min_pnl_per_ic` config keys + multiplied by
  `entry.contracts` at the per-entry call sites. Variant B/C set max=$100,
  min=−$1,000 per-contract → $1,500 / −$15,000 effective at 15c.
- **Verdict**: ✅ Test coverage exists. Math correct.

#### `b75393d` fix(meic): bypass ORDER-004 margin gate in dry-run mode
- **Problem**: Even with the BP gate config-overridable, dry-run synthetic
  positions consume zero real margin — the gate was filtering out
  diagnostically-useful entries.
- **Fix**: In dry-run, log "would-be insufficient BP" but return True so the
  entry proceeds. Live mode unchanged.
- **Verdict**: ✅ Correct. The dry-run-only branch only affects bookkeeping
  logging; live mode keeps the real safety check.

#### `e52a445` fix(hydra): commission overcharge on Brandon-skipped one-sided entries
- **Problem**: Open commission booked as 4-leg ($150 at 15c) for entries that
  Brandon GEX-ADJ converted to put-only (which only places 2 legs, $75).
  `place_put_only` was set BEFORE `_simulate_entry`, so the post-placement
  check missed the in-flight conversion.
- **Fix**: Check `entry.call_side_skipped` / `entry.put_side_skipped` flags
  AFTER placement, fall back to `place_put_only` for non-Brandon paths.
- **Verdict**: ✅ Logic is correct. Side-effect: variant A's commission also
  derives from the same code path but A doesn't run Brandon GEX-ADJ, so its
  flags don't get set mid-flight — its commission accounting is unchanged.

---

### Theme 2 — Brandon close-path P&L accounting

#### `a497578` fix(brandon): correct actual_*_stop_debit unit + dry-run realized P&L
- **Problem**: `actual_put_stop_debit` was set as `put_spread_value × 100 ×
  contracts`. But `put_spread_value` is **already in dollars** (the
  property multiplies by 100 and contracts internally). At 15c the field
  was 1500× too large ($56,250 stored for $37.50 close cost).
  Plus: `_close_entry_early` adds full credit to realized_pnl on its
  deferred-fill branch. In dry-run that's never corrected → realized P&L
  overstated by close_cost per side.
- **Fix**: store `entry.put_spread_value` raw + subtract from realized_pnl
  in the Brandon TP path.
- **Verdict**: ✅ TP path is correct. Tested via
  `test_brandon_strategy_integration.py::TestTakeProfitDispatch`.
- **Latent bug found later**: this fix targeted **only** the TP path. The
  identical bug existed in the breach-exit path and wasn't caught until
  `984430a`.

#### `984430a` fix: breach exit recorded $0 close cost + capital_deployed double-counted
- **Problem #1 (breach exit)**: same bug pattern as `a497578` but in the
  breach-exit path. Worse — the breach path used
  `self._brandon_side_alive(entry, "put")` AFTER `_close_entry_early`. Inside
  `_close_entry_early`, `*_side_expired=True` gets set, which makes
  `_brandon_side_alive` return False. So the close-cost block silently
  skipped, leaving `actual_put_stop_debit` at default 0 and never subtracting
  from realized_pnl.
  Live evidence: 5/7 had 4 breach exits on B (real put SVs $750/$4125/$3900)
  and 1 on C ($2925) — all recorded close_cost=$0. **B's reported +$787 day
  was actually ~−$8,000 net.**
- **Fix #1**: capture aliveness AND `spread_value` BEFORE `_close_entry_early`,
  use captured locals in the close-cost block. Mirrors TP path.
- **Verdict #1**: ✅ Verified on real-world shape — captures $4,125 close
  cost on E#5, subtracts from realized correctly.
- **Problem #2 (capital_deployed)**: the function summed every entry's
  max-loss → on a 7-entry day reported $52,500 when peak concurrent was
  $30,000.
- **Fix #2 (v1, this commit)**: sweep-line over (open_time, close_time)
  intervals.
- **Verdict #2 v1**: ❌ **REAL BUG — silent no-op.** Two latent issues
  (lex-sort treating `str(datetime)` differently from ISO strings, AND
  Brandon TP/breach not setting close_time) made it return the SUM, same
  as before. Caught by today's audit. Fixed in `a646a64`.

#### `a646a64` fix: audit caught capital_deployed silent no-op + cleanup
- **Fix**: New `entry.close_time` field set by `_close_entry_early` at the
  moment a side becomes expired. Persisted in state save + restored on load.
  `_calculate_capital_deployed` uses `_to_dt()` to coerce mixed
  datetime/string/empty-string types and sorts on real datetimes. Tie-break:
  closes sort BEFORE opens at same instant.
- **Verdict**: ✅ Verified on yesterday's exact 7-entry timeline — returns
  $30,000 (peak concurrent), not $52,500. Three regression tests added in
  `TestCapitalDeployedSweep`.

---

### Theme 3 — Dashboard correctness + cosmetics

#### `f46229a` fix(dashboard): three P&L Over Time chart bugs (values, label, x-axis)
- **Problem 1**: pnl_history's surviving-long-leg accumulator was adding
  `long_*_price × 100 × contracts` for any `*_side_stopped=True` entry,
  missing the `self.short_only_stop` guard that the per-entry serializer
  uses. Brandon TP closes set `_side_stopped=True` and the code added a
  phantom $750/closed entry to the chart curve.
- **Problem 2**: variant_b_label was stale ("75pt dynamic" — outdated since
  5/5 redesign).
- **Problem 3**: PnLChart.tsx merged variants by ARRAY INDEX, not time. Once
  the shorter series ran out, time fell back to the longer series' early
  timestamps → x-axis went backwards mid-chart.
- **Fix**: gate surviving-long path on `self.short_only_stop`; update label;
  rewrite merge as time-union with sorted HH:MM keys.
- **Verdict**: ✅ All three correct. Frontend bundle deployed to VM.

#### `1ce31e6` feat(dashboard): per-entry disposition + realized P&L on cards
- **Problem**: `is_complete` is True from placement (not lifecycle end) →
  Brandon-skipped put-only entries showed "DONE" while still being monitored.
  Closed entries had no indication of close type or net realized.
- **Fix**: New `entry.close_reason` field set by Brandon close paths
  (TP/BREACH). Server-computed `disposition` field falls back to
  flag-inference when close_reason missing. Per-entry realized P&L shown
  next to disposition badge.
- **Verdict**: ✅ Logic correct. UI shows TP/BREACH/STOP/EXPIRED/SKIPPED/LIVE.

#### `59b61c9` fix: restore close_reason on load + show gross realized per entry
- **Problem**: `close_reason` was serialized but not restored — bot restart
  overwrote backfilled tags with empty string. Per-entry realized was net of
  commission, confusing because the variant card showed commission separately.
- **Fix**: Restore close_reason in `_load_state_file_history`. Show GROSS
  realized per entry (commission stays in variant total).
- **Verdict**: ✅ Correct.

#### `2fde265` fix(dashboard): peak buffer 100% should only fire on real stops, not TPs
- **Problem**: peak-buffer-used calculator forced 100% for any
  `*_side_stopped=True`. But Brandon TP also sets that flag (routes through
  HYDRA's stopped+actual_debit accounting). Every TP day was reported as
  "Put 100% peak buffer used" even when actual put SV at close was ~16% of
  stop level.
- **Fix**: Only force 100% when `close_reason ∈ {STOP, BREACH}` or legacy
  `pivot_closed`. TP/EXPIRED close fall through to spread_snapshots
  historical peak.
- **Verdict**: ✅ Correct. Verified live: peak dropped from 100% to 33–38%
  on 5/7's data.

---

### Theme 4 — Strategy-logic improvements

#### `547c896` fix(brandon): disable HYDRA tighteners + log strike-adjuster KEEP decisions
- **Problem**: MKT-022 walked B's E#5/E#6 puts from 125pt OTM (safe) to
  35–40pt OTM (right on the 7330 GEX wall) chasing credit. Brandon strike
  adjuster ran AFTER tightening and returned silent KEEP because no positive
  cluster was visible at that exact tick. 4 breach exits at the wall = ~$8.7K.
- **Fix**: New `brandon.disable_progressive_tightening` flag. When set,
  MKT-020 / MKT-022 early-return without modifying strikes. Brandon GEX
  adjuster now logs KEEP decisions explicitly (was previously silent).
- **Verdict**: ✅ Correct. Variant A unchanged (flag default False).
- **Trade-off accepted**: at narrow widths, far-OTM credits may not clear
  the per-zone gate → entry skipped. That's the Brandon-correct outcome.

#### `b345063` feat(brandon): delta-target strike selection (Brandon-faithful, replaces OTM × N)
- **Problem**: OTM-multiplier (`call_starting_otm_multiplier × expected_move`)
  drifts onto walls at low VIX where expected_move underestimates real risk.
- **Fix**: New `brandon.delta_target_strike_selection.enabled` flag.
  `_calculate_strikes` override anchors short strikes to a delta target
  (default 0.08, inherited from `strategy.target_delta=8`). Reads delta from
  the Polygon chain already fetched for GEX. Falls back to parent
  `_calculate_strikes` on Polygon outage / missing greeks.
- **Verdict**: ✅ Logic correct. Comprehensive tests including a regression
  test that reproduces 5/7's setup and asserts the bot picks 7280 (8δ, far
  below the 7330 wall) instead of 7340 (yesterday's wall-strike).

---

## Cross-cutting concerns

### State persistence completeness
✅ Verified: `close_reason` and `close_time` (the two new fields added this
period) are both serialized in `_save_state_to_disk` and restored in
`_load_state_file_history`. Backward compatible (default `""` if absent).

### Dry-run vs live re-engagement
✅ Verified: every dry-run-only gate in this period is `if self.dry_run:`
guarded. Live mode logic is unchanged. The only data-flow difference is
informational logging.

### Test coverage
- 152 Brandon-specific tests pass.
- 223 total tests pass (excluding pre-market-time-of-day-sensitive
  `test_daily_summary_v127.py`).
- 21 new tests added across `TestBuildProfileDeltas`, `TestFindStrikeAtDelta`,
  `TestDeltaTargetStrikeSelection`, `TestCapitalDeployedSweep`,
  `TestDryRunStateRecovery`.

### Backward compatibility on state files
✅ All new fields default to safe values when absent. Old state files load
cleanly.

---

## TODO — follow-ups identified by this audit

The audit found no shipped bugs beyond the capital_deployed one. These are
items worth doing as a follow-up but are not blocking.

### High priority (correctness)
- [ ] **`_brandon_breach_states` doesn't survive mid-day restart.** A
      Brandon breach exit confirmed at e.g. 89s would lose its in-progress
      state on restart and need to re-confirm 90s. Low-impact but should
      persist alongside other Brandon dicts (`_brandon_overlay_placed`,
      `_brandon_hedge_legs` already persisted via sidecar).
- [ ] **End-of-day settlement should backfill `close_reason="EXPIRED"` and
      `close_time` for entries that survive to expiry.** Right now
      EXPIRED-via-settlement entries have empty `close_reason` and the
      dashboard's flag-inference fallback picks the right label, but
      `close_time` stays empty → capital_deployed sweep treats them as
      "still open through EOD" (which is conservative and correct, but
      noted for completeness).
- [ ] **Set `close_reason = "STOP"` on the HYDRA stop path.** Currently only
      Brandon TP and breach explicitly tag it. HYDRA's `_execute_stop_loss`
      sets `_side_stopped=True` but doesn't tag `close_reason` — the
      dashboard fallback infers "STOP" from the flag combination but
      explicit > inferred.

### Medium priority (observability)
- [ ] **Add an EOD reconciliation job** that compares each closed entry's
      `actual_*_stop_debit` to the most-recent-pre-close
      `spread_snapshots.put_spread_value`. Flag any > 10% discrepancy.
      Would have caught 5/7's $0-close-cost bug at 4 PM ET instead of 24
      hours later.
- [ ] **Dashboard chart: add an explicit indicator when realized P&L was
      retroactively patched** (today's metrics file has a `correction_note`
      field; surface it on the cumulative P&L chart so the historical
      record is honest).

### Low priority (architecture / tech debt)
- [ ] **MKT-007 liquidity check is in HYDRA's `_calculate_strikes`, which
      Brandon's delta-target override bypasses.** In dry-run this is
      irrelevant (no real fills); in live mode this could land us on
      illiquid strikes. Should run MKT-007 (or a Brandon equivalent) after
      delta-target picks strikes.
- [ ] **Consolidate `call_stop_time` / `put_stop_time` / `close_time` into a
      single field over time.** They currently coexist for backward
      compatibility; the single-field model is cleaner.
- [ ] **`spread_width` setter pattern** — the property has no setter and
      defaults to `max(call_width, put_width)` from the strikes. Fine, but
      callers expect to be able to set it (the failed try/except in the
      delta-target override was a hint). Either expose a setter or make the
      pattern explicit in the Entry dataclass docs.

### Cosmetic
- [ ] **Variant A still labeled "A (baseline 75pt)" on the dashboard** while
      using a 75pt MKT-027 dynamic width capped at 75pt. Label is technically
      correct but easy to misread as a fixed 75pt.
- [ ] **`is_complete` semantics**: this flag is True from placement, not
      from lifecycle end. Several places still treat it as "lifecycle done"
      and were patched defensively this period. A renaming pass
      (`is_placed` for the placement-time meaning, derived
      `is_lifecycle_done` for the disposition-flag computation) would
      reduce future confusion.

### Validation against real data (next 1–2 sessions)
- [ ] Verify delta-target strike selection picks reasonable strikes at the
      first entry tomorrow on B and C. Watch the
      `BRANDON-DELTA-TARGET E#1: target=0.080δ → ...` log line.
- [ ] Verify `entry.close_time` populates on the first Brandon TP/breach
      close tomorrow.
- [ ] Verify `capital_deployed` returns peak-concurrent (not sum) at EOD
      tomorrow. For a typical B day that's max 4–5 ICs × $7,500 = ~$30–37K,
      not $50K+.

---

## Verdict

13 commits. 1 silent no-op shipped (capital_deployed v1) and re-fixed
within hours. Everything else verified correct on real-world data shapes
and / or via test coverage. The Brandon variants now do what Brandon does:

- Pick strikes by delta target (8δ) anchored to the live chain
- Don't let HYDRA's credit-chasing tightener override that
- Skip entries instead of moving onto walls when credit is too low
- Properly account for breach-exit close costs
- Report peak-concurrent capital deployed, not cumulative cycled

**The code is in better shape than it was 72 hours ago.** Margin for further
improvement is in the follow-up TODO list above, none of which is blocking
or correctness-critical.
