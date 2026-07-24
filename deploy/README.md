# `deploy/` — systemd units + deployment runbooks for the IBKR-standalone branch

This directory contains the systemd unit files + setup runbooks for HYDRA on Interactive Brokers.

_Last updated: 2026-05-29 (broker-session pivot — `calypso-broker` is now the single IBKR session owner; A/B/C proxy to it over loopback)._

## Active units (install these on the VM)

| File | Purpose | Status |
|---|---|---|
| `calypso-broker.service` | **Session owner** — single shared IBKR brokerage session (ibind OAuth 1.0a); A/B/C reach it over loopback (`http://127.0.0.1:8788`). **Install + enable FIRST; A/B/C depend on it.** See [`docs/migration/BROKER_SESSION_SERVICE_DESIGN.md`](../docs/migration/BROKER_SESSION_SERVICE_DESIGN.md). | ACTIVE |
| `hydra.service` | Main HYDRA bot, Variant A (paper). Talks to `calypso-broker` via `CALYPSO_BROKER_URL`; opens NO IBKR session of its own. | ACTIVE |
| `hydra_variant_b.service` | Variant B (Brandon Trojan Horse, 7-slot grid 09:45-12:45, 7c). Holds the **live paper seat** since the 2026-07-24 B↔C swap (dashboard PRIMARY). Uses the shared broker via `CALYPSO_BROKER_URL`. | ACTIVE (live) |
| `hydra_variant_c.service` | Variant C (Brandon Trojan Horse, dry-run paper, 3-slot grid, 7c). Was the live paper seat / dashboard PRIMARY until the 2026-07-24 swap; now dry-run shadow. Uses the shared broker via `CALYPSO_BROKER_URL`. | ACTIVE (dry-run) |
| `hydra_variant_d.service` | Variant D (Strategy D "DC Time Machine" — multi-day double calendar → risk-free iron condor). **Dry-run-LOCKED** (the class refuses non-dry_run construction; places NO real orders). Uses the shared broker via `CALYPSO_BROKER_URL`. Go-live runbook: [`docs/migration/D_GOLIVE_RUNBOOK.md`](../docs/migration/D_GOLIVE_RUNBOOK.md). | ACTIVE (dry-run-locked) |
| `hydra_variant_e.service` | Variant E (Strategy E "SPY Double Calendar" — multi-day SPY double calendar, managed laddered profit-take + time-exit, no transformer, no hard stop). **Dry-run-LOCKED** (the class refuses non-dry_run construction; places NO real orders). Uses the shared broker via `CALYPSO_BROKER_URL`. | ACTIVE (dry-run-locked) |
| `entry-window-watch.{service,timer}` | Entry-window watchdog — checks A/B/C + broker just after each entry window (10:20 / 10:50 / 11:20 + 11:35 settle, ET weekdays). | ACTIVE |
| `broker-paper-smoke.{service,timer}` | **Go-live tooling (one-shot).** Places a real 1-contract paper round-trip through the broker, writes an ET-dated PASS sentinel, and (via `ExecStartPost=+flip_a_live.sh`) conditionally flips variant **A** to `dry_run:false` on a clean PASS. The committed `.timer` `OnCalendar` is a specific past date (one-shot, `Persistent=false`) — **re-date it before re-running**, or `systemctl start broker-paper-smoke` manually. | One-shot (go-live) |
| `scripts/flip_bc_swap.sh` / `scripts/flip_bc_rollback.sh` (no unit) | **Operator-run** live-seat swap between B and C — the current procedure for moving the live paper seat (used 2026-07-24 to move it C→B). `flip_bc_swap.sh` flips the target variant to `dry_run:false`/alerts on and the outgoing variant to `dry_run:true`/alerts off; `flip_bc_rollback.sh` reverses it. Runbook: [`RUNBOOKS.md` RB-9](../docs/migration/RUNBOOKS.md). | Manual |
| `scripts/flip_ac_live.sh` (no unit) | **Historical** go-live flip of A **and** C to `dry_run:false` (B stays dry-run). Superseded as the live-seat procedure by `flip_bc_swap.sh`/`flip_bc_rollback.sh` since the 2026-07-24 swap; kept for reference and now guards against running while B holds the live seat. Runbook: [`RUNBOOKS.md` RB-8](../docs/migration/RUNBOOKS.md). | Manual (historical) |
| `apollo.{service,timer}` | Pre-market scout agent (8:30 AM ET weekdays) | ACTIVE |
| `hermes.{service,timer}` | Daily execution analyst (7:00 PM ET weekdays) | ACTIVE |
| `homer.{service,timer}` | Trading journal writer (7:30 PM ET weekdays) | ACTIVE |
| `clio.{service,timer}` | Weekly strategy analyst (Sat 9 AM ET) | ACTIVE |
| `argus.{service,timer}` | Health monitor (every 15 min) | ACTIVE |
| `db_backup.{service,timer}` | Daily backups of SQLite + state + metrics to GCS | ACTIVE |
| `polygon.env.example` | Template for the optional Polygon API key (variants B/C) | Template |

### Broker-session topology (read before installing)

`calypso-broker` owns the **one** IBKR brokerage session (OAuth 1.0a has a one-session-per-username limit; three competing logins evict each other in a crash-loop). A/B/C run a `BrokerClient` that proxies over loopback to the broker and open **no** session of their own. This requires, on each `hydra*.service`:

- `Environment="CALYPSO_BROKER_URL=http://127.0.0.1:8788"` — `bots/hydra/main.py:_build_broker()` falls back to a direct, self-owned `IBClient` when this is **unset**, which is exactly the multi-session eviction failure. It MUST be set.
- `After=calypso-broker.service` (+ `Wants=` or `Requires=`) — so the broker is up before A/B/C try to reach it.

These directives are committed in the `hydra*.service` unit files; do not run A/B/C without them. The broker holds the OAuth credentials (`LoadCredentialEncrypted=`); see `calypso-broker.service`.

## Setup + verification

- **One-time setup of IBKR credentials:** [`IBKR_CREDENTIALS_SETUP.md`](IBKR_CREDENTIALS_SETUP.md) — includes the mandatory pre-start 3-check verification before `systemctl enable hydra`.
- **Live-readiness checklist (before any live flip):** [`docs/migration/LIVE_READINESS_CHECKLIST.md`](../docs/migration/LIVE_READINESS_CHECKLIST.md).
- **Incident runbooks:** [`docs/migration/RUNBOOKS.md`](../docs/migration/RUNBOOKS.md).

## Dead-on-this-branch (do NOT install)

| File | Why it's dead |
|---|---|
| `token_keeper.service.disabled-on-this-branch` | Saxo-only — refreshed Saxo OAuth tokens every 20 min. IBKR OAuth 1.0a is unattended (LST + brokerage session rotate cryptographically; the morning re-auth gate handles the 01:00 ET daily reset). HYDRA does not import `token_coordinator.py` on this branch. The file has a `.disabled-on-this-branch` suffix so any future `setup_vm.sh` loop that enumerates `deploy/*.service` won't accidentally install it. The original (Saxo-era) file lives on `main` if you ever need it. |

## Other files

- `dashboard-hydra.json` — Google Cloud Monitoring dashboard config (Saxo-era; kept until dashboard config is re-validated for the IBKR stack)
- `ops-agent-config.yaml` — Google Cloud Ops Agent config for log forwarding
- `setup_vm.sh` — VM bootstrap script (Saxo-era; needs review before next clean VM provision)
- `setup-monitoring-dashboard.sh` — sets up the dashboard JSON in Cloud Monitoring

## How to install on a fresh VM

```bash
# Copy the active units into place. calypso-broker is listed first because it
# owns the single IBKR session that hydra A/B/C depend on (see topology above).
sudo cp /opt/calypso/deploy/calypso-broker.service \
        /opt/calypso/deploy/hydra.service \
        /opt/calypso/deploy/hydra_variant_b.service \
        /opt/calypso/deploy/hydra_variant_c.service \
        /opt/calypso/deploy/entry-window-watch.service /opt/calypso/deploy/entry-window-watch.timer \
        /opt/calypso/deploy/db_backup.service /opt/calypso/deploy/db_backup.timer \
        /opt/calypso/deploy/apollo.service /opt/calypso/deploy/apollo.timer \
        /opt/calypso/deploy/hermes.service /opt/calypso/deploy/hermes.timer \
        /opt/calypso/deploy/homer.service /opt/calypso/deploy/homer.timer \
        /opt/calypso/deploy/clio.service /opt/calypso/deploy/clio.timer \
        /opt/calypso/deploy/argus.service /opt/calypso/deploy/argus.timer \
        /etc/systemd/system/

sudo systemctl daemon-reload

# Verify before enabling (mandatory — see IBKR_CREDENTIALS_SETUP.md):
sudo systemd-analyze verify /etc/systemd/system/calypso-broker.service
sudo systemd-analyze verify /etc/systemd/system/hydra.service
sudo systemd-analyze verify /etc/systemd/system/hydra_variant_b.service
sudo systemd-analyze verify /etc/systemd/system/hydra_variant_c.service
sudo systemd-analyze verify /etc/systemd/system/entry-window-watch.service

# Then per IBKR_CREDENTIALS_SETUP.md: encrypt creds, verify decrypt.

# Confirm the broker URL + ordering are present on EACH strategy unit BEFORE
# enabling — without them, A/B/C each open their own IBKR session and crash-loop
# evicting each other (the one-session-per-username limit). Checked PER UNIT and
# HARD-ABORTS (a concatenated `grep -q` passes if only ONE unit has the var, and
# a bare echo would let the enable below run anyway — both fail-open):
for u in hydra.service hydra_variant_b.service hydra_variant_c.service; do
  systemctl show -p Environment "$u" | grep -q CALYPSO_BROKER_URL \
    || { echo "FATAL: CALYPSO_BROKER_URL missing on $u"; exit 1; }
  systemctl show -p After "$u" | grep -q calypso-broker.service \
    || { echo "FATAL: After=calypso-broker.service missing on $u"; exit 1; }
done

# Enable + start the broker FIRST and confirm it is up, then the strategy units:
sudo systemctl enable --now calypso-broker.service
curl -fsS http://127.0.0.1:8788/health    # must succeed before starting A/B/C
sudo systemctl enable --now hydra.service hydra_variant_b.service hydra_variant_c.service

# Enable the watchdog + scheduled agents/backups (timers, not the .service):
sudo systemctl enable --now entry-window-watch.timer \
    apollo.timer hermes.timer homer.timer clio.timer argus.timer db_backup.timer
```

**Do NOT use `setup_vm.sh` blindly** — it predates the Saxo→IBKR migration and may install dead units (notably the disabled token_keeper). Validate the script against this README before running on a fresh VM.
