# Strategy D — GO-LIVE RUNBOOK (canonical)

> **THIS IS THE SINGLE SOURCE OF TRUTH for taking Strategy D (`DoubleCalendarStrategy`,
> "DC Time Machine") from dry-run to live (`dry_run=false`) alongside the live-paper variants.**
> If you are a future session (or operator) about to flip D live, touch D's order path, remove the
> dry-run lock, or build any coexistence fix — **read this entire document first, in order.** The D
> code points here (the `ConfigError` dry-run lock in `double_calendar_strategy.py` and the module
> docstring reference this file by name).
>
> **Status of D right now:** dry-run-LOCKED, simulation-only, running daily on the VM
> (`hydra_variant_d.service`). It executes the *full* strategy logic (entry → transform → stop →
> settlement) against **real live two-expiration quotes** but places **zero real orders**. Going
> live is a **multi-week build + a deliberate gated decision**, NOT a config flip. See §3.
>
> **Last updated:** 2026-06-16 · **Owner:** operator (diogodiasgrilo) · **Approver for the flip:** operator

**Supporting analysis (read for the "why"; this runbook is the "how/what/when"):**
- [`D_GOLIVE_SCOPE_AND_AUDIT.md`](D_GOLIVE_SCOPE_AND_AUDIT.md) — the 10-agent scope + adversarial audit + risk register + the NO-GO-on-full-scope verdict.
- [`D_MVL_PHASE1_PLAN.md`](D_MVL_PHASE1_PLAN.md) — the MVL-D (drop-the-transformer-first) plan this runbook operationalizes.
- Memory: `strategy_d_dc_time_machine.md`. Strategy intuition: this repo's chat history / Burnich YouTube `JtGW1wNFNIY`.

---

## 0. How to use this runbook

- Every actionable step has an **ID** (e.g. `G2`, `E1`, `V2`), a **risk tag**, the **files** it
  touches, an **exit criterion**, and where useful a **verify** command. Work top-to-bottom within a
  phase; phases are ordered by dependency.
- Check a box `[x]` only when its exit criterion is objectively met. Record who/when in §10.
- **Do not skip the soak periods.** They are wall-clock, not effort, and they are the safety.
- **Do not bundle the coexistence guards (Phase 1A) into an unrelated change.** They weaken C's
  last-line-of-defense and must ship + be reviewed in isolation.

---

## 1. Golden rules / non-negotiables (violating any of these is a stop-the-line event)

1. **Paper account only.** This whole branch trades the IBKR paper account. There is no live-money
   path. "Live" below means "D places real *paper* orders," which still shares the account with C.
2. **C is the protected path.** Variant C trades real (paper) orders today. Every D change must be
   evaluated for "could this blind / flood / starve / cancel-orders-on C." When in doubt, fail safe
   toward protecting C.
3. **Fail-safe polarity on every account-wide guard.** When ownership of an open position/order is
   uncertain, the guard must take the *conservative* action (HALT / alert / refuse), never the
   permissive one. (The corrected STATE-004 design in §6/Phase 1A is built on this.)
4. **Never flip D from non-flat.** D may only be flipped to live, rolled back, or reconfigured when
   it holds **no open position**. Rollback while holding a real multi-day position silently orphans
   it.
5. **`systemctl stop hydra_variant_d` ≠ flat.** It stops the process; `Restart=always` brings it
   back, and any real legs keep living unmanaged. To stop trading AND close, use the emergency
   flatten (§8.3) first.
6. **The dry-run lock is removed by the arm-gate, never by hand.** Deleting the `ConfigError` by
   hand is forbidden. The programmatic arm-gate (`G6`) is the only sanctioned path.
7. **Shared-code deploys restart the broker first.** Any change to `shared/` that `calypso-broker`
   imports requires restarting `calypso-broker` before the strategies (per CLAUDE.md modularity
   deploy rule). The coexistence guards live in `strategy.py` (NOT `shared/`) so they do *not* need
   a broker restart — but the per-variant ledger (`G1`) may, depending on where it lands.

---

## 2. Current state of D (the starting line)

- `bots/hydra/double_calendar_strategy.py` — full strategy LIVE in **simulation**; dry-run-LOCKED via
  a two-layer `ConfigError` (pre- and post-`super().__init__`). `_initiate_entry` calls
  `_dc_simulate_entry` **unconditionally** (no real-order fork yet).
