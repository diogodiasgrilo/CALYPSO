# 03 — Interactive Brokers: Orders, Positions, Multi-Leg Execution

**Research brief for CALYPSO Saxo→IB migration. Target workload: 0DTE SPX iron-condor bot, 10 contracts/condor at peak (40 short + 40 long option contracts in flight), one-sided entries when Brandon GEX-ADJ skips a side, mark-based 80% TP, debit-spread stop-outs. EUR base account, USD-denominated SPX.**

All cited URLs hit between **2026-05-13** unless noted. The
canonical `interactivebrokers.github.io/tws-api/` docs carry a
deprecation banner directing readers to the IBKR Campus pages (which
mostly 403/redirect to interactivebrokers.com/campus/…). The
underlying API surface is unchanged — the deprecation is editorial,
not protocol — but every fact below is cross-checked against either
the GitHub Pages content, IBKR Campus, or the `ib_async`/`ib_insync`
source.

---

## 1. Order types relevant to 0DTE options

### 1.1 The full catalogue

| Type | API `orderType` | Required fields | Products incl. OPT | Notes |
|---|---|---|---|---|
| Market | `MKT` | `totalQuantity` | yes | No price protection; for 0DTE SPX the spread can be wide enough that MKT is dangerous — use only as fallback in the stop-out path |
| Limit | `LMT` | `lmtPrice`, `totalQuantity` | yes | The workhorse for combo entry/exit |
| Stop | `STP` | `auxPrice` (trigger), `totalQuantity` | yes (incl. `BAG`) | Server-side stop; converts to MKT on trigger |
| Stop-Limit | `STP LMT` | `lmtPrice`, `auxPrice`, `totalQuantity` | yes | Trigger → limit order; can miss in fast markets |
| Market-If-Touched | `MIT` | `auxPrice`, `totalQuantity` | yes | Mirror of STP for take-profit-style triggers |
| Limit-If-Touched | `LIT` | `lmtPrice`, `auxPrice`, `totalQuantity` | yes | Best fit for "fire LMT when mark touches X" |
| Relative / Pegged-to-Primary | `REL` | `lmtPrice` (cap), `auxPrice` (offset) | OPT yes (US only) | Pegs to NBBO with offset; useful for chasing fills on a 2-leg debit close |
| Trailing Stop | `TRAIL` | `trailingPercent` OR `auxPrice`, `trailStopPrice` | yes | Rarely useful on 0DTE — gamma is non-linear |
| Volatility | `VOL` | `volatility`, `volatilityType` | OPT only | Quotes in IV terms; not needed for our flow |
| Pegged-to-Stock | `PEG STK` | `delta`, `stockRefPrice`, `startingPrice` | OPT only | N/A — SPX has no tradable "stock" |
| MOO / LOO | `MKT`/`LMT` + `tif=OPG` | as parent type | yes | N/A — SPX 0DTE doesn't have a meaningful open auction we use |

Source: `interactivebrokers.github.io/tws-api/basic_orders.html` (the
"Order Types" page is the authoritative catalogue and was last
revised under the v9.72+ banner). The page is marked deprecated but
the underlying `orderType` string constants are unchanged in the
current IBKR TWS API 10.x.

### 1.2 Algos — TWAP / VWAP / Adaptive

IBKR-Algo Adaptive (`algoStrategy="Adaptive"`, `algoParams=[("adaptivePriority","Normal")]`) is the only algo worth a serious look for our flow:

- **TWAP** and **VWAP** are designed for equity blocks — they slice a large notional over time. SPX option spreads are not equity-volume problems; they're spread-width problems. Don't use.
- **Adaptive** subtly improves on plain LMT by varying the aggressiveness of the cross over a tunable window (Patient → Urgent). For a 2-leg debit close where we currently "limit at mid, fall back to market after timeout," Adaptive on a `LMT` could collapse those two states into one IBKR-side decision. **Recommend trialling on the stop-out leg only.** Entry IC should stay plain `LMT` because the credit target is the contract.

### 1.3 Time-in-Force

The fully supported TIF values from `Order` class reference (`classIBApi_1_1Order.html`):

| TIF | Meaning | Use for our bot |
|---|---|---|
| `DAY` | Cancel at market close (16:00 ET for SPX) | **Default for every order we send.** 0DTE auto-flattens at expiry anyway, but DAY protects us against a connection-drop leaving stale GTCs |
| `GTC` | Good-til-cancelled (survives session, max ~90d) | **Avoid for 0DTE** — defeats the daily-reset assumption |
| `IOC` | Immediate-or-cancel; partial fills allowed | Useful for "aggressive cross to flatten now" |
| `FOK` | Fill-or-kill; entire qty or nothing | Tempting for combos but on illiquid wings can leave you unable to flatten — prefer IOC |
| `GTD` | Good-til-date; pair with `goodTillDate` (YYYYMMDD HH:MM:SS) | Useful if we want auto-cancel at 15:55 ET to avoid post-close fills |
| `OPG` | Open auction only | N/A for our flow |

