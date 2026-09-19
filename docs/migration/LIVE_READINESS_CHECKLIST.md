# Live-Readiness Checklist — REAL-MONEY gate (variant `bm`, the Brandon IC that B runs on paper)

**Purpose:** A go/no-go checklist an operator MUST complete before trading **REAL MONEY**. Every item is a hard gate. If any item answers "no" or "unknown," do NOT go live.

> ## 🛑 READ THIS FIRST — real money runs ALONGSIDE paper. Nothing is "flipped".
>
> This file was written for a **cutover**: take a paper variant, point it at live
> credentials, and stop running paper. That is **not** the design any more.
> [`LIVE_MONEY_ARCHITECTURE.md`](LIVE_MONEY_ARCHITECTURE.md) (2026-09-18) settled on
> running real money **beside** paper:
>
> | | paper (unchanged) | live money (new) |
> |---|---|---|
> | broker | `calypso-broker` :8788, `/etc/calypso/ibkr/` | `calypso-broker-live` :8789, `/etc/calypso/ibkr-live/` |
> | strategy | `hydra` + `hydra_variant_{b,c,d,e,f,g}` | `hydra_variant_bm` — same Brandon code B runs |
> | after cutover | **keeps running exactly as now** | 1 contract, week 1 |
>
> **B is not promoted. B stays on paper**, with its record, its Gate-4 streak, its
> dashboard and its alerts intact. Real money is variant **`bm`**, a separate unit
> against a separate broker on a separate IBKR username.
>
> Three instructions below were written under the old model and would cause an
> outage — each is struck and corrected in place: Gate 5's "update
> `calypso-broker.service` to the live paths", Gate 6's "`hydra.service` must be
> `inactive`", and the sign-off block's `systemctl restart calypso-broker  # picks up
> the live creds`. **Pointing the shared paper broker at a live account takes all
> seven paper strategies with it**, and `_assert_account_matches_env` then refuses to
> serve the session at all.
>
> *(Corrected 2026-09-19 during a pre-cutover re-measurement. The gates themselves —
> what must be true before real money moves — are unchanged and still authoritative.)*

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
> - **Gate 3's baseline keeps going stale** — "~1918 passed" became 4063, and the suite is at
>   **4284 passed / 16 skipped** as of 2026-09-19. The rule was always *0 failed at the then-current
>   baseline*, never the literal number; the figure is recorded only so a sudden DROP is visible.
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

- [x] `docs/migration/P7_AUDIT_FINDINGS.md` shows **0 OPEN findings** — ✅ verified 2026-09-18 (0 rows marked OPEN).
  ```bash
  gcloud compute ssh calypso-bot --zone=us-east1-b --command="grep -c '| OPEN |' /opt/calypso/docs/migration/P7_AUDIT_FINDINGS.md"
  # MUST output: 0
  ```
- [x] `docs/migration/DEFERRED_WORK.md` — every open DEF entry has an explicit non-blocking justification
- [x] No `# TODO` / `# FIXME` / `# XXX` markers in `bots/hydra/` or `shared/ib_*.py` — ✅ verified 2026-09-18 (grep count 0).
  ```bash
  gcloud compute ssh calypso-bot --zone=us-east1-b --command="grep -rn '# TODO\|# FIXME\|# XXX' /opt/calypso/bots/hydra/ /opt/calypso/shared/ib_*.py 2>/dev/null | wc -l"
  # MUST output: 0
  ```

## Gate 3 — Test state

- [x] Full test suite passes (**4284 passed / 16 skipped**, 0 failed — measured 2026-09-19; the count grows every week, so the gate is **0 failed at the then-current baseline**, never the literal number)
  ```bash
  # RUN LOCALLY, against the deployed commit — NOT on the VM.
  # pytest / pip-audit / coverage are DELIBERATELY excluded from the production venv
  # (requirements.txt lines 71-73 are commented out; requirements-lock.txt documents the
  # exclude-list rationale) to keep the trading box's dependency + CVE surface small.
  # So: confirm the VM's SHA, check it out locally, and test that.
  gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso git -C /opt/calypso rev-parse HEAD"
  git checkout <that-sha> && .venv/bin/python -m pytest tests/ -q
  ```
