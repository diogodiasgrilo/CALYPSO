# Source-fidelity audit — D, E, F, G, H (2026-09-24)

**Question asked:** *"are you 100% sure that all the strategies are as close as possible to their
videos?"* — excluding A/B/C, which are the MEIC/Brandon lineage rather than single-video builds.

**Answer: no, and one variant cannot be.** Full findings below.

---

## Why the previous audit did not settle this

The inherited-gate audit of 2026-09-23 asked exactly one question — *did this strategy inherit a
gate its source never specified?* — and gave D, E, F and G a clean bill. It never asked two others
that both turned out to matter:

| Question it never asked | What it would have caught |
|---|---|
| Does it compute the **quantity** the source means? | **F** derived its boundary from a de-annualised 30-day VIX where Ghauri means the ATM straddle — a ~3× different distance. Found 2026-09-23 by a different question. |
| Does it trade the **instrument** the source trades? | **H** ran on SPX where Tompkins trades SPY, which is how its sizing rule came to permit zero contracts. Found 2026-09-24 by a different question. |

Two of five failed dimensions that audit never looked at. A pass from it is weak evidence, and this
audit therefore uses a **widened lens**: instrument · core quantity · proxy filters ·
instrument-dependent constants · whose parameters they are.

---

## Verdicts

| Variant | Source | Verdict |
|---|---|---|
| **D** — DC Time Machine | Steve Burnich, YouTube `JtGW1wNFNIY` | ✅ **Faithful.** Every checkable parameter matches. |
| **E** — SPY Double Calendar | OptionsKit, YouTube `GuI-hH_jhlg` | 🔴 **Cannot be faithful — the video does not contain the strategy.** |
| **F** — Ghauri Mean Reversion | Ghauri via Theta Profits | ✅ Faithful since the 2026-09-23 expected-move fix. One tolerance wider than stated. |
| **G** — Strangle | **none** | ➖ Exempt. Built as the modularity-audit driver; there is no source to diverge from. |
| **H** — Long Strangle | Tompkins via Theta Profits | ⚠️ Underlying fixed 2026-09-24. IV-percentile window gap remains. |

---

## D — faithful, and the earlier suspicion was wrong

**Its source spec had been lost.** The docstring pointed at `/tmp/strategy_D_dc_time_machine.md` —
an ephemeral path, long gone — so for a strategy carrying a NO-GO verdict and a −$6,129 lifetime
record, nothing in the repo recorded what it was *supposed* to do. The transcript has been
re-pulled with `yt-dlp` and is now committed at
[`docs/sources/D_dc_time_machine_JtGW1wNFNIY_transcript.txt`](sources/D_dc_time_machine_JtGW1wNFNIY_transcript.txt)
so this cannot recur.

Checked against it, D matches on every parameter that can be checked:

| Rule | Source (quoted) | D |
|---|---|---|
| Underlying | *"I almost exclusively do this trade on SPX"* — European, cash-settled, *"no assignment because there are no shares of SPX"* | SPX ✅ |
| Strikes | *"anywhere from the 30 to 40-ish delta"* | `delta_band [0.30, 0.40]` ✅ |
| Front DTE | *"6 days out, 7 days out, 10, 12, 15 days out"* | `short_dte 6–15` ✅ |
| Front/back gap | His own demo: *"9 days in the front and 10 days in the back"* = 1 day | `long_extra_dte 1–4` ✅ |
| Transform trigger | *"once it has a little bit of profit, typically **5 to 10%**"* | `profit_trigger_pct 0.075` ✅ midpoint |
| Give-up point | *"if I see a loss that gets to **20%** on the double calendar, I will typically pull the plug"* | `pre_transform_stop_pct 0.20` ✅ |
| Untransformed at EOD | *"do I want to hold it overnight… or just close it out"* — a decision, not a forced close | `eod_close_if_no_transform: false` ✅ |
| Entry | *"I put this on in the morning"* | `entry_time_et 10:00` ✅ |
| After transform | *"Ideally, you would let them expire"* | held to expiry ✅ |

