# Incident Runbooks — HYDRA on IBKR

**Purpose:** Diagnostic + resolution steps for every IBKR-specific failure mode the operator might encounter at 3 AM. Each runbook follows: **Symptom → Triage → Root cause → Resolution → Verification → Post-mortem trigger**.

**Audience:** Operator on duty. Assumes basic familiarity with `gcloud compute ssh` + `systemctl` + reading `journalctl -u hydra`.

**Telegram alert names** are quoted exactly so an operator who sees an alert can grep this file for the matching runbook.

**Branch:** `hydra-ibkr-standalone` (or `main` post-merge). Commands assume `/opt/calypso` on the GCP VM.

**Failure-recovery first principle:** if you're unsure, **stop the bot** (`systemctl stop hydra`) and **investigate without time pressure**. The bot stopped is safer than the bot wrong.

---

## RB-1 — Bot says "authenticated but not connected"

> **DEPLOYED (broker mode).** The IBKR session is owned by `calypso-broker`,
> NOT the `hydra*` units. A session-lost / authenticated-but-not-connected /
> competing-session fault is resolved by `sudo systemctl restart calypso-broker`
> and inspected via `journalctl -u calypso-broker`. In broker mode HYDRA's
> `ensure_connected()` (`shared/broker_client.py`) is only a `GET /health`
> probe — it reports the broker's session health and **cannot reconnect**; the
> real `disconnect()` + `connect()` re-auth loop runs inside `calypso-broker`
> (`services/broker/main.py:_maintain()`, the only process that owns the IBKR
> session). Restarting a `hydra*` unit just re-probes the broker's `/health` and
> will NOT re-establish the session. The legacy direct-`IBClient` steps below
> apply ONLY when `CALYPSO_BROKER_URL` is unset (standalone, non-broker mode).

