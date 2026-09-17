# Dashboard visual audit — every surface, every strategy

**Status: FINDINGS, 2026-09-17.** Method: a Playwright harness renders all **42 surfaces**
(7 strategies × 6 routes) against **105 API payloads captured from the production VM**, screenshots
each, and collects console errors. Harness lives in `dashboard/frontend/uiaudit/`.

This closes the honest limit recorded in [`DASHBOARD_REBUILD_PLAN.md`](DASHBOARD_REBUILD_PLAN.md) §8
("I cannot see the dashboard"). I can now see it.

---

## 1. What the harness is, and why it is built this way

The dashboard is behind bcrypt + TOTP, so a browser cannot simply log in. Instead:

1. **Capture** — call the FastAPI router handlers *directly on the VM*, dumping each response to
   JSON. Real production data, no auth, no mutation. 105 fixtures.
2. **Replay** — a local Node server serves the built frontend plus those fixtures, keyed by
   `?strategy_id=`, and **replays the real WebSocket broadcast payload** on `/ws/dashboard`.
3. **Render** — Playwright visits every route for every strategy, seeds the picker via
   `localStorage['calypso-selected-strategy']`, screenshots full-page, and records console errors.

**The WebSocket replay is not optional.** The primary strategy renders from the live WS, not from
`/snapshot` (`StrategyDashboard.tsx:57`). Without a WS server the live seat renders an empty shell —
so the seat that matters most is exactly the one that cannot be audited without it.

### Two harness defects I hit first, worth recording

- **Wrong `localStorage` key.** I guessed `hydra.selectedStrategyId`; the real key is
  `calypso-selected-strategy` (`store/hydraStore.ts:232`). With the wrong key every strategy rendered
  the default and all 7 produced *identical* output — the variant dimension was silently untested
  while appearing to pass. Caught only because identical `textLen` across 7 strategies is implausible.
- **`/dc` and `/comparison` are redirects, not pages.** Auditing them measured a redirect stub.

Both are the same failure mode this audit exists to find: **a check that appears to pass because it
never actually ran.**

---

## 2. Correctness result: clean

```
42 surfaces rendered · 0 console errors · 0 page errors · 0 failed requests
0 NaN · 0 "undefined" · 0 Infinity · 0 blank pages · 0 horizontal overflow · 0 clipped text
```

The D1–D8 fixes hold up under real rendering. No runtime defect survived. **The defects below are
not crashes — they are the dashboard confidently displaying something wrong or meaningless**, which
is the harder and more damaging class.

---

## 3. THE finding — why only the live seat shows "previous day"

This is the user's original complaint, and it is **still not fixed**. D4/D5 scoped the *endpoints*;
the *components that display previous-day are rendered for the primary only*.

`IronCondorDashboard.tsx:256` splits into two view implementations:

```
if (source === "ws") return <PrimaryICView />;     // the live seat
return <PolledICView body={...} accent={...} />;   // the other six
```

Measured component sets:

| | components |
|---|---|
| **Only `PrimaryICView`** | `MarketContextBanner`, `OffDaySummaryCards`, `FOMCBanner`, `LiveLogFeed` |
| **Only `PolledICView`** | `BufferBar` |
| Shared | `SPXChart`, `PnLCurve`, `EntryGrid`, `EntryTimeline`, `DailyPnLCard`, `PerformanceMetrics`, `PositionHeatmap`, `AgentStatusPanel` |

`OffDaySummaryCards` and `MarketContextBanner` **are** the "LAST TRADING DAY" / "WEEK IN REVIEW"
cards. Each is rendered in exactly one place in the entire codebase — inside `PrimaryICView`.

> So the answer to *"shouldn't it be a standard model that all of them reuse?"* is:
> **it is one model with a hole in it.** Both paths render the same `IronCondorDashboard` and share 8
> of 13 components. But four components — including both previous-day cards — are wired to the
> primary branch only. The other six strategies cannot show a previous day because nothing on their
> code path draws one.

**Fix:** move the four primary-only components into the shared trunk and feed them from the snapshot
(`previous_session` already exists in the payload for all 7 — Phase 5 shipped the data, not the
view). `LiveLogFeed` is legitimately live-only and should stay branch-scoped.

---

## 4. New defects (D9–D19)

### D9 — F and G are silently dropped from every group comparison · **HIGH**

```python
# dashboard/backend/routers/variants.py:61
_VARIANT_IDS: list[str] = ["a", "b", "c"]
```

`get_group_comparison` resolves members from the taxonomy, then delegates to
`variants_router.build_comparison`, which filters them through `_state_readers` — built from that
hardcoded list (`variants.py:793`). Measured:

| group | `member_ids` (taxonomy) | actually rendered |
|---|---|---|
| `ic_0dte` | `[a, b, c, f]` | `[A, B, C]` — **F dropped** |
| `undefined_risk_0dte` | `[g]` | `[]` — **completely empty** |
| `calendar_multiday` | `[d, e]` | `[D, E]` ✅ (different code path) |

Visible result: the frontend draws an **F header from `member_ids`** but has no data for it, so F's
column collapses to zero width and its `—` values merge into C's numbers — the config table literally
reads `75—`, `7—`, `2.5—`. G's group page is 18 config rows of nothing (13% em-dash density).

This is the exact hardcoded-`_VARIANT_IDS` pattern the taxonomy was introduced to retire.

### D10 — The ratio gate counts calendar days, not traded days · **MEDIUM** (latent, not live)

`PerformanceMetrics.tsx:145` gates Sharpe/Sortino/Calmar on `n >= 20`, where `n` was the **row
count** of `daily_pnls` — which includes zero-P&L days the strategy held nothing:

| var | rows | traded | Sharpe(all rows) | Sharpe(traded only) | gate now → fixed |
|---|---|---|---|---|---|
| a | 147 | 131 | −0.67 | −0.71 | PASS → PASS |
| b | 94 | 73 | 2.01 | 2.29 | PASS → PASS |
| c | 68 | 41 | −2.54 | −3.29 | PASS → PASS |
| **d** | 48 | 14 | −3.82 | −7.45 | **PASS → hold** |
| **e** | 52 | **5** | −3.03 | −10.94 | **PASS → hold** |
| f | 15 | 2 | 3.75 | 9.62 | hold → hold |
| g | 15 | 13 | 2.32 | 2.48 | hold → hold |

**Two corrections to my first write-up of this, both found by actually computing it:**

1. **Direction.** I claimed zero-padding "crushes the standard deviation, so the ratio is inflated."
   That is **wrong**. Padding shrinks the mean by `k = traded/rows` and the deviation by roughly
   `√k`, so the ratio is **compressed toward zero** — it understates good and bad performance alike
   (E: −3.03 padded vs −10.94 traded). The defect is the sample size clearing the gate, not an
   exaggerated number.

2. **Scope.** D and E **never rendered these cards at all** — `PerformanceMetrics` is rendered only
   by `IronCondorDashboard`; the calendar variants use `CalendarDashboard`, which has no ratio
   cards. So the user-visible impact today is **zero**, and I should not have rated this HIGH.

It is still worth fixing, and the save is dated: **F sits at 15 rows / 2 traded and gains a row every
trading day.** Under the old rule, in roughly a week F would have crossed 20 rows and published an
annualised Sharpe derived from two or three real trades.

The computation deliberately still runs over all rows — correct for the `√252` annualisation, where
an idle day is a real 0% return on allocated capital. Only the sufficiency test changed.

### D11 — Analytics and History are entirely shape-blind · **HIGH**

```
pages/Analytics.tsx          1,334 lines — 0 references to data_kind/pnl_shape/capital_basis/sides
pages/History.tsx              149 lines — 0
components/pnl/PerformanceMetrics.tsx  185 lines — 0
```

Confirmed visually: **E (a multi-day net-debit SPY calendar) renders the 0DTE iron-condor analytics
suite** — "Rolling win rate (10-day)" flat at 0%, "Avg P&L by day of week" for a strategy that holds
across days, and a distribution dominated by its 47 zero-padding days.

Codebase-wide taxonomy consumption:

```
data_kind      3 of 36 components
pnl_shape      4 of 36
capital_basis  2 of 36   (DailyPnLCard, MarketContextBanner)
sides          0 of 36   (all 8 grep hits are incidental prose in comments)
```

This is the component-layer half of the original complaint, and it is Phases 8–9 of the rebuild plan.

### D12 — Missing VIX rendered as `0.0` · MEDIUM

Market-holiday rows (Labor Day 09-07, 07-03, Juneteenth 06-19, Memorial Day 05-25, Good Friday
04-03) carry `daily_summaries` rows with falsy VIX and stale carried-forward SPX. Displayed as a
literal `0.0` — an impossible VIX — rather than `—`.

Counts: a 5/148 · b 4/94 · c 4/91 · **d 23/66** · **e 43/67** · f 1/15 · g 1/15.

D and E's counts are far beyond the holiday set: those variants simply do not record `vix_open` most
days.

### D13 — "Entries 0" on days with P&L and stops · MEDIUM

d 6/66 · e 4/67 · f 2/15. For **D/E this is semantically correct** — a multi-day calendar books P&L
on a carry day with no new entry — but the IC-shaped "Entries" column makes a correct row read as
broken. For **F** it is a real gap (entries predate the `_record_entry_to_db` fix of 2026-09-15).

### D14 — Header does not rebind on group routes · MEDIUM

On `/comparison/undefined_risk_0dte` the header still reads *"B · LIVE (BRANDON NARROW, 7-SLOT GRID)
— shows Brandon Narrow (7-slot)"* while the page below is G's group.