**Two nuances worth recording, neither a defect:**

1. He warns against a *hard broker stop*: *"I do not use a stop loss on a double calendar spread…
   a hard stop loss with your broker… is typically not going to end well."* He uses a **mental**
   20% stop. D's automated check is an evaluated stop rather than a resting order, which is the
   mental-stop equivalent — but it is worth not "improving" it into a broker-resting stop later.
2. He **scales out partially** (*"if I did 20 contracts… I might close 10"*). D is all-or-nothing.
   A real difference, small, and only meaningful at size.

---

## E — the video withholds the strategy E implements 🔴

> **CORRECTION (same day, after reading the transcript line by line).** The first
> version of this section overstated the gap. E's *parameters* largely DO come
> from the video: `short_dte_target: 35` is his *"35 days away"*, `long_gap_days:
> 7` is his *"1 week away from the short date"*, `time_exit_days_before: 2`
> honours his *"you always want to make sure that you exit your trade before the
> expiration"*, and SPY and the structure are his. What is withheld is narrower
> than "the strategy": the **strike distance** and the **exit ladder** for the
> double. Those two are ours. The finding below stands; its scope was wrong.

This is the audit's most consequential finding, and it is not a bug that can be fixed by editing
a config value.

The source explains **single** calendars in detail — construction, theta differential, the low-IV
entry condition, the assignment risk — and then, at the point where the double calendar begins:

> *"instead of just buying a call calendar here and a put calendar here, how about we combine both
> in a single trade and turn it into a strategy called the double calendar. **This is my ultimate
> strategy.** With this strategy, I have well over 80% win rate… **If you want to learn this
> strategy in depth, you can learn it in my coaching program.**"*

That is the whole treatment. **No strikes, no DTEs, no entry condition, no exit rule, no sizing.**
The video is an advertisement for a paid program at precisely the point where E's rules would have
come from.

**Consequences, stated plainly:**

* **Every specific rule in E is ours**, inferred from the single-calendar explanation plus general
  calendar principles. That is not necessarily *wrong*, but it is not "built from a video" and the
  code and docs should stop implying it is.
* **E's own docstring records the marketing claim as a property**: *"the creator's 'ultimate
  strategy' (>80% win rate)"*. That number describes a coaching program's pitch, not anything
  measured, and it sits in the module docstring where it reads as a spec.
* **"Make E match the video" is not an achievable instruction.** The honest options are to treat E
  as an *original* strategy that must justify itself on its own measured record (currently
  −$109 lifetime, i.e. near-flat), or to retire it.

**Separately — the one rule the source DOES state, E implements as a weaker proxy.** The source:
*"it is best to enter a trade like this when the implied volatility is at the **lower end of the
spectrum** and we expect it to go up during our trade duration."* That is a **relative** measure.
E implements `max_vix_entry: 22.0` — an **absolute** threshold, which means something different
every season: VIX 18 is the ~90th percentile in a calm year and the ~20th in a volatile one. The
percentile machinery built for H (`iv_percentile_with_n`, with a sample floor) would express this
correctly. Note the caveat that this rule is stated for single calendars.

**And D and E disagree on it.** E gates at VIX ≤ 22; D has **no low-IV gate at all**. They are both
positive-vega double calendars and they exist to be compared, so part of any D-vs-E difference is
currently the gate rather than the strategies — the same class of problem as the slippage mismatch
that already invalidated one D-vs-E comparison.

---

## F — faithful after the expected-move fix

Against the rules recorded in `STRATEGY_CANDIDATES.md` from the article + full transcript:

