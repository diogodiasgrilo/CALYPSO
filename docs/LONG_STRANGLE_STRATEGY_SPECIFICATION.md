# Strategy H — 0DTE Long Strangle ("Tompkins")

**Status:** Steps 0–10 complete (9 offline half; 10 returns **NO-GO**). Dry-run-LOCKED. Functionally complete in dry-run — strikes,
sizing, entry, profit target, settlement — and observable from Telegram and the dashboard.

**The go-live audit returns NO-GO, and the next step is not a flip — it is to run the dry run at
all, which it never has.** Verified on the VM 2026-09-23 08:29 ET: no unit installed, no data
directory, service inactive. Gate: [`H_GOLIVE_SCOPE_AND_AUDIT.md`](migration/H_GOLIVE_SCOPE_AND_AUDIT.md).
**Written:** 2026-09-23 · **Last updated:** 2026-09-23 (Step 10)
**Playbook:** [`docs/NEW_STRATEGY_PLAYBOOK.md`](NEW_STRATEGY_PLAYBOOK.md) Step 0
**Source:** Jeff Tompkins, via Theta Profits —
[article](https://www.thetaprofits.com/a-0dte-long-strangle-targeting-50-100-in-24-hours/) ·
[video](https://www.youtube.com/watch?v=l5LqokEA0A4) ("Inside a 0DTE Options Strategy Targeting
50–100% in 24 Hours", channel `@ThetaProfits`)

---

## 1. The strategy, stated precisely

| | |
|---|---|
| **Legs** | Buy 1 OTM call **+** buy 1 OTM put, same underlying, same expiry. Two legs, no wings. |
| **Direction** | **LONG premium. NET DEBIT.** Long gamma, **short theta**, long vega. |
| **DTE** | 0DTE (article also describes a 1DTE variant entered near the prior close — out of scope here). |
| **Underlying** | **SPY** — what the source actually trades. (SPX until 2026-09-24, chosen "to match the fleet"; that was ours, not his, and it broke his own sizing rule — see §1-bis.) |
| **Entry time** | Near the open. |
| **Strike selection** | Spot ± the options-market **expected move**; then check both sides' premiums are "reasonably similar" to account for skew. |
| **Sizing** | *"Sizing for zero"* — pick an acceptable loss, size assuming **the entire debit goes to zero**. |
| **Profit target** | **+50%** of debit; **+100%** when IV is expanding from a low base. |
| **Stop loss** | **None.** Max loss is the premium paid — defined by construction. |
| **Filter** | IV percentile **below ~35%**. |
| **Adjustment** | Optionally sell against a loser to form a butterfly/condor. **Out of scope** — discretionary, not rule-based. |

**Claimed performance:** "20 of 25" recent trades winners (80%); "75–80%" long-run.

⚠️ **Treat the claims as unverified and implausible as stated.** Single creator, self-reported,
unaudited — the same caveat `STRATEGY_CANDIDATES.md`'s rubric was built for. And the arithmetic does
not work: 80% winners at ~+75% against losers at −100% implies **~+40% expected per trade**, which
would be the best documented edge in retail options. A long strangle is also the *hardest* structure
to win 80% of the time — a move beyond the expected move is a ~32%-probability event by
construction. The only reconciliation is taking profits very early on small gamma moves, which the
source never quantifies. **Build it to measure it, not because the numbers are believed.**

## 1-bis. Source fidelity — where H is NOT what the video says

Asked directly on 2026-09-23: *"is that exactly how the video says to do it?"* It is not, in
four places. Three are deliberate and one is a substitution that cannot currently be avoided.
This table is the honest answer, kept here so it is not re-derived from memory each time.

| Source rule | What H does | Faithful? |
|---|---|---|
| Buy an OTM call + an OTM put at the expected move | Same; strikes from the **ATM straddle** | ✅ |
| No stop — max loss is the debit | Stops explicitly **disarmed**, not merely inherited | ✅ |
| "Sizing for zero" | Same rule; the loss limit is the buying-power floor | ✅ |
| +50% profit target | Same | ✅ |
| **IV percentile below ~35%** | **VIX percentile** over the available window | ⚠️ **closer than first stated — see below** |
| Traded on **SPY** | **SPY** since 2026-09-24 | ✅ **fixed** |
| +100% "when IV is expanding from a low base" | Read as: percentile ≤ max **AND** VIX > prior close | ⚠️ our operationalization of a vague phrase |
| Sell against a loser → butterfly; the 1DTE variant | Out of scope (discretionary / different structure) | ⚠️ deliberate |

**The underlying is fixed (2026-09-24).** H runs on SPY. Verified before switching: SPY quotes
real-time (`6509='Rp'`) where SPX was frozen, the 0DTE chain resolves with 329 strikes at **$1**
spacing near the money, and variant E already trades SPY. This was never a technical constraint —
it was a preference, and it was not free. The identical strangle costs **~$115 on SPY and ~$775 on
SPX**, which is exactly how `sizing_for_zero_max_loss: 500` came to permit **zero contracts**. On
SPY the source's own sizing rule behaves as he describes it: a $1,200 limit buys ~10 contracts.
His **cost cap needed no edit at all** — `max_debit_pct_of_spot` is expressed relative to spot, so
his ~$1.15/share SPY cap and the ~$1,145/contract SPX figure are the same number.

⚠️ **What SPY brings with it: assignment.** SPY options are AMERICAN and PHYSICALLY settled, so an
ITM leg held to expiry becomes 100 shares per contract. This does **not** change dry-run P&L —
`intrinsic − debit` remains the correct economic value — but a real-money H would end a session
holding stock. Recorded as go-live gate **HG-11**; H is dry-run-LOCKED and NO-GO, so it costs
nothing today and must be closed before it ever costs anything.

**On the IV filter — the first statement of this was too harsh.** The probe (Q2) established that
IBKR exposes **no per-option IV fields at all**, so a per-contract IV percentile is genuinely not
computable here. But the source's "IV percentile" is almost certainly the standard platform metric:
the *underlying's* ~30-day implied volatility ranked over a year. For SPY that is, to a close
approximation, **VIX** — both measure 30-day S&P implied vol. So the substitution is much nearer to
his rule than "a proxy for a different quantity" implied. The remaining honest gap is the WINDOW:
the live-seat DB holds ~94 days against the 252 the config requests, which is why every reading now
carries its sample size and says `94d of 252d requested`.

**Sample-size floor (2026-09-23).** `iv_percentile()` had no minimum: one prior day returns
0.0 or 100.0, a number shaped exactly like a percentile with nothing in it. The gate now skips
below `iv_percentile_min_history_days` (60), states the window actually used whenever it is
short of the 252-day lookback, and records `iv_percentile_n` beside every value — on **placed**
entries as well as skipped ones, which previously carried no IV column at all and so left the
35% threshold untestable against its own outcomes.

## 2. Why it is worth building anyway

**It is the only long-gamma strategy the fleet could have.** A/B/C/F/G are all short premium; D/E are
net debit but theta-positive. This is the structural mirror of **variant G** (0DTE short strangle) —
same two legs, opposite sign — and therefore the one structure that profits precisely when the rest
of the book suffers.

Live evidence from this week, not theory:

| session | SPX open→close | B (short gamma) | a long strangle would have |
|---|---|---|---|
| **2026-09-21** | 7692 → 7779 (**+1.1%**) | **−$441/contract**, worst live-era session | profited on the move |
| **2026-09-22** | 20pt range, VIX 14.4 | +$7.61/contract | lost its debit |

The real deliverable is not the claimed edge — it is a **measured answer to "does long gamma hedge
our short-gamma bad days?"**, which the fleet cannot currently answer and which matters more as real
money approaches.

## 3. Build-weight decision (Step 0's actual job)

> ### ⚖️ **MEDIUM** — heavier than F/G, lighter than D/E.
> **Steps needed: 0, 1, 2 (small), 3, 4, 5, 7, 8, 9, 10. Skip 6.**

An earlier verbal estimate called this "light, 7–8/10 ease, mostly G with the sign flipped."
**That was too optimistic**, and finding out is what Step 0 is for. The playbook's table has two
rows — *0DTE credit* (light) and *different shape: multi-day, net-debit, calendar* (heavy) — and this
strategy is **single-day AND net-debit**, a combination that exists in neither row.

**What makes it lighter than D/E:** single expiry, single day. No two-expiry data model, **no sidecar
persistence, no multi-day settlement** → **Step 6 is skipped outright.** Entry/monitor/settle all
close within one session, exactly like G.

**What makes it heavier than F/G — the debit shape, and it reaches further than expected:**

`IronCondorEntry`'s P&L is credit-shaped in **every branch** (`base_strategy.py` ~590–640):
`call_loss = call_debit − call_spread_credit`, `(put_spread_credit − put_spread_value)`, and
`call_side_expired` documented as *"PROFIT — kept credit"*. For a long strangle each inverts —
**expiry worthless is the maximum loss, not the profit.** Reusing that class by overriding signs
would put inverted arithmetic into the money path; a small dedicated entry dataclass is safer and
is the D/E precedent (`calendar_entry.py`), just far smaller.

The schema pushes the same way. `trade_entries` carries `call_credit`, `put_credit`, `total_credit`
and no debit columns. Storing a debit as a negative credit would pollute every shared consumer —
the thin-credit analysis, `expired credits`, `MAX_PNL_PER_IC` bounds — and a migration adding
columns would touch the table **B trades on live**. `daily_summaries`' `gross_pnl` / `net_pnl` are
sign-agnostic and fine.

→ **Step 7 (isolated DB) is IN**, following D/E's `dc_calendar.db` precedent. It keeps the live
variants' schema untouched, which is the deciding factor while B holds the live seat.

## 4. Identity

| | |
|---|---|
| **Variant id** | **`h`** — confirmed free (`a, b, bm, c, d, e, f, g` taken) |
| **Registry name** | **`long_strangle`** — confirmed free |
| **Class** | `LongStrangleStrategy` in `bots/hydra/long_strangle_strategy.py` |
| **Group** | **NEW `GroupMeta` required.** All three existing groups (`ic_0dte`, `calendar_multiday`, `undefined_risk_0dte`) are premium-selling. Proposed `long_gamma_0dte`, `pnl_shape="debit"`, `comparable=False` until a second member exists. |
| **Unit** | `deploy/hydra_variant_h.service`, **dry-run-LOCKED** |

## 5. Reuse map

**Already built — reuse directly:**
- Expected-move calculation — **F computes it today** (`ghauri_strategy.py`, `±EM` boundaries). **Resolved 2026-09-23: the dependency now runs the other way.** H's `expected_move_from_straddle` is the shared helper, and F was moved onto it — so "the expected move" is one piece of arithmetic in this repo, and any difference between the two variants can only be a config difference.
- Two-leg wingless placement — **G's `requires_protective_wings = False`**
- 0DTE SPX chain resolution, conid qualification, snapshot/warmup, IV parsing (percent-parse fixed 2026-09-10)
- Entry scheduling, monitoring loop, alerts, ARGUS, backups (`db_backup.sh` picks up any `data/variant_*/`)
- Dashboard — `/api/strategies` is taxonomy-driven; the picker and nav populate from `/api/strategies/meta`. **A new group needs a renderer** (the `pnl_shape="debit"` calendar renderer is the closest precedent).

**Simpler than every existing variant — worth stating explicitly:**
- **No stop machinery at all.** Max loss = debit. No GUARD-FLOOR, no A2 %-of-width, no MKT-046 anti-spike, no buffer decay.
- **Naked short risk is structurally impossible.** A partial fill leaves a *long* option. The entry-execution failure that cost B **−$159.50 on 2026-09-22 cannot occur here.**
- No margin gate complexity — risk is the debit, known before entry.

**Genuinely new:**
1. **Debit-shaped entry model + P&L** (§3)
2. **IV percentile** — nothing in the repo computes it; raw IV only. Needs a lookback ranking.
3. **Percent-of-debit exits** (+50% / +100%) rather than credit-based targets
4. **"Sizing for zero"** position sizing
5. Isolated DB + its recorder

## 6. Open questions for Step 1

- ~~**Which expected-move definition?**~~ **RESOLVED — the ATM straddle.** Both sources say "the options market's expected move"; on a 0DTE chain that is the ATM straddle, not a de-annualised VIX30. H was built on it, and **F was corrected onto it on 2026-09-23** (`expected_move_source`), retiring the `em_multiplier: 0.50` fudge factor that had been standing in for it. Neither variant falls back to the other definition — they are different distances, and blending them would make the record unsplittable.
- **IV percentile lookback** — what window, from what source? `market_ticks` has VIX history; true option-IV percentile needs a series the repo does not keep.
- **Exit polling rate.** A +50% move on a cheap 0DTE option can appear and vanish inside a minute; B's monitoring cadence may be too slow to capture it, which would make a dry-run result unrealistically poor *or* good depending on direction.
- **Is 1DTE in or out?** Recommend out — it reintroduces overnight hold and Step 6.

## 7. Step 0 exit criteria

- [x] Strategy written down precisely (§1)
- [x] Credit/debit + single/multi-day recorded (§1 — **net debit, single day**)
- [x] Build weight decided and reasoned (§3 — **MEDIUM**, skip 6, include 2 small + 7)
- [x] Variant id + registry name chosen and confirmed free (§4 — **`h`** / **`long_strangle`**)
- [x] Operator go/no-go on proceeding to Step 1 — **given 2026-09-23**

---

## 8. Build progress

| Step | State | Notes |
|---|---|---|
| **0** Classify + spec | ✅ | This document. MEDIUM build. |
| **1** Scaffold + coexistence | ✅ `caf40c9` | Registered, dry-run-LOCKED, **inert**. All 5 coexistence checks pass; dashboard exclusion is automatic via the group's `pnl_shape="debit"`. `main.py` needed no edit (taxonomy-driven banner). |
| **2** Data model | ✅ | `bots/hydra/long_strangle_entry.py` — `LongStrangleEntry(HydraIronCondorEntry)`, every credit-shaped property overridden, 19 tests. |
| **3** Data plumbing | 🟡 **half done** | `bots/hydra/long_strangle_chain.py` — pure selection helpers, 31 tests, no broker/clock. **The market-hours VM probe is still outstanding** and three assumptions below depend on it. |
| **4** Entry + dry-run simulation | ✅ | `long_strangle_strategy.py` — expected-move strike selection, skew veto, sizing-for-zero, the shared pre-entry gates, and a `_simulate_entry` booking synthetic DRY fills into `long_strangle.db`. 53 tests. **No real order can reach the broker**: `_execute_entry` raises rather than inheriting the base's 4-leg IC placement (which would SELL two shorts H doesn't have), and `_calculate_stop_levels_hydra` is an explicit no-op setting both stops unreachable. |
| **5** Exits | ✅ | Percent-of-debit profit target + settlement at intrinsic. 33 tests. **Two inherited exits would have failed SILENTLY** and are overridden: the base books a worthless expiry as "the credit kept" (→ H's total loss recorded as **break-even**), and its DATA-004 sanity guard rejected every H tick as "partial zero prices" (G's S-CRIT-1, mirrored). **No breach-persistence window, on purpose** — see below. |
| 6 | **skipped** | Single-day — no sidecar, no multi-day settlement. |
| **7** Isolated DB | ✅ | `bots/hydra/ls_recorder.py` → `data/variant_h/long_strangle.db`. Four `ls_*` tables, **no credit column anywhere** (asserted). `em_source` stored per entry so the two expected-move regimes stay separable; snapshots accumulate so a +50% peak survives a give-back; `ls_skipped` carries the full counterfactual the GEX work could never recover for B's first 95 vetoes. 18 tests. |
| **8** Observability | ✅ | `bots/hydra/ls_status.py` (pure, read-only) + `/longstrangle` Telegram command; `dashboard/backend/services/ls_reader.py` + `GET /api/long-strangle/{status,recent}` + a `/long-strangle` page. 50 tests. `tsc -b` and `vite build` clean. |
| **9** Hardening | 🟡 **offline half done** | Adversarial pass found 3 defects — a **13-round-trip entry path** against the session live B trades through (now 7), a **small expected move silently building a straddle**, and an **EOD flatten skipped by accident**. 18 tests. **Outstanding: the market-hours VM probe** (item 2 — the one that cannot be done offline) and a measured latency figure. |
| **10** Go-live audit | ✅ **NO-GO** | [`docs/migration/H_GOLIVE_SCOPE_AND_AUDIT.md`](migration/H_GOLIVE_SCOPE_AND_AUDIT.md) — verdict, 15-item risk register, the HG-1..HG-10 gate and halt criteria. Locks, docstrings and the systemd `Description=` all point at it. **The MVL plan and runbook are deferred with stated preconditions**: H has ZERO observations, so an MVL plan would be fiction and a runbook would imply a readiness that does not exist. |

### Step 2 confirmed Step 0's isolated-DB call, for a concrete reason

`total_credit` returns a truthful **`0.0`** (nothing was sold) and the money paid lives in a new
`total_debit`. **`trade_entries` has no column that can hold it** — only `call_credit`, `put_credit`,
`total_credit`. Writing H to the shared table would silently record a strangle that **cost nothing**.
So Step 7 is required, not merely tidy.

### Step 3 (offline half) — the expected move is implemented, not decided

Both definitions are built and the choice is config-driven
(`long_strangle.expected_move_source`), because **they genuinely disagree and the
expected move IS the strike choice**:

| | formula | on 2026-09-22's numbers |
|---|---|---|
| `expected_move_from_straddle` | ATM call + ATM put | **≈ 22pt** — source-faithful |
| `expected_move_from_vix` | `spot × (vix/100) / √252` | **≈ 71pt** — matches variant F's arithmetic exactly |

A test pins that they differ, so nobody later assumes they are interchangeable.
F's formula is reproduced character-for-character so H and F can never disagree
about the maths — only about config.

**⚠️ THREE ASSUMPTIONS AWAIT THE PROBE.** The playbook is explicit that D's
offline tests were green while the live probe caught SPXW expiry gaps and a
missing IV field:

1. **Straddle-as-expected-move.** It systematically *overstates* the 1-SD move
   (~0.85× is the usual correction). **No correction is applied** — the source
   applies none, and inventing a fudge factor before the probe would be fitting
   to nothing.
2. **IV percentile has no honest input in this repo.** Nothing stores option-IV
   history; `market_ticks` keeps VIX, a 30-day *index* proxy, **not** the IV of
   the 0DTE options actually being bought. A caller passing VIX history gets a
   VIX percentile and must say so. The probe must establish what series exists.
3. **The 35% skew tolerance is a guess.** The source says only "reasonably
   similar" and never quantifies it.

Most of the 31 tests pin **refusals**, because every dangerous failure here is
silent-but-plausible: a missing quote becoming a tiny expected move (and
therefore near-the-money strikes); a sparse chain snapping 7825 onto 7700;
`size_for_zero` rounding up to one contract past the loss limit it exists to
enforce; `iv_percentile` returning 0.0 for "unknown" and reading as "passes the
<35% filter".

### What Step 2 found that Step 0 did not anticipate

- **No signed trick rescues the inherited formula.** `total_credit = −debit` with the base's
  `credit − value` gives `−debit − value`; the answer is `value − debit`. They differ by `2 × value`.
  Pinned by a test: a $2,450 debit worth $3,500 is **+$1,050**, and the inherited formula returns
  **−$3,500** — opposite sign, and larger than the position's maximum possible loss.
- **The `[0, width]` clamp had to go.** It exists because a short vertical's cost-to-close is capped
  by its width. A long option has no cap, and clamping would truncate **exactly the large-move payoff
  the strategy exists to capture.**
- **A Step 5 hazard, recorded early.** H populates `long_*` and leaves `short_*` at zero — the mirror
  of G. G needed **S-CRIT-1** because a base guard demanded both legs be priced while its `long_*` was
  permanently zero, so its stop never fired. H cannot hit that exact bug (it has no stop), but any base
  path treating `short_*` as "is there a position here" must be checked in Step 5.

### Step 4 — the decisions the entry path had to make

Four choices in Step 4 were not obvious, and each one is the kind that would have produced a
believable number rather than a visible failure.

**1. No fallback between the two expected moves.** If the configured source cannot be computed — a
missing ATM quote, a VIX of zero — the entry is **skipped**. It does not quietly use the other
definition. The two disagree by roughly 3× (22pt vs 71pt on 2026-09-22), so a fallback would place a
71pt strangle while the config, the logs and the recorded `em_source` all said `straddle`, and the
resulting series could never be separated back into two strategies afterwards. Pinned by a test
asserting the source string does **not** change on failure.

**2. The IV-percentile filter fails closed.** Assumption 2 above is unresolved — nothing in this repo
stores option-IV history — so `iv_percentile_filter_enabled` ships `false`. Enabling it without
naming a series **skips every entry** with an explicit reason rather than passing everything. A
filter that passes everything is indistinguishable from a working one from the outside; a filter that
blocks everything announces itself in the first log line. If the probe's answer to Q2 is "only VIX
exists", the `vix` source is accepted but every skip reason it writes says
`NOT an option-IV percentile`, so no later analysis can mistake a 30-day index vol for the IV of the
0DTE options actually being bought.

**3. The buying-power floor had to be overridden, and not for the reason G's was.** G overrides it
*upward* (a naked short needs far more than the defined-risk IC floor). H overrides it *downward*:
long options are fully paid, so the capital required is the debit. The base derives its floor from
`max(call_width, put_width) × $100` — 60–75pt of IC width, so $6,000–7,500 per contract for a
position costing a few hundred dollars. That would not have failed loudly. It would have skipped
every entry on a modest account and looked exactly like "no signal". The floor is now the
sizing-for-zero loss limit.

**4. The stops are disarmed explicitly, not left inherited.** With `total_credit` truthfully `0.0`,
the base's `credit + buffer` collapses to the `MIN_STOP_LEVEL` floor plus a buffer — a small
arbitrary dollar figure with no relationship to anything, which the monitoring loop would then treat
as a real trigger. `_calculate_stop_levels_hydra` is overridden to set both sides unreachable.
**"No stop" and "a stop nobody chose" are different things**, and only one of them is what this
strategy's max-loss-is-the-debit design actually means.

#### One gate kept deliberately, and it is arguably backwards

The **whipsaw filter still blocks H entries.** It skips when the intraday range exceeds 1.75× the
expected move — which for a long-gamma strategy is a description of the day it *wants*. It is kept
for the first observation window so H's gating matches the rest of the fleet and the dry-run data is
comparable, and it is flagged here rather than silently inverted. `whipsaw_range_skip_mult` is
config-exposed; this is the first knob to revisit once there is data.

#### What Step 4 does NOT do

No exits (Step 5), no live placement path, and no observability surface (Step 8). An H entry opened
today is opened and then **held** — which is why the dry-run lock's message was rewritten to name the
missing exits. `_execute_entry` raises instead of inheriting the base's four-leg placement, because
that path would sell two short legs H does not have and has never sized for; the `__init__` lock
should make it unreachable, and it is the second lock anyway, on the principle that "the other guard
will catch it" is how a strategy ends up selling naked options.

### Step 5 — the exits, and the one place the playbook's own instruction is inverted

**Two inherited exits would have failed silently.** Neither would have raised, logged an error, or
looked wrong in a dashboard — they would simply have produced the wrong number:

| | base behaviour | why it is wrong for H | fix |
|---|---|---|---|
| **Settlement** | books "the full credit kept" for a side finishing OTM | H's `call_spread_credit` is an inherited field nothing ever sets, so the base books **$0.00** — recording a strangle that expired worthless as **break-even** instead of a total loss of the premium | `_settlement_booked_pnl` overridden to `intrinsic − debit` per leg |
| **DATA-004 sanity guard** | rejects a side whose two legs are "partially zero" | H's shorts are permanently 0.0 against a priced long, so it rejected **every tick** and the per-tick manager skipped the entry entirely | `_validate_pnl_sanity` overridden to validate the LONG legs only |

The settlement one is the same class of defect as G's **S-HIGH-2** (an ITM naked short booked as
full-credit profit), with the sign reversed. The sanity guard is G's **S-CRIT-1** mirrored: there the
guard was the bug that kept G's stop from ever firing; here it was discarding the tick the profit
target needs. Both were predicted in Step 2's "a Step 5 hazard, recorded early" note — H populates
`long_*` and leaves `short_*` at zero, so *any* base path reading `short_*` as "is there a position
here" misreads it.