### D15 — Two sources of truth for the palette · MEDIUM (drift hazard)

`lib/tradingColors.ts` re-declares `#1a2229`, `#7ee8c7`, `#f85149` as JS literals; the same values
are `--color-bg`, `--color-profit`, `--color-loss` in `index.css`. Charts read the JS copy, everything
else reads CSS. Changing a token silently desyncs the charts. 29 hex literals across 7 files.

### D16 — Ad-hoc type scale · LOW-MEDIUM

171 arbitrary Tailwind values, **105 of them `text-[Npx]`**, including 8–9px text
(`text-[9px]` on the "scheduled" badge, the `↓$1403` deltas, calendar weekday headers, and the
Loss/Profit legend). Sub-10px is below any reasonable floor and bypasses the type scale entirely.

### D17 — `pages/DoubleCalendar.tsx` is dead code · LOW

280 lines, exported, **never imported**. `/dc` redirects to the calendar group and
`CalendarDashboard.tsx` was extracted from it. No other component is unimported.

### D18 — The `$0` bucket dominates the P&L distribution histogram · LOW

B: 21 no-trade days pile into one bar (count 24) that compresses the entire real distribution.

### D20 — Two capital definitions coexist · LOW (recorded, not changed)

Found while fixing D2's second location. The dashboard's `capital_deployed` is a
plain **SUM of every entry's margin**; `base_strategy._calculate_capital_deployed`
computes **peak CONCURRENT** margin, explicitly because the sum double-counts
dollars that are reused when one position closes before the next opens.

Measured:

| var | SUM(entry margins) | peak-concurrent | SUM overstates by |
|---|---|---|---|
| b | $1,404,000 | $1,372,000 | **2.3%** |
| g | $780,000 | $660,000 | **18.2%** |

B is barely affected (its slots overlap almost continuously). G is, because its
two entries often do not overlap at all — so the dashboard shows Peak Margin/Day
$60,000 where the concurrent figure is $50,769, *understating* G's return
(0.11% shown vs 0.127%).

**Deliberately not changed.** It errs conservative, it is pre-existing for every
variant, and correcting it would move the live seat's published ROI — a decision
worth making explicitly rather than smuggling into a strangle fix. The name
"Peak Margin / Day" is currently inaccurate for a sum-based number; either the
computation or the label should change, together.

### D19 — A single-member group renders a "leaderboard" · LOW

`undefined_risk_0dte` has one member; "TODAY'S LEADER — Tied" is meaningless.

---

## 5. What is genuinely good

Worth recording, because the audit is not a demolition:

- **B's Analytics page is strong** — real equity curve, a config-change marker
  (`require-both-sides · Jul 17 · +$19,474 · 44d`), sensible rolling win rate.
- **E/D's calendar dashboard is well-designed and shape-native** — net debit, transform credit, live
  MTM, short/long expiries. This is what a shape-aware view looks like when it is done right, and it
  is the model for G.
- **G's capital labels already work** — "RETURN ON MARGIN" / "PEAK MARGIN / DAY" instead of
  "ROI (on capital)". `capital_basis` dispatch is correct; only the *values* are still absent.
- **Responsive layout is sound** — zero horizontal overflow, zero clipped text at 1440×1000.
- **Honest empty states** — "ratios need ≥20 days · have 15", "No entry data yet", `—` for absent
  values rather than a fabricated zero. The instinct is right; D10 is a bug in *what* is counted, not
  in the discipline.

---

## 5a. STATUS — what is fixed and deployed

