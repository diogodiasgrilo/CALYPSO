# HYDRA 2-Contract Scaling Plan

**Goal:** Make HYDRA safe to run at `contracts_per_entry ≥ 2` by fixing all hardcoded per-1-contract assumptions discovered in the 2026-04-21 audit **and** the senior-tester follow-up audit that expanded scope to data persistence, agents, Telegram alerts, dashboard, and journal.

**Current state:** Running 1 contract. All money math (fills, P&L, commissions, spread_value) scales correctly via `* self.contracts_per_entry` or `* entry.contracts`. The scaling bugs are concentrated in three layers: **(a)** stop-level additive constants (buffers, theoretical put, floor), **(b)** data-persistence schemas that don't record contract count per row (making historical comparisons ambiguous at mixed counts), and **(c)** presentation surfaces (Telegram, dashboard, agent prompts, journal) that display raw dollar amounts without contract-count context.

**Design principle — scale at USE SITE, not at config load.**
- Config semantics stay in per-contract dollars (`call_stop_buffer: 0.75` → $0.75 option premium per contract).
- Every use of a per-contract-dollar constant that participates in a stop or margin comparison against a contracts-scaled value gets an explicit `* self.contracts_per_entry`.
- Pros: explicit in diffs, no mixed units in logs/Telegram `/config`, `/set` validation ranges stay intuitive, recovery from old state files unchanged.
- Cons: multiple touch points — mitigated by the enumerated checklist below.

---

## Pre-flight reference — current code

| Name | Stored value unit | Must scale vs. |
|---|---|---|
| `self.call_stop_buffer` | per-contract $ (×100) | `call_spread_credit`, `call_spread_value` (both ×contracts) |
| `self.put_stop_buffer` | per-contract $ (×100) | `put_spread_credit`, `put_spread_value` (both ×contracts) |
| `self.downday_theoretical_put_credit` | per-contract $ (×100) | `call_spread_credit` (×contracts) |
| `MIN_STOP_LEVEL = 50.0` | per-contract $ | stop_level (×contracts) |
| `MIN_BUYING_POWER_PER_IC = 5000` | per-IC-at-1-contract $ | actual margin need (×contracts) |
| `self.long_salvage_min_profit = 10.0` | per-IC-at-1-contract $ | `appreciation` (×contracts) |

| Name | Status | Touch points |
|---|---|---|
| `entry.call_spread_credit`, `entry.put_spread_credit` | ✅ scaled ×contracts at assignment | — |
| `entry.call_spread_value`, `entry.put_spread_value` | ✅ scaled ×contracts via `@property` | meic/strategy.py:412-426 |
| Fill→credit conversion | ✅ `fill_price * 100 * self.contracts_per_entry` | meic/strategy.py:2959-2960 |
| Commissions | ✅ `* self.contracts_per_entry` everywhere **except MKT-018 block** (Item 3) | — |
| Credit gate (`_estimate_entry_credit` vs `min_viable_credit_*`) | ✅ both sides per-contract — naturally correct | meic/strategy.py:2473, 2494 |

---

## Items — ordered by blast radius

### Item 1 — Scale stop buffers in stop formulas 🔴 BREAKING

**Problem:** At 2 contracts, `call_spread_credit` is $X×2 but `call_stop_buffer` is still $75 per contract. `stop_level = credit + buffer` mixes scaled and unscaled terms. Stops trigger too early.

**Files to edit:** [bots/hydra/strategy.py](bots/hydra/strategy.py)

**Changes (live path — `_calculate_stop_levels_hydra`):**

| Location | Before | After |
|---|---|---|
| L5221 (call-only stop) | `stop_level = base_stop + self.call_stop_buffer` | `stop_level = base_stop + self.call_stop_buffer * self.contracts_per_entry` |
| L5239 (put-only stop) | `stop_level = base_stop + self.put_stop_buffer` | `stop_level = base_stop + self.put_stop_buffer * self.contracts_per_entry` |
| L5256-5257 (full IC) | `call_stop_level = base_stop + self.call_stop_buffer`<br>`put_stop_level  = base_stop + self.put_stop_buffer` | Each `+ buffer` becomes `+ buffer * self.contracts_per_entry` |
| L5505 (MKT-042 buffer decay) | `extra = buf * (self.buffer_decay_start_mult - 1) * decay_factor` | `extra = buf * self.contracts_per_entry * (self.buffer_decay_start_mult - 1) * decay_factor` |

**Changes (recovery path — `_reconstruct_entry_from_positions`, verified at L8720-L8750):**

| Location | Before | After |
|---|---|---|
| L8728 (put-only recovery) | `stop_level = base_stop + self.put_stop_buffer` | `stop_level = base_stop + self.put_stop_buffer * entry.contracts` |
| L8745-L8746 (full IC recovery) | `call_stop_level = base_stop + self.call_stop_buffer`<br>`put_stop_level = base_stop + self.put_stop_buffer` | Each `+ buffer` becomes `+ buffer * entry.contracts` |

All three recovery paths (call-only at L8708, put-only at L8728, full IC at L8745-L8746) must match their live-path counterparts.