- Companion modules: `calendar_entry.py` (CalendarEntry subclasses IronCondorEntry, overrides
  economics), `calendar_chain.py` (expiry selection), `dc_recorder.py` (isolated `dc_calendar.db`).
- Deployed: `hydra_variant_d.service` active, 1c, single entry slot **10:00 ET**, persists via sidecar
  `data/variant_d/dc_open_trades.json`. Dashboard `/dc`, Telegram `/calendars` (A), variant-aware
  alerts `HYDRA_D`.
- Hardening already shipped (2026-06-16): persistence-confirmed 20%-debit stop, no double-refresh on
  close, calendar-native observability, `dc_max_concurrent=1`, `dc_max_deployed_debit=$5000` (partial
  BP budget).
- **What does NOT exist yet:** any real-order execution path; the coexistence guards (STATE-004 /
  orphan / BP scoping); the programmatic arm-gate; flip/flatten/rollback scripts; a D-specific smoke;
  overnight push alerts; validated edge.

---

## 3. The GO / NO-GO bar (must ALL be green before any flip)

The audit returned **NO-GO** on flipping the *full* strategy today for three structural reasons.
Each is a hard gate:

- **BAR-1 — Real execution path exists, tested, dry-run fork removed.** (Built in Phase 1B.)
- **BAR-2 — The economic invariant is verified on REAL fills, not mids, with commissions, failing
  closed.** For MVL-D this reduces to "max loss is bounded by the debit paid + the stop fires on real
  fills" (no transform claim). For the full transform (Phase 2) it is "the transformed IC's max loss
  is ≤ 0 after real fills + commissions, re-checked post-execution."
- **BAR-3 — Edge proven by a real metric.** MVL-D: the forward dry-run record shows a debit calendar
  + 20% stop is non-negative-EV over a meaningful window. Full transform (Phase 2): a restored
  offline two-expiration backtest OR a multi-week dry-run record re-priced through the fill model
  showing transformer-fire-rate + net P&L ≥ 0. **NOTE:** the term-structure/IV edge signal is
  *unobservable* on the IBKR feed (field 7633 not served for SPXW) — so the edge case must be made on
  P&L outcomes, not the signal.

Plus the coexistence + ops gates (Phases 1A/1C) and the final readiness gate (§9).

---

## 4. Path decision (choose before starting Phase 1B)

| Path | What goes live | Risk | Effort | Recommendation |
|---|---|---|---|---|
| **Micro-MVL-D** | Same-day double calendar, **no overnight hold**, no transform | Lowest (eliminates the STATE-004 overnight blocker) | ~5.5 eng-wk + ~1.5 wk soak | **First live sub-step** |
| **MVL-D** | Multi-day debit calendar + 20% stop + EOD close, **no transform** | Medium | ~7 eng-wk + 2–3 wk soak | **First full live phase** |
| **Full-scope D** | Calendar → **transform** → risk-free IC | Highest (the risk-free invariant breaks on real fills) | ~11 eng-wk + 3–4 wk soak | **Phase 2, only after MVL-D proves out live** |

**Recommended sequence:** Micro-MVL-D → MVL-D (overnight) → (later, separate decision) Full-scope D.
The rest of this runbook is written for MVL-D, with the Micro-MVL-D toggle called out and Phase 2
(the transform) listed at the end as deferred.

---

## 5. MASTER CHECKLIST — every step to go live

> Legend — Risk: 🟥 high (can harm C / the flip) · 🟧 medium · 🟩 low/additive.
> Each step: **exit criterion** in *italics*. Nothing flips D until Phase 1D.

### PHASE 0 — Decision & prerequisites
- [ ] **P0.1** 🟩 Operator picks the path (§4: Micro-MVL-D recommended first). *Decision recorded in §10.*
- [ ] **P0.2** 🟩 Confirm `calypso-broker` healthy + A/B/C green; D flat (no open calendar). *`curl -s http://127.0.0.1:8788/health` ok; `/api/dc/status` shows 0 open.*
- [ ] **P0.3** 🟩 Snapshot baseline: current D dry-run P&L record + the metrics that BAR-3 will be judged on. *Baseline captured.*

