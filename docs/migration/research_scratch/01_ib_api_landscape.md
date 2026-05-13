# Interactive Brokers API Landscape — Migration Reference

**Audience:** CALYPSO migration team (Saxo Bank OpenAPI → Interactive Brokers).
**Use case anchor:** 0DTE SPX iron-condor automated trader running as a Python
systemd service on a GCE Linux VM (24/7 production), server-side auth, multi-leg
option order placement, real-time WebSocket price streaming.
**Date of research:** 13 May 2026.
**Author:** Foundation reference doc — every later migration note assumes the
facts in this file.

> Sourcing note. IBKR's `interactivebrokers.com` and `ibkrcampus.com` domains
> block direct WebFetch (HTTP 403). All quoted facts below were obtained via
> WebSearch result summaries that reference those pages, plus direct WebFetch
> of GitHub / PyPI mirrors. Where a claim could not be confirmed from a
> primary source, it is flagged with **`[unconfirmed]`**. URLs in the
> citations point to the authoritative IBKR page even when the body of that
> page was fetched indirectly — you should re-verify any number you depend on
> for sizing / billing.

---

## 1. API options overview

IBKR exposes five publicly-documented programmatic surfaces. The picture as of
May 2026 is shaped by an in-progress consolidation: IBKR is folding the
Client Portal Web API, Digital Account Management, and Flex Web Service into
one *Web API* umbrella with shared OAuth 2.0 auth, while the underlying
endpoints continue to work under the legacy names. ([IBKR Campus —
Introduction](https://www.interactivebrokers.com/campus/ibkr-api-page/getting-started/),
[IBKR Campus — Web API Documentation](https://www.interactivebrokers.com/campus/ibkr-api-page/webapi-doc/))

| API surface | What it is | Target user | Transport | Language SDKs | Desktop-app required? | Status (May 2026) |
|---|---|---|---|---|---|---|
| **TWS API** | Asynchronous TCP socket protocol speaking to a running TWS desktop or IB Gateway process. The flagship API; full feature surface. | Retail + active developers running a workstation or a headless Gateway. | TCP socket (binary) | Official: Python (`ibapi`), Java, C++, C#/.NET, ActiveX, DDE/Excel. Community: `ib_async` (Python async), node-ib (TS). | **Yes** — TWS *or* IB Gateway must run somewhere reachable on the network. | **Active**, primary API. Latest 10.46 (released 22 Apr 2026), prior stable 10.45 (30 Mar 2026). |
| **Client Portal Web API (CP API / "cpapi v1")** | REST + WebSocket API exposed through a local `clientportal.gw` Java gateway that proxies to IBKR cloud. | Web app integrations and developers who don't want a TCP socket but can still run the local Java gateway. | HTTPS REST + WebSocket (`wss://localhost:5000/v1/api/ws`) | Any HTTP client. No first-party SDK; community SDKs include `ibind` (Voyz), `ibeam` (auth keeper), Elixir, Go, JS clients. | **Yes** — `clientportal.gw` Java process must run locally and be authenticated. | **Active**, will continue to receive updates; being unified under the new Web API umbrella but not deprecated. |
| **IBKR Web API (unified, OAuth 2.0)** | Cloud-hosted REST that does **not** require a local gateway when First/Third-Party OAuth 2.0 is provisioned. Calls IBKR's servers directly. | First-Party OAuth 2.0 (own-account access) and Third-Party OAuth 1.0a (vendor access on behalf of customers). Trading endpoint requires **IBKR Pro** + funded live account. | HTTPS REST (cloud-hosted) | None official; any HTTP client + JWT signer. | **No** for OAuth 2.0 First-Party (this is the headless-friendly path). The Web API umbrella also covers retail clients via the CP Gateway path. | **Beta + rolling GA.** OAuth 2.0 documentation marked "beta and subject to change" through 2026; OAuth 1.0a path is GA for third-party vendors. |
| **Flex Web Service** | Statement / activity reports as XML over REST (no live trading). Now part of the unified Web API. | Compliance, reconciliation, end-of-day reporting. | HTTPS REST (XML) | Any HTTP client. | No. | Active. |
| **FIX / CTCI** | Industry-standard FIX 4.1/4.2 (plus selected 4.3/4.4 + custom tags) over a dedicated link or IB Gateway. | Institutional / enterprise order routing. | FIX (TCP) over VPN/extranet/leased-line/cross-connect, or FIX-over-internet via IB Gateway for non-institutional. | Any FIX engine (QuickFIX, etc.). | IB Gateway only for the non-institutional FIX-over-internet path. | Active; institutional onboarding via `fixengineering@ibkr.com`. ([IBKR Campus — FIX](https://www.interactivebrokers.com/campus/ibkr-api-page/fix/), [FIX CTCI glossary](https://www.interactivebrokers.com/campus/glossary-terms/fix-ctci/)) |

Sources: [IBKR Trading API Solutions](https://www.interactivebrokers.com/en/trading/ib-api.php),
[IBKR API Home — IBKR Campus](https://www.interactivebrokers.com/campus/ibkr-api-page/ibkr-api-home/),
[Web API Documentation](https://www.interactivebrokers.com/campus/ibkr-api-page/webapi-doc/),
[Web API v1.0 Documentation](https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/).

For a 24/7 headless Python systemd service the choice collapses to **TWS API
via IB Gateway** or **IBKR Web API (OAuth 2.0)**. Everything in §2 / §3 / §4
expands those two; §5 dives into auth; §6 picks one.

---

## 2. TWS API / IB Gateway

### 2.1 Latest versions (May 2026)

Two version channels are published at
[interactivebrokers.github.io](https://interactivebrokers.github.io/):

| Channel | Version | Released | Notes |
|---|---|---|---|
| **Latest** | 10.46 | 22 Apr 2026 | Adds Python API support over the Stable feature set. |
| **Stable** | 10.45 | 30 Mar 2026 | Java, C++, C#/.NET, ActiveX, DDE. |

IBKR's compatibility note: *"Recommended TWS or IB Gateway version: 1045 or
higher (for comprehensive feature support)."* The next TWS+API release
([TWS API Changelog](https://www.interactivebrokers.com/campus/ibkr-api-page/tws-api-changelog-2/),
[TWS API Release Notes](https://ibkrguides.com/releasenotes/tws-api.htm)) is
scheduled 23 Feb 2026 and introduces a breaking change for tick-size: in
versions 10.44+, `Delayed_Last_Size` (tick 71) and `Last_Size` (tick 5) are
returned to `tickSize` as `Decimal` rather than `Integer` — relevant for any
size-arithmetic in CALYPSO's quote handler.

A pair of recent additions worth noting for the iron-condor bot:

- `EClient.reqCurrentTimeInMillis()` → millisecond-precision server clock
  (useful for clock-skew bookkeeping during the SPX 0DTE close window).
- Order/Execution objects now carry a `Submitter` field with the username
  that placed the order — useful for audit on a multi-process deployment.

### 2.2 Python SDK options

There are three names you'll see in the wild. Only two are alive:

| Library | What it is | Latest version | Maintained? | Async? | When to use |
|---|---|---|---|---|---|
| **`ibapi`** (official) | The wire protocol library IBKR ships in the API installer. Callback-based, `EWrapper`/`EClient`. PyPI unofficial mirrors: `ibapi-latest` (10.40.01), `ibapi-stable` (10.37.02 — released 14 Nov 2025). | 10.40.01 (mirror) / 10.45-10.46 (direct download) | ✅ by IBKR | No (threaded) | When you need a thin, official, callback-based client. |
| **`ib_insync`** | Historical async wrapper around `ibapi`. Last release 0.9.86 (Dec 2023). | 0.9.86 | ❌ **Inactive** since the author (Ewald de Wit) passed away in early 2024. | Yes (asyncio) | **Do not use for new work** — abandoned. |
| **`ib_async`** | Direct successor to `ib_insync` under a new org (`ib-api-reloaded`), maintained by Matt Stancliff. Re-implements the IBKR binary protocol internally — `ibapi` is *not* a dependency. | **2.1.0 (8 Dec 2025)** on [PyPI](https://pypi.org/project/ib_async/) (per-page); GitHub release tag 2.0.1 dated Jun 2025 — version drift between sources, treat 2.1.0 as the install target. | ✅ Actively maintained | Yes (asyncio, supports sync mode too) | **Default choice for new Python work** including CALYPSO. Python 3.10–3.14 supported. |

Sources: [ib_async on GitHub](https://github.com/ib-api-reloaded/ib_async),
[ib_async on PyPI](https://pypi.org/project/ib_async/),
[ib_insync on PyPI](https://pypi.org/project/ib-insync/),
[deepentropy/ibapi mirror](https://github.com/deepentropy/ibapi/).

The `ib_async` repo's stated policy is to support Python releases two years
back — CALYPSO's existing Python (3.12) is comfortably inside that window.

### 2.3 IB Gateway vs TWS for headless server deployment

| Aspect | TWS (Trader Workstation) | IB Gateway |
|---|---|---|
| Purpose | Full trader GUI + API listener. | Minimal API listener only, no chart/blotter UI. |
| RAM / CPU | ~1 GB+, heavier. | ~200–400 MB, much lighter. |
| Headless suitability | Possible with Xvfb but wasteful. | **Designed for it** — no charts to render. |
| Auto-restart cadence | IBKR force-restarts daily; weekly cold login. | Same, but lighter to script around. |
| Stability for 24/7 | Acceptable but log-noisy. | Preferred for unattended operation. |

**Recommendation for CALYPSO:** IB Gateway, not TWS.
([Installing & Configuring TWS for the API — IBKR Campus](https://www.interactivebrokers.com/campus/trading-lessons/installing-configuring-tws-for-the-api/))

### 2.4 Port assignments

Standard out-of-box ports (configurable in API settings):

| App | Live trading | Paper trading |
|---|---|---|
| TWS | **7496** | **7497** |
| IB Gateway | **4001** | **4002** |

Both apps can run side-by-side on different ports — useful when you want to
shadow live orders against a paper account in the same process tree.
([gnzsnz/ib-gateway-docker](https://github.com/gnzsnz/ib-gateway-docker),
[forum.amibroker.com](https://forum.amibroker.com/t/port-for-paper-trading-tws-is-now-7497/1020))

### 2.5 Connection lifecycle and reconnection

- The TWS API client connects to `host:port` with a numeric `clientId`. Reusing
  a `clientId` after a dirty disconnect can hang; pick a stable ID per
  service and document it.
- IBKR enforces a daily reset window (auto-logoff around midnight US ET unless
  configured otherwise) and a weekly cold login (`Auto-restart` config in
  Gateway → "Restart" extends to a week max).
- On disconnect, `ib_async`'s `IB.disconnectedEvent` fires; the standard
  recovery pattern is exponential-backoff `IB.connectAsync(host, port,
  clientId)`, then re-subscribe market data and re-fetch open orders to
  reconcile with the live state. Order state is **server-side**; the bot
  must reconcile by `reqOpenOrders()` / `reqExecutions()` after reconnect.

### 2.6 IBC / IBController — auto-login

For headless servers the unattended-login layer is critical. There are two
GitHub projects bearing that name; only one is alive:

| Project | Status | Notes |
|---|---|---|
| **`IbcAlpha/IBC`** | **Active.** Fork that took over after the original IBController maintainer withdrew direct support in early 2018. | Supports IBKR Mobile 2FA via push, weekly auto-restart, headless / xvfb, systemd units. Cannot complete login if IBKR has issued a hardware Digital Security Card+ — that path is fully manual. ([IbcAlpha/IBC](https://github.com/IbcAlpha/IBC), [IBC userguide](https://github.com/IbcAlpha/IBC/blob/master/userguide.md)) |
| `ib-controller/ib-controller` | Older repo; effectively superseded by IbcAlpha/IBC. | Mention only for archaeology. ([ib-controller](https://github.com/ib-controller/ib-controller)) |

**Important constraints carried over from the wild:**

- IBC **does not work with the self-updating TWS** — install the standalone
  TWS/Gateway build.
- Two-factor authentication is the chief friction point. IBC can opt out of
  the daily 2FA when "IB Key Security via IBKR Mobile" is the only enabled
  method (push approval becomes the gate, not a typed code), but a weekly
  re-login still requires user action on the phone. There's no fully
  unattended 2FA path for live trading.
  ([IBKR Two-Factor Auth FAQ](https://ibkrguides.com/securelogin/sls/faq.htm))

For the **Client Portal Web API** flavour of headless, [Voyz/ibeam](https://github.com/Voyz/ibeam)
plays the same role as IBC: it uses Selenium + a virtual display to keep the
`clientportal.gw` authenticated, with a hook for 2FA callbacks. The CP API
session times out after ~6 minutes of inactivity, and `ibeam` keeps it warm
via `/v1/api/tickle`.

---

## 3. Client Portal Web API (CP API)

The CP API is the REST-and-WebSocket alternative to TWS API. It still
requires a local process — but that process is the `clientportal.gw` Java
proxy, not a desktop trading app.

### 3.1 Architecture and gateway

- You download the `clientportal.gw` zip from IBKR, unzip, and run
  `bin/run.sh root/conf.yaml` (Linux/macOS) or `bin\run.bat root\conf.yaml`
  (Windows).
- The gateway opens `https://localhost:5000` (HTTP/HTTPS proxy) and
  `wss://localhost:5000/v1/api/ws` (WebSocket).
- Calls go: your code → `localhost:5000` → gateway → IBKR cloud.
- **The gateway must be authenticated through a browser hit to
  `https://localhost:5000` after launch** unless an auth-keeper like `ibeam`
  injects credentials via Selenium.
- Sessions time out after roughly **6 minutes** without traffic; the
  `/tickle` endpoint must be hit at least every ~5 minutes to keep the
  session alive. ([Launching and Authenticating the Gateway — IBKR Campus](https://www.interactivebrokers.com/campus/trading-lessons/launching-and-authenticating-the-gateway/),
  [Two-Factor wiki — Voyz/ibeam](https://github.com/Voyz/ibeam/wiki/Two-Factor-Authentication))

### 3.2 Auth flow

Out of the box: username + password + 2FA challenge through the gateway's
local login page. With OAuth 1.0a or OAuth 2.0 layered on top of the gateway,
the session can be pre-authenticated server-side, but the gateway still must
run.

For First-Party OAuth, the access token + access-token-secret are issued
through the **Self-Service Portal** at the OAuth panel inside Client Portal
account management. ([OAuth 1.0a Extended — IBKR Campus](https://www.interactivebrokers.com/campus/ibkr-api-page/oauth-1-0a-extended/))

### 3.3 Endpoint inventory

REST endpoints are grouped (see [Web API v1.0 Documentation](https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/)):

| Group | Representative endpoints | Purpose |
|---|---|---|
| **Auth / session** | `/iserver/auth/status`, `/iserver/auth/ssodh/init`, `/iserver/reauthenticate`, `/tickle`, `/logout`, `/sso/validate` | Maintain the browser-side brokerage session. |
| **Account** | `/iserver/accounts`, `/portfolio/accounts`, `/iserver/account/{id}/summary`, `/portfolio/{id}/positions/{page}` | Account discovery, summaries, positions. |
| **Market data** | `/iserver/marketdata/snapshot` (REST snapshots), `/iserver/marketdata/history`, `/iserver/secdef/search`, `/iserver/secdef/info`, plus WebSocket topics `smd+{conid}+{fields}` (subscribe) / `umd+{conid}` (unsubscribe). | Snapshots + streaming top-of-book. Note the **Dec 10 2025** change capping `snapshot` at 100 conids per query and 50 fields. |
| **Contract / search** | `/iserver/contract/{conid}/info`, `/trsrv/secdef`, `/iserver/secdef/strikes`, `/iserver/secdef/info` | Contract resolution; option chain discovery. |
| **Orders** | `/iserver/account/{id}/orders` (place), `/iserver/account/{id}/order/{orderId}` (modify/cancel), `/iserver/account/{id}/orders` (list), `/iserver/account/{id}/orders/whatif` (preview margin), `/iserver/notification`, `/iserver/account/orders/reply/{replyid}` | Order placement including reply-prompt dialog for risk warnings. |
| **Positions / P&L** | `/portfolio/{id}/positions`, `/iserver/account/pnl/partitioned` | Live position + P&L. |
| **Streaming (WebSocket)** | `smd` (market data), `sor` (order status), `spl` (P&L), `sbd` (balances), `sld` (ledger), `sts` (auth status). `sor` gained a `filters` argument on 27 Aug 2025. | Push-style updates; required for sub-second iron-condor monitoring. |

Source: [Web API v1.0 Documentation](https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/),
[Web API Changelog](https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-changelog/),
[Websockets — IBKR Campus](https://www.interactivebrokers.com/campus/trading-lessons/websockets/).

### 3.4 Limitations vs TWS API

- **Snapshot caps**: 100 conids / 50 fields per snapshot query (since Dec
  2025).
- **Per-account rate limit** — undocumented but historically tighter than
  TWS API; large multi-leg fans can throttle.
- **Streaming model** — WebSocket but proxied through the local gateway, so
  network failure in either segment loses ticks.
- **Combo orders** — supported but the schema is less ergonomic than TWS's
  native `ComboLeg` objects.
- **Session expiry** — 6-minute idle, weekly hard re-auth (same as TWS), and
  no first-party way to script around 2FA other than the IBKR Mobile push.
- **Java gateway** — same operational burden as IB Gateway: you have a
  background JVM with auth state that can drift.

### 3.5 Recent changelog highlights (sources: [Web API Changelog](https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-changelog/))

- **6 Jan 2026**: Fundamental data tags Dividend Amount, Dividend Yield %,
  Ex-Date, P/E, Market Cap, EPS, Beta **deprecated** — no longer returned by
  the API.
- **10 Dec 2025**: `/iserver/marketdata/snapshot` capped at 100 conids + 50
  fields.
- **27 Aug 2025**: `sor` WebSocket topic gained `filters` arg for order
  status.

---

## 4. IB Web API (the OAuth-2.0 REST consolidation)

This is the headline change for anyone planning a new integration in 2026.

### 4.1 What it is

IBKR is unifying:

- Client Portal Web API (CP API)
- Digital Account Management
- Flex Web Service

…under a single brand — **IBKR Web API** — with **OAuth 2.0** as the shared
auth scheme. The Web API documentation is explicitly labelled "beta and
subject to change" but the underlying endpoints exist and are callable.
([IBKR Campus — Web API Documentation](https://www.interactivebrokers.com/campus/ibkr-api-page/webapi-doc/),
[Introduction — IBKR Campus](https://www.interactivebrokers.com/campus/ibkr-api-page/getting-started/))

### 4.2 Is there a true OAuth REST surface that doesn't need a gateway?

**Yes — partially.** OAuth 2.0 access via the Web API removes the per-host
`clientportal.gw` requirement for many account-management and Flex-style
calls. **For brokerage / trading endpoints**, however, the docs still route
retail and individual clients through the Client Portal Gateway:

> *"For retail and individual clients, authentication to our WebAPI is
> managed using the Client Portal Gateway, a small Java program used to
> route local web requests with appropriate authentication."*
> — [Getting Started — IBKR Campus](https://www.interactivebrokers.com/campus/ibkr-api-page/getting-started/)

In other words, the cloud-hosted OAuth 2.0 REST surface is generally
available for **account, statement, and Flex** workflows, while **live
trading and live market data for retail accounts still go through the
local CP Gateway** (with OAuth providing the auth, not eliminating the
gateway). Third-party institutional vendors get a fuller cloud OAuth path
under OAuth 1.0a Extended.

For CALYPSO's needs (place option orders + stream market data), this means
the gateway is still in the operational picture as of May 2026 even if
auth is OAuth-based. The headless burden is *reduced* (no Selenium login
needed if OAuth is wired correctly) but not *eliminated*.

### 4.3 OAuth 2.0 auth specifics

- IBKR supports only **`private_key_jwt`** client authentication (RFC 7521 /
  7523). You register a public key with IBKR; your client signs a JWT
  (`client_assertion`) per request. IBKR validates with the registered
  public key. No shared client_secret transits the wire. ([Trading Web API](https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-trading/))
- **First-Party OAuth 2.0** (own-account access) → credentials issued through
  the OAuth Self-Service Portal under Client Portal account management.
- **Third-Party OAuth** → vendor flow, currently primarily under OAuth 1.0a
  Extended for IBKR-approved third-party apps.
- Trading endpoint eligibility: **IBKR Pro** account, fully open + funded.
  Lite is not eligible for trading via the Web API.

### 4.4 Endpoint surface

The trading-relevant subset mirrors the CP API endpoints (because under the
hood it *is* the CP API path), with OAuth 2.0 sitting in front. The new
"Account Management Web API" branch covers programmatic account opening,
funding, and statement retrieval — outside CALYPSO's scope, but useful to
know about.

### 4.5 Status May 2026 (confirmed vs unconfirmed)

| Question | Answer | Source / status |
|---|---|---|
| Does an OAuth 2.0 REST surface exist? | Yes. | Confirmed by IBKR Campus docs. |
| Is it GA or beta? | "Beta and subject to change" per IBKR's own banner. | Confirmed. |
| Can it run on a GCE VM without any local gateway, for **live trading**? | **No** for retail accounts — gateway still required. | Confirmed via IBKR's "retail / individual clients" wording. |
| Can it run gateway-free for **account / Flex** workflows? | Yes. | Confirmed in principle, **[unconfirmed]** in detail for May 2026. |
| Is there a fully GA OAuth-only path for individual retail live trading without `clientportal.gw`? | **[unconfirmed]** — no IBKR doc found in this research confirming such a path exists as of May 2026. | Treat as not available until verified directly with IBKR API support. |

---

## 5. Auth flows in detail

### 5.1 TWS API / IB Gateway

| Step | What happens |
|---|---|
| 1. Username + password into Gateway login UI | Manual or scripted via IBC. |
| 2. 2FA challenge — IBKR Mobile push (default), SMS, or DSC+ card | IBC can auto-approve via IBKR Mobile, but the **push notification must be tapped on the user's physical phone**. SMS/DSC+ break automation. |
| 3. Gateway holds an authenticated brokerage session | Lasts until IBKR's daily/weekly reset. |
| 4. TWS API socket clients connect with `clientId` | No further auth at the socket — it's "anyone on `localhost:4001` can place orders". Lock down the network surface. |
| 5. Daily auto-restart | Configurable; weekly cold re-login is forced — phone tap still required. |

**Cloud deployment implications:**

- Runs on a GCE VM with no desktop session via Xvfb + IBC.
- No interactive OS-level login needed after first install, but the **phone-side
  push approval is required at least weekly** for live accounts. There is no
  IBKR-sanctioned way to bypass this for retail accounts.
- The "no auth on the socket" property means the GCE VM must firewall
  4001/4002 to `127.0.0.1` (or to the bot's process namespace) — never
  expose to 0.0.0.0.

### 5.2 Client Portal Web API (username/password + tickle)

| Step | What happens |
|---|---|
| 1. Start `clientportal.gw` on the VM | Java process on `:5000`. |
| 2. Browser / Selenium hits `https://localhost:5000` | Login form posts to gateway. |
| 3. 2FA — same IBKR Mobile push as TWS path | Tap on phone (or DSC+/SMS). |
| 4. Gateway holds an SSO session | `/iserver/auth/status` reports `authenticated: true`. |
| 5. Keep alive | `/tickle` every ≤ 5 minutes. |
| 6. Weekly hard re-login | Forced; same constraint as TWS. |

`ibeam` automates steps 2–4 with Selenium + a virtual display (`pyvirtualdisplay`)
and exposes a 2FA-callback hook for the phone tap.
([Voyz/ibeam](https://github.com/Voyz/ibeam))

### 5.3 IBKR Web API — OAuth 2.0 (`private_key_jwt`)

| Step | What happens |
|---|---|
| 1. Generate an RSA/ECDSA keypair | Store private key on the VM in Secret Manager. |
| 2. Register public key in OAuth Self-Service Portal | One-time. Bind to an IBKR username. |
| 3. Per session, sign a JWT `client_assertion` | RFC 7523-compliant; short-lived. |
| 4. Exchange at IBKR's token endpoint for an access token | OAuth 2.0 client-credentials-ish flow with JWT auth. |
| 5. Call API with bearer token | Standard OAuth 2.0 `Authorization: Bearer <token>` header. |
| 6. Refresh by re-signing a fresh `client_assertion` | No human in the loop after step 2. |

**This is the only path where re-auth is fully scriptable on a GCE VM with no
phone tap.** The catch — see §4.5 — is that for **retail live trading**, the
gateway is still in front of the trade endpoints. For account/Flex
workflows it's a clean cloud REST call.

Sources: [Trading Web API — IBKR Campus](https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-trading/),
[Account Management Web API](https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-account-management/),
[OAuth 1.0a Extended](https://www.interactivebrokers.com/campus/ibkr-api-page/oauth-1-0a-extended/).

### 5.4 Session timeouts at a glance

| Path | Idle timeout | Hard re-auth |
|---|---|---|
| TWS / IB Gateway | None on the socket; gateway daily auto-restart, weekly cold login. | Weekly. |
| Client Portal Gateway | ~6 minutes without `/tickle`. | Weekly cold login. |
| IBKR Web API OAuth 2.0 | Token lifetime + refresh; no human step. | Public-key rotation per IBKR policy (**[unconfirmed]** cadence). |

---

## 6. Recommendation

**Pick TWS API via IB Gateway, driven by `ib_async` 2.1+, auto-logged-in
with IBC. Plan for OAuth 2.0 Web API as the medium-term migration target.**

### 6.1 Why this combination for CALYPSO today

1. **Multi-leg option orders**: TWS API has the most mature combo-order
   primitives (`Contract` with `secType="BAG"` + `ComboLeg[]`). The CP /
   Web API combo schema works but is less battle-tested for iron condors.
   `ib_async` exposes ergonomic `ComboLeg` builders straight out of the box.
2. **Real-time streaming**: TWS API's `reqMktData` over a TCP socket is the
   lowest-latency path IBKR offers retail. The CP API WebSocket adds an
   extra hop through the local Java gateway, which doesn't matter much for
   1-second cadence but does for 0DTE close-window decisions.
3. **Maturity / community**: `ib_async` has 1,500+ stars, active commits,
   public Discord, and a long-running library lineage (`ib_insync` →
   `ib_async`). The CP API has `ibind` + `ibeam` but the population using it
   end-to-end for combo trading is smaller.
4. **Auth ergonomics for a single-user algo**: IBC + IBKR Mobile push
   handles 99% of the unattended case. The one weekly phone-tap is the
   same friction either way (TWS path *or* CP path).

### 6.2 Why not pure OAuth-2.0 Web API today

- The live-trading endpoint still requires the Client Portal Gateway for
  retail accounts per IBKR's own docs (§4.5). So you'd still operate a Java
  proxy on the VM.
- OAuth 2.0 docs are explicitly "beta and subject to change" — fine for new
  builds but with breaking-change risk on the trading endpoint specifically.
- The Self-Service Portal registration step is non-trivial and the
  individual-retail flow for the trading endpoint is **[unconfirmed]** in
  this research — verify with IBKR API support before committing.

### 6.3 Trade-offs accepted

| Cost | Mitigation |
|---|---|
| Need to run IB Gateway on the VM (200–400 MB RSS). | GCE n2-standard-2 already has headroom. |
| Weekly phone tap for 2FA. | Schedule it; document the runbook. |
| Re-implement Saxo OpenAPI's REST abstractions in `ib_async`'s event-driven model. | One-time migration cost; well-trodden patterns exist. |
| `ibapi` is unofficially mirrored on PyPI (mainline downloads from IBKR ZIP). | `ib_async` removes this dependency entirely — it speaks the wire protocol natively. |

### 6.4 When to revisit

Switch to Web API OAuth 2.0 when **any one** of the following becomes true:

- IBKR ships a documented, GA, gateway-free live-trading endpoint for
  retail OAuth 2.0 (watch the [Web API Changelog](https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-changelog/)).
- CALYPSO moves to multi-account / managed-account mode where Third-Party
  OAuth gives clearer per-customer credentialing than TWS API client IDs.
- The weekly phone-tap becomes operationally unacceptable and IBKR offers a
  certificate-based replacement.

Until then, IB Gateway + `ib_async` is the fastest path to a working
0DTE-iron-condor bot on a GCE VM.

---

## 7. Cost (May 2026)

All figures are USD and verified against IBKR pages as of this research; **re-verify on the official pricing page before deploying anything that
depends on them.** ([Commissions Options — IBKR LLC](https://www.interactivebrokers.com/en/pricing/commissions-options.php),
[Market Data Pricing](https://www.interactivebrokers.com/en/pricing/market-data-pricing.php),
[Cboe Options Fees — IBKR](https://www.interactivebrokers.com/en/accounts/fees/CBOEoptfee.php),
[Compare Lite vs Pro](https://www.interactivebrokers.com/en/general/compare-lite-pro.php))

### 7.1 Account-level fees

- **No monthly minimum** for IBKR Pro or IBKR Lite accounts as of 2026
  (the historical $10/month inactivity fee was retired pre-2026). **[unconfirmed]** —
  re-verify on the account opening flow.
- **API access** is free of charge on both Pro and Lite. ([Trading Web API](https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-trading/))

### 7.2 IBKR Pro vs IBKR Lite — the choice for CALYPSO

| Property | IBKR Pro | IBKR Lite |
|---|---|---|
| US-listed equity trades | Tiered (0.05¢–0.35¢/share, $0.35 min, 1% max) or fixed | Commission-free (US-only) |
| **Web API trading endpoint** | ✅ Eligible | ❌ **Not eligible** for the Web API trading endpoint |
| SmartRouting / price improvement | ✅ | ❌ (routed to PFOF market makers) |
| Interest on cash | Benchmark − 0.5% on USD | Benchmark − 1.5% on USD |
| Geography | Everywhere | US residents only |

**CALYPSO must use IBKR Pro.** Lite is disqualified by the Web-API
eligibility rule and disqualified anyway by the SmartRouting requirement for
0DTE SPX. ([IBKR Pro vs Lite comparison](https://www.interactivebrokers.com/en/general/compare-lite-pro.php))

### 7.3 SPX option commissions (IBKR Pro tiered)

Tiered pricing scales with monthly contract volume. Component fees apply
**on top of** IBKR's commission, **per contract per side**:

| Component | Fee | Notes |
|---|---|---|
| IBKR commission (≤10,000 contracts/month, Pro tiered) | **$0.65 / contract** ($1.00 minimum/order). Lower tiers down to ~$0.25 above 50,000/month. | Often *negative* net of rebates for limit-order liquidity adders. |
| Cboe SPX Trade Processing Service | $0.0025 / contract / side | Standard. |
| Cboe SPX Floor Brokerage replacement (when applicable) | $0.04 / contract / side | Only for floor-brokerage non-crossed orders — irrelevant for electronic SPXW. |
| Options Regulatory Fee (ORF) | Per-side, exchange-dependent; charged on **sells only**. **[unconfirmed]** exact 2026 number — historically ~$0.03–0.04/contract on Cboe. | Charged on AMEX, BATS, BOX, CBOE, CBOE2, EDGX, EMERALD, ISE, GEMINI, MERCURY, MIAX, MEMX, NOM, NASDAQBX, PSE, PHLX, SAPPHIRE. |
| SEC Section 31 fee | Per-side, on sell side, fraction of notional. | Cents per contract. |
| OCC clearing fee | $0.02 / contract | Standard. |

**Rule-of-thumb all-in for a 0DTE SPX leg (one side, tiered, electronic, limit
order adding liquidity):** roughly **$0.70–$0.90 / contract** on entry,
similar on exit, with ORF + Section 31 only on sell-side legs. A 4-leg iron
condor at 1 contract per leg ⇒ ~$3–$4 entry + ~$3–$4 exit. **Always
re-derive this from the live IBKR confirmation slip** — see the explicit
disclaimer in IBKR's docs that *"Costs passed on to clients in IBKR's Tiered
commission schedule may be greater than the costs paid by IBKR to the
relevant exchange, regulator, clearinghouse or third party."*

### 7.4 Market data subscriptions

The minimum bundle to get **real-time SPX option quotes** on the API:

| Subscription | Monthly cost (May 2026) | Why CALYPSO needs it |
|---|---|---|
| **US Securities Snapshot and Futures Value Bundle** (core bundle) | **$10.00/month** — auto-waived when monthly commissions ≥ $30. | Prerequisite for OPRA and most US data. |
| **OPRA (Options Price Reporting Authority)** | **$1.50/month** — auto-waived when monthly commissions ≥ $20. | Required for streaming options quotes on the API. **Without this, the API returns delayed or no quotes for SPX options.** |
| **Cboe One / IEX free non-consolidated equities** | $0 | Included for all IBKR clients; not sufficient for options. |
| **Cboe Streaming Market Indexes** (SPX index level) | **[unconfirmed]** — typically ~$3–6/month for non-pros. | Needed if you want the live SPX index print (not just option mid). |

**Effective monthly data cost for an active CALYPSO account:** $0 once
commissions clear ~$30/month, otherwise $11.50–$15/month plus the index
subscription if you want live SPX. ([Market Data Pricing — IBKR LLC](https://www.interactivebrokers.com/en/pricing/market-data-pricing.php),
[Market Data Subscriptions — IBKR Campus](https://www.interactivebrokers.com/campus/ibkr-api-page/market-data-subscriptions/))

### 7.5 What's *not* a cost surprise

- API access itself: **free**.
- Paper trading: **free**, but paper market data is delayed unless you
  subscribe live data (the same subscription covers both).
- Withdrawal: one free wire per month, additional are charged. Not
  CALYPSO-relevant day-to-day.

---

## 8. Open questions for IBKR API support

Before final architecture sign-off the following should be confirmed by an
email to `api-solutions@interactivebrokers.com`:

1. For an **individual IBKR Pro account**, is there a documented OAuth 2.0
   path to the Web API trading endpoint that **does not require running
   `clientportal.gw`** on the deployment host? (Per §4.5 the answer appears
   to be "no" today, but the docs are in beta.)
2. What is the public-key rotation cadence for First-Party OAuth 2.0
   `private_key_jwt` registrations?
3. What is the current monthly fee for the **Cboe Streaming Market Indexes**
   subscription for non-professionals (covers live SPX index)?
4. Is the weekly-cold-login 2FA still enforced for accounts that have
   enabled only "IB Key Security via IBKR Mobile" as of May 2026? Any path
   to a true unattended-week-plus session for a single-trader algorithmic
   account?
5. Final official 2026 ORF on Cboe (SPX) per-contract sell-side.

---

## 9. Appendix — primary sources

- [IBKR Trading API Solutions](https://www.interactivebrokers.com/en/trading/ib-api.php)
- [IBKR API Home — IBKR Campus](https://www.interactivebrokers.com/campus/ibkr-api-page/ibkr-api-home/)
- [Getting Started — IBKR Campus](https://www.interactivebrokers.com/campus/ibkr-api-page/getting-started/)
- [Trader Workstation API — IBKR Campus](https://www.interactivebrokers.com/campus/ibkr-api-page/trader-workstation-api/)
- [TWS API Changelog](https://www.interactivebrokers.com/campus/ibkr-api-page/tws-api-changelog-2/)
- [TWS API Release Notes (ibkrguides)](https://ibkrguides.com/releasenotes/tws-api.htm)
- [API downloads — interactivebrokers.github.io](https://interactivebrokers.github.io/)
- [TWS API source code access](https://www.interactivebrokers.com/campus/trading-lessons/accessing-the-tws-python-api-source-code/)
- [Web API Documentation — IBKR Campus](https://www.interactivebrokers.com/campus/ibkr-api-page/webapi-doc/)
- [Web API v1.0 Documentation (CP API)](https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/)
- [Web API Reference](https://www.interactivebrokers.com/campus/ibkr-api-page/webapi-ref/)
- [Web API Changelog](https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-changelog/)
- [Trading Web API](https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-trading/)
- [Account Management Web API](https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-account-management/)
- [OAuth 1.0a Extended](https://www.interactivebrokers.com/campus/ibkr-api-page/oauth-1-0a-extended/)
- [FIX — IBKR Campus](https://www.interactivebrokers.com/campus/ibkr-api-page/fix/)
- [FIX CTCI glossary](https://www.interactivebrokers.com/campus/glossary-terms/fix-ctci/)
- [Launching and Authenticating the Gateway](https://www.interactivebrokers.com/campus/trading-lessons/launching-and-authenticating-the-gateway/)
- [Websockets — IBKR Campus](https://www.interactivebrokers.com/campus/trading-lessons/websockets/)
- [Installing & Configuring TWS for the API](https://www.interactivebrokers.com/campus/trading-lessons/installing-configuring-tws-for-the-api/)
- [Two-Factor Authentication FAQ](https://ibkrguides.com/securelogin/sls/faq.htm)
- [Two-Factor Authentication Methods](https://ibkrguides.com/securelogin/sls/twofactorauth.htm)
- [Compare IBKR Lite and Pro](https://www.interactivebrokers.com/en/general/compare-lite-pro.php)
- [Commissions Options](https://www.interactivebrokers.com/en/pricing/commissions-options.php)
- [Cboe Options Fees](https://www.interactivebrokers.com/en/accounts/fees/CBOEoptfee.php)
- [Market Data Pricing](https://www.interactivebrokers.com/en/pricing/market-data-pricing.php)
- [Market Data Subscriptions](https://www.interactivebrokers.com/campus/ibkr-api-page/market-data-subscriptions/)
- [ib_async on GitHub](https://github.com/ib-api-reloaded/ib_async)
- [ib_async on PyPI](https://pypi.org/project/ib_async/)
- [ib_insync on PyPI](https://pypi.org/project/ib-insync/)
- [ibapi unofficial mirror](https://github.com/deepentropy/ibapi/)
- [IbcAlpha/IBC](https://github.com/IbcAlpha/IBC)
- [IBC userguide](https://github.com/IbcAlpha/IBC/blob/master/userguide.md)
- [Voyz/ibeam](https://github.com/Voyz/ibeam)
- [Voyz/ibind](https://github.com/Voyz/ibind)
- [gnzsnz/ib-gateway-docker](https://github.com/gnzsnz/ib-gateway-docker)
- [extrange/ibkr-docker](https://github.com/extrange/ibkr-docker)
- [Trading 0DTE Options with the IBKR Native API](https://www.interactivebrokers.com/campus/ibkr-quant-news/trading-0dte-options-with-the-ibkr-native-api/)
- [TWS Python API Placing Complex Orders](https://www.interactivebrokers.com/campus/trading-lessons/python-complex-orders/)
- [Cboe SPX — IBKR](https://www.interactivebrokers.com/en/trading/cboe.php)
