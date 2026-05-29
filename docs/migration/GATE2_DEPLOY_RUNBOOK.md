# Saxo → IBKR production cutover runbook (`calypso-bot`)

**Status:** ⏳ staged, **HARD-GATED on the green probe.** Do NOT run any destructive step (Phase 2 onward) until `scripts/probe_ibkr_market_data.py` returns **all green during a regular US session** (`6509='R'` on SPX+VIX with bid/ask present). See `PROJECT_STATUS.md` Gate 1.

**Decision (operator, 2026-05-28):** completely replace the live Saxo deployment on `calypso-bot`. Remove all Saxo bots + the Saxo token-keeper entirely (code preserved on the `main` branch in git — not deleted from history). Run the IBKR HYDRA bot — strategies **A** (`hydra.service`), **B** (`hydra_variant_b`), **C** (`hydra_variant_c`) — and keep **dashboard + Telegram + DB + Google Sheets** wired to the new IBKR bot.

**This supersedes** the parallel `_ibkr`-suffixed Phase D/E/F approach in `SAXO_TO_IB_MIGRATION_PLAN.md` §8–10. That design predated the `hydra-ibkr-standalone` branch, which already *is* the Saxo-removed end-state — so we deploy the branch wholesale instead of running both side-by-side.

---

## Target + current state (verified 2026-05-28)

| | Detail |
|---|---|
| VM | `calypso-bot`, zone `us-east1-b`, `e2-small`, **RUNNING**, project `calypso-trading-bot` |
| Repo on VM | `/opt/calypso` — currently on **`main`** (Saxo, HEAD `a77027f`) |
| Service user | `calypso` (uid 999) ✅ · systemd 252 (≥250) ✅ |
| Secrets | **GCP Secret Manager** (`calypso-trading-bot`): `calypso-telegram-credentials`, `calypso-google-sheets-credentials`, `calypso-account-config`, `calypso-alert-config` — all read by the IBKR branch unchanged. IBKR OAuth creds are the ONLY new secret, delivered via systemd `LoadCredentialEncrypted=`. |

**Currently running on the VM (all Saxo, to be replaced/removed):**
- `hydra` + `hydra_variant_b` + `hydra_variant_c` — Saxo-wired (replace with IBKR branch versions)
- `token_keeper` — Saxo OAuth refresher → **remove** (dead on IBKR branch)
- `dashboard` (uvicorn :8001 + nginx :8080) — **keep**, follows new state files automatically
- timers: `apollo`, `argus`, `clio`, `hermes`, `homer` — **keep** (ARGUS was rewritten for IBKR)

**Installed-but-disabled siblings to delete entirely:** `delta_neutral`, `iron_fly_0dte`, `meic`, `rolling_put_diagonal`

> **Risk note — verified low blast radius (2026-05-28).** No real money is at stake on either side:
> - **IBKR side is paper by construction.** `bots/hydra/main.py` hardcodes `load_credentials("paper")` at both runtime call sites (L226, L820); there is NO reachable `load_credentials("live")` — going live needs a deliberate code edit. The paper OAuth keypair (`CALYPSOPP`) maps to the paper account, so every order A/B/C could place hits the paper account regardless of `dry_run`. B/C are additionally `dry_run: true`. Multiple `SAFETY-DRY-01..04` gates guard order placement.
> - **The Saxo bot being stopped is in dry-run.** Despite connecting to Saxo's *live* endpoint (`live.logonvalidation.net`) for market data, strategy A logs `[DRY RUN] HEARTBEAT` and places no real orders; its `Active:N` is a simulated trade in its own state file, not a real Saxo position. Variants B/C are also `dry_run: true`. Stopping it strands no real position **created by the bot**.
> - **Residual (non-money):** the underlying Saxo account is a real live account the dry-run bot simply doesn't trade — if real positions exist there from another source, the cutover doesn't touch them (glance at the Saxo web UI first). And `/iserver/accounts` returned `{}` in the probe — confirm the paper account is selectable for order routing at the Phase 6 connect (fails safe — no order — if not).

---

## Phase 0 — Gate (precondition, blocks everything below)