#### No breach-persistence window, and that is the design

The playbook's Step 5 asks for a breach-persistence window on any stop reading a noisy multi-leg
mark — the MKT-046 analogue, so a single stale tick cannot fire a false stop. **Applied here it
would be actively harmful, because the sign of the position inverts the sign of the guard:**

- A **stop** triggers on an *adverse* spike. Waiting to confirm protects you — if it reverts, you
  still hold the position.
- A long strangle's **profit target** triggers on a *favourable* spike, and reverting is exactly what
  those spikes do. Waiting 10 seconds to confirm +50% systematically gives back the move the strategy
  exists to capture.

So the target fires on the **first valid tick**, and the protection against a phantom target is
**quote quality rather than elapsed time**: `_quote_mid`'s crossed-book guard (L-M7) plus the
long-legs-only sanity check. `profit_target_confirm_seconds` exists at a default of **0** so the dry
run can *falsify* that reasoning rather than have it stand as permanent. Every valid tick is written
to `ls_snapshots` **before** the target is evaluated, so the mark that triggered an exit is
recoverable and the peak-versus-exit gap stays measurable — which is the question the source's
80%-win-rate claim actually turns on.

#### One more accident, made deliberate

The base's dry-run **quote-outage fallback** derives every leg from
`total_credit / (140 × contracts)`. H's credit is a truthful `0.0`, so it marks both longs at
**zero**. That is, as it happens, the *right* failure: a zeroed long is rejected by the sanity
guard, so the tick is skipped, no target fires on a fabricated mark, and settlement is unaffected
because it reads the SPX level rather than these prices. Holding the last good mark would be
**worse** — a stale +50% would exit at a price that no longer exists.

