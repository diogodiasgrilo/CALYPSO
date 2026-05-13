# Saxo → Interactive Brokers Migration Plan

> **Status**: research + plan only. No code changes yet. This document is the prescriptive sequencing for the migration; the companion `INTERACTIVE_BROKERS_API_REFERENCE.md` is the supporting encyclopedia.
>
> **Migration scope**: full replacement of `shared/saxo_client.py` (5,152 lines) and the WebSocket streaming layer with an IBKR equivalent built on `ib_async`. All four bots that import the Saxo client (`hydra/`, `meic/`, `iron_fly_0dte/`, `delta_neutral/`, `rolling_put_diagonal/`) cut over together.
>
> **Compiled**: 2026-05-13.
>
> **Companion**: [`INTERACTIVE_BROKERS_API_REFERENCE.md`](./INTERACTIVE_BROKERS_API_REFERENCE.md)

---

## Table of contents

1. [Goals and non-goals](#1-goals-and-non-goals)
2. [Saxo surface inventory — what we actually use](#2-saxo-surface-inventory)
3. [Saxo → IB call-site mapping](#3-saxo--ib-call-site-mapping)
4. [Target architecture](#4-target-architecture)
5. [Migration phases](#5-migration-phases)
6. [Code skeleton — `shared/ib_client.py`](#6-code-skeleton)
7. [Rollout & cutover](#7-rollout--cutover)
8. [Risk register](#8-risk-register)
9. [Pre-flight checklist (before any live cutover)](#9-pre-flight-checklist)
10. [Post-cutover validation](#10-post-cutover-validation)

---

## 1. Goals and non-goals

### Goals

- **Replace Saxo entirely.** Every call to `shared.saxo_client.SaxoClient` must route to an IBKR equivalent.
- **Preserve all current bot logic.** HYDRA's strategy code, MEIC, the dry-run harness, dashboard — these are NOT touched. Only the broker-facing adapter changes.
- **Drop Polygon Options Starter** ($29/mo) — IB OPRA gives us streaming bid/ask + Model Greeks + per-strike OI in one feed.
- **Cut commission costs ~75%** — IB Pro tiered ($0.65/contract) vs Saxo ($2.50/leg) saves ~$75 per IC round-trip at 10c.
- **Tighter NBBO + native CBOE Complex Order Book routing** — better fills on 4-leg iron condors.

### Non-goals (deferred to a future phase)

- Refactoring HYDRA strategy logic (out of scope).
- Migrating to Portfolio Margin (requires $110K equity floor; not yet).
- Section 1256 60/40 US tax optimization (we're EU-tax-resident).
- Building a fancy reconnection state machine beyond `ib_async.Watchdog` (start simple).

---

## 2. Saxo surface inventory

Today's Saxo client is **5,152 lines**, of which the bot code actually exercises this concrete subset (counted via `grep -hE "self.client\.[a-z_]+" bots/`):

### 2.1 Methods called by HYDRA + MEIC

| Method | Purpose | Hit frequency |
|---|---|---|
| `authenticate(force_refresh=False)` | OAuth refresh-token exchange | Once at startup, on token expiry |
| `client_key` (property) | Account identifier | Per order |
| `get_account_info()` | Account metadata | Startup |
| `get_balance()` | Live margin / available BP | Every entry decision + per-tick monitoring |
| `get_quote(uic, asset_type)` | Single instrument quote | SPX (UIC 4913) + VIX (UIC 10606) every monitor tick |
| `get_quotes_batch(uics, asset_type)` | Batch quotes for option legs | Per stop-monitoring tick |
| `get_vix_price(vix_uic)` | VIX spot (alias for get_quote) | Every tick |
| `get_option_chain(...)` | Option strike grid for expiry | Per entry |
| `get_option_greeks(uic, asset_type)` | δ/γ/θ/ν/IV for one contract | Strike-selection step |
| `get_positions(include_greeks=True)` | Open positions list | Reconciliation at startup + per-tick |
| `get_closed_position_price(uic, buy_or_sell)` | Settle price for expired position | EOD settlement |
| `place_order(...)` | Single-leg order | Mostly unused (we use limit-with-timeout) |
| `place_emergency_order(...)` | Market-fallback close | Stop-loss escalation |
| `place_limit_order_with_timeout(...)` | Limit order with cancel-after-N-sec | Primary entry + close path |
| `cancel_order(order_id)` | Cancel working order | After timeout |
| `get_order_status(order_id)` | Check fill state | After place |
| `get_open_orders()` | Open orders list | Reconciliation |
| `check_order_filled_by_activity(...)` | Activity-log fill detection (Saxo race fix) | Post-place verification |
| `get_chart_data(...)` | Historical 1-min bars | EMA20/40 trend calc, intraday OHLC |
| `get_fx_rate(...)` | EUR↔USD rate | P&L currency normalization |
| `start_price_streaming(uics, asset_type, ...)` | WebSocket subscribe | At startup |
| `subscribe_to_option(uic, ...)` | Subscribe to one option | Per entry |
| `is_websocket_healthy()` | Streaming health check | Every tick |
| `is_heartbeat_alive(max_age_seconds)` | Stream heartbeat | Every tick |
| `stop_price_streaming()` | Tear down stream | EOD |

### 2.2 HTTP endpoints we use (from `_make_request` callers)

```
GET  /openapi/trade/v1/infoprices         — single quote
POST /openapi/trade/v1/infoprices/list    — batch quotes
POST /openapi/trade/v2/orders             — place order
PUT  /openapi/trade/v2/orders/{order_id}  — modify
DELETE /openapi/trade/v2/orders/{order_id} — cancel
POST /openapi/trade/v2/positions/{pos_id} — close position
POST /openapi/trade/v1/prices/subscriptions — start streaming
DEL  /openapi/trade/v1/prices/subscriptions/{ctx_id}/{ref_id}
GET  /openapi/port/v1/positions
GET  /openapi/port/v1/orders/me
GET  /openapi/port/v1/orders/{client_key}/{order_id}
GET  /openapi/port/v1/accounts/me
GET  /openapi/port/v1/accounts/{account_key}
GET  /openapi/port/v1/balances
GET  /openapi/port/v1/closedpositions
GET  /openapi/ref/v1/instruments
GET  /openapi/ref/v1/instruments/details
GET  /openapi/ref/v1/instruments/contractoptionspaces/{root}
GET  /openapi/chart/v3/charts
WS   /openapi/streamingws/connect
```

### 2.3 Streaming subscriptions

- One websocket connection, multiplexed by `ref_id` per subscription
- Subscriptions: SPX UIC 4913, VIX UIC 10606, ~80 SPX option contracts during entry+monitoring
- Heartbeat tracked via `_handle_streaming_message` last-message-time

---

## 3. Saxo → IB call-site mapping

Every Saxo client method gets a 1:1 IBKR replacement. Method signature stays close to the existing surface so HYDRA / MEIC / dashboard code doesn't change.

| Saxo method | IBKR replacement (`shared/ib_client.py`) | IB API / `ib_async` call |
|---|---|---|
| `authenticate()` | `IBClient.connect()` → `Watchdog` keepalive | `IB.connectAsync(host, port, clientId)` — gateway handles auth |
| `client_key` | `IBClient.account_id` | `IB.managedAccounts()[0]` (or pin from config) |
| `get_account_info()` | `IBClient.get_account_info()` | `IB.accountSummary()` |
| `get_balance()` | `IBClient.get_balance(currency='USD')` | `IB.accountSummary(tags='AvailableFunds,BuyingPower,ExcessLiquidity', account=...)` filtered to `$LEDGER:USD` |
| `get_quote(uic, asset_type)` | `IBClient.get_quote(symbol_or_contract)` | `IB.reqMktData(contract, '', snapshot=True)` — one-shot, releases ticker line |
| `get_quotes_batch(uics, asset_type)` | `IBClient.get_quotes_batch(contracts)` | Loop with `snapshot=True` OR persist subscriptions if list stable; max 100 streaming |
| `get_vix_price(...)` | `IBClient.get_vix_price()` | `IB.reqMktData(Index('VIX', 'CBOE'), snapshot=True)` |
| `get_option_chain(root_uic, expiry)` | `IBClient.get_option_chain(symbol, expiry, trading_class='SPXW')` | `IB.reqSecDefOptParams(...)` for strikes/expiries; then `qualifyContracts` for each strike to get `conId` |
| `get_option_greeks(uic, asset_type)` | `IBClient.get_option_greeks(contract)` | `IB.reqMktData(opt_contract, genericTickList='106,165,221,225,233', snapshot=False)` → wait for `modelGreeks` ticker |
| `get_positions(include_greeks=True)` | `IBClient.get_positions()` | `IB.positions()` + per-contract `qualifyContracts` for greek context |
| `get_closed_position_price(...)` | `IBClient.get_closed_position_price(...)` | `IB.reqExecutions()` filtered by execId / time |
| `place_order(...)` | `IBClient.place_order(contract, action, qty, order_type, limit_price=...)` | `IB.placeOrder(contract, Order(...))` |
| `place_emergency_order(...)` | `IBClient.place_market_order(...)` | `IB.placeOrder(contract, MarketOrder(...))` |
| `place_limit_order_with_timeout(...)` | `IBClient.place_limit_with_timeout(contract, action, qty, limit_price, timeout_sec)` | `placeOrder` with `Order(orderType='LMT', tif='DAY', ...)`, await fill, `cancelOrder` on timeout |
| `cancel_order(order_id)` | `IBClient.cancel_order(order_id)` | `IB.cancelOrder(Order(orderId=...))` |
| `get_order_status(order_id)` | `IBClient.get_order_status(order_id)` | Maintain dict from `orderStatusEvent` callbacks |
| `get_open_orders()` | `IBClient.get_open_orders()` | `IB.reqOpenOrders()` (returns once) or `IB.openOrders()` (cached list) |
| `check_order_filled_by_activity(...)` | **DELETE** | IB's order status is broker-side authoritative; no race condition equivalent |
| `get_chart_data(symbol, ...)` | `IBClient.get_chart_data(contract, duration, bar_size)` | `IB.reqHistoricalData(...)` |
| `get_fx_rate('USD', 'EUR')` | `IBClient.get_fx_rate('USD', 'EUR')` | `IB.reqMktData(Forex('USDEUR'), snapshot=True)` OR account summary FX rate |
| `start_price_streaming(...)` | `IBClient.subscribe_quotes(contracts)` | Multiple `IB.reqMktData(contract, snapshot=False)` calls; reuses existing connection |
| `subscribe_to_option(uic, ...)` | `IBClient.subscribe_option(opt_contract)` | `IB.reqMktData(opt_contract, genericTickList='106,165,221,225,233')` |
| `is_websocket_healthy()` | `IBClient.is_connected()` | `IB.isConnected()` |
| `is_heartbeat_alive(...)` | `IBClient.last_tick_age()` | Track via Watchdog's last-received-tick timestamp |
| `stop_price_streaming()` | `IBClient.unsubscribe_all()` | Loop `IB.cancelMktData(contract)` |

### 3.1 NEW IB-only methods (no Saxo equivalent)

| Method | Why we need it |
|---|---|
| `IBClient.place_iron_condor(call_short, call_long, put_short, put_long, contracts, net_credit_limit, expiry)` | One-shot 4-leg combo placement via `BAG` contract. Replaces our hand-rolled multi-leg flow. |
| `IBClient.place_vertical_spread(short, long, contracts, net_credit_limit, side)` | 2-leg spread for one-sided entries OR closing one side of an open IC. Same BAG mechanism, 2 legs. |
| `IBClient.what_if(order)` | Pre-trade margin check. Returns `initMarginChange`/`maintMarginChange`. **Replaces our ORDER-004 BP-per-IC gate** with broker-authoritative numbers. |
| `IBClient.qualify(contract)` | `ib_async.qualifyContracts` wrapper. Required before any order on an `Option(...)` — resolves `conId`. |

### 3.2 Methods to delete entirely

| Saxo method | Why not needed on IB |
|---|---|
| `_oauth_authorization_flow`, `_exchange_code_for_token`, `_refresh_access_token` | Gateway handles auth. No OAuth refresh in our code. |
| `_upgrade_session_for_realtime_data`, `_ensure_session_capabilities` | IB has no session-tier upgrade; data subs are account-level. |
| `signal_session_downgrade` | No equivalent concept on IB. |
| `check_order_filled_by_activity` | No race condition — `orderStatusEvent` is authoritative. |
| The entire WebSocket binary-message decoder (`_decode_binary_ws_message`, `_handle_streaming_message`) | `ib_async` parses ticks natively, exposes typed events. |

---

## 4. Target architecture

### 4.1 Process topology on the GCE VM

```
┌──────────────────────────────────────────────────────────┐
│ GCE VM (calypso-bot, us-east1-b)                         │
│                                                           │
│ ┌─────────────────────┐    ┌─────────────────────────┐  │
│ │ ib-gateway-paper    │    │ ib-gateway-live          │  │
│ │ Docker container    │    │ Docker container         │  │
│ │ Port 127.0.0.1:4002 │    │ Port 127.0.0.1:4001      │  │
│ │ IBC auto-login      │    │ IBC auto-login           │  │
│ │ Image: gnzsnz/      │    │ Image: gnzsnz/           │  │
│ │  ib-gateway:10.45.1e│    │  ib-gateway:10.45.1e     │  │
│ └─────────────────────┘    └─────────────────────────┘  │
│           │                          │                    │
│           │  TCP socket              │                    │
│           ▼                          ▼                    │
│ ┌─────────────────────────────────────────────────────┐ │
│ │ Python bots (systemd services)                       │ │
│ │   hydra.service        → uses ib-gateway-live        │ │
│ │   hydra_variant_b      → uses ib-gateway-paper       │ │
│ │   hydra_variant_c      → uses ib-gateway-paper       │ │
│ │                                                       │ │
│ │   shared/ib_client.py — IBClient adapter             │ │
│ │   shared/saxo_client.py — DELETED post-cutover       │ │
│ └─────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────┘
```

**Key design decisions**:
- **Two gateways** — paper and live, on different ports. Variants B/C stay on paper indefinitely (their whole purpose is dry-run); variant A is the live trader once we cut over.
- **127.0.0.1 only** — TWS API has no socket-level auth. Firewall both ports to loopback. **Never expose to public internet.**
- **Each bot uses a unique `clientId`** to avoid socket conflicts (clientId=1 for variant A live, clientId=2 for variant B paper, clientId=3 for variant C paper).
- **`ib_async.Watchdog`** wraps each bot's `IB.connect()` — handles automatic reconnect on disconnect, weekly server reset.
- **IBC handles login** — username/password from Secret Manager, IBKR Mobile push approves the weekly 2FA challenge.

### 4.2 New module layout

```
shared/
  ib_client.py              — IBClient class, top-level adapter (~1500-2000 LOC est.)
  ib_contracts.py           — SPX/SPXW/VIX contract factories, conId cache
  ib_streaming.py           — quote subscription manager, ticker-line accounting
  ib_orders.py              — order placement helpers (single, vertical, IC combo)
  ib_account.py             — account summary, EUR/USD ledger handling
  ib_watchdog.py            — Watchdog wrapper, disconnect/reconnect lifecycle
deploy/
  ib-gateway-paper.service  — systemd unit for paper gateway
  ib-gateway-live.service   — systemd unit for live gateway
  docker-compose-ib.yml     — IB Gateway Docker compose (paper + live)
  ibc-config-paper.ini      — IBC config for paper account
  ibc-config-live.ini       — IBC config for live account
```

`shared/saxo_client.py` gets **deleted** entirely post-cutover. Bots that need a broker swap their import:

```python
# Before
from shared.saxo_client import SaxoClient
client = SaxoClient(config)

# After
from shared.ib_client import IBClient
client = IBClient(config)
```

The IBClient class exposes the **same public method names** as SaxoClient where possible, so HYDRA / MEIC / dashboard code keeps working with minimal patches.

### 4.3 Connection lifecycle

```
[bot startup]
   │
   ▼
[ib_async.Watchdog(IB(), host='127.0.0.1', port=4002, clientId=1).start()]
   │
   ▼
[IB.connectAsync() → connected event]
   │
   ▼
[register orderStatusEvent / errorEvent / disconnectedEvent callbacks]
   │
   ▼
[on_connect: reqOpenOrders() + reqPositions() to reconcile state]
   │
   ▼
[subscribe_quotes(SPX, VIX, open_legs)]
   │
   ▼
[main loop: heartbeats, entry decisions, stop monitoring]
   │
   ▼ [on disconnect]
[Watchdog auto-reconnects; on_connect re-runs reconciliation]
```

### 4.4 Order-state reconciliation on reconnect

This is the biggest behavior difference from Saxo. On IB, orders are **broker-side persistent** — if the bot crashes mid-order, the order is still live on IBKR's books when we reconnect.

```python
async def reconcile_on_connect():
    # 1. Pull all open orders from broker
    open_orders = await ib.reqOpenOrdersAsync()
    # 2. Cross-reference against state file
    state_open = {e.order_id for e in daily_state.entries if e.is_active()}
    broker_open = {o.orderId for o in open_orders}
    # 3. Three cases:
    only_broker = broker_open - state_open  # orphan on broker — likely our restart raced
    only_state  = state_open - broker_open  # state thinks open but broker doesn't — likely filled or cancelled mid-crash
    both        = broker_open & state_open  # normal — re-attach
    # Handle each: orphan → log + cancel (safer than leaving naked), only_state → query reqExecutions to find fill
```

---

## 5. Migration phases

### Phase 0 — Prerequisites (week 1)

Tasks:
- [ ] Open IBKR Pro account (EU resident, EUR base, REG-T retail) — KYC, funding ~$50K
- [ ] Activate paper trading account
- [ ] Subscribe to market data: US Securities Snapshot Bundle ($10/mo, waived ≥$30 commissions), OPRA Top-of-Book ($1.50/mo, waived ≥$20), Cboe Streaming Market Indexes (~$3-6 for non-pros, **confirm exact name with IBKR**)
- [ ] Enable "IB Key Security via IBKR Mobile" 2FA (only)
- [ ] Confirm OAuth Self-Service Portal access for OAuth 2.0 path (future option)

Verification:
- [ ] Log in to TWS desktop, verify SPX option chain renders with live (not delayed) quotes
- [ ] Verify `Option('SPX', '20260520', 5500, 'C', 'CBOE', tradingClass='SPXW')` resolves in TWS

### Phase 1 — IB Gateway deployment on a dev VM (week 2)

Goal: get IB Gateway running headless with auto-login, before touching any bot code.

Tasks:
- [ ] Provision dev GCE VM (`calypso-ib-dev`, e2-medium, Debian 12)
- [ ] Install Docker
- [ ] Pull `ghcr.io/gnzsnz/ib-gateway:10.45.1e`
- [ ] Configure IBC via `config.ini`:
  - `IbLoginId`, `IbPassword` (from Secret Manager)
  - `TradingMode=paper`
  - `IbDir=/opt/ibc`
  - `JtsDir=/root/Jts`
  - `AutoRestartTime=01:30` (Sunday 01:30 ET — covers weekly server reset)
- [ ] Mount volume for `Jts/` settings + logs
- [ ] Open port 127.0.0.1:4002 (paper); never 0.0.0.0
- [ ] systemd unit with `Restart=always`, `RestartSec=60s`

Verification:
- [ ] `nc 127.0.0.1 4002` returns the IBKR banner
- [ ] Test connect from Python: `IB().connect('127.0.0.1', 4002, clientId=99)`
- [ ] Watch IBC logs verify Sunday auto-restart completes cleanly

### Phase 2 — Build `shared/ib_client.py` adapter (weeks 3-5)

Goal: complete IB adapter matching the Saxo public surface, passing unit tests against a mocked `ib_async.IB`.

Subphases:
- **2a** — Connection + auth: `IBClient.connect()`, `disconnect()`, Watchdog wiring, reconnect-test
- **2b** — Account queries: `get_account_info`, `get_balance` (with `$LEDGER:USD`), `client_key`
- **2c** — Contract resolution: `ib_contracts.py` — SPX/SPXW/VIX factories, qualifyContracts cache
- **2d** — Quotes: `get_quote`, `get_quotes_batch`, `get_vix_price` (snapshot mode for one-shot, streaming for monitoring)
- **2e** — Option chain + greeks: `get_option_chain`, `get_option_greeks`
- **2f** — Positions + orders queries: `get_positions`, `get_open_orders`, `get_order_status`
- **2g** — Order placement: `place_order`, `place_limit_order_with_timeout`, `cancel_order`
- **2h** — **The big one — `place_iron_condor`**: 4-leg BAG construction, net-credit limit, `qualifyContracts` for each leg, `smartComboRoutingParams=[("NonGuaranteed", "1")]` on entry
- **2i** — Streaming: `subscribe_quotes`, `unsubscribe`, heartbeat tracking
- **2j** — Historical bars: `get_chart_data`
- **2k** — Order-state reconciliation: `reconcile_on_connect` per §4.4

Tests: unit tests with mocked `IB` against the existing `tests/test_brandon_*` patterns; integration tests against paper gateway.

### Phase 3 — Shadow-mode parity (weeks 6-7)

Goal: run IBClient ALONGSIDE SaxoClient on the same bot, comparing outputs without acting.

Implementation:
- Add a `SHADOW_BROKER=ib` env var to HYDRA
- In dry-run mode, both clients are queried for every quote, position, and order intent
- A new logger writes a side-by-side diff (Saxo result vs IB result) to `data/broker_shadow_diff.jsonl`
- After 5 trading days, audit the diff log: where do quotes disagree? Where do positions disagree?

Acceptance criteria for advancing:
- [ ] Quote disagreement < 0.05 typical, < 0.20 worst-case (IB is usually tighter than Saxo)
- [ ] Zero position-reconciliation failures across 5 days
- [ ] All Brandon GEX-ADJ / delta-target picks produce the same strikes on both sides
- [ ] No IB-side gateway disconnects unrecovered for > 60 seconds
- [ ] Weekly server reset survived once cleanly

### Phase 4 — Paper-trading cutover for B and C (week 8)

Goal: variants B and C cut over fully to IB paper. Variant A stays on Saxo as the control.

Tasks:
- [ ] Update `bots/hydra/main.py` to instantiate `IBClient` when `HYDRA_VARIANT_ID in {b, c}`
- [ ] Delete the Saxo-shadow code path
- [ ] Run for 5 trading days; compare against historical Saxo-based B/C performance
- [ ] **STOP and re-evaluate** if B/C performance materially diverges from historical

### Phase 5 — Live cutover for A (week 9)

Goal: variant A (live trading) cuts from Saxo live to IB live.

Pre-flight (everything in §9 must pass):
- [ ] Live IBKR Pro account funded
- [ ] Live data subscriptions active
- [ ] Live `ib-gateway-live` Docker container running on port 4001
- [ ] IBC + IBKR Mobile push verified for live login
- [ ] B/C have been on IB paper for ≥2 weeks with no incidents
- [ ] All Saxo positions closed cleanly (or migrated — but cleaner to close)
- [ ] Saxo client kept as dead code for 4 weeks pre-deletion (rollback window)

Tasks:
- [ ] Flip `HYDRA_VARIANT_ID=a` config to use IBClient on port 4001
- [ ] Run dry-run for 1 day on IB live (paper-like behavior but real IB connection)
- [ ] Enable `dry_run=false` for live trading

### Phase 6 — Saxo decommission (week 12+)

After 4 weeks of stable IB-live operation:
- [ ] Delete `shared/saxo_client.py`
- [ ] Delete Saxo OAuth secrets from Secret Manager
- [ ] Close Saxo account (or leave funded as backup broker)
- [ ] Update CLAUDE.md broker references

---

## 6. Code skeleton

A minimal `shared/ib_client.py` to anchor Phase 2. Not production-ready — illustrative.

```python
"""IB adapter for CALYPSO — replaces shared/saxo_client.py.

Public API mirrors SaxoClient where possible so HYDRA/MEIC/dashboard code
sees minimal changes. Built on ib_async 2.1+ (the maintained successor to
ib_insync). Connects to IB Gateway running locally on the VM.

See docs/migration/SAXO_TO_IB_MIGRATION_PLAN.md for the full migration spec.
"""

from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

from ib_async import (
    IB, Watchdog, Contract, Option, Index, Forex, Stock,
    Order, LimitOrder, MarketOrder, ComboLeg,
    util,
)

logger = logging.getLogger(__name__)


@dataclass
class IBConfig:
    host: str = "127.0.0.1"
    port: int = 4001              # 4001 live gateway, 4002 paper, 7496/7497 TWS
    client_id: int = 1
    account_id: Optional[str] = None   # pinned from config; if None, use managedAccounts()[0]
    readonly: bool = False
    timeout_seconds: float = 30.0


class IBClient:
    """Top-level IBKR adapter — replaces SaxoClient.

    Construction does NOT connect — call `connect()`. Watchdog handles
    reconnect transparently after initial connection.
    """

    def __init__(self, config: dict | IBConfig):
        if isinstance(config, dict):
            ibcfg = config.get("ibkr", {})
            self.cfg = IBConfig(
                host=ibcfg.get("host", "127.0.0.1"),
                port=int(ibcfg.get("port", 4001)),
                client_id=int(ibcfg.get("client_id", 1)),
                account_id=ibcfg.get("account_id"),
                readonly=bool(ibcfg.get("readonly", False)),
            )
        else:
            self.cfg = config
        self.ib = IB()
        self.watchdog: Optional[Watchdog] = None
        self._contract_cache: dict[tuple, Contract] = {}
        self._connected = False

    # ─── Connection / lifecycle ──────────────────────────────────────────

    async def connect(self) -> bool:
        """Connect to IB Gateway via Watchdog. Auto-reconnects on disconnect."""
        try:
            self.watchdog = Watchdog(
                self.ib,
                host=self.cfg.host,
                port=self.cfg.port,
                clientId=self.cfg.client_id,
                appStartupTime=15,
                appTimeout=20,
                retryDelay=2,
                readonly=self.cfg.readonly,
            )
            self.ib.connectedEvent += self._on_connect
            self.ib.disconnectedEvent += self._on_disconnect
            self.ib.errorEvent += self._on_error
            self.watchdog.start()
            await asyncio.sleep(self.cfg.timeout_seconds)
            self._connected = self.ib.isConnected()
            return self._connected
        except Exception as exc:
            logger.exception("IB connect failed: %s", exc)
            return False

    async def _on_connect(self):
        logger.info("IB connected to %s:%s clientId=%s",
                    self.cfg.host, self.cfg.port, self.cfg.client_id)
        # Reconcile state: pull open orders + positions, cross-check state file
        await self._reconcile_on_connect()

    async def _on_disconnect(self):
        logger.warning("IB disconnected — Watchdog will reconnect")
        self._connected = False

    def _on_error(self, reqId: int, errorCode: int, errorString: str, contract: Optional[Contract]):
        # IB error codes: 200=ambiguous contract, 201=order rejected, 502=connection failure, etc.
        if errorCode in (2104, 2106, 2158):  # market-data-farm-connection-OK informational
            return
        logger.warning("IB error %d (req %d): %s", errorCode, reqId, errorString)

    def is_connected(self) -> bool:
        return self.ib.isConnected()

    async def _reconcile_on_connect(self):
        open_orders = await self.ib.reqOpenOrdersAsync()
        positions = await self.ib.reqPositionsAsync()
        logger.info("IB reconcile: %d open orders, %d positions",
                    len(open_orders), len(positions))
        # … cross-check against daily_state file (see §4.4)

    @property
    def account_id(self) -> str:
        if self.cfg.account_id:
            return self.cfg.account_id
        managed = self.ib.managedAccounts()
        if not managed:
            raise RuntimeError("No managed accounts available — gateway not authenticated?")
        return managed[0]

    @property
    def client_key(self) -> str:
        """Saxo-compat alias for account_id."""
        return self.account_id

    # ─── Contract factories ──────────────────────────────────────────────

    def spx_index(self) -> Index:
        return Index("SPX", "CBOE")

    def vix_index(self) -> Index:
        return Index("VIX", "CBOE")

    def spxw_option(self, expiry: date, strike: float, right: str) -> Option:
        """SPXW (weekly/daily, PM-settled) — use for 0DTE."""
        return Option(
            symbol="SPX",
            lastTradeDateOrContractMonth=expiry.strftime("%Y%m%d"),
            strike=strike,
            right=right,           # 'C' or 'P'
            exchange="CBOE",
            tradingClass="SPXW",   # CRITICAL — distinguishes from monthly AM-settled SPX
            currency="USD",
        )

    async def qualify(self, contract: Contract) -> Contract:
        """Resolve a contract to its conId. Cached by (symbol, expiry, strike, right)."""
        key = (contract.symbol, getattr(contract, "lastTradeDateOrContractMonth", ""),
               getattr(contract, "strike", 0), getattr(contract, "right", ""),
               getattr(contract, "tradingClass", ""))
        if key in self._contract_cache:
            return self._contract_cache[key]
        qualified = await self.ib.qualifyContractsAsync(contract)
        if not qualified or not qualified[0].conId:
            raise RuntimeError(f"Failed to qualify contract: {contract}")
        self._contract_cache[key] = qualified[0]
        return qualified[0]

    # ─── Quotes ──────────────────────────────────────────────────────────

    async def get_quote(self, contract: Contract, snapshot: bool = True) -> Optional[dict]:
        """One-shot or streaming quote. Returns dict with bid/ask/last/mid."""
        c = await self.qualify(contract)
        ticker = self.ib.reqMktData(c, "", snapshot=snapshot, regulatorySnapshot=False)
        # Wait for the snapshot to populate (~1-2s)
        for _ in range(20):
            await asyncio.sleep(0.1)
            if ticker.bid > 0 or ticker.ask > 0 or ticker.last > 0:
                break
        return {
            "bid": ticker.bid if ticker.bid > 0 else None,
            "ask": ticker.ask if ticker.ask > 0 else None,
            "last": ticker.last if ticker.last > 0 else None,
            "mid": (ticker.bid + ticker.ask) / 2 if (ticker.bid > 0 and ticker.ask > 0) else None,
            "volume": ticker.volume,
            "timestamp": ticker.time.isoformat() if ticker.time else None,
        }

    # ─── Iron condor (THE big one) ──────────────────────────────────────

    async def place_iron_condor(
        self,
        expiry: date,
        short_call_strike: float, long_call_strike: float,
        short_put_strike: float, long_put_strike: float,
        contracts: int,
        net_credit_limit: float,
        non_guaranteed: bool = True,        # True for entry, False for stop-out closes
    ) -> Order:
        """Place a 4-leg SPX iron condor as a single BAG combo order.

        Args:
            net_credit_limit: minimum acceptable net credit per spread (positive number).
                              The order is BUY of a synthetic "negative spread" — we pay -credit,
                              but in TWS terms we express it as a LIMIT BUY at -net_credit.
                              See ib_async docs + IBKR Campus "Trading 0DTE Options" example.
            non_guaranteed: True = SmartComboRouting accepts legging risk for better fills.
                            False = atomic complex-order-book fill (safer for closes).
        """
        # 1. Qualify each leg to get conIds
        sc = await self.qualify(self.spxw_option(expiry, short_call_strike, "C"))
        lc = await self.qualify(self.spxw_option(expiry, long_call_strike, "C"))
        sp = await self.qualify(self.spxw_option(expiry, short_put_strike, "P"))
        lp = await self.qualify(self.spxw_option(expiry, long_put_strike, "P"))

        # 2. Build the BAG combo contract
        bag = Contract(
            symbol="SPX",
            secType="BAG",
            exchange="CBOE",
            currency="USD",
            comboLegs=[
                ComboLeg(conId=sc.conId, ratio=1, action="SELL", exchange="CBOE"),
                ComboLeg(conId=lc.conId, ratio=1, action="BUY",  exchange="CBOE"),
                ComboLeg(conId=sp.conId, ratio=1, action="SELL", exchange="CBOE"),
                ComboLeg(conId=lp.conId, ratio=1, action="BUY",  exchange="CBOE"),
            ],
        )

        # 3. Round limit price to $0.05 increments (CBOE requirement)
        limit_price = round(-net_credit_limit * 20) / 20  # negative = we receive credit

        # 4. Place the order
        order = LimitOrder(
            action="BUY",          # buying the combo means we receive the credit
            totalQuantity=contracts,
            lmtPrice=limit_price,
            orderType="LMT",
            tif="DAY",
            smartComboRoutingParams=[("NonGuaranteed", "1" if non_guaranteed else "0")],
        )
        trade = self.ib.placeOrder(bag, order)
        logger.info("IC placed: expiry=%s C:%g/%g P:%g/%g x%d @ net %.2f credit",
                    expiry, short_call_strike, long_call_strike,
                    short_put_strike, long_put_strike, contracts, net_credit_limit)
        return trade
```

This is a sketch — the full module will be ~1500-2000 LOC, with all the methods from the §3 mapping table.

---

## 7. Rollout & cutover

### 7.1 Cutover order

```
Phase 4: B/C → IB paper      (low risk, dry-run only)
   ↓
[2 weeks observation]
   ↓
Phase 5: A → IB live          (real money)
   ↓
[4 weeks observation, Saxo dead-code kept]
   ↓
Phase 6: Delete Saxo code     (point of no return)
```

### 7.2 Rollback plan

At each phase, the previous broker integration stays functional. Specifically:

- **Phase 4 rollback**: revert variant config to `BROKER=saxo` env var; restart.
- **Phase 5 rollback**: same — variant A's config flips back to SaxoClient. Saxo account must stay funded during the 4-week observation window.
- **Phase 6 rollback**: NOT POSSIBLE after this point. Don't enter Phase 6 until everything is rock-solid.

### 7.3 Weekly server reset SOP

Every Sunday:
1. IBC's `AutoRestartTime=01:30` triggers Gateway restart
2. IBKR Mobile push notification sent to operator phone
3. **Operator must approve within ~3 minutes** or login fails
4. After approve: Gateway re-establishes session
5. `ib_async.Watchdog` reconnects all bots
6. `_reconcile_on_connect()` re-syncs state

If operator misses the push:
- Gateway stays unauthenticated
- Bots show "disconnected" on dashboard
- No new orders place
- Existing orders stay live on IBKR's books (broker-side persistence)
- Markets don't reopen until Monday — there's a recovery window

**Calendar reminder**: every Sunday at 01:30 ET, the operator's phone must be reachable.

---

## 8. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Weekly 2FA push missed | Medium | High (bot offline Sunday→Monday) | Calendar alert + backup operator with phone access. Multi-device IBKR Mobile login. |
| Gateway hangs / requires manual login | Medium | High | IBC handles 95% of cases; remaining 5% require SSH-into-VM + manual restart. Document the runbook. |
| `ib_async` library breaking change | Low | Medium | Pin version (`ib_async==2.1.0`), test before upgrading. |
| IBKR API breaking change (TWS API version) | Low | Medium | Stay 1-2 versions behind latest, monitor IBKR API Changelog. |
| Combo order partial fill (one side fills, other doesn't) | Medium with `NonGuaranteed=1` | High (left naked short) | Use `NonGuaranteed=0` on stop-out closes (atomic); accept legging risk only on entries where we have safety nets. |
| 100-ticker streaming limit hit | Low under normal operation | Medium (data starvation) | Reserve 60 tickers for monitoring, use snapshot mode for chain scans, alert if >80 tickers active. |
| EUR-USD margin mis-calculation | Low | High (over-leverage) | Use `$LEDGER:USD` summary; cross-check with `whatIf` pre-trade. |
| IBKR account flagged for pattern day trader rules | Low (we're trading defined-risk spreads on 0DTE — usually not day-trades by SEC def) | Medium | Stay below 4 day-trades/5-day window on margin account; switch to cash account if PDT becomes an issue. |
| OPRA subscription accidentally lapsed | Low | High (quotes go 15-min delayed silently) | Monitor `marketDataType` field on every ticker; alert if any return 3 (delayed). |
| Real money loss during cutover bug | High during week 9 | Catastrophic | 2 weeks of paper-mode for B/C BEFORE live. Manual approval gate on first live order. Dashboard kill-switch. |

---

## 9. Pre-flight checklist

**Do NOT cut over variant A to live until ALL of these are checked:**

### IBKR account & data
- [ ] IBKR Pro live account funded ($50K+)
- [ ] Paper trading account active
- [ ] Market data subscriptions: US Securities Snapshot Bundle, OPRA Top-of-Book, Cboe Streaming Market Indexes — billing visible, all active
- [ ] Real-time SPX quote verified (not 15-min delayed) in TWS desktop
- [ ] Real-time SPX option chain verified (SPXW trading class)
- [ ] IBKR Mobile 2FA enabled, push notifications working on operator phone

### Gateway & deployment
- [ ] `ib-gateway-paper` Docker container running, healthy, port 4002
- [ ] `ib-gateway-live` Docker container running, healthy, port 4001
- [ ] Both bound to `127.0.0.1` only; firewall verified (no external access)
- [ ] IBC `config.ini` correct for both paper and live (Trading Mode, login ID, auto-restart time)
- [ ] Sunday weekly auto-restart tested successfully ≥1 time

### Code & tests
- [ ] `shared/ib_client.py` complete; all methods from §3 mapping table implemented
- [ ] Unit tests passing (≥80% coverage on IBClient)
- [ ] Integration tests against paper gateway passing
- [ ] Shadow-mode (Phase 3) ran ≥5 trading days, diff log audited
- [ ] Phase 4 paper-trading for B/C ran ≥10 trading days, no incidents
- [ ] Reconcile-on-connect tested by killing bot mid-order, verified clean recovery

### Strategy & risk
- [ ] HYDRA strategy code unchanged (only broker adapter swapped)
- [ ] Per-IC max-loss calculation matches IB's `whatIfOrder` margin
- [ ] Commission costs recomputed for IBKR Pro tiered structure; min_pnl_per_ic bounds re-tuned
- [ ] Dry-run mode tested on IB paper for ≥3 days
- [ ] Manual approval gate enabled for first 10 live entries

### Operations
- [ ] WATCHMAN audit protocol updated to verify IB-side health (replace Saxo checks)
- [ ] Dashboard shows IB gateway status + last-tick timestamp
- [ ] Telegram alerts wired for IB disconnect, 2FA challenge needed, order rejected
- [ ] On-call runbook documented for: Sunday 2FA tap, gateway restart, "panic close all"
- [ ] Saxo account still funded as rollback safety net

---

## 10. Post-cutover validation

### Day 1
- [ ] All 3 variants connected to IB Gateway
- [ ] SPX + VIX quotes streaming
- [ ] Entry slots fire on schedule
- [ ] First IC placed cleanly (manual approval)
- [ ] First TP / stop fires correctly
- [ ] EOD settlement reconciles

### Week 1
- [ ] Daily P&L matches expectation (within slippage tolerance)
- [ ] No unexpected position drift on reconnect
- [ ] Sunday auto-restart survived
- [ ] Dashboard shows accurate live positions
- [ ] No "BRANDON-GEX-ADJ SKIP" rate change vs Saxo baseline (rules out chain-data quality drift)

### Month 1
- [ ] Commission savings vs Saxo measured (expected ~75% reduction)
- [ ] Fill quality measured (mid-price slippage)
- [ ] Polygon Options Starter unsubscribed → savings $29/mo realized
- [ ] No gateway-uptime issues > 99.5%
- [ ] All operator-friction items documented for runbook

### Quarter 1
- [ ] Saxo account closed (or archived)
- [ ] `shared/saxo_client.py` deleted from repo
- [ ] CLAUDE.md broker references updated
- [ ] Migration retrospective written

---

## Appendix A — Decision log

| Decision | Date | Rationale |
|---|---|---|
| Pick `ib_async` over `ib_insync` | 2026-05-13 | `ib_insync` author deceased early 2024, library inactive since Dec 2023. `ib_async` is the maintained successor under `ib-api-reloaded`, supports Python 3.10-3.14. |
| Pick TWS API / IB Gateway over Web API OAuth 2.0 | 2026-05-13 | Web API OAuth 2.0 is beta as of May 2026 AND retail live trading still requires the Client Portal Gateway per IBKR's own docs. Revisit when IBKR ships gateway-free retail trading. |
| Pick `ghcr.io/gnzsnz/ib-gateway:10.45.1e` Docker image | 2026-05-13 | Stable, bundles IBC 3.23.0, well-maintained by `gnzsnz`. |
| Use single BAG contract for iron condor (not 2 separate spread orders) | 2026-05-13 | Native CBOE Complex Order Book routing, better fills, atomic close-out option. Mature in TWS API. |
| Keep Python-side credit-based stop logic | 2026-05-13 | Native IB stop orders unreliable on illiquid options per IBKR + community consensus. Current code is industry-standard for 0DTE. |
| Drop Polygon Options Starter | 2026-05-13 | IB OPRA includes streaming bid/ask + Model Greeks + per-strike OI in one feed. Polygon only wins for historical chain replay (not used live). Saves $29/mo. |
| Mandate IBKR Pro (not Lite) | 2026-05-13 | Lite ineligible for Web API trading endpoint; Lite is PFOF-routed (no SmartRouting). Pro is mandatory. |

---

## Appendix B — Where to find help

- **IBKR API support**: `api-solutions@interactivebrokers.com` — confirm open questions in §6 of `INTERACTIVE_BROKERS_API_REFERENCE.md`
- **IBKR Campus docs**: https://www.interactivebrokers.com/campus/ibkr-api-page/ibkr-api-home/
- **TWS API reference**: https://interactivebrokers.github.io/tws-api/
- **`ib_async` docs**: https://ib-api-reloaded.github.io/ib_async/
- **`ib_async` GitHub**: https://github.com/ib-api-reloaded/ib_async
- **IBC**: https://github.com/IbcAlpha/IBC
- **Docker image**: https://github.com/gnzsnz/ib-gateway-docker
- **0DTE on IB blog**: https://www.interactivebrokers.com/campus/ibkr-quant-news/trading-0dte-options-with-the-ibkr-native-api/
- **CBOE SPXW spec**: https://www.cboe.com/tradable_products/sp_500/spx_weekly_options/specifications/

---

**Last updated**: 2026-05-13. Update on every phase advancement.
