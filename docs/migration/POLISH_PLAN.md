# World-Class Polish Plan — `hydra-ibkr-standalone`

**Status:** PLAN — awaiting Plan-agent audit before execution.
**Branch:** `hydra-ibkr-standalone` @ `af4c11f` (after the 3-round audit + CLAUDE.md rewrite + Sunday probe).
**Scope:** Lift the branch from "code-audited" to "professional shop" without changing any IBKR-trading code path. Everything here is around-the-code: alerts, runbooks, observability, docs.
**Constraint:** No `IBClient.connect()` / order-placement / stop-loss code changes. Round 3 said yes to "bet the house" on the existing code; we don't churn that.
**Constraint:** Each item produces a regression test where it touches code; doc-only items don't.
**Final gate:** Multi-agent audit + full `pytest tests/` pass before commit.

---

## Item 1 — Telegram alerts on IBKR-specific failure modes

**Why:** `ib_retry.py` logs WARNING/ERROR on breaker open + non-retryable error but never pages. `ib_client.py` logs WARNING on snapshot warmup exhaustion (P7-audit M17) but never pages. A 3am breaker-OPEN on the orders family means trades are silently failing for at least 30s — invisible without an alert.

**Design:**
1. **Source of truth for alerts**: `bots/hydra/main.py` polls `broker.circuit_breakers` and `broker._iserver_primed` after each `strategy.run_strategy_check()` iteration, compares to last-seen state, fires Telegram on transition.
2. **No IBClient → AlertService dependency.** IBClient stays broker-agnostic. The polling lives in main.py where `strategy.alert_service` is already accessible (lines 282, 650, 762).
3. **State tracked across iterations:**
   - `last_breaker_states: dict[str, CircuitState]` — keyed by family name
   - `last_iserver_primed: bool` — was True last iteration?
   - `last_snapshot_warning_count: int` — running counter (read from a new IBClient counter)
4. **Alert mapping** (reuses existing AlertType enum — no new types):
   - Breaker `CLOSED → OPEN` on **orders** family → CRITICAL `CIRCUIT_BREAKER` ("Cannot place orders — orders breaker tripped — manual intervention required")
   - Breaker `CLOSED → OPEN` on **market** or **portfolio** family → HIGH `API_ERROR` ("Broker degraded — {family} breaker OPEN — data flow may be stale")
   - Breaker `OPEN → CLOSED` (probe recovery) → LOW `CONNECTION_RESTORED` ("{family} breaker recovered")
   - `_iserver_primed: True → False` mid-session → HIGH `API_ERROR` ("iserver session lost — session steal or rate-limit reset")
   - Snapshot warmup-exhaustion count incremented this iteration → MEDIUM `DATA_QUALITY` ("Snapshot warmup exhausted for conid {conid} — {N}th occurrence today")
5. **Idempotence:** state transitions only fire once. A breaker that stays OPEN for 10 minutes pages once, not 600 times. Recovery (`OPEN → HALF_OPEN → CLOSED`) is also a single LOW alert.
6. **IBClient surface additions** (minimal):
   - `IBClient.snapshot_warmup_exhausted_count` — module-level int incremented in `_snapshot_with_preflight` when warmup loop exits without populated data. Already logs WARNING; just add the counter.
   - `IBClient.is_iserver_primed` — read-only property returning `self._iserver_primed`.
7. **Test plan:**
   - Unit test: `tests/test_ib_client_reads.py` — `TestSnapshotExhaustionCounter` (3 tests): counter starts at 0; increments on warmup exhaustion; doesn't increment on success.
   - Integration test: `tests/test_main_alert_hooks.py` (new file) — mock breaker state transitions, assert `AlertService.send_alert` called with correct AlertType + Priority. 5 tests.
8. **Files touched:** `shared/ib_client.py` (~20 lines), `bots/hydra/main.py` (~80 lines for poll-and-alert helper), `tests/test_ib_client_reads.py` (+3 tests), `tests/test_main_alert_hooks.py` (new, 5 tests).

**Verification:** `python -m pytest tests/test_ib_client_reads.py::TestSnapshotExhaustionCounter tests/test_main_alert_hooks.py -v` returns 8 passed.

