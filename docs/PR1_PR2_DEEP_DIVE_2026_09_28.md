# PR-1 and PR-2 — deep dive, 2026-09-28

Follows [`SOURCE_FIDELITY_AUDIT_B_C_2026_09_28.md`](SOURCE_FIDELITY_AUDIT_B_C_2026_09_28.md).

> ## STATUS — updated 2026-09-29 04:40 ET
>
> | item | state |
> |---|---|
> | **Skip counterfactual** — score what we declined to place | ✅ **BUILT + DB POPULATED** (`scripts/score_skipped_entries.py`; 31 rows) |
> | **PR-1 SHIFT-FIRST shadow** | ✅ **BUILT + DEPLOYED** — schema **v18**, `gex_decisions.shift_first_json`, live since 04:35 ET. **Records only; the live path is unchanged and still SKIPs.** |
> | **Overlay arming telemetry** | ✅ **FIXED + DEPLOYED** — it had been recording into a dead branch for 23 days |
> | **PR-1 flip (SKIP → SHIFT in production)** | ⏳ **NOT DONE — awaiting shadow data.** Registered rule below. |
> | **PR-2 hedges** | ⛔ **deliberately NOT re-enabled** — see the recommendation below |
>
> Nothing else from this analysis is pending deployment.

---

# PR-1 · The GEX adjuster SKIPs where the source SHIFTs

## Finding 1 — SHIFT is unreachable when an accel zone fires

`gex_strike_adjuster.py` evaluates acceleration zones **first** and returns immediately:

```python
for c in accel_zones:
    if c.strike_low <= proposed_short <= c.strike_high:
        if abs(proposed_short - c.peak_strike) <= config.accel_peak_locality_pts:
            ...
            return AdjustResult(AdjustAction.SKIP, ...)   # returns here

decel_walls = profile.positive_clusters(...)              # only reached if no SKIP
```

So the case the source addresses — *"I would **manipulate the lower bands to move** a little bit
lower… to be able to have these areas of deceleration captured within"* — is the one case the
code cannot reach. If the strike is in a bad zone, we never try to move it.

## Finding 2 — SHIFT has never fired. Not once.

`gex_decisions`, variant B, consumer `adjuster`, **148 decisions**:

| action | count |
|---|---|
| KEEP | 125 |
| **SKIP** | **23** (21 call, 2 put) |
| **SHIFT** | **0** |

The only GEX placement behaviour the source describes has never executed in production.

## Finding 3 — every evaluable SKIP was wrong 🔴

Each SKIP vetoed a short strike as "inside a dangerous acceleration zone." Testing that claim
against the day's actual high/low:

**21 evaluable · 0 RIGHT · 21 WRONG.** The vetoed strike was **never breached**, once.

Not marginal, either:

| date | vetoed | day extreme | missed by |
|---|---|---|---|
| 2026-09-09 | call 7700 | high 7656.1 | **43.9pt** |
| 2026-09-15 | call 7655 | high 7617.1 | **37.9pt** |
| 2026-09-25 | call 7765 | high 7751.8 | 13.2pt |
| 2026-09-21 | **put** 7680 | low 7692.0 | market went **up** to 7779 |

## Finding 4 — the cost

115 require-both-sides skips in the live era (the GEX-driven ones are a subset; the rest come
from the credit gate). Estimated forfeited credit, optimistic bound: **$5,197.50 gross** —
comparable to B's entire live P&L of $6,476.60.

~~⚠️ The counterfactual column exists and is never populated.~~ **CLOSED 2026-09-29.** It was
true when written — `skipped_entries` carried `would_have_stopped` and `theoretical_pnl` with
**0 of 115 rows filled**, so every skip was recorded and none scored. `scripts/score_skipped_entries.py`
now scores them and 31 rows are populated (the rest predate strike capture, which began
2026-09-11 in `ec71967`, and can never be scored).

## Finding 5 — the counterfactual, now scored (`scripts/score_skipped_entries.py`)

Built 2026-09-28. Two numbers, both narrow on purpose:

* `theoretical_pnl` — the IC's P&L **with the A2 stop modelled** (each side's value capped at
  `pct_of_width × width`). Held-to-expiry is also printed but is the WRONG model for B: it
  turned the same 26 skips from **+$1,976 into −$4,324**, because it lets a breached side cost
  the full width when B actually exits at 40% of it. Verified against 2026-09-21, where three
  stopped call spreads cost **$4,300** to exit rather than the **$10,500** they were worth at
  expiry.
* `would_have_stopped` — **not** a stop simulation. It records whether SPX ever traded through
  the short strike intraday. A skip whose strike was never touched cannot have protected
  anything.

**Result over the scorable window:**

| reason | n | held-to-expiry | **with the A2 stop** | winners | ever threatened |
|---|---|---|---|---|---|
| require-both-sides | 26 | −$4,323.90 | **+$1,976.10** | 20/26 | **7/26** |
| credit gate | 5 | +$45.50 | +$45.50 | 3/5 | 0/5 |
| **TOTAL** | **31** | −$4,278.40 | **+$2,021.60** | | **24/31 never threatened** |

**The skips cost roughly $2,000 in foregone profit**, and three-quarters of them vetoed a strike
the market never came near.

### Why only 31 of 200 rows are scorable — and why that is fine

Not bias. Commit `ec71967` (*"record the strikes a skipped entry would have used"*) landed
**2026-09-11**; before that date **zero** skips captured their theoretical strikes, so they can
never be scored. Post-09-11 coverage is **29 of 34 (85%)**. The one anomaly worth a later look is
**2026-09-17: 7 skips, 0 with strikes.**