### PHASE 1A — Coexistence guards + arm-gate (C-side; **D STAYS dry-run**; land + SOAK first)
*These change nothing observable while D is simulated, but must be live + proven in C's path BEFORE D
ever holds a real overnight leg.*
- [ ] **G1** 🟧 **Per-variant conid ownership ledger.** Each variant publishes its own live conids
  (preferred: `calypso-broker` aggregates a `GET /owned_conids`; fallback: a shared file). Guards
  below consume it. *Ledger returns each variant's live conids; unit-tested; broker restarted if it
  lives in `shared/`.* — files: `services/broker/`, `shared/broker_client.py` (or a shared file +
  `bots/hydra/strategy.py`).
- [ ] **G2** 🟥 **STATE-004 → fail-safe positive-ownership filter.** In `_reset_for_new_day` /
  `_read_open_positions(strict=True)`: HALT on every open conid **except** one positively in
  *another* variant's ledger; unknown / uic-less / ledger-unavailable → **HALT** (fail-safe). Apply
  in A/B/C. *Passes the full §6 test matrix + the live soak.* — files: `bots/hydra/strategy.py`
  (`_reset_for_new_day` ~10979, `_read_open_positions` ~1902).
- [ ] **G3** 🟧 **Orphan sweep → symmetric scoping.** Subtract other variants' conids before
  flagging (MVP: C ignores non-today expiry; D ignores today/0DTE). Apply in BOTH directions.
  *No orphan CRITICAL fires when D holds a paper leg; a genuine C orphan still alerts.* — files:
  `bots/hydra/strategy.py` (`_recon_detect_orphans` ~10796, `_reconcile_orphan_sweep` ~10936).
- [ ] **G4** 🟧 **`_check_buying_power` → C-aware budget.** Gate D against `min(dc_max_deployed_debit,
  available − C_peak_reservation)`; document the static partition; size `C_peak_reservation` from C's
  worst-case `(max_wing − min_credit) × contracts × peak_concurrent`. *D refuses an entry that would
  cross into C's reservation; tested both directions.* — files: `bots/hydra/base_strategy.py`
  (`_check_buying_power` ~6078), `bots/hydra/double_calendar_strategy.py` (`_dc_pre_entry_gates`).
