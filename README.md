# CALYPSO — HYDRA on Interactive Brokers

> **You're on `hydra-ibkr-standalone`.** This branch contains the **Interactive Brokers Web API** rewrite of HYDRA. Account: **paper only** on this branch — there is no live-money path. The pre-migration codebase (Saxo Bank API + 5 bots) lives on [`main`](https://github.com/diogodiasgrilo/CALYPSO/tree/main). Branch history + design docs under [`docs/migration/`](docs/migration/).

**Repository:** https://github.com/diogodiasgrilo/CALYPSO

**Operator reference:** [`CLAUDE.md`](CLAUDE.md) — the authoritative single-file reference for this branch (~940 lines, 24 sections).

**Migration plan + audit:** [`docs/migration/HYDRA_STANDALONE_REWRITE_PLAN.md`](docs/migration/HYDRA_STANDALONE_REWRITE_PLAN.md), [`docs/migration/P7_AUDIT_FINDINGS.md`](docs/migration/P7_AUDIT_FINDINGS.md).

---

## What this is

A single autonomous SPX 0DTE iron-condor trading bot (**HYDRA**) running on a Google Cloud VM, talking to Interactive Brokers via the Web API. Trades the IBKR paper account; the broker stack and credentials infrastructure are wired for a future live cutover but the live path is intentionally not enabled here.

```
HYDRA process (systemd, paper)
   ↓
IBClient (shared/ib_client.py — ibind OAuth 1.0a)
   ↓
IBKR Client Portal Web API
```

Adjacent services (read-only or async):

- **Agent suite** — APOLLO (pre-market scout), HERMES (daily execution analyst), HOMER (trading journal writer), CLIO (weekly strategy analyst), ARGUS (health monitor). All on systemd timers, all use the Claude API + Google Sheets.
- **Dashboard** — FastAPI backend + React 19 frontend, 100% read-only. Live entries, P&L, agent reports, variant comparison, history calendar, analytics.
- **Alerts** — Telegram + Email via Google Cloud Pub/Sub + Cloud Functions.
- **Backups** — Daily SQLite + state-file snapshots to Google Cloud Storage.

## Strategy in one paragraph

HYDRA is Tammy Chambless's MEIC (Multiple Entry Iron Condors) on SPX 0DTE options, hardened with credit validation (MKT-011), VIX-regime-adaptive entry filtering, progressive OTM tightening (MKT-020 calls / MKT-022 puts), and an asymmetric stop formula (`total_credit + buffer` with per-VIX-regime overrides). The current schedule is 2 base entries (10:45 + 11:15 ET) + a conditional E6 at 14:00 (put-only on up-days, call-only on down-days). The EMA 20/40 trend signal is logged but not acted on. Walk-forward backtest Sharpe 3.282; realistic live estimate 2.684. Full spec: [`docs/HYDRA_STRATEGY_SPECIFICATION.md`](docs/HYDRA_STRATEGY_SPECIFICATION.md).

## Project Structure (this branch)

```
calypso/
├── bots/
│   └── hydra/                     # the ONLY bot on this branch
│       ├── main.py                # entry point + monitoring loop
│       ├── strategy.py            # HydraStrategy (IBKR-aware overrides)
│       ├── base_strategy.py       # MEICStrategy base (HYDRA-owned, IBKR-native)
│       ├── brandon/               # Brandon Trojan Horse variants (B/C)
│       └── config/                # config.json + variant configs
│   # On `main` only: iron_fly_0dte/, delta_neutral/,
│   # rolling_put_diagonal/, meic/ (kill-switched). Not on this branch.
├── shared/
│   ├── ib_client.py               # IBClient — OAuth + REST + write path + reconcile
│   ├── ib_oauth.py                # credentials loader (systemd-creds or env vars)
│   ├── ib_retry.py                # RetryPolicy + per-family CircuitBreaker
│   ├── ib_streaming.py            # StreamingManager (lazy, REST-only by default)
│   ├── ib_reconcile.py            # conid→quantity reconciliation primitives
│   ├── logger_service.py          # Google Sheets + trade logging (timeout-protected)
│   ├── alert_service.py           # Telegram/Email via Pub/Sub
│   ├── market_hours.py            # is_market_open / is_early_close_day
│   ├── event_calendar.py          # FOMC + economic calendar
│   └── ...                        # secret_manager, config_loader, technical_indicators
├── services/
│   ├── apollo/                    # 8:30 AM ET — pre-market scout
│   ├── hermes/                    # 7:00 PM ET — daily execution analyst
│   ├── homer/                     # 7:30 PM ET — automatic journal writer
│   ├── clio/                      # Sat 9 AM ET — weekly strategy analyst
│   └── argus/                     # every 15 min — health monitor
├── dashboard/
│   ├── backend/                   # FastAPI (port 8001)
│   ├── frontend/                  # React 19 + TS + Vite
│   └── scriptable/                # iOS Scriptable widget
├── deploy/
│   ├── hydra.service              # main bot (LoadCredentialEncrypted= + sandboxing)
│   ├── hydra_variant_{b,c}.service
│   ├── IBKR_CREDENTIALS_SETUP.md  # one-time setup + pre-start verification
│   ├── apollo/hermes/homer/clio/argus + db_backup .service / .timer
│   └── ...
├── docs/
│   ├── HYDRA_STRATEGY_SPECIFICATION.md
│   ├── HYDRA_TRADING_JOURNAL.md   # updated by HOMER
│   └── migration/                 # F1-F7 + P1-P7 design docs, P7 audit register, runbooks, checklists
├── scripts/                       # ~60 utility + analysis scripts (see scripts/README.md)
└── tests/                         # 900 tests; 885 pass, 15 integration skipped pending paper account
```

