# B ↔ C Live-Paper Swap — Researched, Audited Plan

**Goal:** make variant **B** the live-paper variant (`dry_run=false`, places real orders on the IBKR **paper** account) and put **C** on simulation (`dry_run=true`) — with B executing as close to its current simulation as physically possible.

**Status:** researched + adversarially audited 2026-07-21 via an 8-agent workflow (5 research dimensions → 3 red-team lenses: fidelity / completeness / safety-rollback). **Then the blockers were FIXED (2026-07-21) — see §0.** The remaining work is a set of operator DECISIONS (sizing) + the gated EOD deploy, not code.

> **ORIGINAL VERDICT (pre-fix) — NOT a same-day toggle. Blocked on a CONFIRMED critical bug.** The `dry_run` key itself is a trivial two-line VM edit, but flipping B live *before the fix* would place a 10×-oversized, un-unwindable naked position the first time an overlay fired, and a rollback did **not** flatten.
>
> **CURRENT STATE (post-fix) — the code is READY; the flip is a guarded operator action.** The critical overlay bug is fixed + tested (B1), the flip/rollback/flatten tooling exists (B2/B4/B5), and the dashboard auto-follows the live seat (C5). What remains is the operator's call on **contract size (7 vs 10)** and the **position cap**, plus a one-command gated EOD swap. See §0 + §7.

---

## 1. The two independent axes (the task conflates them)

