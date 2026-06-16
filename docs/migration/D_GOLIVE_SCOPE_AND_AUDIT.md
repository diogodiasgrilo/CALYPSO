# Strategy D Go-Live-Alongside-C — Scoped & Audited Change Plan

**Date:** 2026-06-16 · **Branch:** `feat/strategy-d-phase1` · **Method:** 5 independent expert
scoping agents → synthesized scope → 5 independent adversarial audit agents → this consolidated
plan. Every load-bearing claim was verified against the code (audit agent 1: *no false claims,
only minor line-number imprecisions*).

**Goal scoped:** what must change to flip `dry_run=false` for Strategy D (DoubleCalendarStrategy —
multi-day SPX double calendar → "risk-free" IC) on the SHARED IBKR paper account, alongside
LIVE-paper variant C (Brandon 0DTE, real orders), via calypso-broker (one OAuth session).

---

## 0. VERDICT — NO-GO today. This is a build, not a flip.

The risk-officer audit returned a hard **NO-GO** for flipping D now, and the other four audits
support it. The reasons are structural, not cosmetic:

1. **D has NO real-order execution path at all.** `_initiate_entry` (double_calendar_strategy.py:803)
   calls `_dc_simulate_entry` UNCONDITIONALLY — there is no `if not self.dry_run` fork anywhere in
   the file. `grep place_order|place_and_wait_for_fill` across all 3 D files = zero hits. Every
   action (open/transform/stop/EOD/settle) is simulated from mids + `DRY_*` ids. "Flip dry_run=false"
   does not enable trading — it enables an **unimplemented** path. Verified.

2. **The edge was never validated and its signal is unobservable.** ThetaData lapsed → the offline
   backtest the code's own history calls "the edge gate" was never run. The term-structure / IV
   signal that IS the double-calendar's thesis (`_dc_front_back_iv`, field 7633) is not returned by
   IBKR's SPXW snapshot → D trades its own edge blind. The signal is computed only inside a
   diagnostic probe and consumed by nothing.

3. **The "structurally risk-free" invariant breaks on real fills.** `_dc_attempt_transform`
   (:963-970) computes `transform_credit` and the `debit+wing` threshold purely from MIDS, and
   `evaluate_risk_free()` (:998) re-checks the *stored mid value* → tautologically True, zero
   independent verification. On real fills you sell longs @ bid + buy wings @ ask (both worse) and
   `net_debit` was understated at open → the gate fires when the IC is **not** risk-free. Commissions
   (~$9.20 round-trip/1c) are excluded from the threshold entirely. The strategy's entire selling
   point is a mid-pricing artifact that does not survive contact with the book.

4. **Near-zero upside over dry-run; direct downside to live C.** D in dry-run already consumes real
   two-expiry quotes and produces the identical observational record. Going live buys only real-fill
   slippage + settlement-cash data — which you can't even trust until the fill model lands. Against
   that near-zero upside, any coexistence bug degrades a **live-money** strategy's last line of
   defense (see §A).

**The bar for a future GO (all must be objectively true):** (1) a real 4-leg/2-expiry exec path,
built + tested, dry-run fork removed; (2) the risk-free invariant re-verified against ACTUAL fills +
commissions, failing CLOSED; (3) the edge proven by a real metric (restored backtest OR a multi-week
forward dry-run record re-priced through the fill model). Until then, **keep D in dry-run** — there
is no information loss in doing so.

---

## 1. What the audit CHANGED about the v1 scope (read this before the register)

The 5-agent audit materially corrected the scope. These corrections are the highest-value output:

- **C1 — The STATE-004 fix in v1 was the WRONG POLARITY.** v1 said "scope the halt to *C-owned*
  conids (expected ∪ ever-placed)". That is unsafe: if the keep-set is mis-computed, it *suppresses a
  real C overnight leg* — the exact regression we're trying to avoid. The fail-safe-correct design is
  the **inverse**: halt on EVERYTHING except a conid **positively identified as another variant's**;
  anything unknown / uic-less / ledger-unavailable → **halt** (fail-safe). This requires a *positive
  D-ownership signal*, not "subtract C's expected set."
- **C2 — The "ever-placed ownership ledger" does NOT exist** and isn't cheaply derivable (grep: no
  `ownership_ledger`/`placed_conids`/`ever_placed`). The only signal C has is
  `_expected_position_quantities()`, computed purely from in-memory `*_uic` fields — which the
  partial-recovery / crash-window scenarios corrupt. The fix must build the positive D-ledger
  (broker-side via calypso-broker, or a shared file), not assume an ownership set exists.
