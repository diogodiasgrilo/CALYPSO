# Interactive Brokers Market Data — Migration Research

**Author**: research scratch for the Saxo → IB migration of the 0DTE SPX iron-condor bot
**Date compiled**: 2026-05-13
**Scope**: Real-time spot (SPX + VIX), options chain quotes / greeks, historical bars, comparison vs Polygon Options Starter ($29 / mo)

---

## 0. TL;DR for the impatient

| Need | IBKR subscription | Non-pro / mo | Pro / mo | Source |
|---|---|---|---|---|
| US equities + ETFs + futures top-of-book (prerequisite for everything else) | **US Securities Snapshot and Futures Value Bundle** | **$10** | ~$100+ ("often 10×") | [supa.is 2026 guide](https://supa.is/article/interactive-brokers-market-data-subscription-which-one-do-i-need-2026) |
| SPX options bid/ask/last (OPRA) | **OPRA Top of Book** (requires the bundle above) | **$1.50** | 10× | same |
| Continuous streaming vs snapshot upgrade | **US Equity and Options Add-On Streaming Bundle** | **$4.50** | n/a | same |
| SPX index spot (the `.SPX` value, not the option) | **CBOE Streaming Market Indexes** (or the bundled "Cboe Global Indices Feed") | **price-on-request from Cboe / not publicly itemized on IBKR's pricing list** — community reports put it in the low single-digit USD range | unknown | [cboe.com — Cboe Global Indices Feed](https://www.cboe.com/us/indices/accessing-index-data/), [IBKR Cboe SPX page](https://www.interactivebrokers.com/en/trading/cboe.php) |
| VIX spot (`.VIX`) | Same as above — VIX index is published over the **Cboe Global Indices Feed (formerly CSMI)** | same caveat | unknown | [dxfeed — Cboe Global Indices Feed](https://dxfeed.com/market-data/indices/cboe-global-indices-feed/) |
| VIX **futures** (VX) | **CFE Enhanced Top of Book** | listed but price not public-facing on a single page | higher | [IBKR /cfe page](https://www.interactivebrokers.com/cfe/) |

**Commission waivers** (non-pro): the $10 base bundle is waived once you generate **$30 / month in commissions**; the $1.50 OPRA add-on is waived at **$20 / month** commissions ([supa.is](https://supa.is/article/interactive-brokers-market-data-subscription-which-one-do-i-need-2026), 2026-03-06). For a 0DTE iron-condor bot trading SPX even at modest size, those thresholds are easily cleared inside the first week of the month, so the **effective recurring data cost is dominated by the index-feed line** (SPX / VIX spot), **not by OPRA**.

**Can we drop Polygon Options Starter ($29 / mo)?**
Largely **yes for live monitoring** — IB's OPRA + Model-Greeks delivers per-strike bid/ask/last + IV + delta/gamma/theta/vega + OI streaming, the exact fields our stop-monitor reads. The two practical caveats are (i) the **default 100-ticker concurrency cap** (we want ~80 strikes plus SPX + VIX, which is workable but tight), and (ii) **no historical OPRA backfill** — IB only gives you what you stream, no "give me yesterday's chain at 14:32". If we want historical chain replay for back-testing, Polygon stays. For purely live trading, Polygon can go. Detailed comparison in §7.

---

## 1. IB's tier system — real-time vs delayed, pro vs non-pro

IB sells market data as **per-exchange à-la-carte subscriptions** layered on top of two **bundles**, plus the **Cboe One** and **IEX** non-consolidated feeds that come free to every authenticated client.

### 1.1 What's free

> "IBKR clients receive free real-time streaming market data on all US-listed stocks and ETFs from Cboe One and IEX. However … the free real-time streaming market data on all US-listed stocks and ETFs from Cboe One and IEX is non-consolidated."
> — [Market Data Pricing | Interactive Brokers LLC](https://www.interactivebrokers.com/en/pricing/market-data-pricing.php)

"Non-consolidated" means quotes from those two venues only, not the full NBBO. Useful for sanity-check displays; not adequate for an options bot whose entry leg is priced off the consolidated underlying.

There is **no free real-time SPX or VIX index spot** — both indexes are CBOE-disseminated and require a paid Cboe index feed. Without a subscription, IB falls back to a 15-minute-delayed value (tick types 66–76 in the API, see §3.1).

### 1.2 Pro vs Non-Pro qualification

> "By default, IB classifies everyone as professional. Individual traders should update their status to non-professional in Client Portal → Settings → Market Data Subscriptions → Subscriber Status."
> — [supa.is 2026 guide](https://supa.is/article/interactive-brokers-market-data-subscription-which-one-do-i-need-2026)

Non-pro qualification (SEC standard) means: not registered with any securities regulator, not employed by a financial firm using the data for any business purpose, and trading a personal account. Pro fees are reported as **"often 10× higher"** ([supa.is](https://supa.is/article/interactive-brokers-market-data-subscription-which-one-do-i-need-2026), 2026-04). For our case the bot is owned and operated by an individual trading personal capital, so non-pro applies.

> "IBKR retains 5% – 10% of market data fees to cover administrative costs, with the remaining amount paid to the data vendor."
> — [Market Data Pricing | Interactive Brokers LLC](https://www.interactivebrokers.com/en/pricing/market-data-pricing.php)

IB is the **billing intermediary**, not the data owner. Subscription names map directly to CBOE / NYSE / NASDAQ exchange feeds.

### 1.3 IB Lite vs IB Pro

IB Lite gets the same free Cboe One + IEX feed. **Subscription pricing for the paid bundles is identical between Lite and Pro accounts** — what differs between Lite and Pro is the commission schedule, not the data layer. Pro vs Non-Pro classification (above) is what changes data costs by ~10×.

---

## 2. Subscription names — exactly what to enable for SPX + VIX + SPX options

Verified subscription names from the 2026-03-06 supa.is guide cross-referenced against [interactivebrokers.com/en/pricing/market-data-pricing.php](https://www.interactivebrokers.com/en/pricing/market-data-pricing.php):

| Subscription | Non-Pro $/mo | Waiver | What it gives you for this bot |
|---|---|---|---|
| **US Securities Snapshot and Futures Value Bundle** | $10 | $30 / mo commissions | Consolidated US equity/ETF NBBO, CME/CBOT/COMEX/NYMEX futures top-of-book, OTC, US bonds. **Prerequisite for OPRA** and effectively for every paid US data add-on. |
| **OPRA Top of Book** | $1.50 | $20 / mo commissions | Consolidated US options quotes from all 16+ option exchanges. Required for SPX option bid/ask/last + the Model-Greeks computation. |
| **US Equity and Options Add-On Streaming Bundle** | $4.50 | none documented | Upgrades the underlying bundle from "snapshot every few seconds" to **continuous tick-by-tick streaming**. Without this, the snapshot bundle gives you periodic updates suitable for a quote box but not for a tick-driven stop monitor. **Required if you want true streaming on the underlying** alongside streaming OPRA option quotes. |
| **CBOE Streaming Market Indexes** (a channel of the **Cboe Global Indices Feed**, formerly CSMI) | not itemized on a single public IBKR page — community reports put non-pro at $1–$3 / mo | none documented | Real-time SPX index spot + VIX index spot + ~400 other Cboe indices. Both `.SPX` and `.VIX` are published over this single feed ([cboe.com](https://www.cboe.com/us/indices/accessing-index-data/), [dxfeed.com](https://dxfeed.com/market-data/indices/cboe-global-indices-feed/)). |
| **CFE Enhanced Top of Book** | not publicly itemized | none documented | VIX **futures** L1 quotes + the VIX **index spot value** is also propagated on this feed per CFE docs. Optional for us — we don't trade VX directly, we read VIX spot. The CBOE index feed above already gives us VIX spot, so CFE Enhanced is **not required**. |

**Minimum recommended IBKR data stack for this bot (non-pro, May 2026)**:
1. US Securities Snapshot and Futures Value Bundle — $10
2. OPRA Top of Book — $1.50
3. US Equity and Options Add-On Streaming Bundle — $4.50
4. CBOE Streaming Market Indexes (or whichever exact item IBKR exposes on the subscription list for the Cboe Global Indices Feed) — assume ~$2

**Pre-waiver total: ~$18 / month. Effective total after commission waivers: ~$2–$8 / month** (only the index feed and possibly the streaming add-on don't waive).

For comparison, the existing Saxo + Polygon stack is approximately Polygon $29 + Saxo's bundled data (free with the platform fee). Replacing that with the IB stack saves ~$20–$25 / mo if Polygon is dropped — see §7 for whether that's safe.

> Caveat (May 2026): the **CBOE Streaming Market Indexes** line item is the part of this list that's hardest to pin down to an exact public number — neither the IBKR pricing page nor the supa.is 2026 guide itemizes it cleanly. The standard advice is to subscribe via Client Portal → Settings → Market Data Subscriptions, observe the prorated charge on the first month's statement, and back out the rate. For the migration plan budget, **assume $5 / mo as a conservative placeholder until verified on the first invoice**.

---

## 3. API delivery — TWS API (the canonical, low-latency path)

### 3.1 `reqMktData` — streaming subscription model

`reqMktData(reqId, contract, genericTickList, snapshot, regulatorySnapshot, mktDataOptions)`

From [ib_insync 0.9.86 docs](https://ib-insync.readthedocs.io/api.html) and the [TWS API tick-types reference](https://interactivebrokers.github.io/tws-api/tick_types.html):

- `genericTickList`: comma-separated list of generic-tick codes (see table below). Empty string `""` means "default ticks only".
- `snapshot`: `True` for one-shot, `False` for streaming subscription. **In snapshot mode you must leave genericTickList empty** — IB explicitly rejects snapshots with generic ticks ([IBKR Campus, requesting market data](https://www.interactivebrokers.com/campus/trading-lessons/requesting-market-data/)).
- `regulatorySnapshot`: NBBO snapshot, **incurs per-snapshot fees** even if you have OPRA. Skip unless you need an audit-grade frozen quote.

**Default tick types delivered on every `reqMktData`**:

| Tick ID | Name | Callback | What you get |
|---|---|---|---|
| 1 | BID | `tickPrice` | Best bid |
| 2 | ASK | `tickPrice` | Best offer |
| 4 | LAST | `tickPrice` | Last trade |
| 6 | HIGH | `tickPrice` | Day high |
| 7 | LOW | `tickPrice` | Day low |
| 8 | VOLUME | `tickSize` | Day volume |
| 9 | CLOSE | `tickPrice` | Previous close |
| 14 | OPEN | `tickPrice` | Day open |
| 10/11/12/13 | BID_OPTION_COMPUTATION / ASK_OPTION_COMPUTATION / LAST_OPTION_COMPUTATION / MODEL_OPTION | `tickOptionComputation` | IV + delta/gamma/vega/theta + undPrice + pvDividend + optPrice — emitted automatically for **options contracts** |

Source: [TWS API v9.72+: Available Tick Types](https://interactivebrokers.github.io/tws-api/tick_types.html) (marked "deprecated, see IBKR Campus" but still the canonical reference).

> "The option greek values — delta, gamma, theta, vega — are returned by default following a reqMktData() request for the option."
> — [TWS API: Option Greeks](https://interactivebrokers.github.io/tws-api/option_computations.html)

Greeks are **streamed**, not snapshotted — every time the bid, ask, last, or underlying moves, IB recomputes and re-emits `tickOptionComputation` for the relevant side. This is the key fact for dropping Polygon: per-strike streaming greeks are free with OPRA.

### 3.2 Important generic-tick codes (pass via `genericTickList`)

| Code | Tick type produced | Why we care |
|---|---|---|
| **100** | Option Call/Put Volume (45/46) | Per-leg volume |
| **101** | **Option Open Interest** (27/28) | **Per-strike OI — required for GEX. This is the Polygon replacement.** |
| 104 | Option Historical Volatility (23) | 30-day HV |
| 105 | Average Option Volume (54) | — |
| 106 | Option Implied Volatility (24) | Forward 30-day IV estimate (for the **underlying**, not per-strike) |
| 165 | Misc Stats (Weekly / 52-week ranges) | — |
| 221 | Mark Price | True mid; useful for fair-value benchmarking against our entry mids |
| 225 | Auction values | — |
| 233 | **RT Volume** | Trade-by-trade tape with "unreportable" trades included — useful for tighter fills tracking |
| 236 | Shortable | — |
| 258 | Fundamental Ratios | — |
| 411 | RT Historical Volatility | Live 30-day RV |
| 456 | IB Dividends | Dividend stream incl. PV |
| 595 | Short-Term Volume | 3/5/10-min snapshot volume |

Source: [TWS API: Available Tick Types](https://interactivebrokers.github.io/tws-api/tick_types.html).

For our use case the **minimal generic-tick list per SPX option** is `"100,101"` — gives us volume + OI on top of the default bid/ask/last/greeks. For SPX **index** itself: `""` (no generic ticks needed — bid/ask/last are enough).

### 3.3 Snapshot mode — single-shot quotes

> "A snapshot request will only return available data over the 11-second span; in some cases values may not be returned for all tick types."
> — [Top Market Data (Level I)](https://interactivebrokers.github.io/tws-api/top_data.html)

Behavior: pass `snapshot=True`, IB collects ticks for 11 seconds and then fires a `tickSnapshotEnd` callback. The cost-side wrinkle:

> "Snapshot requests can only be made for the default tick types with no generic ticks specified."
> — [Top Market Data (Level I)](https://interactivebrokers.github.io/tws-api/top_data.html)

So snapshot mode is **not usable for greeks-and-OI bulk chain pulls** — you lose the very fields we need. Streaming with quick `cancelMktData` is the only path for chain analytics.

### 3.4 `reqSecDefOptParams` — option chain expirations + strikes

From [TWS API: Options](https://interactivebrokers.github.io/tws-api/options.html):

> "The function `reqSecDefOptParams` was introduced [in v9.72] and it's now the recommended approach for retrieving options chains. One limitation of the `reqContractDetails` technique is that option chain returns will be throttled and take longer the more ambiguous the contract definition, whereas the new `reqSecDefOptParams` function does not have the throttling limitation."

Signature: `reqSecDefOptParams(reqId, underlyingSymbol, futFopExchange, underlyingSecType, underlyingConId)`

For SPX:
- `underlyingSymbol = "SPX"`
- `futFopExchange = ""` (empty for non-futures-options)
- `underlyingSecType = "IND"`
- `underlyingConId = 416904` (the SPX index conId — first qualified via `reqContractDetails` on an SPX `IND` contract)

Returns `securityDefinitionOptionParameter` callbacks, each with:
- exchange (`SMART`, `CBOE`, etc.)
- trading class (`SPX` for traditional 3rd-Friday AM-settled, **`SPXW` for daily / weekly / EOM PM-settled — what we want for 0DTE**)
- multiplier (`100`)
- expirations: `Set[str]` in `YYYYMMDD` form
- strikes: `Set[float]`

The function emits one callback **per exchange × trading-class combination**, so for SPX you typically get four callbacks: `(CBOE, SPX)`, `(CBOE, SPXW)`, `(SMART, SPX)`, `(SMART, SPXW)`. **Filter for `tradingClass == 'SPXW'`** to get the 0DTE-eligible chain.

> "In some cases it is possible there are combinations of strike and expiry that would not give a valid option contract."
> — [TWS API: Options](https://interactivebrokers.github.io/tws-api/options.html)

So the strikes × expirations Cartesian product is a **superset** of what actually trades. Before subscribing market data, qualify each `Option('SPX', '20260513', strike, 'C/P', 'SMART', tradingClass='SPXW')` via `reqContractDetails` to drop the phantoms.

### 3.5 `reqContractDetails` — instrument lookup

Use when you need the full `ContractDetails` record (conId, minTick, validExchanges, etc.) or when `reqSecDefOptParams` doesn't cover your case (FOPs on weird exchanges). Subject to **throttling** for ambiguous queries — fully specified contracts return promptly, wildcards do not.

### 3.6 `reqMktDepth` — Level 2

Not relevant for our SPX iron condor workflow. SPX options L2 requires a deeper-book subscription, isn't widely useful for index options (depth is thin past top of book), and OPRA top-of-book is what fills our orders.

### 3.7 `reqHistoricalData` — bars

Signature: `reqHistoricalData(reqId, contract, endDateTime, durationStr, barSizeSetting, whatToShow, useRTH, formatDate, keepUpToDate, chartOptions)`

For SPX 1-min bars: `durationStr="1 D"`, `barSizeSetting="1 min"`, `whatToShow="TRADES"` or `"MIDPOINT"`.

**Pacing limits** (very strict, source: [Historical Data Limitations](https://interactivebrokers.github.io/tws-api/historical_limitations.html)):

> "The maximum number of simultaneous open historical data requests from the API is 50."
> "Making identical historical data requests within 15 seconds [is a violation]."
> "Making six or more historical data requests for the same Contract, Exchange and Tick Type within two seconds [is a violation]."
> "Making more than 60 requests within any ten minute period [is a violation]."
> "When BID_ASK historical data is requested, each request is counted twice."

For our analytics use ("back-of-house 1-min SPX bars"), 60 requests / 10 min is plenty — one nightly cron pull of 1 day of 1-min bars is exactly one request, and a weekly backfill of 30 days is one request. The limits only bite if you try to bulk-pull a chain's worth of historical option bars; **don't try to backfill 80 option contract histories at once** — that's a 100-request burst and an immediate pacing violation.

### 3.8 Global TWS API rate limit

> "The TWS API has an inherent limitation of 50 messages per second … API messages sent at a higher rate than 50/second can now be paced by TWS at the 50/second rate instead of causing a disconnection, by invoking SetConnectOptions('+PACEAPI') prior to eConnect."
> — community reference [Elite Trader, IB message rate](https://www.elitetrader.com/et/threads/ib-message-rate-and-api-ctci-limits.211612/), echoed in [TWS API 2022 release notes](https://ibkrguides.com/releasenotes/api/tws/prod-2022.htm)

Set `+PACEAPI` on connect and TWS will rate-limit you to 50/sec instead of disconnecting. With ~80 SPX option subscriptions + 2 index subscriptions, our peak outbound message rate during initial chain build is ~82 `reqMktData` calls, well under the 50/sec ceiling if spaced over 2 seconds (which the loop naturally does).

### 3.9 Ticker concurrency limit ("market data lines")

This is **the** number to watch:

> "By default, every user has a maxTicker Limit of 100 market data lines and can obtain the real time market data of up to 100 instruments simultaneously."
> — [TWS API: Streaming Market Data](https://interactivebrokers.github.io/tws-api/market_data.html)

> "The quantity of market data is allocated using the greater value of (commissions ÷ 8) or (equity × 100 ÷ 1,000,000), which could result in more than the default 100 lines if your account meets those criteria."
> — same source

So:
- Default: **100 simultaneous tickers**.
- Bonus: $800/mo commissions → +100 (200 total); $400 → +50 (150 total). Equity-based: $1M → +100. Whichever is greater.
- Bonus: "Quote Booster Packs" purchasable in increments.

For 80 SPX options + SPX spot + VIX spot = 82 lines, comfortably under 100. For a wider chain (the 530-strike full-SPX 0DTE GEX-style snapshot Polygon serves), 100 is **not enough** — you'd need to either rotate (subscribe, read, cancel, rinse-repeat in batches of 80) or buy booster packs.

**Margin for safety**: keep the standing subscription set at our current ~80 condor-relevant strikes; reuse 10–15 slots for spot indices, futures, watch-list. The booster-pack escalation only matters if we expand to full-chain GEX analytics.

### 3.10 Latency

IBKR doesn't publish a wire-latency SLA. Empirical reports from algorithmic trading communities (Elite Trader, /r/algotrading, backtrader forums) consistently put TWS-API tick latency at **~50–200 ms from exchange to client callback** under healthy network conditions when colocated near IBKR's gateway (Chicago, Stamford, or via IBKR's regional gateways). The dominant additive latency is the TWS / IB-Gateway local process itself (~5–20 ms internal), then network RTT to IB datacenter. **For a stop monitor sampling every 2–15 seconds, this is irrelevant** — even 500 ms latency leaves us with 1.5+ seconds of usable freshness window. For entry placement timing it's the order-roundtrip latency that matters, not the quote latency — see deployment doc.

---

## 4. API delivery — Client Portal Web API (CP API, REST + WebSocket)

The Web API is the **HTTP / WebSocket alternative** to TWS API. Same underlying market data, different transport.

### 4.1 Authentication model

Unlike TWS API (TCP socket to a locally running TWS or IB Gateway process), CP API runs a **local gateway** (`clientportal.gw` Java process) bound to `localhost:5000`, which proxies authenticated HTTPS to IBKR. You log in once via browser, the gateway maintains the session, and your code calls `https://localhost:5000/v1/api/...`.

### 4.2 Key market-data REST endpoints

From [interactivebrokers.github.io/cpwebapi](https://interactivebrokers.github.io/cpwebapi/) and the [IBKR Campus Web API docs](https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/):

| Endpoint | Purpose |
|---|---|
| `GET /iserver/marketdata/snapshot?conids=...&fields=...` | Snapshot top-of-book for **up to 100 conids per call**, **up to 50 fields** per call. Fields are tag codes (31=last, 84=bid, 86=ask, 88=size at bid, 85=size at ask, 7059=Mark, etc.) — full table in the IBKR reference. Greeks live under their own field tags (7311=delta, etc.). |
| `GET /iserver/marketdata/history?conid=...&period=...&bar=...` | Historical bars (5-min, 1-min, daily). The older `/hmds/history` endpoint is **deprecated** as of 2024 ([IBKR docs](https://www.interactivebrokers.com/campus/trading-lessons/requesting-market-data/)). |
| `GET /iserver/secdef/info?conid=...&sectype=OPT&month=MAY26&strike=...&right=C` | Resolve a specific option contract from the underlying conId + expiry + strike + right |
| `GET /iserver/secdef/strikes?conid=...&sectype=OPT&month=MAY26` | List strikes for an option underlying + expiry month — the CP-API equivalent of `reqSecDefOptParams` |
| `GET /iserver/secdef/search?symbol=SPX&secType=IND` | Symbol search → conId |

### 4.3 WebSocket streaming over CP API

> "To open a stream for live, top-of-book market data for an instrument, you write a message to the websocket in the form: `smd+CONID+{\"fields\":[\"field_1\",\"field_2\",...,\"field_n\"]}`, where the values in the fields array are the same field tags used in the HTTP request to /iserver/marketdata/snapshot."
> — [IBKR Campus: Websockets](https://www.interactivebrokers.com/campus/trading-lessons/websockets/)

URL: `wss://localhost:5000/v1/api/ws`. Cancel a subscription with a `umd+CONID+{}` message.

**Stated limit**: 5 concurrent WebSocket market-data subscriptions per session ([interactivebrokers.github.io/cpwebapi](https://interactivebrokers.github.io/cpwebapi/) — note: this number is widely cited but the IBKR docs are less explicit than for TWS-API's 100-ticker limit; **treat 5 as a conservative read** that's worth verifying on the actual sandbox).

> The 5-subscription WebSocket cap is the **decisive reason to use TWS API, not CP API, for this bot.** 80 option contracts × the 5-subscription cap means we'd need 16 parallel sessions to cover the chain via CP-API WebSockets — operationally impossible. CP API is fine for the snapshot REST call (which takes 100 conids per request) but **not for streaming options chains.**

### 4.4 Latency vs TWS API

Same underlying data feed. CP API adds HTTP overhead (~10–30 ms per REST call, WebSocket message latency ~similar to TWS-API tick latency). For a stop monitor sampling every 2–15 sec, indistinguishable. For tick-by-tick streaming, TWS API is preferred. Both routes consume the **same** market-data lines / ticker limit (it's an account-level entitlement, not per-API).

### 4.5 Global Web API rate limit

> "Interactive Brokers currently enforces a global request rate limit of 50 requests per second for each authenticated username in the Web API session."
> — [IbkrApi.RateLimiter — community Elixir wrapper docs](https://hexdocs.pm/ibkr_api/IbkrApi.RateLimiter.html), corroborating IBKR Campus

Same 50 req/sec as TWS API. Snapshot calls of 100 conids each could in principle update an 80-strike chain in 1 REST call — which makes CP-API a viable path for **periodic chain pulls** (e.g., a 3-minute GEX refresh) even if it's wrong for live streaming.

---

## 5. SPX-specific gotchas

### 5.1 Index symbol setup

> "When you select IND for SecType and SPX for symbol, you'll get the contract with conid 416904, and CBOE will be the exchange because IBKR sources the SPX data from them."
> — [twsapi groups.io: how to get SPX Index data](https://groups.io/g/twsapi/topic/how_to_get_spx_index_data/4047244)

```python
spx = Contract()
spx.symbol = 'SPX'
spx.secType = 'IND'
spx.exchange = 'CBOE'
spx.currency = 'USD'
```

VIX is identical with `symbol='VIX'`.

### 5.2 SPX vs SPXW — the trading-class trap

Two distinct option roots on the same underlying ([IBKR Cboe SPX page](https://www.interactivebrokers.com/en/trading/cboe.php), [marketxls](https://marketxls.com/blog/understanding-spxw-options-a-weekly-sp-500-analysis)):

| Root / `tradingClass` | Expiry | Settlement | Used for 0DTE? |
|---|---|---|---|
| **SPX** | 3rd Friday monthly only | **AM-settled** (special opening quotation) | No |
| **SPXW** | Daily Mon–Fri + EOM | **PM-settled** (close at 4:00 PM ET) | **Yes — this is what we trade.** |

When constructing the option contract you **must** set `tradingClass='SPXW'` or IB may return the wrong root or no match at all:

```python
opt = Option('SPX', '20260513', 5800, 'P', 'SMART', tradingClass='SPXW')
```

This is the #1 source of "I can't find the contract" errors when migrating from a broker that hides the trading-class abstraction.

### 5.3 Cboe Global Indices Feed = the only path to live SPX / VIX spot

There is **no free** real-time SPX or VIX value from IBKR. The "Cboe One" free feed covers stocks/ETFs — indices are a separate Cboe product line:

> "The Cboe Global Indices Feed is the definitive real-time streaming index data service for SPX®, VIX®, and indices from Morningstar, S&P Dow Jones, FTSE Russell, MSCI, and others."
> — [cboe.com — accessing index data](https://www.cboe.com/us/indices/accessing-index-data/), [dxfeed CSMI page](https://dxfeed.com/market-data/indices/cboe-global-indices-feed/)

Without that subscription, `reqMktData` on the SPX `IND` contract returns 15-minute-delayed tick types (66–76, the delayed equivalents of 1/2/4/etc.) ([TWS API tick types](https://interactivebrokers.github.io/tws-api/tick_types.html)). **The bot will silently run on 15-min-stale spot if the subscription is misconfigured** — add a startup health check that fails fast if it sees delayed-tick IDs.

### 5.4 Trading hours

> "Cboe Options Exchange offers extended global trading hours for index options in S&P 500 Index (SPX), Cboe Volatility Index (VIX), and Mini SPX Index (XSP) from 8:15 pm ET to 9:15 am ET Monday through Friday."
> — [IBKR Cboe SPX page](https://www.interactivebrokers.com/en/trading/cboe.php)

Regular session is 9:30 AM – 4:00 PM ET (with SPX/SPXW continuing to 4:15 PM for trade-settlement reporting). Our 0DTE bot operates only in the regular session.

---

## 6. Options chain mechanics — building the working set

### 6.1 Workflow

```
1. Qualify the SPX index contract:
     IND SPX CBOE USD → conId 416904

2. Call reqSecDefOptParams("SPX", "", "IND", 416904)
   → multiple securityDefinitionOptionParameter callbacks
   → filter where tradingClass == "SPXW"
   → extract today's expiration string (YYYYMMDD)
   → filter strikes to a window around current SPX spot (e.g. ±5%)

3. For each (expiry, strike, right) in the working set:
     Option(symbol='SPX', lastTradeDateOrContractMonth=expiry,
            strike=strike, right=right, exchange='SMART',
            tradingClass='SPXW')
   → ib.qualifyContracts(*opts) drops phantoms

4. For each qualified option:
     ib.reqMktData(opt, genericTickList='100,101',
                   snapshot=False, regulatorySnapshot=False)
   → Ticker objects auto-populate bid, ask, last, modelGreeks,
     bidGreeks, askGreeks, lastGreeks, openInterest, impliedVolatility

5. Read whenever you need fresh data (every 2–15 sec for stop monitoring)
   → ticker.bid, ticker.ask, ticker.modelGreeks.delta, etc.

6. ib.cancelMktData(opt) when trade closes — frees a ticker slot.
```

### 6.2 Greeks — Model Greeks vs Bid/Ask/Last Greeks

`tickOptionComputation` fires four flavors (tick IDs 10–13):
- **MODEL_OPTION (13)** — IB's internal model greeks using the model price; this is what TWS displays
- **BID_OPTION (10)** / **ASK_OPTION (11)** / **LAST_OPTION (12)** — greeks priced at the bid, ask, or last respectively

For an iron-condor stop monitor we care about the **model greeks** (smoothest, most trustworthy delta) for position aggregation, but for fill quality we want **ask-side greeks for shorts and bid-side greeks for longs** (worst-case fill assumption).

> "Greeks are streamed by default following a reqMktData() request for the option. Note that to receive live greek values it is necessary to have market data subscriptions for both the option and the underlying contract."
> — [TWS API: Option Greeks](https://interactivebrokers.github.io/tws-api/option_computations.html)

**Critical**: if you only subscribe to OPRA + CBOE-indices-feed, the option greeks compute correctly. If you skip the indices subscription, IB falls back to the 15-min-delayed SPX value for the underlying input → **greeks are computed against a stale underlying** and silently wrong. Health-check this.

### 6.3 Ticker budget for an 80-strike condor working set

| Subscription | Tickers | Notes |
|---|---|---|
| SPX index spot | 1 | |
| VIX index spot | 1 | |
| 40 puts (working window) | 40 | each call to reqMktData on an option = 1 ticker |
| 40 calls (working window) | 40 | |
| Future SPX 1-min bars (optional) | 0 | `reqHistoricalData` with `keepUpToDate=False` is a one-shot; doesn't consume a ticker slot |
| **Total** | **82** | within the 100 default cap |

### 6.4 Snapshot the full ~530-strike chain — does it fit?

For ad-hoc "give me the whole 0DTE chain right now" analytics (the Polygon-style GEX use case):
- TWS API path: would need to subscribe 530 options simultaneously → **exceeds 100-ticker limit**. Workable only by rotating in batches of ~80 (subscribe → wait 1 sec for fills → read → cancel → next batch). At 1 sec per batch, full chain in ~7 seconds. Acceptable for 3-minute refresh cadence.
- CP-API REST snapshot path: 530 conids / 100 per call = 6 REST calls. Each call returns top-of-book + greeks (greeks via field tags 7308–7311). Round-trip ~1–2 sec total. **This is actually the cleanest way to do periodic full-chain snapshots — REST, not WebSocket.**

So a hybrid is appealing: TWS API streaming for the 80-strike active working set, CP API REST snapshots for the periodic full-chain GEX refresh. Both share the same `IB-Gateway` auth.

---

## 7. Comparison vs Polygon Options Starter

Polygon Options Starter ($29 / mo, [polygon.io/pricing](https://polygon.io/pricing?product=options)) gives:
- All US options tickers
- Unlimited API calls
- 2 years historical
- **15-minute delayed real-time** (this is the catch — Starter is not real-time; the real-time tier is Developer at $79/mo)
- Greeks, IV, OI
- Minute aggregates
- WebSockets + Snapshot

[polygon.io/knowledge-base/article/does-polygon-support-greeks-for-index-option-contracts](https://polygon.io/knowledge-base/article/does-polygon-support-greeks-for-index-option-contracts) confirms Polygon **does** support per-strike greeks on index options.

### 7.1 Feature parity matrix

| Capability | IBKR (OPRA + indices, ~$18/mo gross, ~$3/mo net after waivers) | Polygon Options Starter ($29/mo) |
|---|---|---|
| Real-time SPX option bid/ask/last | Yes (consolidated OPRA) | **No** — 15-min delayed on Starter |
| Real-time per-strike greeks | Yes (Model Greeks + Bid/Ask/Last variants) | No (15-min delayed) |
| Per-strike Open Interest | Yes (`genericTickList=101`) | Yes |
| Real-time SPX index spot | Yes (with Cboe indices sub) | Yes (index endpoint, but Polygon's index real-time tier is bundled differently — check the indices SKU) |
| Real-time VIX index spot | Yes (same sub) | Yes (same caveat) |
| Bulk full-chain snapshot | Yes via CP-API `/iserver/marketdata/snapshot` 100-conid calls or TWS rotation | Yes (Polygon Snapshot endpoint) |
| Historical option bars | Yes (`reqHistoricalData`, pacing-limited) | Yes (2 yrs) |
| Historical chain replay ("what was OI at 14:32 yesterday") | **No** — IB doesn't sell historical OPRA tick / chain replay | Yes (this is Polygon's core strength) |
| GEX aggregation done for you | No — you compute per-strike gamma × OI yourself | No — also computed by client |
| Refresh cadence | Streaming (free) | REST snapshot (rate-limited but unlimited calls on Starter) |
| Cost | ~$3 / mo net (after waivers, excluding indices sub) — call it **$5–10 / mo realistic** | $29 / mo |

### 7.2 Verdict

**For the live trading loop: drop Polygon.**

IB OPRA + Cboe-indices gives us strictly more than Polygon Options Starter for the live use case, because Starter is 15-min-delayed and IB is real-time. Polygon Starter was useful pre-IB-migration because Saxo's options breadth was thin — IB's OPRA is the deepest options feed available to retail, and it carries the full chain.

**For historical back-test research: keep Polygon (or substitute).**

The one feature IB genuinely doesn't replace is **historical chain replay** — pulling yesterday's full-strike OI + greeks snapshot at a given timestamp. Polygon's flat-file historical archive ([polygon.io/options](https://polygon.io/options)) is the canonical source for this in the retail price range. If we don't actually use this (we run the bot live, we don't back-test new strategies on per-strike chain history), Polygon is dead weight.

**Recommendation**: cut Polygon for an initial trial period (30 days), confirm we never reach for the historical replay during a strategy debug session, and re-evaluate. **Savings: ~$29 / month.**

### 7.3 Caveat about Polygon real-time tier

Note that our current Polygon Starter at $29 is **already 15-min delayed**. If the existing bot's GEX refresh is computed off 15-min delayed Polygon data, dropping Polygon for IB real-time is a **quality upgrade**, not a downgrade. Worth re-reading our existing GEX module to confirm what cadence and freshness it actually relies on — if we're stable on 15-min stale GEX today, IB's streaming-by-default will be over-kill (in a good way).

---

## 8. Practical Python snippets (ib_async, Python 3.12)

[ib_async](https://github.com/ib-api-reloaded/ib_async) is the maintained fork of ib_insync (original author Ewald de Wit passed away in early 2024, fork led by Matt Stancliff). Version 2.1.0 released June 2025. Supports Python 3.10+, which includes 3.12.

```bash
pip install ib_async  # pulls in pandas, eventkit, numpy
```

### 8.1 Subscribe to SPX index spot

```python
import asyncio
from ib_async import IB, Index

async def main() -> None:
    ib = IB()
    await ib.connectAsync(host="127.0.0.1", port=7497, clientId=1)

    spx = Index(symbol="SPX", exchange="CBOE", currency="USD")
    [spx] = await ib.qualifyContractsAsync(spx)  # fills conId, etc.

    ticker = ib.reqMktData(spx, "", snapshot=False, regulatorySnapshot=False)

    # Poll the Ticker — it auto-updates on every tick.
    for _ in range(10):
        await asyncio.sleep(1)
        # Use last if present, else (bid+ask)/2 — indices typically only carry last
        print(f"SPX last={ticker.last}  close={ticker.close}  time={ticker.time}")

    ib.cancelMktData(spx)
    ib.disconnect()

asyncio.run(main())
```

### 8.2 Subscribe to VIX index spot

```python
from ib_async import Index
vix = Index(symbol="VIX", exchange="CBOE", currency="USD")
[vix] = await ib.qualifyContractsAsync(vix)
vix_ticker = ib.reqMktData(vix, "", False, False)
```

### 8.3 Build SPX 0DTE option chain

```python
from datetime import date

# 1. Get the chain parameters
chains = await ib.reqSecDefOptParamsAsync(
    underlyingSymbol="SPX",
    futFopExchange="",
    underlyingSecType="IND",
    underlyingConId=spx.conId,
)

# 2. Filter to SPXW (PM-settled weekly/daily) on SMART exchange
spxw_chain = next(
    c for c in chains
    if c.tradingClass == "SPXW" and c.exchange == "SMART"
)

# 3. Today's expiration in YYYYMMDD form
today_yyyymmdd = date.today().strftime("%Y%m%d")
assert today_yyyymmdd in spxw_chain.expirations, "No 0DTE expiry today"

# 4. Strike window around current SPX spot (±5%)
spot = ticker.last or ticker.close
strike_lo, strike_hi = spot * 0.95, spot * 1.05
strikes = sorted(s for s in spxw_chain.strikes if strike_lo <= s <= strike_hi)
```

### 8.4 Subscribe to greeks for 80 strikes

```python
from ib_async import Option

# Build the working set: ~40 puts + 40 calls
working_set = [
    Option(
        symbol="SPX",
        lastTradeDateOrContractMonth=today_yyyymmdd,
        strike=k,
        right=r,
        exchange="SMART",
        tradingClass="SPXW",
        currency="USD",
    )
    for k in strikes
    for r in ("P", "C")
]

# Qualify in one batched RPC; drops phantoms
qualified = await ib.qualifyContractsAsync(*working_set)
qualified = [c for c in qualified if c.conId]   # drop unfilled

# Subscribe streaming; 100,101 = call/put volume + open interest
tickers = []
for opt in qualified:
    t = ib.reqMktData(opt, "100,101", snapshot=False, regulatorySnapshot=False)
    tickers.append(t)

# Give IB ~3 seconds to populate
await asyncio.sleep(3)

for t in tickers:
    g = t.modelGreeks
    if g is not None:
        print(
            f"{t.contract.localSymbol}: bid={t.bid} ask={t.ask} "
            f"iv={g.impliedVol:.3f} delta={g.delta:.3f} "
            f"gamma={g.gamma:.4f} theta={g.theta:.3f} "
            f"oi={t.callOpenInterest if t.contract.right=='C' else t.putOpenInterest}"
        )
```

### 8.5 Cancel all subscriptions

```python
# Free the ticker slots when the trade closes / bot shuts down
for t in tickers:
    ib.cancelMktData(t.contract)

ib.cancelMktData(spx)
ib.cancelMktData(vix)
```

### 8.6 Defensive: fail fast on delayed-tick fallback

```python
def assert_realtime(ticker, name: str) -> None:
    """Raise if IB is serving delayed (15-min lag) data — indicates a
    missing market-data subscription that would silently corrupt greeks
    computed against the underlying."""
    # Delayed tick types are 66 (BID), 67 (ASK), 68 (LAST), etc.
    # ib_async surfaces delayed values on .bid/.ask/.last only when the
    # marketDataType flag is set; the cleanest check is via the ticker's
    # ticks list before any subscription begins:
    if ticker.marketDataType in (3, 4):
        raise RuntimeError(
            f"{name} is delivering DELAYED data (marketDataType="
            f"{ticker.marketDataType}). "
            "Subscribe to the real-time feed via Client Portal → "
            "Settings → Market Data Subscriptions."
        )

# Use right after the first tick arrives
await asyncio.sleep(2)
assert_realtime(ticker, "SPX")
assert_realtime(vix_ticker, "VIX")
```

`marketDataType=1` is live, `=2` is frozen (last live snapshot), `=3` is delayed, `=4` is delayed-frozen — see [TWS API: marketDataType callback](https://interactivebrokers.github.io/tws-api/market_data_type.html).

### 8.7 Historical 1-min SPX bars

```python
bars = await ib.reqHistoricalDataAsync(
    contract=spx,
    endDateTime="",                # empty = "now"
    durationStr="1 D",
    barSizeSetting="1 min",
    whatToShow="TRADES",
    useRTH=True,                   # regular trading hours only
    formatDate=1,
    keepUpToDate=False,            # one-shot
)
df = util.df(bars)  # ib_async has a pandas helper
```

Note: SPX is an index — `whatToShow="TRADES"` works for index price series (IB synthesizes "trades" from index value updates). `MIDPOINT` is **not** available on indices. For options, use `whatToShow="MIDPOINT"` or `"BID"` / `"ASK"` separately — `TRADES` on illiquid options is sparse.

---

## 9. Loose ends / things to verify on the live IB sandbox before cutover

1. **Exact monthly cost of the CBOE Streaming Market Indexes line item** — neither IBKR's pricing page nor the supa.is 2026 guide itemizes it; subscribe via Client Portal and confirm the prorated charge on the first invoice. Budget placeholder: $5 / mo (May 2026).
2. **CP API WebSocket subscription cap** — interactivebrokers.github.io/cpwebapi cites 5 concurrent; some community sources say higher. If we ever route streaming through CP API instead of TWS API, verify with a live sandbox subscription burst before committing to that architecture.
3. **Ticker concurrency under our actual commission profile** — the entitlement formula `max(commissions/8, equity×100/1M)` may already grant us >100 lines, which would buy us margin for chain expansion. Run `reqAccountSummary` for "EquityWithLoanValue" + last month's commission total to compute the actual entitlement.
4. **0DTE expiry quirks on Mondays / Wednesdays / Fridays** — SPXW lists daily Mon–Fri, but corporate calendars / half-days can introduce surprise gaps; sanity-check `reqSecDefOptParams` returns a today-dated expiry every morning at 9:25 ET before the entry leg fires.
5. **Greeks-during-halt behavior** — when SPX trades halt (vol-shock LULDs, etc.), IB freezes `tickOptionComputation` but the underlying spot may still tick from the index feed. Greeks computed during this window are stale. Stop monitor should fall back to last-good-greeks + an explicit elapsed-time flag, not panic-exit on stale-greek detection.
6. **Pacing under chain churn** — when SPX rallies 1% during the day, the working strike window shifts, and we cancel ~10 strikes and subscribe ~10 new ones. With `+PACEAPI`, this should be smooth; without it, the 50/sec ceiling could disconnect on a fast pivot. **Always set `+PACEAPI` on connect.**

---

## 10. Sources

- [Market Data Pricing | Interactive Brokers LLC](https://www.interactivebrokers.com/en/pricing/market-data-pricing.php) — canonical IBKR pricing page (page itself was 403-gated to WebFetch in May 2026, content reflected via search snippets)
- [Interactive Brokers Market Data Subscription: Which One Do You Actually Need? (2026 Guide) | Supa.is](https://supa.is/article/interactive-brokers-market-data-subscription-which-one-do-i-need-2026) — published 2026-03-06, the most current single-source pricing summary
- [Cboe SPX | Interactive Brokers LLC](https://www.interactivebrokers.com/en/trading/cboe.php)
- [Cboe Global Indices Feed | Cboe](https://www.cboe.com/us/indices/accessing-index-data/)
- [The Cboe Global Indices Feed (formerly CSMI) | dxFeed](https://dxfeed.com/market-data/indices/cboe-global-indices-feed/)
- [CFE Volatility Futures Learning Center | IBKR](https://www.interactivebrokers.com/cfe/)
- [TWS API v9.72+: Options | interactivebrokers.github.io](https://interactivebrokers.github.io/tws-api/options.html)
- [TWS API v9.72+: Option Greeks | interactivebrokers.github.io](https://interactivebrokers.github.io/tws-api/option_computations.html)
- [TWS API v9.72+: Available Tick Types | interactivebrokers.github.io](https://interactivebrokers.github.io/tws-api/tick_types.html)
- [TWS API v9.72+: Streaming Market Data | interactivebrokers.github.io](https://interactivebrokers.github.io/tws-api/market_data.html)
- [TWS API v9.72+: Top Market Data (Level I) | interactivebrokers.github.io](https://interactivebrokers.github.io/tws-api/top_data.html)
- [TWS API v9.72+: Historical Data Limitations | interactivebrokers.github.io](https://interactivebrokers.github.io/tws-api/historical_limitations.html)
- [TWS API v9.72+: Tick-by-Tick Data | interactivebrokers.github.io](https://interactivebrokers.github.io/tws-api/tick_data.html)
- [Client Portal API Documentation | interactivebrokers.github.io](https://interactivebrokers.github.io/cpwebapi/)
- [Websockets | IBKR Campus](https://www.interactivebrokers.com/campus/trading-lessons/websockets/)
- [Requesting Market Data | IBKR Campus](https://www.interactivebrokers.com/campus/trading-lessons/requesting-market-data/)
- [Web API v1.0 Documentation | IBKR Campus](https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/)
- [Market Data Subscriptions | IBKR Campus](https://www.interactivebrokers.com/campus/ibkr-api-page/market-data-subscriptions/)
- [ib_async on GitHub](https://github.com/ib-api-reloaded/ib_async) — version 2.1.0, June 2025
- [ib_async docs (2.0.1)](https://ib-api-reloaded.github.io/ib_async/)
- [ib_insync 0.9.86 API reference](https://ib-insync.readthedocs.io/api.html)
- [Trading 0DTE Options with the IBKR Native API | IBKR Quant](https://www.interactivebrokers.com/campus/ibkr-quant-news/trading-0dte-options-with-the-ibkr-native-api/)
- [twsapi groups.io — how to get SPX Index data](https://groups.io/g/twsapi/topic/how_to_get_spx_index_data/4047244)
- [polygon.io/pricing — Options product](https://polygon.io/pricing?product=options)
- [polygon.io knowledge base — Greeks for index options](https://polygon.io/knowledge-base/article/does-polygon-support-greeks-for-index-option-contracts)
- [SPXW Options reference | MarketXLS](https://marketxls.com/blog/understanding-spxw-options-a-weekly-sp-500-analysis)
- [Elite Trader — IB message rate and API/CTCI limits](https://www.elitetrader.com/et/threads/ib-message-rate-and-api-ctci-limits.211612/)
- [TWS API 2022 Production Release Notes | ibkrguides.com](https://ibkrguides.com/releasenotes/api/tws/prod-2022.htm)
- [IbkrApi.RateLimiter — hexdocs.pm](https://hexdocs.pm/ibkr_api/IbkrApi.RateLimiter.html)
