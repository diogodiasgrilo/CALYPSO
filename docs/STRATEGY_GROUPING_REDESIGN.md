# Strategy Grouping Redesign + Second Calendar Strategy — Design

> **Status:** DESIGN / pending approval + audit. No code written yet.
> **Date:** 2026-06-16.
> **Why:** (1) Add a second multi-day calendar strategy (a sibling of Strategy D), and (2) stop
> identifying strategies by bare letters A/B/C/D/E everywhere — replace with human names + *comparability
> groups* so the dashboard/Telegram/email/DB/logs compare apples-to-apples, and give the main dashboard
> a picker so it can render any strategy (today it is hardcoded to variant C).
> **Method:** synthesized from 5 parallel expert design passes (taxonomy, dashboard FE/BE, comms,
> data, new-strategy). Follows [`NEW_STRATEGY_PLAYBOOK.md`](NEW_STRATEGY_PLAYBOOK.md) (Step 0 row 3 — sibling/reuse build).

---

## 0. The one invariant that makes all of this safe

**The variant letter (`a`/`b`/`c`/`d`/`e`) stays the stable internal key — forever.** Filesystem
(`data/variant_<letter>/`, `logs/hydra_variant_<letter>/`), env (`HYDRA_VARIANT_ID`), `BOT_NAME`,
alert-wire `bot_name`, systemd units, DB file locations — **none change, no data migration.** Human
*names* and *groups* are a **presentation/semantics overlay** layered on top of that key. This is why
the whole redesign can ship without restarting or touching live variants A and C.

---

## 1. The linchpin: a central strategy taxonomy

**New module `shared/strategy_taxonomy.py`** — stdlib-only, dependency-free (so the trading process,
`shared/`, AND the dashboard backend can all import it with no cycles and no heavy deps). It is the
single source of truth every surface reads from.

```
StrategyMeta(frozen):
  id              # "a".."e" — the unchanged letter key
  display_name    # "HYDRA Baseline" / "Brandon Narrow (live)" / "DC Time Machine" / <new>
  short_name      # compact tag for logs/widgets, e.g. "DCTM-D"
  strategy_class  # registry key: "hydra"/"brandon"/"double_calendar"/<new>
  group_id        # FK -> GroupMeta
  structure_family# "iron_condor" | "double_calendar"
  pnl_shape       # "credit" | "debit"   <-- forbids credit-vs-debit comparison
  dte_class       # "0DTE" | "multi_day"
  status          # "live" | "dry_run_shadow" | "dry_run_locked"
  bot_name_base   # the BOT_NAME class const it runs under ("HYDRA"/"DCTM"/<new>)

GroupMeta(frozen): id, label, pnl_shape, comparable(bool)  # members derived from STRATEGIES

helpers: variant_id(), is_default_variant(), meta(vid), group(vid), members(group_id),
         comparable_with(vid), bot_name(vid)   # ONE canonical env-parse + name builder
```

**Initial groups + members:**

| group_id | label | pnl_shape | members |
|---|---|---|---|
| `ic_0dte` | "0DTE Iron Condor" | credit | a, b, c |
| `calendar_multiday` | "Multi-day Calendar" | debit | d, e |

| id | display_name | strategy_class | group | structure_family | pnl_shape | dte_class | status |
|---|---|---|---|---|---|---|---|
| a | HYDRA Baseline | hydra | ic_0dte | iron_condor | credit | 0DTE | dry_run_shadow |
| b | Brandon Narrow (4-slot) | brandon | ic_0dte | iron_condor | credit | 0DTE | dry_run_shadow |
| c | Brandon Narrow (live) | brandon | ic_0dte | iron_condor | credit | 0DTE | live |
| d | DC Time Machine | double_calendar | calendar_multiday | double_calendar | debit | multi_day | dry_run_locked |
| e | *(new — TBD name)* | *(new class)* | calendar_multiday | double_calendar | debit | multi_day | dry_run_locked |

