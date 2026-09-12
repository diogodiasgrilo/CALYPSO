# Proposal — Brandon (B/C) risk-control redesign + remaining bug fixes

**Status:** for review. Follows the Jun 9–10 post-mortem ([../postmortems/2026-06-10_brandon_first_live_days.md](../postmortems/2026-06-10_brandon_first_live_days.md)) and the strategy audit + web research.

**Already shipped (code bugs, tested):** L-C2 backstop (`c0281d9`), L-C2b spread_value clamp (`5b500c7`), strike max-delta clamp / decay-vs-clamp cap / settlement defer (`8d484ef`). **Interim mitigation:** `defensive_overlay.enabled=false` on C's VM config (see Bug #1 below).

This doc splits the rest into **(A) design decisions that are yours to make** and **(B) remaining code bugs with proposed fixes**.

---

## A. Design decisions (your call — these change B/C behavior + the experiment)

### A1. Demote the GEX breach-exit from PRIMARY stop → advisory/observational
**Why (evidence, not opinion):**
- It is **structurally inert on narrow spreads** — it triggers on the cluster's far edge (100–400pt below the short in the common one-wide-cluster case), so it almost never fires near a threatened short. It did not fire once on Jun 10.
- The web research is decisive against it as a *trigger*: GEX wall breaches are **70–86% false**; the only robust real/fake filter is a **~20-minute** sustained breach (Brandon uses **90s**, ~13× too short); GEX's predictive content over realized vol **vanishes once VIX/IV are controlled** (and disappears entirely in high-VIX, where a stop matters most); and **CBOE found no measurable 0DTE gamma-hedging footprint** — a decel wall does not reliably halt SPX.
- **Keep the GEX *strike adjuster*** (lean toward decel walls / away from accel zones at entry) — the GEX *mechanism* is peer-reviewed and real. Only the breach *exit* is unsupported.

**Proposed:** make the credit+buffer (redesigned per A2) the documented PRIMARY for B/C; run GEX-breach as observe-only with per-day counters (armed vs fired vs credit+buffer-fired) to quantify it on our own data. Update `gex_breach_exit.py`, `brandon/strategy.py`, and CLAUDE.md, which all still call it primary.

