# Live Halt Criteria + Week-1 Monitoring Plan

**Status: DRAFT — proposed by Claude 2026-09-12, NOT yet approved.**
Satisfies [`LIVE_READINESS_CHECKLIST.md`](LIVE_READINESS_CHECKLIST.md) **Gate 9** (halt criteria) and
**Gate 10** (week-1 monitoring). The approval commit itself is the operator's to make and must carry
their name — see §5.

> **Scope:** real-money week 1, **1 contract per entry** (Gate 8), variant **B** (the live seat).
> Every number below is derived from B's measured live-paper record, not chosen for roundness.

---

## 1. What the numbers are derived from

B's live-paper era (`analysis_eras.LIVE_ERA_SINCE` = 2026-07-24), measured 2026-09-12 from
`data/variant_b/backtesting.db`:

```
36 sessions · 25 traded · 7 contracts/entry
  net $7,375.65          per traded day  $295.03
  losing days            7 / 25
  WORST single day      -$1,793.30      2nd worst -$1,353.80   median loss -$1,267.70
  max stops in one day   2
  max consecutive days with a stop   3
  max drawdown          -$1,960.70
```

Divided by 7 for week-1 sizing:

```
AT 1 CONTRACT:   worst day -$256.19 · max drawdown -$280.10 · per traded day +$42.15
```

**The halt thresholds are set at roughly 1.5–2× the worst thing that happened in 25 paper sessions.**
The logic: inside that range you are having a bad day the strategy has already shown it can have.
Beyond it, the live behaviour is materially unlike the paper record — which means the model is
wrong, not that you are unlucky, and the correct response is to stop and look rather than to ride it.

⚠️ **These are scaled from a 25-day sample that has never contained a genuinely bad day.** They
bound *operational* damage in week 1. They are not evidence the tail has been measured.

---

## 2. HARD HALT — stop trading immediately, do not wait for the close

Any one of these. Halting is `systemctl stop` on the strategy unit — never `kill` (§4).

> ## ⚠️ H1 AND H3 WERE BOTH BREACHED ON 2026-09-21 — NINE DAYS AFTER THIS WAS DRAFTED
>
> **Do not adopt the table below unchanged.** On 2026-09-21 variant B settled at
> **−$3,088.20 over 7 contracts = −$441.17/contract** against H1's −$400, with **3 stop-losses**
> against H3's ≥3. Nothing malfunctioned — SPX ran 7692→7779 and every stop was call-side.
>
> **The A2 stop worked and is why it was not far worse.** SPX settled at 7765.00, putting all three
> stopped call spreads at or through their long strike — each worth the full $3,500 at expiry. B
> paid $4,300 to exit rather than $10,500: **the stop saved $6,200**, and the session would have
> been ≈−$9,288 without it. What failed is the *threshold*, not the strategy.
>
> ### Why it failed, and why it will fail again
>
> H1 is defined as **"1.6× the worst of 25 paper sessions"**. A threshold defined as a multiple of
> the worst observation is **guaranteed to be breached every time the sample grows a new tail** —
> here, nine days later. The same applies to H3's *"paper max was 2"* and to H2's and H4's
> *"worst/max paper"* derivations. This is a property of the definition, not bad luck.
>
> ### The measured distribution, now n=28 traded live-era sessions
>
> | | per contract |
> |---|---|
> | worst — **2026-09-21** | **−$441.17** |
> | 2nd worst — 2026-07-24 (the −$256 H1 was built on) | −$256.19 |
> | 3rd worst — 2026-09-14 | −$250.86 |
> | 5th percentile | −$256.19 |
> | median | **+$49.57** |
> | mean | +$19.77 |
> | losing sessions | 9 of 28 (32%) |
>
> ### The better basis: this strategy's loss per side is bounded BY CONSTRUCTION
>
> With the A2 %-of-width stop, one side's loss is capped at
> `pct_of_width × width × 100` = `0.40 × 5 × 100` = **$200 per contract**, plus slippage — it does
> not depend on how far the market runs. That is a *structural* number, and unlike "worst observed"
> it does not move when the sample grows. A session threshold can be stated as **sides × $200**:
>
> | sides stopped | implied session loss / contract |
> |---|---|
> | 2 | −$400 *(what H1 says today — i.e. H1 implicitly assumes ≤2 stops)* |
> | 3 | −$600 |
> | 4 | −$800 |
>
> **H1 and H3 are therefore the same constraint stated twice**, which is why both broke on the same
> day: 3 stops × $200 = $600/contract of structural exposure against a $400 cap. Monday came in at
> −$441 because one side closed early for a small gain.
>
> ### Two coherent options for the operator (Gate 9)
>
> **Option A — structural.** Set H1 = `max_sides_tolerated × $200`. Choosing 3 stops as the
> tolerance gives **H1 = −$600/contract** and makes H3 (≥3 stops) the binding trigger, with H1 as
> its dollar shadow. Self-consistent, and stable as the sample grows.
>
> **Option B — distributional.** Keep a percentile rule, but state **n and the date of the worst
> observation beside the number**, and re-derive on a schedule rather than once. Accepts that the
> number will move.
>
> **Not recommended: re-deriving "1.6× the new worst" (≈ −$700).** It repeats the definition that
> just failed, and would be breached again by the next fatter tail.
>
> ⚠️ Whichever is chosen, **H1 and H3 must be made consistent with each other** — today a
> 3-stop session is simultaneously "within H3's limit if it is exactly 3" and "past H1". Recorded
> 2026-09-22; the choice is the operator's and belongs in the Gate-9 approval commit.