**Risk:** LOW. main.py poll loop is short-circuit on no state change; zero impact when nothing's wrong. New counter is a simple int increment.

---

## Item 2 — ARGUS rewrite for IBKR

**Why:** `services/argus/health_check.sh` currently checks `token_keeper.service` (DEAD on this branch — Saxo-only) and `/opt/calypso/data/saxo_token_cache.json` (DEAD — IBKR has no such file). Two of seven checks are inert. If `token_keeper.service` doesn't exist on the VM, line 64's `systemctl is-active --quiet token_keeper` returns non-zero, ARGUS fires a FAIL alert every 15 minutes — false-positive alert spam.

**Design:**
1. **Drop** Check 2 (token_keeper service) and Check 3 (token cache freshness) — both Saxo-only.
2. **Replace** with IBKR-era checks:
   - **Check 2 (replacement): IBClient connection health via state-file `last_iteration_at` timestamp.** HYDRA writes a `last_heartbeat_at` ISO timestamp into `hydra_state.json` every monitoring loop iteration during market hours. ARGUS reads it; if older than `STATE_HEARTBEAT_MAX_AGE_MIN` (default 5 min during market hours, suppressed off-hours), FAIL.
   - **Check 3 (replacement): Circuit-breaker OPEN count in last 15 minutes.** Greps `journalctl -u hydra --since '15 minutes ago'` for `"CLOSED → OPEN"` events. >= 1 on **orders** family in 15 min → FAIL. Any breaker OPEN on any family → WARNING.
3. **Keep** Check 1 (HYDRA process alive), Check 4 (disk), Check 5 (memory), Check 6 (log staleness), Check 7 (state file JSON integrity) — all broker-agnostic.
4. **Constants update at top of file:**
   - Remove `TOKEN_CACHE` path
   - Remove `TOKEN_MAX_AGE_MIN`
   - Add `STATE_HEARTBEAT_MAX_AGE_MIN=5`
   - Add `BREAKER_OPEN_FAIL_FAMILIES=("orders")`
5. **`last_heartbeat_at` write** in `bots/hydra/main.py` — append at the end of each iteration's main try block. Tiny addition (~3 lines). Already a write target via existing `_save_state_to_disk()` calls — just add the field to `daily_state` or write directly.

**Test plan:**
- Shell unit test (existing pattern in `services/argus/` is bash; no formal tests there). Smoke-test by running `health_check.sh` against a fake state file with known timestamps.
- For the Python-side change (writing `last_heartbeat_at`): add `tests/test_hydra_state_heartbeat.py` — 2 tests confirming the field gets written each iteration.

**Files touched:** `services/argus/health_check.sh` (rewrite checks 2+3, ~50 lines), `bots/hydra/main.py` (~5 lines for heartbeat write), `tests/test_hydra_state_heartbeat.py` (new, 2 tests).

**Verification:** Test runs pass. Manual shell run of `health_check.sh` on a freshly-stopped HYDRA → FAILS on stale heartbeat (correct).

**Risk:** MEDIUM. ARGUS firing false positives is annoying but not dangerous. Worst case: tighten the threshold post-deploy. The state-file write is append-only so cannot corrupt existing state.

---

## Item 3 — Incident runbooks

**Why:** Zero runbooks exist today for IBKR-specific failure modes. When the bot misbehaves at 11:46 ET on a Wednesday and you've been pulled into something else, you don't want to be reconstructing the diagnostic steps from memory.

**Design:** Single new file `docs/migration/RUNBOOKS.md` with 5 runbooks. Each follows a fixed shape: **Symptom → Triage steps → Root cause → Resolution → Verification**.

