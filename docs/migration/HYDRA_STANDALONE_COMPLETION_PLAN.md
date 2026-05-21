# HYDRA Standalone — Completion Plan (post-F5)

**Status**: 📋 plan — the path from "F1–F5 done" to a standalone,
Saxo-free, MEIC-free HYDRA.
**Date**: 2026-05-21
**Predecessors**: F1–F5 ✅ (read + reconciliation + settlement flows),
5-agent audit ✅.

---

## 1. Where we actually are

F1–F5 made HYDRA's **read / reconciliation / settlement** flows
broker-agnostic — chart, quotes, option chain, positions, FX, closed
positions. Verified: 912 tests green.

But `HydraStrategy` still `class HydraStrategy(MEICStrategy)`, and the
inherited base `bots/meic/strategy.py` is **100% Saxo** — verified:
`grep -c "self.broker" bots/meic/strategy.py` → **0**.

### The discovery: the order WRITE paths were never rewired

F1–F5 rewired the methods HYDRA *reads* through. They did NOT touch
the **order write paths**, which live in the inherited MEIC base and
are pure Saxo:

| Method | MEIC line | Role |
|---|---|---|
| `_place_option_order` | 2934 | places one option leg (Saxo order API) |
| `_execute_entry` | 2782 | places the 4 legs of an IC |
| `_close_position_with_retry` | 4276 | closes a leg (Saxo) |
| `_execute_stop_loss` | 4035 | stop-out close path |
| `_handle_naked_short` / `_unwind_partial_entry` | 3756 / 3822 | partial-fill recovery |

In dry-run (current mode) these are dormant — `_simulate_entry` runs
instead. But "HYDRA doesn't know Saxo exists" and "a flag flip goes
100% live" both REQUIRE these rewired to IBKR. This is a real flow —
call it **F6 (order write paths)** — and it's the substantive
remaining engineering, not mere code-movement.

## 2. Strategy: reparent, don't method-port

`HYDRA_MEIC_CALL_CHAIN_AUDIT.md` framed the inheritance removal as
"port 101 methods into a standalone `HydraStrategy`." Refined here: a
method-by-method port into one flat ~15,500-line class is **higher
risk** (4,473 LOC of hand-copied code, every line a transcription-error
chance) and **worse engineering** (one monster class).

**Better, world-class, lower-risk approach — reparent:**

1. **Copy** `bots/meic/strategy.py` → `bots/hydra/base_strategy.py`
   (HYDRA-owned). One file copy.
2. **Reparent**: `HydraStrategy` imports its base from
   `bots.hydra.base_strategy`, not `bots.meic.strategy`. One import
   change. At this point `bots/hydra/` has ZERO dependency on
   `bots/meic/` — HYDRA is "independent of MEIC". Verifiable in one
   commit by the full test suite.
3. **Trim** the HYDRA-owned base incrementally — each trim is a delete
   + test-green verification (far safer than a copy):
   - delete the 59 unreachable methods (audit §"Unreachable");
   - delete MEIC-only config / dead branches;
   - this also kills HYDRA's own now-dead overrides
     (`_reconstruct_entry_from_positions`, `_recover_from_state_file_uics`).
4. **F6** — rewire the write paths in the HYDRA-owned base to IBKR
   (`IBClient.place_and_wait_for_fill` etc. — the Phase A.10 write
   methods already exist).
5. **Saxo purge** — once F6 lands, remove every remaining Saxo code
   path / `self.client` reference / `saxo_client` import from the
   HYDRA-owned base. HYDRA "doesn't know Saxo exists."

A HYDRA-owned base + `HydraStrategy` subclass fully satisfies the goal
("doesn't need MEIC, no Saxo, no dead code") — the inheritance
*mechanism* was never the point; the MEIC *dependency* was. Keeping a
sensible base/subclass split is better than a 15,500-line god-class.

## 3. F6 — order write paths → IBKR (its own design doc)

F6 is large enough to need its own audit + design doc
(`F6_ORDER_WRITE_PATH_DESIGN.md`), the same discipline as F3/F4/F5:
- audit every `self.client.place_*` / order-status / cancel call in
  the write paths;
- design the broker-agnostic write helpers (entry placement, leg
  close, stop execution) on top of `IBClient.place_and_wait_for_fill`
  / `cancel_order` / `get_order_status`;
- SPX combo orders, $0.05 increments, partial-fill unwind, the
  emergency-close path — all need IBKR translation;
- the dry-run `_simulate_entry` fork stays as-is.

F6 also closes audit deferrals **DEF-3** (MKT-033 salvage gate) and
**DEF-5** (`_process_expired_credits` keying) — both were deferred
explicitly *to* the write-path rewrite.

## 4. Dead-code deletion (after F6 + reparent + trim)

- `bots/meic/` — entire directory (HYDRA no longer imports it).
- `shared/saxo_client.py` — once no bot imports it.
- The 4 retired bots: `bots/iron_fly_0dte/`, `bots/delta_neutral/`,
  `bots/rolling_put_diagonal/`, `bots/meic/` (already above).
- `broker/` abstraction layer — if it only existed to bridge
  Saxo/IBClient during transition, evaluate for removal.
- Saxo-only `shared/` modules (`token_coordinator`, `external_price_feed`,
  session-capability code) — evaluate each.
- A fresh ODYSSEUS-style dead-code scan after the deletions.

## 5. Docs sweep (final)

- `CLAUDE.md` — rewrite for the standalone IBKR HYDRA (no Saxo, no
  multi-bot, no MEIC); update every VM command, bot table, env-var.
- Every docstring / comment in `bots/hydra/` that still says "Saxo" /
  "UIC" / references MEIC.
- `bots/hydra/README.md`, strategy spec docs.
- Migration docs → mark the migration complete.

## 6. Sequencing

| Phase | Scope | Risk |
|---|---|---|
| **P1** | Reparent: copy base into `bots/hydra/`, flip the import, verify | Low — one commit, test-verified |
| **P2** | Trim the HYDRA-owned base — delete the 59 unreachable methods + dead overrides, incrementally | Low — delete + test each step |
| **P3** | **F6** — order write paths → IBKR (own design doc, multi-commit) | High — real Saxo→IBKR engineering |
| **P4** | Saxo purge from the HYDRA-owned base | Medium |
| **P5** | Delete `bots/meic/`, `saxo_client.py`, dead bots | Low — deletion, test-verified |
| **P6** | Docs sweep — CLAUDE.md + all docstrings/comments | Low |
| **P7** | Full audit (multi-agent) + VM validation + merge to main | — |

P1–P2 are safe and immediate. P3 (F6) is the heavy lift and gets its
own design doc + approval gate, like F3/F4/F5. P5–P7 follow.

## 7. Honest scope note

The call-chain audit estimated 12–15 working days for the inheritance
removal alone; F6 (write paths) is additional. This is a multi-session
effort. Each phase is independently committable, test-verified, and
leaves the branch green — there is no "big bang." P1 starts now.