`_simulate_hydra_entry_prices` is overridden to do it on purpose and to log a WARNING anyway, for
two reasons: arriving at a safe failure by accident is not the same as choosing it, and the base
path is silent where an operator should see that quotes are down.

#### Conventions kept rather than reinvented

Realized P&L is booked **gross of commission**, with the commission added to the day's separate
total. That is what every other close path in this codebase does; a net-of-commission realized P&L
here would have made H's numbers quietly incomparable with A–G's.

#### The +100% target is unreachable, and says so

The source takes +100% when IV is expanding from a low base. That depends on the same IV-percentile
series Step 4's filter needs and this repo does not have, so `_profit_target_pct` returns 50% until
`iv_percentile_source` is wired. It is stated rather than silently defaulted, because
`profit_target_pct_of_debit_iv_expanding: 100` sitting in the config file reads as an implemented
rule, and today it is not one.

#### A restart would have lost the cost basis — found by audit, not by a test failure

**Step 6 is skipped because H is single-day. That is still correct, and it nearly hid a real bug.**

The shared state file serialises only credit-shaped fields (`total_credit`, `call_spread_credit`,
`put_spread_credit`) and its restore path **hardcodes `HydraIronCondorEntry`**. A mid-day restart —
an ordinary event for a 0DTE strategy — therefore hands H back entries that are the wrong class and
have **no debit at all**. Two consequences, one silent and one loud:

