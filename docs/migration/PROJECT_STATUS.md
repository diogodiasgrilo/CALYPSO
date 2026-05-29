# Project Status — where we are, what's next

**This file is the single-source-of-truth for the current state of the `hydra-ibkr-standalone` branch.** Any Claude session arriving at this repo should read this file first, before CLAUDE.md. CLAUDE.md is the operator reference (what the bot does, how to deploy, troubleshoot); this file is the *project state* (what's been done, what's in flight, what's blocked).

**Last updated:** 2026-05-29
**Last commit on branch:** `2e276af` (use `git rev-parse HEAD` to verify)
**Commits ahead of `main`:** 115 (use `git log --oneline main..HEAD | wc -l` to verify)
**Test suite:** 953 unit tests pass, 16 integration/optional tests skipped pending live paper account. The suite is now deterministic at any wall-clock hour (the intraday-OHLC tests were time-gated; fixed 2026-05-28).
**Branch is pushed to `origin`** (github.com/diogodiasgrilo/CALYPSO) as of 2026-05-29 — no longer laptop-only.

> ## ⚡ CUTOVER EXECUTED 2026-05-29 (~12:40 ET) — read this first
> Gate 1 passed (probe at 12:08 ET: SPX `6509='R'`, VIX `6509='R'`, real-time). The Saxo→IBKR cutover was run on `calypso-bot`:
> - **Saxo fully removed** — `token_keeper` + the 4 sibling units deleted; VM switched to `hydra-ibkr-standalone`; venv rebuilt (`ibind` 0.1.23).
> - **Strategy A is LIVE on IBKR paper** (account `DUR049068`), **dry-run**, fresh daily state, real-time SPX/VIX, `NRestarts=0`. Telegram + Google Sheets working. Dashboard locked to `127.0.0.1:8080` (SSH-tunnel only). Backups: `gs://calypso-backups` created + VM SA granted write (fixes the long-broken `db_backup`); pre-cutover state archived locally + off-site.
> - **✅ B and C RESOLVED via `calypso-broker` (deployed 2026-05-29 ~14:11 ET).** The one-brokerage-session-per-username limit (OAuth 1.0a; `compete:true` made 3 processes evict each other) is solved by a single shared broker-session service: `calypso-broker` owns the ONE IBClient (LST + ssodh/init + Tickler + 15-min re-auth loop); A/B/C run via a drop-in `BrokerClient` over loopback (`CALYPSO_BROKER_URL=http://127.0.0.1:8788`) and open NO sessions of their own. **All four (broker + A + B + C) are active, NRestarts=0, zero contention, ~0.9 IBKR req/s** (well under ~10/s). Design + follow-ups: [`BROKER_SESSION_SERVICE_DESIGN.md`](./BROKER_SESSION_SERVICE_DESIGN.md); constraint research: [`IBKR_MULTI_SESSION.md`](./IBKR_MULTI_SESSION.md).
> - **✅ P5a DONE — breaker/warmup alerting runs in the broker.** `IBKRAlertHooks` is instantiated inside `calypso-broker` (where the breakers live) and polled every 10s (breaker-transition / stuck-open / warmup-exhaustion), publishing to the same `calypso-alerts` Pub/Sub path. Strategy-side `BrokerClient.circuit_breakers` stays empty (no duplicate alerts); strategies still emit their own `ensure_connected`/broker-reachability alerts. Verified clean on deploy (no alert-poll errors; strategies degraded gracefully through the broker restart).
> - **Notes:** A/B/C run **dry-run** — flip `dry_run:false` to place real paper orders when ready. The morning re-auth gate now lives in the broker. `hydra*` units still carry (now-unused) `LoadCredentialEncrypted` — only the broker needs creds; harmless, clean up later. `ProtectDevices=yes` silently unsupported on this systemd 252 build (minor). Rollback: remove each unit's `…/broker.conf` drop-in + restart on own `IBClient`, or `main` @ `a77027f` + `~/cutover_backup_*` + `gs://calypso-backups/`.
> - **✅ Dashboard audited + wired to IBKR (2026-05-29).** Swept all 26 endpoints against the live deployment — `health / hydra.state / summary / entries (DB, with spx_at_entry) / market.ohlc / metrics.{cumulative,daily,performance,entries} / agents / widget` all return real IBKR + full Feb→today history. Two fixes: (1) **enabled comparison mode** (`DASHBOARD_COMPARISON_MODE_ENABLED=true`) so the A-vs-B-vs-C `/api/variants/*` view works (was 503 — off on Saxo too); (2) `live_state` today-SPX/VIX now sourced from `state.market_data_ohlc` (exact) instead of absent per-entry fields (closes audit FP6). Dashboard stays localhost-bound (M12).
> - **Telegram alert formatting fixed (2026-05-29).** The `process-trading-alert` Cloud Function (`cloud_functions/alert_processor/`) 400'd on every alert's Markdown (unbalanced `_`/`*` from snake_case fields) and fell back to plain text. `_md_escape()` now escapes dynamic content → valid Markdown. Redeployed (revision 00012); no 400s since. **Confirm formatted delivery in the Telegram chat** (a "Broker fix test ✅" was sent).
> - **Entry-window watchdog deployed.** `entry-window-watch.timer` runs the watchdog Mon–Fri at 10:20/10:50/11:20/11:35 ET (just after the 10:15/10:45/11:15 windows): verifies broker `/health` connected + A/B/C active + no entry-path errors; Telegram-alerts on any problem. Self-contained on the VM. Verified OK on a manual run.
> - **Now = Gate 2 paper-smoke watch:** observe A/B/C + the broker through the session + overnight + **Monday's** morning re-auth gate + entry windows (tomorrow is Saturday) before flipping to live-paper or merging. Still-open items to confirm by observation: Telegram command handler responsiveness (`/status`), and the full dry-run entry evaluation through the broker at Monday's windows (the watchdog flags failures).

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

Cumulative findings closed: **87**. Zero regressions across any cycle.

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

**Status:** ⏳ still pending a valid regular-session run. History: the Sunday 2026-05-24 probe showed `6509='Z'` (frozen — expected weekend/holiday); a 2026-05-28 ~21:09 ET run was **inconclusive** (market closed → all instruments NO DATA, incl. the SPY control) — auth + contract qualification worked, only live data-entitlement is unconfirmed. Next opportunity: **any regular session, ~09:35 ET or later** (Friday 2026-05-29).

Re-running during a regular session will show:
- ✅ `6509='R'` on SPX/VIX → cleared for the cutover (Gate 2)
- ⚠️ `6509='D'` → IBKR account has delayed-only entitlement; do NOT trade live; fix subscription before proceeding
- ❌ Bid/ask still missing during market hours → broader subscription issue; investigate

Command (user has the OAuth env vars from 1Password in their shell):
```bash
cd "/Users/ddias/Desktop/CALYPSO/Git Repo"
source .venv/bin/activate
python scripts/probe_ibkr_market_data.py 2>&1 | tee scripts/probe_mktdata_$(date +%H%M%S).log
```

Expected: paste the output to the Claude session for interpretation.

### Gate 2 — Saxo→IBKR cutover on `calypso-bot` (operator + Claude pair)

**Status:** ⏳ blocked on Gate 1 passing. **Full runbook: [`GATE2_DEPLOY_RUNBOOK.md`](./GATE2_DEPLOY_RUNBOOK.md).**

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

## Active work (none — branch is at a stable rest point)

No code work is in flight. The AUD3 preflight audit + its 20 fixes are committed (`6882e82`, `09e1641`, `2e276af`) and pushed; the suite is green at any hour. The branch is in a deployment-ready pre-flight state, gated on the Gate 1 probe. **A Claude session should not start new code work unless the user explicitly requests it** — but note the deferred items under "AUD3 detail" (the POS-004 settlement-merge bug especially) if asked what's left.

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
| `docs/migration/P7_GO_LIVE_PLAN.md` | The original 6-step go-live sequence | Background only — superseded by Gates 1-5 above |
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
#  → 953 passed, 16 skipped

# Confirm commit count is at/ahead of where this doc was written
git log --oneline main..HEAD | wc -l     # → ≥ 115

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