**Why "scale at use site" over "scale at config load":**
- `self.call_stop_buffer / 100` is displayed in 4 log lines and in `/set` feedback — those stay intuitive ($0.75).
- `/set call_stop_buffer 0.75` keeps the intuitive range (0.00–1.00).
- VIX regime overrides at [strategy.py:8124, 8129](bots/hydra/strategy.py#L8124) also stay per-contract dollars — no additional change needed there.
- Parent MEIC class doesn't use these buffer fields, so no cross-contamination.

**Log/display updates (concurrent with the math fix):**

| Line | Change |
|---|---|
| L5217-L5218 (MKT-040 log) | Append `× {self.contracts_per_entry}c` to the buffer term in the log message so the printed number matches the math actually applied. |
| L5243 (put-only log) | Same — show `buffer ${self.put_stop_buffer/100:.2f} × {contracts}c` |
| L5264, L5269 (full IC log) | Same for call/put buffers |
| L302 (startup log) | Append `× {self.contracts_per_entry}c effective` if contracts > 1 |

**Test (manual, before deploying live):**
1. Load config with `contracts_per_entry: 2`.
2. Log/print `_calculate_stop_levels_hydra()` output for a synthetic entry (credit $400 per side at 1 contract → $800 at 2 contracts).
3. Expected full-IC call stop at 2 contracts: `total_credit (×2) + call_stop_buffer × 2` = `$1600 + $150 = $1750`.
4. At 1 contract same trade would be: `$800 + $75 = $875`. The 2-contract stop should be exactly 2× the 1-contract stop — this is the invariant.

**Risk:** Medium. These are the paths that determine whether a real stop fires. Miswriting a line here would make stops fire late (money loss) or never (catastrophic). MUST validate with the invariant above before live.

---

### Item 2 — Scale theoretical-put term in call-only stops 🔴 BREAKING

**Problem:** MKT-040/035/038 call-only stop formula adds `downday_theoretical_put_credit` ($260 per contract) to the stop. At 2 contracts that addition stays $260 when it should be $520.

**File:** [bots/hydra/strategy.py](bots/hydra/strategy.py)

**Changes (live path — `_calculate_stop_levels_hydra`):**

| Location | Before | After |
|---|---|---|
| L5212 | `theoretical_put = self.downday_theoretical_put_credit` | `theoretical_put = self.downday_theoretical_put_credit * self.contracts_per_entry` |
| L5217 (log) | `theoretical put ${theoretical_put:.2f}` | `theoretical put ${theoretical_put:.2f} ({self.contracts_per_entry}c)` |
| L2030 (pretty-print helper returning descriptor string) | `f"call + ${self.downday_theoretical_put_credit / 100:.2f} theo put"` | `f"call + ${self.downday_theoretical_put_credit / 100:.2f} × {self.contracts_per_entry}c theo put"` |

**Changes (recovery path — `_reconstruct_entry_from_positions`, verified at L8690-L8710):**

| Location | Before | After |
|---|---|---|
| L8698 | `theoretical_put = getattr(self, 'downday_theoretical_put_credit', 260.0)` | `theoretical_put = getattr(self, 'downday_theoretical_put_credit', 260.0) * entry.contracts` |
| L8699 | `call_stop_buffer = getattr(self, 'call_stop_buffer', 35.0)` | `call_stop_buffer = getattr(self, 'call_stop_buffer', 35.0) * entry.contracts` |
| L8700 | `base_stop = credit + theoretical_put` | *(no change — theoretical_put already scaled above)* |
| L8708 | `stop_level = base_stop + call_stop_buffer` | *(no change — buffer already scaled above)* |
| L8709 | `stop_level = max(stop_level, MIN_STOP_LEVEL)` | `stop_level = max(stop_level, MIN_STOP_LEVEL * entry.contracts)` |

**Use `entry.contracts` (not `self.contracts_per_entry`) in recovery paths** — an entry opened yesterday at 2 contracts must retain 2-contract semantics even if config is flipped back to 1 contract before recovery runs.

**Verify with:**
```
grep -n "downday_theoretical_put_credit\|call_stop_buffer\|put_stop_buffer" bots/hydra/strategy.py
```
Every site that ADDS a buffer/theoretical-put to a stop level must have `* self.contracts_per_entry` (live path) or `* entry.contracts` (recovery path) nearby. Mere logging/descriptor uses (dividing by /100 for display) stay unscaled.

**Test:**
- Synthetic call-only entry, call_credit = $150 (1c) / $300 (2c).
- 1c stop: `$150 + $260 + $75 = $485`.
- 2c stop: `$300 + $520 + $150 = $970` = 2× the 1c stop. Invariant must hold.

**Risk:** High if missed — call-only entries on down-days (E6 downday, MKT-038 FOMC T+1, MKT-040 put-non-viable) at 2 contracts would be stopped on noise.

---

### Item 3 — Fix MKT-018 early-close commission accounting 🟡 DORMANT

**Problem:** MKT-018 early-close path forgets to multiply commission by `contracts_per_entry`.

**File:** [bots/hydra/strategy.py:1854-1855](bots/hydra/strategy.py#L1854-L1855)

**Change:**
```python
# Before
entry.close_commission += self.commission_per_leg
self.daily_state.total_commission += self.commission_per_leg

# After
entry.close_commission += self.commission_per_leg * self.contracts_per_entry
self.daily_state.total_commission += self.commission_per_leg * self.contracts_per_entry
```

**Why also now:** MKT-018 is currently disabled (`early_close_enabled: false`), so this is dormant. Fix it in the same PR to prevent a latent landmine if MKT-018 is ever re-enabled. Cheap change, 2 lines.

**Test:** No live test. Just read the change. Consistency with the 12 other commission sites that all use `* self.contracts_per_entry`.

**Risk:** Zero at deploy (code path inactive).

---

### Item 4 — Scale MKT-033 long-salvage threshold 🟡 MINOR

**Problem:** `long_salvage_min_profit = $10` is compared against `appreciation = (bid - open) * 100 * entry.contracts` (total $). At 2 contracts the threshold fires on half the per-contract appreciation it was designed for.

**File:** [bots/hydra/strategy.py:538, 4983](bots/hydra/strategy.py#L538)

**Change:** Scale at use site (line 4983):
```python
# Before
if appreciation < self.long_salvage_min_profit:

# After
threshold = self.long_salvage_min_profit * entry.contracts
if appreciation < threshold:
```

Also update the log strings at L4987 and L4994 to reference `threshold` instead of `self.long_salvage_min_profit`, so logs show the actual effective threshold.

**Why `entry.contracts` not `self.contracts_per_entry`:** `entry.contracts` is stamped on the entry at creation and survives contract-size config changes mid-day. Matches the pattern at L4983's neighboring `* entry.contracts` multiplication.

**Context check:** MKT-033 long salvage only runs when `short_only_stop: true`. Currently `false`. This is also dormant, but hygiene-worthy.

**Test:** Unit check: at `entry.contracts=2`, appreciation of $15 (which = $0.075 per option share per contract) should NOT trigger salvage — threshold becomes $20.

**Risk:** Low. Salvage path is inactive under current config.

---

### Item 5 — Scale ORDER-004 pre-entry margin gate 🟡 LATENT SAFETY

**Problem:** `MIN_BUYING_POWER_PER_IC = 5000` is a hardcoded constant, not scaled by contracts. At 2 contracts, per-IC worst-case margin is ~$21k — the $5k gate is non-functional.

**File:** [bots/meic/strategy.py:147, 7519](bots/meic/strategy.py#L147)

**Change (at use site):**
```python
# meic/strategy.py:7519 — Before
required = MIN_BUYING_POWER_PER_IC

# After
required = MIN_BUYING_POWER_PER_IC * self.contracts_per_entry
```

Also update the adjacent log message (L7522) to print `Required: ${required:,.2f} ({self.contracts_per_entry}c × ${MIN_BUYING_POWER_PER_IC:,.0f})`.

**NOT changing the constant itself** so other downstream readers (MEIC at 1c) are unaffected. Scaling at the one use site is the surgical fix.

**Alternative considered — raise the constant:** rejected. Harder to reason about, affects MEIC base class which is shared with Iron Fly (even if paused). Use-site scaling is cleaner.

**Test:** Synthetic — with `available=$15000` and `contracts_per_entry=2`, gate must now REJECT (required = $10k, passes) vs with `contracts_per_entry=3` where required = $15k should reject at $14,999 available.

**Risk:** Low — tightening a gate that was previously a no-op. Only false-positive risk is if real margin per IC is comfortably under $5k per contract (which it isn't — worst case is $21k/IC at 2 contracts).

---

### Item 6 — Scale MIN_STOP_LEVEL floor 🟢 EDGE CASE

**Problem:** `MIN_STOP_LEVEL = 50.0` is a safety floor. Only matters if `credit` is near zero (API sync failure). At 2 contracts, the floor should be $100 to match the 1-contract semantic of "stops must be at least $50/contract of room."

**Files:**
- [bots/hydra/strategy.py:5199, 5226, 5242, 5249 (or similar)](bots/hydra/strategy.py#L5199)
- [bots/meic/strategy.py:2339, 3688, 5847](bots/meic/strategy.py#L2339)

**Change (at use site in HYDRA only — MEIC class isn't used by active bot):**
```python
# Before
MIN_STOP_LEVEL = 50.0
if credit < MIN_STOP_LEVEL: ...

# After
MIN_STOP_LEVEL = 50.0 * self.contracts_per_entry
if credit < MIN_STOP_LEVEL: ...
```

Or introduce at top of `_calculate_stop_levels_hydra`:
```python
min_stop_level = 50.0 * self.contracts_per_entry
```
Then use `min_stop_level` instead of `MIN_STOP_LEVEL` throughout the function.

**Apply to every `MIN_STOP_LEVEL` reference inside `_calculate_stop_levels_hydra`** — grep shows ~4 uses in that function.

**Meic base class** (lines 2339, 3688, 5847): leave alone. Iron Fly/MEIC are paused; no current use.

**Test:** Contrived — force `credit = 30` with `contracts_per_entry=2`, stop should be floored at $100 (not $50).

**Risk:** Zero in normal operation — only engages on fill price lookup failures (extremely rare).

---

## Data persistence layer

### Item D-1 — Backtesting DB: add `contracts` column to 4 tables 🔴 BLOCKER (historical comparability)

**Problem:** Schema v7 tables `trade_entries`, `trade_stops`, `spread_snapshots`, `shadow_entries` do NOT record contract count per row. At 2 contracts, a row with `total_credit: 1400` is indistinguishable from two days at 1 contract with `total_credit: 700` each. Any cumulative analysis, HERMES/CLIO comparison to historical averages, or backtest calibration becomes ambiguous.

**File:** [services/homer/db_manager.py](services/homer/db_manager.py) — schema definitions around L46-L85.

**Migration (bump to schema v8):**

1. Add `contracts INTEGER NOT NULL DEFAULT 1` to:
   - `trade_entries`
   - `trade_stops`
   - `spread_snapshots`
   - `shadow_entries`
2. Do NOT add to `market_ticks`, `market_ohlc_1min`, `entry_mae_mfe`, `daily_summaries` (these are already contract-agnostic or have a different semantic).
3. Actually — `daily_summaries` SHOULD get a `contracts_per_entry INTEGER NOT NULL DEFAULT 1` column so per-day context is captured for analytics rollup.
4. Write migration block in `ensure_schema()` that detects v7 → v8, runs `ALTER TABLE ... ADD COLUMN contracts INTEGER NOT NULL DEFAULT 1` for each of 5 tables, updates `schema_info.version = 8`.
5. Existing rows default to `contracts = 1` — historically correct because ALL data prior to the flip is at 1 contract.

**File:** [shared/data_recorder.py](shared/data_recorder.py) — `record_entry`, `record_stop`, `record_spread_snapshot`, `record_shadow_entry`, `record_daily_summary`.
- Add `contracts` key to each insert dict.
- Source: `entry.contracts` at call site (live bot knows this). HOMER backfill reads from `metrics.json.daily_returns[date].contracts_per_entry` (see Item D-3 below).

**Call sites to update in bots/hydra/strategy.py** — where `_data_recorder.record_*` is invoked. Grep:
```
grep -n "_data_recorder\.\|record_entry\|record_stop\|record_spread_snapshot\|record_shadow_entry" bots/hydra/strategy.py
```
At each call, pass `contracts=entry.contracts` (or `self.contracts_per_entry` for per-day summaries).

**Test:**
1. Backup `data/backtesting.db` before deploy.
2. Run migration on backup. Verify 4 tables have new column, all existing rows have `contracts=1`.
3. Live insert from HYDRA running at 1 contract → `contracts=1`. At 2 contracts → `contracts=2`.

**Risk:** Medium. Migration is additive (ALTER ADD COLUMN with default). Cannot break existing queries. But if migration fails partway, `schema_info.version` may stay at 7 — wrap in `BEGIN IMMEDIATE / COMMIT` transaction.

---

### Item D-2 — Metrics file: add `contracts_per_entry` to `daily_returns` 🟡 ANALYTICS

**Problem:** `data/hydra_metrics.json` tracks cumulative P&L, win/loss counts, per-day `daily_returns` array. Nothing in the per-day record notes what contract count was used. Cumulative averages silently mix 1c and 2c days.

**File:** [bots/meic/strategy.py](bots/meic/strategy.py) (inherited by HYDRA) — `log_daily_summary` around L6975-L6997, `cumulative_metrics["daily_returns"].append(...)` site.

**Change:** Append `"contracts_per_entry": self.contracts_per_entry` to each per-day record:
```python
cumulative_metrics["daily_returns"].append({
    "date": today_str,
    "pnl": net_pnl,
    "contracts_per_entry": self.contracts_per_entry,  # NEW
    ...
})
```

Backward compatibility: existing entries lacking this key should be treated as `contracts_per_entry=1` by downstream readers. Add a loader helper that `.get("contracts_per_entry", 1)`.

**Test:** After first 2-contract day, inspect `data/hydra_metrics.json`. The new day's record has `contracts_per_entry: 2`; prior days unchanged.

**Risk:** Zero. Additive JSON key.

---

### Item D-3 — State file entry serialization confirms `contracts` field 🟢 VERIFY-ONLY

**Status:** Verified safe by audit (see [strategy.py:7730, 9018, 8566](bots/hydra/strategy.py)). `entry.contracts` IS saved and restored. No code change needed. Add a one-line assertion in recovery:
```python
assert hasattr(restored_entry, "contracts") and restored_entry.contracts >= 1, \
    "State file corruption: entry missing contracts field"
```
Only as a sanity check; not required.

**Risk:** Zero.

---

### Item D-4 — Google Sheets: add `Contracts` column to Daily Summary tab 🟡 ANALYTICS

**Problem:** Daily Summary tab logs `Total Credit`, `Stop Loss Debits`, `Daily P&L`, etc. without noting contract count per row. Mixed-contract rows in the same column cause visual misreads ("credits declining" vs "contracts doubled").

**File:** [shared/logger_service.py](shared/logger_service.py) — `log_daily_summary` writer around L2071-L2173 (in HYDRA-specific section).

**Changes:**
1. Add `"Contracts"` to header row (define as a new column immediately after `Date`).
2. In the daily row, insert `self.contracts_per_entry` as the second value.
3. Header row exists once at tab creation; we need a one-time migration to add the column to existing sheet without re-creating it. Safest approach: leave existing headers alone, append `Contracts` as a RIGHT-most column for new days only. Document in the tab notes that rows prior to 2026-04-XX implicitly had `Contracts=1`.

**Alternative:** create a new tab `Daily Summary v2` with the new schema, leave old tab frozen. Cleaner but adds a tab.

**Recommendation:** append as right-most column. Simpler. Script to backfill the "1" value into all historical rows is optional.

**Test:** After deploy + first 2c day, verify the tab has a new `Contracts` column with "2" for today's row, blank/1 for prior rows.

**Risk:** Low. Column append doesn't break downstream readers (`get_all_records` returns dicts; new key appears).

---

### Item D-5 — Position registry: no change needed 🟢 VERIFY-ONLY

Registry stores position ownership keyed by position_id/bot_name. `Amount` from Saxo is used by Fix #45 (merged-position detection) as an integer; no `Amount == 1` check anywhere. At 2 contracts, merged entries have Amount ≤ 4 and the partial-close math already handles arbitrary counts. **No change.**

**Risk:** Zero.

---

## Telegram alerts + commands

### Item T-1 — Append contract context to every P&L-bearing alert 🟡 SAFETY

**Problem:** Alerts show `P&L: +$78.50` / `Credit: $310` without contract count. User reading on phone can't tell if +$156 at 2c = per-contract improvement or not. Risk decisions on misread data.

**File:** [shared/alert_service.py](shared/alert_service.py) — alert body construction in `position_opened`, `position_closed`, `stop_loss`, `wing_breach`, `profit_target`, `emergency_exit`, `circuit_breaker`, `daily_summary_*`.

**Change pattern:** Every alert body that prints a dollar amount gets an additional trailing annotation. Two options:

**Option A (terse):** append `({contracts}c)` — e.g. `P&L: +$156.00 (2c)`.

**Option B (informative):** append `({per_contract}/c × {contracts}c)` — e.g. `P&L: +$156.00 ($78.00/c × 2c)`.

**Recommendation: Option B** for all money-bearing alerts. The per-contract number is what HERMES historical averages are denominated in; showing it inline keeps comparisons sane at a glance.

**Implementation:** Add a parameter `contracts: int = 1` to each method in `alert_service.py`, default 1 for backwards compatibility. HYDRA callers pass `self.contracts_per_entry` or `entry.contracts`.

**Alert types requiring this treatment** (grep `AlertType\.` for the authoritative list):
- ENTRY_OPENED / POSITION_OPENED
- ENTRY_CLOSED / POSITION_CLOSED
- STOP_LOSS
- WING_BREACH
- PROFIT_TARGET
- EMERGENCY_EXIT
- CIRCUIT_BREAKER (only if body has $)
- DAILY_SUMMARY
- ITM_RISK_CLOSE (Delta Neutral, not HYDRA — skip)

**Alert types NOT requiring this treatment:**
- BOT_STARTED / BOT_STOPPED (no P&L in body — but see Item T-3)
- ENTRY_SKIPPED (contains reason strings, no $)
- MARKET_STATUS alerts
- VIGILANT_* alerts (Delta Neutral — skip)

**Test:** With `contracts_per_entry=2`, place a dry-run entry. Verify ENTRY_OPENED Telegram message shows both total and per-contract credit.

**Risk:** Low (body string change only). Keep default `contracts=1` so any missed call-site remains readable.

---

### Item T-2 — `/config` command highlights contract count prominently 🟡 SAFETY

**Problem:** `/config` response lists 20+ params; `contracts` is buried. User flipping to 2 may not realize every other number they see is 2× what it was yesterday.

**File:** [bots/hydra/telegram_commands.py](bots/hydra/telegram_commands.py) — `/config` handler (around L677-L694 per the audit).

**Change:** Prepend a banner line at the TOP of the response when `contracts > 1`:
```
⚠️ CONTRACTS/ENTRY = {N} (all credits, stops, P&L are scaled ×{N})
```
At `contracts=1`, show a subtler line:
```
Contracts/entry: 1
```

**Test:** `/config` while at 2 contracts → banner at top. While at 1 → subtle line.

**Risk:** Zero.

---

### Item T-3 — BOT_STARTED alert body includes contracts 🟢 COSMETIC

**File:** [bots/hydra/main.py:222](bots/hydra/main.py#L222) — alert body:
```python
message=f"HYDRA started in {mode} mode.\nState: {status.get('state', 'Unknown')}, Entries today: {status.get('entries_completed', 0)}"
```

**Change:** Append `\nContracts/entry: {strategy.contracts_per_entry}` after State line.

**Risk:** Zero.

---

### Item T-4 — `/status`, `/snapshot`, `/entry`, `/lastday`, `/week`, `/account` responses annotate contracts 🟡 CLARITY

**File:** [bots/hydra/telegram_commands.py](bots/hydra/telegram_commands.py) + `build_telegram_*` methods in [bots/hydra/strategy.py](bots/hydra/strategy.py) (called via callbacks registered in main.py).

**Change:** Each response that shows dollar figures appends a trailing line:
```
(all figures at {contracts}c per entry)
```
— OR inlines `/c` divisions alongside totals (consistent with T-1).

**Responses to update:**
- `/status` → header line
- `/snapshot` → each position row
- `/entry N` → credit/stop lines
- `/lastday` → summary line
- `/week` → table footer
- `/account` → lifetime summary footer
- `/stops` → stop debit line

**Test:** Send each command in dry-run at 2c. Confirm annotation visible.

**Risk:** Zero (display only).

---

## Agents (HOMER, HERMES, CLIO, APOLLO, ARGUS)

### Item A-1 — HOMER: capture and persist contract count per day 🟡 AGENT QUALITY

**Problem:** HOMER writes the journal's daily row AND backfills the backtesting DB. Neither currently records contract count. Narrative generator (Claude) sees raw dollar figures with no contract context.

**Files:**
- [services/homer/data_collector.py](services/homer/data_collector.py) — `collect_daily_data()` or similar aggregator.
- [services/homer/narrative_generator.py](services/homer/narrative_generator.py) — system prompt + `_build_data_context()`.
- [services/homer/journal_updater.py](services/homer/journal_updater.py) — Section 2 (Daily Summary table) row builder.
- [services/homer/db_manager.py](services/homer/db_manager.py) — already covered in Item D-1.

**Changes:**

1. `data_collector.py`: read `contracts_per_entry` from `data/hydra_metrics.json.daily_returns[date]` (Item D-2) OR fall back to the live config. Pass through to downstream consumers.

2. `narrative_generator.py` — `_build_data_context()`: add `<contracts_per_entry>{N}</contracts_per_entry>` XML tag to the data context passed to Claude. Also amend SYSTEM_PROMPT (the CRITICAL RULES section) with:
```
All dollar figures in the data are TOTAL across {contracts_per_entry} contracts.
When describing performance, you MAY note per-contract values (divide by {contracts_per_entry})
but all references to "credit collected" or "P&L" refer to the TOTAL.
Do NOT assume 1 contract — always check the <contracts_per_entry> tag.
```

3. `journal_updater.py` Section 2 row builder: add `Contracts` row to the Daily Summary Data table (inserted after `SPX Low` row). Value from Item D-2.

**Test:** Run HOMER with `--dry-run` on a 2c day. Inspect:
- Narrative observations reference total + per-contract correctly.
- Journal Section 2 has new `Contracts` row with `2` for today.

**Risk:** Medium-low. Claude prompt change is semantic — will need one real run to verify Claude obeys the new rule.

---

### Item A-2 — HOMER: add Contracts row to journal Section 2 table 🟡 JOURNAL FORMAT

**Problem:** Section 2's wide table has one row per metric, one column per day. Adding `Contracts` as a new row keeps the format and lets future readers see at-a-glance which days were 1c vs 2c.

**File:** [docs/HYDRA_TRADING_JOURNAL.md](docs/HYDRA_TRADING_JOURNAL.md)

**One-time manual insert** of the `Contracts` row (all 1s for historical days) BEFORE first 2c day, so HOMER's section updater can just append today's value:

Row position: after `VIX Low`, before `Entries Completed`. Values: `1` for every historical column. HOMER (via Item A-1 + D-1) will write `2` for the first 2c day onward.

**Test:** After manual insert, HOMER's next run must successfully parse and update the table. Dry-run with `--dry-run` first.

**Risk:** Low — but a parsing bug in `journal_parser.py` at the new row would break HOMER. Dry-run is mandatory.

---

### Item A-3 — HERMES: summary template + analyzer prompt carry contract context 🟡 AGENT QUALITY

**Problem:** HERMES `<summary>` template at [services/hermes/analyzer.py:77](services/hermes/analyzer.py#L77) uses `{net_pnl}` as raw total. Compared internally (line 80: `cumul {cumulative_pnl}`) to historical running totals that are all denominated at 1c. At 2c the comparison is meaningless.

**Files:**
- [services/hermes/data_collector.py](services/hermes/data_collector.py) — cheat_sheet builder.
- [services/hermes/analyzer.py](services/hermes/analyzer.py) — SYSTEM_PROMPT + `<summary>` template.

**Changes:**

1. `data_collector.py`: add `contracts_per_entry` and `net_pnl_per_contract = net_pnl / contracts_per_entry` to the `cheat_sheet` dict. Also add `avg_win_per_contract`, `avg_loss_per_contract`, `cumulative_pnl_per_contract_equiv` (sum of historical per-contract daily P&Ls — computed from `daily_returns[].pnl / daily_returns[].contracts_per_entry` thanks to Item D-2).

2. `analyzer.py` `<summary>` template (L77-L81): update to include per-contract view:
```
{net_pnl} net ({net_pnl_per_contract}/c × {contracts_per_entry}c) | {clean_entries} clean, {entries_with_stops} stopped ({call_stops}C/{put_stops}P) | Day {day_number}
Best #{best_num} ({best_outcome}), Worst #{worst_num} ({worst_outcome})
Stops: {stop_side_pattern} | VIX {vix_open}→{vix_low} | {placed}/{total_scheduled} placed
{winning_days}W-{losing_days}L cumul {cumulative_pnl} (per-c equiv {cumulative_pnl_per_contract_equiv}) | Streak: {streak}
```

3. SYSTEM_PROMPT: add a CRITICAL RULE:
```
When comparing today's P&L to avg_win / avg_loss, compare PER-CONTRACT values:
today_per_c = today_net_pnl / today_contracts_per_entry
avg_win_per_c = cheat_sheet.cumulative.avg_win_per_contract
Otherwise the comparison is invalid across mixed-contract days.
```

**Test:** Run HERMES after first 2c day. Telegram summary should show both totals and per-contract. Compare manually to ensure cumulative equiv makes sense.

**Risk:** Medium. Claude may still slip into raw-total comparisons — monitor first run output.

---

### Item A-4 — CLIO: aggregate per-contract when mixing contract counts 🟡 AGENT QUALITY

**Problem:** CLIO at [services/clio/analyst.py](services/clio/analyst.py) quotes weekly P&L totals and equity-curve trends. At mixed contract counts, raw sums misattribute performance.

**Files:**
- [services/clio/data_aggregator.py](services/clio/data_aggregator.py)
- [services/clio/analyst.py](services/clio/analyst.py)

**Changes:**

1. `data_aggregator.py`: when building the weekly summary table, include a `Contracts` column per day. When computing week totals, compute both raw total AND per-contract-normalized total (`Σ daily_pnl / daily_contracts` × week-majority contract count).

2. `analyst.py` prompt: add to CRITICAL RULES:
```
If the week spans mixed contract counts (e.g., 3 days at 1c and 2 days at 2c):
- Quote each day's P&L AS-IS with contract count.
- Weekly total: quote both raw sum AND per-contract-equivalent.
- Equity curve: note contract-count transitions as a structural break.
```

**Test:** First week with any 2c day should see CLIO's Saturday report quote contract counts per day and note the transition.

**Risk:** Medium. Prompt-based; Claude compliance must be verified on first run.

---

### Item A-5 — APOLLO: no change 🟢 VERIFY-ONLY

APOLLO runs pre-market. Its output is about overnight news / VIX / expected move — contract-agnostic. No change.

### Item A-6 — ARGUS: no change 🟢 VERIFY-ONLY

ARGUS is a health monitor (bot process, API, token). Contract-agnostic.

---

## Dashboard (read-only monitoring)

### Item X-1 — Dashboard API exposes `contracts_per_entry` 🟡 FRONTEND DATA

**Problem:** Frontend shows entry cards, P&L panels, comparison views — all without contract context. Users making risk calls on the phone could misread.

**Files:**
- [dashboard/backend/routers/hydra.py](dashboard/backend/routers/hydra.py) — `/api/hydra/summary`, `/api/hydra/state`, `/api/hydra/bot-config`, `/api/hydra/entries`.
- [dashboard/backend/routers/widget.py](dashboard/backend/routers/widget.py) — `/api/widget`.
- [dashboard/backend/ws/dashboard.py](dashboard/backend/ws/) — WebSocket broadcasts.

**Changes:**

1. `/api/hydra/bot-config` already reads `bots/hydra/config/config.json` directly — just add `contracts_per_entry` to its response dict (it may already be included since it returns many config keys; verify).

2. `/api/hydra/summary` response: add top-level field `contracts_per_entry: int`.

3. `/api/hydra/entries`: each entry object already includes `contracts` implicitly via state serialization (Item D-3 confirmed). Ensure it's exposed in the API response schema.

4. `/api/widget`: add `"contracts_per_entry": N` to the flat JSON response.

5. WebSocket broadcast on state-change events: include `contracts_per_entry` in payloads so the frontend can re-render if config flips mid-day.

**Frontend-side changes (optional but recommended):**
- Dashboard header: show `2c` badge when >1.
- Entry card: show `×2c` next to credit.
- History / Analytics: column for contract count in day-drill-down.
- iOS widget: show `2c` badge.

**Test:**
1. Deploy backend. `curl /api/hydra/summary` at 1c → field shows 1. Flip to 2c, restart HYDRA, curl again → shows 2.
2. WebSocket: connect and observe broadcast on next state update.

**Risk:** Low. Additive field.

---

## Journal (docs/HYDRA_TRADING_JOURNAL.md)

### Item J-1 — Add `Contracts` row to Section 2 table 🟡 (covered by A-2)

See Item A-2 above. One-time manual insert + HOMER updater change covers this.

### Item J-2 — Audit Section 5 (Key Performance Metrics) and Section 9 (Post-Improvement Tracking) for aggregate math 🟡 ANALYTICS

**Problem:** Sections 5 and 9 show metrics like "Avg Daily P&L", "Sharpe", cumulative totals. When 2c days are added, these aggregates must either be per-contract-normalized OR clearly label a contract-count transition.

**File:** [docs/HYDRA_TRADING_JOURNAL.md](docs/HYDRA_TRADING_JOURNAL.md) Sections 5 + 9.

**Change:** HOMER's journal_updater (Item A-1) must, when computing Section 5 aggregates:
- Split "Avg Daily P&L" into "Avg Daily P&L (1c days)" and "Avg Daily P&L (2c days)" whenever mixed history exists.
- Sharpe/Sortino computed on per-contract-normalized daily returns to stay comparable.
- Add a dated "Contract Count Change" note under Section 8 (Improvement Implementation Log) on the day of the flip.

**Test:** First 2c day's HOMER run should produce a Section 5 with the split rows and a new Section 8 entry "2026-04-22: Contracts/entry 1 → 2".

**Risk:** Medium. Needs careful prompt guidance for HOMER's narrative generator to do this correctly.

---

## Revised cosmetic items — logs, displays, comments

**C-1 — Comment at [meic/strategy.py:1776](bots/meic/strategy.py#L1776)**
```python
# Before: "4 legs × $2.50 = $10 per IC"
# After:  "4 legs × $2.50 × {contracts} contracts = ${4 * 2.5 * contracts} per IC"
```
Update the docstring/inline comment to reflect the scaled formula (no behavior change).

**C-2 — Entry log announces contract count**
At every `logger.info(f"Entry #{n} complete...")` site in [bots/hydra/strategy.py](bots/hydra/strategy.py), append ` ({self.contracts_per_entry}c)` to the completion message. Grep for `complete` inside HYDRA strategy and update 3-4 sites.

**C-3 — Stop logs include contract count**
Lines L5217-L5218, L5243, L5263-L5269 already log stop levels. Append `({self.contracts_per_entry}c)` to the formula description.

**C-4 — Startup banner**
In `__init__` (around L302 after buffers log), add:
```python
logger.info(f"  Contracts per entry: {self.contracts_per_entry}")
```
so the contract count is visible in the first screen of any log dump.

**C-5 — Telegram `/status` and `/entry`**
Check [bots/hydra/telegram_commands.py](bots/hydra/telegram_commands.py) for `/status` handler — add a line `Contracts/entry: {contracts}` to the response body.
`/entry N` response already includes strikes — append contract count near the strikes line.

**C-6 — Dashboard widget & `/api/widget`**
Add `contracts: self.contracts_per_entry` to the top-level widget payload at [dashboard/backend/routers/widget.py](dashboard/backend/routers/widget.py). Frontend change is optional (add a small "2c" badge next to P&L) but not required for safety.

**C-7 — Diagnostic `ask_sv` / `bid_sv` in `_log_stop_detail`** ([strategy.py:5521-5524](bots/hydra/strategy.py#L5521))
These compute diagnostic spread values and are compared with stop_level in log strings. If we change stop_level scaling, these diagnostic values should scale too for apples-to-apples comparison:
```python
ask_sv = ((sc_ask or 0) - (lc_bid or 0)) * 100 * self.contracts_per_entry
bid_sv = ((sc_bid or 0) - (lc_ask or 0)) * 100 * self.contracts_per_entry
```
Otherwise log messages show mismatched scales ("spread @ $850 vs stop @ $1750" — looks like massive cushion when actually at stop).

**C-8 — HERMES/HOMER report templates**
Check [services/homer/narrative_generator.py](services/homer/narrative_generator.py) and HERMES equivalent for any hardcoded "1 contract" in narrative prompts. Likely no code changes needed — they rely on data passed in. Verify with grep: `grep -rn "1 contract\|single contract\|per contract" services/`.

**C-9 — `order_limits` config unused**
[bots/hydra/config/config.json](bots/hydra/config/config.json) has `order_limits.max_contracts_per_order: 10` and `max_total_contracts: 30` — but code uses hardcoded `MAX_CONTRACTS_PER_ORDER = 10` / `MAX_CONTRACTS_PER_UNDERLYING = 30`. At 2 contracts × 4 legs × 2 entries (+2 legs for E6 one-sided) = 20 contracts/day max. Under 30 limit, no block. But the config key is dead code — either wire it up or remove it. **Leave for a separate PR; not 2-contract-scaling-critical.**

---

## Verification plan (before LIVE deploy)

**V-1 — Stop-level invariant unit test (Items 1, 2, 6)**
Write a throwaway Python script that instantiates HYDRA with `contracts_per_entry=2`, creates synthetic entries, and asserts:
```
stop_level(contracts=2) == 2 * stop_level(contracts=1)
```
for each of: full IC call side, full IC put side, call-only, put-only.

**Expected** (credit per side $400 at 1c / $800 at 2c; buffers $0.75 call / $1.75 put):

| Entry type | 1c stop | 2c stop | ratio |
|---|---|---|---|
| Full IC call | $800 + $75 = $875 | $1600 + $150 = $1750 | 2.000 ✓ |
| Full IC put | $800 + $175 = $975 | $1600 + $350 = $1950 | 2.000 ✓ |
| Call-only (credit $150 / $300) | $150 + $260 + $75 = $485 | $300 + $520 + $150 = $970 | 2.000 ✓ |
| Put-only (credit $200 / $400) | $200 + $175 = $375 | $400 + $350 = $750 | 2.000 ✓ |

Also verify the recovery path: same test but routed through `_reconstruct_entry_from_positions` using a fake position list and `entry.contracts=2`. Same ratios must hold.

If any ratio ≠ 2.000, a scaling site was missed.

**V-2 — Buffer decay invariant (Item 1 L5505 MKT-042)**
Synthetic entry with `entry_time` set 0h / 2h / 4h in the past. At each time point, compute stop_level at 1c and 2c. Ratio must stay 2.000. Verifies that `extra * self.contracts_per_entry` correctly decays at the scaled rate.

**V-3 — DB migration dry-run (Item D-1)**
Copy `data/backtesting.db` to a scratch location. Run `ensure_schema()` pointed at the copy. Verify:
- `schema_info.version` = 8
- 4 tables (trade_entries, trade_stops, spread_snapshots, shadow_entries) have `contracts INTEGER NOT NULL DEFAULT 1` column
- `daily_summaries` has `contracts_per_entry INTEGER NOT NULL DEFAULT 1`
- All existing rows have `contracts = 1` (or `contracts_per_entry = 1`)
- No rows lost, no row counts changed vs pre-migration

**V-4 — Metrics JSON additive change (Item D-2)**
Sanity-check: at 1c, `log_daily_summary` writes a new `daily_returns` entry with `contracts_per_entry: 1`. At 2c, writes `2`. Existing pre-fix entries remain with no `contracts_per_entry` key — readers must default to 1.

**V-5 — Grep consistency checks**
After edits, run:
```
grep -n "MIN_STOP_LEVEL" bots/hydra/strategy.py                # every use inside stop formulas must have * {contracts}
grep -n "call_stop_buffer\|put_stop_buffer" bots/hydra/strategy.py
grep -n "downday_theoretical_put_credit" bots/hydra/strategy.py
grep -n "MIN_BUYING_POWER_PER_IC" bots/meic/strategy.py
grep -n "long_salvage_min_profit" bots/hydra/strategy.py
grep -n "record_entry\|record_stop\|record_spread_snapshot\|record_shadow_entry" bots/hydra/strategy.py  # every call passes contracts
grep -rn "net_pnl\|total_credit\|cumulative_pnl" services/hermes/ services/clio/ services/homer/  # every template annotates contracts
```
No unscaled site in a stop formula. No `record_*` call missing `contracts=`.

**V-6 — Dry-run end-to-end on VM at 2 contracts**
Set `dry_run: true` + `contracts_per_entry: 2` on VM. Restart HYDRA. Simulate one entry and one stop.

Checklist of artifacts to inspect:
- [ ] Startup log shows `Contracts per entry: 2` banner line
- [ ] BOT_STARTED Telegram alert body contains `Contracts/entry: 2`
- [ ] Stop formula logs show explicit `× 2c` annotations
- [ ] ORDER-004 log shows `Required: $10,000 (2c × $5,000)`
- [ ] `_data_recorder.record_entry` inserts row with `contracts=2`
- [ ] ENTRY_OPENED Telegram alert shows both total and per-contract ($/c × 2c)
- [ ] STOP_LOSS Telegram alert (from simulated stop) shows contract-annotated P&L
- [ ] `/status`, `/snapshot`, `/config` responses all annotate `2c`
- [ ] Dashboard `/api/hydra/summary` response includes `contracts_per_entry: 2`
- [ ] Dashboard `/api/widget` returns `contracts_per_entry: 2`
- [ ] State file (`data/hydra_state.json`) saves entry with `contracts: 2`
- [ ] Restart bot mid-session — recovery restores entry at `contracts=2`, stop levels match V-1 table

Then flip back `dry_run: false` + `contracts_per_entry: 1` on VM (unless committing for tomorrow).

**V-7 — HOMER dry-run on a simulated 2c day**
Craft a test `daily_summaries` row with `contracts_per_entry=2` in the DB. Run `python -m services.homer.main --dry-run`. Verify:
- Journal Section 2 table gets a new column for today with a `Contracts` row value of `2`
- Narrative generator's data context includes `<contracts_per_entry>2</contracts_per_entry>`
- Observations mention both total and per-contract figures

**V-8 — HERMES dry-run with 2c cheat sheet**
Construct a synthetic cheat_sheet with `contracts_per_entry=2, net_pnl=300, net_pnl_per_contract=150`. Run the analyzer. Verify Telegram `<summary>` template renders `$300 net ($150/c × 2c)`.

**V-9 — ODYSSEUS pre-push audit (3 passes per CLAUDE.md)**
Pass 1 must specifically catch:
- Attribute verification — `entry.contracts` used everywhere in recovery paths; `self.contracts_per_entry` used in live paths; no mix-ups
- Every `record_*` call in strategy.py passes `contracts=`
- Every `send_alert(...)` call that shows $ in body includes the `contracts=` kwarg (Item T-1)
- Schema v7 → v8 migration block exists in `db_manager.py` inside a transaction
- VM config check: `contracts_per_entry` value in VM config matches intended test mode

---

## Deployment sequence (tomorrow, if proceeding)

### Phase 0 — Before market opens (pre-9:30 ET)

1. **Local edits on feature branch `scale-2-contracts`:**
   - Items 1, 2, 3, 4, 5, 6 (code scaling)
   - Item D-1 (DB schema v8 migration)
   - Item D-2 (metrics file additive key)
   - Items T-1, T-2, T-3, T-4 (Telegram/alert annotations)
   - Items A-1, A-2, A-3, A-4 (HOMER + HERMES + CLIO prompt changes)
   - Item X-1 (dashboard API fields)
   - Cosmetic items C-1 through C-8
2. Run V-1 (stop invariant), V-2 (buffer decay), V-4 (metrics additive), V-5 (grep). All clean.
3. Run V-3 against a copy of today's production `backtesting.db`. Schema moves to v8, row counts preserved.
4. Run V-7 (HOMER dry-run) + V-8 (HERMES dry-run) with synthetic 2c data.
5. Run V-9 ODYSSEUS 3-pass. Clean.
6. Commit + push with message enumerating every item number.

### Phase 1 — Safety deploy at 1 contract (regression check)

7. VM: `git pull`, clear `__pycache__`, **keep `contracts_per_entry: 1`**.
8. VM: backup `data/backtesting.db` to `data/backtesting.db.v7.bak`.
9. Restart HYDRA. Watch logs for 2 minutes. Verify:
   - Startup banner shows `Contracts per entry: 1`
   - DB migration completes (`schema_info.version = 8`)
   - Stop levels on any recovered entries match 1-contract expected values (the `× contracts_per_entry` with contracts=1 is a no-op regression check)
   - No new errors/warnings in logs
10. Send a Telegram command (`/status`) — response should show `Contracts/entry: 1` subtle line.

### Phase 2 — Journal prep (manual one-time)

11. Before 9:30 ET, manually insert the `Contracts` row into [docs/HYDRA_TRADING_JOURNAL.md](docs/HYDRA_TRADING_JOURNAL.md) Section 2 (Item A-2) — all existing columns get `1`, commit + push. This ensures HOMER's parser has a row to write into tonight.

### Phase 3 — Flip to 2 contracts (9:30-10:14 ET)

12. On VM: flip `contracts_per_entry: 1 → 2`, restart HYDRA.
13. Within 60 seconds of restart, verify:
    - Startup banner shows `Contracts per entry: 2`
    - BOT_STARTED Telegram alert body contains contract count
    - `/config` Telegram command → banner `⚠️ CONTRACTS/ENTRY = 2`
    - Dashboard `/api/hydra/bot-config` returns `contracts_per_entry: 2`
14. If ANY of the above check is wrong, execute rollback.

### Phase 4 — First entry (10:45 ET, E#2)

15. Confirm via Telegram `/snapshot` that entry schedule is intact.
16. Watch live log for E#2 entry placement:
    - ORDER-004 log: `Required: $10,000 (2c × $5,000)`, margin check passes
    - Credit gate passes based on PER-CONTRACT estimate
    - 4 legs × 2 contracts placed
    - ENTRY_OPENED Telegram alert shows total and per-contract credit
17. After fill, pull live Saxo margin: expected `MarginUsedByCurrentPositions` to jump by approximately `(max_width × 100 × 2) − total_credit`. Note actual vs theoretical to calibrate for E#3 and later E6.

### Phase 5 — Monitor E#3 and watch for E6 trigger (11:15-14:00 ET)

18. Repeat Phase 4 verification for E#3 at 11:15.
19. After both base entries: `MarginUsedByCurrentPositions` should be under $50k. If over, STOP HYDRA before 14:00 ET to block E6 — account would reject with `WouldExceedMargin` and entry might leave a leg orphaned.
20. If E6 triggers at 14:00 (up-day put-only or down-day call-only): the same ORDER-004 gate runs at `2c × $5k = $10k required`. Verify real margin at that moment permits it.

### Phase 6 — End of day

21. Settlement at 4:00 PM ET. Daily summary Telegram shows: total + per-contract breakdown.
22. HOMER runs at 7:30 PM ET. Inspect journal commit: Contracts row has `2`, Section 2 row for today is complete, narrative mentions 2c context, Section 8 log note "2026-04-22: Contracts/entry 1 → 2".
23. HERMES runs at 7:00 PM ET. Telegram report shows per-contract normalized comparison to historical averages.
24. Saturday: CLIO report should note the contract transition.

---

## Rollback plan

**If anything misbehaves within 5 minutes of first 2-contract entry:**
```bash
# Single command to drop back to 1 contract (no restart needed for live positions — this only affects NEW entries)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python - <<PY
import json, pathlib
p = pathlib.Path(\"bots/hydra/config/config.json\")
c = json.loads(p.read_text())
c[\"strategy\"][\"contracts_per_entry\"] = 1
p.write_text(json.dumps(c, indent=2))
print(\"Reverted to 1 contract\")
PY'"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart hydra"
```

**Existing positions at 2 contracts remain at 2 contracts** (recorded on the entry object at creation). Stop math for those positions uses the scaling factors correctly — they'll close at their proper 2c stop levels.

**If the code itself is broken (stop never fires, fills are wrong):**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && git revert --no-edit HEAD && git push'"
```
Then pull + clear cache + restart on VM.

---

## Out of scope (defer)

- Wiring up `order_limits` config keys (C-9). Separate cleanup PR.
- Frontend dashboard badge for contract count. Backend API exposes it; frontend change is polish.
- MEIC base class MIN_STOP_LEVEL / MIN_BUYING_POWER scaling. MEIC/Iron Fly are paused; fix if/when re-enabling.
- ORDER-006 hardcoded limits (10/order, 30/underlying) — no change needed at 2 contracts (peak = 20/day).

---

## Sign-off checklist before flipping to 2c

### Code (Items 1-6)
- [ ] Stop buffers scaled in live path (L5221, L5239, L5256-5257) + MKT-042 decay (L5505)
- [ ] Stop buffers scaled in recovery path (L8728, L8745-L8746)
- [ ] `downday_theoretical_put_credit` scaled in live path (L5212) + recovery (L8698)
- [ ] `call_stop_buffer` getattr fallback scaled in recovery (L8699)
- [ ] `MIN_STOP_LEVEL` scaled in every use inside `_calculate_stop_levels_hydra` + recovery (L8709)
- [ ] MKT-018 commission (L1854-L1855) multiplies by contracts_per_entry
- [ ] MKT-033 `long_salvage_min_profit` scaled at use site (L4983)
- [ ] `MIN_BUYING_POWER_PER_IC` scaled in `_check_buying_power` (meic/strategy.py:7519)

### Data (Items D-1 to D-5)
- [ ] Schema v7 → v8 migration block added to `db_manager.py`, wrapped in transaction
- [ ] All 4 `record_*` dicts in `data_recorder.py` accept `contracts` key
- [ ] Every `_data_recorder.record_*` call in `bots/hydra/strategy.py` passes `contracts=`
- [ ] `daily_summaries` table has `contracts_per_entry` column
- [ ] `log_daily_summary` in `bots/meic/strategy.py` writes `contracts_per_entry` to metrics `daily_returns`
- [ ] Google Sheets Daily Summary tab has new `Contracts` column (or manual backfill)
- [ ] V-3 migration dry-run passed (schema=v8, row count unchanged, all existing rows contracts=1)

### Alerts & Telegram (Items T-1 to T-4)
- [ ] `AlertService` method signatures accept `contracts: int = 1`
- [ ] Every money-bearing alert call in `bots/hydra/strategy.py` passes `contracts=self.contracts_per_entry` or `entry.contracts`
- [ ] `/config` command shows banner when contracts>1
- [ ] `BOT_STARTED` alert body includes contract count
- [ ] `/status`, `/snapshot`, `/entry`, `/lastday`, `/week`, `/account`, `/stops` responses annotate contracts
- [ ] V-6 dry-run shows all Telegram outputs contract-annotated

### Agents (Items A-1 to A-6)
- [ ] HOMER `data_collector.py` reads `contracts_per_entry` from metrics/config
- [ ] HOMER `narrative_generator.py` SYSTEM_PROMPT includes the "figures are TOTAL across N contracts" rule
- [ ] HOMER `_build_data_context()` emits `<contracts_per_entry>` XML tag
- [ ] HOMER `journal_updater.py` Section 2 writes `Contracts` row per day
- [ ] HERMES `data_collector.py` computes `net_pnl_per_contract`, `avg_win_per_contract`, `avg_loss_per_contract`
- [ ] HERMES `<summary>` template uses per-contract breakdown
- [ ] HERMES SYSTEM_PROMPT has the per-contract comparison rule
- [ ] CLIO `data_aggregator.py` adds `Contracts` column to weekly table
- [ ] CLIO `analyst.py` prompt has mixed-contract-week rule
- [ ] V-7 (HOMER dry-run) passed
- [ ] V-8 (HERMES dry-run) passed

### Dashboard (Item X-1)
- [ ] `/api/hydra/summary` exposes `contracts_per_entry`
- [ ] `/api/hydra/bot-config` includes contracts field (verify — may already)
- [ ] `/api/hydra/entries` exposes per-entry `contracts`
- [ ] `/api/widget` includes `contracts_per_entry`
- [ ] WebSocket broadcasts include the field

### Journal (Items A-2, J-1, J-2)
- [ ] Manual insert of `Contracts` row with all-1s in Section 2 committed BEFORE first 2c day
- [ ] HOMER will split Section 5 aggregates on next run (validated via V-7)

### Cosmetic
- [ ] Startup banner has `Contracts per entry: {N}` line
- [ ] Stop logs append `({contracts}c)` annotation
- [ ] Commission comments updated (C-1)
- [ ] Entry completion logs annotate contracts (C-2)
- [ ] Diagnostic `ask_sv`/`bid_sv` in `_log_stop_detail` scaled (C-7)

### Verification gates
- [ ] V-1 stop invariant ratios all = 2.000 (live path)
- [ ] V-1 stop invariant ratios all = 2.000 (recovery path)
- [ ] V-2 buffer decay ratios all = 2.000 across 0h/2h/4h
- [ ] V-3 DB migration dry-run clean
- [ ] V-4 metrics JSON additive check clean
- [ ] V-5 grep checks clean (no unscaled site in stop formula, every record_* has contracts=)
- [ ] V-6 end-to-end dry-run on VM passes every bullet in its checklist
- [ ] V-7 HOMER dry-run produces correct journal section
- [ ] V-8 HERMES dry-run produces correct per-contract summary
- [ ] V-9 ODYSSEUS 3-pass audit clean

### Operational
- [ ] Phase-1 safety deploy at 1c regression-passed (logs clean, no migration errors)
- [ ] DB backup taken (`data/backtesting.db.v7.bak`)
- [ ] Rollback command tested in a separate shell tab (config flip + restart)
- [ ] Margin snapshot confirms ≥$40k available for worst-case 2c peak
- [ ] Commit message lists every item number + file touched
- [ ] Communication plan: user is on standby during first 2c entries (Phase 4-5)