- **RB-1: Bot says authenticated but not connected** — `auth/status` returns `authenticated=true, connected=false`. Triage: `ensure_connected()`; if fails 3× → `disconnect()` + `connect()`. Root cause: usually IBKR's brokerage session expired (different from LST). Resolution: full reconnect.
- **RB-2: All orders timing out** — every `place_and_wait_for_fill` returns `status="timed_out"`. Triage: check `circuit_breakers["orders"].state`; if OPEN → wait 30s for HALF_OPEN probe; if still OPEN → check IBKR's status page; if IBKR up but breaker OPEN → `force_reset()` after confirming no working orders.
- **RB-3: SPX/VIX returns None mid-session** — `_read_index_price` returns None. Triage: run `scripts/probe_ibkr_market_data.py` from a separate shell; check 6509 availability flag; check entitlements in IBKR account portal.
- **RB-4: Session stolen by another client** — auth/status shows `competing=true`. Triage: identify the other client (SaxoTraderGO, TWS, another IBClient process); sign it out; bot re-reconnects via `ensure_connected()`.
- **RB-5: VM deploy broken — rollback** — `systemctl status hydra` shows `failed`. Triage: `journalctl -u hydra -n 100` → identify failure; if code regression → `git -C /opt/calypso checkout <previous-good-sha>` + `systemctl restart`; if credentials regression → rotate via `IBKR_CREDENTIALS_SETUP.md`.

**Each runbook includes** Telegram alert example (so operator can match the alert text to the runbook).

**Files touched:** `docs/migration/RUNBOOKS.md` (new, ~400 lines). Pointer added to `CLAUDE.md` documentation table.

**Verification:** None automated — these are operator references. Quality-check via Plan-agent audit.

**Risk:** None — docs only.

---

## Item 4 — Backup verification

**Why:** `deploy/db_backup.service` exists and covers the right files (`backtesting.db` + `hydra_metrics.json` + `hydra_state.json`). `deploy/db_backup.timer` runs daily at 23:00 UTC. BUT: we have no documentation that the timer is enabled, no verification that `gsutil` works as the calypso user, and no recovery runbook for "I need to restore from yesterday's backup".

**Design:**
1. Add operator-facing **backup section** to `CLAUDE.md` documenting:
   - What gets backed up
   - When (daily 23:00 UTC = 6 PM EST / 7 PM EDT, post-settlement)
   - How to verify the timer is enabled (`systemctl list-timers db_backup.timer`)
   - How to verify gsutil permissions (`sudo -u calypso gsutil ls gs://calypso-backups/ | head`)
   - **Restore procedure** (4 lines)
