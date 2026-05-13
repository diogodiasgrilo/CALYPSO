# Interactive Brokers API — Reference for CALYPSO

> **⚠️ ARCHITECTURE UPDATE 2026-05-13 (afternoon)**: The TL;DR table below shows the ORIGINAL recommendation (IB Gateway + `ib_async` + IBC). **Follow-up research [resolved the 6 open questions](./IB_OPEN_QUESTIONS_ANSWERED.md) and changed this**: the practical retail path is **OAuth 1.0a "Extended" first-party via `ibind`** — fully headless, no Gateway, no IBC, no weekly phone tap. The Gateway path remains documented as a fallback if IBKR ever closes OAuth 1.0a self-service to retail. **Read `IB_OPEN_QUESTIONS_ANSWERED.md` first** for the post-research architecture.
>
> **Status**: research-only reference (no code changes yet). Compiled 2026-05-13 from 5 parallel research agents covering API landscape, market data, orders/positions, production deployment, and SPX 0DTE edge cases. Cite primary IBKR docs at every decision point; this is a navigation index plus the cross-cutting decisions, not a replacement for the source documentation. Re-verify dollar figures, subscription names, and version numbers before any cutover.
>
> **Companion docs**:
> - [`IB_OPEN_QUESTIONS_ANSWERED.md`](./IB_OPEN_QUESTIONS_ANSWERED.md) — **READ FIRST** — answers to the 6 open questions, with the architecture pivot to OAuth 1.0a.
> - [`SAXO_TO_IB_MIGRATION_PLAN.md`](./SAXO_TO_IB_MIGRATION_PLAN.md) — the actual migration sequencing with our Saxo surface mapped to IB calls.
>
> **Deep-dive chapters** (the raw research, kept verbatim under `research_scratch/`):
> 1. [API landscape](./research_scratch/01_ib_api_landscape.md) — every IB API surface, SDK options, auth flows, cost.
> 2. [Market data](./research_scratch/02_ib_market_data.md) — subscriptions, tick types, ticker limits, options chains, Polygon-replacement analysis.
> 3. [Orders & positions](./research_scratch/03_ib_orders_positions.md) — combo/BAG construction, order lifecycle, P&L queries, EUR-base USD-options margin.
> 4. [Production deployment](./research_scratch/04_ib_production_deployment.md) — IB Gateway, IBC, IBeam, Docker, 2FA reality, weekly reset, watchdog.
> 5. [SPX 0DTE edge cases](./research_scratch/05_ib_spx_0dte_edge_cases.md) — SPXW vs SPX, settlement, $0.05 net-credit increments, 4:00 PM ET cutoff, REG-T margin math.

---

## TL;DR — the decisions