- [ ] **G5** 🟩 **Hard assert `dc_short_dte_min >= 1`** at construction + every entry (load-bearing
  for G3's heuristic + the no-shared-conid invariant). *Assert present + tested.* — files:
  `bots/hydra/double_calendar_strategy.py`.
- [ ] **G6** 🟥 **Programmatic arm-gate replaces the deletable `ConfigError`.** Refuse `dry_run=false`
  unless ALL of: (a) module flag `_DC_LIVE_EXEC_IMPLEMENTED == True`; (b) G2/G3/G4 register a
  capability flag; (c) a dated D-arm token exists (written only by `flip_d_live.sh` after a D-smoke
  PASS). Fail-closed; the gate references THIS runbook in its error text. *Hand-deleting the lock or
  hand-editing config can no longer arm D; tested.* — files: `bots/hydra/double_calendar_strategy.py`
  (`__init__`).
- [ ] **G7** 🟩 Keep `reconcile_orders`/`classify_orders` OUT of the broker allowlist; add a
  per-variant cOID prefix to `_ensure_coid` BEFORE any future wiring (the current static `CAL_`
  prefix makes per-variant order filtering impossible). *Allowlist unchanged; cOID carries variant
  id; comment added.* — files: `shared/ib_client.py` (`_ensure_coid`), `shared/broker_service.py`.
- [ ] **G-SOAK** 🟥 **Run Phase 1A in production with D still dry-run for ≥ ~1 week.** Watch C's
  mornings: no false halt, no orphan flood, no entry starvation. *≥5 trading days clean; recorded in §10.*

### PHASE 1B — D real-order execution path (built behind the arm-gate; still inert until 1D)
- [ ] **E1** 🟥 **`_dc_execute_open`** — leg in 4 singles across 2 expiries on the existing conid
  primitives (`_place_leg_order` / `place_and_wait_for_fill`, already allowlisted), **long-before-
  short** ordering, a **running net-debit budget guard** (abort if net debit exceeds the limit), and
  `filled_legs` tracking. Add the `if not self.dry_run:` fork in `_initiate_entry`. *Unit-tested with
  mocked leg results incl. abort path.*
- [ ] **E2** 🟥 **`_dc_unwind_partial_open`** — market-close whatever filled if the open aborts
  (CalendarEntry-shaped analogue of `_unwind_partial_entry`). *Partial-open leaves the account flat;
  tested.*
- [ ] **E3** 🟥 **D-native naked-short detection** for failed partial-open / failed stop-close (the
  base defense is suppressed by `requires_protective_wings=False`). *A simulated leg-fill failure
  triggers detection + cleanup; tested.*
- [ ] **E4** 🟥 **Real 20%-debit stop close + real EOD close** — close all 4 legs; book **realized
  fill P&L**, not the mark. Reuse the shipped persistence-confirm window (`dc_stop_confirm_seconds`).
  *Realized P&L = sum of close fills vs debit; tested.*
- [ ] **E5** 🟥 **Settlement closes surviving legs** — short legs settle at the short expiry but the
  far-dated longs are still alive → SELL them before nulling conids (else they orphan + trip C's
  sweep). Confirm SPXW is European cash-settled (trading_class SPXW). *No leg survives untracked
  after settlement; tested.*
- [ ] **E6** 🟧 **Recovery broker cross-check** — after `_dc_load_sidecar`, verify each adopted leg's
  `(conid, signed-qty)` against `get_positions`; alert/heal on mismatch. *A stale-sidecar mismatch is
  detected, not silently trusted; tested.*
- [ ] **E7** 🟧 **Real margin gate** via `what_if_naked_margin` (already allowlisted) on the 4 legs,
  replacing the static $2000 floor. *Entry gated on broker-returned initial margin; tested.*
- [ ] **E8** 🟩 **Correctness sweeps:** assert `Kc > Kp`; realtime-quote gate ON + **fail closed** on
  the open quotes; early-close/half-day handling for `_dc_past_eod_cutoff` + settlement timing
  (`is_early_close_day`). *Each asserted + tested.*
- [ ] **E-EXIT** 🟩 Live path exists but is dormant (`_DC_LIVE_EXEC_IMPLEMENTED` still gates it off
  until 1D); full unit suite green. *`pytest tests/ -q` green incl. new D exec tests.*

### PHASE 1C — Ops: flatten, rollback, smoke, observability, docs
- [ ] **O1** 🟥 **Multi-day emergency-flatten** — `scripts/flatten_d.sh` + Telegram `/flatten_d`: real
  per-leg close of all open D calendars; confirm flat via `get_positions`. *Closes a simulated/real
  held position to flat; rehearsed.*
- [ ] **O2** 🟥 **Flatten-first rollback procedure** (§8.2) — documented + rehearsed. *Rehearsed
  while D holds a (paper) position.*
- [ ] **O3** 🟧 **`scripts/flip_d_live.sh`** — gated on broker `/health`, a **D-specific** smoke
  sentinel (separate file, never A's `last_pass.txt`), and D currently flat; edits only
  `config_variant_d.json`; refuses while the arm-gate's preconditions are unmet. *Refuses without a
  fresh D-smoke; tested.*
- [ ] **O4** 🟧 **`scripts/broker_dc_smoke.py`** — a real 1c 4-leg/2-expiry open → stop/close round
  trip on paper, writing the D-only sentinel. *Documents that combos can't be validated on paper (so
  this proves legs-place-and-fill, not atomicity — closed only by the V4 live soak).* *Smoke passes
  + writes the D sentinel.*
- [ ] **O5** 🟧 **Overnight push alerts** — daily mark, DTE countdown, account margin headroom (the
  dashboard is pull-only). *A held position pushes a daily digest + a margin-headroom threshold alert.*
- [ ] **O6** 🟩 **ARGUS D coverage** (zero today) + re-derive the 4-process IBKR req/s budget incl.
  D's multi-day vigilant tail. *ARGUS alerts on a D freeze/crash; budget documented under the ceiling.*
- [ ] **O7** 🟩 **Docs:** add a D section to `PROJECT_STATUS.md` + `CLAUDE.md` (+ variant-D row);
  write **RB-9 (flip D)** + **RB-10 (flatten D)** in `RUNBOOKS.md`; add D-awareness to HOMER (no
  written record today = no §9 evidence). *All present; cross-link back to this runbook.*

### PHASE 1D — Validation + the actual flip (the deliberate decision)
- [ ] **V1** 🟧 **Edge sanity (BAR-3, MVL framing):** confirm from the dry-run record the debit
  calendar + 20% stop is non-negative-EV over a meaningful window. *Recorded; meets BAR-3.*
- [ ] **V2** 🟥 **Micro-MVL-D flip first** (EOD-day-1 unconditional close, **no overnight hold**), 1
  contract, a handful of sessions. Run `broker_dc_smoke` PASS the same ET day → `flip_d_live.sh`.
  *Real fills + same-day settlement observed clean; C unaffected.*
- [ ] **V3** 🟥 **Relax to overnight hold** only after V2 clean AND G-SOAK clean. *First real overnight
  D leg held; C's next-morning reset does NOT halt (proves G2 live).*
- [ ] **V4** 🟥 **Tiny live soak** at 1 contract across **multiple expiries** before any size
  increase. *≥ N full lifecycles incl. a settlement + (if Phase 2) a transform; recorded in §10.*

### PHASE 2 — The transform (DEFERRED; separate decision after MVL-D proves out)
- [ ] **T1** 🟥 **Fill model** (long@ask/short@bid for cost; reverse for liquidation) feeding the
  transform gate + stop + profit-trigger + settlement TOGETHER (indivisible — it IS the safety
  thesis). *All four decision points priced off the fill model; tested.*
- [ ] **T2** 🟥 **Real transform exec** — **BUY wings FIRST, then sell longs** (no naked window on the
  shared account); recompute `transform_credit` from real fills; conservative-price the gate so
  realized ≥ estimate; handle partial transform (don't flip `dc_phase` until confirmed). *No naked
  window; tested.*
- [ ] **T3** 🟥 **`evaluate_risk_free()` verifies against ACTUAL fills + commissions**, failing closed
  (today it re-checks the stored mid → tautological). *A mid-passing-but-fill-failing transform is
  refused; tested.*
- [ ] **T4** 🟧 Book transform commissions; fold all commissions into the risk-free threshold;
  chain-snap wing strikes (unlistable wing → transform never fires). *Tested.*
- [ ] **T5** 🟧 **Edge re-validation for the transform** (BAR-3 full): restored backtest OR re-priced
  dry-run record showing transformer-fire-rate + net P&L ≥ 0. *Meets BAR-3 for the transform.*

---

## 6. The STATE-004 test matrix (gate G2 — the single highest-risk change)
G2 narrows C's last line of defense, so it ships LAST in 1A and behind this matrix + a live soak.
Must prove:
- [ ] D holds overnight, C flat → C must **NOT** halt.
- [ ] C holds a genuine overnight leg → C must **STILL** halt.
- [ ] A C leg that lost its `*_uic` in a partial recovery / crash window → must **STILL** halt (fail-safe).
- [ ] Ledger unavailable / broker fetch failure → must **HALT** (fail-safe), not skip.
- [ ] Zombie zero-qty rows interacting with the ownership filter behave correctly.
- [ ] D and C at the same conid → cannot happen at `dc_short_dte_min=6`, but the invariant is asserted + tested.
- [ ] **Live soak:** C survives ≥5 mornings while D holds a paper overnight leg (the G-SOAK step).

---

## 7. (reserved)

---

## 8. Procedures

### 8.1 Flip D live (the sanctioned path — never hand-edit)
1. Confirm D is **flat** (`/api/dc/status` → 0 open) and `calypso-broker` `/health` ok.
2. Run the D smoke the SAME ET day: `sudo systemctl start broker-dc-smoke` (or `broker_dc_smoke.py`) → PASS sentinel written.
3. Run `scripts/flip_d_live.sh` — it re-checks broker health + the fresh D sentinel + D-flat, then sets `config_variant_d.json:dry_run=false` and restarts `hydra_variant_d`.
4. Verify boot: `journalctl -u hydra_variant_d -n 50` shows the arm-gate PASSED (not the `ConfigError`) and `dry_run=false`.
5. Watch the first lifecycle live (entry → manage → close/settle).

### 8.2 Rollback D to dry-run (FLATTEN FIRST — order is mandatory)
1. **Flatten:** run §8.3 emergency flatten; confirm flat via `get_positions`.
2. Only once flat: set `config_variant_d.json:dry_run=true` and `systemctl restart hydra_variant_d`.
3. Verify the arm-gate is back to LOCKED. *(A naive rollback while holding a position silently
   orphans real legs — D won't manage what it thinks is simulated.)*

### 8.3 Emergency flatten (close all D positions NOW)
1. `scripts/flatten_d.sh` (or Telegram `/flatten_d`) — real per-leg close of every open D calendar.
2. Confirm flat via `get_positions` / `/api/dc/status`.
3. If you also want D to stop opening: AFTER flat, `systemctl stop hydra_variant_d` (remember
   `Restart=always` — it will restart but, being flat + (optionally) rolled back, opens nothing).
4. `systemctl stop` ALONE does NOT flatten — it leaves real legs unmanaged.

---

## 9. FINAL GO-LIVE READINESS GATE (sign off ALL before V2/V3)
- [ ] **DG-1** Real 4-leg/2-expiry open + stop/EOD-close path built + tested (E1–E5).
- [ ] **DG-2** Invariant verified on real fills (BAR-2): MVL = max loss bounded by debit + stop fires real; Phase 2 = transformed-IC max loss ≤ 0 after fills+commissions.
- [ ] **DG-3** Edge proven (BAR-3) on P&L outcomes (signal is unobservable).
- [ ] **DG-4** Coexistence guards G1–G5 merged, independently reviewed (NOT bundled), + G-SOAK clean.
- [ ] **DG-5** Emergency flatten exists + rehearsed; `/stop` ≠ flat documented.
- [ ] **DG-6** Flatten-first rollback rehearsed.
- [ ] **DG-7** Settlement closes surviving legs; SPXW European cash-settle confirmed.
- [ ] **DG-8** Overnight push observability live (mark / DTE / margin headroom).
- [ ] **DG-9** `Kc>Kp` asserted; realtime gate ON + fail-closed; early-close handled.
- [ ] **DG-10** Arm-gate (G6) live; `flip_d_live.sh` + D-smoke + D sentinel; docs + RB-9/RB-10; D sized 1c, `dc_max_concurrent=1`, C-aware BP budget.
- [ ] **DG-11** Halt/kill criteria (§ below) configured + the operator monitoring cadence agreed.

### Halt / kill criteria for a live D
- **Auto-halt (stop opening + page):** a TRANSFORMED "risk-free" IC marks negative; sidecar↔broker
  divergence; transformer fails to fire by EOD on ≥X consecutive entries; deployed debit would breach
  the budget/reservation; any unresolved naked short; orders breaker open >5 min or 2 consecutive
  `ensure_connected` failures while holding.
- **Operator-flatten (flatten-first, then stop):** realized holding-period loss > a hard $ cap; any
  C CRITICAL traced to a shared-account read D caused; an un-self-resolving naked short on D's conids.

---

## 10. Sign-off log (fill in as steps complete)
| Date (ET) | Step ID | Who | Result / notes |
|---|---|---|---|
| 2026-06-16 | runbook created | Claude (with operator) | Canonical doc established; D remains dry-run-LOCKED. |
| | | | |

---

## 11. Appendix

**Key files**
- `bots/hydra/double_calendar_strategy.py` — D strategy; dry-run lock + arm-gate (`__init__`), exec stubs to replace.
- `bots/hydra/calendar_entry.py` — CalendarEntry + risk-free invariant.
- `bots/hydra/strategy.py` — C-side account-wide guards (`_reset_for_new_day` STATE-004, `_reconcile_orphan_sweep`, `_read_open_positions`, `_expected_position_quantities`).
- `bots/hydra/base_strategy.py` — `_check_buying_power` (BP gate), `_execute_entry`/`_unwind_partial_entry` (patterns to copy), `_place_leg_order`.
- `shared/ib_client.py` — `place_and_wait_for_fill`, `cancel_order`, `what_if_naked_margin`, `_ensure_coid`; `shared/broker_service.py` — RPC allowlist.
- `scripts/flip_ac_live.sh` / `broker_paper_smoke.py` — clone these for the D flip + smoke (D versions are net-new).
- `deploy/hydra_variant_d.service`, `bots/hydra/config/config_variant_d.json` (VM, gitignored/skip-worktree).

**Provenance:** built from a 5-agent independent scope + a 5-agent adversarial audit (2026-06-16),
synthesized in `D_GOLIVE_SCOPE_AND_AUDIT.md`. Every file:line claim spot-checked against the working
tree.
