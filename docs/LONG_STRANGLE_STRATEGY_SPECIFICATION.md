# Strategy H — 0DTE Long Strangle ("Tompkins")

**Status:** Step 0 only — specification and build-weight decision. **No code written.**
**Written:** 2026-09-23
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
| **Underlying** | SPX assumed, to match the rest of the fleet. The source does not restrict it. |
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
- Expected-move calculation — **F computes it today** (`ghauri_strategy.py:324`, VIX-implied, `±EM` boundaries)
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

- **Which expected-move definition?** F uses VIX-implied. The source says "the options market's expected move" (i.e. the ATM straddle). These differ, and the strike choice is the strategy.
- **IV percentile lookback** — what window, from what source? `market_ticks` has VIX history; true option-IV percentile needs a series the repo does not keep.
- **Exit polling rate.** A +50% move on a cheap 0DTE option can appear and vanish inside a minute; B's monitoring cadence may be too slow to capture it, which would make a dry-run result unrealistically poor *or* good depending on direction.
- **Is 1DTE in or out?** Recommend out — it reintroduces overnight hold and Step 6.

## 7. Step 0 exit criteria

- [x] Strategy written down precisely (§1)
- [x] Credit/debit + single/multi-day recorded (§1 — **net debit, single day**)
- [x] Build weight decided and reasoned (§3 — **MEDIUM**, skip 6, include 2 small + 7)
- [x] Variant id + registry name chosen and confirmed free (§4 — **`h`** / **`long_strangle`**)
- [ ] Operator go/no-go on proceeding to Step 1