So the counterfactual pipeline is already in place upstream; what was missing was anything that
*scored* it. That is now closed.

## Recommendation — PR-1

**Replace SKIP with try-SHIFT-then-KEEP.** On an accel-zone hit, attempt to move the short past
the zone (the machinery exists — `max_shift_pts: 25`); SKIP only if no viable strike remains.
This is what the source describes, and it respects the accel zone rather than ignoring it.

**Is this a fix or a tune?** *Would I make this change if the P&L had come out the other way?*
Yes — on two grounds that are not P&L:
1. The implementation contradicts its own source, and its docstring admits the SKIP path was an
   implementation convenience: *"(HYDRA already supports one-sided entries)"*.
2. The mechanism's own claim — "this strike is dangerous" — is falsified 21/21. That is a broken
   mechanism, which is a legitimate unfreeze trigger, not a performance tweak.

**But do not flip blind.** `gex_shadow.py` and the `shadow_json` / `shadow_disagrees` columns
already exist for exactly this. Run SHIFT-first in shadow, compare against live SKIP for the
pre-registered window, then flip.

**Decision rule, registered in advance:** flip when the shadow has ≥ 20 decisions where live
SKIPped and shadow SHIFTed, **and** the shadow's would-be strikes were breached at a rate no
worse than live's (which is currently 0/21, so the bar is: the shift does not introduce
breaches). Note this rule cannot be gamed by a good P&L run — it is scored on breach rate, not
profit.

---

# PR-2 · The hedges — the prior analysis stands, but two of its inputs were wrong

## What I got wrong first

I framed the hedge disable as resting on `brandon_hedges.db`'s **4 butterflies**. It does not.
The 2026-09-04 analysis used **30 DB-verified live days** and found the morning debit spread was
**0 wins / 8 losses on B (−$1,935), 0/4 on C — 0-for-11**. That kill was correct. The
`hedge_placements` table only contains butterflies because the recorder postdates the debit
spread's removal.

**Its decisive argument is structural and survives the transcript entirely:** butterfly debits
actually paid were **$1,925 / $1,960 / $2,065 / $2,240**, against an IC side loss already bounded
at **~$1,400** by the A2 stop. Paying ~$2,000 to defend a bounded ~$1,400 roughly doubles
exposure. That holds no matter what the source says.

## What the transcript changes

The prior analysis recorded: *"a single unaudited Theta Profits interview whose entire hedging
spec is **two sentences** ('debit spreads earlier, butterflies later') — no trigger, sizing,
strikes, or exit."* **That is no longer true.** The transcript specifies all four:

| | Source | Ours |
|---|---|---|
| **Trigger** | *"if you're starting to break through key levels… that's when you can input the debit spreads"* — a **heat-map level break** | `trigger_distance_pts: 15` — a **point distance** |
| **Structure** | debit spread *"before 12:31"*, butterfly after | butterfly only (debit spread killed 0-for-11) |
| **Strikes** | *"I will take the strikes directly above both of my short and long positions"* | ours |
| **Exit** | *"When you hit the pin"* — closed at the pin | **held to expiry** |
| **Sizing** | *"you can get them for **$5, $10 in premiums**, and then your total anticipated payout can be up to **$450**"* | **$1,925–$2,240 debits** |

## The two findings that matter

**1. Sizing is off by an order of magnitude.** He describes a cheap, far-OTM lottery-ticket pin —
a debit around 2% of max payout. Our 2026-08-31 butterfly (long 7675 @ $12.70, short 7685 ×2 @
$6.20, long 7695 @ $2.65) is a **near-the-money** fly costing ~30% of its max payout. The prior
memory's own instruction was *"if re-enabling, **fix sizing**"* — the transcript now says what to
fix it to.

**2. The arming gate is ours, and the prior analysis already showed it is backwards.** It stood
down at **1.42pt** from a short on 09-01 (where a fly would have paid ~+$1,500), then armed on
09-04 when every IC finished 10pt+ OTM. A point-distance trigger is not what the source uses —
he arms on a gamma-level break, which is a *different signal entirely* and one we already compute.

**3.** The one butterfly that lost paid its **entire** debit (09-04, −$1,960): SPX closed 7716.37,
below the 7720 wing. He closes at the pin; we held to settlement. A butterfly held to expiry is
all-or-nothing by construction.

## Recommendation — PR-2

**Do NOT re-enable the hedges.** The structural argument (≈$2,000 risked to defend a bounded
~$1,400) is untouched by any of this, and the debit spread's 0-for-11 is decisive on its own.

**Do treat this as three separate open questions, in order:**

1. **Fix the arming gate first** (the prior analysis said so, and the transcript says what the
   trigger should be: a gamma-level break, not a point distance). `BRANDON-OVERLAY-WATCH`
   telemetry is already running at zero cost — score the existing trail against a level-break
   rule before writing anything.
2. **Then sizing** — a far-OTM pin at ~2% of max payout, not a near-money fly at ~30%.
3. **Then an exit at the pin**, not at expiry.

Only if all three are addressed does a shadow re-test make sense. Re-enabling as-is would repeat
a measured loss.

---

## What neither PR-1 nor PR-2 changes

B still has **no VIX floor** and has traded **0 of 45 days** in the source's stated 19–40 regime.
Nothing here alters that, and no hedge or adjuster fix makes B into Brandon's strategy.
