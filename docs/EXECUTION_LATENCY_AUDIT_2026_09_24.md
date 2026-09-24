# Execution-latency audit + consolidated backlog — 2026-09-24

**How this started.** B's dashboard showed +$2,800 on a day it had taken two
call-side stops. Diagnosing that (the number was *correct*) surfaced a
9-minute hole in B's own market-data record, which led to a question worth
answering properly: **what actually delays a stop between breach and fill, and
what can it cost?**

Everything below was measured on the live seat, not reasoned about. Two of the
six originally-proposed fixes are **refuted by their own data** and are recorded
here so nobody rebuilds them.

---

## 1. What the day actually cost, and where

B's worst stop overshoot on record (+$525 beyond trigger) broke down like this:

```
12:28:18  FIRST_BREACH   SV $1,470  (A2 trigger $1,400)
12:28:46  CONFIRMED      SV $1,505     <- 28s: one loop iteration
12:28:50  short call bought back @ $10.10   (4s — clean)
12:28:56  long call SELL @ $8.30  (bid − cross: correctly marketable)
12:29:46  ...did not fill. 45s fill-timeout elapsed.
12:29:58  attempt 2 fills @ $7.10
          close cost $2,100 vs $1,470 at breach
```

The long call sold **$1.20 lower** than where it was quoted when the stop
fired — **~$560**, essentially the whole overshoot. The order was priced
correctly; it chased a fast tape and missed, then sat out a timeout sized for
*entries*.

**Aggregate, B's 25 stops since 2026-07-24 (the A2 %-of-width era):**

| | |
|---|---|
| mean exit slippage | $43.10 |
| median | $42.50 |
| **total** | **$1,077.50** |
| worst | $595 (2026-09-24) |

A right-tail problem: 12 of 13 observed closes filled on attempt 1 in 2–5s.

## 2. The monitoring loop

`_handle_monitoring()` runs **one** stop check, then `_initiate_entry()` blocks
the single-threaded loop until every leg places or is abandoned
(`base_strategy.py:1734-1757`). `_check_stop_losses` is never called from
inside the placement path.

| | |
|---|---|
| normal heartbeat cadence (B) | 33s |
| blind window during placement, median | 65s |
| blind window, p90 | 405s (6.8 min) |
| blind window, max | 558s (9.3 min) |

`NORMAL_CHECK_INTERVAL_SECONDS = 5`, so B's *sleep* is only 12.5s (×2.5 pacing)
and vigilant is 2s. Observed cycle is 27–33s — **the sleep is the minority; the
body is 14–26s.** Vigilant mode does engage (cadence 68s → 28s) but it only
removes the sleep.

⚠️ **Known-unknown.** Bot logs only retain back to 2026-09-17, so blind windows
can only be detected on 8 days — containing **6 stops**, none of which fell in
one. An earlier draft of this analysis said "0 of 91 stops", which was wrong:
the other 85 are on days with no log coverage. **The blind window's cost is
unmeasured, not measured-as-zero.**

## 3. The real bottleneck: the shared rate gate

Broker requests are spaced **exactly 200ms apart** — `CALYPSO_IBKR_MAX_RPS=5`,
saturated. Over a 600s window:

```
2,858 requests | 4.88 req/s over active seconds | 85% of seconds pinned AT the cap

52%  iserver/marketdata/snapshot
32%  portfolio/<acct>/positions/0      904 calls
13%  iserver/exchangerate USD->EUR     376 calls
```

**~45% of the budget is telemetry, not trading logic:**

* **The FX calls** came from `HydraStrategy.log_position_snapshot`
  (`strategy.py`), fetching a EUR rate for the Google Sheets Positions tab.
  Sheets is `enabled: false` on all nine configs (retired 2026-07-17) and
  `TradeLoggerService.log_position_snapshot` returns on its first line when it
  is — so the rate *and the whole snapshot* were computed and discarded. The
  account's base currency is USD anyway (`ib_client.get_balance` docstring,
  verified against the live ledger 2026-09-10), so the conversion was a no-op
  even in principle.
* **The position reads** have **no cache** — every `_read_open_positions()`
  hits IBKR. Callers are `get_detailed_position_status()` (status log),
  `_get_total_saxo_pnl()` (P&L banner) and `get_dashboard_metrics()`. All
  display.

**The gate is also very conservative.** IBKR publishes **50 req/s for direct
Web API (authenticated username)** and 10 req/s *only via CP Gateway*. We run
OAuth 1.0a with **no gateway** and are gated at 5 — ~10% of the allowance.
Raise carefully: the penalty is 429 plus a **10-minute IP penalty box**, and
repeat offenders can be blocked permanently, which would take the whole fleet
down.

---

## 4. Verdicts

| # | Item | Verdict |
|---|---|---|
| **B1** | Stop building the discarded position snapshot (kills the FX calls) | ✅ **DONE** — this commit |
| B2 | TTL cache (~3s) on `_read_open_positions` | ✅ **DONE** |
| B3 | Split the **exit** fill-timeout from the entry one | ✅ **DONE** |
| B4 | Placement time budget | ✅ **DONE** (substitute for "interleave stop checks") |
| B5 | Escalate the **riskless long leg** to MARKET | ✅ Do, after B3, exercised on a dry-run seat first |
| B6 | Lower B's `api_pacing_multiplier` from 2.5 | ⏸️ Only after B1/B2 free headroom — otherwise zero-sum |
| B7 | Raise `CALYPSO_IBKR_MAX_RPS` 5 → 8 | ⏸️ Last, with 429 monitoring |
| ~~R1~~ | ~~Pre-entry gate on book stress~~ | ❌ **REFUTED — do not build** |
| ~~R2~~ | ~~Fetch only the at-risk side in vigilant mode~~ | ❌ **PREMISE FALSE — do not build** |

