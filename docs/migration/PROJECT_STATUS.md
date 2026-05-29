# Project Status — where we are, what's next

**This file is the single-source-of-truth for the current state of the `hydra-ibkr-standalone` branch.** Any Claude session arriving at this repo should read this file first, before CLAUDE.md. CLAUDE.md is the operator reference (what the bot does, how to deploy, troubleshoot); this file is the *project state* (what's been done, what's in flight, what's blocked).

**Last updated:** 2026-05-24
**Last commit on branch:** `a4382a1` (use `git rev-parse HEAD` to verify)
**Commits ahead of `main`:** 111 (use `git log --oneline main..HEAD | wc -l` to verify)
**Test suite:** 920 unit tests pass, 15 integration tests skipped pending live paper account

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
| **AUD2 (this cycle)** | 6 parallel domain agents + Round 2 verify + senior overseer | 4 H + 7 M + 17 L | All H+M fixed with regression tests; 7 L fixed; 10 L accepted as observations | **920** |

Cumulative findings closed: **67**. Zero regressions across any cycle.

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

### Gate 1 — Tuesday probe re-run (external — user runs)

**Status:** ⏳ pending Tuesday 2026-05-26 ~09:35 ET (Memorial Day Monday closes the market)

The Sunday 2026-05-24 probe showed `6509='Z'` (frozen — expected for weekend + holiday). Re-running during Tuesday's regular session will show:
- ✅ `6509='R'` on SPX/VIX → cleared for VM deploy (Step 5)
- ⚠️ `6509='D'` → IBKR account has delayed-only entitlement; do NOT trade live; fix subscription before proceeding
- ❌ Bid/ask still missing during market hours → broader subscription issue; investigate

Command (user has the OAuth env vars from 1Password in their shell):
```bash
cd "/Users/ddias/Desktop/CALYPSO/Git Repo"
source .venv/bin/activate
python scripts/probe_ibkr_market_data.py 2>&1 | tee scripts/probe_mktdata_$(date +%H%M%S).log
```

Expected: paste the output to the Claude session for interpretation.

### Gate 2 — VM deploy + paper smoke (operator + Claude pair)

**Status:** ⏳ blocked on Gate 1 passing

Procedure:
1. Follow `deploy/IBKR_CREDENTIALS_SETUP.md` for the 6 IBKR OAuth credentials.
2. Run the mandatory 3-check pre-start verification before `systemctl enable hydra`.
3. Watch `journalctl -u hydra -f` through the first connection + a full morning of dry-run + a full afternoon of paper trading.

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

No code work is in flight. The branch is in a deployment-ready pre-flight state. **A Claude session should not start new code work unless the user explicitly requests it.**

If the user asks "what's next" or "where are we", point them at the Gates above + their corresponding external timing.

If the user pastes the Tuesday probe output:
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
| `docs/migration/AUDIT2_FINDINGS.md` | The most recent (4th) audit cycle | When you need the AUD2 findings + verification |
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

# Confirm tests still pass
python -m pytest tests/ -q --ignore=tests/test_dashboard 2>&1 | tail -3
#  → 920 passed, 15 skipped

# Confirm commit count is at/ahead of where this doc was written
git log --oneline main..HEAD | wc -l     # → ≥ 111

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
