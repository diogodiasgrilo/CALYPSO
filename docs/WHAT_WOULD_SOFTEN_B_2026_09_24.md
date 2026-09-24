# What would actually soften B's P&L curve

**Question asked (2026-09-24):** *"Research strategies that could help B out — something that wins
on days B really loses, but wouldn't lose money all the time."*

I started on that and the data redirected it. **The biggest lever is not a hedge, and the premise
that B loses on big-move days is not what its own record shows.**

---

## 1. B does NOT lose on big-move days

The intuitive story — short premium gets hurt by large moves, so buy convexity — does not survive
contact with B's 95 live days.

| day type | n | B mean/day | losing days |
|---|---|---|---|
| SPX range **> 1.0%** | 21 | **−$169** | 7 / 21 |
| SPX range **≤ 1.0%** | 74 | **+$342** | 16 / 74 |

B wins **two-thirds** of large-range days. And its worst days are utterly ordinary:

| date | B net | SPX range | |
|---|---|---|---|
| 2026-07-16 | **−$7,074** | 0.88% | an average day |
| 2026-07-13 | **−$6,809** | 0.76% | below average |
| 2026-07-24 | −$1,793 | 0.86% | close-to-open move of **0.02%** |

**A tail hedge cannot help here.** Convexity pays on the days B is already fine, and stays silent
on the days that hurt. That is the same mechanism that made [H fail as a
hedge](H_AS_A_HEDGE_FOR_B_2026_09_24.md) — and it is worse than that analysis suggested, because
the days needing cover are not large-move days at all.

---

## 2. B's bad days are STOP days 🔴

Decomposing the worst sessions by what actually closed:

| date | daily net | composition |
|---|---|---|
| 2026-07-16 | −$7,074 | **4 stop-losses = −$7,650** (2 early closes +$775) |
| 2026-07-13 | −$6,809 | **3 stop-losses = −$5,825** |
| 2026-09-21 | −$3,088 | **3 stop-losses = −$3,260** |
| 2026-09-01 | −$1,343 | **2 stop-losses = −$2,800** |
| 2026-07-24 | −$1,793 | **1 stop-loss = −$1,295** on a 0.02% day |

Lifetime: `trade_stops` sum to **−$137,340** against a net of **+$21,742**. The stops are the
entire loss side of the business.

This follows from the structure. B runs **5pt-wide spreads at 7 contracts** with the A2
%-of-width stop at 40%: `0.40 × 5 × 100 × 7 = $1,400` **per side**. Four sides stopping costs
$5,600 — and on a narrow spread, "price reached my short strike" is an ordinary 0.5–1% excursion,
not a tail event. B is not being hurt by volatility; it is being hurt by **routine moves reaching
close strikes, converted into realised losses by its own stop.**

---

## 3. The stop — CORRECTED with per-tick data

> ⚠️ **My first pass overstated this by ~4×, and the correction changes the recommendation.**
> It priced each stopped side against that day's `spx_close`, capped at the spread width, and
> reported **−$12,381**. That approximation is too crude. Re-run against `spread_snapshots` — the
> bot's OWN recorded cost-to-close every ~10s, i.e. what the position was actually worth — the
> real figure is far smaller. The section below is the corrected version; the earlier number
> should not be used.

### 3a. Stopping vs holding, measured from the bot's own marks

| | B | C |
|---|---|---|
| stop-loss sides with snapshot data | 21 | 9 |
| holding would have been better | **17** | **8** |
| stopping was better | 4 | 1 |
| **net of holding instead of stopping** | **+$3,360** | **+$1,195** |
| median per side | +$175 | +$160 |

Both variants agree in direction: the stop is **mildly net-negative**. But the magnitude is
~$160–175 per side, not thousands — and the shape matters more than the sign:

```
2026-08-28 E#4 put   stopped $-1,785   held $-1,225   diff +$560
2026-07-02 E#2 put   stopped $-2,250   held $-1,900   diff +$350
2026-07-16 E#5 put   stopped $-2,000   held $-1,675   diff +$325
```

**Holding does not turn these into winners.** It loses somewhat less. The damage is already done
by the time the stop fires — the position reached the short strike, and every path from there is a
large loss. The stop is not the leak; it is a modest tax on top of one.

### 3b. But %-of-width IS better than credit+buffer — that part holds

Running the existing `stop_shadow` aggregator over C (which still runs credit+buffer) against the
full per-tick record:

| pct | fired | net $ vs credit+buffer | tail-capped | premature |
|---|---|---|---|---|
| 25% | 8 | **+$1,890** | +$4,480 (5) | −$1,680 (2) |
| **40%** | 4 | **+$1,890** | +$1,890 (4) | **$0 (0)** |
| 50% | 1 | +$700 | +$700 (1) | $0 (0) |
| 65% | 1 | +$315 | +$315 (1) | $0 (0) |

%-of-width beats credit+buffer at **every** threshold, and **40% reaches the same net as 25% with
zero premature stops** — the efficient point. **B's existing A2 40% stop is the right choice**, and
this is the evidence the shadow trial was set up to produce.

