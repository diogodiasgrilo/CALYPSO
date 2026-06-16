# Strategy D — MVL-D Phase 1 Implementation Plan (first safe live step)

**Date:** 2026-06-16 · **Status:** PLAN ONLY — not approved, no code written.
**Companion:** [`D_GOLIVE_SCOPE_AND_AUDIT.md`](D_GOLIVE_SCOPE_AND_AUDIT.md) (the full scope + the NO-GO-on-full-scope verdict).

This plan implements **MVL-D** — the audit's recommended *first* live phase: drop the transformer,
run D as a **defined-risk long debit double calendar managed with the 20%-debit stop + EOD close,
settled by closing all legs**. It is the smallest live footprint that still tests D's real execution
path against the shared account, while removing ~35-40% of the highest-risk exec work and most of the
unvalidated-"risk-free" claim.

> **This plan does NOT flip D to live.** It builds the prerequisites so that flipping becomes a
> deliberate, gated decision later. Each phase below ends in a state that is safe to stop at.

---

## 0. The core simplification

| | Full-scope D | **MVL-D (this plan)** |
|---|---|---|
| Structure | Double calendar → transform → risk-free IC | Double calendar, **held with a stop, never transformed** |
| Max loss | Claimed $0 after transform (breaks on real fills) | **Bounded by net debit paid** (the 20%-debit stop is the active control) |
| Edge claim | "risk-free conversion" (unvalidated + unobservable signal) | "a debit calendar is +EV with a stop" (answerable from the dry-run record) |
| Removed risk | — | transform exec, BUY-wings-first atomicity, partial-transform, the mid-priced risk-free gate, the tautological `evaluate_risk_free`, transform commissions |
| Still required | — | real 4-leg/2-expiry open, unwind, naked detection, real stop+EOD close, **settlement that closes surviving legs**, ALL coexistence guards, arm-gate, flatten/rollback, smoke |

**Optional even-smaller floor — Micro-MVL-D:** additionally close the whole calendar at **EOD day-1
unconditionally** (no overnight hold). This removes the single highest-blast-radius coexistence
blocker (STATE-004 overnight halt) entirely, because D is never open at C's morning reset. Recommend
running Micro-MVL-D as the *very first* live sub-step (1 contract, a handful of sessions), then
relaxing to the overnight hold once the exec path is trusted.

---

## 1. Phasing (each phase is independently shippable + stoppable)

### Phase 1A — Coexistence guards + arm-gate (C-side; D STAYS dry-run) — *land and soak first*
Nothing here flips D. These changes let D *eventually* hold a real position without harming A/B/C, and
they soak in C's live path with zero behavior change while D is still simulated.
- **G1. Per-variant conid ownership ledger** — each variant publishes its own live conids (broker-side
  via `calypso-broker`, or a shared file). The guards below consume it. *(scope A3)*
- **G2. STATE-004 → fail-safe positive-ownership filter** — in `_reset_for_new_day` /
  `_read_open_positions(strict=True)`: halt on every open conid **except** one positively in
  *another* variant's ledger; unknown / uic-less / ledger-unavailable → **HALT** (fail-safe). Apply in
  A/B/C. **HIGH-risk — this narrows C's last-line-of-defense; build with the test matrix in §3.**
  *(scope A1, corrected polarity)*
- **G3. Orphan sweep → symmetric scoping** — subtract other variants' conids before flagging. MVP =
  expiry heuristic (C ignores non-today expiry; D ignores today/0DTE). Apply in both directions.
  *(scope A2)*
- **G4. `_check_buying_power` → C-aware budget** — gate D against `min(dc_max_deployed_debit,
  available − C_peak_reservation)`; document the static partition. *(scope A4/B1)*
- **G5. Hard assert `dc_short_dte_min >= 1`** at D construction + every entry. *(scope A5/C6)*
- **G6. Programmatic arm-gate** replacing the deletable `ConfigError`: refuse `dry_run=false` unless
  (a) module flag `_DC_LIVE_EXEC_IMPLEMENTED == True`, (b) the G2/G3/G4 capability flags are
  registered, (c) a dated D-arm token exists (written only by `flip_d_live.sh` after a D-smoke PASS).
  Fail-closed. *(scope F1)*
- **Exit state:** A/B/C run the new guards in production with D still dry-run (no observable change).
  **Soak ≥ ~1 week** to prove the guards don't mis-fire on C's own legs before any real D position.

### Phase 1B — D real-order execution path (still dry-run by default; gated by `_DC_LIVE_EXEC_IMPLEMENTED`)
Build the write path; keep it behind the arm-gate so it can't run live until 1A soaked + 1D passed.
- **E1. `_dc_execute_open`** — leg in 4 singles across 2 expiries on the existing conid primitives
  (`_place_leg_order` / `place_and_wait_for_fill`, both already RPC-allowlisted), long-before-short
  ordering, a **running net-debit budget guard** (abort if the net debit exceeds the limit), and
  `filled_legs` tracking. Add the `if not self.dry_run:` fork in `_initiate_entry`.
- **E2. `_dc_unwind_partial_open`** — market-close whatever filled if the 4-leg open aborts
  (CalendarEntry-shaped analogue of `_unwind_partial_entry`).
- **E3. D-native naked-short detection** for a failed partial-open / failed stop-close (the base
  defense is suppressed by `requires_protective_wings=False`).
- **E4. Real 20%-debit stop close + real EOD close** — close all 4 legs; book **realized fill P&L**,
  not the mark. Reuse the persistence-confirm window already shipped (`dc_stop_confirm_seconds`).