This **replaces ~5 scattered hardcoded "is this strategy comparable?" checks** (the `if vid=="d": continue`
guards in `strategy.py`, the `_VARIANT_IDS=["a","b","c"]` list, the `main.py` banner `if variant=="d"`,
etc.) with one data structure. Comparison refuses credit-vs-debit *by construction*, not by scattered guards.

**Two guardrails the taxonomy MUST have** (audit AUD-1 / AUD-4 — the table is otherwise *unverified hearsay*):
1. **Fail-stop validation.** The real letter→class binding is computed at runtime by
   `registry.resolve_strategy_name(config)` from each variant's own (VM-local, `skip-worktree`'d) config —
   via *two different mechanisms* (`brandon.enabled` for B/C vs `strategy.name` for D). A static table can
   therefore lie. At startup, assert `resolve_strategy_name(config) == meta(variant_id()).strategy_class`
   and **fail-stop** on mismatch (the bot already raises on config-validation failure). Never trust the table over the resolver.
2. **Unknown-letter contract.** `meta()` for a letter not in the table must return a safe sentinel
   (`comparable=False`, excluded from all groups, generic banner) — **never `KeyError`**. Today an unregistered
   letter degrades gracefully (`main.py` banner `else`, discovery includes it); hard table lookups must preserve that.

---

## 2. Per-surface design

### 2a. Dashboard (the big one)

**Backend — new data-driven API (additive; keep `/api/variants/*` + `/api/dc/*` as back-compat adapters):**
- `GET /api/strategies/meta` — the boot payload: groups, members, per-strategy `{display_name, group_id,
  family, pnl_shape, data_kind, is_live, is_primary, capabilities{}, available}`. **No file paths exposed.**
- `GET /api/strategies/{id}/snapshot` — parameterized main-page payload for ANY strategy (the envelope
  carries `data_kind` so the FE picks the renderer: `ic_state` vs `dc_calendar`).
- `GET /api/strategies/groups/{group_id}/comparison` + `/aggregate` — **group-scoped**; returns one
  `pnl_shape`/`family` per group, baseline from group metadata (not hardcoded "A"). Thin adapters over
  the existing `variants.py` helpers (scoped to `member_ids`) + a new calendar reader (§2d).
- New calendar reader `DCDBReader` is **SHAPE-DISTINCT, not IC-shaped reuse** (audit AUD-3-F2 / AUD-4-F4 —
  this corrects the original "reuse the IC math unchanged" claim, which is *false*: the calendar DB has no
  `daily_summaries`, no per-day `net_pnl`, no spread-width columns, and is multi-day-keyed). It computes
  debit-native: capital basis = `SUM(net_debit)`; realized P&L synthesized per day **attributed to `close_date`**
  (not `entry_date` — entry-date attribution makes the cumulative curve retroactively mutate as positions
  close); and it **omits** the credit-only fields entirely (`total_credit`, buffer-margin, spread cushion) rather
  than null-filling them as misleading `$0`/`100%`. The group comparison endpoint returns a **different payload
  schema per `family`**; a test asserts the calendar payload carries **no** IC keys.

**Frontend — data-driven, no hardcoded letters:**
- `useStrategyMeta()` hook fetches `/api/strategies/meta` once (replaces the ad-hoc `useComparisonEnabled`
  / `useDcEnabled` probes in `App.tsx`).