- the per-tick manager skips them (they are not `LongStrangleEntry`), so **the profit target can
  never fire again** for that position; and
- `_settlement_booked_pnl` reads `entry.call_debit`, which a base entry does not have — an
  **`AttributeError` inside the settlement sweep**, which would take down settlement for *every*
  entry that day, not just the recovered one.

**The fix is not a sidecar.** The playbook's Step 6 answer to "the base state schema can't hold your
fields" is a sidecar JSON, and it also says single-day strategies skip Step 6. Both hold here,
because **H already has its own database**: `ls_entries` carries the strikes, conids and both
debits, keyed by `(date, entry_number)`, and the row is written before the position is ever
monitored. Recovery is a read from H's own store — no new file, and **zero edits to the shared
save/load that variant B trades on live.**

An entry whose row cannot be found is reported **CRITICAL and left unconverted** rather than patched
over with a guess, and `_settlement_booked_pnl` now books `$0.00` with a loud "the P&L is genuinely
unknown, NOT zero" instead of raising. An entry with an unknown cost basis has no computable P&L;
inventing one would be worse than saying so.

#### What remains

**Steps 8–10** — observability (a status reader, a dashboard view for the new `long_gamma_0dte`
group), hardening, and the go-live audit. Those are now the only things keeping H locked, along with
the plain fact that **this code has never executed a single tick against a live chain.**