2. Add a backup-status check to **ARGUS** (Item 2's scope extension): check `gs://calypso-backups/hydra_state_$(date +%Y%m%d).json` exists by EOD UTC; warn if missing.

**Files touched:** `CLAUDE.md` (new section, ~40 lines), `services/argus/health_check.sh` (+1 check, gated on `[[ $(date +%H) -ge 23 ]]`).

**Verification:** Manual operator step on Tuesday VM session (not testable here without GCS access).

**Risk:** LOW. The ARGUS check is additive; documentation can't break code.

---

## Item 5 — State-file snapshot-before-restart

**Why:** `systemctl restart hydra` mid-day will reload state from `data/hydra_state.json`. If that file is corrupted (mid-write crash, disk full, fcntl race) the bot starts fresh and forgets the day's entries — losing P&L history at minimum, opening positions that the bot doesn't know about at worst.

**Design:**
1. Add `ExecStartPre=` to `deploy/hydra.service`:
   ```
   ExecStartPre=/opt/calypso/scripts/pre_start_snapshot.sh
   ```
2. New script `scripts/pre_start_snapshot.sh`:
   - Copies `data/hydra_state.json` → `data/state_snapshots/hydra_state.pre_restart_$(date +%Y%m%d_%H%M%S).json`
   - Keeps last 20 snapshots; deletes older.
   - Bash only — no Python dependency.
   - Exit code always 0 (snapshot failure must not block bot start; logged but not fatal).
3. Document the snapshot directory + restore procedure in **RB-5 (Item 3)** so operator knows to grab from `state_snapshots/` if a restart eats state.

**Test plan:**
- Shell unit: `tests/test_pre_start_snapshot.sh` (bash, calls the script against a tmpdir state file, asserts snapshot exists in `state_snapshots/`).

**Files touched:** `deploy/hydra.service` (+1 line), `scripts/pre_start_snapshot.sh` (new, ~25 lines), `tests/test_pre_start_snapshot.sh` (new), `.gitignore` (+ `data/state_snapshots/`).

**Verification:** `bash tests/test_pre_start_snapshot.sh` exits 0.

**Risk:** LOW. The ExecStartPre runs as root inside the systemd unit BEFORE `User=calypso` drops privileges — so the script needs `User=root` execution (the default for ExecStartPre when no `+` prefix used) and must chown the snapshot to calypso. Will check this in execution.

Wait — verifying with systemd docs: `ExecStartPre=` actually runs with the same User= as `ExecStart=` UNLESS prefixed with `+`. Since hydra.service has `User=calypso`, the snapshot script runs as calypso. Calypso has rw access to `data/`. Fine.

---

## Item 6 — HYDRA_STRATEGY_SPECIFICATION.md de-Saxo pass

**Why:** Spec is dated 2026-05-01, version 1.26.0, 16 Saxo/UIC references. It's the canonical strategy reference and is misleading.

**Design:**
1. Update header: version `2.0.0-rc.1`, date `2026-05-22`, status `IBKR-standalone branch, paper-only`.
2. Add **"Broker Integration"** section near top — 1 paragraph pointing at CLAUDE.md's IBKR Integration section.
3. Search/replace `UIC` → `conid` where the reference is to the live IBKR identifier; preserve `UIC` where it's historical context (e.g., "originally Saxo UIC, now stores IBKR conid in the same field name").
4. Update entry-schedule section to reflect the current 2-base + E6 conditional (was already there but version-stamped 1.26.0).
5. Replace "Saxo" mentions of API behavior with "the broker" where the behavior is generic, or "IBKR Web API" where the behavior is IBKR-specific.
6. Add a **"What's no longer in scope"** subsection listing the Saxo-era features (Saxo activity stream, token_keeper, position_id-keyed reconciliation) and pointing at the F4 design for the IBKR replacement.

**Files touched:** `docs/HYDRA_STRATEGY_SPECIFICATION.md` (in-place edits, ~30 line-changes).

**Verification:** `grep -c "Saxo\|UIC" docs/HYDRA_STRATEGY_SPECIFICATION.md` drops from 16 to fewer (the remaining ones must be historical context, verified by reading each).

**Risk:** None — docs.

---

## Item 7 — Top-level README update

**Why:** Repo root `README.md` opens with "Multi-strategy options trading platform using Saxo Bank API" and lists 5 bots. Anyone arriving at the repo today reads pre-migration content.

**Design:**
1. Add **branch-state banner** at top:
   > **You're on `hydra-ibkr-standalone`.** This branch contains the IBKR Web API rewrite of HYDRA, currently paper-only. The pre-migration codebase (Saxo + 5 bots) lives on `main`. See `CLAUDE.md` for current state, `docs/migration/HYDRA_STANDALONE_REWRITE_PLAN.md` for the migration history.
2. Rewrite the "Trading Strategies" section: HYDRA is the only active strategy; the other 4 are listed as **kill-switched** (`DISABLED_FOR_SAFETY=True`) with a 1-line note pointing at `main` for pre-migration usage.
3. Rewrite "Tech Stack" to reflect IBKR (drop Saxo OpenAPI, add ibind OAuth 1.0a, systemd LoadCredentialEncrypted=).
4. Keep the deployment / dashboard / agent suite sections — broker-agnostic.

**Files touched:** `README.md` (in-place edits).

**Verification:** Visual review. `grep -c "Saxo" README.md` from current N → minimal (kept only in the "see `main` for pre-migration" pointer).

**Risk:** None.

---

## Item 8 — Pre-merge squash plan

**Why:** 96 commits on the branch since `main`. Many are intermediate (probe iterations, audit-doc tweaks). Squashing into logical chunks before merge keeps `main`'s `git log` navigable for future maintainers.

**Design:** Document, do **not** execute the squash. The plan goes in `docs/migration/MERGE_PLAN.md` with:
1. Commit-by-commit list grouped into 8-10 logical chunks (F1 / F2 / F3 / F4 / F5 / F6 / F7 / P1-P5 / P6 / P7 audit + fixes / CLAUDE.md / Polish).
2. Recommended squash strategy per chunk (squash to one, keep all, drop pure-revert pairs).
3. Final merge command (rebase-and-merge vs. squash-merge vs. merge-commit — recommendation: squash-merge of each phase chunk to preserve coarse history but lose the noise).
4. Pre-squash safety check: `git log --oneline main..HEAD | wc -l` → `git log --oneline <squashed-tip> | wc -l` (expected ~10 commits post-squash).
5. Branch protection on `main`: list of recommended GitHub settings (require PR review, require status checks pass, no force-push).

**Files touched:** `docs/migration/MERGE_PLAN.md` (new).

**Verification:** Plan-agent reviews. User approves before any squash happens.

**Risk:** None — plan only.

---

## Item 9 — Live-readiness checklist

**Why:** Pre-start verification (`IBKR_CREDENTIALS_SETUP.md`) confirms the systemd unit works. Live-readiness is a different question: **is this bot ready to trade real money next session?** — which requires recent paper success, no open audit findings, credentials valid for live (not paper), etc.

**Design:** New file `docs/migration/LIVE_READINESS_CHECKLIST.md` with a structured checklist an operator goes through before flipping a live switch:
1. **Branch state:** on `main`, not a feature branch
2. **Audit state:** `P7_AUDIT_FINDINGS.md` shows 0 OPEN findings
3. **Test state:** `python -m pytest tests/` shows 100% pass (no skips on non-integration)
4. **Paper history:** last 5 full trading sessions on paper without manual intervention
5. **Live credentials:** new `live` keypair issued by IBKR; `LoadCredentialEncrypted=` references `live`/ paths; `load_credentials("live")` in main.py
6. **VM state:** `hydra.service` shows recent successful start, no `failed` in journal in last 24h
7. **Backup verified:** `gsutil ls gs://calypso-backups/` shows yesterday's snapshot
8. **Approvals:** explicit user sign-off recorded in commit message
9. **Halt plan:** explicit "if this happens, stop the bot" criteria documented
10. **Position size:** start at 1 contract for week 1 regardless of config

**Files touched:** `docs/migration/LIVE_READINESS_CHECKLIST.md` (new).

**Verification:** Plan-agent reviews. Not used until live flip approved.

**Risk:** None — plan only.

---

## Execution order

1. **Item 8 + 9 first** (docs only, lowest risk, gets the plan/checklists onto disk before code changes).
2. **Item 3 + 6 + 7** (more docs).
3. **Item 5** (`pre_start_snapshot.sh`) — small, mechanical, isolated.
4. **Item 4** (CLAUDE.md backup section + ARGUS extension) — small.
5. **Item 1** (Telegram alerts) — biggest code change, most testing.
6. **Item 2** (ARGUS rewrite) — depends on Item 1's `is_iserver_primed` property + Item 4's optional backup check.
7. **Final audit:** spawn 3 parallel domain agents to verify the polish pass; senior overseer to confirm.

---

## Test discipline

After each item:
- `python -m pytest tests/ -q --ignore=tests/test_dashboard` must show 885+ passed (zero regressions).
- New tests must run AND pass (not just be added).
- Shell-script changes (Items 2, 5) verified by running the script against a known-good fixture.

## Commit discipline

One commit per item (or per coherent sub-chunk). Each commit message:
- States which item (Item N — title)
- Lists files touched + lines added/removed
- Lists tests added + their pass status
- Notes any deviation from this plan

## Audit gate before any commit

This plan goes to a Plan agent FIRST. If it finds gaps, the plan gets amended and re-audited. Only after plan approval does execution start.

After all items committed: 3 parallel domain agents (IBKR/strategy/ops + tests + senior overseer) re-audit. PASS gate before declaring polish complete.

---

# Plan-agent audit amendments (2026-05-24)

Plan v1 audited by a Plan agent. Verdict: **AMEND**. Three non-negotiable concerns + ~17 secondary improvements. Amendments below are folded INTO the items above before execution; this section is the audit trail.

**Pinned test-suite baseline (audit gate):** 900 collected, **885 passed, 15 skipped**. Every commit in this polish pass must hold or improve this number — no regressions.

## Critical amendments to Item 1 (Telegram alerts)

### A1.1 — Drop `_iserver_primed` polling. Wire the alert to `ensure_connected()` instead.

`_iserver_primed` is set to True in `_ensure_iserver_primed()` (line 1331) and reset to False ONLY in `disconnect()` and `__init__` (lines 481, 771). It is never reset on a mid-session 401/410. So a `True → False` transition only occurs on clean shutdown — which is not the failure mode we wanted to detect.

The right detection point is **`ensure_connected()` returning False** in the intraday-gate at `bots/hydra/main.py:473`. That call DOES do a full auth/status round-trip and DOES detect a stolen session. Currently it logs ERROR + `break`s for systemd restart, but no Telegram fires.

**Replace** the planned `_iserver_primed` alert with: fire **HIGH `API_ERROR`** ("IBKR session lost mid-day — bot exiting for systemd restart — likely competing session or LST expiry") immediately BEFORE the `break` at main.py:476. This is the highest-value alert in the whole polish pass.

### A1.2 — Dedupe snapshot-warmup alert at the day level, not per-iteration.

Snapshot warmup exhaustion can fire 20+ times in a single iteration (every option chain leg) during an illiquid 3:55 PM ET chain read. Per-iteration MEDIUM alerts → flood.

**Replace** the planned "increment-this-iteration → alert" with:
- `IBClient.snapshot_warmup_exhausted_count` counter — incremented per exhaustion, NEVER reset within a session.
- main.py polls `count > 0 AND first_exhaustion_today_alerted == False` → fire ONE MEDIUM `DATA_QUALITY` alert per day on the first occurrence ("First snapshot warmup exhaustion of the day on conid {conid} — degraded data flow").
- After 25 exhaustions in a single day, fire ONE additional HIGH alert ("25+ snapshot exhaustions today — data flow severely degraded").
- Day boundary resets both the "first today" flag and the "25+" flag.

### A1.3 — Add repeating reminder on stuck-OPEN breaker.

Stuck-OPEN breakers (state stays OPEN > 15 min) fire one CRITICAL at minute 0 and silence after that under v1's idempotence rule. Real-world failure mode: operator dismisses the alert at minute 0, doesn't see another reminder, forgets.

**Add**: while a breaker remains OPEN, fire one HIGH `API_ERROR` reminder every 15 min (capped at 4 reminders/day per family).

### A1.4 — Risk reclassification: Item 1 is MEDIUM, not LOW.

80 lines of new code in the bot's hot loop is not "LOW risk." Acknowledged.

### A1.5 — Add 6th test: zero spurious alerts on stable state.

`test_main_alert_hooks.py` must include a test where the poll runs N iterations with stable state → `send_alert` is called 0 times. Cheap pin against future state-bleed bugs.

### A1.6 — Test fixture design committed up front.

The new file needs a `MockBrokerWithState` fixture that exposes `circuit_breakers`, `snapshot_warmup_exhausted_count`, and a configurable `ensure_connected` return value. Design this in the test file's top section (with docstring) before writing test bodies, so the fixture surface is a stable interface.

## Critical amendment to Item 2 (ARGUS rewrite)

### A2.1 — `last_heartbeat_at` writes go through `_save_state_to_disk` only.

Race risk: a torn write to a separate file mid-iteration. **Commit** to writing `last_heartbeat_at` as a field inside `daily_state`, persisted via the existing atomic `_save_state_to_disk` (temp+rename at `strategy.py:9659-9666`). The status_interval cadence (~10s) is well under ARGUS's 5-min threshold.

### A2.2 — ARGUS Check 3 (breaker grep) must use a stable regex + accept multi-family.

The exact log format from `shared/ib_retry.py:130` is `"CircuitBreaker[%s] %s → OPEN — %s"`. Plan's regex needs:
- `'CircuitBreaker\[' family '\] \(CLOSED\|HALF_OPEN\) → OPEN'` — accept both initial trip AND probe-failure re-open
- Multi-family check via `BREAKER_OPEN_FAIL_FAMILIES=("orders")` (array) — document the array semantics (any one of the families OPEN → FAIL).

### A2.3 — Document the auth-health dependency on Item 1.

Add a comment in `health_check.sh` near Check 3:
```bash
# NOTE: Auth-degradation (mid-session session loss) is NOT detected
# here; it's the responsibility of bots/hydra/main.py's
# ensure_connected() gate + the API_ERROR Telegram alert. ARGUS only
# detects HYDRA-process-alive, not broker-session-alive.
```

### A2.4 — Holiday-aware `is_market_hours()`.

ARGUS's log-staleness Check 6 fires false-positives on holidays. **Add** a `is_market_holiday` shell function that grep's the calendar (or shells out to `python -m shared.event_calendar is_holiday`). Suppress Check 6 on holidays.

## Critical amendment to Item 5 (snapshot-before-restart)

### A5.1 — Retention: 50 snapshots, NOT 20.

20 = 4 days at 5 restarts/day. Bump to 50 (cheap on disk, ~1 MB ceiling).

### A5.2 — Atomic write semantics for the snapshot itself.

`cp src dst` is NOT atomic. Use `cp src dst.tmp && mv dst.tmp dst` (mv on same filesystem is atomic). Otherwise an interrupted snapshot leaves a torn file in `state_snapshots/`.

### A5.3 — Add `TimeoutStartSec=120` to hydra.service.

ExecStartPre with default 90s timeout could fail on slow disk. 120s ceiling gives headroom; bot start is still fast in the happy path.

### A5.4 — Scope: state file only (defer metrics + DB to a future item).

`hydra_metrics.json` snapshots: deferred. The daily `db_backup.timer` already covers this (and `backtesting.db`). State file is the one that needs sub-daily snapshots because it's the only one that changes mid-iteration.

## Critical amendment to Item 4 (backup verification)

### A4.1 — Backup-status check timing fix.

Original plan gates on `[[ $(date +%H) -ge 23 ]]` UTC. The timer fires AT 23:00 UTC; gsutil cp takes 30-90s; backup may not be visible by 23:01. **Change**: gate on "is it now after 23:30 UTC and have we seen today's snapshot in gs://" — give the timer 30 minutes of headroom.

## Critical amendment to Item 6 (HYDRA_STRATEGY_SPECIFICATION.md)

### A6.1 — Manual review, not bulk search/replace.

Item 6's "search/replace UIC → conid" is risky. The code field names remain `*_uic` (per H13 — the rename was abandoned), but stored values are now IBKR conids. The spec must explain this dual-naming explicitly, not silently rename. **Manual review of every Saxo/UIC match required.**

## Critical amendment to Item 3 (runbooks)

### A3.1 — RB-2 wait-30s clarification.

`half_open_after_seconds: float = 30.0` is the TIMER for HALF_OPEN transition, but the probe only fires on the NEXT REQUEST after the timer elapses. RB-2 must say "wait 30s + the next order attempt" or document a manual probe trigger.

### A3.2 — Add RB-6: "Naked short detected — bot still running"

NAKED_POSITION alert exists in the enum (line 102, CRITICAL). C1 fixed the close path but a future regression would surface as this alert. New runbook covering: identify the leg via `_handle_naked_short` log, manual close via IBKR portal, post-mortem trigger.

## Cross-cutting amendments

### A-X1 — Delete or `.disabled` rename `deploy/token_keeper.service`.

The service file is for Saxo-era token refresh — DEAD on this branch. If `setup_vm.sh` enumerates `deploy/*.service`, it gets installed (false-positive ARGUS Check 1 territory). **Action**: rename to `deploy/token_keeper.service.disabled-on-this-branch` AND add a one-line README in `deploy/` explaining what `.disabled-on-this-branch` means.

### A-X2 — Review `deploy/hydra_variant_b.service` and `hydra_variant_c.service`.

If they don't have the same `LoadCredentialEncrypted=` block as `hydra.service`, they're either broken or leak credentials. **Action**: read both, confirm sandbox parity, document in commit.

### A-X3 — Open-positions reconciliation on restart (Gap #7).

If the bot crashes after fill but before state-write, restart loads state without the new position → bot may double-up. The cOID-retry-safety covers within-call (P7-audit H12), NOT across-process restart.

**Action**: as part of the chaos test (Item 11), specifically verify the restart-after-fill-before-state-write case. If reconciliation finds an untracked broker position, the bot must NOT re-attempt entry; it must reconcile to that position. Verify the existing F4 reconciliation does this — and if not, file as a new finding to fix in code.

## New items added

### Item 10 — Secret-leak audit

Manual + grep pass: do decrypted credentials appear in trade logs, journal output, exception backtraces, dashboard payloads, Telegram alert text, or Pub/Sub envelopes? Specifically check ibind exception strings for OAuth Authorization headers. ~1 hour. Optional new unit test: a `_str_` / `__repr__` test that asserts IBClient instance does not include the access token secret in its string representation.

### Item 11 — Chaos / SIGKILL recovery paper test

Single manual test, run during paper validation:
1. Bot running on paper, has placed an entry
2. `kill -9 $(pgrep -f hydra.main)` mid-stop-monitoring
3. Verify: state file is intact JSON (atomic write held), systemd restarts within 30s, no duplicate orders placed (cross-process cOID dedup OR existing reconciliation), no untracked positions.

Documented as a one-shot test step in `LIVE_READINESS_CHECKLIST.md` Item 9. Output of the test goes into the journal for the day.

### Item 12 — Requirements pinning + CVE audit

Verify `requirements.txt` / `requirements-lock.txt` pins ibind ≥0.1.23 exactly (not range), pyOpenSSL pinned, pycryptodome pinned. Run `pip-audit` on the lock file. Document findings in commit.

## Sequencing amendment

Items 10, 11, 12 are LOW-effort and HIGH-value. Slot them in:
- Item 10: between current Items 3 and 5 (docs phase → quick code-grep pass).
- Item 11: documented in Item 9's checklist; performed manually during paper validation.
- Item 12: between current Items 4 and 1 (before any code change, to know dependency state).

Updated execution order:
1. **Plan amendments** (this section) committed before any code.
2. **Item 8** (merge plan — doc).
3. **Item 9** (live-readiness checklist — doc).
4. **Item 3** (runbooks — doc).
5. **Item 6** (HYDRA spec de-Saxo — doc, manual review).
6. **Item 7** (README — doc).
7. **Item 10** (secret-leak audit — read-only grep + optional test).
8. **Item 12** (requirements pinning — read + verify).
9. **Item 5** (pre_start_snapshot.sh + ExecStartPre).
10. **Item 4** (backup verification — CLAUDE.md + ARGUS extension).
11. **A-X1, A-X2** (delete/rename token_keeper.service + verify variant units).
12. **Item 1** (Telegram alerts — biggest code change, all amendments above).
13. **Item 2** (ARGUS rewrite — depends on Item 1's IBClient surface).
14. **Final audit**: 3 parallel domain agents + senior overseer.

Item 11 deferred to paper-validation phase (operator step, not a code change).

## Final amended risk table

| Item | v1 risk | Amended risk |
|---|---|---|
| 1 — Alerts | LOW | **MEDIUM** |
| 2 — ARGUS | MEDIUM | MEDIUM |
| 3 — Runbooks | None | None |
| 4 — Backup | LOW | LOW |
| 5 — Snapshot script | LOW | LOW |
| 6 — Spec | None | None |
| 7 — README | None | None |
| 8 — Merge plan | None | None |
| 9 — Live checklist | None | None |
| 10 — Secret-leak | None | None (read-only) |
| 11 — Chaos test | None (docs) | None (deferred to paper) |
| 12 — Requirements | None (read) | None |

## Plan v2: PROCEED

All amendments folded in. Execution begins with the 6 doc items (8, 9, 3, 6, 7, 10), then code items (12, 5, 4, A-X1, A-X2), then the alert + ARGUS pair (1, 2). Final multi-agent audit gate before the polish pass is declared complete.
