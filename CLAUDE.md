# CALYPSO — HYDRA on Interactive Brokers

> **🧭 New here / Claude session continuing prior work?** Read [`docs/migration/PROJECT_STATUS.md`](docs/migration/PROJECT_STATUS.md) FIRST. It's the single-source-of-truth for *current project state* (what's been done, what's in flight, what's blocked on external gates). THIS file (`CLAUDE.md`) is the *operator reference* (what the bot does, how to deploy, how to troubleshoot). Different purposes — read the status doc first so you know whether to act or wait.

> **Branch state (this branch only — `hydra-ibkr-standalone`).**
> Bot: **HYDRA** v`2.0.0-rc.1` (IBKR-standalone). Broker: **Interactive Brokers Web API** (ibind OAuth 1.0a, no gateway). Account: **paper only** on this branch — there is no live-money path. The legacy Saxo Bank integration plus the 4 sibling bots (Iron Fly, Delta Neutral, Rolling Put Diagonal, MEIC) are **deleted on this branch** (commits P5a + P5b removed `bots/iron_fly_0dte/`, `bots/delta_neutral/`, `bots/rolling_put_diagonal/`, and `bots/meic/` from the tree — `git ls-tree HEAD bots/` shows only `__init__.py` + `hydra/`). Pre-migration code is preserved on `main` where the same 4 bots live as kill-switched (`DISABLED_FOR_SAFETY=True`) modules. Migration history lives in [`docs/migration/`](docs/migration/).

---

## CRITICAL: Bot Control Warning

**NEVER use `kill` or `pkill` to stop bots!** All services have `Restart=always` with `RestartSec=30`. Killing a bot will cause it to auto-restart in 30 seconds. **ALWAYS use `systemctl stop`** to properly stop a bot.

---

## CRITICAL: Paper-Only Branch

This branch trades the IBKR **paper account** only. The systemd unit's `LoadCredentialEncrypted=` directives reference paper credentials. The legacy `--live` CLI flag is a no-op (logs a NOTE on startup). The `IBClient.is_paper` property is `True` for the loaded credentials. Do not flip any of these to live without:

1. Issuing a separate IBKR live-OAuth keypair
2. Re-encrypting credentials with `systemd-creds encrypt` for live
3. Updating the env name in `load_credentials("live")` call sites
4. Approval — this is not a config flip

---

## CRITICAL: Shared Code Change Policy

**Before modifying any code in `shared/`, STOP and consider:**

1. **What does HYDRA actually use?** Check with `grep -rn "function_name" bots/hydra/ shared/`.
2. **Is this change surgical or broad?** Fix the specific bug, don't "improve" surrounding code.
3. **Will it survive the ibind upgrade path?** Anything that depends on internal ibind shapes should have a regression test.

The 4 sibling bots (Iron Fly, Delta Neutral, Rolling Put Diagonal, MEIC) were **deleted on this branch** (P5a + P5b commits). They live on `main` as kill-switched (`DISABLED_FOR_SAFETY=True` + `_check_disabled_kill_switch()` at module top, added in v1.24.0) for back-compat / future restoration. On this branch they cannot be `systemctl start`ed because the unit files and module dirs no longer exist.

---

## Project Overview

CALYPSO on this branch is a single 0DTE SPX iron-condor trading bot (**HYDRA**) running on a Google Cloud VM, talking to IBKR via the Web API. Stack:

- **Broker:** Interactive Brokers Client Portal Web API via [`ibind`](https://github.com/Voyz/ibind) 0.1.23 (OAuth 1.0a, no gateway/container)
- **Credentials:** systemd `LoadCredentialEncrypted=` (TPM- or host-key-bound .cred files in `/etc/calypso/ibkr/`). No `token_keeper`-style refresh service is needed — OAuth 1.0a is unattended; the live session token rotates cryptographically and the morning re-auth gate handles the 01:00 ET daily reset.
- **Google Sheets** for trade logging and post-settlement dashboards
- **Pub/Sub + Cloud Functions** for Telegram/Email alerts (Telegram Bot API + Gmail)
- **Polygon Options Starter** for the GEX-based Brandon variants (B/C)

### Codebase Structure

```
bots/
  __init__.py
  hydra/                      # the ONLY bot on this branch — sibling bot dirs were deleted in P5a/P5b
    main.py                   # entry point, monitoring loop, signal handlers
    strategy.py               # HydraStrategy subclass (IBKR-aware overrides)
    base_strategy.py          # MEICStrategy (HYDRA-owned base, IBKR-native; F1–F7 ports applied)
    brandon/                  # Brandon Trojan Horse variants (B/C only)
    config/                   # config.json + config_variant_{b,c}.json (config.json gitignored)
  # On `main` only: iron_fly_0dte/, delta_neutral/, rolling_put_diagonal/, meic/
  # (kill-switched there). Not on this branch.

shared/
  ib_client.py                # IBClient — OAuth + REST + write path + reconcile (Saxo replacement)
  ib_oauth.py                 # credentials loader; reads $CREDENTIALS_DIRECTORY OR env vars
  ib_retry.py                 # RetryPolicy + per-family CircuitBreaker (oauth/session/portfolio/market/orders)
  ib_streaming.py             # StreamingManager (lazy IbkrWsClient wrapper — REST-only by default)
  ib_reconcile.py             # conid→quantity reconciliation primitives
  logger_service.py           # Google Sheets / trade-logging (timeout-protected)
  config_loader.py            # JSON config + Secret Manager
  market_hours.py             # is_market_open / is_early_close_day / get_us_market_time
  event_calendar.py           # FOMC + economic calendar (single source of truth)
  secret_manager.py           # GCP Secret Manager
  external_price_feed.py      # Yahoo Finance fallback for VIX (still used as last-resort)
  technical_indicators.py     # EMA / ROC / ATR
  alert_service.py            # Telegram/Email via Pub/Sub
  position_registry.py        # vestigial on IBKR (always empty); kept for multi-bot legacy

# Dead-on-this-branch (kept for back-compat with main, never loaded by HYDRA):
  token_coordinator.py        # used to coordinate Saxo OAuth refresh; HYDRA doesn't use Saxo
services/token_keeper/        # Saxo-only service; do NOT start on this branch

services/                     # standalone services
  homer/                      # HYDRA Trading Journal writer (active)
  hermes/                     # daily execution analyst (active)
  apollo/                     # pre-market scout (active)
  clio/                       # weekly strategy analyst (active)
  argus/                      # health monitor (active)
  token_keeper/               # Saxo-only — DEAD on this branch

dashboard/                    # HYDRA dashboard (read-only monitoring, v2.0.0)
  backend/                    # FastAPI + WebSocket (port 8001)
  frontend/                   # React 19 + TypeScript + Vite
  scriptable/                 # iOS Scriptable widget

deploy/
  hydra.service               # main bot (LoadCredentialEncrypted= for 6 IBKR creds, sandboxed)
  hydra_variant_b.service     # parallel dry-run instance (Brandon variant B)
  hydra_variant_c.service     # parallel dry-run instance (Brandon variant C)
  IBKR_CREDENTIALS_SETUP.md   # one-time-setup + pre-start verification runbook
  hermes/apollo/clio/homer/argus .service + .timer  # agent timers
  token_keeper.service.disabled-on-this-branch  # Saxo-only — DEAD on this branch

scripts/
  probe_ibkr_market_data.py   # P7 Step 2 — verify real-time data flow (run after credentials toggle)
  probe_ibkr_chain.py         # option chain + qualify_option_strikes probe
  preview_live_entry.py       # see what HYDRA would do right now
  ...                         # see scripts/README.md
```

---

## VM Details

| | |
|---|---|
| **VM name** | `calypso-bot` |
| **Zone** | `us-east1-b` |
| **Project** | `calypso-trading-bot` |
| **Path** | `/opt/calypso` |
| **User** | `calypso` |

---

## HYDRA Bot Details (v2.0.0-rc.1 — IBKR-standalone)

The strategy itself is unchanged across the Saxo→IBKR migration; only the broker integration was rewritten.

### Schedule

**2 base entries per day** at **10:45 ET** and **11:15 ET** (E#1 at 10:15 dropped at ALL VIX levels). Full iron condors or one-sided via MKT-011 credit gate. VIX entry cutoff disabled (`max_vix_entry=999` — neither Tammy nor Sandvand use VIX cutoffs).

Conditional entries: **E7 disabled.** **E6 (14:00)** fires put-only on up-days (≥ 0.25% above session open — Upday-035) or call-only on down-days (≥ 0.25% below open — Downday-035). Down-day check runs first, then up-day; flat days skip E6.

Walk-forward backtest Sharpe 3.282; realistic live Sharpe estimate 2.684 (ThetaData→Saxo calibration applied).

### Anti-Whipsaw Filter
`whipsaw_range_skip_mult = 1.75` — skips entries when intraday range exceeds 1.75× expected move.

### VIX Regime Adaptive (updated 2026-04-17)

Breakpoints `[18.0, 22.0, 28.0]` define 4 zones. The regime ALWAYS overrides the base `min_viable_credit_per_side` ($2.00) and `min_viable_credit_put_side` ($2.75) — those base values are effectively dead.

| Zone | VIX | Max entries | Entries kept | Min call credit | Min put credit |
|------|------|-------------|--------------|-----------------|----------------|
| 0 | < 18 | 2 (drops E#1) | E#2, E#3 | $1.00 | $1.25 |
| 1 | 18 – 22 | 2 (drops E#1) | E#2, E#3 | $0.50 | $0.75 |
| 2 | 22 – 28 | 2 (drops E#1) | E#2, E#3 | $0.30 | $0.50 |
| 3 | ≥ 28 | 1 (E#3 only) | E#3 | $0.30 | $0.40 |

When the regime applies, `call_credit_floor` / `put_credit_floor` are overwritten to `min_credit − $0.10`. `strategy.py:_apply_vix_regime_overrides()` drops EARLIEST entries when capped (keeps best-performing E#3 at 11:15).

### Strike Selection

- **Wider Starting OTM (MKT-024) — tuned 2026-04-30:** Calls **2.5×**, puts **2.75×** the VIX-adjusted OTM distance, hard-clamped to **180pt**. MKT-020/022 scan inward from there.
- **VIX-Scaled Spread Width (MKT-027):** `round(VIX × 6.0 / 5) × 5`, floor 25pt, cap 110pt.
- **Progressive OTM Tightening (MKT-020 Calls / MKT-022 Puts):** scans from MKT-024 starting distance inward in 5pt steps until credit ≥ active minimum (VIX-regime-dependent) with MKT-029 graduated fallback to `min_credit − $0.10`, or 25pt OTM floor. Each uses batch API (1 chain + 1 batch quote = 2 IBKR calls).
- **Chain Strike Snapping (MKT-045):** After tightening + overlap checks, snaps all 4 strikes to the nearest actual IBKR chain strike (max 25pt tolerance).

### Credit Gate (MKT-011)

Estimates credit from quotes BEFORE placing orders. Thresholds VIX-regime-dependent (table above). MKT-029 fallback steps -$0.05, -$0.10 down to the floor. Decision tree:

- **Call non-viable + put viable + VIX < 15.0** → put-only entry (MKT-032/MKT-039)
- **Call non-viable + put viable + VIX ≥ 15.0** → skip (no put-only in volatile conditions)
- **Put non-viable + call viable** → retry with tighter put strikes (5pt closer, max 2 retries), then call-only entry (MKT-040)
- **Both non-viable** → skip

Per P7-audit H7: the IBKR `_estimate_entry_credit_ib` requires BOTH legs of a side to be quoted in the batch — any None makes that side return 0.0 so MKT-011 stays conservative.

### Stop Formula

Full IC: `total_credit + buffer` (asymmetric — call uses `call_stop_buffer`, put uses `put_stop_buffer`).

**Option B per-VIX-regime overrides (deployed 2026-04-27, see `docs/HYDRA_BUFFER_OPTIMIZATION.md`):**

| Zone | VIX | call_stop_buffer | put_stop_buffer |
|------|------|------------------|-----------------|
| 0 | < 18 | $0.75 (global) | $1.75 (global) |
| 1 | 18 – 22 | $1.50 | $2.50 |
| 2 | 22 – 28 | $1.00 | $1.50 |
| 3 | ≥ 28 | $0.75 (global) | $1.75 (global) |

One-sided stops:
- **Put-only (MKT-039):** `credit + put_stop_buffer`
- **Call-only (MKT-040/MKT-038):** `call_credit + theoretical $2.60 put + call_stop_buffer`

### Stop Anti-Spike Filter (MKT-046)
10-second persistence requirement on breach. Filters momentary bid/ask spikes from MM widening. On first breach, logs full bid/ask detail (`STOP-DETAIL`). If spread recovers within 10s → `MKT-046_FALSE_STOP_AVOIDED`. (MKT-036 75-second confirmation timer is `stop_confirmation_enabled: false`.)

### Buffer Decay (MKT-042)
Starts at `buffer_decay_start_mult` (default 2.50) × normal buffer, linearly decays to 1× over `buffer_decay_hours` (default 4.0h). Wider stops early when premium is rich; normal stops later as theta decays.

### Calm Entry Filter (MKT-043)
Delays entry up to `calm_entry_max_delay_min` (default 5 min) when SPX moved more than `calm_entry_threshold_pts` (default 15.0 pts) in the last `calm_entry_lookback_min` (default 3 min).

### Other knobs
- **Stop Close Mode:** `long_salvage.short_only_stop` (default `false` = close both legs). When `true` → MKT-025 short-only stop + MKT-033 long salvage.
- **FOMC Announcement Skip:** DISABLED. Bot trades normally on FOMC days.
- **FOMC T+1 Blackout:** ENABLED (`fomc_t1_skip_enabled: true`) — skip all entries the day after a FOMC announcement.
- **FOMC T+1 Call-Only (MKT-038):** legacy, DISABLED. Code preserved as fallback when `fomc_t1_skip_enabled` is false.
- **Base-Entry Down-Day Call-Only:** DISABLED (`base_entry_downday_callonly_pct: null`) — negative EV in A/B sweep (Feb–Apr 2026).
- **Early Close (MKT-018):** DISABLED. Hold-to-expiry. Code preserved (set `early_close_enabled: true` to re-enable).
- **VIX-Scaled Entry Time Shifting (MKT-034):** DISABLED.
- **Smart Entry Windows (MKT-031):** DISABLED.
- **Cushion Recovery Exit (MKT-041):** DISABLED (interferes with buffer logic).
- **EMA Trend Signal:** Informational only (logged + stored, does NOT drive entry type).

### State + Recovery
- **State file:** `data/hydra_state.json` (entries, fill prices, stop levels, P&L history, OHLC, FOMC flags).
- **Position Registry:** `data/position_registry.json` (vestigial on IBKR — IBKR has no per-leg position ID; F4 keys reconciliation on `(conid, quantity)` instead).
- **Cumulative metrics:** `data/hydra_metrics.json` (lifetime P&L, win rate, total entries, broker-agnostic — unchanged across Saxo→IBKR cutover).

Mid-day restart recovery is broker-driven: `_recover_positions_from_saxo()` (despite the legacy name) reads state file authoritatively, then reconciles with the broker via `_read_open_positions()`.

### Telegram Commands (16 total)
Background daemon thread polls Telegram `getUpdates` every 5s. Credentials from Secret Manager (`calypso-telegram-credentials`). Responds only to the configured chat_id.

`/status`, `/snapshot`, `/entry N`, `/lastday`, `/week`, `/account`, `/stops`, `/config`, `/set <key> <value>`, `/hermes`, `/apollo`, `/clio`, `/compare`, `/restart`, `/stop`, `/help`.

Long reports (HERMES/APOLLO/CLIO) split at paragraph/line boundaries with `(1/N)` headers instead of truncating at 4096 chars.

---

## IBKR Integration

### Authentication

> **In broker mode (the DEPLOYED topology), this handshake runs ONCE, inside `calypso-broker`** — the A/B/C strategy processes do NOT perform it; they proxy data calls to the broker via `BrokerClient`. See the [calypso-broker section](#calypso-broker-shared-session-service). The description below is the handshake `calypso-broker` runs (and the legacy single-bot path runs when `CALYPSO_BROKER_URL` is unset).

OAuth 1.0a via `ibind`. `IBClient.connect()` is a 3-stage handshake:

1. **LST handshake** — `IbkrClient(use_oauth=True, oauth_config=...)` triggers the live-session-token exchange. 401 / "invalid consumer" → `IBAuthError`; other errors → `IBConnectionError`.
2. **Brokerage session init** — `init_brokerage_session=True` triggers `ssodh/init` on `IbkrClient` construction.
3. **Auth status check** — `/iserver/auth/status`. If `competing=true` post-init, sleep 5s and retry once (the ssodh handoff race). On `authenticated && connected && !competing` → success; else `IBAuthError`.

After connect, `account_id` is pinned via `_discover_account_id()` (calls `portfolio_accounts` — different namespace from `/iserver/accounts`).

### The morning re-auth gate

> **Broker mode (the DEPLOYED topology — A/B/C):** the session lifecycle described below lives in **`calypso-broker`**, NOT in the strategy process. See the [calypso-broker section](#calypso-broker-shared-session-service). When `CALYPSO_BROKER_URL` is set, `main.py:run_bot()` calls `BrokerClient.ensure_connected()`, which is **only an HTTP `GET /health` probe** (`shared/broker_client.py`): it returns `bool(connected)`, performs **no** `disconnect()`/`connect()`, and **cannot re-establish** anything. The real daily + 15-min re-auth loop runs inside `calypso-broker` (`services/broker/main.py:_maintain()`, every `CALYPSO_BROKER_SESSION_CHECK_S`, default 900s). **A session fault is fixed by restarting `calypso-broker`, never by restarting a `hydra*` unit** — a strategy restart cannot repair a session owned by a separate process. The `IBClient` description below applies ONLY to the legacy single-bot fallback (`CALYPSO_BROKER_URL` unset).

In the **legacy direct-IBClient path** (`CALYPSO_BROKER_URL` unset — single-bot only), `IBClient.ensure_connected()` (called at the start of every monitoring iteration in `main.py:run_bot()`) is the once-per-trading-day stale-session check:

- Healthy session → no-op (no round-trip to IBKR — checks `_connected` then auth/status, no reconnect).
- `authenticated=false` OR `connected=false` OR `competing=true` → `disconnect()` + `connect()` fresh.
- Auth-status read exception → triggers full reconnect.
- `_connected=False` → skips status read and reconnects directly.

**Intraday re-check (P7-audit H6):** Every 15 minutes (`INTRADAY_SESSION_CHECK_INTERVAL_S = 15 * 60`), the main loop calls `ensure_connected()` again. In the legacy direct-IBClient path that re-runs the reconnect logic above; **in broker mode it is the same `GET /health` probe** (recovery still happens inside `calypso-broker`, not here). Failure → `break` so systemd restarts the strategy cleanly with `Restart=always` + `RestartSec=30` — note that in broker mode this restart only re-probes the broker's health, it does NOT re-auth the broker's session.

### The /iserver/accounts preflight (P7-audit C2)

IBKR requires `receive_brokerage_accounts()` (= `GET /iserver/accounts`) BEFORE the snapshot endpoint and `/iserver/account/trades` will return real data. Without it, snapshots silently return metadata-only rows (`{conid, conidEx, _updated}` with no price fields) forever.

`portfolio_accounts()` (which `connect()` calls for account-id discovery) hits `/portfolio/accounts` — a DIFFERENT endpoint namespace — and does NOT satisfy this.

`IBClient._ensure_iserver_primed()` is idempotent (sets `self._iserver_primed=True` after first call; reset on `disconnect()`). Called from:
- `_snapshot_with_preflight` — every quote / option-chain fetch
- `what_if_order` — margin/cost pre-check (P7-audit M11)

### Snapshot warmup (P7-audit H10)

After the preflight, `live_marketdata_snapshot` for a fresh conid often returns only metadata for the first ~1-2 polls even in active hours. `_snapshot_with_preflight` warmup-polls up to **12 × 0.5s = 6s** waiting for a populated row. Logs distinct WARNING for metadata-only vs empty-data exhaustion (P7-audit M17) so operators can tell "no real-time entitlement" from "preflight bug" from "snapshot service outage".

### Retry + per-family circuit breakers

`shared/ib_retry.py` (Phase A.8): every IBClient API call routes through `_ib_call(family, fn, ...)` which applies retry + a family-specific `CircuitBreaker`.

| Family | Endpoints |
|---|---|
| `oauth` | token refresh / LST handshake (rare; **lifecycle NOT wrapped** — connect/disconnect manage their own retries) |
| `session` | `auth/status`, `tickle`, `receive_brokerage_accounts` |
| `portfolio` | `accounts`, `summary`, `positions`, `ledger`, FX |
| `market` | snapshot, option chain, contract search, greeks, history |
| `orders` | place, cancel, modify, status, live_orders, whatif |

**RetryPolicy:** 6 attempts (initial + 5 retries), 1s base, 30s cap, ±50% jitter. Retries: HTTP 429/5xx + transient network errors. **Does NOT retry**: IBKR's misuse-of-503 patterns (`is not found`, `already filled`, `already cancel`, `order is filled or canceled`) — those are 4xx-semantically-permanent.

**CircuitBreaker:** Opens after 5 consecutive retryable failures OR ≥50% failure rate over 20-request / 60-second window. 30s half-open probe. Non-retryable exceptions (auth, validation, bad request) propagate immediately and do NOT record a breaker failure — the breaker is for "broker degraded", not "caller did something wrong" (P7-audit H12 pins this).

### Order placement (`place_and_wait_for_fill`)

The building-block primitive for entry placement, stop-loss escalation, and salvage. Sequence:

1. `place_order(coid=...)` — `_ensure_coid` guarantees every order carries a `client_order_id` for server-side dedup (retry-safe; same cOID across all attempts of the same call per P7-audit H12).
2. Check `place_resp.get("order_status")` (P7-audit C3 — the place-response field is `order_status`, **NOT** `status`; the latter is the live-order field). Terminal status → instant fill / cancel / reject.
3. On `order_status == "filled"` → fetch `get_order_status(order_id)` to get the authoritative `filledQuantity` / `avgPrice` (P7-audit C4 — the place-response doesn't carry fill detail; reporting `filled_quantity=0` would cause double-positions on retry).
4. Otherwise: poll `get_order_status` every `poll_interval_s` (default 0.5s) until terminal status or `timeout_seconds` (default 30s).
5. On `CircuitBreakerOpen` during polling (P7-audit M12) → surface as `status="timed_out"` (the existing "caller cancels + escalates" path).

### Conid-quantity reconciliation (F4)

IBKR has **no per-leg position ID** — every IBClient method that previously took a `position_id` now keys on `(conid, quantity)`. The Position Registry is vestigial (always empty on IBKR) but kept for back-compat. Every action path (`_execute_stop_loss`, `_unwind_partial_entry`, `_handle_naked_short`, settlement reconciliation, MKT-033 long salvage) gates on `*_uic` (= conid stored under the legacy field name), NOT `*_position_id`. P7-audit C1, H1, H2, H9, M7, M8 closed every remaining `pos_id`-gated action path.

`base_strategy._position_is_settled(pid)` treats `None`, empty, AND `DRY_*` synthetic IDs as settled so dry-run paths book expired credits correctly.

### Fill prices

Three authoritative sources (in priority order):
1. **`/iserver/account/orders/{order_id}`** → `avgPrice` / `filledQuantity` (post-fill, before purge)
2. **`/portfolio/accounts/{accountId}/positions`** → `avg_cost` (live position, normalized by `_normalize_position_dict`)
3. **`/iserver/account/trades`** → most-recent execution at conid + side (closed-position lookup; replaces Saxo's `closedpositions`)

`_get_close_fill_price` returns `None` on `FilledPrice == 0` so `_deferred_stop_fill_lookup` (background thread) can re-check after IBKR's sync delay, then apply a P&L correction to `total_realized_pnl` before settlement.

### What's NOT used
- **WebSocket streaming** — `StreamingManager` exists but is OFF by default. HYDRA is REST-only on this branch; quotes are snapshot-driven via the warmup-polled `_snapshot_with_preflight`.
- **`shared/saxo_client.py`** — deleted in P5c. Any import would fail at module load.
- **`shared/token_coordinator.py`** — present (kept for `main`-branch back-compat) but never imported by HYDRA. The Saxo `token_keeper.service` is dead on this branch; do not start it.
- **`broker` abstraction layer (`shared/broker/`)** — deleted in P5c. HYDRA's strategy reads `self.broker` directly; that object is a `BrokerClient` in the deployed (broker-mode) topology, or a direct `IBClient` in the legacy single-bot fallback (see the [calypso-broker section](#calypso-broker-shared-session-service)).

---

## calypso-broker (shared-session service)

> **This is the DEPLOYED topology for A/B/C** (cut over 2026-05-29). Read this before troubleshooting any session/auth problem — the [morning re-auth gate](#the-morning-re-auth-gate) and [Authentication](#authentication) sections above describe the *legacy single-bot* path, which is the fallback, not the live config.

### Why it exists

IBKR OAuth 1.0a allows only **one brokerage session per username**. Three concurrent strategy processes (A/B/C) each opening their own session set `compete:true` and evict each other in a crash-loop. `calypso-broker` solves this by owning the **ONE** IBKR session for all strategies. Design + rollback: [`docs/migration/BROKER_SESSION_SERVICE_DESIGN.md`](docs/migration/BROKER_SESSION_SERVICE_DESIGN.md); constraint research: [`docs/migration/IBKR_MULTI_SESSION.md`](docs/migration/IBKR_MULTI_SESSION.md).

### Topology

```
hydra.service (A) ┐
hydra_variant_b   ├─ BrokerClient ──HTTP/loopback──► calypso-broker ──OAuth──► IBKR
hydra_variant_c   ┘  (CALYPSO_BROKER_URL=          (owns the 1 IBClient:
                      http://127.0.0.1:8788)         LST + ssodh/init + Tickler
                                                     + daily/15-min re-auth loop
                                                     + 6 OAuth creds + breakers)
```

- **Session owner:** `calypso-broker` (`services/broker/main.py` + `deploy/calypso-broker.service`). It holds the only `IBClient`, runs `connect()` (LST handshake + `ssodh/init` + Tickler), and runs the re-auth loop in `_maintain()` every `CALYPSO_BROKER_SESSION_CHECK_S` (default 900s = 15 min). `Restart=always` keeps it up; the `hydra*` units depend on it softly (`Wants=`).
- **Strategies (A/B/C):** `main.py:_build_broker()` returns a `BrokerClient` when `CALYPSO_BROKER_URL` is set (per-host `broker.conf` systemd drop-in, not in the committed `.service` files), else a direct `IBClient` (legacy single-bot). `BrokerClient` (`shared/broker_client.py`) proxies the 16 allowlisted data methods over loopback `/rpc` and returns the IDENTICAL shapes `IBClient` would — no strategy code changes. The strategies open **NO** IBKR session of their own (~0.9 combined req/s, well under the ~10/s ceiling).
- **OAuth credentials live in `calypso-broker` only.** The `hydra*` units still carry now-unused `LoadCredentialEncrypted=` lines (harmless; flagged for cleanup in PROJECT_STATUS). Only the broker needs the 6 creds.
- **Circuit breakers + breaker/warmup alerting** run inside the broker process (it owns the `IBClient`). `BrokerClient.circuit_breakers` is an empty dict so the strategy-side alert poll is a harmless no-op (no duplicate alerts).

### Session lifecycle in broker mode

- `BrokerClient.connect()` → `GET /health`; raises `BrokerError` (strategy fails to start) if the broker is not holding a session. It does NOT open a session.
- `BrokerClient.ensure_connected()` → `GET /health`, returns `bool(connected)`, **never raises, never reconnects**. The strategy's session gate treats `False` as a transient broker/data outage (skip the tick / `break` for a systemd restart).
- **Session recovery happens ONLY in `calypso-broker`** (`_maintain()` re-auth loop + `Restart=always`). Restarting a `hydra*` unit cannot repair the session.

### Operator rule of thumb

| Symptom | Act on |
|---|---|
| Session stale / auth failed / `compete:true` (broker mode) | `systemctl restart calypso-broker` — NOT the hydra units |
| A single strategy misbehaving (entry logic, config) | restart that `hydra*` unit |
| Emergency stop of trading | stop the `hydra*` units (the broker is a passive session holder; stop it too only if you intend to drop the IBKR session entirely — see Emergency stop below) |

RUNBOOKS RB-1 / RB-4 still describe the legacy `ensure_connected()` self-reconnect and tell you to restart `hydra` for a session fault; **in broker mode those steps target the wrong unit** — resolve session faults at `calypso-broker`.

---

## Credentials (systemd LoadCredentialEncrypted=)

Six credentials, all per-environment (paper here):

| # | Name | Form | systemd-creds name |
|---|---|---|---|
| 1 | Consumer key | 9-char A–Z string | `ibkr_consumer_key` |
| 2 | Access token | string | `ibkr_access_token` |
| 3 | Access-token-secret | string | `ibkr_access_token_secret` |
| 4 | Signature key | PEM file | `ibkr_signature_pem` |
| 5 | Encryption key | PEM file | `ibkr_encryption_pem` |
| 6 | Diffie-Hellman params | PEM file | `ibkr_dhparam_pem` |

**Full one-time setup runbook:** [`deploy/IBKR_CREDENTIALS_SETUP.md`](deploy/IBKR_CREDENTIALS_SETUP.md) — includes mandatory pre-start verification (3 checks: `systemd-analyze verify`, per-file `systemd-creds decrypt | wc -c` against expected byte ranges, spot-check decrypt of consumer key). Do **not** `systemctl enable hydra` until all 3 pass.

**Loading at runtime:** systemd sets `$CREDENTIALS_DIRECTORY=/run/credentials/hydra.service` (private tmpfs) BEFORE the sandboxing directives take effect, then drops the bot into the sandbox. `shared/ib_oauth.load_credentials("paper")` reads from there. `ProtectSystem=strict` does NOT block credential reading — the bot reads from the tmpfs, never from `/etc/calypso/ibkr/`.

**Dev path:** When `$CREDENTIALS_DIRECTORY` is unset, `load_credentials` falls back to env vars (`IBIND_OAUTH1A_CONSUMER_KEY` / `IBIND_OAUTH1A_ACCESS_TOKEN` / `IBIND_OAUTH1A_ACCESS_TOKEN_SECRET`) plus PEM files in `$CALYPSO_IBKR_KEYS_DIR/{env}/` (default `~/ibkr-oauth/{env}/`). An empty/whitespace-only `$CREDENTIALS_DIRECTORY` raises `RuntimeError` (P7-audit M3) — that means systemd's credential load failed and the service should not silently fall through to dev creds.

**Rotation:** Re-encrypt the changed credential, `sudo systemctl restart hydra`. The .cred files are host-key-bound — they don't port to another VM, re-encrypt on each host.

---

## Variant Comparison (Dry-Run Head-to-Head)

A and B/C are 3 parallel HYDRA processes running concurrently. Each variant has its own systemd unit, isolated `data/variant_<id>/*` paths via the `HYDRA_VARIANT_ID` env var, and `alerts.enabled=false` + `google_sheets.enabled=false` so non-A variants don't pollute the canonical record.

**Current scheme (v1.28.x, IBKR-standalone branch):**

| Variant | Service | Strategy | Schedule | Contracts | Widths |
|---|---|---|---|---|---|
| A | `hydra.service` | HYDRA baseline (MKT-027 dynamic) | 10:45 / 11:15 (+ E6 14:00 conditional) | 1c | 75pt MKT-027 dynamic |
| B | `hydra_variant_b.service` | `BrandonHydraStrategy` (Trojan Horse stack LIVE) | 09:45 / 10:45 / 11:15 / 11:45 (+ E6) | 10c | 5pt below VIX 22, 10pt above (narrow) |
| C | `hydra_variant_c.service` | `BrandonHydraStrategy` (Brandon-faithful baseline) | 10:15 / 10:45 / 11:15 (+ E6) | 10c | Same narrow widths as B |

**Brandon Trojan Horse features (LIVE on B/C):** take-profit at 80% credit captured, GEX-aware strike adjuster (skips sides inside accel zones, shifts toward decel walls), GEX breach exit (closes IC after sustained 90s breach of decel wall), defensive overlay (debit spread / butterfly hedge), delta-target strike selection anchored to 8δ from the live Polygon chain (replaces HYDRA's OTM-multiplier).

Only HYDRA's credit+buffer stop runs in `hydra_stop_shadow` on B/C — parallel for head-to-head journal comparison, never acts on B/C.

**Dashboard:** `/comparison` page (gated by `DASHBOARD_COMPARISON_MODE_ENABLED=true` on the dashboard service) auto-discovers running variants via `/api/variants/health`. End-of-day `VARIANT_COMPARISON_DAILY` Telegram alert from variant A + on-demand `/compare` command.

**Adding a new variant:** (1) add 5 `variant_<id>_*` fields to `dashboard/backend/config.py`; (2) append id to `_VARIANT_IDS` in `dashboard/backend/routers/variants.py`; (3) create `deploy/hydra_variant_<id>.service`; (4) create `bots/hydra/config/config_variant_<id>.json` on the VM. See `docs/HYDRA_VARIANT_TESTING_PLAN.md`.

**API pacing:** Each variant's config can set `strategy.api_pacing_multiplier` (default 1.0 = A; B=1.5, C=2.0 recommended) to scale monitoring + heartbeat intervals — keeps combined IBKR request rate under the rate-limit ceiling. Vigilant-mode stop checks are NOT scaled (safety-critical).

**Polygon:** Variants B/C require `POLYGON_API_KEY` (Options Starter tier — `EnvironmentFile=-/etc/calypso/polygon.env` per `deploy/polygon.env.example`). If absent, GEX features silently disable; TP and narrow widths continue.

---

## Alert System (Telegram/Email)

```
HYDRA → AlertService → Pub/Sub (~50ms) → Cloud Function → Telegram/Gmail → User
```

Alerts are sent AFTER actions complete with actual results. The bot publishes to Pub/Sub non-blocking and continues. All timestamps in US Eastern Time (ET) — exchange timezone, DST handled.

| Priority | Delivery | Examples |
|----------|----------|----------|
| CRITICAL | Telegram + Email | Circuit breaker, emergency exit, naked position, intervention required |
| HIGH | Telegram + Email | Stop loss, max loss, position mismatch |
| MEDIUM | Telegram + Email | Position opened/closed, profit target, settlement complete |
| LOW | Telegram (only) | Bot started/stopped, daily summary, entry skipped, vigilant exit |

AlertService auto-prefixes `[Nc]` on the title when `contracts > 1` (v1.24.0). 14 HYDRA call sites pass `contracts=entry.contracts` so the prefix appears on multi-contract events.

**Telegram credentials** in Secret Manager (`calypso-telegram-credentials`), not config files. **Email** address optionally in Secret Manager (`calypso-alert-config`).

```bash
# Test alerts in dry-run
export ALERT_DRY_RUN=true
python -c "from shared.alert_service import AlertService; AlertService({'alerts': {'enabled': True}}, 'TEST').circuit_breaker('Test', 3)"

# Cloud Function logs
gcloud functions logs read process-trading-alert --region=us-east1 --project=calypso-trading-bot --limit=50

# Dead-letter queue (failed alerts)
gcloud pubsub subscriptions pull calypso-alerts-dlq-sub --project=calypso-trading-bot --limit=10 --auto-ack
```

Full deployment guide: [`docs/ALERTING_SETUP.md`](docs/ALERTING_SETUP.md).

---

## Agent Suite (5 Agents)

5 autonomous agents on systemd timers. All use `shared/claude_client.py` for Claude API + `shared/sheets_reader.py` for Google Sheets.

| Agent | Service | Schedule | Purpose |
|-------|---------|----------|---------|
| APOLLO | `apollo.service` | 8:30 AM ET weekdays | Pre-market scout (overnight news, VIX, expected move) |
| HERMES | `hermes.service` | 7:00 PM ET weekdays | Daily execution quality analyst |
| HOMER | `homer.service` | 7:30 PM ET weekdays | Automatic HYDRA Trading Journal updates |
| CLIO | `clio.service` | Sat 9:00 AM ET | Weekly strategy analyst |
| ARGUS | `argus.service` | Every 15 min | Health monitor (bot process, API, OAuth) |

**Config:** Template `services/agents_config.json.template`; production `services/agents_config.json` (gitignored, on VM only). Shared: `anthropic` API key, `google_sheets` credentials, `alerts` settings.

### HOMER (Trading Journal Writer)

Updates `docs/HYDRA_TRADING_JOURNAL.md` after market close. Flow:

1. Detect missing trading days (compare journal vs Google Sheets)
2. Collect Sheets Daily Summary + Positions + Trades tabs + metrics file
3. Fill gaps from fallback sources (HYDRA log file for stop times/P&L; P&L identity derivation)
4. Update sections 1, 2, 3, 4, 5, 8, 9
5. Use Claude API for narrative sections (observations, assessments)
6. Validate journal structure, commit + push to git
7. Send Telegram alert on completion/failure

**Fallback chain for missing trade records:**
1. Google Sheets Trades tab (primary)
2. HYDRA log file (`logs/hydra/bot.log`) — parses MKT-025 stop events
3. P&L identity derivation — `missing_debit = total_stop_debits - sum(known_debits)`

```bash
# Manual run
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python -m services.homer.main'"

# Dry-run (parse + collect, don't write)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python -m services.homer.main --dry-run'"

# Backfill historical data into SQLite
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python -m services.homer.main --backfill'"
```

### Backtesting Database (`data/backtesting.db`)

SQLite populated live by HYDRA's `DataRecorder` + backfilled by HOMER. Current schema: **v8** (added `contracts` per-row + `contracts_per_entry` on daily_summaries, 2026-04-21).

| Table | Content | Rows/day |
|-------|---------|----------|
| `market_ticks` | Heartbeat snapshots (~11s: SPX, VIX, trend, state) | ~1,500 |
| `market_ohlc_1min` | 1-min OHLC from ticks | ~390 |
| `trade_entries` | Entries (strikes, credits, signals, OTM, Greeks, bid-ask, slippage, margin, contracts) | ~3 |
| `trade_stops` | Stop events (debit, P&L, trigger, mid, slippage, salvage, contracts) | 0–3 |
| `daily_summaries` | EOD totals (SPX OHLC, VIX, P&L, entry/stop counts, contracts_per_entry) | 1 |
| `spread_snapshots` | Per-entry cost-to-close every ~10s; leg-level mid/bid/ask | ~3,000 |
| `skipped_entries` | Counterfactual (entries rejected by MKT-011) | 0–3 |
| `entry_mae_mfe` | Max Adverse / Favorable Excursion per side | 0–6 |
| `shadow_entries` | OTM-based shadow selection (observation only) | 0–3 |
| `schema_info` | Schema version | — |

```sql
-- SPX price at a specific time
SELECT timestamp, spx_price, vix_level FROM market_ticks WHERE timestamp LIKE '2026-05-20 11:15%';

-- Daily P&L roll-up
SELECT date, net_pnl, entries_placed, entries_stopped FROM daily_summaries ORDER BY date DESC LIMIT 20;

-- Entries with OTM distances
SELECT date, entry_number, entry_time, spx_at_entry, short_call_strike, short_put_strike,
       otm_distance_call, otm_distance_put, total_credit, contracts
FROM trade_entries WHERE date = '2026-05-20' ORDER BY entry_number;
```

---

## HYDRA Dashboard (v2.0.0)

Real-time read-only monitoring. **100% read-only — zero changes to the bot.**

### Architecture

```
Browser → nginx:8080 → React SPA + /api/* proxy → uvicorn:8001 (FastAPI)
                                                      ↓ reads
                                              hydra_state.json (1s poll)
                                              hydra_metrics.json (10s poll)
                                              backtesting.db (30s poll, WAL mode)
                                              bot.log (2s tail)
                                              intel/*/*.md (agent reports)
```

| Service | Port | Description |
|---|---|---|
| `dashboard.service` | 8001 (localhost) | FastAPI backend (uvicorn, 1 worker) |
| nginx | 8080 (public) | Reverse proxy + static file server |

**Stack:** FastAPI + uvicorn, SQLite `PRAGMA query_only=TRUE`, WebSocket broadcaster. Frontend: React 19 + TypeScript + Vite, Tailwind CSS v4, Zustand, Recharts, TradingView Lightweight Charts. PWA-installable. iOS Scriptable widget at `dashboard/scriptable/HYDRA_Widget.js`.

### Pages

| Page | Route | Content |
|---|---|---|
| Dashboard | `/` | Live entries, SPX chart, cushion bars, P&L w/ comparison context, position heatmap, perf metrics, agents, log feed |
| History | `/history` | Calendar heat map, week/month summary cards, day drill-down + session replay, CSV export |
| Analytics | `/analytics` | 4-tab: Performance / Entries / Stops / Market |
| Comparison | `/comparison` | N-variant panels (gated by env flag) |

### Key API endpoints

| Endpoint | Description |
|---|---|
| `GET /api/health` | Health check |
| `GET /api/hydra/state` | Current HYDRA state |
| `GET /api/hydra/bot-config` | Config flags (read directly from `bots/hydra/config/config.json`) |
| `GET /api/hydra/entries?date=...` | Entries for a date |
| `GET /api/hydra/summary` | Daily summary (P&L, entries, stops, contracts_per_entry) |
| `GET /api/metrics/cumulative` | Lifetime metrics |
| `GET /api/metrics/daily?days=30` | Daily summaries for calendar |
| `GET /api/market/ohlc?date=...` | 1-min OHLC for SPX chart |
| `GET /api/agents/status` | Agent last-run times |
| `GET /api/metrics/{comparisons,performance}` | Historical context for the panel |
| `GET /api/variants/{health,list,comparison,aggregate}` | Variant discovery + cross-day analytics |
| `GET /api/widget` | Flat JSON for iOS Scriptable |
| `WS /ws/dashboard` | Real-time updates |

### Safety guarantees
- 100% read-only — never writes to state, metrics, DB, or logs
- SQLite opened `query_only=TRUE`
- Does NOT import IBClient or any trading code
- Separate systemd service — `systemctl stop dashboard` has zero effect on HYDRA
- Resource capped: 256MB RAM, 25% CPU

### Commands

```bash
# Start/stop/restart
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl {start,stop,restart} dashboard"

# Status + logs
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl status dashboard"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u dashboard -n 50 --no-pager"

# Deploy frontend (build locally, scp, swap)
cd dashboard/frontend && npm run build
gcloud compute scp --recurse dist/ calypso-bot:/tmp/dashboard-dist --zone=us-east1-b
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo cp -r /tmp/dashboard-dist/* /opt/calypso/dashboard/frontend/dist/ && sudo chown -R calypso:calypso /opt/calypso/dashboard/frontend/dist/ && rm -rf /tmp/dashboard-dist"

# Deploy backend (pull, clear cache, restart)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && git pull && find dashboard -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; echo Cache cleared'"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart dashboard"
```

---

## Quick Reference Commands

### Service lifecycle

> **Broker mode (deployed):** `calypso-broker` owns the IBKR session and the OAuth creds — the `hydra*` units talk to it over loopback. Stopping the `hydra*` units does NOT touch the IBKR session (the broker keeps holding it). A **session/auth fault is fixed by restarting `calypso-broker`**, not the `hydra*` units (see the [calypso-broker section](#calypso-broker-shared-session-service)). There is no Saxo `token_keeper` on this branch.

```bash
# Stop HYDRA + variants (broker keeps the IBKR session — strategies stop trading; session stays up)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop hydra hydra_variant_b hydra_variant_c"

# Stop just HYDRA
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop hydra"

# Start the broker FIRST (it owns the session the strategies depend on), then HYDRA + variants
# (run pre-start verification first — see deploy/IBKR_CREDENTIALS_SETUP.md)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start calypso-broker hydra hydra_variant_b hydra_variant_c"

# Restart HYDRA (config / strategy change pickup — does NOT re-auth the IBKR session)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart hydra"

# Restart the broker (session/auth fault, OAuth re-auth — the strategies degrade gracefully through it)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart calypso-broker"
```

### Status / logs

```bash
# All active HYDRA-related services (calypso-broker = the shared IBKR session owner)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl status calypso-broker hydra hydra_variant_b hydra_variant_c dashboard"

# Broker session health + re-auth loop logs (where session/auth problems show up in broker mode)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u calypso-broker -n 50 --no-pager"

# HYDRA logs (50 lines)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hydra -n 50 --no-pager"

# Follow live
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hydra -f"

# Today
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hydra --since today --no-pager"

# Variant B/C
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hydra_variant_b -n 50 --no-pager"

# Agents
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u homer -n 50 --no-pager"
```

### Emergency stop (everything)

```bash
# Stop all trading immediately. The hydra* units are the ones that place orders;
# stopping them halts trading. calypso-broker is a passive session holder (it does
# not trade on its own) — add it to also drop the shared IBKR session.
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop hydra hydra_variant_b hydra_variant_c calypso-broker"
```

Stopping `calypso-broker` drops the one shared IBKR session; the `hydra*` units will fail their `ensure_connected()` health probe and `break` (then systemd retries them, but they stay down without a healthy broker). Bring the broker back FIRST when restarting.

There is **no** Saxo `token_keeper` to worry about on this branch — IBKR OAuth 1.0a is unattended and the broker's re-auth loop survives strategy restarts without external help.

---

## Deployment Workflow

### Pre-Commit Checklist

1. Run the affected test files: `python -m pytest tests/ -q`
2. Update `bots/hydra/__init__.py` version history if behavior changed
3. Update docstrings for any modified functions
4. Update relevant `.md` files (this file, `HYDRA_STRATEGY_SPECIFICATION.md`, `docs/migration/*` if migration-related)
5. **For shared/ib_client.py changes:** re-read [`docs/migration/P7_AUDIT_FINDINGS.md`](docs/migration/P7_AUDIT_FINDINGS.md) to confirm your change doesn't reopen any of the 49 closed findings.

### Push local → VM

```bash
# 1. Commit + push locally
git add -A && git commit -m "your message" && git push

# 2. Pull on VM and clear Python cache (must run as calypso user)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && git pull && find bots shared -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; echo Cache cleared'"

# 3. Restart HYDRA (config / strategy change)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart hydra"

# 4. Verify
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl status hydra"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hydra -n 50 --no-pager"
```

### Cold deploy (first VM setup)

1. Encrypt 6 IBKR credentials with `systemd-creds encrypt --name=... > .cred` (see `deploy/IBKR_CREDENTIALS_SETUP.md` step-by-step)
2. `sudo cp deploy/hydra.service /etc/systemd/system/`
3. `sudo systemctl daemon-reload`
4. **Pre-start verification (mandatory):**
   - `sudo systemd-analyze verify /etc/systemd/system/hydra.service`
   - For each .cred: `sudo systemd-creds decrypt <file> - | wc -c` and confirm byte count matches expected (consumer_key 9, access_token/secret ~32, signature/encryption PEM ~1700, dhparam ~400-500)
   - `sudo systemd-creds decrypt /etc/calypso/ibkr/consumer_key.cred -` and confirm it matches 1Password
5. Only if all 3 pass: `sudo systemctl enable --now hydra`
6. `sudo journalctl -u hydra -f` to watch the connect handshake

---

## Troubleshooting

### Snapshot returns metadata-only forever

**Symptom:** Quotes are `{conid, conidEx, _updated}` rows with no price fields, even during regular market hours.

**Cause 1:** `_iserver_primed` flag bug. Verify `_ensure_iserver_primed()` is called before `live_marketdata_snapshot`. (P7-audit C2 fixed this in commit c5f43b3; should not recur.)

**Cause 2:** IBKR data-sharing toggle hasn't propagated. Run `scripts/probe_ibkr_market_data.py` — it tests SPY (control US equity, free real-time) before SPX/VIX (indices, gated on toggle). If SPY shows data but SPX/VIX don't, the index entitlement / data-sharing hasn't propagated yet (allow up to 24h after toggling).

**Cause 3:** No real-time entitlement for the conid (or stale conid). The `_snapshot_with_preflight` warmup-exhaustion WARNING logs distinguish: "metadata-only" = entitlement/conid issue, "empty list" = service outage (P7-audit M17).

### Stop loss closed at wrong amount

**Cause:** Probably NOT the C1 bug anymore (action paths gate on `*_uic`, not `*_position_id`). Check: was the entry merged with another at the same strike? IBKR (like Saxo) merges positions at the same (conid, side); the older order_id gets deleted, the newer one keeps its ID with increased Amount. Look for `_get_position_amount` / `_is_position_shared` log lines around the stop time.

### Order rejected with "Order is filled or canceled"

**Cause:** Race — the order completed (filled or cancelled) between when the bot decided to cancel and the DELETE arrived. IBKR returns 4xx/503 with this body. `RetryPolicy.is_retryable` short-circuits these so we don't waste backoff (and don't trip the orders breaker). The `cancel_order()` method specifically returns `True` on this pattern since "no longer working" is the caller's intent.

### "Order X is not found" during stop confirmation

**Cause:** Same family as above — order was purged after reaching a terminal state. `place_and_wait_for_fill`'s poll loop catches this (`"is not found"`/`"no longer found"`) and surfaces as `status="cancelled"`.

### Bot can't connect — `IBAuthError: LST handshake failed`

**Cause:** Wrong consumer key (Saxo creds in a `.cred` file?), or the consumer key is pre-activation. Verify: `sudo systemd-creds decrypt /etc/calypso/ibkr/consumer_key.cred -` matches 1Password's paper consumer key (9 uppercase A–Z chars). If newly issued, IBKR can take up to several hours to activate.

### `ProtectSystem=strict` failing to start service

**Cause:** Almost certainly NOT this — `LoadCredentialEncrypted=` runs BEFORE sandboxing, so credentials are accessible. More likely: the bot is trying to write somewhere outside `/opt/calypso`. Check `journalctl -u hydra` for the actual write path.

### Empty `$CREDENTIALS_DIRECTORY` causes `RuntimeError`

**Cause:** systemd's credential load failed. Run `sudo systemd-analyze verify /etc/systemd/system/hydra.service` and check journalctl for the credential-load error. **Do not** unset `$CREDENTIALS_DIRECTORY` to "fix" this — that would fall through to dev creds.

### Bots running old code after deploy

**Cause:** Python bytecode cache. Always clear after pull:

```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && git pull && find bots shared -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; echo Cache cleared'"
```

### Bot frozen, stop loss not firing

**Cause:** Some blocking call without a timeout. The Fix #64+#68 audit pass added timeout-protected wrappers for Google Sheets (10s), Secret Manager (10s), config token-coordinator file locks (10s polling on `LOCK_NB`), and `ib_oauth` request flows (30s). If the bot freezes again, identify the blocker via `py-spy dump --pid $(pgrep -f hydra.main)` and add a timeout. The IBKR side is already covered by `_ib_call`'s retry+breaker.

---

## Running Diagnostic Scripts on VM

Scripts that hit IBKR need a real (paper-authenticated) `IBClient` — they run on the VM, not locally (local dev path is fine for offline analysis).

### Requirements
1. Run as `calypso` user (file permissions + Secret Manager + credentials access)
2. From `/opt/calypso` (import path)
3. Use the virtualenv Python (`.venv/bin/python` has ibind + ibind-rsa-tools installed)
4. For IBKR-talking scripts: env vars OR systemd credentials must be visible. systemd's `LoadCredentialEncrypted=` files are only accessible inside `hydra.service` — scripts won't see them. For local-script use, either:
   - Export `IBIND_OAUTH1A_*` env vars in the shell first, OR
   - Run via `sudo systemd-run --unit=oneshot-probe --service-type=oneshot ...` borrowing the same `LoadCredentialEncrypted=` lines

### Pattern: heredoc multi-line script

```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python << \"SCRIPT\"
import sys
sys.path.insert(0, \"/opt/calypso\")
from shared.ib_client import IBClient, IBConfig
from shared.ib_oauth import load_credentials

client = IBClient(IBConfig(credentials=load_credentials(\"paper\")))
client.connect()
try:
    positions = client.get_positions()
    print(f\"Found {len(positions)} positions\")
    for p in positions:
        print(p)
finally:
    client.disconnect()
SCRIPT
'"
```

### The Step 2 probe

`scripts/probe_ibkr_market_data.py` is the canonical IBKR-data-flow diagnostic:

```bash
# Locally (env vars exported from 1Password)
source .venv/bin/activate
python scripts/probe_ibkr_market_data.py 2>&1 | tee scripts/probe_mktdata_$(date +%H%M%S).log
```

It tests SPY (control US equity) before SPX/VIX (indices). Output interpretation:
- **Control SPY OK, SPX/VIX NO DATA** → index data-sharing toggle not propagated yet
- **Everything NO DATA** → broader broker-data-sharing issue
- **All DATA OK** → Step 2 passes; check field 6509 for `R`/`D`/`Z` availability

---

## Backups (Polish Item 4)

Three files are backed up daily to Google Cloud Storage. A fourth (state file) gets sub-daily snapshots via `ExecStartPre` (Polish Item 5).

### Daily GCS backups via `db_backup.service` + `db_backup.timer`

Runs daily at **23:00 UTC** (= 7 PM EDT / 6 PM EST — after market close + after HOMER's journal write). Backs up:

| Source on VM | GCS destination (under `gs://calypso-backups/`) |
|---|---|
| `/opt/calypso/data/backtesting.db` | `backtesting_YYYYMMDD.db` |
| `/opt/calypso/data/hydra_metrics.json` | `hydra_metrics_YYYYMMDD.json` |
| `/opt/calypso/data/hydra_state.json` | `hydra_state_YYYYMMDD.json` |

`deploy/db_backup.service` shells `gsutil cp` per file. Failure of metrics/state copies is non-fatal (the `|| true` at the end of the `ExecStartPost`) — the DB copy is the primary; the JSON files are best-effort because they're tiny + can be re-synthesized from the DB in the worst case.

### Verify the timer is enabled

```bash
# Check the timer's next + last run
gcloud compute ssh calypso-bot --zone=us-east1-b --command="systemctl list-timers db_backup.timer --no-pager"
# Expected: NEXT = today 23:00 UTC (or tomorrow if already fired today); LAST = yesterday's 23:00 UTC

# Confirm the service is enabled
gcloud compute ssh calypso-bot --zone=us-east1-b --command="systemctl is-enabled db_backup.timer"
# Expected: enabled

# Test gsutil works as the calypso user (read-only check)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso gsutil ls gs://calypso-backups/ | tail -5"
# Expected: 3-15 files visible (recent backtesting/metrics/state)
```

### Sub-daily state-file snapshots

`hydra.service`'s `ExecStartPre=` invokes `scripts/pre_start_snapshot.sh` before EVERY (re)start. Snapshots land in:

- HYDRA main: `/opt/calypso/data/state_snapshots/hydra_state.pre_restart_<UTC>.json`
- Variant B: `/opt/calypso/data/variant_b/state_snapshots/...`
- Variant C: `/opt/calypso/data/variant_c/state_snapshots/...`

Retention: 50 most recent snapshots per dir (~500 KB ceiling). Older are deleted by the retention sweep. The script ALWAYS exits 0 — a snapshot failure never blocks bot start.

### Restore procedure

See [`docs/migration/RUNBOOKS.md`](docs/migration/RUNBOOKS.md) **RB-5** (state-file corruption → restore from local snapshot) and **RB-7** (GCS-backed restore). RB-7's rehearsal cadence is once every 30 days; record the run in the trading journal per `LIVE_READINESS_CHECKLIST.md` Gate 7.

### Operator one-liner — confirm yesterday's backup landed

```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso gsutil ls gs://calypso-backups/hydra_state_$(date -u -d yesterday +%Y%m%d).json 2>&1"
# Expected: gs://calypso-backups/hydra_state_YYYYMMDD.json (single line, the path)
# If missing: gsutil prints 'BucketNotFoundException' or 'AccessDeniedException' or 'matched no objects'.
```

---

## VM System Commands

```bash
# SSH interactive
gcloud compute ssh calypso-bot --zone=us-east1-b

# Disk
gcloud compute ssh calypso-bot --zone=us-east1-b --command="df -h"

# Memory
gcloud compute ssh calypso-bot --zone=us-east1-b --command="free -h"

# Python processes
gcloud compute ssh calypso-bot --zone=us-east1-b --command="ps aux | grep python"

# Log directories
gcloud compute ssh calypso-bot --zone=us-east1-b --command="ls -la /opt/calypso/logs/"

# Data directory
gcloud compute ssh calypso-bot --zone=us-east1-b --command="ls -la /opt/calypso/data/"
```

---

## Config Files

**IMPORTANT:** Config files (`bots/hydra/config/config*.json`) are `.gitignore`'d and edited directly on the VM. Real credentials come from Secret Manager (Telegram, Google Sheets) or systemd-creds (IBKR OAuth) — never from config files.

```bash
# View HYDRA config
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /opt/calypso/bots/hydra/config/config.json"

# View variant configs
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /opt/calypso/bots/hydra/config/config_variant_b.json"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /opt/calypso/bots/hydra/config/config_variant_c.json"

# Edit (then restart for changes to take effect)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso nano /opt/calypso/bots/hydra/config/config.json"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart hydra"

# View service files
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /etc/systemd/system/hydra.service"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /etc/systemd/system/hydra_variant_b.service"
```

### Mode (dry-run vs paper-live)

`config.json` is the source of truth. Set `"dry_run": true` (root level) for simulation, `false` for actual paper-money orders. CLI `--dry-run` flag overrides. Default is `false`.

> Reminder: even with `dry_run: false`, this branch trades the IBKR **paper account** only. There is no live-money path.

```bash
# Flip dry_run via heredoc
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python << \"SCRIPT\"
import json
with open(\"bots/hydra/config/config.json\") as f: c = json.load(f)
c[\"dry_run\"] = True
with open(\"bots/hydra/config/config.json\", \"w\") as f: json.dump(c, f, indent=2)
print(\"dry_run =\", c[\"dry_run\"])
SCRIPT
'"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart hydra"
```

---

## Google Secret Manager

```bash
# List secrets
gcloud secrets list --project=calypso-trading-bot

# View a secret
gcloud secrets versions access latest --secret=SECRET_NAME --project=calypso-trading-bot
```

**Active secrets on this branch:**
- `calypso-telegram-credentials` — bot token + chat id
- `calypso-alert-config` — alert email (optional)
- `calypso-google-sheets-credentials` — service-account JSON
- `calypso-anthropic-api-key` — agent suite (Claude API)
- `calypso-polygon-api-key` — Brandon variants B/C (Options Starter tier)

IBKR OAuth is **not** in Secret Manager on this branch — it's in systemd-creds for paper, accessed only from within `hydra.service`'s sandbox.

---

## Key IBKR Symbols + Conids

Conids are not stable across IBKR's environments and can change without notice. Always resolve via `qualify_contract()` — never hard-code. For reference (paper, late 2025 / early 2026):

| Symbol | sec_type | Approximate conid | Description |
|--------|----------|-------------------|-------------|
| SPX | IND | 416904 | S&P 500 index (cash-settled) |
| SPXW | OPT trading_class | — | SPX Weekly options (0DTE PM-settled) |
| VIX | IND | 13455763 | CBOE VIX index (verified via Step 2 probe 2026-05-24) |
| SPY | STK | 756733 | SPDR S&P 500 ETF (used as control instrument in probes) |

The `IBClient._conid_cache` keys on `(symbol, expiry_iso, strike, right, trading_class, sec_type)` and is cleared on `disconnect()`.

---

## Migration history

Saxo → IBKR migration spans 7 functional phases (F1–F7) + 7 cleanup passes (P1–P7). Code-complete on this branch as of 2026-05-22. Phases:

| Phase | Scope |
|---|---|
| F1 | Auth + LST handshake (`IBClient.connect`) |
| F2 | Contract qualification (`qualify_contract` + conid cache) |
| F3 | Option chain (`get_option_chain` via probed secdef behavior) |
| F4 | Position read + reconciliation (conid-quantity model — IBKR has no per-leg position id) |
| F5 | Closed-position price (`/iserver/account/trades` + `_deferred_stop_fill_lookup`) |
| F6 | Order write path (`place_order`, `place_and_wait_for_fill`, `cancel_order`, `modify_order`, cOID dedup) |
| F7 | Strategy-layer broker abstraction (read helpers + balance + ORDER-004 BP gate) |
| P1–P7 | Imports, dead-Saxo-helper purge, method ranges audit, broker-abstraction flattening, streaming subsystem, retry + per-family circuit breakers, go-live (re-auth gate, systemd creds, multi-agent code audit) |

P7 multi-agent code audit found and closed 49 issues (4 Critical, 13 High, 17 Medium, 15 Low) across 3 audit rounds. The senior-overseer Round 3 verdict was PASS — branch is cleared for VM deploy + paper smoke test. See [`docs/migration/P7_AUDIT_FINDINGS.md`](docs/migration/P7_AUDIT_FINDINGS.md) for the full register.

Detailed plan: [`docs/migration/HYDRA_STANDALONE_REWRITE_PLAN.md`](docs/migration/HYDRA_STANDALONE_REWRITE_PLAN.md). Per-phase design docs under `docs/migration/F*_*.md` and `docs/migration/P*_*.md`.

---

## Key Lessons Learned

### From the IBKR migration (this branch)

1. **IBKR has no per-leg position id.** Every action path that used to gate on `*_position_id` must gate on `*_uic` (= conid). 6 of 13 P7-audit High findings (C1, H1, H2, H9, M7, M8) were this pattern.

2. **The `/iserver/accounts` preflight is mandatory.** Without it, `live_marketdata_snapshot` and `/iserver/account/trades` silently return metadata-only forever. `portfolio_accounts` does NOT satisfy this — it's a different namespace (P7-audit C2).

3. **Place-response status field is `order_status`, not `status`.** Reading the wrong key means instant fills look like "submitted" and the bot retries → double-positions (P7-audit C3).

4. **Place-response doesn't carry fill detail.** On instant fill, fetch `get_order_status(order_id)` for authoritative `filledQuantity` / `avgPrice`; the place-response only has the status (P7-audit C4).

5. **IBKR uses 503 for some 4xx-semantic permanent errors** (`is not found`, `already filled`, `already cancel`, `order is filled or canceled`). The retry policy must short-circuit these or it wastes backoff and trips the breaker (`shared/ib_retry.py:is_retryable`).

6. **`AveragePrice` / `ExecutionPrice` / `Price` — not `FilledPrice`.** Activities endpoint field naming. The closed-position lookup (`/iserver/account/trades`) is the authoritative fallback when activities returns 0.

7. **Crossed quotes happen during fast moves.** `mid = (bid+ask)/2` on a crossed market is nonsense; `_parse_quote_row` returns `mid=None` instead (P7-audit L9).

8. **Snapshot warmup is real.** First poll for a fresh conid returns metadata-only; 12 × 0.5s = 6s warmup is calibrated. Less is brittle (H10).

9. **cOID dedup is the only safe retry strategy for order placement.** `_ensure_coid` guarantees every order carries a `client_order_id`. IBKR dedupes server-side so a timed-out place that secretly succeeded won't double-fill on retry. Tests pin that the same cOID flows through every retry attempt of the same call (P7-audit H12).

10. **systemd `LoadCredentialEncrypted=` is the right answer for the 6 IBKR creds.** Host-key bound, off-process-env, tmpfs at runtime, runs before sandboxing so `ProtectSystem=strict` doesn't block reading. Pre-start verification (3 mandatory checks before `systemctl enable`) catches encrypt-step errors at the operator level.

### Operator gotchas (preserved from Saxo era, still apply)

11. **Always clear Python bytecode cache after `git pull` on VM.** Stale `__pycache__` was the cause of multiple "fix isn't running" incidents.

12. **Daily summary only at market close, not calendar day reset.** Calendar days change at midnight UTC (7 PM ET); trading days end at 4 PM ET. Never send a daily summary from `_reset_for_new_day()` — only from main.py after-hours check.

13. **Google Sheets API can hang indefinitely.** All `gspread` calls are wrapped in `_sheets_call_with_timeout()` (10s) so a 503 doesn't freeze the trading loop. Same pattern applied to Secret Manager (10s) and file locks (10s polling on `LOCK_NB`).

14. **Profit target must not exceed credit received.** Otherwise it's literally unreachable. `_calculate_profit_target` caps the target at credit.

15. **HOMER's fallback chain matters.** When Sheets logging times out, stop events still need to land in the journal. HOMER falls back to log-file parsing of MKT-025 events, then P&L identity derivation for the missing piece.

For the full 86-fix history including all Saxo-era bugs and resolutions, see `bots/hydra/__init__.py` Version History.

---

## Documentation

### By problem type

| Problem | Document | Key sections |
|---------|----------|--------------|
| IBKR migration history | [docs/migration/HYDRA_STANDALONE_REWRITE_PLAN.md](docs/migration/HYDRA_STANDALONE_REWRITE_PLAN.md) | F1–F7 + P1–P7 phases |
| P7 audit findings (49 issues, all closed) | [docs/migration/P7_AUDIT_FINDINGS.md](docs/migration/P7_AUDIT_FINDINGS.md) | Round 1 register + Round 2/3 verifications |
| Credentials setup | [deploy/IBKR_CREDENTIALS_SETUP.md](deploy/IBKR_CREDENTIALS_SETUP.md) | One-time setup + pre-start checklist |
| HYDRA strategy spec | [docs/HYDRA_STRATEGY_SPECIFICATION.md](docs/HYDRA_STRATEGY_SPECIFICATION.md) | Full spec: decision flows, MKT rules, stop math |
| HYDRA trading journal | [docs/HYDRA_TRADING_JOURNAL.md](docs/HYDRA_TRADING_JOURNAL.md) | Daily results (updated by HOMER) |
| Buffer optimization | [docs/HYDRA_BUFFER_OPTIMIZATION.md](docs/HYDRA_BUFFER_OPTIMIZATION.md) | Per-VIX-regime buffer study |
| Early close analysis | [docs/HYDRA_EARLY_CLOSE_ANALYSIS.md](docs/HYDRA_EARLY_CLOSE_ANALYSIS.md) | Why MKT-018 is disabled |
| MEIC base strategy | [docs/MEIC_STRATEGY_SPECIFICATION.md](docs/MEIC_STRATEGY_SPECIFICATION.md) | Tammy Chambless's MEIC |
| Variant testing | [docs/HYDRA_VARIANT_TESTING_PLAN.md](docs/HYDRA_VARIANT_TESTING_PLAN.md) | Adding new variants |
| Alert system | [docs/ALERTING_SETUP.md](docs/ALERTING_SETUP.md) | Pub/Sub + Cloud Function deploy |
| Multi-bot Position Registry | [docs/MULTI_BOT_POSITION_MANAGEMENT.md](docs/MULTI_BOT_POSITION_MANAGEMENT.md) | Vestigial on this branch but still loaded |
| Scripts inventory | [scripts/README.md](scripts/README.md) | Which script to use for which task |

### Saxo-era docs (historical, on `main`)

| Document | Note |
|---|---|
| `docs/SAXO_API_PATTERNS.md` | Saxo-era API patterns — superseded by this file's "IBKR Integration" section |
| `docs/IRON_FLY_*.md` | Iron Fly bot — **deleted on this branch** (P5a); kill-switched on `main` |
| `docs/DELTA_NEUTRAL_*.md` | Delta Neutral bot — **deleted on this branch** (P5a); kill-switched on `main` |
| `docs/MEIC_EDGE_CASES.md` | 79 MEIC edge cases (still relevant — HYDRA inherits from MEIC) |

---

## Important Notes

1. **Git on VM:** must run as `calypso`: `sudo -u calypso bash -c 'cd /opt/calypso && git pull'`
2. **Service names use underscores:** `hydra`, `hydra_variant_b`, `hydra_variant_c`, `dashboard`
3. **Log locations:** `/opt/calypso/logs/hydra/bot.log`; variants under `/opt/calypso/logs/hydra_variant_{b,c}/`
4. **State files:** `data/hydra_state.json`, `data/variant_b/hydra_state.json`, `data/variant_c/hydra_state.json`
5. **Position Registry:** `data/position_registry.json` — vestigial on IBKR (always empty), kept loaded for back-compat
6. **Token Keeper:** dead on this branch. The `services/token_keeper/` code and the `deploy/token_keeper.service.disabled-on-this-branch` unit (suffixed so no `deploy/*.service` install loop can pick it up) exist only for back-compat with `main` and must never be started. IBKR OAuth 1.0a is unattended.
7. **All four sibling bots:** **deleted on this branch** (P5a/P5b). `git ls-tree HEAD bots/` shows only `__init__.py` + `hydra/`. The kill-switched versions live on `main`.
8. **The legacy `--live` CLI flag:** retained for back-compat as a no-op. The bot logs a NOTE at startup if it sees the flag.
9. **Branch policy:** non-trivial work happens on a feature branch off `hydra-ibkr-standalone`. Merge back via PR. Pre-merge: full test suite (`python -m pytest tests/ -q`) must pass.
10. **Memory:** `bots/hydra/__init__.py` Version History is the authoritative log of behavior changes. CLAUDE.md (this file) is the operator reference and intentionally summarizes — historical fix detail belongs in the version history, not here.