### Step 8 — why H needed its own views rather than a row in existing ones

**Every other renderer in this system assumes premium was COLLECTED.** "Expired worthless" is the
best outcome there and the **worst** one here; P&L is a percentage of a credit there and of a
**debit** here; capital is a spread width there and there is no spread at all here. Folding H into
the iron-condor comparison would not merely look odd — it would state its numbers backwards. So H
gets a Telegram command, an API route and a page of its own:

| surface | what | note |
|---|---|---|
| Telegram | `/longstrangle` | **Not** a `/compare` selector. `long_gamma_0dte` has one member and nothing shares its shape, so there is no head-to-head to render. |
| API | `GET /api/long-strangle/status`, `/recent` | Debit-shaped payload; a test asserts no `total_credit` / `spread_width` key can appear in it. |
| Page | `/long-strangle` | Tab renders only when the group is registered — taxonomy-driven, no hardcoded letter. |

**Three things this view shows that no other one does:**

1. **Max loss as a fact, not an estimate.** It is the debit paid, known before the position opens.
   No other strategy on this dashboard can say that about its own day.
2. **The peak next to the exit.** A long strangle can touch +50% on a gamma spike and give it all
   back inside a minute, so what a position *reached* and what it *captured* are different numbers.
   The gap is computed and rendered as a first-class field (`peak_minus_exit_pct`), not left for
   someone to derive — it is invisible in realized P&L alone, and it is what the source's
   80%-win-rate claim actually turns on.