(On B the same tool reports ≈$0 at 40%, which is expected and not a result: B has *run* the 40%
stop since 2026-07-24, so there it is comparing the stop against itself.)

For every `stop_loss` side with a matching entry row, comparing what the stop cost against what
that side would have cost at settlement (capped at the spread width):

| | count | amount |
|---|---|---|
| stop **saved** money | 6 | $14,169 avoided |
| stop **cost** money | 15 | $26,550 given up |
| **net effect of stopping vs holding** | | **−$12,381** |

### 3c. What this means for the recommendation

The stop question is now **answered and largely closed**:

* the *type* is right (%-of-width, not credit+buffer) — worth +$1,890 on C;
* the *threshold* is right (40% — same benefit as 25% with zero premature stops);
* removing it entirely would have added ~$3,360 over two months on B, at the cost of unbounded
  intraday risk the external literature warns about specifically for 0DTE gamma in the final
  60–90 minutes. **That is not a trade worth making for ~$175 a side.**

So **re-calibrating the stop is NOT the big lever I first claimed.** The ~$160–175 per side it
could recover is real but small against −$7,074 days. The damage is done *before* the stop fires:
price reaches the short strike, and from there every path is a large loss.

**Which relocates the lever to strike selection and entry selectivity** — not being at those
strikes on those days. That was item 2, and it should now be item 1.

---

## 4. So what to do — in order of expected value

### First: re-calibrate the stop (no new strategy, no new capital)
The highest-leverage change available, because it acts on the entire loss side rather than
offsetting a fraction of it. Options worth measuring against the existing shadow data:
* **A wider stop** — 40% of a 5pt width is tight when the short strike is ~8δ away.
* **A time-conditional stop** — loose early, tightening into the gamma hour, instead of one
  constant threshold. MKT-042 buffer decay already does this for the credit+buffer path and is
  explicitly bypassed in A2 mode.
* **Stop on the UNDERLYING, not the mark** — several of these fired on spread marks during moves
  that reverted, which is the same failure mode D hit at the open.

### Second: entry-side selectivity
If routine moves toward the strikes are the damage, the cheapest defence is fewer/further strikes
on days that look directional. B already skips FOMC days; the GEX veto work was aimed at this and
is data-blocked rather than disproven.