- **C3 — Per-variant cOIDs do NOT exist.** `_ensure_coid` returns a static `CAL_` prefix for *every*
  variant. So v1's `reconcile_orders` guard-rail ("filter by per-variant cOID prefix") describes a
  filter that **cannot be written** until cOIDs are tagged per-variant. v1's "ZERO-risk comment" is a
  false sense of safety. The real backstop today is that `reconcile_orders` is **not allowlisted** in
  broker_service — keep it that way.
- **C4 — The blast radius is WIDER than "C only".** STATE-004 (`_reset_for_new_day`, no dry-run gate,
  called from main.py:526) and the ORDER-004 BP gate (`_check_buying_power` + `_read_account_balance`,
  no dry-run gate) **also fire on dry-run variants A and B** → D's overnight legs would corrupt the
  A/B head-to-head record AND page on A/B daily. ONLY the hourly orphan sweep is dry-run-gated
  (`_check_hourly_reconciliation` returns early when `self.dry_run`). So A/B are *not* automatically
  safe.
- **C5 — The coexistence problem is SYMMETRIC.** Flipping D live UN-SKIPS D's OWN reconciliation
  (the dry-run early-return disappears), so the D process then flags C's 0DTE legs as orphans and
  could (if expiry-disjointness were ever violated) book a bogus external close on a C conid via
  `_handle_position_discrepancies`. Every guard fix must be applied in BOTH directions with OPPOSITE
  filters (C ignores non-today expiry; D ignores today/0DTE expiry).
- **C6 — `assert dc_short_dte_min >= 1` is load-bearing for THREE vectors** (phantom-close,
  orphan-heuristic validity, flood) but v1 rated it LOW. Config already sets `dc_short_dte_min: 6` so
  the C/D expiry-disjointness invariant *holds today*, but it is NOT enforced by an assert. Make it a
  hard runtime assert.
- **C7 — The fill model is HARD, not SOFT.** It's an indivisible cluster feeding the transform gate +
  20%-stop + profit-trigger + settlement P&L (all read mids today). You cannot ship a conservative
  gate without simultaneously rebasing the stop and settlement onto the same basis. It IS the safety
  thesis.
- **C8 — The anti-spam gate is NOT a backstop for the orphan flood.** CRITICAL dedup window is 300s;
  the orphan sweep runs hourly (3600s ≫ 300s) and `CRITICAL_INTERVENTION` is in `_NEVER_SUPPRESS`. So
  the hourly Telegram+email flood is real and indefinite — A2 must actually be fixed.
- **C9 — Reuse is larger than v1 implied; the guards are C-side, not shared/.** The exec *plumbing*
  exists and is conid/expiry-agnostic and already RPC-allowlisted (`place_and_wait_for_fill`,
  `cancel_order`, `_place_leg_order`, `_close_leg_order`, `what_if_naked_margin`). The net-new work is
  the *orchestration*. And A1/A2 live in `strategy.py` (C's class), NOT `shared/` → a normal restart
  of `hydra_variant_c` suffices; **no calypso-broker restart, no modularity-deploy-bug exposure** (v1
  implied otherwise).
- **C10 — `what_if_order` is not allowlisted but `what_if_naked_margin` IS** — so a real 4-leg
  margin gate can be built today with no broker change (pass the 4 calendar legs as 2 BUY + 2 SELL).
- **C11 — New gaps v1 missed:** early-close/half-day handling for D's EOD + settlement (fixed
  15:55 cutoff; `is_early_close_day` not consulted); a re-derived 4-process req/s budget including
  D's *multi-day* vigilant tail (the ~0.9 req/s design number excluded D); no coexistence regression
  tests (the only D test asserts the very lock the flip must delete); ARGUS has **zero** D health
  coverage; and a TOCTOU race where C and D read the shared `MarginAvailableForTrading` at the same
  tick with no lock.
- **C12 — One v1 overstatement:** the TRANSFORMED settlement branch is SAFE (wings are on the short
  expiry → all 4 legs settle together). Only the un-transformed CALENDAR-leftover / failed-transform
  branch orphans the far-dated longs. The fix target is narrower than v1 said.

---

## 2. Change register (risk-rated, effort S/M/L)

Effort is engineer-effort; **soak/wall-clock is called out separately** because some validation can
only happen live across multiple expiries (combos are unvalidatable on paper).

