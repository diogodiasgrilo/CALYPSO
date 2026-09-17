# Dashboard rebuild — make every strategy render its own truth

**Status: PLAN, 2026-09-17.** Supersedes the narrower
[`DASHBOARD_VARIANT_AUDIT_2026_09_17.md`](DASHBOARD_VARIANT_AUDIT_2026_09_17.md), which found 3 of
the 8 defects below.

**The brief:** fix everything, exhaustively, and change how a strategy is *displayed* where the
current view does not fit what the strategy actually is. No time pressure.

---

## 1. The finding that reframes all the others

The frontend **already has a shape-aware architecture**. `lib/pnlShape.ts` defines, per shape, the
`axisLabel`, the `capitalLabel`, and explicitly *"whether credit/buffer/spread fields are meaningful
for this shape"*. `data_kind` already dispatches which body renderer to use.

So this is not a dashboard that was built one-strategy-deep. It is a **correctly designed system with
one missing axis**:

> `pnl_shape` conflates **how P&L is earned** (credit vs debit) with **what capital means**
> (defined-risk width vs net debit vs broker margin).

Today only `credit` and `debit` exist. G is a naked short strangle — it earns a **credit**, so it is
tagged `credit` and inherits the iron-condor renderer *and the iron-condor capital model*. That single
conflation is the root of the "G is missing return-on-capital" symptom, and of the broader complaint
that the view does not fit the strategy.

**The fix is to add the missing axis, not to special-case G.**

---

## 2. Every defect found (8)

| # | Defect | Root cause | Who it affects |
|---|---|---|---|
| **D1** | `total_trades` is permanently **0** | `base_strategy.py:5899` initialises it; nothing ever increments it | **All 7**, incl. B |
| **D2** | G has **zero** `daily_returns` rows → no ROC, no avg capital/day | `_calculate_capital_deployed`: `if entry.spread_width <= 0: continue`; a naked strangle has no width | G, and any future undefined-risk strategy |
| **D3** | G's **Sortino is meaningless**, not merely missing | `_calculate_sortino_ratio` averages `return_pct` over `daily_returns` — which is empty | G |
| **D4** | Only B shows "previous day" | `/api/hydra/summary` + `/api/metrics/cumulative` take **no `strategy_id`** — canonical/live-seat only | Every non-live variant |
| **D5** | Main-page live state is live-seat-only | `/api/hydra/state`, `/api/hydra/bot-config`, `/api/metrics/range` unscoped | Every non-live variant |
| **D6** | **E's calendar page cannot show E** | `routers/dc.py` hardcodes `settings.variant_d_state_file` / `variant_d_baseline_date` | E |
| **D7** | F renders call **and** put fields it never has | F is `structure_family='iron_condor'` but places **one-sided** verticals; its entry stored a phantom `SC=0.0` | F |
| **D8** | Pre-market shows empty charts for everyone | The snapshot has **no previous-day concept for any variant** — it returns the freshly-reset day | All (masked on B by D4/D5) |

**Scale check:** 26 of 37 endpoints are unscoped. Most are legitimately global (auth, agent reports,
market status, group endpoints that take `group_id`). The ones above are the ones that lie.

---

## 3. Design decision — introduce `capital_basis`

Add to `StrategyMeta`, independent of `pnl_shape`:

| `capital_basis` | Meaning | Capital = | Variants |
|---|---|---|---|
| `defined_risk` | Loss capped by spread width | `width × 100 × contracts` | A, B, C, F |
| `net_debit` | Capital is the debit paid | `sum(open net_debit)` | D, E |
| `broker_margin` | **Undefined risk** — no structural cap | broker margin requirement | **G** |

And a second field, `sides`, so a renderer stops assuming both wings exist:

| `sides` | Variants |
|---|---|
| `two_sided` | A, B, C, G (strangle still has two shorts) |
| `one_sided` | F |

These are **taxonomy facts**, so `shared/strategy_taxonomy.py` stays the single source of truth and
both backend and frontend derive from it — no per-variant branching anywhere else.

> **Why a new field rather than a new `pnl_shape` value:** `pnl_shape` drives the **comparison axis**
> (you may never plot a credit and a debit strategy on one P&L axis). G *is* a credit strategy and
> belongs on that axis. What differs is its capital denominator. Overloading `pnl_shape` would
> silently make G non-comparable to A/B/C on P&L, which would be wrong.

---

## 4. What G's view must show instead

Return-on-capital against spread width is not "missing" for G — **it is undefined**. A naked strangle
has no denominator of that kind. So the card set changes:

| Replace | With | Why |
|---|---|---|
| Return on capital (% of width) | **Return on margin** (% of broker requirement) | The only real capital G ties up |
| Avg capital per day | **Peak margin per day** | Same reason |
| Max loss (defined) | **Max loss: UNBOUNDED** + distance-to-short in points/σ | Stating a number here would be a lie |
| — | **Tail exposure**: worst adverse excursion, σ-move to breach | The risk that actually characterises G |

This is the part of the brief about the view not fitting the strategy. G should look *different*,
because it **is** different — and a dashboard that renders it identically to an iron condor is
misinforming the reader, which is worse than leaving a card blank.

Equivalent, smaller treatments: **F** hides the side it never trades; **D/E** already have their own
`dc_calendar` renderer and only need D6 fixed.

---

## 5. Phases — each independently shippable, testable, revertible

### Phase 0 — the harness that proves the bugs (do first)
A per-variant **contract test**: for all 7, assert the snapshot's declared `data_kind` /
`capital_basis` matches the fields actually populated, and that no metric is silently zero when its
inputs are absent. **It must FAIL on today's code**, on D1/D2/D4/D6/D7. Without this, "fixed" is an
opinion.
*Exit:* a red test naming each defect.