1. **TRADING behavior** — who places real paper orders — is controlled SOLELY by the root `dry_run` key in each variant's VM config (`bots/hydra/config/config_variant_{b,c}.json`), read at `main.py:1246` (`dry_run = args.dry_run or config.get("dry_run", False)`). The systemd units pass NO `--dry-run` flag (`hydra_variant_b.service:66`, `_c.service:57`), so the config key is the sole lever. `dry_run` gates ONLY order placement — NOT strike selection, schedule, stops, or the Brandon stack — so B's *logic* at `dry_run=false` is byte-identical to sim; only fills become real.
2. **COHERENCE (primary / record / alerts / docs)** — "which variant is live" is hardcoded to `c` in ~13 places (dashboard PRIMARY_ID ×2, config.py canonical paths, taxonomy status, the analyst agents' `read_db`, alert ownership, emergency-stop runbooks, tests, docs). A bare `dry_run` flip leaves all of them pointing at the now-simulated C. See §4.

**Config reality:** the VM configs are `.gitignore`d + `skip-worktree` — the in-repo copies are **stale samples** (they show `dry_run:true` + `one_sided_entries_enabled:true`, both wrong for prod). Every value below (`dry_run`, `alerts.enabled`, `google_sheets.enabled`, and the 3 structural B/C deltas) MUST be SSH-read on the VM before editing.

**VM config reality (SSH-read 2026-07-21) — B and C are NOT the same strategy with a dry_run flag between them:**

| Knob | **C (live now)** | **B (sim now)** | Note |
|---|---|---|---|
| `dry_run` | `false` (LIVE) | `true` (sim) | the swap flips these |
| `contracts_per_entry` | **7** | **10** | **DECISION:** B live places 10 unless changed to 7. B does NOT inherit C's 7. |
| `defensive_overlay.enabled` | **false** | **true** | **C-live has overlays OFF** — that is *why* the B1 bug never fired in prod. B's overlays are what drove its recent P&L. |
| entry slots (`entry_times`) | 3 (10:15/10:45/11:15) | 7 (09:45→12:45) | B is a denser grid |
| `vix_regime.max_entries` | [2,2,2,1] (keep 2) | [7,7,7,7] (keep 7) | B holds more concurrent ICs |
| `max_contracts_per_underlying` | 180 | **60** | **DECISION (B3):** 60 truncates B's denser live grid; sim ignores the cap. |
| `alerts.enabled` | true | false | the swap flips these (else live-B trades silently) |
| `max_contracts_per_order` | 15 | 15 | overlay legs >15 are chunked by the fix |

---

## 0. Progress — blockers FIXED (2026-07-21)

Committed on `hydra-ibkr-standalone` (deploy pending a safe EOD window):

- **B1 — FIXED** (`d0b5714`). `_place_option_order`/`_place_option_order_ib` gained an optional `quantity` (None ⇒ `contracts_per_entry`, so the IC path is byte-identical; ORDER-006 now validates the **actual** requested qty). The overlay caller places each leg **once** at `leg.quantity`, chunked by `max_contracts_per_order`, and is now **ATOMIC** — a partial fill unwinds every filled leg (`_brandon_unwind_overlay_legs` → `_flatten_accumulated_partial`) instead of stranding a naked short. +10 tests; 143 existing overlay/Brandon tests green.
- **B4 + B2 + B5 — DONE** (`e6df195`). `scripts/flip_bc_swap.sh` (forward C→B) + `scripts/flip_bc_rollback.sh` (reverse) + `scripts/flatten_paper_account.py` (check-only default; `--execute` market-closes the book). The swap script guards on broker `/health` + fresh ET-day smoke PASS + **overlay-fix-deployed** + **account-flat**, flips `dry_run` AND `alerts.enabled` for both, sets **C dry FIRST** then B live (B5 ordering), and never touches A. The rollback **hard-aborts unless flat** (B2) and points at the flatten helper.
- **C5 — DONE** (`042066e`). Dashboard `PRIMARY_ID` is now `_primary_id()`, which auto-follows whichever Brandon seat is live (`dry_run=false`) — no dashboard code change at flip time. +2 tests.

**Remaining before a flip:** the two sizing DECISIONS (contracts 7-vs-10, cap) in §7; the VM-only coherence edits (agents repoint C2/C3; optional baseline C6); deploy (pull + `__pycache__` clear + restart the two hydra units — **no broker restart**, no `shared/` the broker imports changed); then run `flip_bc_swap.sh` at an EOD-flat window.

---

## 2. HARD BLOCKERS — RESOLVED (status inline below)

### B1 — ✅ FIXED (`d0b5714`, 2026-07-21): the overlay live-order path over-placed 10× and could not unwind

B's defensive overlay (debit spreads / butterflies) has **never placed a real order** (synthetic `DRY_OVERLAY_*` in dry-run). Its live branch has a verified sizing bug:

- `_brandon_place_overlay` loops `for q in range(int(leg.quantity)): self._place_option_order(...)` ([brandon/strategy.py:1660](../../bots/hydra/brandon/strategy.py)).
- `_place_option_order` has **no quantity parameter** ([base_strategy.py:2618](../../bots/hydra/base_strategy.py)) and always places `target_qty = int(self.contracts_per_entry)` = **10** ([base_strategy.py:2756](../../bots/hydra/base_strategy.py)).
- So each "one-contract" iteration places **10** contracts. A butterfly (long 10 / short 20 / long 10 = 40 intended) **attempts 100 / 200 / 100 = 400 real SPX contracts** — 10× oversize. Sim books a clean modeled 40.
- The ORDER-006 cap (`max_contracts_per_underlying=60`) rejects mid-loop once the running total tops 60 → the butterfly's final wing never places → **the filled short block is a naked short ratio** (uncapped-loss short calls / large-loss short puts).
- The overlay path has **NO naked-short detection and NO unwind** (unlike `_execute_entry:2579`) — it only fires a CRITICAL "PARTIAL hedge" alert and leaves the unbalanced position open.

**Impact:** the first threatened afternoon B fires a live butterfly, it cements a real, oversized, partially-naked, mis-tracked (HedgeLeg counts orders ≤10 vs 100 real) position that also breaks POS-003 reconcile and understates settlement P&L 10×.

**Fix applied (option a):** `_place_option_order`/`_place_option_order_ib` now take an optional `quantity` (None ⇒ `contracts_per_entry`, IC path byte-identical; ORDER-006 validates the actual qty). The overlay caller places each leg once at `leg.quantity`, chunked by `max_contracts_per_order`, and is **atomic** — a partial fill unwinds every filled leg via `_brandon_unwind_overlay_legs` → `_flatten_accumulated_partial` (the same opposite-MARKET flatten the IC path uses). Partial-overlay alert reworded (unwound, not tracked). +10 tests (`test_brandon_overlay_live_sizing_2026_07_21`). **Still recommended before the first live overlay:** a paper smoke that actually *triggers* an overlay (an SPX move into an armed side) to exercise the real chunked placement end-to-end.

### B2 — ✅ DONE (`e6df195`): rollback tooling now flattens-first (the bare dry flip does NOT)

`dry_run=true` gates order **placement only**: `_place_option_order` returns None (`base_strategy.py:2664`), `_reconcile_positions` returns immediately (`:5074`), `_handle_naked_short` returns via SAFETY-DRY-02 (`:3596`), recovery skips the broker cross-check (`strategy.py:13457`). So if B holds **any** real open position or working order at flip-back time, the dry bot is structurally incapable of monitoring, stopping, or closing it — it orphans, rides to expiry unmanaged, and the bot books phantom simulated P&L over the top. The realistic worst case chains B1→B2: naked overlay leg → operator flips to dry to "stop it" → orphaned naked short.

**Fix applied:** `scripts/flip_bc_rollback.sh` HARD-aborts unless the broker shows 0 open option positions, and points the operator at `scripts/flatten_paper_account.py --execute` (queries the broker directly, market-closes every open option leg) as the flatten-first prerequisite. Never flips-to-dry with anything open.

### B3 — ⚠️ DECISION (config): the 60-contract cap makes live-B diverge from sim-B + spams CRITICAL alerts

`max_contracts_per_underlying=60` (B config) is enforced ONLY on the real path (`base_strategy.py:3219`, called from `_place_option_order_ib:2726`), never in `_simulate_entry`. With B's denser 4-slot 10c grid, 2 concurrent ICs = 80 counted → the 3rd IC's first leg projects 90>60 → rejected (clean whole-IC skip, no naked leg), but `_initiate_entry` retries up to `ENTRY_MAX_RETRIES=3` → up to 3 CRITICAL "POSITION LIMIT EXCEEDED" circuit-breaker alerts per over-cap slot. B has never hit this (sim is uncapped); C never hit it (cap 180). Live-B would drop the 3rd/4th concurrent IC that sim allowed → real entry-count divergence + loud benign alerts.

**Decision required before flip:** raise B's `max_contracts_per_underlying` to ≥160 (4 concurrent ICs × 40) or C's 180 so it matches sim (safe — after the swap only B places real orders, so no shared-margin competition), OR keep 60 knowingly + downgrade the over-cap alert from CRITICAL to INFO.

### B4 — ✅ DONE (`e6df195`): B flip tooling written (and it never touches A)

`flip_ac_live.sh` covers A+C only and its `flip_one` only moves true→false (can't touch B, can't revert C→true); `flip_a_live.sh` is A-only. Worse, `deploy/broker-paper-smoke.service:23` has `ExecStartPost=+flip_a_live.sh`, so `systemctl start broker-paper-smoke` (the sanctioned way to produce the RB-8 same-ET-day PASS sentinel) would **flip variant A live** as a side effect (A is currently dry). The smoke itself is only a 1-contract buy→close round-trip — it never exercises B's IC leg-in, 4-slot grid, 60-cap, or the overlay path.

**Fix applied:** `scripts/flip_bc_swap.sh` (not `flip_b_live.sh`) does the whole swap — Guard-1 (broker `/health`), Guard-2 (fresh ET-day smoke sentinel), **Guard-3 (overlay fix deployed)**, **Guard-4 (account flat)**, atomic validated JSON writes, C-first ordering, post-restart verify — and touches only B and C. Produce the sentinel by running `scripts/broker_paper_smoke.py --place` **directly** as `calypso` (never the smoke service, whose `ExecStartPost` would flip A).

### B5 — ✅ ENCODED in the swap script: strict EOD-flat, C-first ordering

The one shared IBKR paper OAuth session serves all variants. If B and C are both live even briefly, same-strike 8-delta picks can **merge** on the shared account → double-counted contracts, conflicting stops, phantom P&L. And restarting C into dry mid-session (or before after-hours settlement) strands its real positions + books stale-SPX phantom P&L (`settlement_stale_spx_bug`).

**Hard gate (now enforced in code):** `flip_bc_swap.sh` Guard-4 reads broker positions and aborts if non-zero; STEP 1 flips **C→dry + restart** before STEP 2 flips **B→live**. Still run it only after the 16:00 ET close + after-hours settlement (so the account is genuinely flat). Never intraday, never overlapping.

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
| C1 | **Alerts follow B** ✅ handled by `flip_bc_swap.sh` | HIGH | done | The swap script sets B `alerts.enabled=true` + C `false` atomically with the `dry_run` flip. Confirmed the VM values start B=false/C=true. Alerts self-label by variant (`alert_service.py:403`). |
| C2 | **Agents repoint** (HERMES/CLIO/HOMER `read_db`) | HIGH | easy | VM-only `agents_config.json`: `read_db` `data/variant_c/backtesting.db`→`variant_b`. Else the git-committed canonical journal (HOMER 7:30pm ET) narrates the **simulated** variant forever. Do BEFORE HOMER's auto-commit; verify `git status` clean. |
| C3 | HOMER/HERMES `state_file`+`metrics_file` (latent bug: point at variant **A**) | MED | easy | Repoint to `data/variant_b/*` with C2 (fixes a pre-existing A-scoping bug). |
| C4 | **Emergency-stop runbooks** name `hydra_variant_c` as live | HIGH | easy | Update RB-8, `LIVE_READINESS_CHECKLIST.md:219/223`, CLAUDE.md → `hydra_variant_b`. Else an operator stops the wrong (now-harmless) unit under stress. |
| C5 | Dashboard PRIMARY ✅ DONE (`042066e`) | MED | done | `PRIMARY_ID` → `_primary_id()` auto-follows the live seat (reads config `dry_run`), so the picker re-defaults to B once B flips — no code change at flip time. NOTE: the 6 `config.py` `bot_config_file`/`hydra_*` paths still hard-point at `variant_c/*` (they drive the legacy WS view + `/api/hydra/*`); leaving them is fine for a trade-only swap (reach live-B via the picker). Repoint them only for a *full* primary move (see decision #3). |
| C6 | Dashboard `baseline_date` | MED | trivial | If PRIMARY moves: set `DASHBOARD_BASELINE_DATE`(+`_VARIANT_B_`) to the swap date so B's multi-month **dry-run** history doesn't blend into the "live" cumulative card. |
| C7 | Taxonomy status | MED | trivial | `strategy_taxonomy.py`: b `dry_run_shadow`→`live`, c `live`→`dry_run_shadow`. Display-only, but every surface lies otherwise. |
| C8 | Tests encode primary=C ✅ moot | MED | done | Primary is now dynamic (`_primary_id()`), and its tests assert it follows the live seat — no static `primary_id="c"` assertion to break at flip time. |
| C9 | Docs (README, CLAUDE.md table, deploy/README, GO_LIVE_MASTER slot_edge path) | LOW | easy | Update to B=live/C=sim in the same PR. |
| C10 | ARGUS comment says C is live | LOW | trivial | Comment-only (it watches both units) — update for clarity. |
| C11 | GCS backup doesn't cover the live variant's DB | LOW | easy | Pre-existing; add `data/variant_<live>/backtesting.db` to `db_backup.sh`. |
| C12 | Google Sheets ownership | — | easy | If Sheets writes are active, mirror `google_sheets.enabled` + `spreadsheet_name` C→B. |
| C13 | HOMER auto-commit hygiene | — | easy | Land all VM edits + verify `git status` clean on `/opt/calypso` before 7:30pm ET (HOMER `git add -A && push`). |

**Confirmed NON-issues:** the systemd units (no `--dry-run` flag), the Telegram command poller (env-gated to **A** via `is_default_variant`, unaffected), `/compare` (group_id-scoped), and `calypso-broker` (no `shared/` import changes → **no broker restart**).

---

## 5. Gated execution sequence (ONLY after §2 blockers cleared)

1. **Pre-flight (done in code):** the overlay fix (B1), swap/rollback/flatten tooling (B2/B4/B5), and dashboard auto-follow (C5) are committed. Decide §7 #1–2 and set them in `config_variant_b.json` on the VM. Deploy: `git pull` + `__pycache__` clear + `systemctl restart hydra_variant_b hydra_variant_c dashboard` (**no broker restart**). Full suite green.
2. **Same ET day:** run `scripts/broker_paper_smoke.py --place` **directly** as `calypso` (NOT `systemctl start broker-paper-smoke`, whose `ExecStartPost` flips A) → PASS sentinel. Confirm broker `/health` connected.
3. **After 16:00 ET close + after-hours settlement, both flat:** run `sudo scripts/flip_bc_swap.sh`. It self-guards (broker health, fresh sentinel, overlay-fix-deployed, account-flat), flips **C→dry+alerts-off (STEP 1, restart)** then **B→live+alerts-on (STEP 2, restart)**, verifies, and Telegrams the result. A untouched.
4. **VM-only coherence:** repoint agents (C2/C3) + optional baseline (C6) per §7 #4–5. Verify `git status` clean on `/opt/calypso` before HOMER's 7:30pm ET commit.
5. **Day-1 observation:** watch `logs/hydra_variant_b/bot.log` for ORDER-006 rejects (cap), the 09:45-slot delta-target strike quality (early-session Polygon greeks C never fed live), any POS-003 mismatch, and — the first time an overlay arms — the **chunked live overlay placement** (`BRANDON-OVERLAY … x<chunk>` lines; a PARTIAL should auto-unwind).

## Rollback (flatten-first — enforced by the script)

`scripts/flip_bc_rollback.sh` — hard-aborts unless the account is flat. If B has open positions: `scripts/flatten_paper_account.py --execute` (broker-direct market close) FIRST, confirm flat, then the rollback flips **B→dry (STEP 1)** then **C→live (STEP 2)**. Easiest at EOD when 0DTE has settled on its own. Then revert the VM-only coherence edits (agents/baseline).

---

## 6. Difficulty verdict — UPDATED (post-fix, 2026-07-21)

The hard part (B1 overlay fix + tooling) is **done, tested, committed**. The full-fidelity swap is no longer a ~2–4 day build — it collapses to:

1. **Decide** contract size (7 vs 10) + cap (§7 #1–2) — minutes.
2. **Deploy** the committed code: `git pull` + `__pycache__` clear + restart the two hydra units. **No broker restart** (no `shared/` the broker imports changed — only `base_strategy.py`/`brandon/strategy.py`/dashboard, which the strategy processes load). ~10 min.
3. **Same ET day:** `broker_paper_smoke.py --place` directly → PASS sentinel; confirm broker `/health`.
4. **After the close, both flat:** run `scripts/flip_bc_swap.sh` — one guarded command does the whole swap.
5. **VM-only coherence:** repoint agents (C2/C3); optional baseline (C6). Verify `git status` clean before HOMER's 7:30pm commit.

So: **a well-understood ~half-day of deploy + one gated EOD command, not a multi-day build.** The overlay-OFF shortcut is now moot — it was only attractive to dodge B1, which is fixed; and the user's requirement ("execute exactly like it does now") *needs* overlays on.

## 7. Open decisions for the user (the only things left before a flip)

1. **B's contract size when live: keep `contracts_per_entry=10` ("exactly like B runs now") or drop to `7` (match C's current live risk)?** B does NOT inherit C's 7 — it uses its own config. 10 = higher paper risk per entry but true-to-sim; 7 = conservative parity with today's live seat. *(Set in `config_variant_b.json` before the flip; the swap script does not touch it.)*
2. **B's `max_contracts_per_underlying`:** raise to ~160–180 (let B run its denser grid like sim + no CRITICAL cap-alert spam) or keep 60 (throttle live-B below sim, accept the divergence + benign alerts)? Interacts with #1 (at 7c the effective contract totals are lower).
3. **Full primary move vs trade-only:** the picker already auto-follows to B (C5 done). Do you also want the 6 `config.py` canonical paths + the legacy WS `/api/hydra/*` view repointed to B (full move), or is "reach live-B via the picker, C stays the config-default" fine?
4. Repoint the analyst agents/journal (HERMES/CLIO/HOMER `read_db`) to `data/variant_b/*` so the canonical record follows the live seat — yes? *(VM-only `agents_config.json` edit; do before HOMER's commit.)*
5. Reset B's dashboard baseline to the swap date so its dry-run history doesn't present as the live record — yes?

**Recommendation:** given B's edge is still one-pin-deep on ~14 days (see the earlier both-sided audit), do the deploy now so we're *ready*, but treat the actual flip as its own deliberate go — ideally after a bigger clean B sample. #1: I'd lean **7** to keep live paper risk at today's level while B proves out. #2: raise to ~160 so live-B mirrors sim. #3: trade-only (picker) is enough for now. #4/#5: yes.