- [ ] **Paper order-path smoke** passes against the **paper** account in the last 7 days —
  **use [`scripts/broker_paper_smoke.py`](../../scripts/broker_paper_smoke.py)**, which drives the real
  production path (BrokerClient → broker → IBClient) and causes no session contention.
  ```bash
  # CHECK-ONLY (safe any time; SKIPS with exit 75 when the market is closed)
  gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python scripts/broker_paper_smoke.py'"
  # ARMED — real 1-contract round trip. RTH ONLY. This is the Gate-3 evidence.
  gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start broker-paper-smoke"
  ```
  **Exit codes:** `0` PASS · `75` skipped because the market was closed (NOT a pass — re-run during
  RTH) · anything else is a real failure. A check-only run on a weekend proves the safety gate and
  broker reads only; **Gate-3 evidence requires an RTH run.**

  > 🔴 **Do NOT use `tests/integration/test_ib_paper_smoke.py` for this.** It constructs its own
  > `IBClient` and calls `connect()`; IBKR OAuth 1.0a allows **one brokerage session per username**, so
  > it would **evict `calypso-broker`** and take all seven strategies offline. It is a Phase-A.10
  > artifact (May 2026) predating the broker.
  >
  > **As of 2026-09-12 it physically refuses to run** when anything is holding (or could hold) a
  > session — verified against a stub broker in all four states. Override for a genuine maintenance
  > window only, with `calypso-broker` **stopped**: `ALLOW_SESSION_EVICTION=1`.