3. **Declined entries with their counterfactual** — proposed strikes, debit and expected move for
   every skip, so a veto can be scored later rather than reconstructed. The GEX work on variant B had
   to be retro-fitted for exactly this and could never recover its first 95 vetoes.

**Read-only is asserted, not assumed.** Both readers open `mode=ro` and contain no write verb in any
*executable* string — checked via the AST rather than a grep, because the module docstrings name
those verbs on purpose to explain that they are absent (the same fix `test_ls_recorder` needed). The
dashboard reader also imports no bot code, duplicating its small amount of SQL the way `dc_reader.py`
does for D and E.

**`available: false` is the expected production response today.** H is dry-run-locked and not
installed on the VM, so the page renders "no data yet" rather than an error — a 500 for "not
installed" would be worse than an empty view.

### Step 9 — hardening. Three findings, and the first is the one Step 9 exists for

**1. The entry path cost 13 broker round-trips, against the session live B trades through.**
Step 9's third item is blunt about why this is a *correctness* concern and not a tidy-up: every
strategy proxies through the ONE `calypso-broker` session, so a read-heavy entry burst on a
**dry-run** variant adds latency to the **live seat**. The playbook records that D's first entry took
~4 minutes and had to be bounded before it could safely run beside a live variant — *"bound it
before soak, not after."*