- **Main-dashboard picker** — `StrategyPicker.tsx` in the Header (grouped dropdown of strategies whose
  `capabilities.main_dashboard`), selection in the Zustand store, persisted to `localStorage`
  (`calypso-selected-strategy`), default = `is_primary`. `Dashboard.tsx` becomes a thin
  `<StrategyDashboard>` that dispatches by `data_kind`: the current IC layout → `IronCondorDashboard.tsx`,
  the DC body → `CalendarDashboard.tsx` (extracted from `DoubleCalendar.tsx`).
  - Primary IC strategy keeps the **live WebSocket** fast-path (zero regression). Non-primary selection
    starts with **polling** `/api/strategies/{id}/snapshot` (proven by Comparison's 2s poll); WS-subscribe
    is a later enhancement.
  - **The whole Header chrome must re-bind to the selection** (audit AUD-3-F1, CRITICAL). Today the single
    WebSocket + `useBotConfig` singleton hard-bind the label, dry-run banner, SPX/VIX prices, and WS stop-toasts
    to the primary (C) at construction. A picker that changes only the body would leave the header showing C's
    label/banner and — worst — **C's SPX/VIX (the wrong underlying for a QQQ strategy)** plus C's stop-toasts
    while the body shows E. So the `/api/strategies/{id}/snapshot` envelope must carry the full header chrome
    (`display_name`, `dry_run`, `underlying_symbol`, a header price source), and the Header must read from the
    selected strategy, not the WS store.
  - **Picker scoped to the Dashboard tab only** until History/Analytics are parameterized (audit AUD-3-F4):
    those pages are hard-bound to canonical-C readers (`metrics.py`), so a global picker that silently leaves
    them on C is a "control that lies." Either hide/disable the picker off the Dashboard tab with a visible
    "History shows <primary>" notice, or parameterize the metrics routers by `?strategy=<id>` first.
- **Group tabs** replace A/B/C/D tabs: `GroupComparison.tsx` at `/comparison/:groupId`, one tab per
  comparable group, branching renderer by `family`/`pnl_shape`. A single chart NEVER receives two
  `pnl_shape`s — enforced server-side (one family per group endpoint) AND client-side.
- A `pnlShape` config centralizes axis labels/zero-reference/colors for credit vs debit; accents come
  from metadata (removes positional `VARIANT_ACCENTS`).

### 2b. Telegram
- Headers + push-alert titles render `display_name · group_label` (e.g. `Iron Condor (C) · 0DTE Iron Condor`)
  via **additive payload fields** (`display_name`/`group_label`), falling back to `bot_name` when absent.
- `/compare` becomes **group-scoped**: bare `/compare` = the poller's group `{a,b,c}` (zero behavior change);
  `/compare calendars` compares `{d,e}` with a calendar-native renderer. `/calendars` becomes an alias.
  The `if vid=="d"` exclusion hack is removed (D is simply in its own group now).
- Per-strategy commands (`/status`, `/snapshot`, `/entry`, `/stops`) gain an **optional name/letter selector**;
  owning variant A reads in-process (live), others read their state file (labeled with mtime staleness).
- **Poller stays variant-A-only.** No new consumer of the bot token.

### 2c. Email
- Subject: `[prefix] DisplayName (Letter): title` (keeps the letter so existing filters survive).
- **Anti-spam is byte-identical** — gating stays in `AlertService` keyed on the unchanged internal
  `bot_name` (which is the **dedup partition key**, not a label — audit AUD-1-H1: it must be FROZEN, or two
  variants merge dedup namespaces / in-flight dedup state resets). No new alert types/volume.
- **Display rename is NOT "in the Cloud Function"** (audit AUD-4-secondary): the Cloud Function is a
  standalone GCP deploy that **cannot import `shared/strategy_taxonomy.py`**. So `AlertService.send_alert`
  must attach `display_name`/`group_label` as **additive payload fields** (touches `alert_service.py` + its
  call sites — not free); the CF renders them and **falls back to `bot_name`** when absent. The alert-wire
  `bot_name` itself never changes.

### 2d. DB + recorder
- New strategy gets its **own** `data/variant_e/dc_calendar.db`, **reusing D's calendar schema** (one
  `DCDataRecorder`, one reader, one schema version). The transformer-only columns/`dc_transformations`
  table stay **present-but-empty** for E (additive, free; readers treat `transform_credit`/`is_risk_free`
  as nullable detail). E sets a distinct `structure` literal (e.g. `"double_calendar_managed"`) so rows
  self-identify even if read together.
- `shared/data_recorder.py` (the IC `backtesting.db`) is **untouched** — the whole point of the isolated-DB
  pattern.
