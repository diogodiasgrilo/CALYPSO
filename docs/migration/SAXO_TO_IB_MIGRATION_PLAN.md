# Saxo → Interactive Brokers Migration Plan (v2)

> **Major rewrite 2026-05-14.** Architecture pivoted from IB Gateway + `ib_async` to **ibind + OAuth 1.0a**, then refined again into **Option 4 hybrid** (standalone IB client → broker abstraction → parallel variant deploy → gradual cutover). All `ib_async`-based code skeletons and Gateway-deployment instructions in prior versions are obsolete. Latest research (May 13-14) integrated.
>
> **Current state**: Phase 0 ~70% complete. Paper OAuth registered with consumer key `CALYPSOPP` on 2026-05-14 (activation pending ~Sunday 2026-05-17). Variants A/B/C still running Saxo dry-run on the VM — nothing touched. Build of `shared/ib_client.py` (Phase A) starts now during the activation wait.
>
> **Companion docs**:
> - [`IB_OPEN_QUESTIONS_ANSWERED.md`](./IB_OPEN_QUESTIONS_ANSWERED.md) — the 6 prior open questions, all answered
> - [`INTERACTIVE_BROKERS_API_REFERENCE.md`](./INTERACTIVE_BROKERS_API_REFERENCE.md) — IB API encyclopedia
> - `research_scratch/01-12*.md` — verbatim agent research chapters (12 files, ~5,500 lines)

---

## Table of contents

