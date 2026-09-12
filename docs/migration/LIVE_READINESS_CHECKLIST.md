# Live-Readiness Checklist — REAL-MONEY gate (0DTE IC group: A/B/C)

**Purpose:** A go/no-go checklist an operator MUST complete before flipping a 0DTE IC variant from live-PAPER to **REAL MONEY**. Every item is a hard gate. If any item answers "no" or "unknown," do NOT go live.

**This file is NOT** the credentials-deploy runbook (that's `deploy/IBKR_CREDENTIALS_SETUP.md`) or the merge plan (`docs/migration/MERGE_PLAN.md`). It's the **final readiness gate** before live-money trading.

**Authority:** Live trading requires explicit written approval (committed to the repo). The operator records the approval as the final item.

> **📍 Scope + currency (refreshed 2026-07-14 — see [GO_LIVE_MASTER.md](../GO_LIVE_MASTER.md)):**
> - This is the **Level II (live-MONEY) gate ONLY.** The **Level I dry-run→live-PAPER flip** is covered by
>   `GO_LIVE_MASTER.md` §3 + `RUNBOOKS.md` RB-8, **not here**. **Start at [`GO_LIVE_MASTER.md`](../GO_LIVE_MASTER.md).**
> - It is written **0DTE-IC-centric (A/B/C)** — apply it per variant (swap the `hydra*` unit +
>   `config_variant_*.json`). The **calendar group (D/E) needs a SEPARATE real-money gate** — the 0DTE gates
>   4/7/8/9 don't transfer (see `D_GOLIVE_SCOPE_AND_AUDIT.md` §5).
> - **Broker mode (deployed topology):** OAuth/credentials now live in **`calypso-broker`**, not the strategy
>   units — read Gates 5 & 6 in that light (this file predates the broker; the live-cred swap happens at
>   `calypso-broker`, and a session fault is fixed by restarting `calypso-broker`, not `hydra`).
> - This file lives at `docs/migration/` (not `docs/`).
>
> **Currency refresh 2026-09-12.** Two things this file used to get wrong:
> - **It was written A-centric.** The live seat is **variant B** (since 2026-07-24, RB-9) — apply every gate
>   to whichever variant `variant_readers.live_seat_id()` reports, and read `hydra.service` /
>   `config.json` below as *"the live variant's unit / config"*. Today that is `hydra_variant_b.service` and
>   `config_variant_b.json`.
> - **Gate 3's "~1918 passed" baseline is long stale** — the suite is at **3608 passed / 16 skipped** as of
>   2026-09-12. The rule was always *0 failed at the current baseline*, never the literal number.
>
> **A measured, gate-by-gate status snapshot lives in [`GO_LIVE_MASTER.md` §2-bis](../GO_LIVE_MASTER.md).**
> Fill this checklist in at cutover; read §2-bis to know what is already red.

---

## Gate 1 — Branch state

> **Scope note (AUD2-M4):** This gate applies to the **live-money cutover** —
> live trading must come from `main`. The earlier paper-validation phase
> (Gate 4's 5 consecutive sessions + chaos test) IS permitted on the
> `hydra-ibkr-standalone` feature branch BEFORE the merge to main.
> Do NOT flip the bot live from a feature branch.

- [ ] Bot deployed from **`main`** branch, not a feature branch
  ```bash
  gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && git rev-parse --abbrev-ref HEAD'"
  # MUST output: main
  ```
- [ ] Last commit on `main` is tagged or referenced in a release note
  ```bash
  gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && git log -1 --format=\"%h %s %d\"'"
  ```
- [ ] No local uncommitted changes on the VM
  ```bash
  gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && git status'"
  # MUST output: nothing to commit, working tree clean
  ```

## Gate 2 — Audit state

- [ ] `docs/migration/P7_AUDIT_FINDINGS.md` shows **0 OPEN findings**
  ```bash
  gcloud compute ssh calypso-bot --zone=us-east1-b --command="grep -c '| OPEN |' /opt/calypso/docs/migration/P7_AUDIT_FINDINGS.md"
  # MUST output: 0
  ```
- [ ] `docs/migration/DEFERRED_WORK.md` — every open DEF entry has an explicit non-blocking justification
- [ ] No `# TODO` / `# FIXME` / `# XXX` markers in `bots/hydra/` or `shared/ib_*.py`
  ```bash
  gcloud compute ssh calypso-bot --zone=us-east1-b --command="grep -rn '# TODO\|# FIXME\|# XXX' /opt/calypso/bots/hydra/ /opt/calypso/shared/ib_*.py 2>/dev/null | wc -l"
  # MUST output: 0
  ```

## Gate 3 — Test state

- [ ] Full test suite passes (**3608 passed / 16 skipped**, 0 failed — baseline as of 2026-09-12; the count grows every week, so the gate is **0 failed at the then-current baseline**, never the literal number)
  ```bash
  gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python -m pytest tests/ -q --ignore=tests/test_dashboard 2>&1 | tail -3'"
  ```
- [ ] **Integration smoke** (`tests/integration/test_ib_paper_smoke.py`, the 15 currently-skipped tests) passes against the **paper** account at least once in the last 7 days
  ```bash
  gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && IBIND_INTEGRATION=paper .venv/bin/python -m pytest tests/integration/ -v'"
  # MUST output: ≥ 15 passed
  ```
- [ ] `pip-audit` returns zero **High** or **Critical** CVEs in the IBKR stack
  ```bash
  gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/pip-audit -r requirements.txt 2>&1 | grep -E \"high|critical\" | wc -l'"
  # MUST output: 0
  ```
  > ✅ **RESOLVED 2026-09-12.** Was RED: `cryptography==48.0.0` carried four advisories — CVE-2026-69248
  > (X.509 name-constraint bypass), CVE-2026-69249 (cert-chain recursion DoS), CVE-2026-69247 (PKCS7
  > decrypt oracle), GHSA-537c-gmf6-5ccf (statically-linked OpenSSL in the wheel). Now pinned at
  > **50.0.0**, which clears all four; `pip-audit -r requirements.txt` reports *No known vulnerabilities*.
  >
  > **Getting there needed `msal` 1.36 → 1.38.** msal 1.36 caps `cryptography<49`, so 48.0.1 was the
  > ceiling until msal moved to `<51`. msal is not a direct dependency — it arrives via
  > `Office365-REST-Python-Client`, which backs the dormant SharePoint/Excel logging path in
  > `shared/logger_service.py`. Do not "clean up" that package without checking this constraint again.
  >
  > **Correcting an earlier claim in this file:** `cryptography` is **NOT** under the IBKR OAuth path.
  > ibind signs OAuth 1.0a with **pycryptodome** (`from Crypto.*`), and `requests` does TLS through
  > `urllib3`/`ssl`. `cryptography` is pulled in by **google-auth / PyJWT / oauthlib[rsa] / msal** — i.e.
  > service-account JWT signing for **Secret Manager, Google Sheets and Pub/Sub**. That is the blast
  > radius to validate after any bump here, not the broker handshake.

## Gate 4 — Paper history

- [ ] **Five consecutive full trading sessions** on paper without manual intervention required
  - "Manual intervention required" = any `systemctl restart` for a code/config reason, any position closed by hand, any orphaned position recovery, any CRITICAL_INTERVENTION alert
  - Source of truth: HYDRA Trading Journal (`docs/HYDRA_TRADING_JOURNAL.md`) and the `intel/argus/incidents/` directory for the 5 sessions
- [ ] **Net P&L of those 5 sessions ≥ 0** (paper performance must at least break even)
- [ ] **No false-positive stops** in those 5 sessions (MKT-046 anti-spike filter caught everything it should have)
- [ ] **No null/None VIX** during regular market hours in those 5 sessions
  ```bash
  gcloud compute ssh calypso-bot --zone=us-east1-b --command="sqlite3 /opt/calypso/data/backtesting.db \"SELECT COUNT(*) FROM market_ticks WHERE timestamp >= date('now', '-7 days') AND timestamp NOT LIKE '%T0[09]:%' AND vix_level IS NULL\""
  # MUST output: 0 (or close to 0 — any non-zero needs investigation)
  ```
- [x] **Chaos test passed**: `kill -9` on the live seat → state file intact JSON, automatic restart, no
  duplicate orders, no untracked positions. **RUN 2026-09-12 — PASS** (log: `RUNBOOKS.md` **RB-10**).
  Restarted in 33s, state JSON valid, snapshot fired, 0 `.tmp` residue, recovery clean, account flat.
  > **RUN IT WITH `scripts/chaos_test.sh`** (added 2026-09-12) — never by hand. It is the project's ONE
  > sanctioned `kill -9`, and it hard-refuses unless: outside RTH (13:30–20:00 UTC), broker `connected:true`,
  > and the account **flat by QUANTITY** (IBKR returns qty-0 rows for expired contracts; `len(positions)` is
  > not flatness). Then it measures restart time, re-validates the state JSON, checks the `ExecStartPre`
  > snapshot fired, looks for atomic-write `.tmp` residue, greps the recovery log, and re-checks for orphans.
  >
  > ⚠️ **Gate 4's original "systemd restart < 30s" is UNMEETABLE BY DESIGN** — the units set `RestartSec=30`,
  > so a correct restart is *≥* 30s. Criterion corrected to "restarts automatically"; don't fail a good run
  > against an impossible number.
  >
  > **Scope honesty:** Gate 4 says "mid-trade-attempt". That needs an in-flight order, which only exists
  > during RTH — and the script refuses to run then. So it covers crash/restart/recovery/reconciliation, NOT
  > a crash between placing a leg and recording it. That case is protected by cOID dedup (`_ensure_coid`,
  > IBKR dedupes server-side), which is unit-tested. A true mid-order test needs a deliberate RTH window with
  > an operator watching — a separate, riskier exercise, and a decision rather than a task.
  >
  > Safe to run because state and metrics are written **atomically** (temp + fsync + `os.replace`), so
  > SIGKILL cannot truncate them — the worst case is losing the most recent write. The test confirms that
  > rather than assuming it.

## Gate 5 — Live credentials

> **Gate 5 used to start too late.** It opened at "request a keypair", but **four things precede that**, each
> with its own IBKR turnaround, and they are **strictly ordered** — none can be parallelised. This is the
> project's actual critical path; the engineering can proceed underneath it.
>
> **Status 2026-09-12: step 1 done, steps 2–5 not started.**

- [x] **5.0.1 — Live account CREATED.** ✅ done (2026-09-12).
- [ ] **5.0.2 — Live account FUNDED.** Nothing downstream is approved or testable until it is.
- [ ] **5.0.3 — Options-trading permissions granted.** SPX defined-risk spreads need spread-level options
  approval and a matching margin type. A separate IBKR review with its own turnaround — **do not assume the
  paper account's permissions carry over; they do not.**
- [ ] **5.0.4 — Live market-data subscriptions active.** A live account starts at **zero** entitlements and
  inherits nothing from paper. Required: CBOE index real-time (SPX **and** VIX) plus the options feed the
  0DTE chain reads. Note the paper account has index real-time but **not** US equity, so "it worked on paper"
  proves nothing here. Verify with `scripts/probe_ibkr_market_data.py` — field `6509` first char must be `R`,
  not `D`/`Z`. Precedent for how long this takes: `e_spy_realtime_entitlement`.
- [ ] **5.0.5 — Then** the keypair below. Activation is a wait (precedent: ~2 weeks for the read-only scanner
  keypair), so request it the moment 5.0.2–5.0.4 allow.

**Code-side status (already DONE — 2026-09-11, `fac138d`):** paper-vs-live is a real switch, not a literal.
`load_credentials(resolve_environment())` at both call sites (`services/broker/main.py`,
`bots/hydra/main.py`); `$CALYPSO_IBKR_ENV` defaults to `paper` and **raises** on an unrecognised value rather
than guessing; `_assert_account_matches_env()` cross-checks the *discovered* account code and **raises** on
declared-paper-but-actually-LIVE (the asymmetry that matters — real money under a simulation assumption).
So Gate 5 is now purely operational.

- [ ] **NEW** live OAuth keypair issued by IBKR (NOT the paper keypair re-purposed)
  - 9-char A-Z `consumer_key`
  - Live access token + access-token-secret
  - Live signature + encryption + dhparam PEM files
- [ ] All 6 live credentials encrypted via `systemd-creds encrypt --name=ibkr_<id>` to `/etc/calypso/ibkr-live/*.cred`
  - Note: directory name MUST differ from paper (`/etc/calypso/ibkr-live/` vs `/etc/calypso/ibkr/`)
- [ ] **Broker mode (deployed):** `deploy/calypso-broker.service` `LoadCredentialEncrypted=` paths updated to
  `/etc/calypso/ibkr-live/...`. In broker mode **`calypso-broker` owns the one IBClient/OAuth session** — the
  live-cred swap happens THERE, not in the `hydra*` strategy units (which proxy data/orders to the broker and
  carry now-unused cred lines). *(Legacy single-bot fallback only, if `CALYPSO_BROKER_URL` is unset:
  `deploy/hydra.service` + `bots/hydra/main.py`'s `load_credentials(...)` call site.)*
- [ ] The broker resolves the **live** keypair — after the swap, the broker's session `is_paper` is **False**.
  ```bash
  # Confirm the broker is loading the LIVE credential set (per deploy/IBKR_CREDENTIALS_SETUP.md pre-start checks)
  gcloud compute ssh calypso-bot --zone=us-east1-b --command="grep -n 'load_credentials' /opt/calypso/services/broker/main.py"
  ```
- [ ] Pre-start verification (per `deploy/IBKR_CREDENTIALS_SETUP.md`, 3 checks) passes against the new `/etc/calypso/ibkr-live/` directory
- [ ] **The paper credentials remain in `/etc/calypso/ibkr/`** for fallback / rollback. Do not delete.

## Gate 6 — VM state

- [ ] `hydra.service` is in `inactive` state at the moment of the live flip (not running on paper credentials)
  ```bash
  gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl is-active hydra"
  # MUST output: inactive
  ```
- [ ] No `failed` in the journal in the last 24h
  ```bash
  gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hydra --since '24 hours ago' | grep -c 'failed'"
  # MUST output: 0
  ```
- [ ] ARGUS shows PASS for the last 4 cycles (1 hour)
  ```bash
  gcloud compute ssh calypso-bot --zone=us-east1-b --command="tail -4 /opt/calypso/intel/argus/health_log.jsonl | grep -c '\"status\":\"PASS\"'"
  # MUST output: 4
  ```
- [ ] Dashboard accessible + WebSocket alive
  ```bash
  gcloud compute ssh calypso-bot --zone=us-east1-b --command="curl -sf http://localhost:8001/api/health"
  # MUST output: {"status":"healthy",...} (HTTP 200)
  ```
- [ ] Disk usage < 70%
- [ ] Memory usage < 80%

## Gate 7 — Backup verified

- [ ] Today's `db_backup.timer` fired successfully
  ```bash
  gcloud compute ssh calypso-bot --zone=us-east1-b --command="systemctl list-timers db_backup.timer --no-pager"
  # CHECK: NEXT shows tonight's run, LAST shows yesterday's success
  ```
- [ ] Yesterday's snapshot is visible in GCS
  ```bash
  gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso gsutil ls gs://calypso-backups/ | tail -3"
  # MUST include yesterday's date in filename
  ```
- [x] Restore procedure tested in the last 30 days (see `RUNBOOKS.md` RB-7)
  > ✅ **RUN 2026-09-12 — PASS.** First rehearsal ever performed; logged in `RUNBOOKS.md` RB-7
  > "Rehearsal log". Restored the live seat's DB + state + metrics from GCS to scratch:
  > `integrity_check ok`, schema v17, 287 trade_entries / 113 trade_stops / 91 daily_summaries,
  > `DataRecorder.ensure_schema()` True, counts matched live exactly, live data untouched.
  > **Re-run by 2026-10-12** — the 30-day clock cannot be satisfied retroactively.
  >
  > ⚠️ **It found a real gap before it could pass.** The live seat had NO database or metrics backup
  > at all — `db_backup.sh` protected variant A (a dry-run shadow) and every variant's *state* file,
  > but no variant's DB. Fixed in `ab63407`; the rehearsal then ran against the first backup the
  > fixed script produced. **RB-7 itself was also wrong** — it documented only the destructive
  > restore and called it the rehearsal; running it as a drill would have overwritten a healthy live
  > state file with a days-old copy. It now separates §A (non-destructive rehearsal) from §B (real
  > restore).
  >
  > For any DB copy, use `shared/db_backup.py:safe_db_backup()` — a `cp`/`shutil.copy2` of a
  > WAL-mode database silently drops committed rows, and a restore is exactly where you'd find out.

## Gate 8 — Position sizing

- [ ] Config `contracts_per_entry` = **1** for week 1 of live trading, regardless of paper sizing
  ```bash
  # NOTE: the LIVE VARIANT's config, not A's. Today that is config_variant_b.json.
  gcloud compute ssh calypso-bot --zone=us-east1-b --command="grep 'contracts_per_entry' /opt/calypso/bots/hydra/config/config_variant_b.json"
  # MUST show: "contracts_per_entry": 1
  ```
  > ⚠️ **Currently 7 on B.** This is a deliberate cutover-time change, not a standing config — and remember
  > `config_variant_*.json` carries `skip-worktree` on the VM yet is **still overwritten by a `git pull`**
  > that advances the tracked file. Re-verify the value *after* the final pre-flip deploy, not before.
- [ ] `min_buying_power_per_ic` configured for live margin (verify with `what_if_order` once before first entry)
- [ ] Daily loss limit / max position count safety bounds tightened for live (recommend: 50% tighter than paper for week 1)

## Gate 9 — Approval + halt criteria

- [ ] **Explicit user approval** in a commit message:
  ```
  git commit --allow-empty -m "approved: HYDRA live trading starting $(date +%Y-%m-%d)
  
  Approver: <name>
  Approver email: <email>
  
  This commit serves as the auditable approval record for the live cutover.
  Live-readiness checklist (docs/migration/LIVE_READINESS_CHECKLIST.md) all
  gates GREEN as of HEAD."
  ```
- [ ] **Halt criteria** explicitly documented (in this same commit message or a linked doc):
  - If realized loss > $X, halt and notify
  - If consecutive stop losses > N, halt
  - If CRITICAL_INTERVENTION alert fires, halt
  - If broker breaker `orders` family OPEN for > 5 minutes, halt
  - If ARGUS shows FAIL for 3 consecutive cycles, halt
- [ ] **Halt procedure** rehearsed (operator can `systemctl stop hydra` in < 30 seconds from any location)
- [ ] **Telegram alerts working** — fire a `BOT_STARTED` test alert and confirm receipt within 1 minute
  ```bash
  gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python -c \"from shared.alert_service import AlertService, AlertType, AlertPriority; AlertService({\\\"alerts\\\": {\\\"enabled\\\": True}}, \\\"TEST\\\").send_alert(alert_type=AlertType.BOT_STARTED, title=\\\"LIVE READINESS TEST\\\", message=\\\"This is a test alert from the live-readiness checklist.\\\", priority=AlertPriority.LOW)\"'"
  ```
- [ ] **At-the-moment-of-flip operator availability** — operator confirms they will be reachable + at a terminal for the first 4 hours of live trading

## Gate 10 — Week 1 monitoring plan

- [ ] **Day 1 (live):** 1 contract per entry. Operator watches every entry, every stop, every settlement. Journal entry mandatory at EOD.
- [ ] **Days 2-3:** 1 contract. Mid-day check-ins (each entry time + settlement).
- [ ] **Days 4-5:** 1 contract. End-of-day check-in only.
- [ ] **Week 1 review (Friday EOD):** if all 5 days closed in profit / break-even AND no CRITICAL_INTERVENTION, consider scaling to 2 contracts week 2. Otherwise: continue at 1c or pause for review.

---

## Sign-off format

The operator records the sign-off in a final commit message after every gate is checked:

```
git commit --allow-empty -m "HYDRA live-readiness checklist: all gates GREEN

Branch: main @ $(git rev-parse HEAD)
Live cutover date: $(date +%Y-%m-%d)
Approver: <name> <email>

Gate 1 (Branch state):     GREEN [n items checked]
Gate 2 (Audit state):      GREEN
Gate 3 (Test state):       GREEN
Gate 4 (Paper history):    GREEN — $(N) consecutive sessions, P&L: $X, no manual intervention
Gate 5 (Live credentials): GREEN — /etc/calypso/ibkr-live/ deployed + verified
Gate 6 (VM state):         GREEN
Gate 7 (Backup):           GREEN — yesterday's snapshot at $(GCS_PATH)
Gate 8 (Position sizing):  GREEN — 1c week 1
Gate 9 (Approval/halt):    GREEN — halt criteria committed at $(SHA)
Gate 10 (Monitoring):      GREEN — operator availability confirmed

Cutting over now via (BROKER MODE — restart the broker FIRST; it owns the live session):
  systemctl restart calypso-broker   # picks up the live creds; wait for /health connected:true
  systemctl restart hydra hydra_variant_b   # (per-variant — substitute the unit(s) actually being
                                             # promoted to real money; hydra_variant_b shown here as B
                                             # holds the live-PAPER seat as of the 2026-07-24 swap, see
                                             # RUNBOOKS.md RB-9 — the strategy units proxy to the broker)
  sudo journalctl -u calypso-broker -u hydra -f &  # monitor in another shell

If any halt criterion fires in week 1, execute:
  systemctl stop hydra hydra_variant_b   # stop trading (broker stays up as a passive session holder) —
                                          # again, substitute the unit(s) actually promoted
  # stop calypso-broker too only to drop the IBKR session entirely
  # then: refer to docs/migration/RUNBOOKS.md
"
```

---

## What this checklist is NOT

- It is not a guarantee of profitability — strategy edge is separate.
- It is not a substitute for ongoing monitoring (the agents — APOLLO, HERMES, HOMER, CLIO, ARGUS — keep doing their jobs).
- It is not a one-time-only check — re-run it before any major change (config flip from 1c→2c, broker swap, new strategy variant promoted to live).