## Tech Stack

- **Broker:** Interactive Brokers Client Portal Web API via [`ibind`](https://github.com/Voyz/ibind) 0.1.23 (OAuth 1.0a, no gateway)
- **Runtime:** Python 3.11+ on Debian 12 GCE
- **Credentials:** systemd `LoadCredentialEncrypted=` (host-key bound, tmpfs at runtime, off process env)
- **Cloud:** Google Cloud Platform (GCE VM, Secret Manager, Pub/Sub, Cloud Functions, GCS for backups)
- **Logging:** Google Sheets (per-trade + per-stop rows), local rotating log files
- **Backtesting + analytics:** SQLite (`data/backtesting.db`, schema v8)
- **Alerts:** Telegram Bot API + Gmail SMTP via Cloud Function workers
- **Dashboard:** FastAPI + uvicorn (backend) + React 19 + TypeScript + Vite + Tailwind v4 (frontend)
- **Agents:** Anthropic Claude API for narrative generation; gspread for Sheets reads

## Quick start (operator)

This branch is currently in dry-run paper validation. Production deployment will follow the runbooks in `deploy/IBKR_CREDENTIALS_SETUP.md` and the gates in `docs/migration/LIVE_READINESS_CHECKLIST.md`.

For development on a local machine:

```bash
git clone https://github.com/diogodiasgrilo/CALYPSO.git
cd CALYPSO
git checkout hydra-ibkr-standalone
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# Run the test suite (no IBKR creds needed for unit tests)
python -m pytest tests/ -q --ignore=tests/test_dashboard
# Expected: 885 passed, 15 skipped
```

To run the IBKR data-flow probe against your own paper account (requires the 3 OAuth secrets exported from 1Password to the shell):

```bash
export IBIND_OAUTH1A_CONSUMER_KEY=...
export IBIND_OAUTH1A_ACCESS_TOKEN=...
export IBIND_OAUTH1A_ACCESS_TOKEN_SECRET=...
# PEM files in ~/ibkr-oauth/paper/
python scripts/probe_ibkr_market_data.py
```

## Migration history (one paragraph)

The branch migrates HYDRA from Saxo Bank's OpenAPI to Interactive Brokers' Web API across 7 functional phases (F1–F7: auth, contract qualification, option chain, position reconciliation via conid-quantity model, settlement / FX, order write path with cOID dedup, strategy-layer broker abstraction) and 7 cleanup passes (P1–P7: imports, dead-Saxo purge, method ranges audit, broker-abstraction flattening, streaming subsystem, retry + per-family circuit breakers, go-live with re-auth gate + systemd LoadCredentialEncrypted + multi-agent code audit). The P7 audit ran 3 rounds (Round 1 found 49 issues across 4 Critical / 13 High / 17 Medium / 15 Low — all closed; Round 2 verified no new bugs; Round 3 senior-overseer signed off). Branch is currently in paper-validation pre-merge. See [`docs/migration/HYDRA_STANDALONE_REWRITE_PLAN.md`](docs/migration/HYDRA_STANDALONE_REWRITE_PLAN.md) and [`docs/migration/P7_AUDIT_FINDINGS.md`](docs/migration/P7_AUDIT_FINDINGS.md) for the full record.

## License

Private repository. Not for redistribution.

## Contact

[github.com/diogodiasgrilo](https://github.com/diogodiasgrilo) — issues + PRs via GitHub.