| # | Trigger | Threshold (1 contract) | Why this number |
|---|---|---|---|
| H1 | Realized loss in one session | **≤ −$400** | 1.6× the worst of 25 paper sessions (−$256) |
| H2 | Cumulative realized loss, week 1 | **≤ −$600** | 2.1× the worst paper drawdown (−$280) |
| H3 | Stop-losses in one session | **≥ 3** | paper max was 2 |
| H4 | Consecutive sessions with ≥1 stop | **≥ 4** | paper max was 3 |
| H5 | `CRITICAL_INTERVENTION` alert | **any** | by definition operator-required |
| H6 | Naked short detected | **any** | undefined risk — see [RUNBOOKS RB-6](RUNBOOKS.md) |
| H7 | `orders` circuit breaker OPEN | **> 5 min during RTH** | cannot exit a position you cannot send orders for |
| H8 | Broker session down during RTH | **> 15 min** | positions unmanaged; restart `calypso-broker`, and if it will not hold a session, flatten |
| H9 | ARGUS `FAIL` | **3 consecutive cycles (~45 min)** | health monitor says the bot is not well |
| H10 | Position count disagrees with broker | **any unreconciled** | never trade through a reconciliation gap |

**Scaling rule:** every dollar threshold is *per contract*. At 2 contracts, double H1/H2. Do not
carry week-1 dollar limits forward unchanged after scaling up — that would silently tighten them.

---

## 3. SOFT REVIEW — do not halt, but look before the next session

These do not stop trading; they mean the day gets read carefully at EOD rather than skimmed.

- Any single session worse than **−$250** (≈ the worst paper day at 1c)
- Two consecutive losing sessions
- Any entry whose fill is worse than **$0.10/leg** below the decision mid (`analyze_fill_quality.py`)
- Any `BROKER-RECONCILE` drift line that is not explained by 0DTE settlement timing
- Realized P&L diverging from `scripts/verify_pnl_vs_account.py` by **> $50 cumulative**
- Any entry skipped for a reason not seen in paper

---

## 4. Halt procedure (rehearse before go-live)

```bash
# STOP TRADING — the hydra* units are what place orders.
gcloud compute ssh calypso-bot --zone=us-east1-b --project=calypso-trading-bot \
  --command="sudo systemctl stop hydra_variant_b"
```

- **Never `kill`/`pkill`** — `Restart=always` brings it straight back. The one sanctioned `kill -9`
  is `scripts/chaos_test.sh` ([RB-10](RUNBOOKS.md)).
- Stopping the strategy does **not** close open positions. If the halt is for risk (H1/H3/H6), decide
  explicitly whether to flatten — and do it through the broker, not by leaving it to expiry.
- `calypso-broker` is a passive session holder; stop it too only to drop the IBKR session entirely.
- **Target time from decision to stopped: < 30 seconds from any location.** Rehearse it: the command
  must be somewhere reachable from a phone, not only in this repo.

---

## 5. Week-1 monitoring plan (Gate 10)

**Sizing: 1 contract every day of week 1.** No intra-week scaling, whatever the results.

| Day | Sizing | Operator commitment | Mandatory at EOD |
|---|---|---|---|
| **1** | 1c | Watch **every** entry, stop and settlement live. Be at a terminal 09:30–16:15 ET. | Journal entry; `variant_performance.py`; `analyze_fill_quality.py`; `verify_pnl_vs_account.py` |
| **2–3** | 1c | Check in at each entry slot and at settlement | Journal entry + fill-quality check |
| **4–5** | 1c | EOD check-in only | Journal entry |
| **Fri review** | — | Week-1 go/no-go (below) | Written verdict committed to the repo |

**Friday decision rule — all four must hold to scale to 2 contracts in week 2:**

1. Zero hard-halt triggers fired.
2. Week-1 net **≥ 0** (break-even counts).
3. Fill quality within **$0.05/leg** of the paper baseline (`$26.83/entry` at 7c ⇒ ~$3.83/entry at 1c).
4. `verify_pnl_vs_account.py` reconciles cumulative P&L to within **$25**.

Otherwise: **stay at 1 contract for another week**, or pause. Do not average across the two.

> **Explicit anti-pattern.** "It's only 1 contract, the losses don't matter, let's scale early" is
> exactly how a week-1 process becomes decorative. The point of week 1 is not the P&L — it is
> confirming that live execution matches paper execution. Scale on *process*, not on profit.

---

## 6. What this plan does NOT protect against

Stated plainly so it is not mistaken for more than it is:

- **The tail.** B wins 71% with a 0.76 payoff ratio over 25 traded days that have never contained a
  genuinely bad day. These thresholds bound week-1 operational damage; they do not tell you the
  strategy's worst case, because nothing has measured it yet.
- **Execution drag.** ~30% of paper edge is estimated to be execution cost. Real fills may be worse
  than paper fills, and paper fills midpoint orders optimistically.
- **Combo behaviour.** Untestable on paper (IBKR simulates combos). Week 1 legs in, like paper.

---

## 7. Approval record (operator action — NOT Claude's)

Gate 9 requires an auditable approval. After every gate is green, the operator runs:

```bash
git commit --allow-empty -m "approved: HYDRA live trading starting $(date +%Y-%m-%d)

Approver: <name>
Approver email: <email>

Halt criteria + week-1 plan: docs/migration/LIVE_HALT_CRITERIA.md @ <sha>
Live-readiness checklist: all gates GREEN as of HEAD."
```

**This document is a draft until that commit exists.** Claude proposed the thresholds; only the
operator can adopt them.