- **E5. Settlement closes surviving legs** — for a held (un-transformed) calendar, the short legs
  settle at the short expiry but the **far-dated longs are still alive** → must actually SELL them
  before nulling conids (else they orphan and trip C's sweep). Confirm SPXW European cash-settle.
- **E6. Recovery broker cross-check** — after `_dc_load_sidecar`, verify each adopted leg's
  `(conid, signed-qty)` against `get_positions`; alert/heal on mismatch (no cross-check today).
- **E7. Real margin gate** — `what_if_naked_margin` on the 4 legs (already allowlisted) replacing the
  static $2000 floor.
- **Correctness sweeps that ride along:** assert Kc > Kp (D2); chain-snap wing strikes is N/A for
  MVL-D (no wings) — defer; realtime-quote gate ON + fail-closed on the open quotes (D3); early-close
  handling for `_dc_past_eod_cutoff` + settlement timing (E4 of scope).
- **Exit state:** the live path exists but is dormant (arm-gate off). Full unit tests with mocked leg
  results green.

### Phase 1C — Ops, flatten, rollback, observability, docs
- **O1. Multi-day emergency-flatten** (Telegram `/flatten_d` + `scripts/flatten_d.sh`) — real
  per-leg close of all open D calendars. `/stop` ≠ flat must be documented.
- **O2. Flatten-first rollback** procedure — flatten real legs, confirm flat via `get_positions`,
  *then* set `dry_run:true` + restart (naive rollback silently orphans a held position).
- **O3. `flip_d_live.sh`** — gated on broker `/health`, a **D-specific** smoke sentinel (separate from
  A's `last_pass.txt`), and D currently flat. Edits only `config_variant_d.json`.
- **O4. `broker_dc_smoke.py`** — a real 1c 4-leg/2-expiry open → stop/close round-trip on paper,
  writing the D-only sentinel. (Note: combos can't be validated on paper — this proves "legs place +
  fill," not atomicity; that gap is closed only by the tiny live soak.)
- **O5. Overnight push alerts** — daily mark, DTE countdown, account margin headroom.
- **O6. ARGUS D coverage** + re-derived 4-process req/s budget incl. D's multi-day vigilant tail.
- **O7. Docs** — PROJECT_STATUS.md D section, CLAUDE.md D section + variant-D row, RB-9 (flip D) +
  RB-10 (flatten D), the D-specific readiness gate (DG-1…DG-10 from the scope doc, minus the
  transform-specific ones).

### Phase 1D — Validation + the actual flip (the deliberate decision)
- **V1. Edge sanity (MVL framing):** confirm from the dry-run record that a debit calendar + 20% stop
  is non-negative-EV over a meaningful window (no transform claim to validate).
- **V2. Micro-MVL-D first:** flip with EOD-day-1 unconditional close (no overnight hold), 1 contract,
  for a handful of sessions — exercises real fills + settlement with the STATE-004 blocker neutralized.
- **V3. Relax to overnight hold** only after V2 + the 1A guard soak both look clean.
- **V4. Tiny live soak** at 1 contract across multiple expiries before any size increase.

---

## 2. Dependency DAG / critical path

```
1A (guards + arm-gate, C-side)  ──soak ≥1wk──┐
   G1 ledger → G2 STATE-004 / G3 orphan / G4 BP   │ (no broker restart — strategy.py, not shared/)
   G5 assert ; G6 arm-gate                        │
                                                  ▼
1B  E1 open → E2 unwind → E3 naked → E4 stop/EOD close → E5 settle-close-legs → E6 recovery xcheck
        (E7 margin gate, D2/D3 correctness ride along)
                                                  ▼
1C  O1 flatten → O2 rollback ; O3 flip script + O4 D-smoke ; O5/O6/O7 obs+docs
                                                  ▼
1D  V1 edge sanity → V2 Micro-MVL flip (1c, no overnight) → V3 overnight → V4 multi-expiry soak
```

**Hard rule:** 1A must be live + soaked **before** 1D V2 (the first real overnight-capable hold).
**Longest pole:** 1B exec path (build) + 1A STATE-004 test/soak + 1D multi-expiry soak (wall-clock).

---

## 3. The STATE-004 test matrix (the highest-risk item — gate G2)
Must prove, before trusting G2:
- D holds overnight, C flat → C must NOT halt.
- C holds a genuine overnight leg → C must STILL halt.
- A C leg that lost its `*_uic` in partial recovery / crash-window → must STILL halt (fail-safe).
- Ledger unavailable / broker fetch failure → must HALT (fail-safe), not skip.
- Zombie zero-qty rows interacting with the ownership filter.
- D and C at the same conid (can't happen at `dc_short_dte_min=6`, but assert + test the invariant).
Plus a **live soak**: watch C survive several mornings while D holds a paper overnight leg.

---

## 4. Rough effort
MVL-D ≈ **7 engineer-weeks** of build/test + **2–3 weeks** of unavoidable serial soak.
Micro-MVL-D first sub-step shaves the STATE-004 soak off the critical path for the initial flip.
(Full-scope-with-transformer, for reference, ≈ 11 eng-weeks + 3–4 weeks soak — deferred to a later
phase, only after MVL-D proves out live.)

---

## 5. Explicitly DEFERRED to a later phase (NOT in MVL-D)
- The transformer (sell longs + buy wings), BUY-wings-first atomicity, partial-transform handling.
- The mid-priced "risk-free" gate + the fill-model rebasing of it + `evaluate_risk_free` verification.
- Transform commissions; wing chain-snapping.
- Restoring ThetaData / the offline two-expiration backtest (only needed to validate the *transform*
  edge; MVL-D's weaker claim is answerable from the forward dry-run record).