- Comparison/aggregate become **group-iterating**: pick `BacktestingDBReader` (credit) or `DCDBReader`
  (debit) per member's `structure_family`; one leaderboard per group; **never sum credit + debit**. Calendar
  P&L is attributed to **`entry_date`** for H2H alignment; capital basis = **`net_debit`** (not spread-width
  notional).

### 2e. Terminal logging
- The `bot_name` column in `monitor.log` stays **byte-for-byte unchanged** (journald greppability +
  HOMER's parser depend on it). Enrichment goes into the **banner + startup lines** (display_name + group
  + letter for every variant, not just D) — driven by the taxonomy.
- **HOMER coupling (tightest constraint):** the Sheets Entry `action` is a **hardcoded literal `"HYDRA Entry #N"`**
  (`strategy.py:10205`, NOT `BOT_NAME`-derived) and HOMER's Entry regex anchors on it (`data_collector.py:139`).
  So Phase 2's "name derivation" must **NOT** replace that literal with a taxonomy name (audit AUD-1-H2 — it
  would silently drop every IC entry from the journal). E has Sheets **disabled** (dry-run), so no conflict today.
  Correction to an earlier claim: **not all** Stop regexes are prefix-agnostic — `_build_stop_records`
  (`data_collector.py:1478`) is anchored on `(?:HYDRA|MEIC)` too (audit AUD-4-F2), so if any calendar ever logs
  Stops to Sheets, that regex needs widening in lockstep as well.

---

## 3. The new strategy — `CalendarStrategyBase` + the second calendar

Approach **B** (playbook Step-0 row 3): extract a shared base, then build the sibling.

```
HydraStrategy                       (UNTOUCHED)
   └── CalendarStrategyBase         (NEW — the shared lift from D)
          ├── DoubleCalendarStrategy   (D — keeps transformer + 20%-debit-stop; byte-identical)
          └── <NewCalendarStrategy>    (NEW — managed-exit calendar, NO transformer)
```

**`CalendarStrategyBase` gets the SHARED machinery** (lifted verbatim from D, names preserved): dry-run
lock scaffolding, two-expiry data layer (`_resolve_calendar_legs`, IV reads, `_em_otm_distance`, quote/
realtime helpers, the VM data probe), entry simulation skeleton (`_calculate_strikes` delegating to a
`_select_strike` hook, `_simulate_entry`, `_pre_entry_gates` + an `_extra_entry_gates` hook,
`_initiate_entry`), sidecar persistence (serialize/load/save/recover), the isolated-recorder hook,
multi-day lifecycle (`_reset_for_new_day`, capital/max-loss/`get_daily_summary`/`get_detailed_position_status`,
`get_monitoring_mode` skeleton via a `_vigilance_reasons` hook), and close/settlement primitives
(`_refresh_marks`, `_close_calendar`, `_settle_due`, CALENDAR-leftover settlement). `CalendarEntry`/`DCPhase`
and `calendar_chain.py` are **reused as-is** (E only walks `CALENDAR → CLOSED`; `OPEN_PHASES` is an
overridable class attr).

**D keeps (strategy-specific):** the transformer (`_attempt_transform`), its `_manage_calendar`
(transform → 20%-debit-stop → EOD), delta-band strike search, DTE-window expiry picker, the TRANSFORMED
settlement branch, its knobs. D becomes a thin subclass; behavior **byte-identical**.

