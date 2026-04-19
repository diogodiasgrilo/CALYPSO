# HYDRA Scaling Roadmap

**Created:** 2026-04-19
**Last updated:** 2026-04-19 (Downday-035 added to Phase 1)
**Status:** Phase 1 active (with Downday-035 deployed)

A phased plan for validating and scaling HYDRA. Each phase isolates ONE change so any issue is attributable to its specific cause. Do NOT skip phases — EXCEPT when a change is orthogonal (different slot, different code path) and can be validated in parallel, as with Downday-035 below.

---

## Current state (Phase 1 active, starting 2026-04-20)

**Active config on VM:**
- 2 base entries: E#2 (10:45), E#3 (11:15). E#1 (10:15) dropped at ALL VIX levels.
- E6 conditional: fires at 14:00 based on direction — **Upday-035** (put-only when SPX ≥ +0.25%) OR **Downday-035** (call-only when SPX ≤ -0.25%, added 2026-04-19).
- VIX regime: `max_entries [2, 2, 2, 1]`, min credits regime-dependent.
- Stops: `call_stop_buffer 0.75`, `put_stop_buffer 1.75`, MKT-042 decay 2.50×/4h.
- MKT-045 chain snap + MKT-046 stop anti-spike filter active.
- Contracts per entry: 1.

