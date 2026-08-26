# Brandon defensive-overlay hedge — P&L reconstruction and the debit-spread cut (Aug 24–25, 2026)

**Scope:** the defensive-overlay hedge on variants B (LIVE) and C (dry-run-shadow) — the debit-spread (morning) / butterfly (afternoon) structure placed when GEX data threatens a short IC leg. This document exists because the config comments and `bots/hydra/__init__.py` version history for the 2026-08-25 changes cite specific dollar figures and an "audit" that were never otherwise written down anywhere durable — they came from a live VM investigation (log greps + SQLite queries against `data/variant_{b,c}/backtesting.db` and the bot log files under `logs/hydra_variant_{b,c}/`) that is not itself committed to this repo. This is that writeup, so the numbers are checkable later instead of resting on an unlocatable "audit."

**Caveat that applies to everything below:** the VM's bot log files rotate at ~8 days (confirmed directly: `logs/hydra_variant_{b,c}/bot.log.YYYY-MM-DD` only goes back to 2026-08-18 as of this writing). The overlay has been live since 2026-05-05. So anything here sourced from raw log lines is limited to the retained window (2026-08-18 through 2026-08-25); anything from `backtesting.db` covers the DB's full history (not log-rotated), but the database alone cannot separate hedge P&L from IC P&L for dates before 2026-08-25 (there was no hedge-specific table until `bots/hydra/brandon/hedge_recorder.py` was added this same day) — those older figures are inferred from `trade_entries.realized_pnl` exceeding `trade_entries.total_credit`, which is only possible with an external (hedge) contribution, since a plain iron condor can never earn more than 100% of its own credit.

## Part 1 — the "2026-08-24 audit" reference

Earlier the same day this document is dated, a chat-only investigation (not a saved workflow report) reconciled B and C's 2026-08-24 hedge activity directly against `daily_summaries.net_pnl`:

- **B, 2026-08-24**: `daily_summaries.net_pnl = $43.40`. Two settled hedge legs from `logs/hydra_variant_b/bot.log.2026-08-24`:
  ```
  2026-08-24 23:16:00 BRANDON-OVERLAY-SETTLED E#3 call debit_spread: SPX_close=7653.62, debit_paid=$315.00, hedge_pnl=$-315.00
  2026-08-24 23:16:03 BRANDON-OVERLAY-SETTLED E#4 call debit_spread: SPX_close=7653.62, debit_paid=$210.00, hedge_pnl=$-210.00
  ```
  Combined hedge cost: **-$525**. IC-only P&L = 43.40 − (−525) = **+$568.40**. (Framed at the time as "$665 credit − $525 hedge − $96.60 commission = $43.40 net" — consistent with this reconstruction.)
- **C, 2026-08-24**: `daily_summaries.net_pnl = -$1,542.38`. Two settled hedge legs:
  ```
  2026-08-24 23:15:30 BRANDON-OVERLAY-SETTLED E#1 call debit_spread: SPX_close=7653.23, debit_paid=$1150.24, hedge_pnl=$-1150.24
  2026-08-24 23:15:30 BRANDON-OVERLAY-SETTLED E#2 call debit_spread: SPX_close=7653.23, debit_paid=$1150.24, hedge_pnl=$-1150.24
  ```
  Combined hedge cost: **-$2,300.48**. IC-only P&L = -1542.38 − (-2300.48) = **+$758.10**. C's structure was near-identical to B's (same short strike 7680, same 5pt-wide 7685/7690 call debit spread) yet cost **4.4x more** — the direct evidence that motivated the same-day dry-run-pricing fix (C used a Black-Scholes model instead of real broker quotes; B's fills were real).

## Part 2 — full retained-log-window hedge history (2026-08-18 through 2026-08-25)

Every `BRANDON-OVERLAY-SETTLED` line found across every retained log file, both variants (`grep -H "OVERLAY-SETTLED" logs/hydra_variant_{b,c}/bot.log*`), verbatim:

| Date | Variant | Entry | Side | Structure | Debit paid | hedge_pnl |
|---|---|---|---|---|---|---|
| 2026-08-19 | B | E2 | call | debit_spread | $280.00 | **-$280.00** |
| 2026-08-19 | B | E4 | put | debit_spread | $280.00 | **-$280.00** |
| 2026-08-19 | B | E5 | call | debit_spread | $205.00 | **-$205.00** |
| 2026-08-19 | C | E2 | put | debit_spread | $888.17 | **-$888.17** |
| 2026-08-24 | B | E3 | call | debit_spread | $315.00 | **-$315.00** |
| 2026-08-24 | B | E4 | call | debit_spread | $210.00 | **-$210.00** |
| 2026-08-24 | C | E1 | call | debit_spread | $1,150.24 | **-$1,150.24** |
| 2026-08-24 | C | E2 | call | debit_spread | $1,150.24 | **-$1,150.24** |

**Zero positive `hedge_pnl` anywhere in this window, on either variant.** Every settled event found was `debit_spread` structure (morning). No `BRANDON-OVERLAY-SETTLED` line for a `butterfly` structure appears anywhere in the retained window — see Part 4 for the one butterfly placement that *was* found (and never settled).

Day-level reconciliation for B (real fills, not dry-run-modeled) via `daily_summaries.net_pnl`:

| Date | B net_pnl (reported) | Hedge cost that day | IC-only P&L (net_pnl − hedge) |
|---|---|---|---|
| 2026-08-19 | -$611.00 | -$765.00 (3 legs above) | **+$154.00** |
| 2026-08-24 | +$43.40 | -$525.00 | **+$568.40** |

Both days, B's underlying iron-condor trading was profitable on its own; the hedge cost turned Aug 19 from a +$154 day into a -$611 day, and reduced Aug 24 from +$568.40 to +$43.40.

## Part 3 — B's full-history hedge-win search (not log-limited)

Query: `SELECT date, entry_number, total_credit, realized_pnl FROM trade_entries WHERE realized_pnl > total_credit` against `data/variant_b/backtesting.db` (full history, not log-rotated). A plain IC's `realized_pnl` can never exceed `total_credit` — any excess is mathematically attributable to an external booking (a settled hedge, per `_brandon_settle_hedges`'s `_book_realized_pnl(s.total_pnl, entry)`).

