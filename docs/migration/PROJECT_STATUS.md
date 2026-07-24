# Project Status — where we are, what's next

**This file is the single-source-of-truth for the current state of the `hydra-ibkr-standalone` branch.** Any Claude session arriving at this repo should read this file first, before CLAUDE.md. CLAUDE.md is the operator reference (what the bot does, how to deploy, troubleshoot); this file is the *project state* (what's been done, what's in flight, what's blocked).

**Last updated:** 2026-07-24 (B↔C live-paper swap recorded — see the dated section below; the rest of this file's narrative still stops at 2026-06-16 and is due a fuller refresh, see `docs/NEXT_STEPS.md` §10 for in-flight doc-consolidation work)
**Base branch:** `hydra-ibkr-standalone` — last commit `d83d50b` + the AUD5 remediation commit (use `git rev-parse HEAD` to verify).
**Active feature branch:** `feat/strategy-grouping-spy-calendar` (commits `f9e7d2a`…`9f9e7ad`, off `hydra-ibkr-standalone`) — strategy taxonomy/grouping + Strategy D go-live work + new Strategy E (SPY double calendar). **NOT merged.** See the "Strategy D go-live + Strategy E + strategy-grouping" section immediately below.
**Commits ahead of `main`:** 153 + the AUD5 remediation commit (base branch) (use `git log --oneline main..HEAD | wc -l` to verify)
**Test suite:** unit suite passes; integration/optional tests skipped pending live paper account. Re-run `pytest` for the exact current count (it grew past the old 953 snapshot after the 2026-05-31 30-agent audit, `38ac9d6`); see CI / run `python -m pytest tests/ -q`. The suite is deterministic at any wall-clock hour (the intraday-OHLC tests were time-gated; fixed 2026-05-28).
**Branch is pushed to `origin`** (github.com/diogodiasgrilo/CALYPSO) as of 2026-05-29 — no longer laptop-only.

> ## ⚡ CUTOVER EXECUTED 2026-05-29 (~12:40 ET) — read this first
> Gate 1 passed (probe at 12:08 ET: SPX `6509='R'`, VIX `6509='R'`, real-time). The Saxo→IBKR cutover was run on `calypso-bot`:
> - **Saxo fully removed** — `token_keeper` + the 4 sibling units deleted; VM switched to `hydra-ibkr-standalone`; venv rebuilt (`ibind` 0.1.23).
> - **Strategy A is LIVE on IBKR paper** (account `DUR049068`), **dry-run**, fresh daily state, real-time SPX/VIX, `NRestarts=0`. Telegram + Google Sheets working. Dashboard locked to `127.0.0.1:8080` (SSH-tunnel only). Backups: `gs://calypso-backups` created + VM SA granted write (fixes the long-broken `db_backup`); pre-cutover state archived locally + off-site.
> - **✅ B and C RESOLVED via `calypso-broker` (deployed 2026-05-29 ~14:11 ET).** The one-brokerage-session-per-username limit (OAuth 1.0a; `compete:true` made 3 processes evict each other) is solved by a single shared broker-session service: `calypso-broker` owns the ONE IBClient (LST + ssodh/init + Tickler + 15-min re-auth loop); A/B/C run via a drop-in `BrokerClient` over loopback (`CALYPSO_BROKER_URL=http://127.0.0.1:8788`) and open NO sessions of their own. **All four (broker + A + B + C) are active, NRestarts=0, zero contention, ~0.9 IBKR req/s** (well under ~10/s). Design + follow-ups: [`BROKER_SESSION_SERVICE_DESIGN.md`](./BROKER_SESSION_SERVICE_DESIGN.md); constraint research: [`IBKR_MULTI_SESSION.md`](./IBKR_MULTI_SESSION.md).
> - **✅ P5a DONE — breaker/warmup alerting runs in the broker.** `IBKRAlertHooks` is instantiated inside `calypso-broker` (where the breakers live) and polled every 10s (breaker-transition / stuck-open / warmup-exhaustion), publishing to the same `calypso-alerts` Pub/Sub path. Strategy-side `BrokerClient.circuit_breakers` stays empty (no duplicate alerts); strategies still emit their own `ensure_connected`/broker-reachability alerts. Verified clean on deploy (no alert-poll errors; strategies degraded gracefully through the broker restart).
> - **Notes:** A/B/C run **dry-run**. **B/C stay dry-run.** Variant A will **auto-flip** to `dry_run:false` (live paper) IF the one-shot Monday 2026-06-01 09:35 ET broker paper-smoke passes (`broker-paper-smoke.timer` → `broker-paper-smoke.service`, whose `ExecStartPost=+/opt/calypso/scripts/flip_a_live.sh` flips ONLY A and restarts `hydra` on a clean PASS — guarded on broker `/health` connected + a fresh dated PASS sentinel); otherwise A stays dry-run. To flip manually, set `dry_run:false` in `config.json` + restart `hydra`. The morning re-auth gate now lives in the broker. `hydra*` units still carry (now-unused) `LoadCredentialEncrypted` — only the broker needs creds; harmless, clean up later. `PrivateDevices=yes` is used on this systemd 252 build (`ProtectDevices=yes` is silently ignored there; minor). Rollback: remove the inline `Environment="CALYPSO_BROKER_URL=…"` line from each `hydra*` unit file (+ restart on its own `IBClient`), or `main` @ `a77027f` + `~/cutover_backup_*` + `gs://calypso-backups/`.
> - **✅ Dashboard audited + wired to IBKR (2026-05-29).** Swept all 26 endpoints against the live deployment — `health / hydra.state / summary / entries (DB, with spx_at_entry) / market.ohlc / metrics.{cumulative,daily,performance,entries} / agents / widget` all return real IBKR + full Feb→today history. Two fixes: (1) **enabled comparison mode** (`DASHBOARD_COMPARISON_MODE_ENABLED=true`) so the A-vs-B-vs-C `/api/variants/*` view works (was 503 — off on Saxo too); (2) `live_state` today-SPX/VIX now sourced from `state.market_data_ohlc` (exact) instead of absent per-entry fields (closes audit FP6). Dashboard stays localhost-bound (M12).
> - **Telegram alert formatting fixed (2026-05-29).** The `process-trading-alert` Cloud Function (`cloud_functions/alert_processor/`) 400'd on every alert's Markdown (unbalanced `_`/`*` from snake_case fields) and fell back to plain text. `_md_escape()` now escapes dynamic content → valid Markdown. Redeployed (revision 00012); no 400s since. **Confirm formatted delivery in the Telegram chat** (a "Broker fix test ✅" was sent).
> - **Entry-window watchdog deployed.** `entry-window-watch.timer` runs the watchdog Mon–Fri at 10:20/10:50/11:20/11:35 ET (just after the 10:15/10:45/11:15 windows): verifies broker `/health` connected + A/B/C active + no entry-path errors; Telegram-alerts on any problem. Self-contained on the VM. Verified OK on a manual run.
> - **Commission model corrected to IBKR (2026-05-29, `078049f`).** `strategy.commission_per_leg` previously fell back to the Saxo $2.50/leg default on every broker (display/reported-P&L only; not strategy logic), overstating IBKR fees by ~$1.35/leg. Set to **$1.15/leg** (~$0.65 IBKR Pro base + ~$0.45 CBOE index + ~$0.05 ORF/OCC/CAT) across all three live VM configs (A/B/C) — decisive for judging B/C's dry-run P&L (the Saxo-vs-IBKR gap was ~$170–450/day at their leg counts). Applied + restarted; NRestarts=0.
> - **✅ Overnight session-lost incident handled + hardened (2026-05-31, `3f67bc7`).** A 01:02 ET HIGH alert fired on a routine IBKR ~01:00 ET auth-server reset; the broker's 15-min `ensure_connected()` caught the one-cycle drop, alerted, and re-authed next cycle (**self-healed, NRestarts=0**). The investigation surfaced + fixed 3 defects: (1) **nightly false HIGH** — the broker now alerts only after ≥2 consecutive `ensure_connected` failures (a routine reset clears in one) with broker-accurate wording (`will_restart=False` → "broker stays UP and keeps retrying"); the bot path (`will_restart=True`) is unchanged; (2) **watchdog was blind** — it grepped journald, but these units log to FILES; it now resolves each bot's open log via `/proc/<MainPID>/fd` + scans the broker's known path, with level-based `| ERROR |` matching so normal `ssodh/init` INFO no longer false-triggers; (3) **broker had no persistent log** — added a `RotatingFileHandler` at `/opt/calypso/logs/broker/broker.log`. Deployed + verified on the VM. +2 tests.
> - **Go-live plan updated 2026-06-02 (`b9e79a4`):** the Monday 2026-06-01 broker paper-smoke ran (one-shot timer) and its `ExecStartPost` ran `flip_a_live.sh`. The operator plan is now to flip **A AND C** to real paper after the **2026-06-02** close (for the 2026-06-03 open) via `scripts/flip_ac_live.sh`; **B stays dry-run.** ⚠️ `flip_ac_live.sh` is **operator-run (manual)** — there is intentionally NO auto-flip timer, and its Guard 2 requires a **same-ET-day** paper-smoke PASS sentinel, so a fresh smoke must run the day of the flip first. Runbook: [`RUNBOOKS.md`](./RUNBOOKS.md) **RB-8**. (AUD5 fixed the flip-script timezone handling + the false "scheduled via timer" comment; see `AUD5_FINDINGS.md` GL-1.)
> - **AUD5 audit complete (2026-06-02).** 20-agent audit of `38ac9d6..d83d50b` + 3 meta-auditors + 16 backfill agents. Confirmed code findings fixed with regression tests (`tests/test_aud5_fixes.py`): GL-2 (ORDER-004 margin gate was failing open on the IBKR path — `margin_pct=None` formatting), C-1 (real-time market-data gate completed: index Z/Y/N gating, halt detection, `_option_quote_is_realtime` + option-pricing gates), C-2/C-3 (Sheets stale-rows + settlement-metrics throttle). Plus GL-1 flip-script hardening and a doc-staleness sweep (this file, commission spec, MAX_RPS comments, version history). Full register: [`AUD5_FINDINGS.md`](./AUD5_FINDINGS.md).
> - **Now = Gate 2 paper-smoke watch + 2026-06-02 A+C go-live decision** (today is 2026-06-02). Observe A/B/C + the broker through the session + the morning re-auth gate + entry windows; the AUD5 fixes above land before/with the A+C live-paper flip. Still-open by observation: Telegram command handler responsiveness (`/status`) and full entry evaluation through the broker at the windows (the watchdog flags failures).

---

## Strategy D go-live work + Strategy E (SPY double calendar) + strategy-grouping/taxonomy (2026-06-16, feat branch)

> **All of the below is on the feature branch `feat/strategy-grouping-spy-calendar` (off `hydra-ibkr-standalone`), commits `f9e7d2a`…`9f9e7ad`. NOT merged. Nothing here is live — the two new calendar strategies (D, E) are dry-run-LOCKED, and the taxonomy/dashboard work is additive and does not change A/B/C behavior.**

**State summary:** full test suite green (~1680 tests — re-run `python -m pytest tests/ -q` for the exact count); the dashboard was deployed to the VM for a visual check; **not merged**; **nothing new is live.**

What landed on this branch:

- **Strategy D ("DC Time Machine", `double_calendar`) — fully built + hardened.** Multi-day double calendar → risk-free iron condor; runs in **simulation only** (dry-run-LOCKED; the class refuses any non-dry_run construction). Go-live audit returned an explicit **NO-GO** (correct outcome). All three go-live docs written: [`D_GOLIVE_RUNBOOK.md`](./D_GOLIVE_RUNBOOK.md) (canonical runbook), [`D_GOLIVE_SCOPE_AND_AUDIT.md`](./D_GOLIVE_SCOPE_AND_AUDIT.md) (scope + NO-GO + risk register), [`D_MVL_PHASE1_PLAN.md`](./D_MVL_PHASE1_PLAN.md) (minimum-viable-live phase-1, transformer stripped). Coexistence MUST-FIXes (scope STATE-004 + orphan sweep to per-variant conids; per-variant BP budget) remain go-live gates.

- **Strategy E ("SPY Double Calendar", `spy_double_calendar`, `BOT_NAME` SPYDC) — built, dry-run-LOCKED.** A *managed* SPY double calendar from an **OptionsKit** video (short ≈35 DTE + long ≈+1 week, ±1×expected-move strikes, low-IV entry gate, laddered early profit-take + trading-day time-exit, **no transformer, no hard stop**). Sibling of D: D's shared `_dc_*` machinery was lifted **verbatim** into the new `bots/hydra/calendar_strategy_base.py` (`CalendarStrategyBase`), which both `DoubleCalendarStrategy` (D — byte-identical) and `SpyDoubleCalendarStrategy` (E) subclass. E is fully simulated (NOT stubbed). SPY is American-exercise + dividend-paying, so early-assignment + dividend handling are go-live gates (net-new vs D's European SPXW). *(NB: an earlier "Theta Profits / Ahuja" spec was a superseded placeholder — the shipped strategy is the OptionsKit SPY double calendar.)*

- **Strategy taxonomy + comparability groups** — new `shared/strategy_taxonomy.py` (stdlib-only) is the single source of truth: **5 variants / 2 groups** — `ic_0dte` (credit; **a** dry-run-shadow, **b** live/primary, **c** dry-run-shadow — see the 2026-07-24 B↔C swap below) + `calendar_multiday` (debit; **d**, **e** both dry-run-locked). The letter stays the stable internal key; human display-names + groups are a presentation overlay. Credit-vs-debit comparison is refused **by construction** (a group carries a single `pnl_shape`), replacing the scattered `if vid=="d": continue` / `_VARIANT_IDS` exclusions. A startup fail-stop validates the static table against the runtime resolver; unknown letters degrade to a safe sentinel.

- **Dashboard** — main-page **strategy picker** (grouped dropdown, dispatches IC vs calendar layout by `data_kind`), **group tabs** replacing A/B/C/D tabs (`/comparison/:groupId`; legacy `/comparison` + `/dc` redirect into it), a group-aware `/api/strategies` API + a **shape-distinct** calendar DB reader (debit-native; no IC-field leakage), and **EOD auto-update** (cumulative/summary widgets refresh live at the close, no page reload). Group-scoped `/compare` in Telegram + taxonomy display-names in alert/Telegram/email rendering (alert-wire `bot_name` + HOMER anchors FROZEN).

**Status:** on the feature branch only, full suite green, dashboard deployed for a visual check, **NOT merged, nothing new is live.** Living **what's-left** tracker: [`docs/NEXT_STEPS.md`](../NEXT_STEPS.md) (remaining steps + Strategy D & E deployment paths). Design + as-shipped detail: [`docs/STRATEGY_GROUPING_REDESIGN.md`](../STRATEGY_GROUPING_REDESIGN.md) (§§1–8) and the [`docs/NEW_STRATEGY_PLAYBOOK.md`](../NEW_STRATEGY_PLAYBOOK.md) audit log.

---

## B↔C live-paper swap executed (2026-07-24)

> **State change, not a code-work section — recorded here so this file doesn't silently drift from the live VM.** On 2026-07-24 (~03:04 ET) the operator manually executed the live-paper seat swap researched in [`BC_SWAP_PLAN.md`](./BC_SWAP_PLAN.md) (dated 2026-07-21) via `scripts/flip_bc_swap.sh`:
>
> - **B is now LIVE** (`dry_run=false`, real paper orders): 7 contracts/entry (down from its prior dry-run 10), widened to a 7-slot entry grid (09:45–12:45), `alerts.enabled=true`, dashboard PRIMARY.
> - **C is now `dry_run_shadow`** (simulation only): 7 contracts, `alerts.enabled=false`.
> - **A is unchanged** throughout — `dry_run_shadow`, 1 contract.
> - The dashboard's canonical views (main page, WebSocket, iOS widget, legacy `/api/hydra/*`), Telegram alert identity, and the analyst agents (`services/agents_config.json` HERMES/CLIO/HOMER `read_db`) now all **dynamically follow whichever variant is live** via `dashboard/backend/services/variant_readers.py:live_seat_id()` — nothing is hardcoded to `c` anymore. Dashboard baseline date moved to 2026-07-24.
> - Rollback tooling: `scripts/flip_bc_rollback.sh` (hard-aborts unless the paper account is flat first). Runbook: `RUNBOOKS.md` **RB-9**.
> - **CLAUDE.md's Variant Comparison table + related sections are the current operator reference for this state** (already updated separately). `BC_SWAP_PLAN.md` is retained for historical/audit context and carries an "EXECUTED" banner at its top.

---

## TL;DR for a Claude session continuing this work

1. **Read this file (you're doing it).**
2. **Read CLAUDE.md** for the operator reference (broker integration, strategy details, deploy commands).
3. **Check the "What's blocked / pending external input" section** below for any open external gate (probe run, user approval, etc.) that decides what to do next.
4. **If everything below the "Active work" line is `pending`**, the right action is usually one of:
   - Wait for the next external gate (Tuesday probe, paper validation, user signoff).
   - Ask the user what they want next.
   - Do NOT start new code work unless the user explicitly asks.
5. The branch is in a **production-ready pre-flight state** — extensive audit + polish complete. The merge to `main` is gated on external operator work, not on more code changes.

---

## Where this branch came from

The original CALYPSO codebase on `main` is a Saxo Bank multi-bot setup (5 trading bots — HYDRA + Iron Fly + Delta Neutral + Rolling Put Diagonal + MEIC) talking to Saxo OpenAPI. This branch is the **complete Saxo→IBKR migration of the HYDRA bot only**, on the IBKR Client Portal Web API via ibind 0.1.23 (OAuth 1.0a, no gateway). On this branch:

- HYDRA is the **only** bot (4 sibling bots deleted in P5a/P5b — preserved kill-switched on `main`).
- Account is **IBKR paper only** — no live-money path is wired.
- Saxo client + token_keeper + Saxo Secret Manager refs all dead/disabled.
- All deployment is `systemd LoadCredentialEncrypted=` for the 6 IBKR OAuth credentials.

Full migration history: `docs/migration/HYDRA_STANDALONE_REWRITE_PLAN.md`.

---

## What's been completed

### Migration phases (F1–F7 + P1–P7)

- **F1**: Auth + LST handshake (`IBClient.connect`)
- **F2**: Contract qualification + conid cache
- **F3**: Option chain via probed IBKR secdef behavior
- **F4**: Position reconciliation via conid-quantity model (IBKR has no per-leg position id)
- **F5**: Closed-position price + settlement reconciliation + FX flow
- **F6**: Order write path with cOID dedup safety
- **F7**: Strategy-layer broker abstraction (read helpers + balance + ORDER-004 BP gate)
- **P1**: Imports + reparent HydraStrategy onto HYDRA-owned base
- **P2**: Dead-Saxo-helper purge
- **P3**: Method ranges audit
- **P4**: Broker-abstraction flattening
- **P5**: Streaming subsystem + sibling-bot deletion
- **P6**: Retry + per-family circuit breakers + scripts/README rewrite
- **P7**: Go-live (re-auth gate, systemd creds, multi-agent code audit)

### Audit cycles (4 to date)

| Cycle | Method | Findings | Outcome | Test count after |
|---|---|---|---|---|
| **P7 Round 1** | 6 parallel domain agents | 49 (4 C + 13 H + 17 M + 15 L) | All closed with regression tests | 885 |
| **P7 Round 2** | 4 parallel verify-fixes agents | 0 new | PASS | 885 |
| **P7 Round 3** | Senior overseer | 0 blockers | PASS, "house bet" 85% confidence | 885 |
| **Polish pass** | 12 items + 17 amendments + 3-agent re-audit + senior overseer | 0 new | PASS, confidence raised to 90% | 918 |
| **AUD2** | 6 parallel domain agents + Round 2 verify + senior overseer | 4 H + 7 M + 17 L | All H+M fixed with regression tests; 7 L fixed; 10 L accepted as observations | **920** |
| **AUD3 — preflight (2026-05-28)** | 20 parallel domain agents + per-finding adversarial verify + Claude adjudication (web-search enabled) | 81 raw → 74 confirmed (1 C + 7 H + 14 M + 46 L + 6 I), 7 false-positive | 20 cutover-relevant findings fixed w/ regression tests (3 commits: `6882e82`, `09e1641`, `2e276af`); rest triaged (see below) | **953** |
| **AUD4 — 30-agent migration audit (2026-05-31, post-cutover)** | 30 parallel domain agents over the live Saxo→IBKR migration | 79 verified findings | 79 fixed (`38ac9d6`, 39 files, +2959/−610) + follow-up hardening on 2 fixes that rested on unverified runtime assumptions (`9b83067`) | re-run `pytest` (grew past 953; see CI) |
| **AUD5 — go-live audit since AUD4 (2026-06-02)** | 20 parallel domain agents (read+command) + 3 meta-auditors (coverage check) + 16 backfill agents over `38ac9d6..d83d50b` | 155 raw (89+66) → deduped to ~12 confirmed clusters: 2 go-live blockers (GL-1 broken flip mechanism, GL-2 ORDER-004 margin fail-open), C-1 incomplete real-time data gate, C-2/C-3, + doc staleness | All confirmed code findings fixed w/ regression tests (`tests/test_aud5_fixes.py`); docs reconciled. Full register: [`AUD5_FINDINGS.md`](./AUD5_FINDINGS.md) | re-run `pytest` |

Cumulative findings closed: **166** through AUD4; AUD5 (2026-06-02) added the GL-1/GL-2/C-1/C-2/C-3 cluster (see [`AUD5_FINDINGS.md`](./AUD5_FINDINGS.md)). Zero regressions across any cycle.

**AUD3 detail** — full register + per-finding adjudication in [`PREFLIGHT_AUDIT_FINDINGS.md`](./PREFLIGHT_AUDIT_FINDINGS.md). The 20 fixed: cOID uniqueness (#1 critical + M2), partial-fill flatten (M3), warmup metadata (#2/M4), retry token-match (#4), durable state write (M11), breaker outage alerting (M7/M10), streaming lock (#3), dashboard path-traversal (#6) + API-key auth (M12) + Telegram token redaction (#5), streaming staleness (M5), retry-policy preservation (M6), `/config` buffer (M8), variant-unit hardening (AUD2-L4 regression), a latent settlement `NameError`, plus stale-deploy-file deletion (#7/#8) and operator-doc accuracy. **None were real-money risks** (IBKR paper account; variants B/C dry-run).

**Consciously deferred (not gold-plated pre-cutover):**
- Internal config-comment Saxo cosmetics (`$50,741 margin pool`, `external_price_feed` rationale, Brandon/IBConfig docstrings) → one mechanical `/simplify` sweep post-cutover.
- **POS-004 same-conid short+long settlement-merge misclassification** (strategy.py ~L10586-10609) — a real reconciliation-logic bug rated low; deliberately not touched in a pre-cutover doc pass. **Fix before relying on multi-leg settlement P&L.**
- `connection_timeout_seconds` is documented but still not enforced as a hard cap (a true watchdog is a tracked follow-up; bounded today by ibind per-request timeouts + systemd StartLimit).

**Honest assessment from the AUD2 senior overseer:** "The audit process itself is the weakest spot, not the code." 4 prior single-domain audits missed the `consumer_key` log leak (AUD2-H1) because IBKR-client auditors focused on API correctness, not log-content side-effects. The 6-agent multi-domain pattern caught it. Future major branches must mandate this pattern AFTER any polish pass, not just before.

### Polish pass (between Round 3 and AUD2)

12 items + 17 amendments + 11 commits — all complete and audited:

- **Item 1**: IBKR-specific Telegram alerts (breaker transitions, `_iserver_primed` reset, snapshot warmup exhaustion, `ensure_connected()` failure) — `bots/hydra/alert_hooks.py`, 18 regression tests
- **Item 2**: ARGUS Saxo→IBKR rewrite (drop token_keeper + saxo_token_cache checks; add heartbeat + breaker grep + GCS-backup checks; holiday-aware) — 3 regression tests
- **Item 3**: 7 incident runbooks (RB-1..RB-7) + alert-to-runbook map — `docs/migration/RUNBOOKS.md`
- **Item 4**: Backup verification — `CLAUDE.md` backups section + ARGUS GCS check (rolled into Item 2)
- **Item 5**: Pre-start state-file snapshot — `scripts/pre_start_snapshot.sh` + `ExecStartPre=` + 10 bash tests + 50-snapshot retention
- **Item 6**: `HYDRA_STRATEGY_SPECIFICATION.md` IBKR-era pass (v2.0.0-rc.1 + dual-naming note)
- **Item 7**: Top-level `README.md` IBKR-era rewrite
- **Item 8**: Pre-merge squash plan — `docs/migration/MERGE_PLAN.md` (8 chunks)
- **Item 9**: Live-readiness 10-gate checklist — `docs/migration/LIVE_READINESS_CHECKLIST.md`
- **Item 10**: Secret-leak audit — `IBKRCredentials` `field(repr=False)` hardening + 8 regression tests
- **Item 11**: Chaos / SIGKILL recovery test — documented as operator step for paper validation
- **Item 12**: Requirements pinning + `requirements-lock.txt` + pip-audit clean (0 CVEs)
- **Cross-cutting**: variant_b/c.service brought to parity with hydra.service; `token_keeper.service` renamed `.disabled-on-this-branch`; `deploy/README.md` written

---

## What's blocked / pending external input

The branch is **production-ready code-wise** — the merge is gated on external operator actions, not more code changes.

### Gate 1 — regular-session market-data probe (external — user runs)

**Status:** ✅ **PASSED (2026-05-29 12:08 ET).** The regular-session probe returned `6509='R'` on both SPX and VIX (real-time, entitled), clearing the cutover. History: the Sunday 2026-05-24 probe showed `6509='Z'` (frozen — expected weekend/holiday); a 2026-05-28 ~21:09 ET run was **inconclusive** (market closed → all instruments NO DATA, incl. the SPY control) — auth + contract qualification worked, only live data-entitlement was unconfirmed until the 2026-05-29 regular-session run resolved it to `R`.

The passing probe showed:
- ✅ `6509='R'` on SPX/VIX → cleared for the cutover (Gate 2 — executed 2026-05-29)

(For reference, the other outcomes the probe could have returned: `6509='D'` → delayed-only entitlement, do NOT trade live, fix subscription first; bid/ask missing during market hours → broader subscription issue.)

Command, if a re-probe is ever needed (user has the OAuth env vars from 1Password in their shell):
```bash
cd "/Users/ddias/Desktop/CALYPSO/Git Repo"
source .venv/bin/activate
python scripts/probe_ibkr_market_data.py 2>&1 | tee scripts/probe_mktdata_$(date +%H%M%S).log
```

Expected: paste the output to the Claude session for interpretation.

### Gate 2 — Saxo→IBKR cutover on `calypso-bot` (operator + Claude pair)

**Status:** ✅ **EXECUTED 2026-05-29 (~12:40 ET)** — see the "CUTOVER EXECUTED" blockquote at the top of this file for the as-run result. The procedure below is kept for the historical record + rollback reference. **Full runbook: [`GATE2_DEPLOY_RUNBOOK.md`](./GATE2_DEPLOY_RUNBOOK.md).**

**Operator decision (2026-05-28):** completely replace the live deployment on `calypso-bot` — remove all Saxo bots + the Saxo token-keeper (code preserved on `main`), and run the IBKR HYDRA strategies A/B/C, with dashboard + Telegram + DB + Google Sheets rewired to the IBKR bot. Verified safe: no real money on either side (the current Saxo bot runs `[DRY RUN]`; the IBKR account is paper-only via hardcoded `load_credentials("paper")`; B/C are dry-run). This supersedes the parallel `_ibkr`-suffixed Phase D/E/F plan — the `hydra-ibkr-standalone` branch *is* the Saxo-removed end-state, so we deploy it wholesale.

Reality discovered on the VM (folded into the runbook): `calypso-bot` currently runs the **live Saxo** stack on `main`; the IBKR branch is now pushed so the VM can fetch it. Secrets are in GCP Secret Manager (`calypso-trading-bot`) — Telegram/Sheets carry over unchanged; only the 6 IBKR OAuth creds are new (systemd `LoadCredentialEncrypted=`).

Procedure (abbrev — see runbook for the full ordered steps + rollback):
1. Push done ✅. Back up live state to `gs://calypso-backups/precutover_*`.
2. Stop + remove the Saxo units (token_keeper + 4 siblings); `git checkout hydra-ibkr-standalone` + rebuild venv.
3. Encrypt the 6 IBKR OAuth creds; run the mandatory 3-check before `systemctl enable hydra`.
4. Start A, then B/C (dry-run) + dashboard + agent timers; verify each component is IBKR-wired (zero `saxo` log lines).
5. **Dashboard lockdown (audit M12):** nginx now binds `127.0.0.1:8080` (reach via SSH tunnel); the `calypso-dashboard-api-key` secret + frontend key-shim are staged for optional app-layer auth.
6. Watch the first connection + a morning dry-run + an afternoon paper session + the next morning's re-auth gate.

### Gate 3 — Full integration test suite (post-account-activation)

**Status:** ⏳ 15 integration tests in `tests/` are currently skipped — they require a live paper account to run against IBKR's real API. Once Gate 2 deploys, run with:
```bash
IBIND_INTEGRATION=paper .venv/bin/python -m pytest tests/integration/ -v
```
Expected: ≥ 15 passed.

### Gate 4 — 5-day paper validation week

**Status:** ⏳ blocked on Gates 2 + 3

Per `LIVE_READINESS_CHECKLIST.md` Gate 4: 5 consecutive trading sessions with:
- No manual `systemctl restart` for code/config reasons
- Net P&L ≥ 0
- No false-positive stops
- No null/None VIX during regular market hours
- Chaos test passed (`kill -9` mid-trade verifies state-file snapshot + clean systemd restart + no duplicate orders)

### Gate 5 — Merge to `main`

**Status:** ⏳ blocked on Gate 4

Procedure: `docs/migration/MERGE_PLAN.md` (8-chunk squash, backup branch, byte-identical verify, branch-protection setup, v2.0.0 tag).

### Gate 6 (eventual, not on this branch) — Live cutover

**Status:** Out of scope for this branch. The branch is **paper only** — live trading requires:
1. NEW IBKR live OAuth keypair (separate from paper)
2. Re-encrypted credentials in `/etc/calypso/ibkr-live/`
3. `load_credentials("live")` in main.py
4. Full `LIVE_READINESS_CHECKLIST.md` 10-gate sign-off
5. Explicit user approval committed to the repo

---

## Active work (AUD5 remediation committed; 2026-06-02 A+C go-live decision pending operator)

The **AUD5 go-live audit** (2026-06-02, see the audit-cycles table + [`AUD5_FINDINGS.md`](./AUD5_FINDINGS.md)) ran and its confirmed fixes are committed with regression tests (`tests/test_aud5_fixes.py`): GL-2 ORDER-004 margin fail-open, C-1 real-time data gate completion, C-2/C-3 logging fixes, GL-1 flip-script hardening, + a doc-staleness sweep. Earlier work remains as recorded: AUD3 fixes (`6882e8`/`09e1641`/`2e276af`), the calypso-broker shared-session service + entry-window watchdog (`91b9088`..`a90430e`), the IBKR commission-model fix (`078049f`), and the overnight session-lost hardening (`3f67bc7`).

The branch is **deployed on `calypso-bot`** (A on IBKR paper; B/C dry-run via the broker). **The open operator action is the 2026-06-02 A+C live-paper flip** — `scripts/flip_ac_live.sh` is manual and requires a fresh same-ET-day paper-smoke PASS first (RB-8). **A Claude session should not start new code work unless the user explicitly requests it** — note the deferred items under "AUD3 detail" (the POS-004 settlement-merge bug especially) and AUD5's lower-severity register (`AUD5_FINDINGS.md` appendix) if asked what's left.

If the user asks "what's next" or "where are we", point them at the Gates above + their external timing, and the cutover runbook (`GATE2_DEPLOY_RUNBOOK.md`).

If the user pastes the probe output:
1. Interpret 6509 (`R`/`D`/`Z`)
2. Check that bid/ask fields are present
3. If all green → recommend Gate 2 (VM deploy)
4. If anything yellow/red → diagnose, do NOT proceed to deploy

---

## Pointer index — every doc in the migration tree

Read these for context as needed:

| Doc | Purpose | Read it when |
|---|---|---|
| **This file** (`PROJECT_STATUS.md`) | Current state + what's next | First, always |
| `CLAUDE.md` (repo root) | Operator reference — broker, strategy, deploy, troubleshoot | After this, for any operational question |
| `README.md` (repo root) | Branch-state pointer for anyone arriving at the repo | First-time visitor |
| `docs/migration/HYDRA_STANDALONE_REWRITE_PLAN.md` | Full F1-F7 + P1-P7 migration plan | When you need migration design rationale |
| `docs/migration/P7_AUDIT_FINDINGS.md` | 49 P7 findings + 3 round verdicts | When you need the P7 audit history |
| `docs/migration/POLISH_PLAN.md` | 12 polish items + amendments + 3-agent audit | When you need the polish-pass design rationale |
| `docs/migration/AUDIT2_FINDINGS.md` | The 4th audit cycle | When you need the AUD2 findings + verification |
| `docs/migration/PREFLIGHT_AUDIT_FINDINGS.md` | AUD3 — the 20-agent preflight audit: all 81 findings, adjudication, fixes | When you need the latest audit detail or the deferred-items list |
| `docs/migration/GATE2_DEPLOY_RUNBOOK.md` | The full Saxo→IBKR production cutover runbook (Gate 2) | When executing the cutover after Gate 1 is green |
| `docs/migration/MERGE_PLAN.md` | 8-chunk squash plan for merge to main | When the merge is approved |
| `docs/migration/LIVE_READINESS_CHECKLIST.md` | 10-gate go/no-go for live trading | Before any live flip (not on this branch) |
| `docs/migration/RUNBOOKS.md` | RB-1..RB-7 incident runbooks | When an alert fires |
| `docs/migration/DEFERRED_WORK.md` | DEF-1..DEF-7 explicitly-deferred items | When evaluating "should we fix X now?" |
| `docs/migration/F*_DESIGN.md` | Per-phase design docs | When you need to understand a specific migration phase |
| `docs/migration/archive/P7_GO_LIVE_PLAN.md` | The original 6-step go-live sequence (archived) | Background only — superseded by Gates 1-5 above |
| `docs/migration/D_GOLIVE_RUNBOOK.md` | Strategy D ("DC Time Machine") canonical go-live runbook — arm-gate, flip/rollback/flatten, smoke, DG-1..DG-11 readiness | When working Strategy D go-live (D is dry-run-LOCKED) |
| `docs/migration/D_GOLIVE_SCOPE_AND_AUDIT.md` | Strategy D go-live scope + audit with the explicit **NO-GO** verdict + risk register | When you need D's go/no-go rationale |
| `docs/migration/D_MVL_PHASE1_PLAN.md` | Strategy D minimum-viable-live phase-1 plan (transformer stripped) | When planning D's first live phase |
| `docs/NEW_STRATEGY_PLAYBOOK.md` | Repeatable, auditable procedure for adding a new dry-run-locked strategy variant (Steps 0–10) + audit log | When adding or auditing a new strategy variant |
| `docs/STRATEGY_GROUPING_REDESIGN.md` | Design + as-shipped state of the strategy taxonomy, comparability groups, dashboard picker/group tabs, and the second calendar (E) | When you need the grouping/taxonomy + variant-E design |
| `docs/HYDRA_STRATEGY_SPECIFICATION.md` | Strategy spec (entry rules, stops, VIX regime) | When you need strategy details |
| `docs/HYDRA_TRADING_JOURNAL.md` | HOMER-maintained daily journal | When you need historical session data |
| `docs/HYDRA_BUFFER_OPTIMIZATION.md` | Per-VIX-regime buffer study | When tuning stop buffers |
| `deploy/IBKR_CREDENTIALS_SETUP.md` | One-time IBKR OAuth setup + 3-check verification | VM deploy time |
| `deploy/README.md` | Which units to install on a fresh VM | VM deploy time |
| `services/argus/README.md` | ARGUS health checks (post-Polish #2) | When ARGUS fires an alert |
| `scripts/README.md` | Scripts inventory + which to use when | When picking a diagnostic script |

---

## Branch identity for verification

```bash
# Confirm you're on the right branch
git rev-parse --abbrev-ref HEAD          # → hydra-ibkr-standalone

# Confirm tests still pass (now deterministic at any wall-clock hour)
python -m pytest tests/ -q --ignore=tests/test_dashboard 2>&1 | tail -3
#  → all passed, integration/optional skipped (exact count grows per audit; see CI / run pytest)

# Confirm commit count is at/ahead of where this doc was written
git log --oneline main..HEAD | wc -l     # → ≥ 141

# Confirm zero open audit findings
grep -c "| OPEN |" docs/migration/P7_AUDIT_FINDINGS.md       # → 0
grep -c "| OPEN |" docs/migration/AUDIT2_FINDINGS.md          # → 0 (only "OPEN" is in section headers, not status cells)

# Confirm pip-audit is clean
.venv/bin/pip-audit -r requirements.txt 2>&1 | tail -1       # → No known vulnerabilities found
```

If any of the above disagrees with this file → this file is stale; update it before continuing.

---

## How to keep this file current

This file is the **single source of truth for project state**. When meaningful project state changes, update this file in the same commit. Specifically:

- After a gate clears (e.g., Tuesday probe passes) → update the gate's status
- After a new audit cycle → add a row to the audit-cycles table
- After a merge → archive this file's content to a "history" section and reset
- After any external blocker changes → update the "What's blocked" section

Do NOT let this file rot. A future Claude session reading a stale PROJECT_STATUS.md is worse than no file at all.