Run during Friday (or any) regular session and confirm green:
```bash
cd "/opt/calypso" && source .venv/bin/activate    # or on laptop
python scripts/probe_ibkr_market_data.py 2>&1 | tee scripts/probe_mktdata_$(date +%H%M%S).log
```
Proceed only if: `6509='R'` on **both** SPX and VIX (VIX may legitimately show only `mark`/field 7635 — that's expected for a calculated index), and bid/ask present on SPX. Any `D` (delayed) / `Z` (frozen during RTH) / missing data → **STOP**, fix the IBKR market-data subscription first.

---

## Phase 1 — Pre-cutover prep (non-destructive; can run before the gate clears)

**1.1 Push the IBKR branch to origin** (it currently exists ONLY on the laptop — the VM cannot reach it):
```bash
# laptop, in the repo
git push -u origin hydra-ibkr-standalone
```

**1.2 Capture the rollback anchor** (so we can revert cleanly):
```bash
# on VM
git -C /opt/calypso rev-parse HEAD > ~/cutover_rollback_commit.txt   # expect a77027f...
systemctl list-unit-files | grep -iE 'hydra|token|delta|iron|meic|rolling|dashboard|apollo|argus|clio|hermes|homer' > ~/cutover_units_before.txt
```

**1.3 Backup current live Saxo state + DB to GCS** (independent of the routine db_backup):
```bash
DATE=$(date +%Y%m%d_%H%M%S)
gsutil -m cp -r /opt/calypso/data gs://calypso-backups/precutover_${DATE}/ 2>&1 | tail -3
```

**1.4 Confirm IBKR paper PEMs are ready to ship** (on laptop): `~/ibkr-oauth/paper/{private_signature,private_encryption,dhparam}.pem` exist. Trim the 1Password consumer key to exactly `CALYPSOPP` (9 chars, no trailing space — the probe earlier captured a stray space).

---

## Phase 2 — Stop & remove all Saxo services (destructive — after gate green)

```bash
# on VM, as root
# 2.1 Stop the running Saxo services
systemctl stop hydra hydra_variant_b hydra_variant_c token_keeper dashboard

# 2.2 Disable + remove the Saxo-only units entirely (siblings + token_keeper)
for u in token_keeper delta_neutral iron_fly_0dte meic rolling_put_diagonal; do
  systemctl disable "$u" 2>/dev/null
  rm -f /etc/systemd/system/${u}.service /etc/systemd/system/${u}.timer
done
systemctl daemon-reload

# 2.3 Sanity: no Saxo bot processes left
ps -eo pid,cmd | grep -iE 'saxo|token_keeper|delta_neutral|iron_fly|meic|rolling_put' | grep -v grep || echo "clean — no Saxo processes"
```
> The hydra/variant/dashboard units are only *stopped* here; they get overwritten with the IBKR versions in Phase 5 and restarted in Phase 6. The Saxo *code* remains intact on the `main` branch.

---

## Phase 3 — Switch the VM to the IBKR branch + rebuild venv

```bash
# on VM
cd /opt/calypso
sudo -u calypso git fetch origin
sudo -u calypso git checkout hydra-ibkr-standalone
sudo -u calypso git rev-parse --abbrev-ref HEAD          # expect hydra-ibkr-standalone

# rebuild the venv against the branch's pinned deps (drops Saxo libs, adds ibind)
sudo -u calypso /opt/calypso/.venv/bin/pip install -r requirements.txt 2>&1 | tail -5
# optional clean rebuild if deps conflict:
#   sudo -u calypso python3 -m venv --clear /opt/calypso/.venv && ... pip install -r requirements.txt
sudo -u calypso /opt/calypso/.venv/bin/pip-audit -r requirements.txt 2>&1 | tail -1   # expect: no known vulnerabilities
```

---

## Phase 4 — IBKR OAuth credentials (systemd encrypted) + 3-check verify

**4.1 Ship the 3 paper PEMs to the VM:**
```bash
# laptop
gcloud compute scp ~/ibkr-oauth/paper/private_signature.pem \
  ~/ibkr-oauth/paper/private_encryption.pem ~/ibkr-oauth/paper/dhparam.pem \
  calypso-bot:/tmp/ibkr/ --zone=us-east1-b --project=calypso-trading-bot
```

**4.2 Encrypt all 6 into host-bound creds** (names MUST match `deploy/hydra.service`):
```bash
# on VM, as root
install -d -m 0700 /etc/calypso/ibkr
echo -n 'CALYPSOPP'                | systemd-creds encrypt --name=ibkr_consumer_key        - /etc/calypso/ibkr/consumer_key.cred
echo -n 'YOUR_ACCESS_TOKEN'        | systemd-creds encrypt --name=ibkr_access_token        - /etc/calypso/ibkr/access_token.cred
echo -n 'YOUR_ACCESS_TOKEN_SECRET' | systemd-creds encrypt --name=ibkr_access_token_secret - /etc/calypso/ibkr/access_token_secret.cred
systemd-creds encrypt --name=ibkr_signature_pem  /tmp/ibkr/private_signature.pem  /etc/calypso/ibkr/signature.pem.cred
systemd-creds encrypt --name=ibkr_encryption_pem /tmp/ibkr/private_encryption.pem /etc/calypso/ibkr/encryption.pem.cred
systemd-creds encrypt --name=ibkr_dhparam_pem    /tmp/ibkr/dhparam.pem            /etc/calypso/ibkr/dhparam.pem.cred
shred -u /tmp/ibkr/*.pem && rmdir /tmp/ibkr
```

**4.3 Mandatory 3-check (BEFORE enabling):**
```bash
systemd-analyze verify /etc/systemd/system/hydra.service                  # check 1: exit 0
for f in /etc/calypso/ibkr/*.cred; do printf '%-50s %s bytes\n' "$f" "$(systemd-creds decrypt "$f" - | wc -c)"; done
#   check 2 expected: consumer_key=9 (NOT 10), access_token ~20-32, secret ~32+, sig/enc ~1704, dhparam ~428
systemd-creds decrypt /etc/calypso/ibkr/consumer_key.cred -               # check 3: prints CALYPSOPP, no newline
```
**ABORT enabling if any check fails.** (`SAXO_TO_IB_MIGRATION_PLAN.md` and `IBKR_CREDENTIALS_SETUP.md` have the rationale.)

---

## Phase 5 — Install IBKR units + reconcile

```bash
# on VM, as root — install ONLY the active IBKR-era units
cp /opt/calypso/deploy/hydra.service \
   /opt/calypso/deploy/hydra_variant_b.service \
   /opt/calypso/deploy/hydra_variant_c.service \
   /opt/calypso/deploy/db_backup.service /opt/calypso/deploy/db_backup.timer \
   /opt/calypso/deploy/apollo.service /opt/calypso/deploy/apollo.timer \
   /opt/calypso/deploy/hermes.service /opt/calypso/deploy/hermes.timer \
   /opt/calypso/deploy/homer.service  /opt/calypso/deploy/homer.timer \
   /opt/calypso/deploy/clio.service   /opt/calypso/deploy/clio.timer \
   /opt/calypso/deploy/argus.service  /opt/calypso/deploy/argus.timer \
   /etc/systemd/system/
cp /opt/calypso/dashboard/deploy/dashboard.service /etc/systemd/system/
systemctl daemon-reload

# verify the 3 bot units parse (catches LoadCredentialEncrypted typos)
for u in hydra hydra_variant_b hydra_variant_c; do systemd-analyze verify /etc/systemd/system/$u.service; done
```
> Do NOT copy `deploy/calypso.service` (legacy) or `token_keeper.service.disabled-on-this-branch`.

---

## Phase 6 — Start + verify each wired component

```bash
# 6.1 Strategy A first; watch the IBKR connect
systemctl enable --now hydra
journalctl -u hydra -f          # want: Connected to IBKR paper account, LST + Tickler up, strategy init
```
Look for: IBKR (not Saxo) auth, non-null SPX/VIX during RTH, no breaker trips. Then start B + C:
```bash
systemctl enable --now hydra_variant_b hydra_variant_c     # both dry-run
systemctl enable --now dashboard
systemctl enable --now apollo.timer argus.timer clio.timer hermes.timer homer.timer db_backup.timer
```

**6.x — Lock down the dashboard (audit M12 + #6 + #5).** The dashboard exposes full bot state/P&L/config and previously had NO REST auth while GCP firewall rule `allow-dashboard` opens tcp:8080 to `0.0.0.0/0`. Two layers:

```bash
# (1) Network: nginx now listens on 127.0.0.1:8080 (see dashboard/deploy/
#     nginx-dashboard.conf). Reinstall the conf + reload, so 8080 is NOT
#     reachable from the public internet regardless of the firewall rule.
sudo cp /opt/calypso/dashboard/deploy/nginx-dashboard.conf \
        /etc/nginx/sites-available/dashboard && sudo nginx -t && sudo systemctl reload nginx
#     Reach the dashboard via SSH tunnel from your laptop:
#       gcloud compute ssh calypso-bot --zone=us-east1-b \
#         --project=calypso-trading-bot -- -L 8080:localhost:8080
#     then browse http://localhost:8080
#     (Optional belt-and-braces — also drop the public firewall rule:
#       gcloud compute firewall-rules delete allow-dashboard --project=calypso-trading-bot )

# (2) App-layer API key (defense in depth, OFF by default). The key already
#     lives in Secret Manager as calypso-dashboard-api-key, and the frontend
#     fetch-shim (dashboard/frontend/src/apiKey.ts) is wired in. To ARM it:
#       a. rebuild the frontend so dist/ includes the shim:
#            (cd /opt/calypso/dashboard/frontend && npm ci && npm run build)
#       b. deliver the key to the dashboard service as DASHBOARD_API_KEY, e.g.
#            sudo systemctl edit dashboard   # add:
#            # [Service]
#            # Environment=DASHBOARD_API_KEY=<value from the secret>
#          (or read it at start from Secret Manager). Then restart dashboard.
#       c. open the dashboard once as http://localhost:8080/?api_key=<KEY> to
#          persist the key in the browser (matches the WS auth convention).
#     Until armed, the localhost bind in (1) is the active protection.
```

**Per-component verification (the "rewired to IBKR" acceptance checks):**

| Component | Check |
|---|---|
| **Bot A/B/C → IBKR** | `journalctl -u hydra -u hydra_variant_b -u hydra_variant_c --since '-10min' \| grep -iE 'ibkr\|connected\|saxo'` → IBKR present, **zero** `saxo` lines |
| **DB / state** | `ls -la /opt/calypso/data/hydra_state.json /opt/calypso/data/variant_b /opt/calypso/data/variant_c` → fresh mtimes |
| **Dashboard** | `curl -s localhost:8001/api/...` (or load nginx :8080) → shows new IBKR state, not stale Saxo |
| **Telegram** | a startup/breaker alert lands in the chat (alert hooks fire on connect) — `calypso-telegram-credentials` from Secret Manager |
| **Google Sheets** | Homer's next run writes the journal tab (`services/homer`); `calypso-google-sheets-credentials` |
| **DB backup** | `systemctl start db_backup.service && gsutil ls gs://calypso-backups/ \| tail` → new object |
| **Sandbox** | `systemd-analyze security hydra` → ProtectSystem=strict etc. as designed |

---

## Rollback (if any Phase 6 check fails hard)

```bash
# stop the IBKR units
systemctl disable --now hydra hydra_variant_b hydra_variant_c
# return code to Saxo
cd /opt/calypso && sudo -u calypso git checkout main          # a77027f
# reinstall the Saxo units from main + restart token_keeper + hydra + dashboard
sudo -u calypso /opt/calypso/.venv/bin/pip install -r requirements.txt
cp /opt/calypso/deploy/{token_keeper,hydra,...}.service /etc/systemd/system/ && systemctl daemon-reload
systemctl enable --now token_keeper hydra dashboard
# restore pre-cutover data if needed: gsutil -m cp -r gs://calypso-backups/precutover_<DATE>/data/* /opt/calypso/data/
```
State backups: `gs://calypso-backups/precutover_<DATE>/`. Rollback commit: `~/cutover_rollback_commit.txt`.

---

## After a clean cutover

Update `PROJECT_STATUS.md` in the same commit: mark Gate 1 + Gate 2 cleared, note the live deployment is now IBKR paper on `calypso-bot`, and proceed to **Gate 3** (integration tests):
```bash
IBIND_INTEGRATION=paper /opt/calypso/.venv/bin/python -m pytest tests/integration/ -v   # expect >= 15 passed
```
Then **Gate 4** (5-day paper validation) → **Gate 5** (merge to `main`, per `MERGE_PLAN.md`).
