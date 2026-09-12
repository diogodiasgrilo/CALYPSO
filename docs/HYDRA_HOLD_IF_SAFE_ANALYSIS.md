# Hold-if-safe cushion — why 50 pt (not 25)

**Date:** 2026-06-23 · **Scope:** Brandon variants B + C · **Knob:**
`strategy.brandon.take_profit.hold_safe_cushion_pts` (code default in
`bots/hydra/brandon/strategy.py`).

## What hold-if-safe does

Near expiry, when a Brandon iron condor has reached the 80% take-profit *and*
every live short is at least `hold_safe_cushion_pts` OTM, HYDRA **suppresses the
TP and rides the IC to expiry** — keeping 100% of the credit at zero close cost
instead of taking ~80% and paying slippage + commission. The credit+buffer stop
and the GEX breach-exit still backstop a reversal (hold-if-safe only suppresses
the *early* TP, never the stop). Implemented in `_tp_hold_to_expiry`.

This is distinct from MKT-049 (the net-of-cost gate), which holds when the close
is *expensive*. Hold-if-safe holds a *cheap-to-close, safe* IC purely to capture
the last ~20% by letting it expire.

## The decision

The cushion default was **raised from 25 → 50 pt** on 2026-06-23. The original
25 pt was not data-derived and is **decisively −EV**.

## The payoff is severely asymmetric

Per contract, choosing to RIDE instead of taking the ~80% TP:

- **Upside (no reversal):** the extra ~20% of a thin narrow-spread credit
  (≈ **+$12/contract** at a $60 credit).
- **Downside (a short is breached → credit+buffer stop near 0DTE expiry):**
  a near-max-loss on a 5-pt spread, ≈ **−$100 to −$440/contract**.

Break-even reversal (touch) rate:

```
p* = 0.20·C / (L + C)
```

For C = $60 credit and L = $100…$440 stop loss, **p\* ≈ 2.4%–7.5%**. Riding is
+EV only if the final-hour reversal rate is below that.

## Measured reversal rate — 84 trading days (Feb 5 – Jun 22 2026)

From `market_ohlc_1min` (SPX 1-min bars in `data/backtesting.db`): for each day,
the **max intraday excursion** of SPX from its price at T to the close (the right
measure — a stop fires when SPX *touches* the short, not at the close). "Touched
N pt" = a short N pt OTM at T would have been tested.

| Cushion | Final 90 min touched | Final 60 min touched |
|---|---|---|
| 15 pt | 59.0% | 51.8% |
| 20 pt | 44.6% | 34.9% |
| **25 pt** (old default) | **26.5%** | **26.5%** |
| 30 pt | 20.5% | 14.5% |
| 40 pt | 8.4% | 7.2% |
| **50 pt** (new default) | **4.8%** | **3.6%** |

(`scripts`-free repro: read `market_ohlc_1min`, group by `date(timestamp)`,
window from 14:30 / 15:00 to the last bar, `max(high−start, start−low)`.)

## Conclusion

- At **25 pt** the touch rate is **26.5%** — 4–10× the ~3–7% break-even. Holding
  there loses roughly **−$45 to −$85 per contract per decision**. Strongly −EV.
- At **50 pt** the touch rate is **~4–5%**, at/below break-even → holding is
  genuinely safe and marginally +EV. 50 pt is the data-derived threshold.

## Options considered and rejected

- **Widen the time window 60 → 90 min** (the original proposal): strictly −EV —
  the touch rate at 25 pt is the same 26.5% in both windows. Dead.
- **Build a shadow logger** to gather forward data: the 84-day path data already
  answers it decisively; a 2-week shadow would re-derive a known result.
- **VIX-scale the cushion:** VIX systematically *over*-states realized intraday
  vol, so a VIX-implied cushion (~36 pt of σ in the final hour at VIX 20) would
  over-cushion and rarely fire. The empirical 50 pt already bakes in realized
  final-hour behavior. More code, more brittle, less correct.
- **Remove hold-if-safe entirely:** forfeits a small real +EV (at ≥50 pt) and
  discards tested logic for no real simplicity gain.

## Caveat

50 pt is calibrated to the sample's *average* VIX (~18–22). On a genuinely
extreme day it is slightly under-cushioned, the feature just fires less safely,
and the credit+buffer stop + GEX exit remain the backstops. Perfecting that tail
(a regime-aware model) is not worth it for a ~$12/contract feature — the real fix
in this area was MKT-049 fail-closed (the same-day net-of-cost TP correction),
which stopped the actual money leak.