### A. Shared-account coexistence guards — the part that endangers live C
| ID | Change | File | Risk | Effort |
|----|--------|------|------|--------|
| A1 | **STATE-004 overnight halt → positive D-ownership filter (fail-safe to halt on uncertainty).** Halt on every open conid EXCEPT one positively in another variant's published ledger. Apply in A/B/C processes. | strategy.py `_reset_for_new_day` (~10979) + `_read_open_positions` (~1902) | **HIGH** (narrows C's last-line-of-defense; mis-built → suppresses a real C overnight leg) | M–L |
| A2 | **Orphan sweep → subtract other variants' conids** (MVP: expiry heuristic — C ignores non-today; D ignores today). Apply symmetrically. | strategy.py `_recon_detect_orphans` (~10796), `_reconcile_orphan_sweep` (~10936) | MED (alert-only; 2nd-order desensitization = HIGH) | M |
| A3 | **Build the positive per-variant conid ledger** (broker-side via calypso-broker, or shared file) that A1/A2 consume. Prereq for the fail-safe polarity. | new; calypso-broker `services/broker/` | MED | M |
| A4 | **ORDER-004 BP gate → C-aware budget** (not scoping — single account number). See §B. | base_strategy.py `_check_buying_power` (~6078) | MED | M |
| A5 | **Hard assert `dc_short_dte_min >= 1`** at D construction + every entry (load-bearing for A2 heuristic + phantom-close). | double_calendar_strategy.py | LOW | S |
| A6 | **Leave `reconcile_orders`/`classify_orders` UNwired + out of the allowlist**; add per-variant cOID prefix to `_ensure_coid` BEFORE any future wiring. | ib_client.py `_ensure_coid` (~2914); ib_reconcile.py | LOW (dormant) | S (comment) / M (cOID tagging) |
| A7 | **Coexistence regression test matrix** (A1: D-holds/C-flat→no-halt; C-holds-real-leg→still-halt; uic-less C leg→halt; ledger-unavailable→halt; zombie zero-qty rows). | tests/ | — | M (the real cost of A1) |

### B. Buying power / margin / capital
| ID | Change | Risk | Effort |
|----|--------|------|--------|
| B1 | **C-aware BP reservation in D's gate:** D budget = min(`dc_max_deployed_debit`, available − C_peak_reservation). `dc_max_deployed_debit` alone is C-blind/insufficient. | MED | M |
| B2 | **Real margin gate** via `what_if_naked_margin` (already allowlisted) on the 4 calendar legs, replacing the static $2000 floor. | LOW | S |
| B3 | **Express allocation as fraction of net_liq** (net_liq is read NOWHERE today; add to `get_balance` map). | LOW | M |
| B4 | **Mid-transform naked-short margin spike → BUY wings FIRST, then sell longs** (legged transform on a shared account; a naked window can margin-call C). Constraint inside C-exec-transform. | **HIGH** | M |
| B5 | **Book transform commission + fold all commissions into the risk-free threshold** (8-leg round trip). | LOW | S |
| B6 | Use `_dc_open_debit_at_risk` (excludes TRANSFORMED) for the budget, not `_calculate_capital_deployed`. | LOW | S |

### C. Real-order execution path (net-new orchestration on existing primitives)
| ID | Change | Risk | Effort |
|----|--------|------|--------|
| C-exec1 | `_dc_execute_open` — leg in 4 singles across 2 expiries on the conid primitives, long-before-short ordering, running net-debit budget guard, `filled_legs` tracking. | HIGH | L |
| C-exec2 | `_dc_unwind_partial_open` (analogue of `_unwind_partial_entry`, but CalendarEntry-shaped). | HIGH | M |
| C-exec3 | D-native naked-short detection for failed partial-open/transform/stop (`requires_protective_wings=False` suppresses the base defense). | HIGH | M |
| C-exec4 | **Fill model** (long@ask/short@bid for cost; reverse for liquidation) feeding gate + stop + profit-trigger + settlement TOGETHER. | **CRITICAL** | L |
| C-exec5 | Real transform exec (B4 sequencing; recompute credit from real fills; conservative-price the gate so realized ≥ estimate; handle partial transform — `dc_phase` flips unconditionally today at :997). | **CRITICAL** | L |
| C-exec6 | Real 20%-stop close + real EOD close (4 legs; book realized fill P&L not the mark). | HIGH | M |

### D. D-internal correctness for real money
| ID | Change | Risk | Effort |
|----|--------|------|--------|
| D1 | `evaluate_risk_free()` must verify against ACTUAL fills post-exec, not the stored mid (tautology today). | CRITICAL | M |
| D2 | Assert **Kc > Kp** at strike selection AND transform (no guard today). | MED | S |
| D3 | Real-time quote gate ON + **fail CLOSED** on the transform (default OFF + fails OPEN today); probe non-0DTE SPXW entitlement first. | HIGH | S |
| D4 | Chain-snap wing strikes (MKT-045 analogue) — unlistable wing → transform never fires → silent ride-to-stop. | MED | S |
| D5 | Settlement vs official SPXW PM SOQ (recorded close today) + reconcile booked P&L vs broker cash. | MED-HIGH | M |
| D6 | Strike/expiry search robustness (delta step-search caps silently skip entries → dry-run survivorship bias; pick most-liquid long, not smallest-gap). | MED | M |

