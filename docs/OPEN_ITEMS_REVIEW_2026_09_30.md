# The open items, researched — what's still doable, what's worth it

**2026-09-30, pre-market.** Every item below was measured, not recalled. Three
changed status on contact with the data; two recommendations are the opposite of
what the backlog implies.

## The governing constraint

`docs/GO_LIVE_MASTER.md` §2-quater: **any change to money behaviour resets the
sample, bug fixes included.** The real gate is *15 consecutive trading days with
no economics-changing commit* — **never once achieved** in ~45 trading days
(commits to B's money path per week: 2,1,2,3,8,7,10,20,10,18). The clock is
currently at ~day 2.

That reframes every recommendation here. The question is not "is this a good
change" but "is it worth another restart of the only gate that isn't noisy".

---

## 1. Rate gate (B7) — MEASURED, and it is pinned. **Recommend: DO NOT raise yet.**

Re-measured over 09-29 RTH, 83,340 requests:

* **91% of inter-request gaps sit exactly at the 200ms cap** (was 85% pre-fix — *worse*)
* peak 600s rate **5.00/s against a cap of 5.00** — literally saturated
* mix moved: snapshot 52%→**84.8%**, positions 32%→**12.2%**, exchangerate 13%→**0%**

So the 09-24/09-27 fixes worked on *composition* — the FX calls are gone, position
reads are cached — and the loop simply spent the freed budget on quotes.

**Safety is not the blocker.** Over 7 days: **0 HTTP 429s, 0 non-200 RPCs, 0 breaker
opens, 0 SLOW-penalty engagements.** The 5 rps cap is self-imposed and has never
been tested by IBKR.

**Why not do it anyway:** raising it changes quote cadence → changes when stops
fire → money behaviour → resets the 15-day clock. And the backlog's own honest
sizing says the latency half is *tail-risk insurance, not EV* — **mean stop
overshoot is NEGATIVE (−$267)**. Paying the only gate that matters for insurance
against a negative-mean risk is a bad trade.

**Do it when:** the next legitimate unfreeze window opens anyway, or a stop-latency
incident makes it urgent. Bundle it with other money-path changes so one clock
reset buys several fixes.

## 2. B5 (MKT-047 MARKET escalation) — **still blocked, blocker did NOT expire.**

`Close via MARKET (MKT-047 escalation)` has fired **0 times**, ever. My first grep
said 15 — it was matching the descriptive string "marketable limit, MARKET
fallback" in ordinary close logs. Checked against the actual log signature in
`base_strategy.py:5077`, the count is zero.

**Recommend: leave.** There is still nothing to observe. Note also that **B's logs
retain only ~7 days** (09-23 onward), which caps every log-based forensic here.

## 3. The `C`/`H` price prefix — **recommend: DO NOT "fix". It is already correct.**

IBKR decorates price fields as `'C7638.04'` (close) or `'H…'` (halted); the parser
returns None. The backlog calls this "a real second bug".

It is not. A `C`-prefixed price means *this is not a live quote*. Returning None is
the truthful answer for a live trading path, and stripping the prefix would feed
stale close prices into live decisions — **exactly the 2026-07-06 stale-SPX phantom
that cost a fabricated −$6,036.92**. Settlement does not need it either:
`_resolve_spx_close` recovers the day's last tick from the DB.

**The only change worth making** is a debug counter so the frequency is known.
Cheap, no behaviour change, no clock reset.

## 4. C2 — independent income. **MEASURED. Nothing is ready to promote.**

Daily net P&L since the live-seat swap (2026-07-24), correlation against B:

| var | n | total | mean/day | t | corr with B |
|---|---|---|---|---|---|
| **B** (live) | 34 | **+$4,811.45** | +$141.51 | 0.60 | — |
| C | 25 | −$7,856.18 | −$314.25 | −1.60 | 0.48 |
| F | 8 | +$20.70 | +$2.59 | 0.16 | **0.75** |
| **G** | 21 | +$190.35 | +$9.06 | **0.10** | **0.04** |
| D | 9 | −$133.25 | −$14.81 | −0.16 | 0.06 |
| E | 3 | −$21.80 | −$7.27 | −1.21 | — |

**The finding:** no candidate has a positive expectancy distinguishable from zero.
G is the only *structurally* independent one (**corr 0.04** — genuinely uncorrelated,
which is precisely what C2 wants) but its edge is **t = 0.10**. F is 0.75 correlated
— it is not independent income, it is more of the same trade. C is a worse B.

**Recommend: keep all of them running in dry-run and do nothing else.** C2's blocker
is sample size, not effort, and no amount of work substitutes. Revisit G when it
reaches n ≈ 60 traded days.

⚠️ **CLAUDE.md's G figure was stale by 6.5×** — it read +$1,239.35 (measured 09-19);
G's DB and metrics file agree it is now **+$190.35**. Corrected. A dry-run lifetime
P&L quoted in a doc ages fast.

## 5. C3 — capital efficiency. **NOT MEASURABLE TODAY. This is the cheap unlock.**

`trade_entries.margin_utilization_pct`: 315 rows, 85 non-null, **and every one of
them is 0.0**. The column exists and records nothing — the same defect class as
`day_type` and `expected_move`, both fixed today.

Account tradable is **$1,003,712.75**; B's structural worst case is ~7 entries × 2
sides × $3,500 ≈ **$49,000, under 5%**. But a paper account's size is arbitrary, so
"return on capital deployed" cannot be answered without real margin figures.

**Recommend: fix the telemetry, not the strategy.** Populating
`margin_utilization_pct` is additive, touches no decision, and does not reset the
clock — and without it C3 cannot even be stated as a question. This is the highest
value-per-risk item on the page.

## 6. The unwound-partial-leg reconcile lead — **recommend: instrument, don't chase.**

See `[[unwound_partial_legs_invisible_to_pnl]]`. Legs from abandoned partial fills
realize P&L at IBKR and appear in neither `trade_entries` nor `trade_stops`
(−$840.19 on 09-25 alone). Days with an unwind have large drift; days without have
small drift (n=5).

The per-leg breakdown **already captures `qty`** but the log line prints only
description and value. Print it, and flag whether each conid appears in
`trade_entries` — the drift should then decompose. Additive telemetry, no clock cost.
The daily reset of `realizedpnl` destroys the evidence overnight, so it must be
captured during the session.

## 7. Registered tests — **not yet at their thresholds.**

| test | needs | has |
|---|---|---|
| one-entry-a-day | 40 traded live days | **34** |
| e#4 prune | 27 entries | **19** |
| GEX gate | 25 vetoes | **28** ✅ |

Only the GEX gate is ready. **Recommend: run that one** — it is pure analysis
against a pre-registered rule, so it carries no clock cost and settles a
question that has been open since 09-05.

## 8. Real money — blocked on funding. Nothing here is actionable by me.

Gate 9 also still wants the operator's approval commit on `LIVE_HALT_CRITERIA.md`,
and two governance items are open regardless of funding: **H1/H3 were breached on
2026-09-21 with no recorded review**, and **G-3** — the halt criteria were derived
from B, a defined-risk IC, so their dollar limits do not transfer to an
undefined-risk strangle.

---

## Recommended order

1. **Populate `margin_utilization_pct`** — unlocks C3, additive, no clock cost.
2. **Instrument the unwound-leg decomposition** — closes a P&L-visibility gap
   before real money, additive, no clock cost.
3. **Run the GEX gate test** — the one registered test that is ready; analysis only.
4. **Add the C/H debug counter** — cheap, and turns a "known bug" into a known rate.
5. **Leave B7, B5, C2 alone.** B7 is real but costs the clock for negative-mean
   insurance; B5 has nothing to observe; C2 needs days, not work.

Items 1–4 share a property worth stating: **none of them changes a trading
decision**, so all four can ship inside the 15-day measurement window without
restarting it.