Two genuine anomalies found (excluding rows where the "excess" was $0.00, i.e. floating-point noise on a plain full-credit expiry):

- **2026-07-21**: entries #5 and #6 show `realized_pnl` of **$6,228.83** and **$6,353.82** against credits of only $375.00 and $500.00 — excess of **~$5,854 each (~$11,708 combined)**. `daily_summaries` for that date: `spx_close=7507.44`, and the entries' hedge was a butterfly pinned at 7510 (per `bots/hydra/brandon/strategy.py`'s own test file `tests/test_brandon_overlay_fill_spread_2026_07_21.py`, which independently documents "B's real 07-21 butterfly (LC 7500x10 / SC 7510x20 / LC 7520x10)"). **SPX closed 2.56 points from the pin** — a butterfly's payoff is maximized exactly at the pin strike, so this is a real, thesis-confirming outcome, not a pricing artifact in direction (though the exact magnitude is likely somewhat inflated: this predates the 2026-07-21 same-day dry-run-pricing partial-fix, and B was still dry-run at this point — it did not go live until 2026-07-24, so **this was never real money**).
- **2026-08-14**: entry #2 shows `realized_pnl` of **$2,471.00** against a credit of $175.00 — excess of **~$2,296**. Structure type not independently confirmed (outside the retained log window); B was live by this date, so if hedge-attributable, this would have been real.

No other `realized_pnl > total_credit` anomaly was found in B's full history as of this writing (excluding the two known Aug 19/Aug 24 events already covered by log lines in Part 2, whose entries individually stay below their own credit once the day's IC-only component is netted out — those don't independently appear in this query in a way distinguishable from ordinary IC results).

## Part 4 — the Aug-19 unresolved butterfly (resolved)

`logs/hydra_variant_b/bot.log.2026-08-19` shows a fourth placement that day with no matching settlement:
```
2026-08-19 12:50:54 BRANDON-OVERLAY E#5 put: placing butterfly — afternoon hedge: put butterfly pin=7550 width=±10pt — SPX 25pt from short. Legs: LP 7540×7, SP 7550×14, LP 7560×7
```
Trace of the fill sequence (same log file, 12:51:00–12:53:05): all three legs filled completely (LP 7540 7/7, SP 7550 14/14 after one escalation retry, LP 7560 7/7) — no partial fill, no unwind, no CRITICAL alert. A real, fully-filled position. Yet `trade_entries` for E#5 that day shows `realized_pnl = -$100.00` — far too small to include this butterfly's cost, and the day's `daily_summaries.net_pnl` (-$611.00) already reconciles exactly against the *other three* known hedges (Part 2) without it.

**Root cause**: `bots/hydra/__init__.py`'s own 2026-08-20 version-history entry documents this exact bug — settling multiple same-day hedges on one entry collapsed them into a single `HedgeSettlement` (deriving `structure`/`threatened_side` from `legs[0]` alone while still summing `total_pnl` across every leg), so "only 3 BRANDON-OVERLAY-SETTLED lines for 4 real placements on 2026-08-19" — already fixed the next day (`bots/hydra/brandon/strategy.py`'s `_brandon_settle_hedges`, the `group_settlements` per-`placed_at` grouping). Not a live issue.

**Real financial size**: SPX closed at 7707.49 that day — 157 points from the 7550 pin, so every leg of the butterfly expired worthless. Net cost = (2 long legs × $0.10 fill − 1 short leg × 14 × $0.08 fill) × 100 = **-$28.00**. Immaterial regardless of the settlement-tracking gap.

## Decision (2026-08-25)

Given Part 2's clean, fully-real (B) losing record for the morning debit-spread structure with zero offsetting wins found anywhere, and Part 3's evidence that the butterfly's one large, clearly-attributable outcome was a genuine win (a real 2.56pt pin), `bots/hydra/brandon/defensive_overlay.py`'s `OverlayConfig` gained independent `debit_spread_enabled`/`butterfly_enabled` switches. `debit_spread_enabled: false` is now live on both B and C; `butterfly_enabled` is unchanged (`true`). See `bots/hydra/__init__.py` version history for the code-level detail and `config_variant_{b,c}.json`'s `_comment_debit_spread_disabled` for the config-level pointer back to this document.

**What would change this decision**: real evidence the fixed-pricing, fixed-locality-gate hedge system (the earlier 2026-08-25 change, active on C as a staged trial) performs meaningfully differently from what's documented here — or a larger, cleaner sample of butterfly outcomes (win or loss) than the single Jul 21 event and the single Aug 19 non-event this document was able to find.