### Symptom
- Telegram alert: `API_ERROR` — "IBKR session lost mid-day — bot exiting for systemd restart — likely competing session or LST expiry" (this is the alert from Polish Item 1's `ensure_connected()` site)
- OR `journalctl -u hydra` shows: `Auth status: authenticated=true connected=false` repeatedly
- OR `IBClient.ensure_connected()` is returning False and the bot is in an `inactive (auto-restart)` state

### Triage
1. **Confirm the bot's current state:**
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl status hydra --no-pager"
   ```
   Expected: `active (running)` after the auto-restart kicks in. If `failed`, see RB-5.

2. **Read the last 100 lines of journal for the auth flow:**
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hydra -n 100 --no-pager | grep -E 'auth|ensure_connected|IBAuthError|IBConnectionError|stage [123]'"
   ```
   Look for: `stage 1/3` (LST handshake), `stage 2/3` (brokerage session), `stage 3/3` (auth status). Identify which stage failed.

3. **Check for competing sessions:**
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hydra -n 100 --no-pager | grep -i competing"
   ```
   If `competing=true` appears → another client is logged into the IBKR account (TWS, SaxoTraderGO, another `IBClient` process somewhere, the IBKR mobile app). Skip to **Root cause: competing session** below.

### Root cause: brokerage session expired (most common)

IBKR's brokerage session (`/iserver/auth/status` `connected=true`) is shorter-lived than the LST. It can drop without invalidating the LST — so `authenticated=true` but `connected=false`.

### Resolution: brokerage session expired

The bot's `ensure_connected()` SHOULD handle this automatically (calls `disconnect()` + `connect()`). If it's failing repeatedly:

1. Stop the bot to interrupt the restart loop:
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop hydra"
   ```
2. Run the auth flow manually to surface the real error:
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python -c \"
   from shared.ib_client import IBClient, IBConfig
   from shared.ib_oauth import load_credentials
   c = IBClient(IBConfig(credentials=load_credentials(\\\"paper\\\")))
   try: c.connect(); print(\\\"OK\\\", c.account_id)
   finally: c.disconnect()
   \"'"
   ```
3. If `IBAuthError`: credentials issue — see Gate 5 of `LIVE_READINESS_CHECKLIST.md` or rotate per `deploy/IBKR_CREDENTIALS_SETUP.md`.
4. If `IBConnectionError`: IBKR is rejecting the connection (IBKR status page, network); wait + retry. Document the wait in the journal.

### Root cause: competing session

Another client (TWS, IBKR mobile app, SaxoTraderGO from the Saxo era, another orphaned `IBClient` Python process) is logged in.

### Resolution: competing session

1. Identify the other client:
   ```bash
   # Check for orphan Python processes
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="ps aux | grep ibind | grep -v grep"
   # Check IBKR Client Portal web session (operator must check the IBKR account portal in a browser — there's no API)
   ```
2. Sign out the other client. For the IBKR portal: log in, click your name (top right) → Sign Out.
3. Re-start HYDRA:
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start hydra"
   ```
4. Watch the journal for `stage 3/3` showing `competing=false`.

### Verification
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hydra --since '5 minutes ago' --no-pager | grep -E 'connected successfully|stage 3/3'"
# Expected: "IBClient connected successfully — account=DU..."
```

### Post-mortem trigger
If this fires more than once per week: HYDRA's `ensure_connected()` is not detecting + recovering as designed. File as a P0 bug.

---

## RB-2 — All orders timing out

### Symptom
- Telegram alert: `CIRCUIT_BREAKER` — "Cannot place orders — orders breaker tripped — manual intervention required" (Polish Item 1)
- OR every `place_and_wait_for_fill` is returning `status="timed_out"`
- OR journal shows: `CircuitBreaker[ib.orders] CLOSED → OPEN` followed by `Circuit breaker 'ib.orders' is OPEN — refusing call`
- OR positions can't be placed AND can't be cancelled

### Triage
1. **Read the trigger:**
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hydra --since '15 minutes ago' --no-pager | grep -E 'CircuitBreaker.*OPEN|orders breaker'"
   ```
2. **Determine if IBKR is up:**
   - Web: https://www.interactivebrokers.com/en/index.php?f=2225 (System Status)
   - Or run the Step-2 probe (which uses the `market` family, not `orders`):
     ```bash
     gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python scripts/probe_ibkr_market_data.py 2>&1 | tail -20'"
     ```
     If probe returns DATA OK → IBKR is up; the issue is specific to the `orders` namespace. Probably an order-validation rejection cycle (e.g., bad strike, repeated tick-size error).
3. **Check current open positions:**
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python -c \"
   from shared.ib_client import IBClient, IBConfig
   from shared.ib_oauth import load_credentials
   c = IBClient(IBConfig(credentials=load_credentials(\\\"paper\\\")))
   c.connect()
   try: print(c.get_positions())
   finally: c.disconnect()
   \"' 2>&1 | tail -20"
   ```

### Root cause
Possibilities (in descending likelihood):
1. **Validation cycle** — bot is repeatedly placing an order IBKR rejects (e.g., strike not in the chain, contract not qualified). Each rejection is a "retryable" exception per `is_retryable` semantics, so the breaker counts it. 5 consecutive → OPEN.
2. **Real IBKR orders-namespace outage** — rare but possible. Status page confirms.
3. **Spurious breaker trip from a 5xx-misuse pattern** that `is_retryable` should have short-circuited but didn't. File as a bug.

### Resolution

**Wait 30 seconds + the next order attempt** for HALF_OPEN probe:
- `half_open_after_seconds: float = 30.0` is the timer.
- BUT the probe only fires on the next request after the timer elapses. If the bot is in a wait state (no new entries until next slot), no probe fires.
- The bot's next entry attempt (10:45 or 11:15) will trigger the probe.

**If the probe fails too** (breaker re-OPENs):
1. Stop the bot:
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop hydra"
   ```
2. **Verify no working orders** before any force_reset (orders left in the book would be reactivated on bot restart):
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python -c \"
   from shared.ib_client import IBClient, IBConfig
   from shared.ib_oauth import load_credentials
   c = IBClient(IBConfig(credentials=load_credentials(\\\"paper\\\")))
   c.connect()
   try: print(c.get_open_orders())
   finally: c.disconnect()
   \"'"
   ```
3. If there are working orders: cancel them via the IBKR portal manually (the bot's cancel_order would also hit the breaker).
4. Inspect the journal for the validation-cycle pattern (same error N times in a row → known IBKR-rejection issue). File the underlying cause as a bug; don't just reset.
5. Once the root cause is understood, restart:
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start hydra"
   ```
   The new process gets a fresh breaker state (CircuitBreaker is in-process, not persisted).

**Do NOT** `force_reset()` the breaker without understanding the trigger. The breaker exists to prevent retry storms; bypassing it can pile real money onto a real problem.

### Verification
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hydra --since '5 minutes ago' --no-pager | grep -E 'CircuitBreaker.*CLOSED|connected successfully'"
# Expected: probe success OR fresh connect
```

### Post-mortem trigger
ANY orders-breaker trip is a P1. Document in journal + investigate root cause.

---

## RB-3 — SPX/VIX returns None mid-session

### Symptom
- Telegram alert: `DATA_QUALITY` — "First snapshot warmup exhaustion of the day on conid {conid}" (Polish Item 1) OR `DATA_QUALITY` — "25+ snapshot exhaustions today — data flow severely degraded"
- OR `journal | grep` shows: `_read_index_price(SPX) failed` or `get_vix_price returned None` repeatedly
- OR `_check_credit_gate` is skipping every entry with "credit estimation failed" (because MKT-011 can't get quotes)
- OR HYDRA heartbeat shows `SPX: N/A` / `VIX: N/A`

### Triage
1. **Run the Step-2 probe from a separate shell** (does NOT disrupt the bot):
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python scripts/probe_ibkr_market_data.py 2>&1 | tail -40'"
   ```
2. **Read field 6509** in each row (R / D / Z):
   - `'R'` = real-time. Healthy.
   - `'D'` = delayed-only. **Subscription gap** — bot needs real-time for credit gate timing. See "Resolution: delayed-only" below.
   - `'Z'` = frozen. Only expected outside market hours. During regular session = IBKR snapshot service degraded.
3. **Check the warmup-exhaustion counter** (Polish Item 1):
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hydra --since '1 hour ago' --no-pager | grep -c 'snapshot warmup exhausted'"
   ```
   - 0-2: transient illiquid conid, no action
   - 5-20: degraded service, monitor
   - 25+: HIGH alert should have already fired (Polish Item 1); proceed to "Resolution: degraded service"

### Root cause: degraded IBKR snapshot service
IBKR's snapshot endpoint is having issues. SPX/VIX/options all affected.

### Resolution: degraded service
1. Confirm via IBKR status page.
2. If during a regular session: **stop the bot** to prevent placing trades against null data:
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop hydra"
   ```
3. Wait for IBKR to recover (status page green) + Step-2 probe shows `6509='R'` again.
4. Restart:
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start hydra"
   ```
5. Document the outage + duration in the journal.

### Root cause: delayed-only subscription
The IBKR account doesn't have real-time entitlement for the index. SPX/VIX show `6509='D'` (delayed).

### Resolution: delayed-only
1. Verify subscription state in the IBKR account portal: Settings → Account Configuration → Market Data Subscriptions.
2. For SPX index: need "US Securities Snapshot and Futures Value Bundle" or equivalent (includes real-time VIX).
3. Subscribe; propagation can take up to 24h. **Do NOT trade live on delayed data.**

### Root cause: bot ignored `6509='Z'` during off-hours

Not a real problem. The bot suppresses `_read_index_price` failures outside market hours.

### Verification
```bash
# Step-2 probe shows R on all 3 instruments
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python scripts/probe_ibkr_market_data.py 2>&1 | grep \"6509\"'"
```

### Post-mortem trigger
2+ DATA_QUALITY alerts in a single trading week → review subscription stack with IBKR + tighten ARGUS data-flow check.

---

## RB-4 — Session stolen by another client

> **DEPLOYED (broker mode).** The IBKR session is owned by `calypso-broker`, not
> the `hydra*` units. A stolen / competing session is cleared by signing out the
> other client and then `sudo systemctl restart calypso-broker` (inspect via
> `journalctl -u calypso-broker`) — restarting a `hydra*` unit only re-probes the
> broker's `/health` and cannot re-establish the IBKR session, because in broker
> mode HYDRA's `ensure_connected()` is a probe only (the re-auth loop lives in
> `calypso-broker`, the single process that holds the session). The
> stop/start-`hydra` steps below apply ONLY when `CALYPSO_BROKER_URL` is unset
> (standalone, non-broker mode).

### Symptom
- Telegram alert: `API_ERROR` — "IBKR session lost mid-day — likely competing session" (Polish Item 1)
- AND journal shows: `Auth status: ... competing=true`
- HYDRA exits + auto-restarts in a loop, EACH restart hits competing=true.

### Triage
1. **Identify the other client:**
   - IBKR Client Portal web (https://www.interactivebrokers.com/en/home.php → login)
   - TWS or IBKR Mobile app
   - Another `IBClient` process (orphan Python from earlier debugging?):
     ```bash
     gcloud compute ssh calypso-bot --zone=us-east1-b --command="ps aux | grep -E 'ibind|ib_client|IbkrClient' | grep -v grep"
     ```
   - The dashboard (no — dashboard does NOT import `IBClient`, verified)
   - The Step-2 probe IF run while the bot is also running (the probe constructs its own `IBClient` → competes)

### Root cause
Each new IBKR Web API session "wins" — older sessions get evicted (Last-In-Wins). If you ran the probe at 11:00 ET while the bot was running, the probe stole the bot's session for the duration of the probe.

### Resolution
1. **Stop the bot** (interrupt the restart loop):
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop hydra"
   ```
2. **Sign out the other client.** For the IBKR portal: top-right name → Sign Out.
3. **Kill any orphan probe / `IBClient` Python processes:**
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo pkill -f 'probe_ibkr|ibind'"
   ```
   (This is the rare safe exception to the 'no kill' rule — these are not systemd-managed.)
4. **Restart HYDRA:**
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start hydra"
   ```
5. Watch journal for clean auth flow (no `competing=true`).

### Verification
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hydra --since '2 minutes ago' --no-pager | grep -E 'competing|connected successfully'"
# Expected: "competing=false" + "connected successfully"
```

### Post-mortem trigger
If the competing client wasn't run by the operator (you, the operator, ran nothing): security concern — investigate IBKR account access logs.

---

## RB-5 — VM deploy broken — rollback

### Symptom
- `systemctl status hydra` shows `failed` (not `active` or `inactive`)
- OR HYDRA crash-loops past `StartLimitBurst=5` and systemd refuses further restarts
- OR `journalctl -u hydra -n 100` shows a clear Python traceback / ImportError / config-load error after a recent `git pull`

### Triage
1. **Read the failure:**
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hydra -n 100 --no-pager"
   ```
2. **Identify the recent change:**
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && git log -5 --format=\"%h %ai %s\"'"
   ```
3. **Classify the failure:**
   - **ImportError / syntax error** → bad code on the branch tip
   - **OSError / file-not-found on `/etc/calypso/ibkr/*.cred`** → credentials issue, see Gate 5 of `LIVE_READINESS_CHECKLIST.md`
   - **IBAuthError on startup** → IBKR-side or credentials, see RB-1
   - **`load_credentials("paper")` raising RuntimeError on empty $CREDENTIALS_DIRECTORY** → systemd's `LoadCredentialEncrypted=` failed, see `deploy/IBKR_CREDENTIALS_SETUP.md` pre-start verification

### Resolution: code regression → rollback

1. **Identify the last known good commit** — the previous trading day's commit, or per the merge tag:
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && git log --oneline -20'"
   ```
2. **Hard-reset the VM checkout to the last good commit:**
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && git fetch origin && git reset --hard <LAST_GOOD_SHA>'"
   ```
3. **Clear Python bytecode cache** (always after any git change):
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && find bots shared -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; echo Cache cleared'"
   ```
4. **Restart:**
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl reset-failed hydra && sudo systemctl restart hydra"
   ```
   (`reset-failed` clears the `StartLimitBurst` counter so systemd will try again.)
5. **Verify:**
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl status hydra"
   # Expected: active (running)
   ```

### Resolution: credentials regression → rotate

Follow `deploy/IBKR_CREDENTIALS_SETUP.md` to re-encrypt the affected credential. The pre-start verification section will catch typos before `systemctl start` is attempted.

### Resolution: state-file corruption → restore from snapshot

If `journalctl` shows `json.decoder.JSONDecodeError` on `hydra_state.json` load:

> **Per-variant paths.** The commands below use strategy **A**'s paths
> (`data/hydra_state.json`, `data/state_snapshots/`, unit `hydra`). For a
> variant, substitute the variant tree and unit: variant **B** →
> `data/variant_b/hydra_state.json`, `data/variant_b/state_snapshots/`, unit
> `hydra_variant_b`; variant **C** → `data/variant_c/...`, unit
> `hydra_variant_c`. Restoring A's file over a variant (or vice-versa)
> corrupts the wrong bot.

1. **Stop the bot** (it's auto-restarting; interrupt the loop):
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop hydra"
   ```
2. **List available state-file snapshots** (Polish Item 5):
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="ls -lat /opt/calypso/data/state_snapshots/ | head -10"
   ```
3. **Restore the most recent valid one:**
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso cp /opt/calypso/data/state_snapshots/hydra_state.pre_restart_<TIMESTAMP>.json /opt/calypso/data/hydra_state.json"
   ```
4. **Verify it's valid JSON before restart:**
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python -c \"import json; json.load(open(\\\"data/hydra_state.json\\\"))\" && echo OK'"
   ```
5. **Restart:**
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl reset-failed hydra && sudo systemctl restart hydra"
   ```

### Verification
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl status hydra --no-pager"
# AND
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hydra --since '2 minutes ago' --no-pager | tail -20"
```

### Post-mortem trigger
Any rollback is automatic P0 — file the underlying cause, add a regression test before re-attempting the deploy.

---

## RB-6 — Naked short detected, bot still running

### Symptom
- Telegram alert: **CRITICAL** `NAKED_POSITION` — "NAKED SHORT: {leg_name} (position {uic}) - closing immediately"
- AND journal shows: `HANDLING NAKED SHORT: ...` + either `Closed naked short ...` (good — handler succeeded) or `FAILED to close naked short` (bad — needs intervention)

### Triage

**FIRST: Determine if the naked short was actually closed.**

```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hydra --since '15 minutes ago' --no-pager | grep -E 'NAKED|naked_short'"
```

Look for:
- `Closed naked short {uic} via order ...` → handler succeeded, no action needed (alert was informational; the handler did its job)
- `FAILED to close naked short` → handler failed, manual close required NOW
- `Exception closing naked short` → handler crashed, manual close required NOW

### Root cause

A NAKED_POSITION alert means: at some point during entry placement, a SHORT leg filled but the corresponding LONG leg either didn't fill or had its fill detection fail. The bot detected this asymmetry post-fill and attempted to close the short via market order.

The C1 fix made this path correctly use `uic` (conid) instead of `pos_id`. A C1 regression would manifest here.

### Resolution: handler succeeded

No action. The short was closed; the trade is over. Confirm via:

```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python -c \"
from shared.ib_client import IBClient, IBConfig
from shared.ib_oauth import load_credentials
c = IBClient(IBConfig(credentials=load_credentials(\\\"paper\\\")))
c.connect()
try:
    for p in c.get_positions():
        print(p.get(\\\"conid\\\"), p.get(\\\"position\\\"), p.get(\\\"contractDesc\\\"))
finally: c.disconnect()
\"'"
```

Expected: the conid mentioned in the alert is NOT in the position list (position = 0 or absent).

### Resolution: handler failed (URGENT — manual close required)

**Stop the bot to prevent further side effects:**

```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop hydra"
```

**Manually close the naked short via the IBKR portal** (NOT via a script — at this point we don't know what's wrong with the bot's close path):

1. Log into the IBKR Client Portal (web).
2. Account → Positions.
3. Find the position with the matching conid.
4. Close it via "Trade" → "Close Position" → confirm.

**After manual close, investigate the failure:**

```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hydra --since '1 hour ago' --no-pager | grep -A 5 -B 5 'NAKED_SHORT_CLOSE_FAILED'"
```

The handler logs the error reason. Common causes:
- `breaker OPEN` → see RB-2
- `IBAuthError` mid-session → see RB-1
- `connection reset` / `timed out` → likely transient; retry the close after stopping
- Anything else → likely a code regression; file P0 bug; do not restart the bot until root cause is understood.

### Verification

```bash
# Position is gone
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python -c \"
from shared.ib_client import IBClient, IBConfig
from shared.ib_oauth import load_credentials
c = IBClient(IBConfig(credentials=load_credentials(\\\"paper\\\")))
c.connect()
try:
    targets = [p for p in c.get_positions() if int(p.get(\\\"conid\\\", 0)) == <CONID_FROM_ALERT>]
    print(\\\"REMAINING:\\\" , targets if targets else \\\"NONE\\\")
finally: c.disconnect()
\"'"
# Expected: REMAINING: NONE
```

### Post-mortem trigger
ANY naked-short event (handler succeeded OR failed) is P0. File a journal entry with:
- Why did the asymmetry happen (which long leg didn't fill, and why)?
- What did the handler do (the alert log + the close log)?
- Was the bot's recovery correct?
- Should the entry order have prevented this via a different sequence or limit?

This is the C1 family of bugs — exactly the failure mode the P7 audit closed. A naked-short event means we need to re-verify C1 hasn't regressed.

---

## RB-7 — Backup restore procedure (rarely run, must be rehearsed)

Not a failure mode by itself — but referenced by `LIVE_READINESS_CHECKLIST.md` Gate 7 as a test that must be rehearsed in the last 30 days.

### When to run
- State file is corrupted and snapshots from Polish Item 5 don't go back far enough
- `backtesting.db` corrupted and you want yesterday's data back
- VM rebuild

### Steps

1. **List available GCS backups:**
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso gsutil ls gs://calypso-backups/ | tail -10"
   ```
2. **Stop the bot:**
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop hydra"
   ```
3. **Restore the file:**
   ```bash
   # Example for state file
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso gsutil cp gs://calypso-backups/hydra_state_20260520.json /opt/calypso/data/hydra_state.json"
   ```
4. **Validate JSON:**
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python -c \"import json; json.load(open(\\\"data/hydra_state.json\\\"))\" && echo OK'"
   ```
5. **Restart bot:**
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start hydra"
   ```

### Rehearsal cadence
- Initial: once during VM setup
- Repeat: every 30 days (per LIVE_READINESS_CHECKLIST Gate 7)
- After any GCS bucket policy change

---

## Appendix — Alert → Runbook map

Use this table when a Telegram alert arrives and you need the matching runbook.

| Alert text contains | AlertType | Priority | Runbook |
|---|---|---|---|
| "IBKR session lost" | API_ERROR | HIGH | RB-1 |
| "competing session" | API_ERROR | HIGH | RB-4 |
| "Cannot place orders — orders breaker tripped" | CIRCUIT_BREAKER | CRITICAL | RB-2 |
| "Broker degraded — {family} breaker OPEN" | API_ERROR | HIGH | RB-2 |
| "{family} breaker recovered" | CONNECTION_RESTORED | LOW | RB-2 follow-up only |
| "still OPEN after N minutes" | API_ERROR | HIGH | RB-2 (escalate) |
| "First snapshot warmup exhaustion of the day" | DATA_QUALITY | MEDIUM | RB-3 |
| "25+ snapshot exhaustions today" | DATA_QUALITY | HIGH | RB-3 (escalate) |
| "NAKED SHORT" | NAKED_POSITION | CRITICAL | RB-6 |
| "Position Mismatch Detected" | CRITICAL_INTERVENTION | CRITICAL | RB-5 (state-file path) |
| "ARGUS Health Check FAILED" | (ARGUS direct) | (varies) | check `failures` JSON for the specific check, map to RB-1/3/5 |

Generic ops (not in this file but in `IBKR_CREDENTIALS_SETUP.md`):
- Credentials-encrypt failed → `IBKR_CREDENTIALS_SETUP.md` "Pre-start verification" section
- Credential rotation → same file, "Rotation" section