The count broke down as one strike-grid fetch, four `_get_option_uic` calls (each internally a chain
fetch **plus** a qualify) and four single-leg quotes. `_read_option_chain` already returns **both**
rights' maps from one read, and quotes batch — so resolving pairs instead of legs brings it to
**seven**, and the VIX expected-move source to **four** (it needs no chain to compute the move).

| | before | after |
|---|---|---|
| straddle source | 13 | **7** |
| vix source | 11 | **4** |

**2. A small expected move could silently build a straddle instead of a strangle.**
`select_strangle_strikes` refuses a non-positive expected move, precisely because "spot ± 0" is an
ATM straddle — a materially different and far more expensive position. It did **not** catch the same
thing happening through the **snap**: with 5pt strikes near the money, any expected move under
~2.5pt rounds **both** legs onto the same strike. That is reachable — the ATM straddle collapses
late in a quiet session, which is exactly when this strategy is least likely to be watched. Now
refused, with the collapsed strike and the move named in the skip reason. **Refused rather than
widened**: widening to the next strike out would invent a position the expected move did not ask
for.

**3. The EOD flatten was being skipped by accident.** MKT-047 force-closes open 0DTE **shorts** near
the cutoff so a late breach cannot ride to max loss in the un-closable final minutes. H has no shorts
and no such tail, so the rule does not apply — but it was already being skipped only because the base
gates on `requires_protective_wings`, which **H sets for one reason, the calendars set for another,
and the naked-short guard reads for a third.** Three unrelated rationales resolving to one flag is
what breaks silently when someone changes one of them. Now an explicit override.