**The new strategy supplies (its genuinely new logic — LOCKED from the transcript, see §6):**
- **Structure** — a DOUBLE calendar (the creator's "ultimate strategy", >80% win rate): a call calendar
  *above* spot + a put calendar *below* spot, net debit, **same strike per leg**. (A single ATM calendar is
  the degenerate neutral case.)
- **Expiry picker** — short leg ≈ **35 DTE**; long leg = short **+ ~1 week** (same strike). Absolute-DTE +
  small gap (not D's short-window/day-of-week picker).
- **`_select_strike`** — **expected-move** based: call leg ≈ spot **+1× the short-expiry EM**, put leg ≈ spot
  **−1× EM** (neutral = ATM). EM derived from IV (reuses `_em_otm_distance`); zero/low greeks reads.
- **Entry IV gate** — enter only when **IV is at the low end** (below ~1-yr median / low IV-rank), expecting
  mean-reverting IV to rise (calendars are positive vega); **skip high IV and pre-earnings** (vol-crush is the
  named risk). Needs an IV-history/rank source (new — D has none).
- **`_manage_calendar`** — **laddered early profit-taking**: scale out across contracts as profit rises
  (e.g. 10c → 1/1/2/3 at increasing levels); partials early are wins (20-30%+); **never hold to expiry**.
  **No hard stop** — defined risk = the debit; size accordingly (the creator explicitly uses no stop on
  calendars, sizing smaller instead). This is the management divergence from D.
- **Assignment handling (SPY-specific, NET-NEW vs D)** — SPY options are **American / physically settled**:
  the short near leg can be **early-assigned** if ITM (esp. around ex-dividend for calls). Must exit before
  the short expiry and cover any assignment with the long leg. D has none of this (SPXW = European,
  cash-settled). **This is a go-live gate, not a dry-run concern.**
- Own config (`config_variant_e.json`, committed, dry-run-locked, alerts/sheets off), registry row,
  `deploy/hydra_variant_e.service`, recorder, status reader + `/api/.../status`, and tests.

> **Spec LOCKED from the video transcript (2026-06-16).** Source: OptionsKit "calendar spread / double
> calendar" video (`GuI-hH_jhlg`). **Ticker = SPY** (every worked example; also demoed on MSFT). ⚠️ **SPY ≠ the
> SPX/SPXW-native stack**: SPY is American-exercise + dividend-paying, so the build needs SPY market-data/chain
> plumbing (SPY is reachable — it's the probe *control* instrument) **and** early-assignment handling — the
> single biggest delta vs D, and a go-live gate. The strategy generalizes to SPX cleanly if SPY's added infra
> isn't wanted, but the video says SPY.

**Critical safety step — TWO commits, not one** (audit AUD-2, which empirically verified 135/135 of D's
tests stay green under a faithful lift):
- **Commit A — pure verbatim lift (the gate).** Move D's SHARED methods into `CalendarStrategyBase` under
  their **real current names** (`_dc_manage_calendar`, `_dc_pre_entry_gates`, `_dc_pick_delta_strike`, the
  inline `get_monitoring_mode` thresholds, etc. — the override "hooks" like `_select_strike`/`_vigilance_reasons`
  **do not exist in D** and must not be invented here). Keep `__init__` ordering exactly (dry-run lock →
  `_dc_loaded=False` → `super()` → knob reads); parameterize only the lock *message* + config namespace via class
  attrs. The 5 methods using bare `super()` (`get_daily_summary`, `_reset_for_new_day`, `_save_state_to_disk`,
  `_recover_positions_from_saxo`, `check_after_hours_settlement`) must be moved **as source text** (not copied),
  or their `__class__` cell breaks. Gate: **D's full suite passes with ZERO edits to D's test files.** That is the proof.
- **Commit B — introduce E's override seams** (`_select_strike`, `_extra_entry_gates`, `_vigilance_reasons`,
  `_manage_calendar`, and the transformer-coupling guards in `_dc_simulate_entry`'s wing stamp /
  `_dc_open_debit_at_risk` / `_dc_settle_entry`), each with its own tests. D's behavior stays identical because
  its overrides reproduce the old inline values.

---

## 4. Phased implementation plan (each phase = its own commit(s) + tests; nothing un-gated touches live A/C)

0. **Confirm the video facts** (ticker + management rules) and lock the open decisions in §6.
1. **`shared/strategy_taxonomy.py` + tests** — pure data + import-purity guard. Nothing consumes it yet.
2. **Wire the bot to the taxonomy** (additive): `main.py` env-parse/banner/bot_name, `strategy.py` discovery
   filter, `base_strategy.py`/`logger_service.py` name derivation. A/B/C/D behavior unchanged. **+ audit:** add
   the fail-stop `strategy_class` validation + unknown-letter sentinel (§1 guardrails); add a test that exactly
   one variant starts the Telegram poller (poller stays gated on the raw `HYDRA_VARIANT_ID` env, NOT a taxonomy
   field); do NOT touch the hardcoded `"HYDRA Entry"` Sheets literal.
3a. **Extract `CalendarStrategyBase` — pure verbatim lift** (the gate): D byte-identical, real `_dc_*` names,
    `super()` methods moved as source, **D's full suite green with zero test edits**.
3b. **Introduce E's override seams** (separate commit, own tests): `_select_strike`, `_extra_entry_gates`,
    `_vigilance_reasons`, `_manage_calendar`, transformer-coupling guards. D unchanged.
4. **Build the new strategy** (Steps 1–8 of the playbook, reuse-driven): class, config, registry, systemd unit,
   recorder (**reuse `DCDataRecorder`**, distinct `structure`), status reader, tests. Dry-run-locked **with D's
   un-flippable constructor lock** (not a config flag). **+ audit:** add E to the `/compare` exclusion +
   `_VARIANT_IDS` non-inclusion **in this phase** (when `data/variant_e/` first appears) — not deferred to Phase 7.
5. **Dashboard backend**: `/api/strategies/*` + a **shape-distinct** `DCDBReader` (debit-native; no IC-field
   leakage; P&L by `close_date`) + group-scoped comparison. Register `/api/strategies` under `_api_guard`; tests
   for read-only + missing-file tolerance + "no IC keys in calendar payload." Old endpoints preserved.
6. **Dashboard frontend**: `useStrategyMeta`, `StrategyPicker`, `StrategyDashboard` dispatch, `GroupComparison`
   tabs, shape-aware renderers. **+ audit (blocking):** decide the per-strategy live-state model (header chrome
   re-binds to the selection via the snapshot envelope; stop-toasts don't cross strategies); scope the picker to
   the Dashboard tab until History/Analytics are parameterized. `tsc -b` + `vite build` clean.
   **+ operator request (2026-06-16): EOD auto-update.** The MAIN dashboard's **cumulative section** and every
   other end-of-trading-day field must refresh **automatically the moment the trading day ends** — today they
   only update on a manual page reload. The backend already polls `hydra_metrics.json` (10s) + `backtesting.db`
   (30s); the gap is that the cumulative/summary widgets fetch once on mount and don't react to the end-of-day
   write. Fix: emit a settlement/EOD-complete event over the WebSocket (or have the metrics poll detect the
   daily-summary write) so the main page re-fetches cumulative metrics + the daily summary live at close, no
   reload. Applies per selected strategy.
7. **Comms**: Telegram group-scoped `/compare` + selectors (requires threading `text` into 4 handler signatures —
   not purely additive) + a **calendar-native summary reader** (reads the DC sidecar, not `hydra_state.json`);
   `AlertService` adds `display_name`/`group_label` **payload** fields (CF renders + falls back to `bot_name`);
   banner/startup enrichment. Anti-spam fingerprint + alert-wire `bot_name` + HOMER anchors FROZEN.
8. **Hardening** (playbook Step 9): adversarial multi-agent audit (done once at design — re-run on the diff),
   live VM probe (esp. ticker/data entitlement if non-SPX), and a **hard go/no-go shared-broker req/s gate
   measured under worst case (all 5 variants in vigilant mode at once)** — not a post-hoc check.
9. **Go-live docs** (playbook Step 10): scope+audit (GO/NO-GO), MVL plan, runbook. **E inherits D's three
   coexistence MUST-FIXes (STATE-004 overnight / orphan sweep / BP budget) as GO-LIVE GATES** — ideally landed
   ONCE as a per-variant-scoped guard in `CalendarStrategyBase` so D and E share it. Strategy stays
   dry-run-locked until the gate passes.

---

## 5. Back-compat / coexistence guarantees (must hold every phase)
- Letter stays the key; no data migration; no path/env/`BOT_NAME`/alert-wire-name changes.
- `/api/variants/*` and `/api/dc/*` stay working (new endpoints are additive adapters).
- Telegram poller stays variant-A-only; bare commands behave exactly as today.
- Anti-spam fingerprint + email routing unchanged; HOMER Sheets/log parsing unchanged.
- IC `backtesting.db` schema untouched; new strategy isolated in its own DB.
- New variant: `alerts`/`sheets` off, `api_pacing_multiplier` set, excluded from the 0DTE `/compare`,
  zero-greeks entry — keeps the shared broker under budget. The go-live MUST-FIXes (STATE-004 / orphan /
  BP scoping) remain gates (irrelevant while dry-run-locked).

---

## 6. OPEN DECISIONS (need answers before/within Phase 0)

1. **Underlying ticker — RESOLVED: SPY** (transcript, 2026-06-16). The video's every worked example is SPY
   (plus one MSFT). SPY is American-exercise + dividend-paying, NOT the SPX/SPXW-native stack — so this carries
   net-new SPY data plumbing + early-assignment handling (go-live gate). **Operator confirmed SPY exactly
   (2026-06-16)** — Phase 4 includes SPY market-data/chain plumbing + American early-assignment + dividend
   handling. (SPX-adapt was offered and declined; the video says SPY.)
2. **Management rules — RESOLVED** (transcript): double calendar; short ≈35 DTE, long +~1wk; ±1×EM strikes;
   low-IV entry gate; laddered early profit-taking; no hard stop; never hold to expiry. (Superseded the earlier
   Theta-Profits/Ahuja placeholder numbers, which were a different creator.)
3. **New strategy name + registry key + `BOT_NAME`** — proposal pending the source (avoid "Theta" if it's
   OptionsKit). Variant letter `e`.
4. **Group id label** — `calendar_multiday` ("Multi-day Calendar") chosen; confirm.
5. **Recorder** — reuse `DCDataRecorder` with transformer table kept-empty + distinct `structure` (chosen),
   vs a trimmed recorder.
6. **Non-primary main-dashboard live updates** — polling first (chosen) vs WebSocket-subscribe now.

---

## 7. Provenance
Synthesized 2026-06-16 from 5 parallel expert design passes: strategy-taxonomy, dashboard FE/BE,
Telegram/email/logging, DB/recorder/comparison, and new-strategy/`CalendarStrategyBase`. Grounded in the
current code (file:line refs in the agent transcripts). **Adversarially audited 2026-06-16 — see §8.**

---

## 8. Adversarial audit findings (2026-06-16) — folded into the design above

Four adversarial auditors attacked the design against the real code (one empirically performed the
`CalendarStrategyBase` lift). **Verdict: the architecture is sound and buildable — no blockers.** The
findings below are corrections/clarifications already folded into §§1–4; they are recorded here as the
authoritative register and supersede any conflicting earlier phrasing.

| ID | Sev | Finding | Resolution (where applied) |
|---|---|---|---|
| AUD-1-C1 | **Crit** | E is a *second* multi-day debit strategy; the only thing protecting live C is "E places no real orders." D's three coexistence MUST-FIXes are account-wide and would trip C when E goes live. | E inherits D's MUST-FIXes as **go-live gates**, landed once as a per-variant-scoped guard in `CalendarStrategyBase`; E gets D's **un-flippable constructor dry-run lock**. (§3, §4, §9) |
| AUD-3-F1 | **Crit** | Single WebSocket + `useBotConfig` singleton hard-bind the *entire header* (label, dry-run banner, SPX/VIX, stop-toasts) to primary C. A body-only picker shows the **wrong underlying** + cross-strategy toasts. | Snapshot envelope carries header chrome; header re-binds to selection; per-strategy live-state model is a **blocking** Phase-6 decision. (§2a, Phase 6) |
| AUD-3-F2 | **Crit** | `DCDBReader` "returns IC-shaped rows, reuse the math unchanged" is **false** — the calendar DB has no `daily_summaries`/`net_pnl`/spread-width and is multi-day-keyed. | `DCDBReader` is **shape-distinct** (debit-native capital = `SUM(net_debit)`, no credit/buffer fields); per-family payloads; test for no IC-key leakage. (§2a, Phase 5) |
| AUD-1-H1 | High | `bot_name` is the anti-spam **dedup partition key**, not a label — renaming it merges/​resets dedup state. | Alert-wire `bot_name` **FROZEN**; display via additive payload fields. (§2c) |
| AUD-1-H2 | High | HOMER's Entry anchor is the **hardcoded literal `"HYDRA"`** (`strategy.py:10205`); Phase-2 "name derivation" could silently break the journal. | Do-not-touch the literal; widen HOMER regexes only in lockstep. (§2e, Phase 2) |
| AUD-1-H3 | High | Shared-broker req/s for 5 variants is asserted, not measured; vigilant-mode stop checks aren't paced. | Hard go/no-go req/s gate under worst-case concurrency. (Phase 8) |
| AUD-2-F1/F2 | High | The override "hooks" (`_select_strike`, etc.) **don't exist in D**; `_dc_simulate_entry`/`_open_debit_at_risk`/`_settle_entry` are transformer-coupled, not cleanly shared. | Two-commit split: verbatim lift (real names) → then seams. (§3, Phase 3a/3b) |
| AUD-3-F4 | High | Global picker but History/Analytics stay C-bound = "control that lies." | Picker scoped to Dashboard tab until metrics routers are parameterized. (§2a, Phase 6) |
| AUD-4-F1 | High | Static `strategy_class` can lie vs runtime `resolve_strategy_name` (two mechanisms; VM configs are `skip-worktree`'d). | Startup fail-stop validation + unknown-letter sentinel. (§1 guardrails, Phase 2) |
| AUD-4-F4 | High | `entry_date` P&L attribution makes the cumulative curve retroactively mutate; capital basis can't reuse the width SQL. | Attribute to `close_date`; reimplement `net_debit` capital basis. (§2a) |
| AUD-2-F3/F7 | Med | `__init__` lock message is D-specific; 5 `super()` methods break if copied not moved-as-source. | Parameterize lock message via class attr; move as source. (§3) |
| AUD-4-secondary | Med | The Cloud Function **can't import** the taxonomy — "display rename in the CF" is impossible. | `AlertService` adds the payload fields; CF falls back to `bot_name`. (§2c) |
| AUD-4-F5 | Med | Telegram selectors need 4 handler-signature changes (not "additive"); removing the `if vid=="d"` guard without a calendar-native summary reader renders D's `hydra_state.json` as IC garbage. | Thread `text` into the 4 handlers; add a calendar-native summary reader. (Phase 7) |
| AUD-1-M1 | Med | `/compare` auto-discovers `data/variant_e/` the moment Phase 4 creates it. | Add the E exclusion in Phase 4, not Phase 7. |
| AUD-1-M2 | Med | Poller ownership must stay on the raw env check, not a taxonomy field. | Keep raw `HYDRA_VARIANT_ID` gate + a one-poller test. (Phase 2) |
| AUD-3-F5 | Med | Single-axis credit/debit guarantee is endpoint-only; the chart components are credit-shaped. | `pnlShape` is a hard input to every chart; client-side mismatch assertion. (§2a) |

**Adjudicated agent disagreements:** group id = **`calendar_multiday`** (don't name a group after one member's
structure); recorder = **reuse `DCDataRecorder`** (transformer table kept-empty + distinct `structure`) — note
`dc_calendar_snapshots` lacks a `structure`/`date` column, so drop the "self-identifies if DBs are ever merged" claim.

**Provenance of the audit:** 4 adversarial agents (coexistence/safety, `CalendarStrategyBase` refactor, dashboard
FE/BE, taxonomy/comms/data). The refactor auditor empirically ran D's 135 tests against a hand-built lift: 135/135 green.
