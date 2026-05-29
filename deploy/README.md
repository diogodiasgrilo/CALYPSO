# `deploy/` — systemd units + deployment runbooks for the IBKR-standalone branch

This directory contains the systemd unit files + setup runbooks for HYDRA on Interactive Brokers.

## Active units (install these on the VM)

| File | Purpose | Status |
|---|---|---|
| `hydra.service` | Main HYDRA bot (paper, IBKR OAuth 1.0a) | ACTIVE |
| `hydra_variant_b.service` | Variant B (Brandon Trojan Horse, dry-run paper, 4-slot grid) | ACTIVE (dry-run) |
| `hydra_variant_c.service` | Variant C (Brandon Trojan Horse, dry-run paper, 3-slot grid) | ACTIVE (dry-run) |
| `apollo.{service,timer}` | Pre-market scout agent (8:30 AM ET weekdays) | ACTIVE |
| `hermes.{service,timer}` | Daily execution analyst (7:00 PM ET weekdays) | ACTIVE |
| `homer.{service,timer}` | Trading journal writer (7:30 PM ET weekdays) | ACTIVE |
| `clio.{service,timer}` | Weekly strategy analyst (Sat 9 AM ET) | ACTIVE |
| `argus.{service,timer}` | Health monitor (every 15 min) | ACTIVE |
| `db_backup.{service,timer}` | Daily backups of SQLite + state + metrics to GCS | ACTIVE |
| `polygon.env.example` | Template for the optional Polygon API key (variants B/C) | Template |

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
# Copy the active units into place
sudo cp /opt/calypso/deploy/hydra.service \
        /opt/calypso/deploy/hydra_variant_b.service \
        /opt/calypso/deploy/hydra_variant_c.service \
        /opt/calypso/deploy/db_backup.service /opt/calypso/deploy/db_backup.timer \
        /opt/calypso/deploy/apollo.service /opt/calypso/deploy/apollo.timer \
        /opt/calypso/deploy/hermes.service /opt/calypso/deploy/hermes.timer \
        /opt/calypso/deploy/homer.service /opt/calypso/deploy/homer.timer \
        /opt/calypso/deploy/clio.service /opt/calypso/deploy/clio.timer \
        /opt/calypso/deploy/argus.service /opt/calypso/deploy/argus.timer \
        /etc/systemd/system/

sudo systemctl daemon-reload

# Verify before enabling (mandatory — see IBKR_CREDENTIALS_SETUP.md):
sudo systemd-analyze verify /etc/systemd/system/hydra.service
sudo systemd-analyze verify /etc/systemd/system/hydra_variant_b.service
sudo systemd-analyze verify /etc/systemd/system/hydra_variant_c.service

# Then per IBKR_CREDENTIALS_SETUP.md: encrypt creds, verify decrypt, enable.
```

**Do NOT use `setup_vm.sh` blindly** — it predates the Saxo→IBKR migration and may install dead units (notably the disabled token_keeper). Validate the script against this README before running on a fresh VM.