| | defect | status |
|---|---|---|
| §3 | Previous-day cards reachable only from `PrimaryICView` | **FIXED + DEPLOYED** `d61c53d` |
| §3 | Off-day cards showed the live seat's best/worst/avg for every strategy | **FIXED + DEPLOYED** `d61c53d` |
| §3 | `OffDaySummaryCards` fetched without `strategy_id` | **FIXED + DEPLOYED** `d61c53d` |
| — | `MarketContextBanner` conditional hook (rules-of-hooks) | **FIXED + DEPLOYED** `d61c53d` |
| D9 | F and G dropped from every group comparison | **FIXED + DEPLOYED** `bf287a2` |
| D10 | Ratio gate counted rows, not traded days | **FIXED + DEPLOYED** `7df9817` |
| D14 | Group tab asserted a single strategy's identity | **FIXED + DEPLOYED** `134c915` |
| D17 | Dead `pages/DoubleCalendar.tsx` | **DELETED** `134c915` |
| D2 | G has no capital history (Return on Margin / Peak Margin blank) | **FIXED + DEPLOYED + VERIFIED ON SCREEN** — backfill run, and D2's 2nd/3rd locations closed. G now renders Return on Margin **+0.11%**, Peak Margin/Day **$60,000**, win rate 61.5% |
| D20 | Two capital definitions (SUM vs peak-concurrent) | recorded, deliberately not changed — see below |
| D11 | Analytics + History entirely shape-blind | **FIXED + DEPLOYED** `Analytics` page-gated for calendars + 16 charts declare their taxonomy facts; `History` keeps its P&L calendar and drops the IC columns |
| D12 | Missing VIX rendered as `0.0` | **FIXED + DEPLOYED** — em dash; zero such cells remain |
| D13 | "Entries 0" on days with P&L | **FIXED + DEPLOYED** — entry/stop columns are intraday-only |
| D15 | Two sources of truth for the palette | **FIXED + DEPLOYED** — `bgDeep` had ALREADY drifted (`#1a2229` vs CSS `#161d23`); corrected + parity test |
| D16 | 105 arbitrary `text-[Npx]` values | open |
| D18 | `$0` bucket dominates the distribution histogram | **FIXED + DEPLOYED** — no-trade days excluded, count shown |
| D19 | Single-member group renders a "leaderboard" | **FIXED + DEPLOYED** — "sole member of this group" |
| — | eslint errors | **rules-of-hooks: 3 → 0.** 17 remain and are deliberately NOT chased — see below |
| P8 | Wire `sides` | **NOT A DEFECT — claim was stale.** Every renderer already guarded F's absent side since 2026-06-15/16. Pinned by 14 regression tests + one genuine hardening in `SessionReplay` (guarded on `entry_type` alone, which can be blank). `sides` IS now wired, via chart applicability. |
| P10 | G's tail cards | **FIXED + DEPLOYED** — `UndefinedRiskCard`: MAX LOSS UNBOUNDED + nearest short in points and × expected move |

### The G backfill has an ordering constraint

`cumulative_metrics` is loaded **once at startup** (`base_strategy.py:1366`) and the whole file is
rewritten from that in-memory copy at settlement (`:6511`). Writing underneath a running bot is
therefore **silently discarded at the next close**. The script refuses to write while the unit is
active; run it after a settlement completes (or with `--force` plus an immediate restart):

```bash
python -m scripts.backfill_capital_deployed --variant g            # dry run
python -m scripts.backfill_capital_deployed --variant g --write    # after the close
```

Dry-run output, verified against real data — the reconstructed per-day net sums to **$836.45**,
exactly G's independently-tracked lifetime P&L:

```
13 rows · avg capital/day $50,769 · net $836.45 · ROI 0.127%
capital is $30,000 on the four days the two entries did NOT overlap, $60,000 otherwise
```

---

## 5b. The 17 remaining eslint errors — a deliberate non-decision

All **3 `rules-of-hooks` errors are fixed** (`MarketContextBanner`, and
`EntryCard`'s two `useAnimatedNumber` calls). Those were the ones that could
become crashes. What is left:

| rule | count | why it is not being chased |
|---|---|---|
| `react-hooks/set-state-in-effect` | 12 | Performance advice about cascading renders, not correctness. Fixing means restructuring every page's data fetching. |
| `react-hooks/immutability` | 3 | One is in `useWebSocket`'s reconnect path. **Measured rather than assumed:** a harness that drops the socket server-side shows the client reconnecting in ~1.2s and returning to `Connected`. The circular `useCallback` dependency is self-consistent. Restructuring live reconnect logic to satisfy a linter is the riskier change. `repro-reconnect.mjs` keeps that claim honest. |
| `react-refresh/only-export-components` | 2 | Dev-server ergonomics only; zero production effect. |

---

## 6. Priority

> Re-rated after measurement: D10 dropped HIGH → MEDIUM once I checked which
> components actually render the ratio cards. See its entry for both corrections.

| | defect | why first |
|---|---|---|
| 1 | **§3 previous-day components** | The original complaint. Visible on 6 of 7 strategies. |
| 2 | **D9** F/G dropped from comparisons | Investor-facing page rendering visible garbage (`75—`). |
| 3 | **D10** zero-padded ratio gate | Latent, not live (D/E never render the cards) — but F crosses the old threshold within about a week. |
| 4 | **D11** shape-blind Analytics/History | Largest scope; Phases 8–9 already planned. |
| 5 | D12–D14 | Data-honesty fixes, small and independent. |
| 6 | D15–D19 | Polish and hygiene. |

**None of this touches trading.** The dashboard is a separate systemd service.

---

## 7. Reproducing

```bash
cd dashboard/frontend && npm run build
cd uiaudit && node mock-server.mjs &      # serves dist + 105 fixtures + WS replay
node audit.mjs                             # 42 screenshots + console errors  → shots/, findings.json
node structural.mjs                        # overflow / clipping / empty-density → structural.json
```

Re-capture fixtures from the VM when the data should be refreshed; the capture scripts call the
router handlers directly and are read-only.
