# Adding a New Strategy — Playbook & Audit Checklist

> **Purpose.** A repeatable, *auditable* procedure for adding a new trading strategy to HYDRA as a
> dry-run-locked variant, the way Strategy D ("DC Time Machine", `double_calendar`) and the short
> `strangle` were added. Follow it top-to-bottom for a new strategy; audit a completed (or
> in-flight) strategy against it by checking each gate.
>
> **This is the operator/engineering process doc.** The *worked example* it is distilled from is
> Strategy D — its go-live artifacts ([`docs/migration/D_GOLIVE_RUNBOOK.md`](migration/D_GOLIVE_RUNBOOK.md),
> [`D_GOLIVE_SCOPE_AND_AUDIT.md`](migration/D_GOLIVE_SCOPE_AND_AUDIT.md),
> [`D_MVL_PHASE1_PLAN.md`](migration/D_MVL_PHASE1_PLAN.md)) are the canonical templates for *this*
> strategy's go-live docs. See [Appendix A](#appendix-a--strategy-d-as-the-worked-example) for D's
> exact commit-by-commit trail.

---

## How to use this document

- **Building a strategy:** work the steps in order. Each step is one commit (or a small cluster),
  ships its own tests, and has a **Done when** gate you must satisfy before moving on.
- **Auditing a strategy:** for each step, confirm the gate holds in the actual code. Record the
  result in the [Audit Log](#audit-log) at the bottom. A failed gate is a finding, not a footnote.
- **The two hard invariants that hold for the ENTIRE build** (every step, every commit):
  1. **The live variants (A/B/C) stay byte-identical.** No destructive edits to `base_strategy.py` /
     `strategy.py`. Reuse by subclass + override; if you must touch a base file, it is additive and
     opt-in only. (CLAUDE.md → *Shared Code Change Policy*.)
  2. **The new strategy cannot place a real order.** A two-layer dry-run lock in `__init__` raises
     `ConfigError` unless `dry_run=True` — removed only as the final, gated go-live step.

---

## Step 0 — Classify the strategy (decides how heavy the build is)

Before any code, write down the strategy precisely: legs, entry rule(s), exit/stop rule(s), DTE,
**credit vs debit**, **single-day vs multi-day**, and data needs (single vs multi-expiry, greeks,
external feeds like Polygon). Then answer the one question that governs everything else:

> **How far is this from the 0DTE-credit-iron-condor base (`IronCondorEntry` / `MEICStrategy`)?**

| If the strategy is… | Build weight | Steps you actually need |
|---|---|---|
| **Another 0DTE credit structure** (IC/strangle variant, like `strangle`, Brandon B/C) | **Light** | 0, 1, 3, 4, 5, 8, 9, 10 — reuse `IronCondorEntry`, base settlement, and `backtesting.db`. **Skip 2, 6, 7.** |
| **A different shape** (multi-day, net-debit, calendar, ratio — like `double_calendar`) | **Heavy** | All steps 0–10. Own data model, sidecar persistence, isolated DB. |
| **A sibling of an existing non-base strategy** (e.g. a second calendar variant alongside `double_calendar`) | **Reuse-driven** | Inherit/reuse the sibling's modules (`calendar_entry`, `calendar_chain`, two-expiry plumbing, recorder/sidecar/observability patterns). Net-new work is usually only the *differing management logic* (Step 5) + entry/strike rules (Steps 3–4). **Inherit 2, 6, 7 — don't rebuild.** If a second sibling lands, extract a shared base (e.g. `CalendarStrategyBase`) both inherit — additive, doesn't disturb the live variants. |

**Do not cargo-cult D's full weight onto a light strategy.** D needed a data model, sidecar, and
isolated DB *only because* it is multi-day net-debit — alien to the base. The `strangle` strategy,
being a 0DTE credit structure, skipped all three. And when a strategy is a *sibling* of an existing
one — same structural family, different management — reuse that sibling's modules; don't rebuild them.

**Pick:** a variant id (next free letter, e.g. `e`) and a registry name (e.g. `"iron_butterfly"`).

- [ ] **Done when:** the strategy spec is written down (a short `docs/<NAME>_STRATEGY_SPECIFICATION.md`
  or `/tmp` spec the module docstring can point at), the light/heavy decision is made and recorded,
  and the variant id + registry name are chosen and confirmed free.

---

## Step 1 — Scaffold + coexistence safety  *(D: commit `0c75f11`)*

The riskiest step — not because of the new strategy, but because adding a variant perturbs
**shared/global state** the live variants depend on.

**Create:**
- `bots/hydra/<name>_strategy.py` — `class <Name>Strategy(HydraStrategy)` (or `BrandonHydraStrategy`),
  **dry-run-locked**. Copy the lock pattern verbatim from
  [`double_calendar_strategy.py:100-132`](../bots/hydra/double_calendar_strategy.py#L100-L132) or
  `strangle_strategy.py`: raise `ConfigError` if `not kwargs.get("dry_run")` **before**
  `super().__init__`, and re-check `self.dry_run` **after**. Set `BOT_NAME` and, if the structure
  isn't a hedged short, `requires_protective_wings = False`.
- `bots/hydra/config/config_variant_<e>.json` — **committed** (not gitignored like root `config.json`).
  `dry_run: true`, `strategy.name: "<name>"`, `alerts.enabled: false`, `google_sheets.enabled: false`,
  `strategy.brandon.enabled: false` (if not a Brandon derivative), a strategy-specific knob section,
  and an `api_pacing_multiplier` (D uses 2.5 — gentler, because it's read-heavy).
- `deploy/hydra_variant_<e>.service` — copy `deploy/hydra_variant_d.service`; set
  `HYDRA_VARIANT_ID=<e>`, `CALYPSO_BROKER_URL=http://127.0.0.1:8788` (broker-proxy mode),
  `--config bots/hydra/config/config_variant_<e>.json`, `Restart=always`. Reference this strategy's
  (future) go-live runbook by name in the `Description=`.

**Edit (one line / additive each):**
- [`bots/hydra/registry.py`](../bots/hydra/registry.py) — add one row to `_REGISTRY`:
  `"<name>": "bots.hydra.<name>_strategy:<Name>Strategy"`.
- `bots/hydra/main.py` — `HYDRA_VARIANT_ID=<e>` detection + startup banner + `bot_name`.
- `dashboard/backend/config.py` + `dashboard/backend/routers/variants.py` — **exclude** the variant
  from the 0DTE `/compare` view and `_VARIANT_IDS` **if its P&L shape is incompatible** (D did — net
  debit vs net credit is apples-to-oranges). A like-for-like IC variant can stay in `/compare`.

**Coexistence safety audit (do this explicitly — it is the load-bearing part of Step 1):**
- [ ] Telegram `getUpdates` poller is still gated to **variant A only** (D's scaffold fixed a latent
  race where each variant started its own poller on the one bot token).
- [ ] No new write to the **canonical record**: `google_sheets.enabled=false`, `alerts.enabled=false`.
- [ ] Shared **`calypso-broker` request budget**: the new variant's steady-state request rate keeps
  the A+B+C+new total under the ~10 req/s ceiling (set `api_pacing_multiplier` accordingly).
- [ ] Shared-account **buying power** and the account-wide **STATE-004 overnight guard** +
  **orphan sweep** are not perturbed *while dry-run* (dry-run places no real orders, so the account
  stays flat — but list these now as the MUST-FIXes go-live will have to scope; see Step 10).
- [ ] Dashboard `/compare` + `_VARIANT_IDS` don't render the new shape incorrectly.

**Tests:** dry-run-lock test (constructing with `dry_run=false` raises `ConfigError`); registry test
(`resolve_strategy_name` returns the new name; class imports). Add to `tests/test_registry.py` +
`tests/test_<name>_strategy.py`.

- [ ] **Done when:** full suite green (`python -m pytest tests/ -q`); the variant starts in dry-run
  and is inert (no entry logic yet); the coexistence checklist above is all ticked; A/B/C unchanged.

---

## Step 2 — Data model  *(heavy build only; D: commit `a017447`)*

Only if the strategy's economics don't fit `IronCondorEntry`. Subclass it (reuse the Leg bridge,
`active_entries`, state save/load, `(conid, quantity)` reconciliation) and **override every economic
property** so the IC credit-vertical math never runs for your structure. Add a lifecycle enum if the
position has phases (D's `DCPhase`: CALENDAR → TRANSFORMED → CLOSED). Put it in
`bots/hydra/<name>_entry.py`.

- [ ] **Done when:** the model's economic properties (P&L, value, width) are correct and unit-tested
  in isolation; A/B/C's use of `IronCondorEntry` is untouched. **Light builds skip this step.**

---

## Step 3 — Data plumbing  *(D: commit `2e80f19`)*

Add broker wrappers **only for genuinely new data shapes**; reuse inherited `IBClient` /
`BrokerClient` methods otherwise (option chain, quote, greeks, SPX/VIX ticks all exist). Put any
**selection logic in a pure, broker-free module** (like
[`calendar_chain.py`](../bots/hydra/calendar_chain.py)) so it's unit-testable without a broker.

**Probe any new entitlement/data assumption on the VM, read-only, during market hours, before you
build on it.** D's offline tests were green but the live probe caught SPXW expiry gaps and a missing
IV field. Add a `_probe_*` diagnostic method and run it via the on-VM pattern (CLAUDE.md → *Running
Diagnostic Scripts on VM*).

- [ ] **Done when:** selection helpers are pure + unit-tested; new data assumptions are confirmed by
  a real VM probe (or explicitly flagged as unverified in the docstring).

---

## Step 4 — Entry + dry-run simulation  *(D: commit `bef24ee`)*

Strike selection, `_calculate_strikes`, `_initiate_entry`, pre-entry gates (orphan check, market
halt, BP floor, any concurrency/budget cap), and a `_simulate_entry` that books **synthetic DRY
fills** (no real order reaches the broker while locked). Override the BP floor if it differs from the
IC default (D: `min_buying_power_per_calendar` ≈ $2000 vs IC $500).

- [ ] **Done when:** a simulated entry books correctly (strikes, debit/credit, commission, state
  save) under dry-run; happy-path + skip-path tests pass; no real order is ever placed.

---

## Step 5 — Core mechanic / risk controls  *(D: commit `8f4d259`)*

The strategy's defining logic: stop, profit target, any transformation/roll, EOD handling. Implement
`_check_stop_losses` / the per-tick manager. Use a **breach-persistence window** for any stop on a
noisy multi-leg mark (D requires the −20% breach to persist `stop_confirm_seconds` before closing —
the MKT-046 analogue; a single stale tick must not fire a false stop).

- [ ] **Done when:** stop / target / EOD paths book the right P&L and are unit-tested, including the
  noise/false-trigger guard.

---

## Step 6 — Persistence + settlement  *(multi-day only; D: commit `8fa0654`)*

If the base state-file schema can't hold your fields, persist via a **sidecar JSON** next to the base
state file (D: `data/variant_<e>/dc_open_trades.json`), guarded by a `_loaded` flag set **before**
`super().__init__` so recovery can't clobber it with `[]`. Carry open positions across
`_reset_for_new_day`; override `check_after_hours_settlement` to settle on your terminal event. **Zero
edits to the fix-scarred base save/load/settlement.** Same-day 0DTE strategies skip this — the base
handles it.

- [ ] **Done when:** serialize/load round-trips; open positions survive a restart and carry across
  the daily reset; settlement books terminal P&L. **Light builds skip this step.**

---

## Step 7 — Isolated DB  *(incompatible-shape only; D: commit `49b17c6`)*

Only if your rows don't fit `backtesting.db`'s IC schema. Create a `<Name>DataRecorder` writing to a
**separate DB file** (D: `data/variant_<e>/dc_calendar.db`) so you never bump the shared schema
version or risk A/B/C. All `CREATE IF NOT EXISTS`, fire-and-forget (recording never blocks trading).
Otherwise reuse `DataRecorder`.

- [ ] **Done when:** schema + record methods round-trip in tests; shared `backtesting.db` untouched.
  **Light builds skip this step.**

---

## Step 8 — Observability  *(D: commits `e793a4c`, `f958083`)*

- Telegram: a status reader (`bots/hydra/<name>_status.py`, pure) + a command (D: `/calendars`),
  rendered by variant A's poller.
- Dashboard: a backend reader (`dashboard/backend/services/<name>_reader.py`, **no bot import**) + a
  `GET /api/<name>/status` endpoint + a frontend page — **its own view** if the shape is incompatible
  with `/comparison`. State a "dry-run shadow — places no real orders" disclaimer up front.

- [ ] **Done when:** the variant is observable from Telegram and the dashboard; readers are read-only
  and tested; `tsc -b` + `vite build` clean for any frontend change.

---

## Step 9 — Hardening  *(D: commits `1f2de8a`, `33b1838`, `a68aadf`, `93ddb9f`, `249931d`, `e52d8fb`, `4501eae`, `c92cf79`)*

1. **Adversarial multi-agent review** — run `/code-review ultra` (or an equivalent multi-agent pass)
   on the diff. Fix findings; re-review. D ran a 26-agent then a 14-agent pass.
2. **Live VM probe during market hours** — read-only, validates data/entitlement assumptions end to
   end. This is where shape bugs hide (D's SPXW expiry gaps).
3. **Performance pass = correctness, because the broker session is shared.** A read-heavy entry burst
   adds latency to *live C* on the shared `calypso-broker`. D's first entry took ~4 minutes (chain +
   greeks fan-out) and had to be bounded (windowed scan → seeded monotonic step-search) before it
   could safely run beside live variants. Bound it **before** soak, not after.

- [ ] **Done when:** the review is clean (or all findings triaged/fixed), the VM probe passes, and
  measured entry/manage latency keeps the combined request rate under the shared-broker ceiling.

---

## Step 10 — Go-live audit + docs  *(D: commits `5861e1c`, `5f5ee3a`; still NO-GO/locked)*

Produce, modeled on D's three docs in [`docs/migration/`](migration/):
1. **Scope + audit** with an explicit **GO / NO-GO verdict** and a risk register. A NO-GO is a
   *successful, honest* outcome — D's audit returned NO-GO and that was correct.
2. **MVL (minimum-viable-live) plan** — the first live phase with the riskiest mechanic stripped
   (D defers the transformer; goes live first as a defined-risk debit calendar + stop).
3. **Go-live runbook** — the single source of truth: arm-gate, flip / rollback / emergency-flatten
   procedures, smoke test, readiness gate (D's DG-1..DG-11), and halt/kill criteria. Wire the code to
   it: module + class docstrings, both `ConfigError` lock messages, and the systemd `Description=`
   point at the runbook by name.

**Go-live readiness gate (must all be GO before the dry-run lock comes off):**
- [ ] A **real-order execution path** exists and is smoke-tested (open→close on paper).
- [ ] The **edge is validated** (backtest / paper soak) and the signal is observable on the IBKR feed.
- [ ] **Coexistence MUST-FIXes** are shipped *and have soaked in the live variant's code path first*:
      STATE-004 overnight guard scoped to per-variant conids; orphan sweep scoped the same way;
      per-variant buying-power budget. (These protect *live C*, so they land in C and soak before the
      new strategy ever holds a real position.)
- [ ] Go-live is via a **documented manual flip** (a `scripts/flip_<e>_live.sh`), gated on broker
      `/health` + a same-ET-day smoke PASS. **Never an auto-flip.**
- [ ] `bots/hydra/__init__.py` **version history** updated for every behavior change in the build.
- [ ] CLAUDE.md gains an operator section for the new variant; `docs/migration/RUNBOOKS.md` gains its
      flip + flatten entries.

- [ ] **Done when:** the three docs exist, the readiness gate is recorded as GO (or NO-GO with the
  blocking reasons), and — only on GO — the lock is removed via the sanctioned flip. **Until then the
  strategy stays dry-run-locked.**

---

## Coexistence safety — the master checklist

Adding a variant is mostly about *not breaking the live ones*. Re-confirm all of these at Step 1 and
again before go-live:

| # | Shared resource | Guard |
|---|---|---|
| 1 | Telegram bot token / `getUpdates` poller | Poller stays gated to variant A only. |
| 2 | Canonical Google Sheets + metrics | `google_sheets.enabled=false` on the new variant. |
| 3 | Operator alerting | `alerts.enabled=false` while scaffolding (no paging, no spam). |
| 4 | `calypso-broker` request budget (~10 req/s) | `api_pacing_multiplier` set; entry fan-out bounded. |
| 5 | Shared-account buying power | Per-variant BP budget before any real order (go-live MUST-FIX). |
| 6 | STATE-004 overnight guard (account-wide) | Scoped to per-variant conids before a real overnight hold. |
| 7 | Orphan sweep (account-wide) | Scoped the same way before a real position exists. |
| 8 | Dashboard `/compare` + `_VARIANT_IDS` | New shape excluded if P&L is incompatible. |
| 9 | `base_strategy.py` / `strategy.py` (A/B/C share these) | No destructive edits; subclass + override; any base touch is additive + opt-in. |

---

## Branch & commit conventions

- **Branch:** per CLAUDE.md item 9, non-trivial work goes on a feature branch off
  `hydra-ibkr-standalone`, merged via PR with the full suite green. *(Note: D was actually built as
  linear commits directly on the branch — decide explicitly which you want before starting; default
  to the stated feature-branch + PR policy.)*
- **Commits:** one logical step per commit, tests included, e.g.
  `feat(strategy-<e>): Phase N — <what landed>` / `fix(strategy-<e>): …` / `perf(strategy-<e>): …` /
  `docs(strategy-<e>): …`. Bump `bots/hydra/__init__.py` version history when behavior changes.
- **Deploy:** `shared/` changes that `calypso-broker` imports require restarting `calypso-broker`
  FIRST (it holds loaded bytecode) — see CLAUDE.md → *Deployment Workflow*.

---

## Audit Log

Record each time this playbook is followed or a strategy is audited against it.

| Date | Strategy (name / variant) | Step / scope audited | Verdict | Notes / findings | Reviewer |
|---|---|---|---|---|---|
| 2026-06-16 | `double_calendar` / D | Full build (Steps 0–10) | **NO-GO** (locked) | Worked example this playbook is distilled from; NO-GO per [`D_GOLIVE_SCOPE_AND_AUDIT.md`](migration/D_GOLIVE_SCOPE_AND_AUDIT.md) | — |
| 2026-06-16 | Theta Profits Double Calendar (Ravish Ahuja) — proposed variant | Step 0 feasibility vs playbook | **GO — playbook applies** | Sibling of `double_calendar` (same family, *no transformer*, simpler defined-exit mgmt). Net-new = Step 5 mgmt logic + Step 3/4 entry/strike rules; inherits D's model/plumbing/persistence/DB/observability. Surfaced + closed the "sibling" classification gap (Step 0 row 3). | Claude |
| | | | | | |

---

## Appendix A — Strategy D as the worked example

D's 19 linear commits (Jun 14–16 2026) map 1:1 onto the steps above:

| Step | D commit(s) | Subject |
|---|---|---|
| 1 | `0c75f11` | scaffold + Phase-0 coexistence safety |
| 2 | `a017447` | Phase 1 — `CalendarEntry` two-expiry net-debit foundation |
| 3 | `2e80f19` | Phase 2 — two-expiry chain/quote/greeks data layer |
| 4 | `bef24ee` | Phase 3 — entry + net-debit dry-run simulation |
| 5 | `8f4d259` | Phase 4 — transformer + 20%-debit stop + EOD close |
| 6 | `8fa0654` | Phase 5 — multi-day persistence + per-expiry settlement |
| 7 | `49b17c6` | Phase 6 — calendar DB schema (isolated tables) |
| 8 | `e793a4c`, `f958083` | Phase 7 — Telegram `/calendars` + dashboard `/dc` |
| 9 | `1f2de8a`, `33b1838`, `a68aadf`, `93ddb9f`, `249931d`, `e52d8fb`, `4501eae`, `c92cf79` | adversarial reviews, live-probe fix, bounded scan, monotonic step-search, stop/BP/cadence hardening |
| 10 | `5861e1c`, `5f5ee3a` | scope+audit (NO-GO) + MVL-D plan + canonical runbook |

**Precedent #2 — the `strangle` strategy** (registry name `"strangle"`,
`bots/hydra/strangle_strategy.py`) is a *light* build of the same pattern: a 0DTE undefined-risk
short strangle, dry-run-locked, that reused `IronCondorEntry` and skipped Steps 2/6/7. It is the
reference for a light-build strategy.

## Appendix B — Key files to copy from

| Pattern | Copy from |
|---|---|
| Dry-run lock (`__init__`) | [`bots/hydra/double_calendar_strategy.py:100-132`](../bots/hydra/double_calendar_strategy.py#L100-L132) |
| Registry row | [`bots/hydra/registry.py:27-38`](../bots/hydra/registry.py#L27-L38) |
| Committed variant config | [`bots/hydra/config/config_variant_d.json`](../bots/hydra/config/config_variant_d.json) |
| systemd unit | [`deploy/hydra_variant_d.service`](../deploy/hydra_variant_d.service) |
| Pure selection helper | [`bots/hydra/calendar_chain.py`](../bots/hydra/calendar_chain.py) |
| Isolated DB recorder | [`bots/hydra/dc_recorder.py`](../bots/hydra/dc_recorder.py) |
| Status reader | [`bots/hydra/dc_status.py`](../bots/hydra/dc_status.py) |
| Go-live runbook template | [`docs/migration/D_GOLIVE_RUNBOOK.md`](migration/D_GOLIVE_RUNBOOK.md) |
| Scope+audit template | [`docs/migration/D_GOLIVE_SCOPE_AND_AUDIT.md`](migration/D_GOLIVE_SCOPE_AND_AUDIT.md) |
| MVL plan template | [`docs/migration/D_MVL_PHASE1_PLAN.md`](migration/D_MVL_PHASE1_PLAN.md) |