The trade-off is stated rather than left implicit: closing at 15:50 would capture whatever extrinsic
is left, and holding to settlement forfeits it. At 0DTE with minutes to run that is small, SPXW is
cash-settled so there is no assignment risk, and hold-to-expiry is what the source describes. **The
cost of holding is variance, not a systematic loss** — the P&L is set by the 4pm print rather than
by where the position could have been sold ten minutes earlier. If the dry run shows meaningful
extrinsic being given up, `_check_eod_flatten` is the method to change.

#### Still outstanding in Step 9

- **The live VM probe during market hours** (`scripts/probe_long_strangle_data.py`) — item 2, and the
  one that cannot be done offline. It settles the three flagged assumptions from Step 3 and is where
  shape bugs hide (D's offline tests were green while the live probe caught SPXW expiry gaps and a
  missing IV field).
- **A measured latency figure.** The call count is now bounded by construction; the wall-clock number
  comes from the probe.

### The RTH probe ran (2026-09-23, SPX 7724) — all three flagged assumptions resolved

Step 3's outstanding half. Every number below is measured, not assumed.

| Question | Answer | Consequence |
|---|---|---|
| **Chain completeness** | 599 strikes, **5pt spacing near the money**, range 2,600–10,000 | The 25pt `snap_to_chain` cap is **safe** — the widest near-money gap is 5pt. |
| **Q1 — which expected move** | straddle **±22.85pt** vs VIX **±71.62pt**, ratio **3.13×** | Confirms the ~3× claim precisely. They pick genuinely different strikes (C 7745/P 7700 vs C 7795/P 7650). `expected_move_source: straddle` stands as source-faithful. |
| **Q2 — IV percentile** | **No per-option IV fields on the quote at all.** VIX percentile over 90d (n=656) = **11.9%** | Per-option IV is genuinely unavailable, so the VIX proxy was the right call — but it is a proxy, and every skip reason says so. Today's 11.9% is well under 35%, so the filter passes rather than blocking everything. |
| **Q3 — skew tolerance** | Real index skew today: **3.8%** (C 7745 mid 3.80 / P 7700 mid 3.95) | 35% is **very loose**, not too tight. It will rarely veto. Keep it — it catches only genuinely lopsided pairs. |

#### The probe also found what would have wasted every session

**A real 1-contract debit of $775** at SPX 7724, against a `sizing_for_zero_max_loss` of **$500**.
`size_for_zero(500, 775) = 0` — **H would have skipped every day and collected nothing**, defeating
the only reason the variant exists.

$500 was **our** number, not the source's. He trades **SPY**, where the same strangle costs ~$115,
so his own sizing rule would buy several contracts for $500. Raised to **$1,200**, which keeps one
contract affordable in normal conditions while staying just under his own cost cap scaled to SPX
(~$1,145/contract). **This is a risk-appetite dial and it is reversible** — lower it to stop H
trading, raise it to let it size up.

This is the clearest argument for installing a strategy rather than reasoning about it: three
offline steps, a full test suite and a go-live audit did not surface a sizing limit that would have
silently produced an empty dataset.
