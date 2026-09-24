# Would H stabilise B? — measured against B's own 95 live days

**Question asked (2026-09-24):** *"Is it possible that H and B complement each other? Because they
want different things. This is the key to the puzzle we were missing to make B really stable and
our profit curve as good as possible."*

This is precisely what H was built to answer. Its spec says so: *"The real deliverable is not the
claimed edge — it is a measured answer to 'does long gamma hedge our short-gamma bad days?'"*

**Answer, on the evidence available: no — and the reason is more interesting than the answer.**

---

## The structural logic is sound. That is not what fails.

B sells premium and is short gamma: it is hurt by large moves. H buys premium and is long gamma:
it needs them. Structurally they are opposites, and the anti-correlation is real — measured at
**−0.19** across 95 days when H is modelled as trading every day.

What fails is everything after that.

---

## Result 1 — H modelled on all 95 of B's live days

| | B (actual) | H (modelled, 1 contract) |
|---|---|---|
| mean/day | **+$228.86** | **−$315.67** |
| sd | $2,046 | $779 |
| win rate | — | 44% |
| total | — | **−$29,988** |

**Correlation: −0.187.** Negative, as the thesis predicts.

And yet the combined curve is worse on every axis:

| | mean/day | sd | worst day |
|---|---|---|---|
| B alone | +$228.86 | $2,046 | −$7,074 |
| B + H×1 | **−$86.80** | $2,049 | −$6,556 |
| B + H×5 | −$1,349 | $4,048 | −$9,894 |
| B + H×10 | −$2,928 | $7,678 | −$15,075 |

**The volatility does not fall even at 1 contract** (2,046 → 2,049), while the mean goes negative.
That is the whole lesson in one line: *anti-correlation is not enough.* A hedge that loses money
and carries comparable variance makes the curve worse, not smoother. Diversification pays only
when the added leg is either profitable or cheap enough that its cost is less than the variance it
removes. This is neither.

On B's ten worst days H offsets **1.5%** of the combined loss at 1 contract — and on three of
those ten days (07-06, 07-24, 09-14) **H lost money too**: the market moved enough to hurt B and
not enough to pay H, or moved and reverted.

---

## Result 2 — with H's real gates applied, it gets WORSE, and the sign flips 🔴

H does not trade every day. It has an IV-percentile gate (<35%) and a range-expansion gate. Re-run
on only the days those gates would have passed:

| | value |
|---|---|
| days H would have traded | **9 of 95 (9%)** |
| H win rate on them | **22%** |
| H mean/day | **−$653.58** |
| **correlation with B** | **+0.535** |

**The correlation inverts from −0.19 to +0.54.** The gates turn H from a weak hedge into something
that loses *alongside* B.

### Why — and this is the finding that matters

H's entry filter says **"enter when implied volatility is cheap."** Cheap IV means the market
expects little movement. **The market is usually right about that.** So the filter systematically
selects low-movement days — which are exactly **B's best days**.

H is running a *value* bet (buy premium when it is underpriced). A hedge is an *insurance* bet (pay
whatever it costs on the days you are most exposed). **Those are different objectives, and H's
filters are tuned for the first one.** Among the 9 gated days, the two where B lost meaningfully
(−$1,354 and −$1,268) H also lost (−$924 and −$949).

⚠️ **n = 9. That correlation is not statistically meaningful** — it is a mechanism worth
understanding, not a number to trust. The 95-day ungated result is the better-powered one, and it
says the same thing less sharply.

---

## What the model does and does not do

Honest limits, because the conclusion rests on them:

* **The expected move is calibrated from a single real observation** — H's actual 2026-09-24 entry
  (SPY 764.69, VIX 15.73 → EM 2.8pt, debit $1.02/share). Every other day is scaled from that by
  VIX. A different EM shape moves both the strikes and the debit.
* **Intraday exits are approximated from daily OHLC.** The +50% target is credited when the best
  single-sided excursion reaches 1.5× the debit. That is *generous* to H — it assumes the touch is
  captured — so the real numbers are likely worse, not better.
* **The +100% elevated target is not modelled.**
* Commissions and slippage are excluded. Both hurt H (four legs of round-trip on a $102 position).

The model is therefore **optimistic toward H** on exits and crude on pricing. It is not a backtest.
It is enough to answer a directional question, and not enough to size anything.

---

## What would actually have to be true for a hedge to work

1. **It must pay on B's worst days specifically.** B's worst days are large-move days. A hedge
   should therefore be selected for *move potential*, not for *cheap premium*. That is close to the
   opposite of H's current filter.
2. **It must be cheap relative to the variance it removes.** At 1 contract H costs ~$316/day
   modelled and removes no measurable variance. Sizing it up to matter multiplies the losing days
   faster than the winning ones — visible in every row of the sizing table.
3. **Or it must be profitable on its own.** A profitable anti-correlated leg improves the curve at
   any size. H is not that in this model, and its source's 80% win-rate claim remains unverified.

---

## Recommendation

* **Do not size H as a hedge for B on this evidence.** The one configuration that would help
  (large size) is the one the data says is most damaging.
* **Judge H on its own merits.** "Does this make money?" is the question its dry run should answer
  first. It has **one real day** of record (2026-09-24, entered at 09:45, $102 debit).
* **Keep the measurement running.** The model is crude and optimistic toward H; the live record is
  the real test and it has barely started.
* **If a stabiliser for B is the actual goal**, this analysis says the search should be for
  something selected on *exposure* rather than on *cheapness* — and that is a different strategy,
  not a retuning of H.

**Finding this out is H doing its job.** It was built to answer this question, and a well-measured
"no" is the answer arriving, not the project failing.