- [x] `pip-audit` returns zero **High** or **Critical** CVEs in the IBKR stack — ✅ verified 2026-09-18 ("No known vulnerabilities found").
  ```bash
  # LOCALLY (pip-audit is deliberately not on the VM — see the note above).
  .venv/bin/pip-audit -r requirements.txt
  # And audit what is ACTUALLY INSTALLED on the VM, which the line above cannot see:
  gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso /opt/calypso/.venv/bin/pip freeze" > /tmp/vm_installed.txt
  .venv/bin/pip-audit -r /tmp/vm_installed.txt
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
  # NOTE: data/backtesting.db is variant A's (a dry-run shadow). Query the LIVE SEAT's DB —
  # data/variant_<seat>/backtesting.db, today variant_b — or this gate grades the wrong bot.
  gcloud compute ssh calypso-bot --zone=us-east1-b --command="sqlite3 /opt/calypso/data/variant_b/backtesting.db \"SELECT COUNT(*) FROM market_ticks WHERE timestamp >= date('now', '-7 days') AND timestamp NOT LIKE '%T0[09]:%' AND vix_level IS NULL\""
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
  - Note: live credentials MUST live in their OWN directory, a **SIBLING** of paper's and
    never nested inside it — `/etc/calypso/ibkr-live/*.cred` vs paper's
    `/etc/calypso/ibkr/*.cred`. The reason is blast radius, not tidiness: paper credential
    rotation routinely operates on `/etc/calypso/ibkr/` (see CLAUDE.md “Rotation”), so
    live-money credentials nested under it would share every `rm -rf`, `chmod -R` and
    re-encrypt aimed at paper. **The path is pinned by `deploy/calypso-broker-live.service`
    and `tests/test_live_broker_unit_2026_09_18.py` — the unit reads nowhere else.**
    (Settled 2026-09-19. The unit briefly used the nested `/etc/calypso/ibkr/live/`; it was
    moved out while nothing was installed and no credentials existed, which is the only
    time the move is free — afterwards it costs a re-encrypt of all six and a repeat of the
    three-check verification.)
- [ ] 🔴 ~~`deploy/calypso-broker.service` `LoadCredentialEncrypted=` paths updated to
  `/etc/calypso/ibkr-live/...`~~ **— STRUCK 2026-09-19. DO NOT DO THIS.** `calypso-broker` is the
  **paper** session that A/B/C/D/E/F/G are all trading through right now. Repointing it at live
  credentials moves every one of them onto a real-money account; `_assert_account_matches_env`
  then refuses to serve the session (declared paper, actually live) and the whole fleet stops.
  **Instead:** install the SECOND broker, which carries the live paths already and touches
  nothing existing.
  ```bash
  sudo cp /opt/calypso/deploy/calypso-broker-live.service /etc/systemd/system/
  sudo systemctl daemon-reload
  # calypso-broker.service is NOT edited, NOT restarted, NOT stopped.
  ```
- [ ] The paper broker is byte-identical to before the cutover — prove it, do not assume it.
  ```bash
  gcloud compute ssh calypso-bot --zone=us-east1-b --command="diff /etc/systemd/system/calypso-broker.service /opt/calypso/deploy/calypso-broker.service && grep -c 'ibkr-live' /etc/systemd/system/calypso-broker.service"
  # MUST output: no diff, and 0 occurrences of ibkr-live
  ```
- [ ] The **live** broker resolves the live keypair — and the **paper** broker still resolves paper.
  Ask each one directly rather than reading source; `/health` is the authoritative answer and it
  is what the strategies' account guard reads.
  ```bash
  curl -s http://127.0.0.1:8789/health   # live:  {"environment":"live","account":"<non-D code>",...}
  curl -s http://127.0.0.1:8788/health   # paper: {"environment":"paper","account":"DUR049068",...}
  ```
  A `D`-prefixed account code on :8789 means live credentials were encrypted from the paper
  keypair. `"environment":"paper"` on :8789 means `CALYPSO_IBKR_ENV=live` did not take. Either is
  a STOP.
- [ ] Pre-start verification (per `deploy/IBKR_CREDENTIALS_SETUP.md`, 3 checks) passes against the new `/etc/calypso/ibkr-live/` directory
- [ ] **The paper credentials remain in `/etc/calypso/ibkr/`** for fallback / rollback. Do not delete.

## Gate 6 — VM state

- [ ] 🔴 ~~`hydra.service` is `inactive` at the moment of the live flip~~ **— STRUCK
  2026-09-19. The paper fleet KEEPS RUNNING.** Stopping it was right under the cutover model and
  is wrong under this one: paper B is the control the real-money seat is measured against, and
  it holds the Gate-4 streak. What must be true instead is that **nothing paper-declared has
  moved**:
  ```bash
  gcloud compute ssh calypso-bot --zone=us-east1-b --command="systemctl is-active calypso-broker hydra hydra_variant_b hydra_variant_c hydra_variant_d hydra_variant_e hydra_variant_f hydra_variant_g | tr '\n' ' '"
  # MUST output: active x8 — unchanged by the live cutover
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
  # MUST output: {"status":"ok","clients":N,"state_loaded":true,"state_date":"YYYY-MM-DD"}
  # (This said `"status":"healthy"` until 2026-09-19. The endpoint has never returned that,
  #  so the gate could only ever be failed by an operator reading it literally.)
  ```
- [ ] Disk usage < 70%
- [ ] Memory usage < 80%

## Gate 7 — Backup verified

- [ ] Today's `db_backup.timer` fired successfully
  ```bash
  gcloud compute ssh calypso-bot --zone=us-east1-b --command="systemctl list-timers db_backup.timer --no-pager"
  # CHECK: NEXT shows tonight's run, LAST shows yesterday's success
  ```
- [x] Yesterday's snapshot is visible in GCS — ✅ verified 2026-09-18 (`variant_g_hydra_state_20260917.json` et al in `gs://calypso-backups/`).
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
  # The REAL-MONEY variant's config — config_variant_bm.json. NOT B's: B stays on paper
  # at whatever size it runs, and changing it would corrupt the control this is measured against.
  gcloud compute ssh calypso-bot --zone=us-east1-b --command="grep 'contracts_per_entry' /opt/calypso/bots/hydra/config/config_variant_bm.json"
  # MUST show: "contracts_per_entry": 1
  ```
  > **`bm` already ships `contracts_per_entry: 1`** (and `dry_run: true`), so this gate starts GREEN
  > and the job is to keep it that way rather than to change anything. **B's 7 contracts are no
  > longer a Gate-8 failure** — that reading came from the cutover model, where B itself went live.
  > Still re-verify *after* the final pre-flip deploy: `config_variant_*.json` carries `skip-worktree`
  > on the VM yet is **still overwritten by a `git pull`** that advances the tracked file.
- [ ] `min_buying_power_per_ic` configured for live margin — **and read this before trying to measure it.**
  > Probed 2026-09-19 on paper: a **single-leg** `what_if_order` returns a full, correct block (naked SPX
  > short call, 1c: amount 225 USD, initial.change 109,213), but a **BAG/combo** whatif returns em-dash
  > placeholders with all legs snapshot, and a 2-leg BAG **times out and opens the `ib.orders` breaker**.
  > So the defined-risk IC margin cannot be previewed as a combo here, and summing naked per-leg previews
  > is useless as a gate — $109k per short would refuse every entry. The shape still to test is **the
  > short leg previewed while its protective long is already held**, which is how HYDRA legs in. That
  > needs a position on the books, so it is an RTH measurement.
  >
  > ⚠️ Never put a whatif call in the entry path: it is on the **orders** family, so a margin probe
  > shares a failure budget with real placement.
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
- [~] **Halt criteria** explicitly documented — **DRAFTED 2026-09-12:
  [`LIVE_HALT_CRITERIA.md`](LIVE_HALT_CRITERIA.md)**. The `$X`/`N` placeholders below are now real
  numbers derived from B's measured live-paper loss distribution (25 traded sessions), set at
  ~1.5–2× the worst observed event and stated **per contract** so they scale correctly:
  H1 session loss ≤ −$400 · H2 week-1 cumulative ≤ −$600 · H3 ≥3 stops in a day · H4 ≥4 consecutive
  days with a stop · H5 CRITICAL_INTERVENTION · H6 naked short · H7 `orders` breaker >5min in RTH ·
  H8 broker down >15min in RTH · H9 ARGUS FAIL ×3 · H10 any unreconciled position gap.
  **Operator must adopt them** — the doc is a draft until the approval commit exists.
- [ ] **Halt procedure** rehearsed — operator can `systemctl stop hydra_variant_bm` in < 30 seconds from
  any location. That stops **real money only**; paper keeps running and is meant to.
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

Going live now. NOTHING running is restarted — real money is ADDED beside paper:
  systemctl enable --now calypso-broker-live    # the SECOND broker, :8789, live creds
  curl -s http://127.0.0.1:8789/health          # REQUIRE environment=live, account NOT starting with D
  curl -s http://127.0.0.1:8788/health          # REQUIRE paper still environment=paper, competing=false
  systemctl enable --now hydra_variant_bm       # 1 contract; expect ACCOUNT-ASSERT OK ... 'live_money'
  sudo journalctl -u calypso-broker-live -u hydra_variant_bm -f &   # monitor in another shell

  # calypso-broker and the seven paper units are NOT touched at any point above.

If any halt criterion fires in week 1, execute:
  systemctl stop hydra_variant_bm   # stops REAL-MONEY trading, and nothing else.
  # Paper keeps running throughout — it is the control, not collateral.
  # Stop calypso-broker-live too only to drop the live IBKR session entirely.
  # then: refer to docs/migration/RUNBOOKS.md
"
```

---

## What this checklist is NOT

- It is not a guarantee of profitability — strategy edge is separate.
- It is not a substitute for ongoing monitoring (the agents — APOLLO, HERMES, HOMER, CLIO, ARGUS — keep doing their jobs).
- It is not a one-time-only check — re-run it before any major change (config flip from 1c→2c, broker swap, new strategy variant promoted to live).