### Phase 1 — scope the lying endpoints (D4, D5)
Add `strategy_id` to `/api/hydra/summary`, `/api/metrics/cumulative`, `/api/hydra/state`,
`/api/hydra/bot-config`, `/api/metrics/range`, defaulting to the live seat so existing callers are
unchanged. Mirror the July `4b3d6a0` pattern exactly — **that fix was partial, and this completes
it.**
*Risk:* low, backend-only. *Exit:* picking any variant changes every card.

### Phase 2 — taxonomy gains `capital_basis` + `sides` (enables D2, D7)
Add the fields, populate all 7, expose via `/api/strategies/meta`. No behaviour change yet.
*Exit:* meta endpoint returns the new fields; taxonomy tests pin every variant's values.

### Phase 3 — capital model per basis (D2, D3)
Dispatch `_calculate_capital_deployed` on `capital_basis`. Implement `broker_margin` for G using the
margin snapshot the strategy already records. Backfill G's `daily_returns` from history where margin
is recoverable; where it is not, **leave it absent and label it** rather than invent a denominator.
*Risk:* touches `base_strategy`, which every bot loads ⇒ full suite + mutation tests + a dry-run
variant restart first, never the live seat first.
*Exit:* G has `daily_returns`; its Sortino is either real or explicitly `null`, never 0.0.

### Phase 4 — per-shape view models (D7 + G's card set)
Frontend renders from `capital_basis`/`sides`. G gets margin/tail cards; F hides its absent side.
*Exit:* a visual diff per variant; no card shows a number whose denominator does not exist.

### Phase 5 — previous-day fallback (D8)
Give the snapshot an explicit `previous_session` block so a pre-market view shows the last completed
session for **every** variant, clearly labelled as such — not silently.
*Exit:* all 7 render identically-structured pre-market.

### Phase 6 — E's calendar view (D6)
Scope `routers/dc.py` by `strategy_id` via `variant_readers`, removing the `variant_d_*` hardcodes.
*Exit:* `/dc` renders D and E from their own DBs.

### Phase 7 — kill the dead field (D1)
Remove `total_trades` or populate it from `total_entries`. **Prefer removal** — a field that has read
0 forever is a trap.
*Exit:* no consumer references it; the suite pins its absence.

---

## 6. Ordering rationale

Phase 0 first because without a failing test we cannot prove any of this is fixed. Then Phases 1 and
7 (highest visible impact, lowest risk, backend-only). Phase 2 unlocks 3 and 4, which are the real
work. Phases 5 and 6 are independent and can land any time after 1.

**Nothing here touches trading.** The dashboard is a separate systemd service; restarting it cannot
affect the bots, the live seat, or the Gate-4 clean-session streak. Phase 3 is the single exception —
it edits `base_strategy.py`, which the bots do load, so it ships to a dry-run variant first.

---

## 8. STATUS 2026-09-17 — what is actually done, and what is not

### Done: the 8 data-layer defects (D1–D8)

All closed, all deployed, all verified on live data. The Phase 0 contract file
has **zero xfails**; every former defect is now a permanent guard rail. Suite
3732 passed.

### NOT done, and I overstated one of them

**`sides` is declared but WIRED NOWHERE.** Phase 2 added it to the taxonomy and
Phase 4 shipped it via `/api/strategies/meta`, but no component consumes it.
I described Phase 4 as hiding F's absent side; **it does not**. F still renders a
phantom call leg wherever entries are drawn.

Measured:

```
components consulting capital_basis :  2 of 36   (DailyPnLCard, MarketContextBanner)
components consulting sides         :  0 of 36
components carrying IC-shape assumptions : 7+   (EntryCard alone: 19 field refs)
```

**34 of 36 components have never had the shape-aware lens applied.** The 8
defects were found by auditing the BACKEND (endpoints, taxonomy, snapshot). The
same audit has not been run on the component layer, so the honest expectation is
that more of this class exists there.

### Remaining phases

| | Work | Why |
|---|---|---|
| **8** | Wire `sides` — hide the side a one-sided strategy never trades | Declared, shipped, unused. F renders a leg it does not have. |
| **9** | Component-level shape audit — the other 34 | Same lens, layer never checked. `EntryCard`, `icEntryView`, `PositionHeatmap`, `DayDetailEntries`, `SessionReplay`, `Analytics`, `Comparison`. |
| **10** | G's tail cards | The plan promised "max loss: UNBOUNDED" + distance-to-short. `boundedLoss:false` exists in config; no card renders it yet. |
| **11** | Visual / UX pass | **Not started, and not assessable from here — see below.** |

### On "beautiful, intuitive, Apple-like" — the honest limit

**I cannot see the dashboard.** Everything above was established by reading code,
querying APIs and diffing payloads. I have never rendered a page, and I have no
screenshot. So I can audit *structural* qualities — design-token consistency,
spacing scales, hardcoded colours, duplicated layout primitives, accessibility
attributes, responsive breakpoints — but I cannot judge whether it *looks* good,
and any claim that it does would be unfounded.

Two honest routes, and they compose:

1. **Structural audit I can do now** — enumerate every hardcoded colour/spacing
   value that bypasses the design tokens, every one-off layout, every
   inconsistent label. That reliably finds the *incoherence* that reads as
   "unpolished", without me seeing anything.
2. **You look, I fix** — screenshots or a list of what feels wrong, which is the
   only way to get taste-level judgement into this.

Route 1 is real work with a real output and should happen regardless. Route 2 is
the only path to "Apple-like", because that is a judgement about how it *feels*,
and I do not have access to that signal.