### 1.4 Right limit-order pattern for 0DTE SPX

There is no IBKR-blessed answer; this is bot-design choice. Empirically, from the IBKR Quant blog on 0DTE (`interactivebrokers.com/campus/ibkr-quant-news/trading-0dte-options-with-the-ibkr-native-api/`) and how multi-leg desks price these:

- **Combo entry (open IC)**: limit at **net mid** of the four legs, refresh every 5–10s if unfilled, walk price toward bid+ε (for a credit you'd walk *down*) over a 30–60s window. CBOE complex-order book *does* see combo quotes, but liquidity is thin for far-OTM wings — leg-by-leg routing risks chasing one fill while the other moves.
- **TP close (close IC at 80% credit captured)**: limit at net mid of the *debit* to close. Same walk pattern.
- **Stop-out (close 2-leg spread)**: limit at mid + 1 tick (more aggressive — we're trying to exit, not optimise), 10–15s timeout, then fall back to MKT. The bot already does this — preserve the pattern on IB.

Tick size for SPX options is `$0.05` for premiums < $3, `$0.10` for premiums ≥ $3 (CBOE rule — confirm at order placement; IBKR will reject mis-ticked prices with error code 110).

---

## 2. Multi-leg / spread orders — the heart of the migration

### 2.1 BAG construct

A combo order in IBKR is a single `Order` placed against a `Contract`
with `secType="BAG"`. The contract holds a `comboLegs` list. Each
`ComboLeg` references an already-resolved option contract by `conId`
plus a ratio and a side. This is the **only** way to get a single
net-credit limit on a multi-leg order at IBKR.

Source: `interactivebrokers.github.io/tws-api/spread_contracts.html`,
`classIBApi_1_1ComboLeg.html`.

`ComboLeg` fields:

| Field | Type | Meaning |
|---|---|---|
| `conId` | int | The leaf option's IB unique contract ID (resolve via `reqContractDetails` once and cache for the trading day) |
| `ratio` | int | Number of contracts of this leg per "unit" of the combo. For an IC where every leg is 1:1, all four legs get `ratio=1`. The order's `totalQuantity` is the multiplier — 10 contracts means 10× each leg |
| `action` | string | `"BUY"` or `"SELL"`. The combo's net direction (credit vs debit) is *implied* by the legs + `order.action`, not declared separately |
| `exchange` | string | Per-leg routing; use `"SMART"` for SPX so IBKR can route to whichever CBOE venue has the best leg, OR `"CBOE"` to force complex-order-book treatment |
| `openClose` | int | Retail must be `0` (same-as-parent). Institutional accounts get 1/2/3 |
| `shortSaleSlot`, `designatedLocation`, `exemptCode` | — | Stock-only; ignore for options |

### 2.2 The 4-leg iron condor

**Yes — IBKR supports the full 4-leg IC as one BAG order with one net-credit `lmtPrice`.** The combo structure for an IC is the same construct used for vertical credit spreads, butterflies, calendar spreads, etc. (per spread_contracts.html). What changes is the count of legs.

Net-credit convention: when **selling** an IC at a credit (our case), `order.action="SELL"` and `order.lmtPrice` is the **absolute value** of the desired net credit. IBKR interprets that price against the leg actions. *This is the historical source of "error 463 — you must enter a valid price"* documented in chadhumphrey's gist — leg directions and sign of `lmtPrice` have to match the BAG semantics.

### 2.3 SmartRouting limitations for combos

- For options combos on `SMART`, IBKR will attempt complex-order-book execution at CBOE first, falling back to leg-by-leg routing if the combo book has no fill. **This means a "single combo order" can still leg into you** — the fill report will show one fill per leg with different timestamps.
- To *force* combo-book execution (and avoid leg-in risk), set `order.smartComboRoutingParams = [("NonGuaranteed", "0")]`. Default is `"1"` (non-guaranteed → SmartRouting may leg). Set to `"0"` for **guaranteed** combo execution — fills only as an atomic combo, but reduces fill probability.
- Per-leg `exchange="CBOE"` on every `ComboLeg` also concentrates routing on the complex-order book.

Recommendation for our bot: **default to non-guaranteed (`"1"`) for entry IC** (we want fills, the legging risk is bounded by the four-way structure), and **guaranteed (`"0"`) for stop-out 2-leg debit closes** (legging into one debit-spread side could leave us net-short a naked option — unacceptable).

### 2.4 SPX vs SPXW vs mini-SPX

| Symbol | Style | Settlement | Expirations | Best for our bot |
|---|---|---|---|---|
| `SPX` | European, cash-settled | A.M. (SOQ) | 3rd Friday + LEAPS | **Avoid** — A.M. settlement means a 0DTE position can't be held to expiration cleanly on the actual expiry day |
| `SPXW` | European, cash-settled | P.M. (close of regular trading) | Mon/Tue/Wed/Thu/Fri + 3rd-Fri + LTD-of-month | **Use this.** All 0DTE flow you've described |
| `XSP` (mini-SPX) | European, cash-settled | P.M. | weekly | 1/10 the notional; only relevant if we want sub-10c position sizing later |

Source: CBOE SPX Weeklys spec sheet
(`cboe.com/tradable_products/sp_500/spx_weekly_options/specifications/`)
and IBKR Cboe SPX page
(`interactivebrokers.com/en/trading/cboe.php`). Cash settlement means
**no early assignment risk and no share delivery** — operationally
simpler than equity options.

### 2.5 Code: building the IC BAG in `ib_async`

```python
from ib_async import IB, Index, Option, Bag, ComboLeg, LimitOrder

ib = IB()
ib.connect("127.0.0.1", 7497, clientId=11)  # paper port

# 1. Resolve the four leaf option conIds via qualifyContracts
exp = "20260513"          # YYYYMMDD, today for 0DTE
spx_spot = 5800            # use a live snapshot in production
width = 25
short_put_k  = spx_spot - 50
long_put_k   = short_put_k  - width
short_call_k = spx_spot + 50
long_call_k  = short_call_k + width

legs_spec = [
    Option("SPX", exp, short_put_k,  "P", "SMART", tradingClass="SPXW"),
    Option("SPX", exp, long_put_k,   "P", "SMART", tradingClass="SPXW"),
    Option("SPX", exp, short_call_k, "C", "SMART", tradingClass="SPXW"),
    Option("SPX", exp, long_call_k,  "C", "SMART", tradingClass="SPXW"),
]
ib.qualifyContracts(*legs_spec)   # populates .conId

# 2. Build BAG
bag = Bag(
    symbol="SPX",
    exchange="SMART",
    currency="USD",
    comboLegs=[
        ComboLeg(conId=legs_spec[0].conId, ratio=1, action="SELL", exchange="SMART"),
        ComboLeg(conId=legs_spec[1].conId, ratio=1, action="BUY",  exchange="SMART"),
        ComboLeg(conId=legs_spec[2].conId, ratio=1, action="SELL", exchange="SMART"),
        ComboLeg(conId=legs_spec[3].conId, ratio=1, action="BUY",  exchange="SMART"),
    ],
)

# 3. Place — SELL at $3.00 net credit, 10 contracts (= $3,000 total credit)
order = LimitOrder("SELL", 10, 3.00)
order.tif = "DAY"
order.smartComboRoutingParams = [("NonGuaranteed", "1")]  # accept legging for fill probability
trade = ib.placeOrder(bag, order)

# trade.orderStatus.status will progress: PreSubmitted → Submitted → Filled
```

Sourced from the `ib_insync`/`ib_async` API
(`ib-insync.readthedocs.io/api.html`,
`ib-api-reloaded.github.io/ib_async/`) plus the canonical TWS API
spread_contracts.html. The `tradingClass="SPXW"` is **critical** —
without it, `qualifyContracts` may match the A.M.-settled `SPX`
contract with the same strike/expiry and your bot trades the wrong
underlying.

---

## 3. Order placement & lifecycle

### 3.1 `nextValidId` and `orderId` assignment

- TWS API issues sequential order IDs scoped to the **TWS session + clientId**. On connect, the client receives a `nextValidId` event with the starting ID.
- `reqIds(-1)` re-requests it. `ib_async` exposes `ib.client.getReqId()` which auto-increments locally.
- For multiple concurrent clients on the same TWS, use IDs **strictly greater** than any ID seen via `openOrder`/`orderStatus` from any client.
- IDs persist across TWS sessions; can be reset in API Settings only when no live orders exist.

Source: `interactivebrokers.github.io/tws-api/order_submission.html`.

For our bot: clientId 1 = production live, clientId 2 = paper sandbox, clientId 3 = monitor-only read-side. Reserve a fourth for a manual-intervention console.

### 3.2 Order status callback values

Full list from `order_submission.html` and confirmed against the groups.io thread:

| Status | Meaning | Terminal? |
|---|---|---|
| `ApiPending` | Created locally, not yet sent (e.g. `transmit=False`) | no |
| `PendingSubmit` | Sent to IB, awaiting acknowledgement | no |
| `PendingCancel` | Cancel requested, awaiting confirmation | no |
| `PreSubmitted` | Simulated order (STP, LIT, MIT, etc.) accepted, awaiting trigger | no |
| `Submitted` | Live and working at destination exchange | no |
| `ApiCancelled` | Cancelled via API before broker accepted | yes |
| `Cancelled` | Confirmed cancelled by IB | yes |
| `Filled` | Completely executed | yes |
| `Inactive` | Rejected, errored, or blocked (read `whyHeld`/error code for cause) | yes (usually) |

Callback signature (`orderStatus`): `(orderId, status, filled, remaining, avgFillPrice, permId, parentId, lastFillPrice, clientId, whyHeld, mktCapPrice)`.

`ib_async` wraps all of this into the `Trade` object — `trade.orderStatus.status`, `trade.fills`, `trade.log`. Subscribe to `trade.filledEvent`, `trade.statusEvent`, `trade.cancelledEvent` for callbacks.

**Operational caveat**: duplicate orderStatus callbacks are normal. Fast MKT orders may bypass intermediate states. Always treat fills as authoritative via `execDetails` / `trade.fills`, not just `orderStatus.filled`.

### 3.3 How does a 4-leg combo report fills?

Two regimes:

1. **Combo-book fill (CBOE complex order book, or `NonGuaranteed=0`)**: the order fills as a unit. `orderStatus.filled` jumps from 0 to `totalQuantity` in one step. `execDetails` emits **one `Execution` per leg** but with identical `permId` and very close timestamps. `commissionReport` reports four commissions, one per leg.

2. **Legged-in fill (`NonGuaranteed=1`)**: legs fill independently. You can see `orderStatus.filled` partially-fill in stages, or one leg fill at NBBO while another sits at the limit. Critical for risk management: until **all four** legs are filled, your position is *not* an iron condor — it could be naked short an option for seconds. Mitigation: short timeout (e.g. 15s) + abort flow that cancels remaining legs and uses MKT to close any partially-filled leg.

### 3.4 Modifying a live limit order

Per `interactivebrokers.github.io/tws-api/modifying_orders.html`:

- Modification = **cancel/replace** internally. Affects exchange queue position.
- Safe fields to modify: `lmtPrice`, `totalQuantity`, `tif` (only `DAY → IOC`).
- For anything else (`orderType`, `auxPrice` on a STP, combo legs) — **cancel and re-place**.
- Must use the same `clientId` that submitted the order. clientId 0 is the only one that can modify TWS-manual orders.
- Race: if the order partially fills between your modify request and TWS handling it, the modify applies to the *remaining* quantity. Watch `orderStatus.remaining` after every modify.

Pattern: send `placeOrder(contract, order)` with the **same `orderId`** and updated fields. `ib_async` exposes this as the same `IB.placeOrder(...)` call.

### 3.5 `cancelOrder` semantics

- `ib.cancelOrder(order)` returns the same `Trade` object. The cancel is acked via `orderStatus.status="PendingCancel"` → `"Cancelled"`.
- If the order is **already filled** when cancel arrives: IBKR returns error 161 ("Cancel attempted when order is not in a cancellable state. OrderPermId = …"). The `Trade` will still show `Filled` — no double-execution risk, but our code needs to swallow 161 gracefully.
- If the order is **partially filled** and cancel succeeds: remaining quantity cancels; filled quantity remains as a position. Standard race-management.
- `reqGlobalCancel()` cancels **everything**, including TWS-manually-placed orders. Useful as a panic button but never as part of normal flow.

Sources: `cancel_order.html`, `ib-api-reloaded/ib_async` discussion #76.

---

## 4. Bracket orders / OCO

### 4.1 Bracket construct (parent + TP + stop)

Standard IBKR bracket = three linked orders sharing a `parentId`:

```python
parent = LimitOrder("SELL", 10, 3.00)
parent.orderId = ib.client.getReqId()
parent.transmit = False

tp = LimitOrder("BUY", 10, 0.60)        # close at 80% credit captured = 20% of 3.00
tp.orderId = parent.orderId + 1
tp.parentId = parent.orderId
tp.transmit = False

stop = StopOrder("BUY", 10, 6.00)       # debit-to-close stop at 2× credit
stop.orderId = parent.orderId + 2
stop.parentId = parent.orderId
stop.transmit = True                     # last child transmits the whole bundle

ib.placeOrder(bag, parent)
ib.placeOrder(bag, tp)
ib.placeOrder(bag, stop)
```

The TP and STOP form an implicit OCA group rooted at the parent — when one fills, IBKR cancels the other.

Source: `interactivebrokers.github.io/tws-api/bracket_order.html`.

### 4.2 Are brackets useful for our case?

**Partially.** Our current Python-side stop-out logic does three things IBKR brackets *can't* do server-side:

1. **Mark-based trigger** (TP at 80% of *captured credit measured at the mid*, not at a fixed price). IBKR `STP` triggers on last-trade price. For 0DTE combos with thin trades, mark vs last can diverge by 5–10 ticks.
2. **One-sided stop-outs when Brandon GEX-ADJ skips a side** — bracket can't dynamically re-shape after entry.
3. **Stop-out via 2-leg debit spread close** rather than as a combo-buyback at 6.00 — the bracket would close the whole IC, not just the broken side.

**Recommendation**: keep the Python-side trigger logic. Optionally use a **single safety-net STP bracket child** at, say, 4× credit (catastrophic-loss floor) — purely as a "if my bot crashes, IBKR still flattens" backstop. Don't rely on it for normal TP/stop.

### 4.3 OCA (One-Cancels-All) groups

`order.ocaGroup = "my-group-id"` + `order.ocaType` (1 = cancel with block, 2 = reduce with block, 3 = reduce non-block). Use 1 for true mutual exclusion. Brackets use this internally; you rarely need to set it manually.

---

## 5. Positions & P&L queries

### 5.1 Positions

| Call | Returns | When to use |
|---|---|---|
| `ib.positions(account="")` | List of `Position(account, contract, position, avgCost)` | Sync; one-shot snapshot |
| `ib.reqPositions()` | Streams `position` callbacks | Long-lived subscription |
| `ib.reqPositionsMulti(reqId, account, modelCode)` | Multi-account streaming | FA / multi-account; not us |

Filter for SPX option positions:

```python
spx_positions = [
    p for p in ib.positions()
    if p.contract.secType == "OPT" and p.contract.symbol == "SPX"
]
```

`Position.avgCost` for short options is **negative of the credit per contract × multiplier** — e.g. selling SPX 5800P at $3.00 shows `avgCost = -300.0`.

### 5.2 Account summary / margin

`ib.accountSummary(account="")` blocks-then-streams a list of `AccountValue(account, tag, value, currency)`. Tags relevant to us (`account_summary.html`):

| Tag | Meaning | For our bot |
|---|---|---|
| `NetLiquidation` | Total account equity | Health-check |
| `AvailableFunds` | Funds available for new orders | **Pre-trade gate** — block new IC if below threshold |
| `BuyingPower` | RegT buying power | Less useful for spreads than AvailableFunds |
| `ExcessLiquidity` | Funds above maintenance margin | **Live risk gauge** — alert if drops below buffer |
| `InitMarginReq` | Initial margin held | What new orders consume |
| `MaintMarginReq` | Maintenance margin held | What positions consume continuously |
| `FullInitMarginReq` / `FullMaintMarginReq` | Same, full account (vs `Full` excluding lookahead) | Use these — `Full*` is the real number |
| `Cushion` | (NLV - MaintMargin) / NLV | Auto-liquidation if this hits 0 |
| `LookAheadAvailableFunds` | What AvailableFunds will be at next margin-change time | Useful pre-open, less so intraday |
| `DayTradesRemaining` | PDT counter | Index options aren't subject to PDT, but the counter exists |
| `$LEDGER:USD` | Restrict response to USD ledger only | **Critical for EUR-base account** |

For our EUR-base account trading USD SPX, request `$LEDGER:USD` to see USD-cash and USD-margin in isolation. The base-currency `AvailableFunds` is auto-converted at IBKR's snapshot FX rate and can mislead you about how much actual USD margin headroom you have.

### 5.3 Executions & fills

`ib.reqExecutions(execFilter=None)` returns historical fills. Filter by `clientId`, `symbol`, `time` (YYYYMMDD-HH:MM:SS). For real-time fill streaming, subscribe to `IB.execDetailsEvent` or `Trade.filledEvent` on each placed Trade. `commissionReport` event delivers final commissions a few seconds after fill — store *that*, not the pre-trade estimate from `OrderState.commission`.

### 5.4 P&L

| Call | Granularity |
|---|---|
| `ib.reqPnL(account, modelCode="")` | Account-level real-time P&L |
| `ib.reqPnLSingle(account, modelCode, conId)` | Per-position P&L |

For our bot the per-leg PnLSingle is overkill — account-level reqPnL + reconciling against `positions()` is enough.

### 5.5 Client Portal (CP) API equivalents

For the REST-based CP API (separate from TWS API; useful only if you want HTTP rather than socket):

- `GET /portfolio/{accountId}/positions/{pageId}`
- `GET /portfolio/{accountId}/summary`
- `GET /iserver/account/orders` for open orders
- `POST /iserver/account/{accountId}/orders` for placement

CP API uses a server-assigned `orderId` returned in the response (no `reqIds`/`nextValidId` dance). Slower than the socket API. **Recommend sticking with TWS API + `ib_async`** for our latency-sensitive flow — CP API is more for portfolio dashboards.

---

## 6. Account & margin (Reg-T retail US)

### 6.1 Reg-T iron condor formula

Per the IBKR KB article on iron-condor margin (linked via
`ibkrguides.com/kb/article-600.htm`, redirects to
`interactivebrokers.com/lib/cstools/faq/#/content/1163244548`), the
Reg-T initial-margin requirement for an iron condor is:

```
Margin per IC = max(call_spread_width, put_spread_width) × $100 × contracts
              − net_credit_received × $100 × contracts
```

For our 25-point-wide IC at $3.00 credit, 10 contracts:

```
margin = 25 × 100 × 10  −  3.00 × 100 × 10
       = 25,000         −  3,000
       = $22,000 USD per IC at peak
```

IBKR's "max loss" framing is identical: if both spreads were equal width, only one side can lose at expiration (the index can't be simultaneously above the call wing and below the put wing), so margin = one spread's max loss − credit.

### 6.2 0DTE-specific surcharge?

**No special 0DTE Reg-T surcharge.** Same formula applies. *However*, IBKR's **house margin** can exceed rule-based margin, and they reserve the right to bump house margin near earnings, FOMC, etc. The Margin Requirements Wizard in the IBKR Portal will show your live number. Code defensively: read `FullInitMarginReq` and `FullMaintMarginReq` before each new IC, don't rely on the static formula.

### 6.3 Portfolio Margin

Eligibility: ≥ $110K NLV (US retail). Once enabled, an SPX IC at the same strikes typically requires **40–60% less margin** because the model recognises the index hedge between the call and put sides. If account size permits, Portfolio Margin is the single biggest capital-efficiency lever we have. **Not assumed in the migration plan**; treat as a stretch goal.

### 6.4 EUR base trading USD SPX

- Base-currency margin: IBKR converts every USD-denominated margin requirement to EUR at the daily reference FX rate. Your EUR `AvailableFunds` reflects USD-margin-needed × FX_rate.
- **Margin loan in USD is allowed** — you don't have to pre-convert EUR→USD to short an SPX spread. IBKR auto-borrows USD against your EUR collateral. Interest accrues on the negative USD balance (BM ≈ Fed-funds + 1.5% for retail).
- For live USD-margin-available: `accountSummary` with `$LEDGER:USD` tag returns the USD-isolated view. Use `AvailableFunds` from that response.
- FX volatility risk: if EUR weakens vs USD intraday, your EUR-denominated margin requirement *grows* even with no position change. Build a 10% FX buffer into the `AvailableFunds` gate.

---

## 7. Commissions & fees for SPX options

### 7.1 IBKR plans

| Plan | Base per contract | Volume tiers? | Exchange fees / ORF / OCC | Min per order |
|---|---|---|---|---|
| **Fixed (IBKR Pro Fixed)** | $0.65 / contract | None | Included in $0.65 | $1.00 |
| **Tiered (IBKR Pro Tiered)** | $0.15 – $0.65 by monthly volume | Yes (see below) | Pass-through (separate) | $1.00 |
| **IBKR Lite** | $0.65 fixed for first 1,000/month, then auto-tiered | partial | depends | $1.00 |

Tiered monthly-volume breakpoints (per IBKR's commissions-options page; marginal application within a calendar month):

| Monthly volume (contracts) | Per-contract |
|---|---|
| ≤ 10,000 | $0.65 |
| 10,001 – 50,000 | $0.50 |
| 50,001 – 100,000 | $0.25 |
| > 100,000 | $0.15 |

### 7.2 Pass-through fees (Tiered only; Fixed absorbs these)

| Fee | Rate | Notes |
|---|---|---|
| ORF (Options Regulatory Fee) | $0.0023 / contract / side | Cboe rate Jan 2 2026 – Jun 30 2026; reverts to $0.0017 after sunset. Source: SEC filing `2025-23517` |
| OCC Clearing | $0.02 / contract for ≤ 2,750 contracts; capped above | OCC sets this, not IBKR |
| SEC Fee | $27.80 / $1M of sell-side notional (2025 rate; check annually) | Only on sells |
| FINRA Trading Activity Fee | $0.00279 / contract (sell side, capped at $8.30/trade) | |
| CBOE per-contract exchange fee | Varies by class. SPX: typically $0.45–$0.55/contract for non-Customer; **$0 for retail Customer orders** (CBOE Customer-rebate program) | This is the big asymmetry — retail SPX customers often have *negative* effective exchange cost via the Customer rebate |

### 7.3 Worst-case math: 10c iron condor, round trip

**Per IC (open + close = 8 legs total, 10 contracts each = 80 contract-fills)**

Fixed plan (simplest):
- 80 × $0.65 = **$52.00 / round trip**
- Plus the $1 minimum is irrelevant — we're way above it
- All-in cost: $52.00

Tiered plan (assuming we stay at ≤ 10K monthly = $0.65 base):
- IBKR commission: 80 × $0.65 = $52.00
- ORF: 80 × $0.0023 = $0.18
- OCC clearing: 80 × $0.02 = $1.60
- CBOE exchange (Customer rebate): typically $0 to slight rebate
- SEC + FINRA on sell legs (4 legs × 10 = 40 sells): negligible (< $0.50)
- All-in: **~$54.30 / round trip**

Tiered is slightly worse at our current volume. **Fixed wins until we hit ~10K contracts/month** (~250 trading days × 40 contracts/day = 10K). At our peak 10c IC peak we'd hit 80 contracts/day round-trip → ~16K/month → Tiered's $0.50 bracket would start to pay back.

**Recommendation**: start on **Fixed** for predictability, switch to Tiered after a quarter of live data confirms monthly volume.

### 7.4 SPX cash-settled — assignment fee?

No. Cash-settled European options don't generate an assignment event in the traditional sense — at expiry, any in-the-money contract is *exercised* by OCC and IBKR credits/debits the cash settlement. **IBKR does not charge an exercise/assignment fee on SPX cash settlement.** The $0.15 exercise / $0.50 assignment fees referenced elsewhere apply to *equity* options.

---

## 8. Practical Python snippets (current `ib_async` API)

```python
from ib_async import IB, Index, Option, Bag, ComboLeg, LimitOrder, MarketOrder

ib = IB()
ib.connect("127.0.0.1", 7497, clientId=11)   # paper

# ---------- 1. Place 4-leg SPX iron condor at $3.00 net credit, 10 ICs ----------
exp = "20260513"
legs = [
    Option("SPX", exp, 5750, "P", "SMART", tradingClass="SPXW"),  # short put
    Option("SPX", exp, 5725, "P", "SMART", tradingClass="SPXW"),  # long put
    Option("SPX", exp, 5850, "C", "SMART", tradingClass="SPXW"),  # short call
    Option("SPX", exp, 5875, "C", "SMART", tradingClass="SPXW"),  # long call
]
ib.qualifyContracts(*legs)

bag = Bag(
    symbol="SPX", exchange="SMART", currency="USD",
    comboLegs=[
        ComboLeg(conId=legs[0].conId, ratio=1, action="SELL", exchange="SMART"),
        ComboLeg(conId=legs[1].conId, ratio=1, action="BUY",  exchange="SMART"),
        ComboLeg(conId=legs[2].conId, ratio=1, action="SELL", exchange="SMART"),
        ComboLeg(conId=legs[3].conId, ratio=1, action="BUY",  exchange="SMART"),
    ],
)
entry = LimitOrder("SELL", 10, 3.00)
entry.tif = "DAY"
entry.smartComboRoutingParams = [("NonGuaranteed", "1")]
ic_trade = ib.placeOrder(bag, entry)

# wait up to 30s for fill
ib.waitOnUpdate(timeout=30)
print(ic_trade.orderStatus.status, ic_trade.fills)

# ---------- 2. Cancel a working limit order ----------
if ic_trade.orderStatus.status in ("PreSubmitted", "Submitted"):
    ib.cancelOrder(ic_trade.order)

# ---------- 3. Close the call side only (2-leg debit spread) ----------
call_side_bag = Bag(
    symbol="SPX", exchange="SMART", currency="USD",
    comboLegs=[
        # to close: BUY the short, SELL the long
        ComboLeg(conId=legs[2].conId, ratio=1, action="BUY",  exchange="SMART"),
        ComboLeg(conId=legs[3].conId, ratio=1, action="SELL", exchange="SMART"),
    ],
)
close_call = LimitOrder("BUY", 10, 0.40)          # debit to close
close_call.tif = "DAY"
close_call.smartComboRoutingParams = [("NonGuaranteed", "0")]   # guaranteed — avoid legging out
close_trade = ib.placeOrder(call_side_bag, close_call)

# ---------- 4. Query open SPX positions ----------
open_spx = [
    p for p in ib.positions()
    if p.contract.secType == "OPT" and p.contract.symbol == "SPX"
]
for p in open_spx:
    c = p.contract
    print(f"{c.lastTradeDateOrContractMonth} {c.strike}{c.right} qty={p.position} avg={p.avgCost}")

# ---------- 5. Get USD-isolated available margin (EUR base account) ----------
acct_summary = ib.accountSummary(account="")
usd_funds = next(
    (v for v in acct_summary if v.tag == "AvailableFunds" and v.currency == "USD"),
    None,
)
print("USD AvailableFunds:", usd_funds.value if usd_funds else "N/A")
```

All four snippets compile against `ib_async` ≥ 2.1.0 (current as of
2026-05-13 per `ib-api-reloaded.github.io/ib_async/`). The `ib_insync`
0.9.86 API is signature-compatible — substitute `from ib_insync
import ...` if you stay on the legacy lib.

---

## 9. Paper trading

- **Ports**: TWS Live = 7496, TWS Paper = 7497, IB Gateway Live = 4001, IB Gateway Paper = 4002. (TWS API docs `initial_setup.html`; AmiBroker forum thread.)
- **Side-by-side**: yes — connect one `IB` instance to `:7496, clientId=1` (live) and another to `:7497, clientId=2` (paper) in the same Python process. They get independent order ID spaces and account streams.
- **Behavioural differences**:
  - Paper combo fills are **simulated** — IBKR doesn't actually send the combo to a real exchange. The simulation assumes mid-price fills with some randomisation; **real combos often fill worse**. Don't tune fill-rate parameters on paper.
  - Paper market data is delayed unless you have a paid feed; tick streams may lag 15–20 min. Forces all "is my mid sensible" testing to use live data on the live account.
  - Paper account margin is generally accurate but house-margin overrides may not replicate.
  - Paper accounts auto-reset to $1M periodically — don't build long-running P&L expectations.

For our migration: connect bot to paper for ≥ 2 weeks of dry-run; specifically verify (a) combo-order rejection codes, (b) status-callback timing, (c) `accountSummary` USD-ledger fields. Then flip to live with same clientIds incremented (1→11 live).

---

## 10. Reliability & race conditions

- **Disconnection**: orders placed via the API persist on IBKR's broker-side books. If `ib_async` disconnects while an IC is working, the order remains live (subject to `tif=DAY` expiring at 16:00 ET). On reconnect, `ib.reqOpenOrders()` or `ib.reqAllOpenOrders()` resyncs state. **This is essential for our 24/7 monitoring** — a connection blip during US market hours doesn't lose orders, but if the bot was about to cancel-on-timeout, it now has to reconcile.
- **`reqIds`**: don't share an ID space across clientIds. Each clientId has its own. Use clientId 0 if and only if you also need to bind/modify TWS-manual orders.
- **Duplicate order prevention**: IBKR rejects duplicate orderIds within a session (error 103). Our bot must persist the last-used orderId to disk so a crash-restart doesn't recycle IDs. `ib_async`'s `getReqId()` reads the latest seen value on connect, but only for that connection — explicit persistence is safer.
- **`ApiCancelled` vs `Cancelled`**: `ApiCancelled` means the client (us) cancelled before IBKR ever acked the order. `Cancelled` means IBKR confirmed cancellation. Both are terminal; treat identically.
- **Phantom fills**: a stale `orderStatus` callback can arrive *after* a `Cancelled` status. Always treat the latest-timestamp callback as authoritative, but never re-place an order on the basis of a "non-terminal" status without checking `Trade.isDone()`.
- **Error 161** (cancel-when-not-cancellable): handle as a "no-op + treat order as already terminal." Don't retry the cancel.
- **Margin-call auto-liquidation**: IBKR will auto-liquidate when `Cushion` ≈ 0. The bot must subscribe to `accountSummary` and pre-emptively flatten if Cushion < 0.05. IBKR liquidates *in random order*, possibly closing the long-wing legs first and leaving us naked short — catastrophic for an IC.

---

## Sources (canonical primary references; all retrieved 2026-05-13)

- TWS API order types — `interactivebrokers.github.io/tws-api/basic_orders.html`
- Spread / combo contracts — `interactivebrokers.github.io/tws-api/spread_contracts.html`
- `ComboLeg` class — `interactivebrokers.github.io/tws-api/classIBApi_1_1ComboLeg.html`
- `Order` class — `interactivebrokers.github.io/tws-api/classIBApi_1_1Order.html`
- Placing orders + status callbacks — `interactivebrokers.github.io/tws-api/order_submission.html`
- Modifying orders — `interactivebrokers.github.io/tws-api/modifying_orders.html`
- Cancelling orders — `interactivebrokers.github.io/tws-api/cancel_order.html`
- Bracket orders — `interactivebrokers.github.io/tws-api/bracket_order.html`
- Positions — `interactivebrokers.github.io/tws-api/positions.html`
- Account summary — `interactivebrokers.github.io/tws-api/account_summary.html`
- IBKR Campus 0DTE Quant blog — `interactivebrokers.com/campus/ibkr-quant-news/trading-0dte-options-with-the-ibkr-native-api/`
- CBOE SPX Weeklys spec — `cboe.com/tradable_products/sp_500/spx_weekly_options/specifications/`
- CBOE Options Regulatory Fee update Jan 2 2026 — `cdn.cboe.com/resources/fee_schedule/2026/Cboe-Options-Exchanges-Regulatory-Fee-Update-Effective-January-2-2026.pdf`
- IBKR options commissions — `interactivebrokers.com/en/pricing/commissions-options.php`
- IBKR iron condor margin KB — `interactivebrokers.com/lib/cstools/faq/#/content/1163244548`
- IBKR base currency — `interactivebrokers.com/campus/glossary-terms/base-currency/`
- `ib_async` 2.1.0 docs — `ib-api-reloaded.github.io/ib_async/`
- `ib_async` GitHub — `github.com/ib-api-reloaded/ib_async`
- `ib_insync` API reference — `ib-insync.readthedocs.io/api.html`
- chadhumphrey IB combo gist — `gist.github.com/chadhumphrey/872a9d8d7e1619b974754fc1d9b6fd05`

**Doc currency note**: the `interactivebrokers.github.io/tws-api/`
pages carry a deprecation banner. The deprecation is editorial only —
the underlying TWS API socket protocol they describe (v9.72+, current
through 10.x as of 2026-05) is what `ib_async` 2.1 actually targets,
so the field semantics in this brief remain correct. When a fact
matters operationally (commission rates, ORF, margin rule changes),
re-verify against the live `interactivebrokers.com/campus/` or
`cboe.com/us/options/notices/` page just before go-live.