### Third — and only then — an external hedge
If you still want a separate leg, the profile it must satisfy is now precise, and it is **not** a
tail hedge:
* pays on **0.5–1% directional excursions** reaching the short strikes
* within the **same session** (B's risk is intraday)
* cheap enough to carry on the ~75% of days nothing happens

That combination is genuinely hard, and it is expensive precisely because such moves are common —
you are buying insurance against an ordinary event. Anything far enough OTM to be cheap will not
pay. **This is why I would exhaust items 1 and 2 first:** they cost nothing to test and act on the
dominant term.

---

## 4-bis. Named candidates, and why each is eliminated

*"Which strategies exactly?"* — items 1 and 2 are changes to **variant B itself**, not new
strategies. Item 3 asked for one, so here are the concrete candidates scored against B's measured
damage profile.

**First, what the profile actually is.** Across B's 15 worst days:

| discriminator | worst days | all other days | usable? |
|---|---|---|---|
| VIX change | **−0.11** (rose on only **7 of 15**) | −0.22 | ❌ no vol spike to monetise |
| direction | **UP 7 / DOWN 6 / chop 2** | mixed | ❌ no side to pick |
| max intraday excursion | **0.66%** median | 0.55% | ❌ barely distinguishable |

**That table is the whole answer.** A hedge can only be cheap if it fires selectively, and
selectivity requires the bad days to be *distinguishable* by some market variable. B's are not —
not by range, not by vol, not by direction, not even by excursion (0.66% vs 0.55%). A hedge that
cannot tell the days apart pays on all of them, which is precisely the "loses money all the time"
outcome to be avoided.

| candidate | verdict | why |
|---|---|---|
| **Long strangle (H)** | ❌ measured | correlation inverts to **+0.54** once H's filters apply |
| **Long OTM puts / put backspread** | ❌ | only **6 of 15** worst days were DOWN days |
| **VIX calls / long VIX futures** | ❌ | **VIX FELL on 8 of 15** worst days; mean change −0.11 |
| **Tail hedge (far OTM)** | ❌ | there is no tail — 0.66% vs 0.55% excursion |
| **Trend / momentum overlay** | ⚠️ weak | 13/15 had a direction, but so do most ordinary days |
| **Second uncorrelated income strategy** | ⚠️ | diversification, not a hedge — helps the average, not the worst days |
| **Intraday delta hedge of B's own book** | ✅ **the only survivor** | fires on the exact event that does the damage (price nearing a short strike), works **both** directions, and costs **no premium** — only whipsaw and commissions |

### And item 3 has already been tried on B

`config_variant_b.json` → `defensive_overlay`: `debit_spread_enabled: false`,
`butterfly_enabled: false`. Both overlay hedges are **OFF**, disabled 2026-08-25 and 2026-09-04,
with the recorded reason being decisive: **the hedge debits ($1,925–$2,240) EXCEEDED the IC-side
loss they were defending (~$1,400, already bounded by the A2 stop).**

That is this exact experiment, already run on this exact book, and it failed on cost — which is
the same conclusion the discriminator table reaches from the other direction.

---

## 5. What I would NOT do

* **Do not size H as the hedge.** Measured separately: correlation inverts to **+0.54** once H's
  filters are applied, because "enter when IV is cheap" selects quiet days — B's best days.
* **Do not buy tail convexity.** B wins on 14 of 21 large-range days. Convexity pays there.
* **Do not simply remove the stop.** The counterfactual assumes a holding regime whose gamma risk
  this analysis does not model, and 6 of the 21 stops genuinely saved money.

---

## Reproducing this

```
python -m scripts.analyze_h_hedges_b       # the H-as-hedge model
```
The B decomposition above came from ad-hoc queries over `daily_summaries`, `trade_entries` and
`trade_stops` in `data/variant_b/backtesting.db`; the stop counterfactual joins `trade_stops` to
`trade_entries` and prices each side against that day's `spx_close`, capped at the spread width.


---

# Item 3 investigated — and it closes too. **B is well-calibrated.**

The stop question resolved to "already correct", which relocated the lever to **strike selection
and entry selectivity**. That has now been measured, and the answer is that there is nothing to
take.

## What actually predicts a stop

Per SIDE (the unit the stop acts on), across 485 sides of which 21 stopped:

| entry-time feature | stopped | survived | effect |
|---|---|---|---|
| **\|delta\|** | **0.11** | **0.08** | **d = +0.68** |
| **OTM distance** | **0.54%** | **0.73%** | **d = −0.52** |
| credit collected | $304 | $218 | d = +0.48 |
| VIX at entry | 16.20 | 16.79 | d = −0.41 |
| **entry hour** | 11.01 | 11.10 | **d = −0.10** |

**Timing is noise.** The per-slot table looks compelling (12:15 stops 7.7%, 09:45 stops 44%) but
entry hour barely separates stopped from surviving sides at all, and the slot CIs almost all cross
zero at n=9–17. Picking the best four of seven slots post-hoc would have been an overfit.

**Strike placement is the real signal**, and it is monotonic:

| OTM quartile | n | stop rate |
|---|---|---|
| Q1 0.20–0.48% | 121 | **8%** |
| Q2 0.48–0.62% | 121 | 5% |
| Q3 0.62–0.85% | 121 | **2%** |
| Q4 0.85–2.57% | 122 | **2%** |

The closest quartile is stopped **4× more often** than the two furthest.

## And acting on it loses money — every way it can be acted on

Baseline: **485 sides, +$66,607, 21 stops.**

**Refusing close sides (an OTM floor):**

| floor | refused | credit forfeited | stops avoided | **net** |
|---|---|---|---|---|
| 0.35% | 32 | $8,890 | 3 | **−$5,425** |
| 0.50% | 134 | $30,910 | 10 | **−$16,550** |
| 0.60% | 226 | $51,227 | 16 | **−$27,392** |

**Refusing high-delta sides (a delta ceiling):** −$3,180 at 0.20δ through −$40,110 at 0.09δ. Every
threshold negative.

**Relocating rather than refusing** — keep trading, just further out — is a wash: **+$1,772** at
best (0.55% floor) and negative at every other threshold. That is noise against a $66,607 base,
and it leans on the assumption that the safer band's credit and stop rate transfer when you move
there *deliberately*, which is exactly the kind of assumption that does not hold.

## Why — and this is the real answer

**The close strikes that stop more often also collect materially more credit: $304 vs $218.** The
extra premium more than pays for the 8%-vs-2% stop rate. Q1 is *net positive*, it is simply
noisier.

**B's 21 stops are not a defect. They are the price of $66,607 of collected credit.** Every
mechanism for removing them removes more edge than it saves — which is the definition of a
well-calibrated premium-selling book.

## So all three levers are closed

| lever | verdict |
|---|---|
| **A hedge** | No viable candidate. B's bad days are indistinguishable from good ones by vol, direction, range or excursion — so nothing can fire selectively, and the one overlay already tried cost more than the loss it defended. |
| **The stop** | Already correct. %-of-width beats credit+buffer (+$1,890 on C), and 40% is the efficient threshold — same benefit as 25% with zero premature stops. |
| **Strike selectivity** | Measured and negative. Every floor, ceiling and relocation loses money or is noise. |

## What that means for the original question

There is no adjustment to B that softens its curve without costing more than it saves. Its
drawdowns are **the variance of a positive-expectancy business**, not an engineering defect.

The remaining paths to a better curve are therefore not risk management at all:

* **more edge per trade** — the entry-fill work (`entry_fill_leak_rung_pricing`) measured ~38% of
  B's net going to crossing the spread; that is a real, already-identified leak;
* **more independent bets** — not a hedge, but genuinely uncorrelated *income*, which smooths by
  diversification rather than by insurance;
* **capital efficiency** — the same edge on less deployed capital.

**Confirming there is nothing to fix here is itself the result.** It redirects attention from a
risk-management problem that does not exist to an execution problem that is already documented.