| Question | Answer | Why |
|---|---|---|
| Which IB API? | **TWS API via IB Gateway**, driven by `ib_async`, auto-logged in with **IBC** | Lowest-latency for combo orders + streaming. The newer OAuth-2.0 Web API still requires the local Client Portal Gateway for retail live trading per IBKR's own docs (May 2026). Revisit when IBKR ships a gateway-free retail trading endpoint. |
| Which Python SDK? | **`ib_async` 2.1.0** (Dec 2025) | `ib_insync` is **dead** — author Ewald de Wit passed away early 2024, last release Dec 2023. `ib_async` is the maintained fork under `ib-api-reloaded`, re-implements the wire protocol natively (no `ibapi` dependency), supports Python 3.10–3.14. |
| IBKR Pro or Lite? | **Pro, mandatory** | Lite is ineligible for the Web API trading endpoint *and* gets routed to PFOF market makers (no SmartRouting). |
| Auto-login layer? | **IBC** (`IbcAlpha/IBC` 3.23+) | Active fork after original IBController withdrew 2018. Push-2FA via IBKR Mobile. **No fully unattended 2FA path for live retail** — operator must approve one push per week. |
| Gateway flavor? | **IB Gateway** (not full TWS) | 200–400 MB RSS vs 1 GB+, designed for headless. Latest May 2026: 10.45 stable, 10.46 latest. |
| Docker image? | **`ghcr.io/gnzsnz/ib-gateway:10.45.1e`** | Ships bundled IBC, well-maintained, stable. |
| Drop Polygon Options Starter ($29/mo)? | **Yes** — IB OPRA provides streaming bid/ask/last + Model Greeks + per-strike OI in real time, all in one feed. Polygon Starter is 15-min delayed and only useful for historical chain replay (which we don't do live). |
| Stop-loss strategy? | **Keep Python-side credit-based stops** (current pattern) | Native IB stop orders are unreliable on illiquid options. Industry-standard 0DTE approach matches our current code. |
| Bracket orders (server-side TP/stop)? | **No** | IB brackets are useful as a catastrophic-loss safety net only; can't express mark-based TP triggers we use. Keep the logic in Python; consider a wide IB stop as belt-and-suspenders. |
| Combo orders for iron condors? | **Yes — single `BAG` contract with 4 ComboLegs**, one net-credit limit price | Routes to CBOE Complex Order Book. Use `smartComboRoutingParams=[("NonGuaranteed", "1")]` on entry (legging risk accepted for fill probability), `"0"` on stop-out closes (atomic, avoid being left naked short). |

---

## Surface comparison — Saxo OpenAPI vs IBKR options

| Property | Saxo OpenAPI (today) | IBKR TWS API + Gateway | IBKR Web API (OAuth 2.0) |
|---|---|---|---|
| Transport | HTTPS REST + WebSocket | TCP socket (binary) | HTTPS REST + WebSocket |
| Auth | OAuth 2.0 (refresh tokens) | Username + 2FA → gateway session | OAuth 2.0 (`private_key_jwt`) |
| Local process required? | **No** | **Yes** — IB Gateway (Java) on `localhost:4001/4002` | **Yes for retail live trading** — Client Portal Gateway still in front |
| Cloud-native? | Yes | Workable with IBC + Docker | Yes for account/Flex; **no for retail trading** as of May 2026 |
| 2FA cadence on a Linux VM | None (after token issued) | Weekly phone tap | Same — gateway still in path |
| Combo orders | Yes (multi-leg list) | Yes (`BAG` contract, mature) | Yes (mirrors CP API; less battle-tested for ICs) |
| Real-time options chain | Yes (`/trade/v1/infoprices/list`) | Yes (`reqMktData` per contract; 100-ticker cap) | Yes (REST snapshots, 100-conid cap per call) |
| Latency for 0DTE | ~100-300 ms | **~5-50 ms** | ~50-150 ms |
| Maturity of Python SDK | n/a (we hand-rolled) | **High** (`ib_async`, 1500+ ⭐) | Lower (`ibind`, smaller community) |

**Net**: TWS API + Gateway is the most mature, lowest-latency path for our use case. The trade-off is that we trade Saxo's stateless OAuth REST for IBKR's stateful gateway process — meaning more ops surface (Docker, IBC auto-login, watchdog reconnect, weekly 2FA tap), but more capability (better combo support, tighter NBBO, dropped Polygon dependency).

---

## Critical 0DTE-SPX-specific gotchas (read before writing code)

These are the items that will bite the migration if missed. Source: research_scratch/05_*.md.

### 1. **Use `tradingClass='SPXW'`, not `'SPX'`, for 0DTE**

```python
# CORRECT — PM-settled, daily/weekly expiries (this is what 0DTE traders use)
Option(symbol='SPX', lastTradeDateOrContractMonth='20260513',
       strike=5500, right='C', exchange='CBOE', tradingClass='SPXW')

# WRONG — AM-settled, 3rd-Friday monthlies only, NO 0DTE
Option(symbol='SPX', ..., tradingClass='SPX')
```

Two community examples (robotwealth, aicheung/0dte-trader) use `symbol='SPXW' exchange='SMART'` — works but **not canonical**. Always `qualifyContracts()` before trading.

### 2. **0DTE trading stops at 4:00 PM ET sharp**

NOT 4:15 PM ET. The 4:15 number applies only on *non-expiry* days. The hard cutoff for our `_close_all_positions_at_eod` loop must be **3:55 PM ET** to leave a 5-minute buffer for fills.

### 3. **Net-credit must be in $0.05 increments**

CBOE Complex Order Book rejects orders with non-$0.05 limit prices. Round before sending:

```python
def to_cboe_increment(price: float) -> float:
    return round(price * 20) / 20
```

### 4. **100-ticker streaming concurrent cap**

TWS API caps streaming `reqMktData` subscriptions at **100 simultaneous tickers** by default (extendable to 1000+ via paid subscriptions). Our use case:
- SPX + VIX = 2 tickers
- Open option legs in flight (4 per IC × N ICs) = ≤ 28 tickers for a typical day at peak
- **Total ≤ 30 tickers** → comfortably under cap during normal monitoring.

But for a **full chain GEX snapshot** (~500 strikes), we'd exceed the cap. Options:
- Use `snapshot=True` (one-shot, releases the line immediately) — preferred
- Rotate subscriptions
- Drop to CP API `/iserver/marketdata/snapshot` (100 conids per REST call, faster for batch)

### 5. **SPX is cash-settled European → no early assignment risk**

Big simplification vs SPY. We can remove any assignment-monitoring code paths. Settlement is in cash at the index level, T+1. The bot never needs to handle delivery.

### 6. **Stop orders on options are NOT reliable on IB**

Per IBKR docs and forum consensus, native stop orders on options don't trigger reliably during illiquid wings or fast moves. **Keep our credit-based Python stop monitoring** — it's the industry-standard pattern for 0DTE. We can optionally place a wide "catastrophic" stop as a safety net (e.g., 5× expected max loss) — but the live decision logic stays in Python.

### 7. **EUR base account, USD options — read the right field**

For an EUR-base account trading USD SPX options:

```python
# WRONG — gives EUR-equivalent, FX-converted at end-of-day sweep rate
ib.accountSummary()  # 'AvailableFunds' in account base currency

# CORRECT — gives USD-tradable amount, live
ib.accountSummary(account, tags='AvailableFunds,BuyingPower,ExcessLiquidity')
# Then filter for tag with currency='USD' OR use $LEDGER:USD ledger query
```

Use the `$LEDGER:USD` tag with `reqAccountSummary` for the correct currency view.

### 8. **REG-T margin for short option spreads**

`margin = max(call_spread_width, put_spread_width) × $100 × contracts − net_credit_received × $100 × contracts`

For our 10c configs at 5-pt narrow spreads:
- Width 5pt × $100 × 10 = $5,000 per IC (max loss before credit)
- Credit ~$0.30 × 100 × 10 = $300 reduction
- Net margin block: **~$4,700 per IC**

At a $50K account, headroom for ~10 concurrent ICs — generous vs our current 4-slot B + 3-slot C config. Portfolio Margin (50–70% reduction) requires $110K minimum equity — not relevant yet.

### 9. **Order ID is broker-side persistent**

Once `placeOrder` returns, the order lives on **IBKR's books**, not our process. If our bot crashes mid-order:
- The order remains active on IBKR's side
- On reconnect, `reqOpenOrders()` returns it
- We MUST reconcile state on every restart — don't blindly resubmit

This is fundamentally different from Saxo's behavior (where some calls were idempotent client-side). Recovery logic needs a rewrite.

### 10. **Weekly server reset**

Every Sunday from ~midnight ET to ~1 AM ET, IBKR servers are down for maintenance. Connections drop. Gateway must re-authenticate (operator phone tap required for live accounts).

Code must:
- Catch the disconnect via `ib_async.Watchdog`
- Wait for IBKR side to come back up
- Re-authenticate via IBC's `RestartTime` config (e.g., set to `01:30 ET Sunday`)
- Re-subscribe market data
- Re-reconcile open orders

---

## What IB gives us that Saxo doesn't

1. **Mature combo orders** — 4-leg iron condor as one `BAG` contract with one net-credit limit. Saxo required us to roll our own multi-leg order construction with leg-by-leg fills.
2. **Native CBOE Complex Order Book routing** — better fills on SPX combos than leg-by-leg Smart routing.
3. **Tighter NBBO** — IB is on more routes than Saxo, typically 0.05-0.10 tighter on the spread.
4. **Drop Polygon Starter** — IB OPRA gives streaming bid/ask + Model Greeks + OI per strike. Saves $29/mo and eliminates one dependency.
5. **Lower latency** — TCP socket vs Saxo's HTTPS round-trip. 5-50ms vs 100-300ms typical.
6. **Lower commissions** — IBKR Pro tiered ~$0.65/contract vs Saxo's ~$2.50/leg. For a 10c IC: ~$26 vs ~$100 round-trip. **~75% commission reduction.** This is the biggest line-item improvement.
7. **Section 1256 60/40 tax treatment** for SPX index options if we ever become US-tax-resident (currently EU-tax, so this is theoretical).

## What IB takes away that Saxo had

1. **Stateless OAuth REST** — IB requires a stateful gateway process on the VM.
2. **One-tap auth refresh** — Saxo's refresh tokens are scriptable; IB requires a weekly phone tap on the IBKR Mobile app.
3. **Single-endpoint REST simplicity** — IB's TWS API is event-driven with async callbacks; the mental model is heavier.
4. **HTTPS-only firewall surface** — IB Gateway listens on `localhost:4001` (no auth on the socket); we must firewall it to loopback only.

---

## Glossary

| Term | Meaning |
|---|---|
| `ibapi` | Official IBKR Python wire protocol library. Threaded, callback-based. Distributed via the API installer (not PyPI primarily). |
| `ib_insync` | **DEAD.** Async wrapper around `ibapi`, last release Dec 2023. Author Ewald de Wit deceased early 2024. Do not use for new work. |
| `ib_async` | The maintained fork — `ib-api-reloaded` org, maintained by Matt Stancliff. 2.1.0 (Dec 2025). Re-implements the wire protocol natively (no `ibapi` dependency). asyncio-native, also supports sync mode. **The default Python choice.** |
| TWS API | The flagship IB API — TCP socket protocol, requires running TWS or IB Gateway process. |
| IB Gateway | Lightweight headless version of TWS for production deployment. ~200-400 MB RSS. |
| Client Portal Web API / CP API | REST+WebSocket alternative; requires running `clientportal.gw` Java proxy locally. |
| IBKR Web API | Marketing umbrella for the OAuth-2.0-fronted CP API + Account Management + Flex. Beta as of May 2026. Retail trading still goes through CP Gateway. |
| IBC | `IbcAlpha/IBC` — auto-login layer for TWS/Gateway. Handles IBKR Mobile 2FA push approval, daily restart, weekly cold-login. |
| IBeam | Equivalent of IBC for the Client Portal Gateway. Selenium-based, keeps `clientportal.gw` session warm. |
| `BAG` contract | IB's multi-leg combo construct. Used for iron condors (4 legs), call/put spreads (2 legs). |
| ComboLeg | A single leg within a BAG, with `conId`, `ratio`, `action` (BUY/SELL), `exchange`. |
| `clientId` | Numeric identifier per API client connecting to TWS/Gateway. Each connection needs a unique ID; reusing after a dirty disconnect can hang. |
| SmartRouting | IB's order-router that splits orders across exchanges for best execution. Does NOT apply to SPX options — SPX only trades on CBOE. |
| SPXW | Weekly/daily SPX options, PM-settled, European-style. **What 0DTE traders use.** |
| SPX (`tradingClass='SPX'`) | Monthly 3rd-Friday SPX options, AM-settled. **Not 0DTE.** |
| OPRA | Options Price Reporting Authority — the consolidated US options quote feed. $1.50/mo IBKR subscription, waived above $20/mo commissions. |
| `qualifyContracts` | `ib_async` helper that resolves an under-specified `Contract` to its canonical `conId` via IB's reference data. **Always call before trading.** |
| `reqMktData` | TWS API method to subscribe to streaming quotes. Returns ticks via `tickPrice`/`tickSize`/`tickString` callbacks. |
| `reqContractDetails` | Used to look up option strike grids and expiry dates for a given underlying. |
| `reqSecDefOptParams` | Bulk option-chain discovery — returns all strikes/expiries for a given underlying. |
| `whatIfOrder` | Pre-trade margin check — IB returns what the order *would* cost in margin without placing it. Useful for our pre-entry BP gate. |
| `account_value` / `accountSummary` | Account balance queries; need `$LEDGER:USD` tag for currency-specific views. |

---

## Open questions — STATUS UPDATE 2026-05-13 afternoon

**All 6 questions resolved via additional research.** See [`IB_OPEN_QUESTIONS_ANSWERED.md`](./IB_OPEN_QUESTIONS_ANSWERED.md) for the full answers; key items:

1. ✅ **Gateway-free OAuth 2.0 for retail**: NO (institutional/vendor only). **But OAuth 1.0a Extended first-party works gateway-free for retail** — architecture pivot to `ibind`.
2. ✅ **OAuth 2.0 key rotation**: not documented; self-impose 12-month overlap via Message Center tickets.
3. ✅ **Cboe Streaming Market Indexes fee**: ~$1.50/mo non-pro for **VIX only**. **SPX is a separate subscription** (CME S&P Indexes, ~$1.50-3/mo). Total ~$3-5/mo.
4. ✅ **Unattended week+ session**: only possible on the OAuth 1.0a Web API path (no phone tap). Gateway path's Sunday tap is enforced, no documented bypass.
5. ✅ **2026 ORF on SPX**: $0.0023/contract sell-side (effective Jan 2 – Jun 30 2026; reverts to $0.0017 Jul 1 unless extended).
6. ✅ **EUR-base USD-tradable**: `reqAccountSummary(tags="AvailableFunds,$LEDGER:USD")` + compute `EUR_AvailableFunds × ExchangeRate@USD + USD_CashBalance`. **3-minute refresh cadence, not per-fill** — bot must reserve margin client-side.

**Remaining items that DO still need IBKR API support email**:

1. Max concurrent registered public keys per OAuth 2.0 `client_id` (matters only if we ever move from 1.0a to 2.0).
2. Fast-revoke SLA on a compromised public key — same-business-day disable path or only standard Message Center ticket?

---

## Update process for this doc

When IBKR ships material changes (Web API GA, new 2FA flow, pricing change, breaking SDK change):

1. Re-run the 5 research agents (prompts archived under `research_scratch/00_research_prompts.md` — to be added).
2. Update the relevant scratch chapter under `research_scratch/`.
3. Update the TL;DR table + Critical Gotchas section in this doc.
4. Bump the "Compiled" date at the top.

**Last compiled**: 2026-05-13 by 5 parallel general-purpose research agents.