**Baseline numbers (for comparison against Phase 1+ results):**
- Lifetime P&L (Feb 10 – Apr 17): -$1,442.50 (pre–E#1 drop)
- Cumulative trading days: 41
- E#1 historical: 24% WR, -$79/entry avg (worst slot)
- E#2: +$26/entry avg
- E#3: +$10/entry avg

---

## Phase 1 — Validate E#1 drop + MKT-045/MKT-046
**Duration:** ~2 weeks (2026-04-20 through 2026-05-02)

**What's being validated:**
- E#1 drop reduces drawdown without proportional loss of upside
- MKT-045 chain snapping prevents "phantom strike" skips (April 15 bug)
- MKT-046 10-second timer filters false stops without missing real ones

**What to monitor in the logs:**
- `MKT-045_CHAIN_SNAP` — chain snapping fired (expect occasional firings)
- `STOP-DETAIL [FIRST_BREACH]` — stop triggered, 10s timer started
- `STOP-DETAIL [CONFIRMED]` / `MKT-046_FALSE_STOP_AVOIDED` — how many real vs filtered
- Daily P&L — should trend similar or better than pre-changes
- No new crash types or unexpected errors

**Go/No-go criteria for Phase 2:**
- [ ] At least 1 `MKT-046_FALSE_STOP_AVOIDED` has fired correctly
- [ ] All executed stops show legitimate sustained breach (per STOP-DETAIL logs)
- [ ] No HYDRA crashes, missed positions, or DATA-* errors
- [ ] E#2+E#3 performance flat or better than historical average
- [ ] No unexpected margin spikes

**Data already collected (through 2026-04-17):**
- Apr 17 E#1 stop: MKT-046 filtered 1 false breach at 10:49:20 (9s recovery), then correctly confirmed real stop at 10:49:48. Working as designed.

---

## Phase 2 — Drop E6 entirely (optional simplification)
**Duration:** ~1-2 weeks (starting ~2026-05-04, after Phase 1 validation)

Originally this was a choice between dropping E6 OR adding Downday-035. As of 2026-04-19, Downday-035 has already been deployed alongside Phase 1 (see "Phase 1b" below) because it's orthogonal to the other Phase 1 changes. The only remaining Phase 2 decision is whether to drop E6 entirely.

### Phase 2 (optional): Drop E6 entirely
**Change:** `conditional_upday_e6_enabled: false` AND `conditional_downday_e6_enabled: false`

**Effect:** E6 never fires. Margin slot (~$11k) fully idle. Simplifies strategy.

**When to pick this:** Only if Phase 1 + Phase 1b data shows E6 conditional entries are net negative or creating operational issues.

**Tradeoff:** Lose ~$243 (Upday-035 historical) + ~$1,100 est (Downday-035 historical) = ~$1,343 P&L, gain simplicity.

**Most likely outcome:** SKIP this phase — keep E6 with both Upday-035 and Downday-035 active.

---

## Phase 1b — Downday-035 (deployed 2026-04-19 alongside Phase 1)

**Status:** DEPLOYED

**Change:** Introduced call-only E6 when SPX drops ≥ 0.25% from open at 14:00 (mirror of Upday-035).

**Config on VM:**
```json
"conditional_downday_e6_enabled": true,
"conditional_downday_threshold_pct": 0.0025
```

**Historical data supporting Downday-035 (at time of deploy):**
- 11 of 11 historical down days (26% of trading days) would have been wins at VIX-scaled OTM
- Estimated ~$100-145 average credit at 14:00 (70% of put credit due to put skew)
- Estimated +$1,100 additional P&L over the 42-day sample
- All 11 candidates pass MKT-011 credit gate at their respective VIX regimes

**Logic:**
- E6 slot fires at 14:00
- Up day (SPX ≥ +0.25%) → put-only (Upday-035)
- Down day (SPX ≤ −0.25%) → call-only (Downday-035)
- Flat day → skip

**Integration with existing rules:**
- **MKT-038 FOMC T+1:** Both Downday-035 and MKT-038 force call-only on T+1 down days. Downday-035 wins (more specific reason); override_reason = "downday-035". On T+1 flat/up days: existing behavior unchanged.
- **MKT-011 credit gate:** Handles per-regime credit floors. If credit too low, skips automatically.
- **Base-entry downday call-only:** Separate feature (0.57% threshold at morning entries E#2-E#3). Coexists without conflict — different slots.
- **VIX gate:** No VIX gate on call-only entries (mirrors MKT-040). Fires at any VIX level.

**Go/No-go criteria (2-week observation window ~2026-04-20 to 2026-05-03):**
- [ ] Downday-035 has triggered at least 2-3 times without unexpected behavior
- [ ] Credit gate correctly skips when credit too low for VIX regime
- [ ] No conflict with MKT-038 (FOMC T+1) or base-downday logic
- [ ] override_reason = "downday-035" appears correctly in Sheets/dashboard/telegram

**Rollback:** Single config flip — `"conditional_downday_e6_enabled": false` + restart HYDRA. Upday-035 unaffected.

---

## Phase 3 — Double E#2 and E#3 contracts
**Duration:** Open-ended (starting ~2026-05-18 or later)

**Change:** `contracts_per_entry: 2`

**Effect:**
- Every E#2 and E#3 now uses 2 contracts instead of 1
- Capital deployed jumps from ~$22k to ~$44k per day
- P&L and drawdowns both scale 2x
- Expected annual profit: ~$54k vs current ~$32k (based on Feb–Apr data extrapolation)

**Prerequisites:**
- [ ] Phase 1 validated (MKT-045/MKT-046 working cleanly)
- [ ] Phase 2 complete (E6 path chosen and stable)
- [ ] Account shows ability to absorb -$1,500 daily drawdown without margin stress
- [ ] No open issues or bugs flagged in previous phases

**First week at 2x — watch carefully:**
- Monitor margin utilization (stay below 85% of available)
- Watch stops for any unusual slippage (larger orders can have worse fills)
- Verify the bot handles 2-contract closes correctly (different commission pattern)
- Check that Position Registry tracks multiple contracts per entry correctly

**Worst-case drawdown at 2x (based on historical data):**
- Single day max loss: ~$2,180 (historical: March 20 at 1x was -$1,090)
- Represents ~4.3% of $51k margin — acceptable
- Consecutive bad days: ~$5k cumulative — represents ~10% drawdown

**Rollback trigger:**
If first week at 2x shows any unexpected behavior (margin issues, fill problems, bugs), roll back to 1x:
```bash
# On VM:
.venv/bin/python3 -c "import json; c=json.load(open('bots/hydra/config/config.json')); c['strategy']['contracts_per_entry']=1; json.dump(c, open('bots/hydra/config/config.json','w'), indent=2)"
sudo systemctl restart hydra
```

---

## Summary timeline

| Date | Phase | Action |
|------|-------|--------|
| **2026-04-17** | Pre-Phase 1 | MKT-045/MKT-046 deployed, E#1 drop config edit |
| **2026-04-19** | Phase 1 start | HYDRA restarted with new config |
| **~2026-05-02** | Phase 1 review | Decide Phase 2 path (2a or 2b) |
| **~2026-05-04** | Phase 2 start | Either drop E6 or add Downday-035 |
| **~2026-05-16** | Phase 2 review | Decide Phase 3 go/no-go |
| **~2026-05-18** | Phase 3 start | Double contracts to 2x |
| **~2026-05-25** | Phase 3 review | Permanent config or rollback |

---

## Discipline — do NOT skip phases

Each phase isolates ONE change:
- Phase 1: MKT-045 + MKT-046 + E#1 drop combined (all deployed together on 2026-04-17)
- Phase 2: E6 changes (drop or add Downday)
- Phase 3: Contract scaling

If something goes wrong:
- **Wrong in Phase 1:** Blame the E#1 drop or the new stop filters → debug those
- **Wrong in Phase 2:** Blame the E6 change → revert that specifically
- **Wrong in Phase 3:** Blame contract scaling → revert to 1x

Mixing changes = mixing causes = slow debugging and lost money.

---

## Open questions for future phases

1. **Should we tune the 10-second MKT-046 timer?** Need 10+ stop events to decide.
2. **Should we try put_stop_buffer variations?** Currently $1.75 — data may suggest $1.50 or $2.00.
3. **Is the 25pt min_otm_distance still right?** After E#1 drop, later entries tend to be wider — may never hit 25pt floor.
4. **Should we scale contracts further (3x)?** Only after 2x is validated for 1-2 months.
5. **POT-based strike selection?** Backtesting scripts exist (`pot_strike_recommender.py`). Evaluate after Phase 3.

---

## Weekly tracking template

Run this at the end of each week to track phase progress:

```
Week ending: YYYY-MM-DD
Phase: [1/2a/2b/3]

Trading days this week: N
Total stops: N (expected ~N based on prior data)
MKT-046_FALSE_STOP_AVOIDED events: N
MKT-045_CHAIN_SNAP events: N

Weekly P&L: $X
Worst single-day loss: $X
Best single-day gain: $X

Anomalies / issues: [none | describe]

Phase advance recommendation: [Yes / No / Wait another week]
```

HERMES daily reports + CLIO weekly report already capture most of this automatically.
