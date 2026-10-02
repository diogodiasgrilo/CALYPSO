# GO-LIVE MASTER — CALYPSO / HYDRA on IBKR

> **Start here for anything about taking a strategy live.** This is the single umbrella that maps the whole
> go-live path across **all strategies** and **both go-live levels**, and links out to the detailed runbooks,
> scripts, and gates (it does **not** duplicate them). Last updated: **2026-09-12**.
>
> **For the real-money question specifically, go straight to [§2-bis](#2-bis-level-ii-real-money-gate-status--measured-2026-09-12)** — the ten Level-II gates measured, plus the external
> account/funding/permissions/market-data chain that is the actual critical path.
>
> **Reality check:** this branch (`hydra-ibkr-standalone`) trades the **IBKR paper account only**. There is
> **no live-money path wired** on this branch — real money is a deliberate, approval-gated build (see Level II).
> Today, "live" means **live-PAPER** (real orders against the paper account); **variant B** holds the live
> seat (swapped from C on 2026-07-24 — see [`RUNBOOKS.md` RB-9](migration/RUNBOOKS.md)).

---

## 1. The two go-live levels (and the boundary between them)

There are **two distinct** go-live transitions. Do not conflate them.

| | **Level I — dry-run → live-PAPER** | **Level II — live-paper → REAL MONEY** |
|---|---|---|
| What changes | `dry_run: true → false` — the bot places **real orders on the IBKR *paper* account** | New **live** IBKR account/credentials — real capital at risk |
| Risk | Execution/reconciliation realism; **no capital at risk** | Real money |
| How | Manual operator flip, gated on broker health + a same-ET-day paper-smoke PASS | Full readiness checklist + **new live-OAuth keypair** + **explicit written approval** |
| Docs | **RB-8** + the flip scripts + `broker_paper_smoke.py` (this doc §4) | **`LIVE_READINESS_CHECKLIST.md`** + `IBKR_CREDENTIALS_SETUP.md` (this doc §5) |
| Status on this branch | **Available** (B is here) | **Not wired** — deliberate. Needs the Level-II build below. |

**The boundary:** everything on this branch is Level I. Level II (real money) is intentionally unbuilt — the
credential model, approval record, and cutover would all be new work (§5). Don't cross the boundary without it.

---

## 2. Per-strategy status matrix

Current mode → next gate → how it flips → its smoke test → edge/readiness verdict. (Source of truth for *mode*
is each variant's `config_variant_*.json` `dry_run` on the VM; this table is the human-readable rollup.)

| Var | Strategy (group) | Current mode | Next gate | Flip mechanism | Smoke test | Edge / readiness verdict |
|---|---|---|---|---|---|---|
| **A** | HYDRA baseline — 0DTE IC (`ic_0dte`) | dry-run shadow | Level I paper-flip (available) | [`flip_a_live.sh`](../scripts/flip_a_live.sh) (auto via smoke `ExecStartPost`) or [`flip_ac_live.sh`](../scripts/flip_ac_live.sh) | [`broker_paper_smoke.py`](../scripts/broker_paper_smoke.py) | dry-run baseline; not a live candidate today |
| **B** | Brandon Narrow 7-slot — **live** — 0DTE IC | **LIVE-PAPER** (`dry_run=false`) | **Level II** (real-money) — see §5 | flipped onto the live seat via [`flip_bc_swap.sh`](../scripts/flip_bc_swap.sh) on 2026-07-24 ([RUNBOOKS.md RB-9](migration/RUNBOOKS.md)) | [`broker_paper_smoke.py`](../scripts/broker_paper_smoke.py) | Now the live seat (7c, 7-slot grid 09:45–12:45). Its prior edge estimate was **simulated-only** and collected ~31% more credit than real fills give (`golive_readiness_and_fill_lever` memory) — watch real-fill economics as live data accumulates. Rollback: [`flip_bc_rollback.sh`](../scripts/flip_bc_rollback.sh) (hard-aborts unless flat). |
| **C** | Brandon Narrow baseline — 0DTE IC | dry-run shadow (was **LIVE-PAPER** through 2026-07-24) | Level I paper-flip (available again via rollback) | live seat handed to it by [`flip_bc_rollback.sh`](../scripts/flip_bc_rollback.sh) if swapped back | [`broker_paper_smoke.py`](../scripts/broker_paper_smoke.py) | Proven live-paper track record through 2026-07-24 (~breakeven); now dry-run shadow alongside B. Its **Entry-Schedule Lock ~mid-Aug** ([NEXT_STEPS §5](NEXT_STEPS.md)) analysis still applies to its historical data. |
| **D** | DC Time Machine — multi-day SPX calendar (`calendar_multiday`) | dry-run-**LOCKED** | Level I = **a BUILD**, not a flip (DG-1..DG-11) | `flip_d_live.sh` **(NOT BUILT)** | `broker_dc_smoke.py` **(NOT BUILT)** | **NO-GO** ([`D_GOLIVE_SCOPE_AND_AUDIT.md`](migration/D_GOLIVE_SCOPE_AND_AUDIT.md)) — no real-order path; edge **INSUFFICIENT_DATA**. See [`D_GOLIVE_RUNBOOK.md`](migration/D_GOLIVE_RUNBOOK.md). |
| **E** | SPY Double Calendar — multi-day (`calendar_multiday`) | dry-run-**LOCKED** | Level I = a BUILD ([NEXT_STEPS §2b](NEXT_STEPS.md)) | `flip_e_live.sh` **(NOT BUILT)** | `broker_dc_smoke.py` **(NOT BUILT)** | Edge **INSUFFICIENT_DATA** (n=0); least-documented; SPY assignment/dividend + IV-rank gate unbuilt. E go-live runbook to be modeled on D's. |
| **F** | Ghauri Mean Reversion — 0DTE one-sided credit spread (`ic_0dte`) | dry-run-**LOCKED** | Level I paper-flip — **BLOCKED**, see §2-ter | `flip_f_live.sh` **(NOT BUILT)** | [`broker_paper_smoke.py`](../scripts/broker_paper_smoke.py) | **Barely any record: 1 recorded entry, 2 stops, lifetime −$14.80.** Entries were NEVER recorded until `88ec8ed` (2026-09-15) — every earlier session is unrecoverable. **Also trades FOMC announcement days** (`fomc_announcement_skip=false`). Needs a real track record before any flip. |
| **G** | Strangle — 0DTE naked short strangle, **UNDEFINED RISK** (`undefined_risk_0dte`) | dry-run-**LOCKED** | Level I paper-flip — **BLOCKED**, see §2-ter | `flip_g_live.sh` **(NOT BUILT)** | [`broker_paper_smoke.py`](../scripts/broker_paper_smoke.py) | 11 traded days, lifetime **+$1,114.85**. **Sole variant taking FOMC event risk** while every defined-risk variant sits out — on 2026-09-16 it sold $3,085 of event premium, was whipsawed on BOTH sides (4 stops) and netted **−$708.80** on a 1.53% range day. Undefined risk is sized by broker margin, not a defined-risk floor. |

**Level-II (real-money) status for B is measured gate-by-gate in [§2-bis](#2-bis-level-ii-real-money-gate-status--measured-2026-09-12) below.**

**Key takeaways:** B is the only live variant (live seat swapped from C on 2026-07-24 —
[RUNBOOKS.md RB-9](migration/RUNBOOKS.md)); A/C are dry-run; **D and E are BUILDS, not flips** (they have no
real-order path yet) and are calendar-shaped, so the 0DTE readiness checklist (§5) does not transfer to them —
they need their own gate.

---

## 2-ter. PROMOTION BLOCKERS — per-variant, must be cleared before a Level-I flip

A variant being dry-run is not the same as it being *ready but unflipped*. These are the specific
reasons each one cannot take a live seat today. **Check this table before running any `flip_*`
script** — a config that is harmless in simulation travels with the variant when it is promoted, and
that is the moment nobody re-reads it.

### G — Strangle (undefined risk)

| # | Blocker | Why it matters | Status |
|---|---|---|---|
| **G-1** | **`fomc_announcement_skip = false`** | G is the ONLY variant that trades FOMC announcement days; A/B/C all skip them. So the one strategy with **unbounded** loss is the only one taking Fed-event risk. **Deliberately left ON in dry-run** — event days are rare (~8/yr) and the data is scarce and valuable, and nothing is at risk in simulation. It must be a conscious decision at promotion, not an inherited default. | **OPEN — decide at flip** |
| **G-2** | No live track record on event days | n=1. 2026-09-16 cost −$708.80 on 1 contract (≈ −$4,960 at B's 7c) — but it also collected 8× normal premium, so one bad day is not a policy. | **OPEN — revisit after ~3 more FOMC days** |
| **G-3** | Undefined risk is not covered by the halt criteria | [`LIVE_HALT_CRITERIA.md`](migration/LIVE_HALT_CRITERIA.md) thresholds were derived from **B**, a defined-risk IC whose per-side loss is capped by spread width. A naked strangle has no such cap, so H1/H2 dollar limits do not transfer. | **OPEN — needs its own thresholds** |

> **What 2026-09-16 actually proved, in G's favour:** its stops worked. All four fired between 11:00
> and 13:04, so G was flat *before* the announcement. The undefined-risk fear did not materialise.
> But stops protect against a *move*, not a *gap* — had the announcement produced an instant drop,
> naked puts would have gapped through them. Treat the good outcome as path luck, not proof.

### F — Ghauri Mean Reversion

| # | Blocker | Why it matters | Status |
|---|---|---|---|
| **F-1** | No usable track record | Entries were never recorded before `88ec8ed` (2026-09-15). Lifetime P&L reads −$14.80 from **one** entry and two stops; every earlier session is unrecoverable. | **OPEN — needs weeks of data** |
| **F-2** | ~~Daily-summary accounting disagrees with the trade tables~~ | ✅ **CLOSED 2026-09-30 — this described a bug fixed on 2026-09-18.** The $195.00-gross double-booking was `_ghauri_close_for_take_profit` adding `credit − close_cost` on top of `_close_entry_early`'s own unconditional booking, giving `2 × credit − cost`; its docstring now records the fix. Re-audited today: **gross == entry realized_pnl on every F day, `net = gross − commission` holds throughout, and `entries_placed = 1` on all 7 days.** ℹ️ Two `early_close` rows carry the FULL CREDIT rather than the realised P&L (2026-09-15 11:18:52 → 127.50 vs 67.50; 2026-09-24 12:24:17 → 167.50 vs 92.50), so any consumer summing `trade_stops` for F is $135 high — including the dashboard's day-detail. **This is NOT a live defect:** it is pre-fix residue. `d09c1da` (A4, *"a dry-run early close must not record the full credit as the result"*) deployed 2026-09-24 21:48 ET, **after both rows**, and F has had **zero** `early_close` exits since. Historical only — do not go looking for a bug in the close path. | **CLOSED** |
| **F-3** | Trades FOMC days | Same class as G-1, but F is defined-risk so the exposure is bounded. | **OPEN — decide at flip** |

---

## 2-bis. Level II (real-money) gate status — MEASURED 2026-09-12

Snapshot of the ten [`LIVE_READINESS_CHECKLIST.md`](migration/LIVE_READINESS_CHECKLIST.md) gates, **measured
rather than asserted**. Several decay (test state, backups, VM state) — re-measure before any cutover.

| Gate | Status | Measured evidence | What closes it |
|---|---|---|---|
| **1** Branch state | 🟡 | **MERGED 2026-09-12** — `main` is at `59a1fc7`, carrying all 613 commits (`--no-ff`, history preserved; every cited SHA still resolves). Suite green *on the merge result*; the branch-vs-merge diff outside the journal was empty. **The VM is still checked out on `hydra-ibkr-standalone`** — deliberately, see below. | Move the VM to `main` (MERGE_PLAN §7) as part of the go-live sequence, not before. |
| **2** Audit state | 🟢 | `P7_AUDIT_FINDINGS.md` **0 OPEN**; **0** `TODO`/`FIXME`/`XXX` under `bots/hydra/` + `shared/ib_*.py`. | — (re-check at cutover) |
| **3** Test state | 🟡 | Suite **3608 passed / 16 skipped / 0 failed**. **Dependency CVEs: 44 across 9 packages → ZERO.** `pip-audit` clean on `requirements.txt`, `requirements-lock.txt` **and the VM's installed set**. Root cause was the unpinned dashboard stack — now pinned; `starlette` 0.52→**1.3.1** validated (142 dashboard tests, live REST+WS+`domytrade.com` 200). **Smoke repointed** to `scripts/broker_paper_smoke.py` (broker-safe); the legacy pytest smoke would have **evicted the broker session** and now physically refuses to run. | **One RTH run of `broker_paper_smoke.py`** — the only item left on this gate. |
| **4** Paper history | 🟡 | **Chaos test DONE — PASS 2026-09-12** on the live seat (`RUNBOOKS.md` RB-10): restarted 33s, state JSON valid, snapshot fired, 0 `.tmp` residue, recovery clean, account flat. Repeatable via `scripts/chaos_test.sh`. **Remaining: 5 consecutive sessions with no manual intervention** — unattainable at the current commit cadence. | A deliberate change freeze, then 5 clean sessions. |
| **5** Live credentials | 🟡 | **Code side DONE**: `load_credentials(resolve_environment())` at both call sites; `_assert_account_matches_env` raises on declared-paper-but-actually-LIVE. **Operational side absent**: live account **created but NOT funded**; no live keypair; `/etc/calypso/ibkr-live/` does not exist **on the VM** (the unit that reads it, `deploy/calypso-broker-live.service`, was written 2026-09-18 and is deliberately NOT installed — so “0 repo references”, measured before that, is no longer true); `calypso-broker.service` still loads `/etc/calypso/ibkr/`. | The external chain below. |
| **6** VM state | ⚪ | **The 09-12 reading ("restart loop, IBKR weekend maintenance") is STALE — do not read it as current.** Re-measured **2026-09-19 07:34 UTC**: `calypso-broker` + all 7 strategies active on `ed229e8`, `NRestarts=0` on every unit, session authenticated / not competing, account flat (0 position rows), VM tree clean and in sync with origin, installed unit files identical to the repo. Kept ⚪ because this gate decays and its checklist covers more than liveness. | Measure at cutover. |
| **7** Backup verified | 🟢 | **RB-7 rehearsal RUN 2026-09-19 — PASS** (second run; logged in `RUNBOOKS.md` RB-7). Restored the live seat's DB + state + metrics to scratch: `integrity_check ok`, schema v17, 297/115/96 rows matching live exactly, `ensure_schema()` True, live data untouched. It also found that the procedure asked for *today's* backup, which does not exist until the 23:00 UTC timer fires — so a daytime rehearsal failed at step 1; it now auto-detects the latest. The **first** run (2026-09-12) exposed that the **live seat had no DB/metrics backup at all** — only variant A (a dry-run shadow) and state files were covered; fixed in `ab63407`, now seat-agnostic and gating. | Re-run by **2026-10-19** (30-day clock, reset by the 09-19 run). |
| **8** Position sizing | 🔴 | B runs **7 contracts**; the gate mandates **1** for week 1. | Set at cutover; tighten the daily-loss bounds with it. |
| **9** Approval + halt criteria | 🟡 | **H1 RESOLVED 2026-09-30 — operator chose Option A (structural): H1 = `max_sides_tolerated × $200` = **−$600/contract**, with H3 (≥3 stops) as the real trigger and H1 its dollar shadow. Consistent with H3 by construction. ⚠️ **Corrected 2026-10-02: that ordering is INVERTED by slippage** — B's two stops on 2026-10-01 reached −$528.63/contract (88% of H1) against $400 of structural exposure, so **H1 trips at ≈2.3 stops, before H3's 3.** Thresholds unchanged; see `migration/LIVE_HALT_CRITERIA.md`. Enforcement is **MANUAL by decision** — the hook stays stubbed, because §2-bis measured a same-day halt firing 0/32 days at −$600 and costing $280 at −$400. These are an operator tripwire, not an automated control.** The `--allow-empty` approval commit is the remaining step and is the operator's to make. | Operator makes the approval commit. |
| **10** Week-1 monitoring | 🟡 | **DRAFTED 2026-09-12** in [`LIVE_HALT_CRITERIA.md`](migration/LIVE_HALT_CRITERIA.md) §5 — day-by-day commitment, EOD artefacts, and a four-condition Friday scale-up rule (scale on *process*, not profit). | Operator confirms availability for day 1 (09:30–16:15 ET). |

### Why the VM is still on the feature branch after the merge

The merge landed on `main`, but `/opt/calypso` was **deliberately left on `hydra-ibkr-standalone`**.
Moving it now would buy nothing and cost two things:

- **HOMER pushes nightly to whatever branch the VM tracks.** On `main` that means an automated
  agent committing to the protected production branch every night — exactly what Gate 1 exists to
  prevent. Day-to-day work should keep landing on the feature branch and be merged deliberately.
- **`config_variant_*.json` carries `skip-worktree` but a branch switch still overwrites it.** Every
  live variant's `dry_run`, `contracts_per_entry` and `alerts.email` would need re-verifying, during
  a period when nothing requires the move.

Gate 1 is checked **at cutover**, not continuously. The right sequence is: keep working on the
branch → merge again when the go-live window opens → move the VM to `main` then, per MERGE_PLAN §7.

### The external prerequisite chain (the checklist's Gate 5 starts too late)

Gate 5 opens at "request a live OAuth keypair". **Four things precede it**, each with its own lead time, and
they are **strictly ordered** — none can be parallelised:

1. **Live account created** — ✅ done.
2. **Funded** — ❌. Nothing downstream is approved or testable without it.
3. **Options-trading permissions** — ❌. SPX defined-risk spreads need spread-level options approval and a
   matching margin type. A separate IBKR review with its own turnaround.
4. **Live market-data subscriptions** — ❌. A live account starts at **zero** entitlements and inherits
   nothing from paper. Note the paper account carries CBOE index real-time (SPX/VIX) but **not** US equity —
   so "it works on paper" says nothing here. Precedent: variant E's SPY entitlement needed an operator
   subscription plus a next-day verification cycle (`e_spy_realtime_entitlement`).
5. **THEN** the live OAuth keypair → activation wait (precedent: ~2 weeks for the read-only scanner keypair).

**This chain, not the code, is the critical path**, and steps 2–5 are calendar rather than engineering. Start
it before the repo work, because the repo work can proceed underneath it.

### Two things that are only learnable with real money

Both are currently blocking analysis, and neither can be closed on paper — which is the argument for a first
live deployment sized so its **purpose is learning, not earning**:

- **Combo routing / atomicity.** IBKR SmartRoutes combos leg-by-leg unless routed direct; ibind 0.1.23 cannot
  express the choice; paper *simulates* combo order types and our own probe recorded phantom `PendingSubmit`
  with `OrderID doesn't exist` on cancel. See [NEXT_STEPS §A P3](NEXT_STEPS.md).
- **`/iserver/account/trades` returns ZERO rows on paper** (probed over a 7-day window containing dozens of
  real fills; the `accountId` theory was tested and **refuted**). It is the documented F5 fill-price
  authority, so a live account that revives it would activate three currently-masked defects at once.

---

## 2-quater. THE CONFIG FREEZE — 2026-09-27

**Read this before changing any live-seat parameter, and before quoting a Sharpe.**

### Why

B carries **154 free parameters** (45 of them boolean switches) against **32 live trading
days / 98 entries**. Roughly five tuned knobs per observed day. Under that ratio the
performance numbers cannot distinguish skill from fitting.

The measured record, from B's own `backtesting.db`:

| Window | n (traded days) | Net | Annualised Sharpe | **t-stat** |
|---|---|---|---|---|
| live, all calendar days | 46 | $6,339 | 1.94 | **0.83** |
| live, traded days only | 32 | $6,477 | 2.37 | **0.85** |

**The live edge is not statistically distinguishable from zero.** Mean $137.81/day against a
standard error of $166.36.

And the headline Sharpe decayed monotonically as data arrived — 4.96 (n=9) → 4.33 (n=25, the
window in which "Sharpe 3.99" was recorded) → 3.61 (n=27) → 2.37 (n=32). **But nothing
degraded.** The two chronological halves have near-identical means ($208.74 vs $196.05) at
near-identical VIX (16.01 vs 15.70). Removing 2026-09-21 alone restores it to 3.97. The
Sharpe fell because the *variance estimate* got honest when a −$3,088 day finally appeared.

The corollary matters: **August's 4.96 was not a better config, it was a shorter sample.**
Reverting to an earlier config to "get the good Sharpe back" is the overfitting error itself —
selecting parameters by the P&L of the window they were measured in.

### What is frozen, and the test

Two questions that look like one but come apart:

| | May I change it? | Does the sample reset? |
|---|---|---|
| **Defect fix** that changes money behaviour | Yes, always | **YES** |
| **Defect fix** with no economic effect (logging, dashboards, telemetry, accounting-only) | Yes | No |
| **Tune** | Only with a pre-registered decision rule | Yes |

The test for which one you have: **would I have made this change if the P&L had come out the
other way?** Yes → fix. No → tune.

Note the correction in row 1. A bug fix is *permitted* but still **resets the statistical
clock**: rung pricing was unambiguously a fix and it moved fill quality by $59.91/entry, so
pre-fix and post-fix days are not draws from the same distribution.

### The measurement clock has not started — and bugs, not tuning, are why

Commits to B's live money path, per week since it went live:

```
W30  W31  W32  W33  W34  W35  W36  W37  W38  W39
  2    1    2    3    8    7   10   20   10   18
```

**Accelerating, not settling.** 30 distinct change-days out of ~45 trading days. Median gap
between changes: **1 day**. Longest stretch with no change: **9 calendar days**.

So the freeze has two separate start dates:

1. **Tuning freeze: 2026-09-27.** Immediate. Costs nothing.
2. **Measurement clock: starts after 15 consecutive trading days with no economics-changing
   commit to the live path.** Never yet achieved. **Achieving it is the gate** — and it is a
   better gate than any P&L number, because it is not noisy.

At mean $202/day and sd $1,354, reaching t = 2 needs ≈ **179 traded days (~9 months)**. No
config choice shortens that; only n does. Which is the argument for Gate 8's one contract:
**go live on the strength of the risk controls, not the Sharpe.** Those *are* established —
the A2 %-of-width stop saved $6,200 on 2026-09-21 as a matter of arithmetic, not as a fitted
result.

### What legitimately unfreezes it

The freeze exists to stop reactions to **noise**, not to disasters. A devastating finding is
exactly what should break it.

| Legitimate unfreeze | **Not** a reason |
|---|---|
| A **mechanism** is broken — a stop cannot fire, a gate is inverted, a guard is a no-op | A drawdown inside pre-registered tolerance |
| A **halt criterion** breaches → mandatory recorded review (which may or may not change a parameter) | A run of losing days |
| **Unobserved regime** — VIX enters a zone never traded, so the config's behaviour there is untested | A backtest on the same history suggesting better |
| **External change** — IBKR fill behaviour, SPX settlement, commissions, entitlements | Another variant looking better (multiple-comparisons trap) |
| **Risk-limit** breach — margin, account, exposure | "We have learned a lot since then" |

⚠️ **Open (corrected 2026-09-27):** H1 and H3 were both breached on 2026-09-21
(−$441.17/contract vs −$400; 3 stops vs ≥3). An earlier draft of this section said *"no review
is recorded"* — **that was wrong.** A detailed review was recorded 2026-09-22 inside
[`LIVE_HALT_CRITERIA.md`](migration/LIVE_HALT_CRITERIA.md), including why a
"1.6× the worst observation" threshold is *guaranteed* to break whenever the sample grows a new
tail, and two coherent replacement options.

What is genuinely open is the **operator's Gate-9 choice** between them. Recommendation:
**Option A (structural)** — `H1 = max_sides_tolerated × $200` → **−$600/contract**, making H3
(≥3 stops) the binding trigger and H1 its dollar shadow. It is self-consistent, and unlike a
"worst observed" rule it does not move when the sample grows.

And a finding from 2026-09-27 that changes what these criteria are *for*: **a same-day halt is
structurally close to inert for B** — see LIVE_HALT_CRITERIA.md §2-bis. Do not automate it as a
reaction to a bad session.

### Pre-freeze corrections applied 2026-09-27

All three pass the fix-vs-tune test — each would be made regardless of P&L — and all three are
pinned by tests (`tests/test_config_freeze_invariants_2026_09_27.py`,
`tests/test_eod_flatten_safety.py`), with negative controls.

**1. `eod_flatten.skip_otm_pts` 10 → 20pt (a, b, c, bm).** The 2026-08-18 audit that set 10pt
found the 20pt cushion had a 0/19 win rate against holding and concluded it was too wide — but
0/19 is what fire insurance looks like in a sample where the house did not burn down.

Measured from B's own ticks, 45 live days: the final-10-minute SPX move has median 3.89pt, 90th
pct 11.02pt, max 16.80pt, and **exceeds 10pt on 8 of 45 days (17.8%)**. The earlier 84-day study
put the max at 18.4pt. So a short 10pt OTM at 15:50 was crossed by a move of ordinary size about
one day in six, and a crossed short settles the spread ITM.

**25pt was deployed briefly the same day and reverted.** The stated reason for it — "insure a tail
the sample does not contain" — **is wrong here, and worth recording as a trap**: B's spreads are
defined-risk, so the loss is bounded by width at `5 × 100 × 7 = $3,500/side` whether SPX moves 21
points or 60. There is no fat tail for the cushion to insure. The marginal 20→25 step covers the
20–25pt band, which has **zero observations across 129 combined days**, costs ~$1,600/yr and buys
~$112/yr of expected protection — about **14:1 against**.

| cushion | covers | cost / 32 traded days | verdict |
|---|---|---|---|
| 10pt | 82% of final-10-min moves | — (baseline) | crossed ~1 day in 6 |
| **20pt** | **every move ever recorded** | **+$735** | **chosen** |
| 25pt | + a band with zero observations | +$945 | reverted — 14:1 against |

What justifies 20 is narrower than a tail story: the protection is **correlated with bad days** —
a short near its strike at 15:50 on a trend day is the same session other entries already stopped,
so it trims the worst sessions specifically. Worth a premium while sizing up on a t = 0.83 edge.
It does **not** justify padding past what has been observed.

⚠️ **Accepted weakness, and the real gap.** 20pt clears the 18.4pt observed max by only 1.6pt
(8.7%) — a threshold near its sample maximum, the pattern criticised throughout this document.
Accepted because the loss is width-bounded, so being wrong is capped. But the genuine inadequacy
is different in kind: **the cushion is regime-blind.** It is a fixed point value while the
final-10-min move scales with VIX. B's sample averages VIX 16; at VIX 30 neither 20 nor 25 is
adequate and ~40pt would be. B has never traded above VIX 19, so it has never bitten.
**VIX-scaling the cushion is the proper fix, pre-registered in `NEXT_STEPS.md` and deliberately
NOT built during the freeze.**

**2. MKT-043 calm-entry keys removed (b, c, bm).** Fired **zero** times in 101 days. Removed not
for the parameter count but because a path that has never executed, and only activates in the
most stressed conditions, is not protection — it is untested code waiting for a crisis, the same
class of thing as the over-fill correction found dead on the morning of 2026-09-27. There is no
`enabled` flag (the gate is "all three keys present"), so removing the keys *is* the off switch,
and it is behaviourally a no-op. To restore it, calibrate the threshold to fire a few times a
month so it actually gets exercised.

**3. `vix_regime.max_entries` [7,7,7,7] → [7,7,3,1] (b, bm).** B has never traded above **VIX
19.0** — zones 2 (22–28) and 3 (≥28) have **zero** observed days. It also compounds: above VIX
22 Brandon widens spreads 5pt → 10pt, so the A2 stop doubles to $2,800/side while the entry
count stays at 7 — **$39,200** theoretical daily max in a regime never observed. The cap holds
*dollar* exposure flat across the width doubling rather than letting it double silently.
Mechanical, not fitted. **Zones 0 and 1 are deliberately unchanged at 7** — freezing means not
re-tuning what has actually been running.

Note `bm` (real money) inherited B's 10pt cushion and uncapped `[7,7,7,7]` by copy-and-forget.
A test now pins bm to B on the frozen safety knobs.

---


### 2-quinquies. THE DEPLOY GATE — asymmetric, because the costs are asymmetric

**Added 2026-09-30**, after measuring both directions instead of guessing. A
single uniform rule gets one of two opposite cost profiles wrong.

**What was measured** (330 money-path commits, 120 days):

* **59%** of money-path commits are fixes; **44%** were followed by a same-file
  fix within three days. Shipping new behaviour straight to the live seat
  demonstrably produces rework.
* Delay is not free either. **MKT-011B cost B $137.20** while it sat unfixed;
  the Brandon overlay put **$6,230** of hedge debit against **$315** of real
  damage before being disabled. A fix to a bug that is actively losing money
  gets more expensive every session it waits.
* The queue is normally **empty** (0 unpushed, 0 uncommitted when this was
  written), so a wait on new behaviour usually delays nothing at all.

**The rule — two independent questions, WHEN and HOW VERIFIED:**

| change | WHEN | HOW VERIFIED |
|---|---|---|
| Fix to an active, costing bug | **immediate** | as below |
| New behaviour / refactor / telemetry | **after one full canary session** | as below |
| Touches the order path | *(unchanged by this row)* | **next session's real fills** |
| Everything else | *(unchanged)* | **a canary variant, one full session** |

A batch takes the most restrictive WHEN: one new-behaviour commit pulls the
whole batch onto the canary path.

**🔴 The canary has a hard structural limit, and it is the expensive class.**
`_place_option_order` returns at **SAFETY-DRY-01** in dry mode — *no dry-run
variant ever places an order*. Placement, rung pricing, fills, cancel/replace,
over-fill correction and partial unwinds therefore **cannot be canaried at
all**. Recommending a canary for them would be recommending a verification that
is structurally incapable of verifying anything. They get deployed after the
close and checked against the next session's real fills — which is what rung
pricing actually got.

**Check it before deploying:**

```bash
python -m scripts.deploy_gate            # classifies unpushed commits
python -m scripts.deploy_gate --ref A..B # an explicit range
```

Advisory, not enforcement. A gate that blocks a genuine hotfix at 15:58 ET
would be worse than the problem it solves.

**Worth recording:** run against 2026-09-30's own batch (`fb3e4ec..786a5ef`) it
returns **AFTER ONE CANARY SESSION** — i.e. that day's changes, which included a
refactor of `_check_whipsaw_filter`, a live entry-skip decision, should not have
gone straight to the live seat. They did.

## 3. Level I — dry-run → live-PAPER (the flip that actually happens here)

**Canonical procedures:** [`RUNBOOKS.md` RB-8](migration/RUNBOOKS.md) ("Flip a variant from dry-run to LIVE
paper" — A's original 2026-06-02 go-live; kept as a historical example) and
**[`RUNBOOKS.md` RB-9](migration/RUNBOOKS.md)** — the **current** procedure for moving which 0DTE-IC variant
holds the live paper seat (executed C→B on 2026-07-24).

> ⚠️ **Check [§2-ter PROMOTION BLOCKERS](#2-ter-promotion-blockers--per-variant-must-be-cleared-before-a-level-i-flip)
> first.** The two preconditions below are about the broker being healthy; §2-ter is about whether the
> VARIANT is ready. A config that is harmless in dry-run travels with the variant when it is promoted.

**Two hard preconditions (both must be true, shared by both procedures):**
1. `calypso-broker` `/health` returns `connected:true` (the shared IBKR session is up).
2. A **fresh, same-ET-day** paper-smoke **PASS** sentinel exists at `/opt/calypso/data/smoke/last_pass.txt`
   (written by `broker_paper_smoke.py --place` — see §6).

**Current live-seat-swap procedure (B↔C — RB-9):**
```bash
# 1. Same ET day: run the broker paper smoke → writes today's PASS sentinel (or aborts)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start broker-paper-smoke && sleep 20 && curl -s http://127.0.0.1:8788/health"
# 2. AFTER the close + after-hours settlement, with the account flat: swap the live seat
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo /opt/calypso/scripts/flip_bc_swap.sh"
# 3. Verify: B active dry_run=false + alerts.enabled=true; C active dry_run=true + alerts.enabled=false;
#    dashboard restarted so its WS/widget canonical view follows the new live seat
```
- [`flip_bc_swap.sh`](../scripts/flip_bc_swap.sh) hands the live seat **C → B**, guarded on broker health, a
  fresh same-ET-day paper-smoke PASS, the overlay-over-placement fix being deployed, B's concentration cap
  fitting its overlay load, and the shared paper account being flat. Flips `dry_run` + `alerts.enabled` on both
  variants, restarts both units, then restarts `dashboard` so the WS/widget canonical view follows the new live
  seat. This is the script that executed the 2026-07-24 swap. Full detail: RB-9.
- [`flip_bc_rollback.sh`](../scripts/flip_bc_rollback.sh) hands the live seat back **B → C** — **hard-aborts
  unless the account is flat** (flipping B to dry-run first would strand any open B position unmanaged).
  Flatten B with `flatten_paper_account.py --execute` first if it isn't already flat.

**A's independent flip (still valid; unaffected by the B/C swap):**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso /opt/calypso/scripts/flip_ac_live.sh"
```
- [`flip_ac_live.sh`](../scripts/flip_ac_live.sh) is the original 2026-06-02 A+C go-live script (RB-8). It now
  carries a **Guard 0** that refuses to run while B holds the live seat (running it would place B's and C's
  real paper orders simultaneously on the one shared account). Kept for reference / a possible future
  A-plus-something flip — not the current live-seat procedure.
- [`flip_a_live.sh`](../scripts/flip_a_live.sh) flips **A only** and is wired as `ExecStartPost=+` on
  `broker-paper-smoke.service` (auto-flip on a clean smoke).

**Operator rule (broker mode):** a session/auth fault is fixed by **`systemctl restart calypso-broker`**, NOT the
`hydra*` units (they proxy through the broker). See [`CLAUDE.md` calypso-broker section](../CLAUDE.md).

---

## 4. Level II — live-paper → REAL MONEY (not wired on this branch)

**Canonical gate: [`LIVE_READINESS_CHECKLIST.md`](migration/LIVE_READINESS_CHECKLIST.md)** (10 hard gates; every
item must be GREEN). **Credential mechanics: [`IBKR_CREDENTIALS_SETUP.md`](../deploy/IBKR_CREDENTIALS_SETUP.md).**

This transition is a **deliberate build**, not a flip. The essentials the checklist enforces:
- Deploy from **`main`** (not a feature branch); audit register 0-OPEN; **full test suite green** (~1918 pass);
  integration paper-smoke; `pip-audit` 0 High/Critical.
- **5 consecutive clean paper sessions**, P&L ≥ 0, no false stops, no null VIX, chaos test passed.
- A **NEW live-OAuth keypair** (not the paper keypair) encrypted to `/etc/calypso/ibkr-live/` — **in broker mode
  these live in `calypso-broker`**, not the strategy units (the checklist is being refreshed for this — see
  [NEXT_STEPS §10 B](NEXT_STEPS.md)).
- **1 contract week 1**; tightened halt criteria; **explicit written approval committed to the repo**; operator
  availability for the first session.

**Scope caveat:** the readiness checklist is written for the **0DTE IC group (A/B/C)**. The **calendar group
(D/E) needs its own real-money gate** — the 0DTE gates (paper-history shape, position sizing, flat-overnight
assumptions) don't transfer (per [`D_GOLIVE_SCOPE_AND_AUDIT.md`](migration/D_GOLIVE_SCOPE_AND_AUDIT.md) §5).

---

## 5. Strategy D / E — the calendar go-live (a BUILD, currently NO-GO)

The most thorough go-live *framework* in the repo is D's — reuse its structure for E.

- **[`D_GOLIVE_RUNBOOK.md`](migration/D_GOLIVE_RUNBOOK.md)** — §5 phased plan (PHASE 0 decision → 1A coexistence
  guards + arm-gate → 1B real-order exec path → 1C ops/flatten/rollback/smoke → 1D validate + flip → PHASE 2
  transform), each step with an ID / risk tag / exit criterion / verify command; §8 flip/rollback/flatten; §9 the
  **DG-1..DG-11** readiness gate.
- **[`D_GOLIVE_SCOPE_AND_AUDIT.md`](migration/D_GOLIVE_SCOPE_AND_AUDIT.md)** — the scope + adversarial audit.
  **Verdict: NO-GO** ("a build, not a flip") for three reasons: (1) no real-order path exists at all; (2) the edge
  was never validated and its signal is unobservable on IBKR; (3) the "risk-free" invariant is a mid-pricing
  artifact that breaks on real fills.
- **[`D_MVL_PHASE1_PLAN.md`](migration/D_MVL_PHASE1_PLAN.md)** — the reduced-scope first-live phase (drop the
  transformer). PLAN ONLY, not approved.
- **E** — only [NEXT_STEPS §2b](NEXT_STEPS.md) gates exist (SPY American-assignment + dividend handling, real IV-rank
  entry gate, coexistence MUST-FIXes, multi-contract ladder). **E's go-live runbook is not written** ("model on D's").

**Edge status (both):** `INSUFFICIENT_DATA` (D n=1, E n=0) per the edge reader (§6). MVL-D is gated on
`EDGE_POSITIVE`.

---

## 6. Tests + pass-criteria appendix (the one place)

| Test / analyzer | What it verifies | PASS criterion | How to run |
|---|---|---|---|
| **`broker_paper_smoke.py`** ([scripts](../scripts/broker_paper_smoke.py)) | The production order path end-to-end (BrokerClient→broker→IBClient) is healthy + real-time | HARD-refuses non-paper (`DU…`) accounts; requires `6509` first-char `='R'` on SPX **+** VIX **+** the SPXW leg; `--place` does a real 1c buy→fill→sell round trip that closes flat → writes ET-dated `data/smoke/last_pass.txt`; **exit 0** | `sudo systemctl start broker-paper-smoke` (or `python -m scripts.broker_paper_smoke --place`) |
| **Full pytest suite** | Code correctness / regressions | **~1918 passed, 15 skipped, 0 failed** (Level-II Gate 3) | `.venv/bin/python -m pytest tests/ -q` |
| **Integration paper-smoke** (`tests/integration/test_ib_paper_smoke.py`) | 15 IBKR paper-account integration tests | ≥15 passed, run against paper in last 7 days | `IBIND_INTEGRATION=paper .venv/bin/python -m pytest tests/integration/ -v` |
| **`pip-audit`** | No High/Critical CVEs in the IBKR stack | 0 High/Critical | `.venv/bin/pip-audit -r requirements.txt` |
| **`slot_edge.py`** ([bots/hydra](../bots/hydra/slot_edge.py)) | Per-slot edge (95% t-CI + sample floor) | verdicts firm up as sample grows (Entry-Schedule Lock, NEXT_STEPS §5) | `python -c "from bots.hydra.slot_edge import analyze_slots, format_slot_report; print(format_slot_report(analyze_slots('data/variant_b/backtesting.db')))"` (point at whichever variant's DB currently holds the live seat) |
| **`stop_shadow.py`** ([bots/hydra](../bots/hydra/stop_shadow.py)) | %-of-width stop replay vs acting stop | RESOLVED 2026-07-14: don't flip C's stop | `analyze()` / `format_report()` over the variant DB |
| **`dc_edge.py` / `analyze_calendar_edge.py`** | D/E calendar edge | `EDGE_POSITIVE` required to advance MVL-D; currently `INSUFFICIENT_DATA` | see NEXT_STEPS §3 |
| **D STATE-004 matrix** | D coexistence/recovery | per `D_GOLIVE_RUNBOOK.md` §6 / `D_MVL_PHASE1_PLAN.md` §3 | (D build) |

---

## 7. Go-live line items (carried here, tracked in NEXT_STEPS)

- **Entry-Schedule Lock (~mid-August 2026)** — the last strategy-side gate: with ~40+ live-paper days, re-run
  `slot_edge.py` + the real-fill economics filter and **lock C's go-live entry schedule** on economics + tail-risk
  + robustness (NOT p-values — per-slot significance takes months–years given ~$884/entry std). [NEXT_STEPS §5](NEXT_STEPS.md).
- **Split-spread / midpoint ENTRY-pricing fill-quality lever** — targets the measured ~31% sim-vs-live credit gap
  (C fills ~$0.025/leg below mid). HYDRA already prices entries at `mid ± buffer` (`base_strategy.py:~2815`); the
  lever is tightening toward pure mid / a pegged-midpoint order. **ENTRY-ONLY** (never stops — those use aggressive
  marketable limits, correctly); trades price for fill-rate; **validate on real money** (paper fills midpoint orders
  optimistically). Memory `golive_readiness_and_fill_lever`.
- **B live-fill monitoring (post-swap)** — B took the live paper seat on 2026-07-24 (7c, 7-slot grid). Its prior
  edge estimate was simulated-only and collected ~31% more credit than real fills give
  (`golive_readiness_and_fill_lever` memory); watch real-fill economics against that estimate as live-paper data
  accumulates on B.
- **Per-strategy edge validation** — C proven through 2026-07-24 (breakeven, live-paper; now dry-run shadow); B
  now accruing its own live-paper track record; D/E `INSUFFICIENT_DATA`.

---

## 8. Build-gated pending artifacts (do not exist yet)

Blocked on the D/E real-order **builds** (no real-order path exists for calendars), so a master doc can only
reference them as pending:

- `RUNBOOKS.md` — a future D-flip entry (number TBD; **RB-9** is now the B↔C live-seat-swap procedure, §3) /
  a future flatten-D entry
- **E's go-live runbook** (model on `D_GOLIVE_RUNBOOK.md`)
- `scripts/flip_d_live.sh`, `scripts/flip_e_live.sh`
- `scripts/broker_dc_smoke.py` (the calendar equivalent of `broker_paper_smoke.py`)

---

## 9. The reusable framework + related docs

- **Generic go-live template:** [`NEW_STRATEGY_PLAYBOOK.md`](NEW_STRATEGY_PLAYBOOK.md) **Step 10** — a
  strategy-agnostic go-live gate (arm-gate, flip/rollback/flatten, smoke, readiness gate, halt/kill). Use it +
  `D_GOLIVE_RUNBOOK.md` §5 as the template for any new go-live.
- **Incident runbooks:** [`RUNBOOKS.md`](migration/RUNBOOKS.md) RB-1..RB-7 (session lost, orders breaker, null
  SPX/VIX, stolen session, deploy rollback, naked short, backup restore).
- **State tracker:** [`docs/migration/PROJECT_STATUS.md`](migration/PROJECT_STATUS.md) (project state) +
  [`NEXT_STEPS.md`](NEXT_STEPS.md) (current what's-left, incl. **§10** — this doc's own build/refresh TODO).
- **Operator reference:** [`CLAUDE.md`](../CLAUDE.md) (deploy, troubleshooting, calypso-broker).

---

*Maintenance: keep §2 (matrix) and §6 (tests) current; this doc links out rather than duplicating, so most updates
happen in the linked sources. The open build/refresh tasks for this doc live in [NEXT_STEPS §10](NEXT_STEPS.md).*