### A2. Re-express the narrow-spread stop as %-of-width (or reconsider stops entirely)
**Why:** the fixed-$ buffer (tuned for the original HYDRA's 50–110pt spreads) fires at **55–89% of structural max** on a 5pt spread — near-zero protection. And the controlled research (tastytrade, 45-DTE defined-risk) found a 2× stop only trades away win-rate/largest-loss/consistency: *"the defined max loss already IS your stop."* SPXW 0DTE cash-settles at the **4 PM index close for $0**, so for a spread already pinned past its short, **holding to settlement weakly dominates** a wide-quote buy-back.

**Proposed (pick one):**
- **(a) %-of-width stop:** trigger when cost-to-close ≥ ~35–50% of width, so the stop fires at a consistent %-of-max regardless of width. Make the buffer a function of the *placed* spread_width, not the VIX regime.
- **(b) Hold-to-settlement for defined-risk narrow spreads:** drop the credit+buffer close for B/C; rely on strike placement + the defined max loss (the original HYDRA instinct, which the research supports). Keep a stop only for the near-the-short-late-with-recovery case.
- **Recommended:** **(a)** as the safety floor, plus a guard that prefers settlement over a marketable buy-back when a spread is already >~70% of width within N minutes of the close.

### A3. Let the variant A/B framework settle it empirically
No public backtest isolates stop-vs-hold or GEX-breach-vs-credit+buffer on narrow 5pt 0DTE defined-risk with matched entries. The variant framework already shadows the competing stop — so run an Option-Omega-style A/B on our actual structure rather than leaning on consensus. The data is already being collected.

---

## B. Remaining code bugs (proposed fixes — touch the live order path, so reviewed/batched)

### B1. CRITICAL — defensive-overlay legs placed at `contracts_per_entry`× intended size *(mitigated)*
`brandon/strategy.py:1030-1059` loops `for q in range(int(leg.quantity))` and calls `_place_option_order`, but that helper **always** sizes to `contracts_per_entry`. A butterfly center short (`quantity = 2×contracts`) → 2×7 iterations × 7c = **98 contracts instead of 14**; a debit-spread leg → 49 instead of 7. LIVE-only (dry-run builds `HedgeLeg(quantity=…)` correctly); C is the only live variant and the overlay was **enabled**.
**Mitigation done:** `defensive_overlay.enabled=false` on C.
**Fix:** add an explicit `quantity` param to `_place_option_order`/`_place_option_order_ib` (default `contracts_per_entry`); place each overlay leg with ONE call sized to `leg.quantity`; assert placed contracts == `sum(leg.quantity)`. Then re-enable.

### B2. HIGH — partial close can leave a NAKED short, marked done + dropped from monitoring
`_close_entry_early` sets `{side}_side_expired=True` whenever ≥1 leg of the side closed — including when the **short** buy-back failed but the long sold. The Brandon TP/breach handlers read `*_side_expired` as "closed" and stop monitoring the side; the mid-session leg-missing reconciliation is dormant on IBKR (gates on `position_id`, always None). Net: a failed short close → naked short rides to expiry unmonitored (~$3.5K max loss at 7c × 5pt).
**Fix:** only mark a side done when the **short** specifically closed (track short-close success separately). If the short fails but the long sold, keep the side ALIVE, fire a CRITICAL naked-short alert, retry the short / route through `_handle_naked_short`.

### B3. HIGH — partial defensive overlay (broken butterfly) leaves an uncovered short
Butterfly legs are placed `(long lower, short pin ×2, long upper)`; the center short is uncovered until the upper wing fills, and a partial leaves a broken fly with an uncovered short, **alert-only, held to expiry**.
**Fix:** place both long wings BEFORE the center short (mirror the IC longs-first rule); auto-unwind filled legs on any partial that leaves an uncovered short. (Also gated by the B1 re-enable.)

### B4. MEDIUM — concentration cap blind to overlay legs
`_get_current_position_size` sums only the 4 IC legs, so `max_contracts_per_underlying` (180) can't see overlay contracts — with B1, true exposure can far exceed the cap while it reads "well under."
**Fix:** include tracked overlay hedge legs in `_get_current_position_size` (or override in `BrandonHydraStrategy` as `_expected_position_quantities` already does).

### B5. MEDIUM/LOW — settlement robustness + misc
- `_process_expired_credits` books off a **non-strict** position read — pass the strict snapshot (could book off a `[]` that is really a fetch failure).
- Un-booked prior-day loss dropped at next-day reset — flush settlement before `MEICDailyState()` zeroes state.
- spread_value masked to **0** on crossed quotes inflates the dashboard (+885.6 artifact) and can *suppress* the credit+buffer backstop — floor the cost-to-close at intrinsic for ITM legs near settlement.
- 8δ short is below MEIC's 8–28δ center and the 10–16δ mainstream — judge B/C on **net-of-cost risk-adjusted P&L, not win rate**; treat `target_delta` as a hard risk parameter.
- `MIN_STOP_LEVEL` $0.50 floor + MKT-045 snap tolerance are sized for wide spreads — fold into the %-of-width redesign.

---

## Recommended sequence
1. **Now (done):** the three `8d484ef` fixes + overlay disable.
2. **Next batch (B1–B4, reviewed):** overlay sizing, naked-short-on-partial, butterfly leg order, concentration cap. These bound real live risk.
3. **Design (A1–A2, your decision):** demote GEX-breach to advisory; %-of-width stop (or hold-to-settlement). I can draft the implementation once you pick the direction.
4. **Validate (A3):** A/B the redesigned stop vs hold and vs GEX-breach on our own structure via the variant framework.