### ❌ R1 — pre-entry gate on book stress. Refuted.

Every B entry since 2026-07-24, classified by whether any *other* open side was
≥50% of its stop at placement:

| book at placement | n | mean | median | win rate | total |
|---|---|---|---|---|---|
| calm (<50%) | 87 | +$80.92 | +$245 | 80% | +$7,040 |
| **stressed (≥50%)** | **5** | **+$294.00** | **+$315** | **100%** | **+$1,470** |

Entries into a stressed book did **3.6× better**. n=5 is too small to claim a
real effect, but there is **zero evidence of harm**, and the gate would have
skipped $1,470 of winners to prevent a risk never observed to fire. It is also
economically sensible: a stressed book means the market moved, which means
richer premium — which is when selling is good.

### ❌ R2 — per-side vigilant fetch. Premise false.

`_check_stop_losses` calls `_batch_update_entry_prices()`, which fetches **every
leg of every open entry in ONE batched snapshot call**. Fetching fewer legs
saves a conid off a batch — nothing. The latency is gate queueing (§3).

### 🔄 B4 — why NOT to interleave stop checks into placement

The re-entrancy hazard is concrete, not theoretical: **74% of B's trading days
have two entries sharing a strike** (84 occurrences over 31 days). On
2026-09-24, E#5 and E#6 both used conid 920688820. Firing a stop close at a
conid another leg is actively working would net at the broker — exactly the
merged-position confusion that produced that day's stranded contracts and
$5,995 accounting drift.

A **placement time budget** (~90–120s, abort remaining rungs) bounds the blind
window deterministically with no re-entrancy risk — and would have prevented
E#6 outright, which spent 3.7 minutes on one leg that never filled.
**Detection-only telemetry** during placement is the way to learn whether the
acting version is ever warranted, since §2 says we currently cannot measure it.

---

## 5. Still open from the 2026-09-24 B diagnosis

Diagnosed, reported, **not yet fixed** — these predate the latency work and
rank above most of it, because two are on the live seat.

| # | Item | Where |
|---|---|---|
| A1 | ✅ **FIXED 2026-09-24.** **ORDER-010 accumulator drift.** Failed-entry unwind P&L books to the day aggregate but no entry: `total_realized_pnl` +$2,600 vs per-entry sum −$3,395, drift +$5,995. Same dual-accumulator class as the L-M3 double-book guard. | live seat |
| A2 | **Cancel/fill race.** A partial cancelled at 5/7 filled 7 in flight; the escalation then bought 2 more → **9 bought where 7 intended**. Re-read the terminal `cum_fill` after a cancel instead of trusting the count observed at cancel time. | live seat |
| A3 | **2 stranded long 7625 puts** from that race — owned by no entry, invisible to state. | live seat |
| A4 | **F's `trade_stops.net_pnl` records the credit, not the P&L** ($167.50 vs $92.50). The documented KNOWN GAP: the DB row is written before the dry-run close-cost correction. Dry-run only, but HOMER/HERMES/CLIO and the Stops tab read that table. | F |

---

## 6. The bigger prize — three tracks (carried forward)

This audit is *execution-quality hardening*. Useful, but let us be honest about
size: exit slippage is ~$1,078 over two months (~5% of B's gross), and the
detection-latency work is **tail-risk insurance, not expected value** — mean
stop overshoot is *negative* (−$267); stops usually realize better than
trigger.

The operator-requested strategic work (2026-09-24, memory
`b-curve-three-tracks-to-investigate`) is where the real money is:

| # | Track | Status |
|---|---|---|
| **C1** | **Entry fill leak** — measured at **~38% of B's net** going to crossing the spread. Rung pricing is live on B; the open question is what it actually recovered. **~4× larger than anything else on this page.** | not started |
| C2 | **Independent income** — diversification, not insurance. A second uncorrelated positive-expectancy stream. | not started |
| C3 | **Capital efficiency** — same edge on less deployed capital. | untouched |

**C1 and the B-items are the same family** — C1 is execution quality on the
*entry* path, B1–B7 on the *exit and monitoring* paths. They share tooling and
measurement, so doing B first is not a detour.

⚠️ **The three RISK levers are measured and CLOSED — do not re-derive them.**
A hedge is not possible (B's bad days are indistinguishable by vol, direction,
range or excursion); the stop is already correct (%-of-width beats
credit+buffer, 40% is the efficient threshold); strike selectivity loses money
(close strikes collect $304 vs $218). See
`docs/WHAT_WOULD_SOFTEN_B_2026_09_24.md` and
`docs/H_AS_A_HEDGE_FOR_B_2026_09_24.md`.

---

## 7. Sequencing

Everything touching the live seat deploys **after the close**, never with open
positions (`CLAUDE.md`, and B held 6 open sides while this was written).

1. **B1** — stop building the discarded snapshot ✅ *done, this commit*
2. **B2** — TTL cache on position reads
3. *observe one session* — confirm the gate drops off the cap and stop-detection cadence improves
4. **A1–A4** — the live-seat accounting fixes
5. **B3** — exit fill-timeout split
6. **B4** — placement budget + telemetry
7. **B5** — long-leg MARKET escalation (dry-run seat first)
8. **B6 / B7** — pacing and gate, with 429 monitoring
9. **C1** — the fill leak. The actual prize.