| Rule | Source | F |
|---|---|---|
| Boundary | the options market's expected move | `expected_move_source: straddle` ✅ (fixed 2026-09-23) |
| Confirmation | *no* confirmation wait | fires on touch ✅ |
| Short strike | 10–25Δ | `target_delta_pct 0.15` ✅ |
| Width | 5–20pt | `width_pt 10.0` ✅ |
| Targets | 50% profit / 100% stop | `0.50` / `1.00` ✅ |
| Cutoff | ~intraday | `13:00 ET` ✅ |

⚠️ **One tolerance is wider than the source states:** `delta_band [0.05, 0.35]` admits strikes from
5Δ to 35Δ around the 15Δ target, where the source says 10–25Δ. In a thin chain F can therefore
select a strike the source would not. Tightening to `[0.10, 0.25]` would match; left as-is it is a
documented widening rather than a hidden one.

---

## G — exempt

`strangle_strategy.py` line 1: *"a 0DTE SPX short strangle (**modularity-audit driver**)"*. There is
no source video, so there is nothing to be faithful to. Its inherited gating is a *stated* choice
recorded in its config, which the 2026-09-23 audit already pinned.

---

## Open items — ALL CLOSED (2026-09-24)

| # | Item | Outcome |
|---|---|---|
| 1 | E's provenance in code + docs; the ">80% win rate" claim | **Done.** Claim removed; what is his and what is ours stated in the docstring AND the config. |
| 2 | E's low-IV gate → percentile with a sample floor | **Done.** Shares H's machinery; fails closed. |
| 3 | D-vs-E gate asymmetry | **Resolved as deliberate.** Burnich specifies no IV condition, so gating D would make it unfaithful. Documented, not accidental. |
| 4 | F's `delta_band` → `[0.10, 0.25]` | **Done.** |
| 5 | H's IV window → a real 252 days | **Done.** `scripts/backfill_vix_history.py`; live window now 13.47–31.05. |
| 6 | D's partial scale-out | **Done.** `_dc_eod_partial_scale_out` — close half of an untransformed, profitable calendar at the close, carry the rest. D sized 1c→2c so the rule can exist. |

### Found by the closing sweep

**The calm-entry filter (MKT-043) was a live-looking dead key on D and E.** Both configs carried
`calm_entry_threshold_pts: 15.0`, but MKT-043 is applied inside `HydraStrategy._initiate_entry`
and **both variants override that method in full**, so it never ran. Same shape as the whipsaw and
FOMC keys the 2026-09-23 audit found, and the same reasoning: a premium-selling concept on a
positive-vega calendar, mentioned by neither source — Burnich says only *"I put this on in the
morning"*, and the OptionsKit video gives no timing condition at all. Now nulled with an
`_comment_calm_entry_INERT` marker, so nobody re-adds it believing the absence was an oversight.

**A stale comment** at E's gate call site still described the retired absolute-VIX proxy as "a
go-live refinement". Corrected.

### Standing verdicts

| Variant | Verdict |
|---|---|
| **D** | ✅ Faithful on every rule the video states, including the EOD half-close. |
| **E** | ✅ Faithful on every rule the video states; the strike distance and exit ladder are OURS and labelled so, because the video withholds them. |
| **F** | ✅ Faithful. |
| **G** | ➖ No source. Its inherited block is a stated choice in its config. |
| **H** | ✅ Faithful on instrument, structure, sizing, target and stops. The IV filter is a VIX proxy because IBKR exposes no per-option IV at all — the one gap that cannot be closed from this repo. |

| # | Item | Needs |
|---|---|---|
| 1 | E's provenance corrected in code + docs; the ">80% win rate" marketing claim removed from the docstring | a decision on whether E continues as an original strategy |
| 2 | E's low-IV gate → percentile with a sample floor (H's machinery) | small |
| 3 | D-vs-E gate asymmetry — either both gate or neither | a decision |
| 4 | F's `delta_band` → `[0.10, 0.25]` to match the stated 10–25Δ | one config value |
| 5 | H's IV window — backfill VIX to 252 days from Yahoo | small |
| 6 | D's partial scale-out | low value while dry-run |