### E. Lifecycle / recovery / settlement
| ID | Change | Risk | Effort |
|----|--------|------|--------|
| E1 | Recovery: cross-check the sidecar against `get_positions` (no cross-check today → stale sidecar = phantom/naked). | HIGH | M |
| E2 | Settlement CALENDAR-leftover branch must actually CLOSE surviving far-dated longs before nulling conids (else orphans → trips C's sweep). TRANSFORMED branch is already safe. | HIGH | M |
| E3 | Confirm SPXW is European cash-settled (trading_class SPXW, not AM-settled SPX); assignment guard. | LOW | S |
| E4 | Early-close/half-day handling for `_dc_past_eod_cutoff` + settlement timing (fixed 15:55; `is_early_close_day` not consulted). | HIGH | S |

### F. Risk controls / observability / ops / governance
| ID | Change | Risk | Effort |
|----|--------|------|--------|
| F1 | **Programmatic arm-gate** replacing the deletable ConfigError: require (a) `_DC_LIVE_EXEC_IMPLEMENTED=True`, (b) coexistence-fix capability flags, (c) a dated D-specific arm token from the flip script. Fail-closed. | — | M |
| F2 | `flip_d_live.sh` + a **D-specific** 4-leg/2-expiry/transform smoke + a **separate** D sentinel (never piggyback A's `last_pass.txt`); flip only from flat. | — | M |
| F3 | **Multi-day emergency-flatten** (Telegram + script). `/stop` only stops the PROCESS → real legs ride unmanaged. `Restart=always` + held legs = crash-loop hazard. | CRITICAL | M |
| F4 | **Flatten-first rollback** procedure (naive dry-run rollback silently orphans a real position — D won't even notice its own legs). | CRITICAL | S |
| F5 | Overnight PUSH alerts: daily mark, DTE countdown, transform status, account margin headroom (observability is pull-only + dry-run-shaped today). | MED | M |
| F6 | Re-derive the 4-process IBKR req/s budget incl. D's multi-day vigilant tail + order/transform/settle traffic. | MED | S |
| F7 | ARGUS D health coverage (zero today — a D crash/freeze reports PASS). | MED | S |
| F8 | Docs: PROJECT_STATUS.md D section (absent — the read-first doc), CLAUDE.md D section + variant-D row, RB-9 (flip D) + RB-10 (flatten D), D-specific readiness gate, HOMER D-awareness (no written record = no gate evidence). | — | M |

---

## 3. Recommended path — phase the SCOPE down, not just the schedule

The audit's highest-leverage recommendation: **shrink the live surface before shrinking the
timeline.** Three options, smallest-risk first:

- **Micro-MVL-D (smallest):** same-day double calendar — open and **close at EOD day-1
  unconditionally**, never hold overnight, never transform. This eliminates the entire **A1 STATE-004
  overnight-halt blocker** (D is never open at the morning reset) AND most of the orphan/BP overnight
  exposure. It is barely "the DC Time Machine," but it is the smallest possible live footprint and
  the safest first live step. ~5.5 eng-weeks + ~1.5 weeks soak.
- **MVL-D (recommended first live phase):** drop the **transformer** entirely → a defined-risk long
  debit calendar managed with the 20%-debit stop + EOD close, settled by closing all legs. Removes
  ~35-40% of the highest-risk exec work (C-exec5, the transform fill-model complexity) AND most of
  the unvalidated-edge blocker (you're no longer claiming a risk-free conversion — just "is a debit
  calendar +EV with a stop," answerable from the dry-run record). Still needs the open/unwind/naked
  detection, real stop+EOD close, settlement-close-the-longs, and ALL coexistence guards (it holds
  overnight). ~7 eng-weeks + 2-3 weeks soak.
- **Full-scope D (with transformer):** the complete strategy. ~11 eng-weeks + 3-4 weeks soak
  (~3 months wall-clock for one engineer). Build this as a SEPARATE live phase only after MVL-D
  proves out live at 1 contract.

**Whichever phase: the coexistence guards (A1/A2/A3 + B1 BP budget) and the arm-gate (F1) must land,
be tested, and SOAK in C's live path BEFORE D ever opens a real overnight leg** — not concurrently,
not after. If the ConfigError lock is removed before the guards land, the first D overnight hold is a
C-blinding event.

---

## 4. Sequencing (dependency DAG) + critical path

```
TIER 0 (independent, land + soak while D stays dry-run):
  A1 STATE-004 positive-ownership filter ─┐
  A2 orphan sweep symmetric scoping       ├─ C-side (strategy.py); restart hydra_variant_c only
  A3 per-variant conid ledger             │   (NO broker restart — not shared/)
  A5 dc_short_dte_min assert; D2 Kc>Kp; D4 chain-snap; A6 reconcile guard
       │  (these change nothing observable while D is dry-run, but must soak in C's live path)
       ▼
TIER 1 (exec spine — critical path):
  C-exec1 _dc_execute_open ─▶ C-exec2 unwind ─▶ C-exec3 naked detection
       │
       ▼
  C-exec4 FILL MODEL  ◀── HARD, indivisible ──▶ feeds C-exec5(transform) + C-exec6(stop/EOD)
       │                                              + D1(verify) + D5(settle)
       ▼
TIER 2 (depends on the spine existing):
  F3 emergency-flatten ─▶ F4 flatten-first rollback ; E1 recovery cross-check ; E2 settle-close-longs
  B1 BP budget (start in T0, finalize here) ; B2 what_if gate ; B4 wings-first
       ▼
TIER 3 (gating/ops):
  EDGE VALIDATION (parallel wall-clock track) ; F1 arm-gate ; F2 flip_d_live + D-smoke
  F5 push alerts ; F6 req/s budget ; F7 ARGUS ; F8 docs/RB-9/RB-10/readiness gate
```

**Longest build pole:** the fill-model + transform cluster (C-exec4 + C-exec5).
**Longest wall-clock pole:** the A1 morning soak + the multi-expiry live soak (combos are
unvalidatable on paper — the legged path stays untested until live).

---

## 5. D-specific go-live gate (replaces C's 0DTE-shaped checklist)

C's `LIVE_READINESS_CHECKLIST.md` is 0DTE/flat-overnight shaped. Gates 4 (5 sessions), 7 (backup),
8 (sizing), 9 (halt) don't transfer. D requires:

- **DG-1** 4-leg/2-expiry open + (if applicable) transform + stop/EOD-close path built + tested;
  unwind + leg-sequencing (long-before-short on open; wings-before-longs on transform).
- **DG-2** Risk-free invariant verified on REAL fills + commissions; fail-closed.
- **DG-3** Edge proven: restored backtest OR multi-week forward dry-run re-priced through the fill
  model (transformer-fire-rate + net P&L ≥ 0).
- **DG-4** Coexistence MUST-FIXes merged + independently audited (NOT bundled) + regression tests.
- **DG-5** Multi-day emergency-flatten exists + rehearsed; `/stop` ≠ flat documented.
- **DG-6** Flatten-first rollback procedure.
- **DG-7** Settlement closes surviving longs; SPXW European cash-settle confirmed.
- **DG-8** Overnight push observability (mark / DTE / transform / margin headroom).
- **DG-9** Kc>Kp asserted; wings chain-snapped; realtime gate ON + fail-closed.
- **DG-10** PROJECT_STATUS/CLAUDE.md D sections + RB-9/RB-10 + HOMER D-awareness; programmatic
  arm-gate live; D sized at `contracts_per_entry=1`, `dc_max_concurrent=1`, C-aware BP budget.

---

## 6. Halt / kill criteria for a (future) live D

**Auto-halt (stop opening, page):** a TRANSFORMED "risk-free" IC marks negative (invariant violated
on real fills); sidecar↔broker divergence; transformer fails to fire by EOD on ≥X consecutive
entries; deployed debit would breach budget/reservation; any unresolved naked short; orders breaker
open >5 min or 2 consecutive `ensure_connected` failures while holding.
**Operator-flatten (flatten-first, then stop):** realized holding-period loss > hard $ cap; any C
CRITICAL traced to a shared-account read D caused; an un-self-resolving naked short on D's conids.
**Document NOW (true even in dry-run):** `systemctl stop hydra_variant_d` stops the process, not the
positions; with `Restart=always`, a crash-loop while holding real legs leaves them live + unmanaged.

---

## Appendix — agent provenance
5 scope agents (shared-account guards; BP/margin; exec+lifecycle; D-internal correctness; ops/
governance) → synthesized v1 (`/tmp/D_golive_scope_v1.md`) → 5 audit agents (claim-verification;
completeness; harm-to-C register; effort/sequencing/MVP; go/no-go governance). All file:line claims
spot-checked against the working tree on 2026-06-16.
