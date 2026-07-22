# B ↔ C Live-Paper Swap — Researched, Audited Plan

**Goal:** make variant **B** the live-paper variant (`dry_run=false`, places real orders on the IBKR **paper** account) and put **C** on simulation (`dry_run=true`) — with B executing as close to its current simulation as physically possible.

**Status:** researched + adversarially audited 2026-07-21 via an 8-agent workflow (5 research dimensions → 3 red-team lenses: fidelity / completeness / safety-rollback). This document is the recorded plan; nothing here is executed yet.

> **VERDICT — NOT a same-day toggle. Blocked on a CONFIRMED critical bug.** The `dry_run` key itself is a trivial two-line VM edit, but flipping B live today would place a 10×-oversized, un-unwindable naked position the first time an overlay fires. There is a hard-blocker bug plus a rollback that does **not** flatten. See §2. A safe swap is a **~2–4 day build-and-test effort** (full-fidelity path) or a **faster overlay-off path** (§6).

---

## 1. The two independent axes (the task conflates them)

1. **TRADING behavior** — who places real paper orders — is controlled SOLELY by the root `dry_run` key in each variant's VM config (`bots/hydra/config/config_variant_{b,c}.json`), read at `main.py:1246` (`dry_run = args.dry_run or config.get("dry_run", False)`). The systemd units pass NO `--dry-run` flag (`hydra_variant_b.service:66`, `_c.service:57`), so the config key is the sole lever. `dry_run` gates ONLY order placement — NOT strike selection, schedule, stops, or the Brandon stack — so B's *logic* at `dry_run=false` is byte-identical to sim; only fills become real.
2. **COHERENCE (primary / record / alerts / docs)** — "which variant is live" is hardcoded to `c` in ~13 places (dashboard PRIMARY_ID ×2, config.py canonical paths, taxonomy status, the analyst agents' `read_db`, alert ownership, emergency-stop runbooks, tests, docs). A bare `dry_run` flip leaves all of them pointing at the now-simulated C. See §4.

**Config reality:** the VM configs are `.gitignore`d + `skip-worktree` — the in-repo copies are **stale samples** (they show `dry_run:true` + `one_sided_entries_enabled:true`, both wrong for prod). Every value below (`dry_run`, `alerts.enabled`, `google_sheets.enabled`, and the 3 structural B/C deltas) MUST be SSH-read on the VM before editing.

---

## 2. HARD BLOCKERS — must be resolved before ANY B flip

### B1 — CONFIRMED CRITICAL: the overlay live-order path over-places 10× and cannot unwind

B's defensive overlay (debit spreads / butterflies) has **never placed a real order** (synthetic `DRY_OVERLAY_*` in dry-run). Its live branch has a verified sizing bug:

- `_brandon_place_overlay` loops `for q in range(int(leg.quantity)): self._place_option_order(...)` ([brandon/strategy.py:1660](../../bots/hydra/brandon/strategy.py)).
- `_place_option_order` has **no quantity parameter** ([base_strategy.py:2618](../../bots/hydra/base_strategy.py)) and always places `target_qty = int(self.contracts_per_entry)` = **10** ([base_strategy.py:2756](../../bots/hydra/base_strategy.py)).
- So each "one-contract" iteration places **10** contracts. A butterfly (long 10 / short 20 / long 10 = 40 intended) **attempts 100 / 200 / 100 = 400 real SPX contracts** — 10× oversize. Sim books a clean modeled 40.
- The ORDER-006 cap (`max_contracts_per_underlying=60`) rejects mid-loop once the running total tops 60 → the butterfly's final wing never places → **the filled short block is a naked short ratio** (uncapped-loss short calls / large-loss short puts).
- The overlay path has **NO naked-short detection and NO unwind** (unlike `_execute_entry:2579`) — it only fires a CRITICAL "PARTIAL hedge" alert and leaves the unbalanced position open.

**Impact:** the first threatened afternoon B fires a live butterfly, it cements a real, oversized, partially-naked, mis-tracked (HedgeLeg counts orders ≤10 vs 100 real) position that also breaks POS-003 reconcile and understates settlement P&L 10×.

**Fix required:** either (a) rewrite the overlay live path to place each leg with a SINGLE call at `leg.quantity` total contracts (needs `_place_option_order` to accept a quantity, or a Brandon-specific placement) **AND** give the overlay path the same naked-short-detect + `_unwind_partial_entry` that `_execute_entry` has, then validate with a smoke that actually **triggers** an overlay; or (b) set `defensive_overlay.enabled=false` on B's VM config until (a) lands. **Do not flip B live with the overlay live path in its current state.**

### B2 — Rollback is NOT a flatten (the reversibility guarantee is false)

`dry_run=true` gates order **placement only**: `_place_option_order` returns None (`base_strategy.py:2664`), `_reconcile_positions` returns immediately (`:5074`), `_handle_naked_short` returns via SAFETY-DRY-02 (`:3596`), recovery skips the broker cross-check (`strategy.py:13457`). So if B holds **any** real open position or working order at flip-back time, the dry bot is structurally incapable of monitoring, stopping, or closing it — it orphans, rides to expiry unmanaged, and the bot books phantom simulated P&L over the top. The realistic worst case chains B1→B2: naked overlay leg → operator flips to dry to "stop it" → orphaned naked short.

**Fix required:** a written **flatten-first rollback runbook**: (1) query IBKR positions + live orders DIRECTLY at the broker (not the state file), (2) cancel all working orders + manually flatten any residual, (3) confirm 100% flat, THEN (4) flip to dry + restart. Never flip-to-dry with anything open.

### B3 — The 60-contract cap makes live-B diverge from sim-B + spams CRITICAL alerts

`max_contracts_per_underlying=60` (B config) is enforced ONLY on the real path (`base_strategy.py:3219`, called from `_place_option_order_ib:2726`), never in `_simulate_entry`. With B's denser 4-slot 10c grid, 2 concurrent ICs = 80 counted → the 3rd IC's first leg projects 90>60 → rejected (clean whole-IC skip, no naked leg), but `_initiate_entry` retries up to `ENTRY_MAX_RETRIES=3` → up to 3 CRITICAL "POSITION LIMIT EXCEEDED" circuit-breaker alerts per over-cap slot. B has never hit this (sim is uncapped); C never hit it (cap 180). Live-B would drop the 3rd/4th concurrent IC that sim allowed → real entry-count divergence + loud benign alerts.

**Decision required before flip:** raise B's `max_contracts_per_underlying` to ≥160 (4 concurrent ICs × 40) or C's 180 so it matches sim (safe — after the swap only B places real orders, so no shared-margin competition), OR keep 60 knowingly + downgrade the over-cap alert from CRITICAL to INFO.

### B4 — No B flip tooling, and the smoke gate auto-flips the WRONG variant

`flip_ac_live.sh` covers A+C only and its `flip_one` only moves true→false (can't touch B, can't revert C→true); `flip_a_live.sh` is A-only. Worse, `deploy/broker-paper-smoke.service:23` has `ExecStartPost=+flip_a_live.sh`, so `systemctl start broker-paper-smoke` (the sanctioned way to produce the RB-8 same-ET-day PASS sentinel) would **flip variant A live** as a side effect (A is currently dry). The smoke itself is only a 1-contract buy→close round-trip — it never exercises B's IC leg-in, 4-slot grid, 60-cap, or the overlay path.

**Fix required:** write a guarded `scripts/flip_b_live.sh` mirroring `flip_ac_live.sh`'s Guard-1 (broker `/health` connected) + Guard-2 (fresh ET-day smoke sentinel) + atomic validated JSON write + post-restart verify; produce the smoke sentinel by running `scripts/broker_paper_smoke.py --place` **directly** as `calypso` (never the smoke service).

### B5 — Strict EOD-flat ordering (never two-live, never intraday)

The one shared IBKR paper OAuth session serves all variants. If B and C are both live even briefly, same-strike 8-delta picks can **merge** on the shared account → double-counted contracts, conflicting stops, phantom P&L. And restarting C into dry mid-session (or before after-hours settlement) strands its real positions + books stale-SPX phantom P&L (`settlement_stale_spx_bug`).

**Hard gate:** execute ONLY after 16:00 ET close AND after C's after-hours settlement confirms **both** variants flat (broker positions == 0, no working orders). Flip **C→dry, restart, confirm flat FIRST**, THEN flip **B→live**. Never intraday, never overlapping.

---

## 3. The "execute EXACTLY like simulation" reality

Achievable for **logic / strikes / schedule / sizing** (the `dry_run` key changes none of them). **Not** achievable for **realized prices** — flipping the key necessarily replaces every modeled fill with a real paper fill:

| Axis | Sim | Live | Inherent? |
|---|---|---|---|
| IC entry credit | MKT-011 bid/ask mid × contracts | real leg-in fills (progressive slippage) — **~31% credit gap** | yes (fills) |
| Overlay fill | Black-Scholes mid ±0.25 | real marketable fill | yes (fills) |
| Entry completion | always books once MKT-011 passes | can VANISH (failed leg / GUARD-INVERT unwind) or partial-fill | yes (real transport) |
| Exit close cost | worst-case mark estimate | real fill + deferred lookup | yes (fills) |
| Timing | frozen decision-time mid | SPX drifts during the seconds-long leg-in | yes |

**Re-frame the requirement:** "same strikes / schedule / sizing / logic, with real fills replacing modeled mids." A tighter credit match is a *separate* lever (split-spread / midpoint entry pricing — see `golive_readiness_and_fill_lever` memory), not part of the flip.

---

## 4. Coherence checklist (~13 items — a bare flip leaves all pointing at C)

| # | Item | Sev | Difficulty | Action |
|---|---|---|---|---|
| C1 | **Alerts follow B** | HIGH | moderate | SSH-read VM `alerts.enabled` on B/C; set B `alerts.enabled=true`, C `false`. Else live-B trades **silently** (real stop/naked-short → no alert) while sim-C pages on phantom events. Alerts self-label by variant (`alert_service.py:403`). |
| C2 | **Agents repoint** (HERMES/CLIO/HOMER `read_db`) | HIGH | easy | VM-only `agents_config.json`: `read_db` `data/variant_c/backtesting.db`→`variant_b`. Else the git-committed canonical journal (HOMER 7:30pm ET) narrates the **simulated** variant forever. Do BEFORE HOMER's auto-commit; verify `git status` clean. |
| C3 | HOMER/HERMES `state_file`+`metrics_file` (latent bug: point at variant **A**) | MED | easy | Repoint to `data/variant_b/*` with C2 (fixes a pre-existing A-scoping bug). |
| C4 | **Emergency-stop runbooks** name `hydra_variant_c` as live | HIGH | easy | Update RB-8, `LIVE_READINESS_CHECKLIST.md:219/223`, CLAUDE.md → `hydra_variant_b`. Else an operator stops the wrong (now-harmless) unit under stress. |
| C5 | Dashboard PRIMARY (`PRIMARY_ID="c"` ×2 + 6 `config.py` paths) | MED | moderate | If a true swap: change both `PRIMARY_ID` constants + the 6 `config.py` primary paths to `variant_b` **atomically** (mismatch → reads wrong DB), redeploy dashboard. Optional for the trade itself. |
| C6 | Dashboard `baseline_date` | MED | trivial | If PRIMARY moves: set `DASHBOARD_BASELINE_DATE`(+`_VARIANT_B_`) to the swap date so B's multi-month **dry-run** history doesn't blend into the "live" cumulative card. |
| C7 | Taxonomy status | MED | trivial | `strategy_taxonomy.py`: b `dry_run_shadow`→`live`, c `live`→`dry_run_shadow`. Display-only, but every surface lies otherwise. |
| C8 | Tests encode primary=C | MED | easy | If PRIMARY moves: update `test_dashboard_strategies_api.py:130/158` (`primary_id`/`is_primary`) or CI fails the merge. |
| C9 | Docs (README, CLAUDE.md table, deploy/README, GO_LIVE_MASTER slot_edge path) | LOW | easy | Update to B=live/C=sim in the same PR. |
| C10 | ARGUS comment says C is live | LOW | trivial | Comment-only (it watches both units) — update for clarity. |
| C11 | GCS backup doesn't cover the live variant's DB | LOW | easy | Pre-existing; add `data/variant_<live>/backtesting.db` to `db_backup.sh`. |
| C12 | Google Sheets ownership | — | easy | If Sheets writes are active, mirror `google_sheets.enabled` + `spreadsheet_name` C→B. |
| C13 | HOMER auto-commit hygiene | — | easy | Land all VM edits + verify `git status` clean on `/opt/calypso` before 7:30pm ET (HOMER `git add -A && push`). |

**Confirmed NON-issues:** the systemd units (no `--dry-run` flag), the Telegram command poller (env-gated to **A** via `is_default_variant`, unaffected), `/compare` (group_id-scoped), and `calypso-broker` (no `shared/` import changes → **no broker restart**).

---

## 5. Gated execution sequence (ONLY after §2 blockers cleared)

1. **Pre-flight (any day):** SSH-read VM configs; confirm the 3 structural B deltas (`entry_times`, `vix_regime.max_entries`, `max_contracts_per_underlying`), `alerts.enabled`, `dry_run`. Land the overlay-bug fix (B1) + `flip_b_live.sh` (B4) + rollback runbook (B2) + cap decision (B3) via commit/pull, `__pycache__` clear, full suite green.
2. **Same ET day:** run `broker_paper_smoke.py --place` directly → PASS sentinel. Confirm broker `/health` connected.
3. **After 16:00 ET close + after-hours settlement, both flat:** flip **C→dry_run:true**, `restart hydra_variant_c`, confirm C has zero broker positions/orders.
4. **Then:** flip **B→dry_run:false** via `flip_b_live.sh`, `restart hydra_variant_b`; set B `alerts.enabled=true`, C `false`.
5. **Coherence:** repoint agents (C2/C3), dashboard PRIMARY (C5/C6), taxonomy (C7), runbooks/docs/tests (C4/C8/C9), verify `git status` clean before HOMER's commit.
6. **Day-1 observation:** watch `logs/hydra_variant_b/bot.log` for ORDER-006 rejects, the 09:45-slot delta-target strike quality (early-session Polygon greeks C never fed live), any POS-003 mismatch, and the **first live overlay** (only after B1 is fixed + a triggering smoke).

## Rollback (flatten-first — NOT a bare dry flip)

Query the broker directly → cancel all working orders → manually flatten any residual → confirm flat → flip B→dry + C→live at EOD → restart → revert coherence edits.

---

## 6. Difficulty verdict + two paths

- **The `dry_run` toggle itself:** trivial (2 VM keys + restart, at EOD-flat).
- **A SAFE full-fidelity swap:** **hard / ~2–4 days.** Long pole = fixing + testing the overlay live path (B1: sizing + naked-short unwind + a triggering smoke) — the one dimension whose research agent stubbed out and which two auditors flagged critical. Plus B2–B5 + the ~13-item coherence checklist.
- **Faster alternative — overlay-OFF path:** set `defensive_overlay.enabled=false` on B → B becomes a plain **narrow-IC** live variant. The IC real-order path is **proven** (C runs it live); B's only new IC behavior is the 60-cap (B3) + the 09:45 slot. This sidesteps B1 entirely and could be done in ~1 day after B3–B5 + coherence — but it is **not** "exactly like sim" (no overlays), and B's overlays are the very thing that drove its recent P&L.

## 7. Open decisions for the user

1. **Overlay: fix (full fidelity, ~2–4d) or disable on B (fast, ~1d but no overlays)?** — the pivotal call.
2. **B's `max_contracts_per_underlying`:** raise to 160/180 (match sim) or keep 60 (throttle + accept divergence)?
3. **Move the dashboard PRIMARY to B** (full swap) or leave PRIMARY=C and reach live-B via the picker (trade-only)?
4. Repoint the analyst agents/journal to B (canonical record follows live) — yes?
5. Reset B's dashboard baseline to the swap date so its dry-run history doesn't present as the live record — yes?