1. [Goals + architecture decision (Option 4)](#1-goals--architecture-decision-option-4)
2. [Current status (what's done as of 2026-05-14)](#2-current-status)
3. [Saxo surface inventory — what we actually use](#3-saxo-surface-inventory)
4. [Target architecture — broker abstraction + parallel variants](#4-target-architecture)
5. [Phase A — Build standalone `shared/ib_client.py`](#5-phase-a-build-ib-client)
6. [Phase B — Introduce broker abstraction in HYDRA](#6-phase-b-broker-abstraction)
7. [Phase C — Deploy IB variants in parallel](#7-phase-c-parallel-variants)
8. [Phase D — Cut B/C from Saxo to IB](#8-phase-d-cut-bc-to-ib)
9. [Phase E — Cut A live to IB](#9-phase-e-cut-a-live)
10. [Phase F — Saxo decommission](#10-phase-f-decommission)
11. [Saxo → IB call-site mapping](#11-saxo-ib-call-site-mapping)
12. [Code skeletons (ibind + conidex combo + ws streaming)](#12-code-skeletons)
13. [Risk register](#13-risk-register)
14. [Cutover SOPs](#14-cutover-sops)
15. [Pre-flight checklist (before any live cutover)](#15-pre-flight-checklist)
16. [Post-cutover validation](#16-post-cutover-validation)
17. [Appendix — IBKR error code dictionary](#17-appendix-error-codes)

---

## 1. Goals + architecture decision (Option 4)

### Goals

- **Replace Saxo entirely** with IB for HYDRA (the only live-tradable bot today; other bots are kill-switched).
- **Preserve HYDRA's strategy code** — only the broker-facing adapter changes. Variants A/B/C strategy logic stays untouched.
- **Run Saxo and IB variants in parallel during cutover** — Saxo variants keep running unchanged on their existing systemd services; new IB variants are NEW services with NEW state files. Zero risk to current dry-run during build.
- **Cut commission costs ~55%** (verified — corrected from prior 75% claim).
- **Drop Polygon Options Starter** ($29/mo savings — IB OPRA gives streaming bid/ask + Greeks + OI).
- **Eliminate the weekly Sunday phone tap** by using OAuth 1.0a Web API (not IB Gateway).

### Non-goals (deferred)

- Refactoring HYDRA strategy logic
- Portfolio Margin (requires $110K NLV)
- Migrating MEIC / IronFly / DeltaNeutral / RollingPutDiagonal — they're all kill-switched
- Section 1256 60/40 tax optimization (EU-tax resident; not applicable)

### Architecture decision: Option 4 (hybrid)

We considered four options:

| Option | Description | Verdict |
|---|---|---|
| 1. In-place swap | Replace `shared/saxo_client.py` with `shared/ib_client.py`. All bots cut over together. | ❌ Hard cutover, no rollback during build |
| 2. Whole HYDRA fork | Copy `bots/hydra/` → `bots/hydra_ib/`. ~10K LOC duplicated. | ❌ Maintenance cost too high |
| 3. Broker abstraction (in-place) | `shared/broker/` interface + adapters. Refactor HYDRA to use abstraction. | ⚠️ Good design but eager refactor risks breaking Saxo dry-run |
| **4. Hybrid: standalone-first** | Phase A standalone IB module (zero HYDRA changes) → Phase B broker abstraction → Phase C parallel variants → Phase D-F cutover | ✅ **Chosen** — preserves Saxo during build, no duplication, gradual rollout |

User-confirmed sub-decisions:
- **Full SaxoClient parity** for `shared/ib_client.py` (every method has an equivalent, not MVP)
- **Enforced ABC** for trade-relevant `BrokerInterface` methods (clear contract, fails loudly)
- **Duck-typed** for utility methods (`get_chart_data`, `get_fx_rate`)
- **Fully separate state** for IB variants — `data/variant_a_ibkr/`, separate DBs, separate logs

---

## 2. Current status

### Phase 0 — Prerequisites (in progress)

| Task | Status | Notes |
|---|---|---|
| IBKR Pro account opened | ✅ | Existing IBIE (Ireland) account, Malta-based, EUR-base |
| Account type = Margin (REG-T) | ✅ | Verified via Configure Account Type page |
| Account is IBKR Pro (not Lite) | ✅ | Verified |
| Non-Professional subscriber status | ✅ | Verified |
| IBKR Mobile 2FA enabled (sole method) | ✅ | DSC+ not configured (modern account); optional TOTP backup available |
| TWS Desktop installed | ✅ | One-time smoke-test tool only |
| Paper account creation initiated | ✅ | "Will be created next business day" — expect 2026-05-15 |
| OpenSSL keypairs generated (paper + live) | ✅ | At `~/ibkr-oauth/{paper,live}/`, mode 600 on private files |
| Global gitignore for keys | ✅ | `~/.gitignore_global` covers `*.pem`, `ibkr-oauth/`, `*_access_token*` |
| Paper OAuth registered with IBKR | ✅ | Consumer key `CALYPSOPP`, registered 2026-05-14 |
| Access tokens stored in 1Password | ✅ | Paper entry has access token + secret |
| Activation poller built + verified | ✅ | `~/ibkr-oauth/poll/check.sh paper` — toolchain confirmed via `id: 19030 invalid consumer` (expected pre-activation response) |
| **Paper OAuth activation** | ⏳ | **Pending IBKR weekend reset Sunday 2026-05-17** |
| Live account funding | ⏳ | "Soon" — no specific date |
| Live OAuth registration | ⏳ | Blocked on live funding |
| Market data subscriptions | ⏳ | Blocked on live funding (3 subs: CBOE Streaming Market Indexes, CME S&P Indexes, OPRA Top of Book) |
| TWS smoke test against live data | ⏳ | Blocked on subs activating |

### What's NOT touched (and stays that way through Phase B)

- `shared/saxo_client.py` — untouched
- `bots/hydra/` — entirely untouched (strategy code, configs, state files)
- VM systemd services `hydra`, `hydra_variant_b`, `hydra_variant_c` — untouched
- All HYDRA variants continue running Saxo dry-run on the existing schedule

---

## 3. Saxo surface inventory

The exact subset HYDRA + MEIC parent call against `SaxoClient`, counted via `grep -hE "self.client\.[a-z_]+" bots/`:

### 3.1 Methods called by HYDRA + MEIC (~25 methods)

| Method | Purpose | Hit frequency |
|---|---|---|
| `authenticate(force_refresh=False)` | OAuth refresh-token exchange | Once at startup, on token expiry |
| `client_key` (property) | Account identifier | Per order |
| `get_account_info()` | Account metadata | Startup |
| `get_balance()` | Live margin / available BP | Every entry decision + per-tick monitoring |
| `get_quote(uic, asset_type)` | Single instrument quote | SPX + VIX every monitor tick |
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

### 3.2 Methods to delete (no IB equivalent needed)

| Saxo method | Why not needed on IB |
|---|---|
| `_oauth_authorization_flow`, `_exchange_code_for_token`, `_refresh_access_token` | OAuth 1.0a handshake is in `ibind` |
| `_upgrade_session_for_realtime_data`, `_ensure_session_capabilities` | IB has no session-tier upgrade |
| `signal_session_downgrade` | No equivalent |
| `check_order_filled_by_activity` | IB orders are broker-side authoritative; no race |
| Binary WS message decoder (`_decode_binary_ws_message`, etc.) | `ibind`'s `IbkrWsClient` parses JSON natively |

---

## 4. Target architecture

### 4.1 Three layers

```
┌──────────────────────────────────────────────────────────────┐
│  HYDRA strategy code (bots/hydra/, unchanged)                │
│                                                                │
│  Today:    self.saxo_client.place_order(...)                  │
│  Phase B:  self.broker.place_iron_condor(...)                 │
└────────────────────────┬─────────────────────────────────────┘
                         │
        ┌────────────────┴────────────────┐
        │                                  │
        ▼                                  ▼
┌──────────────────┐              ┌──────────────────┐
│ shared/broker/   │              │ shared/broker/   │
│ saxo_adapter.py  │              │ ibkr_adapter.py  │
│ wraps SaxoClient │              │ wraps IbkrClient │
└────────┬─────────┘              └────────┬─────────┘
         │                                  │
         ▼                                  ▼
┌──────────────────┐              ┌──────────────────┐
│ shared/          │              │ shared/          │
│ saxo_client.py   │              │ ib_client.py     │
│ (unchanged,      │              │ (NEW — Phase A)  │
│  5152 lines)     │              │  wraps ibind     │
└────────┬─────────┘              └────────┬─────────┘
         │                                  │
         │ HTTPS REST + WS                  │ HTTPS REST + WS
         │ OAuth 2.0 refresh tokens         │ OAuth 1.0a
         ▼                                  ▼
   Saxo OpenAPI                       api.ibkr.com (CP API)
   (US-East GCP egress)               (direct, no Gateway)
```

### 4.2 Module layout (post-Phase B)

```
shared/
  saxo_client.py              ← unchanged
  ib_client.py                ← NEW (Phase A): wraps ibind, full Saxo parity
  broker/
    __init__.py               ← factory: build_broker(config) → BrokerInterface
    interface.py              ← enforced ABC for trade methods, duck-typed for utility
    saxo_adapter.py           ← thin wrapper exposing SaxoClient via BrokerInterface
    ibkr_adapter.py           ← thin wrapper exposing IbkrClient via BrokerInterface
deploy/
  hydra.service               ← unchanged (variant A, Saxo)
  hydra_variant_b.service     ← unchanged (variant B, Saxo)
  hydra_variant_c.service     ← unchanged (variant C, Saxo)
  hydra_variant_a_ibkr.service  ← NEW (Phase C): variant A, IB
  hydra_variant_b_ibkr.service  ← NEW (Phase C): variant B, IB
  hydra_variant_c_ibkr.service  ← NEW (Phase C): variant C, IB
bots/hydra/config/
  config_variant_b.json       ← unchanged (Saxo)
  config_variant_c.json       ← unchanged (Saxo)
  config_variant_a_ibkr.json  ← NEW (Phase C): {"broker": "ibkr", ...}
  config_variant_b_ibkr.json  ← NEW (Phase C)
  config_variant_c_ibkr.json  ← NEW (Phase C)
data/
  variant_b/                  ← unchanged (Saxo state)
  variant_c/                  ← unchanged
  variant_a_ibkr/             ← NEW (Phase C): separate state, separate DB
  variant_b_ibkr/             ← NEW
  variant_c_ibkr/             ← NEW
```

### 4.3 Per-variant config gains one key

```json
{
  "broker": "ibkr",                          // NEW; defaults to "saxo" if missing
  "ibkr": {
    "oauth": {
      "consumer_key": "CALYPSOPP",
      "access_token_secret_name": "ibkr-paper-oauth",  // GCP Secret Manager
      "encryption_key_path": "/opt/calypso/secrets/paper/private_encryption.pem",
      "signature_key_path": "/opt/calypso/secrets/paper/private_signature.pem",
      "dh_param_path": "/opt/calypso/secrets/paper/dhparam.pem"
    }
  },
  "strategy": { ... }                        // unchanged
}
```

Existing Saxo variant configs need NO modification — they're treated as `broker: "saxo"` by default. Adding the explicit `"broker": "saxo"` key is optional but recommended for clarity.

### 4.4 BrokerInterface (Phase B sketch)

```python
# shared/broker/interface.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import Optional, Protocol

@dataclass
class QuoteSnapshot:
    bid: Optional[float]; ask: Optional[float]; last: Optional[float]
    mid: Optional[float]; timestamp: str; currency: str = "USD"

@dataclass
class OrderResult:
    order_id: str; status: str  # "Submitted", "Filled", "Cancelled", "Rejected"
    filled_qty: int = 0; avg_fill_price: Optional[float] = None
    reject_reason: Optional[str] = None

@dataclass
class IronCondorRequest:
    expiry: date
    short_call_strike: float; long_call_strike: float
    short_put_strike: float;  long_put_strike: float
    contracts: int; net_credit_limit: float
    timeout_seconds: int = 60
    non_guaranteed: bool = True  # entry: True; stop-out close: False

class BrokerInterface(ABC):
    """Trade-relevant methods — must be implemented by every broker adapter."""

    @abstractmethod
    async def connect(self) -> bool: ...
    @abstractmethod
    def is_connected(self) -> bool: ...
    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    async def get_quote(self, symbol: str, asset_type: str = "option") -> Optional[QuoteSnapshot]: ...
    @abstractmethod
    async def get_account_summary(self, currency: str = "USD") -> dict: ...
    @abstractmethod
    async def what_if_order(self, request: IronCondorRequest) -> dict: ...

    @abstractmethod
    async def place_iron_condor(self, request: IronCondorRequest) -> OrderResult: ...
    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool: ...
    @abstractmethod
    async def get_open_orders(self) -> list[OrderResult]: ...
    @abstractmethod
    async def get_positions(self) -> list[dict]: ...


class StreamingBroker(Protocol):
    """Duck-typed methods for streaming — not all brokers implement all variants."""
    def subscribe_quote(self, symbol: str) -> None: ...
    def unsubscribe_quote(self, symbol: str) -> None: ...
    def is_stream_healthy(self) -> bool: ...
```

---

## 5. Phase A — Build standalone `shared/ib_client.py`

**Window**: NOW through paper OAuth activation (~2026-05-17 to 2026-05-21).
**Risk**: zero. Doesn't touch HYDRA. No deploys.

### 5.1 Tasks (in order)

#### A.1 Fork `ibind` locally + swap `pyCrypto` → `pycryptodome` (~30 min)

`ibind`'s OAuth 1.0a code uses `pyCrypto` (unmaintained, known CVEs). IBKR provided that code; IBKR hasn't updated it. **Hard requirement before any live use**.

```
git clone https://github.com/Voyz/ibind.git ~/code/ibind
cd ~/code/ibind
# Inspect ibind/oauth/oauth1a.py imports
# Replace `from Crypto.X import Y` → `from Cryptodome.X import Y`
# pycryptodome is API-compatible with pyCrypto
# Run ibind's own test suite to verify no regression
pip install -e .  # install fork in editable mode into the CALYPSO venv
```

Track the swap as a single commit in our fork. If upstream `ibind` ever switches to `cryptography` natively, we drop the fork.

#### A.2 Set up `shared/ib_client.py` scaffold (~1 day)

```python
# shared/ib_client.py
"""IB adapter for CALYPSO — Phase A standalone module.

Wraps Voyz/ibind 0.1.23+ (forked locally to swap pyCrypto→pycryptodome).
Provides the same public surface as SaxoClient where applicable so that
the Phase B broker abstraction can wrap either one transparently.

Not imported by HYDRA yet (Phase A is standalone). Phase B introduces
the broker abstraction and wires HYDRA through it.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from ibind import IbkrClient, IbkrWsClient, OrderRequest
from ibind.oauth.oauth1a import OAuth1aConfig

logger = logging.getLogger(__name__)


@dataclass
class IBConfig:
    """Loaded from secret manager + config file."""
    consumer_key: str
    access_token: str
    access_token_secret: str
    dh_prime: str  # hex string extracted from dhparam.pem
    encryption_key_path: Path
    signature_key_path: Path
    account_id: Optional[str] = None  # discovered via managedAccounts if None
    tickle_interval_seconds: int = 60


class IBClient:
    """Top-level IBKR adapter — wraps ibind for OAuth 1.0a Web API."""

    def __init__(self, config: IBConfig):
        self.cfg = config
        self._client: Optional[IbkrClient] = None
        self._ws: Optional[IbkrWsClient] = None
        # Account state caches updated by WebSocket
        self._smd_subscriptions: dict[int, dict] = {}   # conid → last tick
        self._conid_cache: dict[tuple, int] = {}        # (symbol, expiry, strike, right) → conid
        # ... (full method set per §11 mapping)
```

#### A.3 Implement read-only methods (~2 days)

In order of HYDRA usage frequency:
1. `connect()`, `disconnect()`, `is_connected()`, `_tickle_loop()`
2. `_brokerage_session_init()` — calls `/iserver/auth/ssodh/init`, retries on competing
3. `get_account_summary()` + currency-aware USD-tradable computation (2-step: portfolio_summary + get_ledger)
4. `get_quote(symbol)` — REST snapshot fallback
5. `get_quotes_batch(symbols)` — REST snapshot, max 100 conids per call
6. `get_vix_price()` — convenience wrapper for VIX index
7. `get_option_chain(expiry, trading_class='SPXW')` — `secdef/search` → `secdef/strikes` → `secdef/info`
8. `get_option_greeks(conid)` — REST snapshot with fields 7308-7311, 7633
9. `get_positions()` — paginated `portfolio/{account}/positions`
10. `get_open_orders()` — `iserver/account/orders`
11. `get_order_status(order_id)` — filter from open orders
12. `get_chart_data(symbol, duration, bar_size)` — `iserver/marketdata/history`
13. `get_fx_rate(from_ccy, to_ccy)` — read from ledger or quote

#### A.4 Implement write methods (~2 days)

1. `place_iron_condor(request)` — **the centerpiece**. Constructs the `conidex` string per the agent 9 finding:

```python
async def place_iron_condor(
    self, expiry: date,
    short_call_strike: float, long_call_strike: float,
    short_put_strike: float, long_put_strike: float,
    contracts: int, net_credit_limit: float,
    non_guaranteed: bool = True,
) -> OrderResult:
    # 1. Resolve conids for the 4 legs (cached after first call)
    sc = await self._resolve_conid("SPX", expiry, short_call_strike, "C")
    lc = await self._resolve_conid("SPX", expiry, long_call_strike, "C")
    sp = await self._resolve_conid("SPX", expiry, short_put_strike, "P")
    lp = await self._resolve_conid("SPX", expiry, long_put_strike, "P")

    # 2. Build conidex string. Format: "{spread_template_conid};;;{leg1_conid}/{ratio},..."
    #    28812380 = IBKR's USD spread template conid (universal for USD multi-leg combos)
    #    Negative ratio = SELL leg; positive ratio = BUY leg
    conidex = (
        f"28812380;;;{sc}/-1,{lc}/1,{sp}/-1,{lp}/1"
    )

    # 3. Round limit to $0.05 increments (CBOE COB requirement)
    price = round(net_credit_limit * 20) / 20

    # 4. Build order. For SHORT iron condor: side="SELL", positive price = credit received.
    #    Counter-intuitive but IBKR's documented convention.
    order = OrderRequest(
        conid=None,
        conidex=conidex,
        sec_type="BAG",
        side="SELL",
        order_type="LMT",
        price=price,
        quantity=contracts,
        tif="DAY",
        acct_id=self.account_id,
    )

    # 5. Place + handle reply prompts (ibind handle_questions auto-confirms via answers dict)
    result = await self._client.place_order(
        order_request=order,
        answers=DEFAULT_ANSWERS,    # auto-confirm safety prompts
        account_id=self.account_id,
    )
    return self._parse_order_result(result)
```

2. `place_vertical_spread(request)` — same conidex pattern with 2 legs (for one-sided entries + stop-out closes)
3. `place_limit_order_with_timeout(...)` — wrapper: places, polls status, cancels on timeout
4. `cancel_order(order_id)` — `DELETE /iserver/account/{accountId}/order/{orderId}`
5. `place_emergency_order(...)` — market-order fallback for stops
6. `what_if_order(request)` — `POST /iserver/account/{accountId}/orders/whatif` — returns 5 blocks (`amount`, `equity`, `initial`, `maintenance`, `position`) in EUR-base, each with `current`/`change`/`after` keys

#### A.5 WebSocket streaming subscription manager (~1 day)

Critical detail from agent 10 research: **`smd` topics silently auto-terminate after ~15 min**. ibind 0.1.23 does NOT auto-refresh. We must implement a `umd → smd` cycle ourselves.

```python
class StreamingManager:
    """Manages WebSocket subscriptions to market data with auto-refresh.

    Background thread cycles each subscription every ~13 minutes (under the
    15-min auto-termination ceiling) by sending umd+conid then smd+conid.
    Caller code reads from self.snapshots[conid] which is updated on every
    tick.
    """

    REFRESH_INTERVAL_S = 13 * 60   # 13 min — safely under IBKR's 15-min auto-termination

    def __init__(self, ws_client: IbkrWsClient):
        self.ws = ws_client
        self.snapshots: dict[int, dict] = {}      # conid → latest fields
        self._subscriptions: dict[int, list[int]] = {}  # conid → field codes subscribed
        self._refresh_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def subscribe_quote(self, conid: int, fields: list[int] = None):
        """Subscribe to a conid's market data. Auto-refreshes every 13 min."""
        fields = fields or [31, 84, 86, 88, 85, 7635, 7308, 7309, 7310, 7311, 7633]
        # 31=last, 84=bid, 86=ask, 88=bid_size, 85=ask_size,
        # 7635=mark, 7308-7311=delta/gamma/theta/vega, 7633=IV
        self._subscriptions[conid] = fields
        self.ws.send_subscription(f"smd+{conid}", {"fields": [str(f) for f in fields]})

    def unsubscribe_quote(self, conid: int):
        self.ws.send_subscription(f"umd+{conid}", {})
        self._subscriptions.pop(conid, None)
        self.snapshots.pop(conid, None)

    def _refresh_loop(self):
        while not self._stop_event.wait(self.REFRESH_INTERVAL_S):
            for conid, fields in list(self._subscriptions.items()):
                try:
                    self.ws.send_subscription(f"umd+{conid}", {})
                    time.sleep(0.5)
                    self.ws.send_subscription(f"smd+{conid}", {"fields": [str(f) for f in fields]})
                except Exception as exc:
                    logger.warning("smd refresh failed for conid %s: %s", conid, exc)
```

#### A.6 Order-status WebSocket subscription (~half day)

Subscribe `sor` topic. Maintain `self._order_states[order_id] = {status, filled, remaining}`. `get_order_status(order_id)` reads from this cache; doesn't need an HTTP round-trip.

#### A.7 Reconcile on (re)connect (~half day)

```python
async def _reconcile_on_connect(self):
    """Pull open orders + positions + cross-check against state file.

    Critical: IB orders are broker-side persistent. If we crashed mid-order,
    the order is still live. We MUST reconcile, not blindly resubmit.
    """
    open_orders = await self._client.get_open_orders()
    positions = await self.get_positions()
    # Three cases (per migration plan §4.4 spec):
    # 1. Order in broker but not in our state — orphan (log + cancel for safety)
    # 2. Order in our state but not in broker — likely filled or cancelled mid-crash
    # 3. Both — re-attach by order_id
    ...
```

#### A.8 Retry + circuit breaker (~half day)

Per agent 12 finding: ibind retries network errors only (3× linear backoff). 429/5xx is OUR responsibility. Pattern:

- Outer exponential-with-jitter retry on `{429, 500, 502, 503, 504}`
- Per-endpoint-family circuit breakers (`oauth`, `session`, `marketdata`, `orders`, `portfolio`)
- Open on 5 consecutive failures OR ≥50% over 20-req / 60-s window
- Half-open probe every 30s
- 401 handler bypasses breaker, triggers single-flight `_brokerage_session_init()` reinit
- **Never retry order placement** without a client-side order ID dedup (CP API has `cOID` — use it)

#### A.9 Unit tests against mocks (~1 day)

`tests/test_ib_client.py` — mock `IbkrClient` responses, verify our wrappers:
- conidex construction (4 leg orders → correct string format)
- $0.05 increment rounding
- USD-tradable computation for EUR-base
- whatif response parsing
- Order status reconciliation on reconnect
- smd refresh cycle timing

Aim for 80%+ line coverage. No live IBKR calls in this suite.

#### A.10 Integration smoke test on paper (after activation)

Once paper activates (estimated 2026-05-17 to 05-21):
- Connect to paper
- Reconcile (should find 0 positions, 0 orders)
- Subscribe to SPX index quote — verify ticks
- Subscribe to one 0DTE SPX option — verify Greeks
- Place a $0.05 1-contract IC (well OTM, will expire worthless)
- Cancel it
- Whatif a 10-lot IC, verify margin numbers
- Disconnect cleanly

This is the gate to Phase B.

### 5.2 Phase A deliverables

| File | Lines (est.) | Purpose |
|---|---|---|
| `shared/ib_client.py` | ~2000 | Full SaxoClient parity via ibind |
| `shared/ib_streaming.py` | ~300 | StreamingManager with smd refresh |
| `shared/ib_oauth.py` | ~200 | OAuth1aConfig loader, DH prime extractor |
| `shared/ib_secrets.py` | ~150 | GCP Secret Manager integration for tokens + key files |
| `tests/test_ib_client.py` | ~1500 | Unit tests, all mocked |
| `tests/test_ib_streaming.py` | ~400 | Streaming manager tests |
| `tests/integration/test_ib_paper_smoke.py` | ~300 | Live paper integration test |
| **Total** | **~4850** | |

---

## 6. Phase B — Introduce broker abstraction in HYDRA

**Window**: starts when Phase A integration smoke passes (estimated 2026-05-22 to 2026-05-28).

### 6.1 Tasks

#### B.1 Define `BrokerInterface` (~half day)

Per §4.4 sketch above. Two parts:
- `class BrokerInterface(ABC)` — enforced abstract base for trade methods
- `class StreamingBroker(Protocol)` — duck-typed for utility/streaming

#### B.2 Implement `SaxoAdapter(BrokerInterface)` (~1 day)

Wraps existing `SaxoClient`. Provides a transparent `BrokerInterface` over the existing Saxo surface. **Existing Saxo dry-run must continue working unchanged after this.**

#### B.3 Implement `IbkrAdapter(BrokerInterface)` (~half day)

Wraps `IBClient` from Phase A. Should be straightforward since `IBClient` was designed with the interface in mind.

#### B.4 Factory in `shared/broker/__init__.py` (~half day)

```python
def build_broker(config: dict) -> BrokerInterface:
    broker_type = config.get("broker", "saxo")  # default to Saxo for backwards compat
    if broker_type == "saxo":
        return SaxoAdapter(config)
    if broker_type == "ibkr":
        return IbkrAdapter(config)
    raise ValueError(f"Unknown broker: {broker_type}")
```

#### B.5 Refactor HYDRA strategy.py (~2 days, careful work)

Replace `self.saxo_client` → `self.broker` throughout `bots/hydra/strategy.py` and `bots/meic/strategy.py`. Use `build_broker(config)` in `__init__`. Run existing test suite — must stay green.

#### B.6 Verify Saxo dry-run on VM still works (~half day)

Deploy refactored HYDRA to VM. Variants A/B/C should continue exactly as before (no config change → defaults to `broker: "saxo"`). Compare day-N entries pre vs post-refactor — must be byte-identical.

This is the proof that the abstraction was lossless. If anything diverges, roll back and investigate before proceeding.

---

## 7. Phase C — Deploy IB variants in parallel

**Window**: after Phase B Saxo verification (estimated 2026-05-29 onward, gated on live OAuth activation).

### 7.1 Tasks

#### C.1 Create IBKR variant configs (~half day)

`bots/hydra/config/config_variant_{a,b,c}_ibkr.json` — clone the existing variant configs and add the `"broker": "ibkr"` + `"ibkr": {...}` block. Strategy parameters identical to Saxo counterparts.

#### C.2 New systemd unit files (~half day)

`deploy/hydra_variant_{a,b,c}_ibkr.service` — clone existing units, change config path, change data dir, unique clientId. NOT systemd auto-enabled; manually started.

#### C.3 New data directories on VM (~half day)

`data/variant_{a,b,c}_ibkr/` — empty state, separate DB. No risk of polluting Saxo variant state.

#### C.4 Start IBKR paper variants (B and C first)

```
sudo systemctl start hydra_variant_b_ibkr hydra_variant_c_ibkr
```

A_ibkr deferred until live funded. Verify connection, reconciliation, first heartbeats.

### 7.2 Parallel deployment topology

```
After Phase C:

VM systemd services:
  hydra.service                     ← variant A, SAXO live  (unchanged)
  hydra_variant_b.service           ← variant B, SAXO dry   (unchanged)
  hydra_variant_c.service           ← variant C, SAXO dry   (unchanged)
  hydra_variant_a_ibkr.service      ← NEW: variant A, IBKR paper (until live funded)
  hydra_variant_b_ibkr.service      ← NEW: variant B, IBKR paper
  hydra_variant_c_ibkr.service      ← NEW: variant C, IBKR paper

  6 processes total, all independent, all writing to separate data dirs.
  ZERO cross-talk. ZERO risk to existing Saxo variants.
```

### 7.3 Parity comparison

After 5 trading days of parallel running, audit:
- Strike selection: does variant A_ibkr pick the same strikes as variant A on the same chain? (Should be 100% if chain data agrees.)
- GEX cluster detection: same clusters on both sides?
- Entry timing: same slots fire?
- TP / breach / stop decisions: same disposition?

Discrepancies surface DATA differences (e.g., IB's NBBO tighter than Saxo's) more than LOGIC differences (logic is identical — same code, just different broker).

---

## 8. Phase D — Cut B/C from Saxo to IB

**Window**: after 10 trading days of clean Phase C parallel running (~mid-June 2026).

### 8.1 Tasks

1. Stop `hydra_variant_b.service` and `hydra_variant_c.service` (Saxo side)
2. `systemctl disable` both
3. Verify `hydra_variant_b_ibkr.service` and `hydra_variant_c_ibkr.service` continue
4. Variant A remains on Saxo live until Phase E

Rollback: re-enable the Saxo services. Their state files are intact. Resumes within minutes.

---

## 9. Phase E — Cut A live to IB

**Window**: after Phase D + live OAuth activation + live data subs confirmed working (~late June 2026).

### 9.1 Pre-flight (all must pass)

See §15 below — every item in the pre-flight checklist must be green.

### 9.2 Cutover

1. Manual one-day "drill" — run variant A_ibkr in dry-run for one full day; confirm everything works
2. Set `dry_run: false` in `config_variant_a_ibkr.json`
3. Restart `hydra_variant_a_ibkr.service`
4. **MANUAL APPROVAL GATE on first 10 live orders** — Telegram alert, operator confirms each
5. Stop `hydra.service` (Saxo live)
6. Variant A now trading on IB live

### 9.3 Rollback (within first 30 days)

If something breaks: re-enable `hydra.service`. Saxo position state should be empty (we'd have closed everything before cutover) but the systemd unit + auth tokens are intact.

---

## 10. Phase F — Saxo decommission

**Window**: 4 weeks after Phase E with no incidents (~late July 2026).

### 10.1 Tasks

1. Delete `shared/saxo_client.py`
2. Delete `shared/broker/saxo_adapter.py`
3. Delete `hydra.service`, `hydra_variant_b.service`, `hydra_variant_c.service` from `deploy/`
4. Delete `bots/hydra/config/config_variant_{b,c}.json` (old Saxo configs)
5. Delete `data/variant_b/`, `data/variant_c/` archives
6. Remove Saxo OAuth secrets from Secret Manager
7. Close Saxo account (or leave funded as backup broker for one quarter)
8. Update CLAUDE.md broker references

---

## 11. Saxo → IB call-site mapping

Every Saxo public method gets an IB equivalent via `ibind`. Method signatures match where reasonable so HYDRA / MEIC strategy code minimally changes after Phase B refactor.

| Saxo method | `shared/ib_client.py` equivalent | Underlying CP API endpoint / ibind call |
|---|---|---|
| `authenticate()` | `IBClient.connect()` | OAuth 1.0a handshake via ibind (`OAuth1aConfig` init) |
| `client_key` | `IBClient.account_id` | `client.portfolio_accounts()[0]["accountId"]` |
| `get_account_info()` | `IBClient.get_account_info()` | `GET /portfolio/accounts` |
| `get_balance(currency='USD')` | `IBClient.get_balance(currency='USD')` | `GET /portfolio/{acct}/summary` + `GET /portfolio/{acct}/ledger`; compute USD-tradable |
| `get_quote(uic)` | `IBClient.get_quote(symbol)` | `GET /iserver/marketdata/snapshot` (REST) OR WebSocket `smd+{conid}` (cache) |
| `get_quotes_batch(uics)` | `IBClient.get_quotes_batch(contracts)` | `GET /iserver/marketdata/snapshot?conids=...` (max 100/call) |
| `get_vix_price()` | `IBClient.get_vix_price()` | `get_quote("VIX", asset_type="index")` |
| `get_option_chain(root, expiry)` | `IBClient.get_option_chain(symbol, expiry, trading_class='SPXW')` | `secdef/search` → `secdef/strikes` → `secdef/info` chain |
| `get_option_greeks(uic)` | `IBClient.get_option_greeks(conid)` | snapshot with fields 7308 (delta), 7309 (gamma), 7310 (theta), 7311 (vega), 7633 (IV) |
| `get_positions()` | `IBClient.get_positions()` | `GET /portfolio/{acct}/positions/{page}` (paginated) |
| `get_closed_position_price(uic)` | `IBClient.get_closed_position_price(conid)` | `GET /iserver/account/trades` filtered |
| `place_order(...)` | `IBClient.place_order(contract, action, qty, type, limit)` | `POST /iserver/account/{acct}/orders` |
| `place_emergency_order(...)` | `IBClient.place_market_order(...)` | `POST /iserver/account/{acct}/orders` (orderType=MKT) |
| `place_limit_order_with_timeout(...)` | `IBClient.place_limit_with_timeout(...)` | place + poll status + cancel on timeout |
| `cancel_order(order_id)` | `IBClient.cancel_order(order_id)` | `DELETE /iserver/account/{acct}/order/{order_id}` |
| `get_order_status(order_id)` | `IBClient.get_order_status(order_id)` | WebSocket `sor` cache OR `GET /iserver/account/orders` |
| `get_open_orders()` | `IBClient.get_open_orders()` | `GET /iserver/account/orders` |
| `check_order_filled_by_activity(...)` | **DELETE** | No race on CP API — order status is broker-authoritative |
| `get_chart_data(symbol, ...)` | `IBClient.get_chart_data(...)` | `GET /iserver/marketdata/history` |
| `get_fx_rate('USD', 'EUR')` | `IBClient.get_fx_rate('USD', 'EUR')` | From `get_ledger()` `exchangerate` field |
| `start_price_streaming(uics)` | `IBClient.subscribe_quotes(conids)` | WebSocket `smd+{conid}` via StreamingManager |
| `subscribe_to_option(uic)` | `IBClient.subscribe_option(conid)` | Same — WebSocket smd |
| `is_websocket_healthy()` | `IBClient.is_stream_healthy()` | `IbkrWsClient.connected` + last-tick age |
| `is_heartbeat_alive(N)` | `IBClient.last_tick_age()` | StreamingManager's last-received-tick timestamp |
| `stop_price_streaming()` | `IBClient.unsubscribe_all()` | Loop `umd+{conid}` for all subscribed |

### 11.1 NEW IB-only methods (no Saxo equivalent)

| Method | Why we need it |
|---|---|
| `IBClient.place_iron_condor(...)` | Centerpiece — 4-leg combo via `conidex` string |
| `IBClient.place_vertical_spread(...)` | 2-leg spread (one-sided entries OR closing one side) |
| `IBClient.what_if_order(request)` | Pre-trade margin check — replaces our ORDER-004 BP gate with broker-authoritative numbers |
| `IBClient.qualify_contract(...)` | `secdef/info` wrapper; cache conids by (symbol, expiry, strike, right) |
| `IBClient.tickle()` | Background thread to keep CP API session warm (~60s cadence) |

---

## 12. Code skeletons

### 12.1 OAuth 1.0a config loader

```python
# shared/ib_oauth.py
import os, re, subprocess
from pathlib import Path
from ibind.oauth.oauth1a import OAuth1aConfig

def extract_dh_prime(dhparam_path: Path) -> str:
    """Extract DH prime as hex from a dhparam PEM file."""
    result = subprocess.run(
        ["openssl", "dhparam", "-in", str(dhparam_path), "-text"],
        capture_output=True, text=True, check=True,
    )
    match = re.search(r"(?:prime|P):\s*((?:\s*[0-9a-fA-F:]+\s*)+)", result.stdout)
    if not match:
        raise ValueError(f"No DH prime in {dhparam_path}")
    return re.sub(r"[\s:]", "", match.group(1))

def load_oauth_config(env_dir: Path, access_token: str, access_token_secret: str,
                     consumer_key: str) -> OAuth1aConfig:
    """Build ibind's OAuth1aConfig from a credentials directory.

    env_dir contains: private_signature.pem, private_encryption.pem, dhparam.pem
    Secrets (access_token, access_token_secret, consumer_key) come from caller
    (typically GCP Secret Manager via shared/ib_secrets.py).
    """
    return OAuth1aConfig(
        access_token=access_token,
        access_token_secret=access_token_secret,
        consumer_key=consumer_key,
        dh_prime=extract_dh_prime(env_dir / "dhparam.pem"),
        encryption_key_fp=str(env_dir / "private_encryption.pem"),
        signature_key_fp=str(env_dir / "private_signature.pem"),
        init_brokerage_session=True,   # auto-call /iserver/auth/ssodh/init
        maintain_oauth=True,            # auto-tickle
    )
```

### 12.2 Iron condor placement (the centerpiece)

```python
# shared/ib_client.py (excerpt)
from ibind import OrderRequest

SPREAD_TEMPLATE_CONID = 28812380  # IBKR's USD spread template — universal for USD multi-leg

async def place_iron_condor(
    self,
    expiry: date,
    short_call_strike: float, long_call_strike: float,
    short_put_strike: float, long_put_strike: float,
    contracts: int, net_credit_limit: float,
    non_guaranteed: bool = True,
) -> OrderResult:
    """Place a 4-leg SPX iron condor as a single net-credit limit combo.

    For SHORT IC: side="SELL", positive `price` = credit received.
    (IBKR's counter-intuitive but consistent rule — see
    https://www.ibkrguides.com/traderworkstation/notes-on-combination-orders.htm)

    non_guaranteed=True → entry (legging risk OK for fill probability)
    non_guaranteed=False → stop-out close (atomic; do NOT leave us naked)
    Atomic-fill enforcement on CP API: no direct flag — we monitor via WebSocket
    `sor` topic and place per-leg market-order fallbacks if 1-3 legs fill but the
    spread doesn't complete within N seconds. See §A.5 / `StreamingManager`.
    """
    # 1. Resolve conids (cached by qualify_contract)
    sc = await self.qualify_contract("SPX", expiry, short_call_strike, "C", trading_class="SPXW")
    lc = await self.qualify_contract("SPX", expiry, long_call_strike,  "C", trading_class="SPXW")
    sp = await self.qualify_contract("SPX", expiry, short_put_strike,  "P", trading_class="SPXW")
    lp = await self.qualify_contract("SPX", expiry, long_put_strike,   "P", trading_class="SPXW")

    # 2. Build conidex. Negative ratio = SELL leg; positive = BUY leg.
    conidex = (
        f"{SPREAD_TEMPLATE_CONID};;;"
        f"{sc}/-1,{lc}/1,{sp}/-1,{lp}/1"
    )

    # 3. Round to $0.05 (CBOE COB requirement; carries over from TWS path)
    price = round(net_credit_limit * 20) / 20

    # 4. Build order
    order = OrderRequest(
        conid=None,
        conidex=conidex,
        sec_type="BAG",
        side="SELL",      # SHORT IC = SELL the combo
        order_type="LMT",
        price=price,      # POSITIVE for credit received (counter-intuitive — see docstring)
        quantity=contracts,
        tif="DAY",
        acct_id=self.account_id,
    )

    # 5. Submit + handle ibind reply prompts automatically
    result = await self._client.place_order(
        order_request=order,
        answers=DEFAULT_ANSWERS,
        account_id=self.account_id,
    )
    return self._parse_order_result(result)
```

### 12.3 USD-tradable for EUR-base account

```python
async def get_balance(self, currency: str = "USD") -> dict:
    """Returns live tradable amount in `currency`, plus diagnostics.

    For EUR-base + USD-trade: computes `eur_avail / eur_per_usd + usd_cash`
    from the ledger. CP API has NO 3-minute throttle (unlike TWS), so we can
    poll at 1Hz — but bot should still reserve margin client-side after each
    order placement since the underlying risk engine updates at ~3s.
    """
    summary = await self._client.portfolio_summary(self.account_id)
    ledger = await self._client.get_ledger(self.account_id)

    base_currency = summary.get("availablefunds", {}).get("currency", "EUR")
    base_available = float(summary["availablefunds"]["amount"])

    if currency == base_currency:
        return {"tradable": base_available, "currency": currency, "base_currency": base_currency}

    # Cross-currency view
    currency_row = ledger.get(currency, {})
    exchange_rate = float(currency_row.get("exchangerate", 0))
    cash_balance = float(currency_row.get("cashbalance", 0))

    # NB: exchange-rate direction (base-per-quote vs quote-per-base) needs
    # empirical verification on first live call — IBKR docs are ambiguous.
    # See research_scratch/11_cpapi_margin_account.md for the test plan.
    tradable_in_target = base_available / exchange_rate + cash_balance if exchange_rate > 0 else cash_balance

    return {
        "tradable": tradable_in_target,
        "currency": currency,
        "base_currency": base_currency,
        "base_available": base_available,
        "exchange_rate": exchange_rate,
        "cash_in_target": cash_balance,
    }
```

### 12.4 WebSocket subscription with auto-refresh

See §A.5 above — full StreamingManager class.

---

## 13. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **OAuth 1.0a activation takes > 2 weeks** | Medium | Medium (delays Phase A integration test) | Built 2 weeks of slack into Phase 0; email IBKR API support at day 14 |
| **IBKR closes OAuth 1.0a retail self-service** | Low | High (forces re-architecture to Gateway path) | We're already registered (CALYPSOPP). Keep Gateway+IBC fallback plan in `research_scratch/04_*.md`. |
| **pyCrypto CVE in ibind** | Medium | High (auth-layer RCE) | **Pre-go-live hard requirement**: fork ibind, swap to pycryptodome. Tracked as Phase A.1 |
| **`smd` topic auto-termination at 15 min** | High (it happens every day) | Medium (data starvation if not refreshed) | StreamingManager rotates `umd → smd` every 13 min (Phase A.5) |
| **conidex sign convention mistake** | Medium during dev | High (wrong side, wrong direction) | Reverse-engineer from ibind examples; unit-test conidex builder explicitly; test all 4 directions on paper before live |
| **CP API "reply prompt" not handled** | Low (ibind handles it) | Medium (orders silently rejected) | Use ibind's `answers` parameter with sensible defaults; log every reply prompt seen for monitoring |
| **CP API rate limit hit (429)** | Low at our cadence | Medium | Outer exponential-with-jitter retry; per-endpoint circuit breakers (Phase A.8) |
| **Brokerage session dies (6-min idle)** | Low (Tickler runs 60s) | Medium | ibind Tickler thread; 401 handler reinits via single-flight `/iserver/auth/ssodh/init` |
| **Combo partial fill on stop-out close** | Medium (CP API has no atomic-fill flag) | High (left naked short on one side) | Monitor `sor` topic; if 1-3 legs fill but spread incomplete within 10s, fire per-leg market closes on remaining |
| **whatif response in wrong currency** | Low | Medium (mis-sized orders) | Empirically verify on first paper trade; validate against `get_balance()` numbers |
| **Account summary cadence assumption (3-min from TWS) carried over to CP API** | Medium (will hit this if we copy-paste TWS logic) | Medium | CP API has NO 3-min throttle. Documented explicitly in §12.3 |
| **ibind 0.1.23 has known shortcomings** | Confirmed (research_scratch/12) | Medium | Wrap with our own retry + circuit breaker. Watch for ibind 0.2.x stable release (currently 0.2.1rc9 on PyPI). |
| **Sunday phone tap missed** | n/a | n/a | Eliminated by OAuth 1.0a path |
| **Pre-trade margin numbers wrong for EUR-base** | Medium during dev | High (over-leverage) | Use `whatif` as authoritative; cross-check against `get_balance("USD")`; pad by 5% buffer on first-2-week live trading |
| **Activation poller returns false positive** | Medium per agent 12 | High (we'd start coding before real activation) | Fixed in Phase A.0 — upgrade poller to 3-step check (LST → ssodh/init → auth/status) |

---

## 14. Cutover SOPs

### 14.1 No more weekly Sunday phone tap

With OAuth 1.0a, the SDK auto-rotates the 24h live session token every day via cryptographic handshake. No human in the loop.

The only operator touch points:
- **One-time**: OAuth registration in the IBKR portal (done for paper, pending for live)
- **Periodic**: 12-month self-imposed key rotation via Message Center ticket (per §IB_OPEN_QUESTIONS_ANSWERED.md Q2)
- **Exception**: if IBKR forces re-auth for any reason — alerts trigger Telegram, operator handles within business hours

### 14.2 Daily session lifecycle

```
00:00 ET  — Live session token expires
00:00 ET  — ibind detects expiry, performs OAuth 1.0a handshake
00:00 ET  — New live session token issued (~5s)
00:00 ET  — ibind calls /iserver/auth/ssodh/init to reopen brokerage session
00:00 ET  — Tickler resumes (every 60s)
24h cycle — no human touch
```

### 14.3 Disconnect recovery

```python
# Pseudocode for what shared/ib_client.py implements
async def _on_disconnect():
    logger.warning("CP API disconnected — attempting reconnect")
    backoff = 1.0
    while True:
        try:
            await self.connect()
            await self._reconcile_on_connect()
            await self._resubscribe_all_market_data()
            logger.info("Reconnected + reconciled")
            return
        except Exception as exc:
            logger.error("Reconnect failed: %s — backoff %.1fs", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
```

---

## 15. Pre-flight checklist

**Do NOT cut over variant A to live until ALL of these are checked:**

### IBKR account & data
- [ ] IBKR Pro live account funded ($50K+)
- [ ] Paper account active with OAuth 1.0a credentials (consumer key `CALYPSOPP`)
- [ ] Live OAuth 1.0a credentials registered + activated (different consumer key from paper)
- [ ] Both OAuth credentials in 1Password + GCP Secret Manager (2 backup copies)
- [ ] Market data subs: CBOE Streaming Market Indexes (VIX), CME S&P Indexes (SPX), OPRA Top of Book — all active, billing visible
- [ ] Real-time SPX + VIX + SPXW 0DTE chain confirmed in TWS desktop

### Code & tests
- [ ] `shared/ib_client.py` complete (Phase A)
- [ ] `shared/broker/{interface,saxo_adapter,ibkr_adapter}.py` complete (Phase B)
- [ ] `tests/test_ib_client.py` ≥ 80% coverage, all passing
- [ ] `tests/test_ib_streaming.py` covers smd refresh cycle, passing
- [ ] Existing HYDRA test suite still passing after Phase B refactor
- [ ] Integration smoke against paper IB: connect, reconcile, subscribe, place 1c IC, cancel, whatif, disconnect — all pass
- [ ] Phase C variants ran on paper IB for ≥ 10 trading days, zero incidents
- [ ] ibind forked + pyCrypto → pycryptodome swapped
- [ ] Reconciliation tested by killing bot mid-order, verified clean recovery

### Strategy & risk
- [ ] Per-IC margin via `whatif` matches our pre-trade BP gate within 5%
- [ ] Commission costs recomputed for IBKR Pro tiered structure
- [ ] `min_pnl_per_ic` / `max_pnl_per_ic` sanity bounds re-tuned for IB pricing
- [ ] Dry-run mode tested on IB paper for ≥ 3 days
- [ ] Manual approval gate enabled for first 10 live entries
- [ ] Combo partial-fill fallback (per-leg market closes) tested

### Operations
- [ ] WATCHMAN audit protocol updated to check IB-side health
- [ ] Dashboard shows IB session status, live session token age, last `sor` tick
- [ ] Telegram alerts wired for: OAuth re-auth required, reply prompt unanswered, order rejected
- [ ] On-call runbook documented for: combo partial fill, OAuth handshake fail, IBKR weekly maintenance window
- [ ] Saxo account still funded as rollback safety net
- [ ] GCP Secret Manager has all IBKR secrets (access tokens, key files mirrored)

---

## 16. Post-cutover validation

### Day 1
- [ ] All 3 IBKR variants connected, reconciled
- [ ] SPX + VIX quotes streaming, last-tick age < 30s
- [ ] Entry slots fire on schedule
- [ ] First IC placed cleanly (manual approval)
- [ ] First TP / stop fires correctly
- [ ] EOD reconciliation passes

### Week 1
- [ ] Daily P&L matches expectation (within slippage tolerance)
- [ ] No `smd` refresh failures (StreamingManager rotates as expected)
- [ ] No 401 / re-auth events
- [ ] Dashboard shows accurate live positions
- [ ] "BRANDON-GEX-ADJ SKIP" rate matches Saxo baseline (rules out chain-data drift)

### Month 1
- [ ] Commission savings measured (~55% reduction expected)
- [ ] Fill quality measured (mid-price slippage vs Saxo)
- [ ] Polygon Options Starter unsubscribed → $29/mo saved
- [ ] CP API uptime > 99.5%
- [ ] All operator-friction items documented in runbook

### Quarter 1
- [ ] Saxo account closed (or archived)
- [ ] `shared/saxo_client.py` deleted
- [ ] CLAUDE.md broker references updated
- [ ] Migration retrospective written

---

## 17. Appendix — IBKR error code dictionary

| Code | Meaning | Recoverable? | Common cause |
|---|---|---|---|
| `19030` | Invalid consumer | ⏳ wait | OAuth credential registered but not activated; clears on weekly server reset |
| `200` | Order error / contract ambiguous | Sometimes | Ambiguous symbol resolution; specify exchange + tradingClass |
| `201` | Order rejected | Sometimes | Margin, trading permissions, RTH restriction |
| `202` | Order cancelled | Yes | Normal cancellation |
| `502` | Couldn't connect to TWS | No | n/a on CP API path |
| `504` | Not connected | No | Brokerage session expired; reinit via `/iserver/auth/ssodh/init` |
| `401 Unauthorized` | Pre-activation OR token expired | Sometimes | If credential pre-activation: wait. If token expired: re-handshake. |
| HTTP 429 | Rate limited | Yes | Back off (exponential+jitter); fewer concurrent requests |
| HTTP 500/502/503/504 | IBKR server error | Yes | Retry with backoff |

---

## Appendix B — Decision log

| Decision | Date | Rationale |
|---|---|---|
| Option 4 hybrid architecture (standalone → abstraction → parallel) | 2026-05-14 | Preserves Saxo dry-run during build; no code duplication; gradual rollout |
| Full SaxoClient parity for ib_client.py | 2026-05-14 | Avoids missing-method surprises during Phase B integration |
| Enforced ABC for trade methods, duck-typed for utility | 2026-05-14 | Clear contract where it matters; flexibility where it doesn't |
| Fully separate state for IB variants | 2026-05-14 | Zero cross-contamination; easy comparison |
| OAuth 1.0a + ibind (not Gateway + IBC) | 2026-05-13 | Eliminates Sunday phone tap, Docker process, etc. |
| ibind 0.1.23 over 0.2.1rc | 2026-05-13 | Stable release; 0.2.1 still RC |
| Fork ibind + swap pyCrypto → pycryptodome | 2026-05-13 | Pre-go-live hard requirement |
| `tradingClass='SPXW'` for 0DTE | 2026-05-13 | Required for PM-settled weeklies |
| Drop Polygon Options Starter | 2026-05-13 | IB OPRA gives streaming bid/ask + Greeks + OI in one feed |
| IBKR Pro mandatory (not Lite) | 2026-05-13 | Lite ineligible for Web API trading endpoint |
| `28812380` USD spread template conid for combos | 2026-05-14 | Documented IBKR convention for USD multi-leg combos |
| Keep Python-side credit-based stops | 2026-05-13 | Native IB stop orders unreliable on illiquid options |

---

**Last updated**: 2026-05-14. Major rewrite (Option 4 + CP API specifics).
