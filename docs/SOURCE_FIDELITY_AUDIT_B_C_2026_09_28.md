# Source-fidelity audit — B and C (2026-09-28)

**Question asked:** *"is B acting like the original video said? Or did we make our own changes?"*

**Answer: B implements 4 of the source's 10 stated rules. It has never once traded in the
regime the source requires, and its entire live P&L was earned outside it.**

This closes the gap left by [`SOURCE_FIDELITY_AUDIT_2026_09_24.md`](SOURCE_FIDELITY_AUDIT_2026_09_24.md),
which audited D/E/F/G/H and explicitly excluded A/B/C as *"MEIC/Brandon lineage rather than
single-video builds."* B **is** a single-video build; nobody had checked it.

**Source:** Brandon Jones (the repo's spelling is correct — the host says *"Welcome Brandon
Jones"*; secondary write-ups calling him "Brendan Johns" are wrong), interviewed on Theta
Profits, YouTube `uJSi0AvYcr8`. Transcript pulled with `yt-dlp` and committed at
[`sources/BC_brandon_jones_trojan_horse_uJSi0AvYcr8_transcript.txt`](sources/BC_brandon_jones_trojan_horse_uJSi0AvYcr8_transcript.txt)
so it cannot be lost the way D's was.

---

## The source's rules, quoted

| # | Rule | Quote |
|---|---|---|
| 1 | **VIX 19–40** | *"my first criteria is the VIX must be between 19 to 40"* · *"when the VIX is trading lower than 19… you're going to have much less wiggle room"* · *"filtering out hyper volatility"* |
| 2 | **Entry 9:31 a.m.** | *"we are opening this at 9:31 a.m."* |
| 3 | **ONE trade per day** | *"this is a one trade per day strategy"* |
| 4 | **Sell 8δ / buy 7δ, both sides** | *"we are selling the eight delta above and below, and we are buying the seven delta above and below"* |
| 5 | **Width 5–10pt, VIX-scaled** | *"anywhere from five to 10 width"* · *"when the VIX is trading between 24 to 28 maybe 30-ish, we… capitalize off of the 10 [pt] widths"* · *"unless we're trending more towards the 20 spot price on the VIX. Then we'll go with the fives"* |
| 6 | **Take profit 80–85%** | *"we look to capitalize on a minimum of 80 to 85% profit"* |
| 7 | **NO fixed stop — mental, gamma-based** | *"Does this mean you do not have any automatic stop loss…? You have a mental stop loss depending on your total exposures according to this gamma maps. Yep. In 90% of cases that is 100% true"* · *"our stop loss is more or less visualized by moves above or below key levels… it's not actually a fixed spot price"* |
| 8 | **Hedge, by time of day** | *"if the market's starting to go against me, probably later than noon… I'd look to capitalize off of a butterfly"* · *"If it's happening earlier in the day… before 12:31, then… the debit spread"* |
| 9 | **Skip Fed / macro days** | *"any day when the Fed speaks, we usually will take a day off"* |
| 10 | **GEX → SHIFT strikes** | *"I would manipulate the lower bands to move a little bit lower… to be able to have these areas of deceleration captured within"* |

His baseline, unmanaged: *"$22,550"* on $100k over a year (22.5%), 1 contract, ~$100 credit,
*"max loss on paper is about 750."*

---

## Verdict table

| # | Rule | B (live seat) | |
|---|---|---|---|
| 1 | VIX 19–40 | **no floor at all** (`max_vix_entry: 999`) | 🔴 |
| 2 | Entry 9:31 | 09:45 earliest (C: 10:15) | 🔴 |
| 3 | One trade/day | **up to 7** | 🔴 |
| 4 | 8δ/7δ both sides | 8δ delta-target ✓ | ✅ |
| 5 | Width 5–10 VIX-scaled | 5pt <VIX 22, 10pt >22 | ✅ |
| 6 | TP 80–85% | 80% | ✅ |
| 7 | No fixed stop | **fixed A2 40%-of-width** | 🔴 |
| 8 | Hedge by time of day | **both hedges DISABLED** | 🔴 |
| 9 | Skip Fed days | `fomc_announcement_skip: true` | ✅ |
| 10 | GEX shifts strikes | **SKIPs the side instead** | 🔴 |

**4 faithful · 6 divergent.**

---

## 🔴 The finding that reframes everything

**B has traded 0 of 45 live days at VIX ≥ 19.** Max VIX in its entire live record: **18.97**.
Its whole **+$6,476.60 over 32 traded days was earned in a regime the source explicitly
excludes.**

Two consequences, and they cut in opposite directions:

1. **The source provides NO evidence for B.** His 22.5% backtest and the "40% in one quarter"
   headline were produced under a VIX 19–40 filter, one trade per day, at 9:31, with hedges and
   no fixed stop. None of that describes B. **Those numbers must never be cited as validation
   for B's behaviour** — a live seat and a real-money gate would be inheriting a false premise.

2. **Adding the filter would delete the strategy.** Applying VIX ≥ 19 to B retroactively yields
   **zero trades in 45 days**. B is profitable *because* it ignores his entry condition. So
   "make B faithful" is not a coherent instruction; it would stop B trading entirely in the
   current regime.

The honest conclusion is that **B is not Brandon's strategy and should stop being described as
one.** It is HYDRA's iron condor engine using three of his ideas — 8δ strike selection, the GEX
adjuster, and the 80–85% take-profit — on a MEIC-style multi-entry grid he never proposed, with
a mechanical stop he explicitly rejects, in a volatility regime he filters out.

That is not automatically wrong. It may be better. But it is **ours**, and its evidence has to
come from our own record, which is currently **t = 0.83** — not from his video.

---

## Two divergences worth re-examining (pre-registered, NOT during the freeze)

### PR-1 · The GEX adjuster should SHIFT, not SKIP

The only sourced GEX-placement quote is about **shifting** strikes to enclose deceleration
zones. **Nothing in the transcript supports skipping a side** — and the word "one side" never
appears. Our adjuster's own docstring gives the game away:

> *"SKIP - the proposed strike sits inside an acceleration zone; don't place this side at all
> **(HYDRA already supports one-sided entries)**"*

That parenthetical is an implementation convenience, not his rule. The SKIP path is ours.

It has a live cost. On **2026-09-28** the adjuster SKIPped the call side at reference strike
7755 on **both** of B's slots (09:46 and 10:15), and `one_sided_entries_enabled=false` converted
each into a full skip — **B placed zero trades**. The shift machinery already exists
(`max_shift_pts: 25`) and is simply not the path that fires.

Note: `one_sided_entries_enabled=false` is **aligned** with the source, not a divergence from
it. The fix is upstream of it.

### PR-2 · The hedge disable tested OUR hedge, in a regime he excludes

Hedging is the source's primary adverse-move response — *"This strategy is profitable by its
nature, and the way that you optimize it is by understanding how to manage it."* We disabled
both hedges on B on 2026-09-04 after measuring that debits exceeded the losses defended.

That measurement may still be right, but note what it actually tested: **our** trigger rules
(`trigger_distance_pts 15`, `confirm_seconds 10`, an `use_adjuster_gex_gate`) rather than his
(debit spread on a heat-map break before 12:31; butterfly pinning a gamma cluster after 12:30),
in a **VIX < 19 regime he filters out**, where moves are smaller and a hedge has less to defend.

Re-testing needs his rules and, ideally, days at his volatility. Neither is available yet.

---

## What was NOT changed

Nothing. This audit is documentation only. The config freeze
([`GO_LIVE_MASTER.md`](GO_LIVE_MASTER.md) §2-quater) holds, and both items above are
pre-registered for later rather than acted on.
