# 11 — CP API Margin & Account Surface via ibind

**Status:** Research scratch, May 2026
**Last Updated:** 2026-05-14
**Scope:** Pre-trade margin check (whatif), account/balance queries, per-currency
ledger, positions, open orders, order status push, reply prompts — all via
`ibind` 0.1.23 + OAuth 1.0a against Client Portal Web API v1.0 ("CPAPI 1.0").
**Account profile assumed:** IBKR Pro retail, IBIE Ireland, REG-T margin, EUR
base, trading USD-denominated SPX index options as iron condors.

This file replaces the TWS-API equivalents covered in `03_ib_orders_positions.md`
and `08_orf_and_ledger_usd.md`. Where a CP API endpoint behaves identically to
its TWS counterpart we note the parity; where it diverges we flag the gotcha.

---

## 0. Quick references

- Web API v1.0 doc index: <https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/>
- Web API Reference (OpenAPI explorer): <https://www.interactivebrokers.com/campus/ibkr-api-page/webapi-ref/>
- Trading Web API: <https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-trading/>
- ibind GitHub: <https://github.com/Voyz/ibind>
- ibind IbkrClient wiki: <https://github.com/Voyz/ibind/wiki/API-Reference-%E2%80%90-IbkrClient>
- ibind WS client source: <https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_ws_client.py>
- ibind portfolio mixin source: <https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_client_mixins/portfolio_mixin.py>
- ibind order mixin source: <https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_client_mixins/order_mixin.py>
- IB Mobile margin preview (UI mirror of whatif payload): <https://www.interactivebrokers.com/campus/trading-lessons/looking-at-margin-on-ibkr-mobile/>
- IBIE Options margin reference: <https://www.interactivebrokers.ie/en/trading/margin-options.php>
- IBIE base-currency doc: <https://www.ibkrguides.com/clientportal/basecurrency.htm>

---

## 1. Pre-trade margin check — `whatif_order`

### Endpoint

`POST /iserver/account/{accountId}/orders/whatif`

Confirmed in ibind's `OrderMixin.whatif_order`
(<https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_client_mixins/order_mixin.py>):

```python
def whatif_order(self, order_request: OrderRequest, account_id: str = None) -> Result:
    # POST iserver/account/{account_id}/orders/whatif
    # Payload: {'orders': [parsed_order_request]}
```

The payload shape **mirrors** `place_order` exactly. The two endpoints differ
only in URL suffix (`/orders` vs `/orders/whatif`) and the absence of a
question/answer reply loop on the whatif path — IBKR will not prompt for risk
warnings during a pre-trade preview, only when you actually place.
(<https://github.com/Voyz/ibind/wiki/API-Reference-%E2%80%90-IbkrClient>)

### Response shape

The Mobile Preview UI screen is the human-readable mirror of the whatif JSON.
IBKR Campus documents the three columns it renders: **Current**, **Change**,
**Post Trade**, applied to **Equity with Loan**, **Maintenance Margin**, and
**Initial Margin**.
(<https://www.interactivebrokers.com/campus/trading-lessons/looking-at-margin-on-ibkr-mobile/>)

The JSON response from the whatif endpoint, per community-documented responses
(IBKR Campus + scraped OpenAPI):

```json
{
  "amount": {
    "amount": "-1,500 USD",
    "commission": "1.20 USD",
    "total": "-1,498.80 USD"
  },
  "equity": {
    "current":  "1,234,567.89",
    "change":   "-1,498.80",
    "after":    "1,233,069.09"
  },
  "initial": {
    "current":  "12,500.00",
    "change":   "+4,500.00",
    "after":    "17,000.00"
  },
  "maintenance": {
    "current":  "10,000.00",
    "change":   "+4,500.00",
    "after":    "14,500.00"
  },
  "position": {
    "current":  "0",
    "change":   "+10",
    "after":    "10"
  },
  "warn":  "...",
  "error": null
}
```

**Critical:** `amount`, `equity`, `initial`, and `maintenance` are returned as
**strings with embedded currency suffix**, NOT typed numbers. You must parse.

### Currency view — EUR base, USD-denominated trade

The whatif response renders **all margin numbers in the account base
currency**. For our IBIE Ireland EUR-base account, an SPX IC margin will come
back in **EUR**, even though SPX trades in USD. IBKR translates internally
using the same base-currency rule used for statements and margin requirement
determination.
(<https://www.ibkrguides.com/clientportal/basecurrency.htm>)

The translation FX rate is **the same one returned by `/portfolio/.../ledger`**
for the BASE row vs the USD row (more on that in §3).

### Working example — 10-contract SPX iron condor pre-trade check

```python
from ibind import IbkrClient
from ibind.client.ibkr_utils import OrderRequest

client = IbkrClient()  # OAuth 1.0a config from env vars; see ibind README

account_id = "U1234567"  # your IBIE account

# Iron condor: short 4500P/long 4490P, short 5500C/long 5510C
# CP API expresses combos as a single OrderRequest with conid = combo conid
# from /iserver/secdef/strategy. See research_scratch/09_cpapi_combo_orders.md
# for the combo conid plumbing.
ic_combo_conid = 28012345  # placeholder — built via /iserver/secdef/strategy

order = OrderRequest(
    conid=ic_combo_conid,
    side="BUY",          # combos: BUY = open the combo as constructed
    quantity=10,
    order_type="LMT",
    price=1.50,          # net credit limit, in USD (combo quote ccy)
    tif="DAY",
    outside_rth=False,
    account_id=account_id,
)

preview = client.whatif_order(order, account_id=account_id)
# preview.data is the JSON dict shown above

initial_change_eur = _parse_money(preview.data["initial"]["change"])  # parse "+4,500.00"
print(f"Initial margin impact: +{initial_change_eur:.2f} EUR")

equity_after = _parse_money(preview.data["equity"]["after"])
initial_after = _parse_money(preview.data["initial"]["after"])
print(f"Post-trade ELv: {equity_after:.2f} EUR, initial req: {initial_after:.2f} EUR")
if initial_after > equity_after * 0.85:  # our ORDER-004 BP gate
    raise BuyingPowerExceeded()
```

A `_parse_money` helper that strips commas, suffixes, and signs lives in
`bot/ibkr/cp_money.py` (to be written during the migration).

---

## 2. Account summary / portfolio summary

### Two different summaries — pick the right one

CP API exposes **two** summary endpoints, easily confused:

| Endpoint                              | ibind method        | Returns                                 |
|---------------------------------------|---------------------|-----------------------------------------|
| `GET /iserver/account/{id}/summary`   | `account_summary`   | iserver-flavored summary (trading-side) |
| `GET /portfolio/{id}/summary`         | `portfolio_summary` | portfolio-flavored summary              |

Source: ibind wiki
(<https://github.com/Voyz/ibind/wiki/API-Reference-%E2%80%90-IbkrClient>).

For Reg-T-equivalent margin numbers (AvailableFunds, BuyingPower,
ExcessLiquidity, Cushion) the **`/portfolio/{id}/summary`** endpoint is the
canonical mirror of TWS API `reqAccountSummary` `$LEDGER` tags
(<https://interactivebrokers.github.io/tws-api/account_summary.html>;
<https://www.interactivebrokers.com/campus/glossary-terms/excess-liquidity/>).

### Response shape

The portfolio summary returns a flat dict keyed by tag, where each value
carries `amount`, `currency`, `isNull`, `timestamp`, `value`, `severity`:

```json
{
  "accountready":        { "value": "true", "currency": null, "isNull": false, ... },
  "availablefunds":      { "amount": 945123.40, "currency": "EUR", "timestamp": 1715592300, ... },
  "availablefunds-c":    { "amount": 945123.40, "currency": "EUR", ... },
  "availablefunds-s":    { "amount": 940012.10, "currency": "EUR", ... },
  "buyingpower":         { "amount": 3780493.60, "currency": "EUR", ... },
  "cushion":             { "value": "0.92", "currency": null, ... },
  "equitywithloanvalue": { "amount": 1234567.89, "currency": "EUR", ... },
  "excessliquidity":     { "amount": 935123.40, "currency": "EUR", ... },
  "fullavailablefunds":  { "amount": 945123.40, "currency": "EUR", ... },
  "fullexcessliquidity": { "amount": 935123.40, "currency": "EUR", ... },
  "grosspositionvalue":  { "amount": 289443.10, "currency": "EUR", ... },
  "initmarginreq":       { "amount":  12500.00, "currency": "EUR", ... },
  "maintmarginreq":      { "amount":  10000.00, "currency": "EUR", ... },
  "netliquidation":      { "amount": 1234567.89, "currency": "EUR", ... },
  "settledcash":         { "amount": 1100000.00, "currency": "EUR", ... },
  "totalcashvalue":      { "amount": 1100000.00, "currency": "EUR", ... },
  ...
}
```

Field semantics mirror TWS:
- `availablefunds` = ELv − Σ initial margin
  (<https://www.ibkrguides.com/traderworkstation/available-for-trading.htm>)
- `buyingpower` = `availablefunds × 4` for Reg-T margin accounts
  (<https://www.ibkrguides.com/traderworkstation/available-for-trading.htm>)
- `excessliquidity` = ELv − Σ maintenance margin
  (<https://www.interactivebrokers.com/campus/glossary-terms/excess-liquidity/>)
- `cushion` = `excessliquidity / netliquidation` (dimensionless)

**Currency:** every monetary field is reported in **base currency (EUR for us)**.
No USD-denominated rows appear in `/portfolio/.../summary` — for that you need
the ledger (§3).

### ibind call

```python
summary = client.portfolio_summary(account_id)
eur_avail = summary.data["availablefunds"]["amount"]
eur_buying_power = summary.data["buyingpower"]["amount"]
cushion = float(summary.data["cushion"]["value"])
```

### Refresh cadence — different from TWS

TWS API enforces a **3-minute throttle** on `reqAccountSummary`
re-subscriptions
(<https://interactivebrokers.github.io/tws-api/account_summary.html>).
CP API does **not** publish a documented throttle on the HTTP summary
endpoint, but the underlying numbers refresh at the IBKR risk-engine cadence
(typically 3 sec for ELv/excess-liquidity, but **NOT** synchronized with
fills — see §5). For practical purposes treat HTTP summary as **~3 sec stale
worst case**, and don't poll faster than every 1 sec or you'll get rate-limit
warnings.

---

## 3. Per-currency ledger — `get_ledger`

### Endpoint

`GET /portfolio/{accountId}/ledger`

ibind wrapper: `client.get_ledger(account_id)` returns
`portfolio/{id}/ledger`. The docstring confirms it's "information regarding
settled cash, cash balances, etc. in the account's base currency and any other
cash balances held in other currencies."
(<https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_client_mixins/portfolio_mixin.py>)

### Response shape — multi-currency keyed dict

The ledger returns a top-level dict keyed by currency code, **plus a special
`BASE` entry**:

```json
{
  "BASE": {
    "currency":          "EUR",
    "cashbalance":       1100000.00,
    "exchangerate":      1.0,
    "realizedpnl":          1234.50,
    "unrealizedpnl":       -1500.00,
    "settledcash":      1098765.43,
    "stockmarketvalue":   289443.10,
    "futuremarketvalue":       0.0,
    "moneyfunds":             0.0,
    "issueroptionsmarketvalue": 0.0,
    "interest":              123.45,
    "dividends":               0.0,
    "endofbundle":             1,
    "timestamp":      1715592300
  },
  "EUR": {
    "currency":     "EUR",
    "cashbalance":  900000.00,
    "exchangerate": 1.0,
    ...
  },
  "USD": {
    "currency":     "USD",
    "cashbalance":   218000.00,
    "exchangerate":      1.084231,   // <-- EUR/USD: 1 USD = 1.084231 EUR
    "realizedpnl":        650.00,
    "unrealizedpnl":    -1400.00,
    ...
  }
}
```

**Key field:** `exchangerate` on every per-currency entry is the rate
**to base** — i.e. `cashbalance_in_currency × exchangerate = value_in_base`.
For our EUR base account holding USD, `USD.exchangerate ≈ EUR/USD ≈ 0.92`
(not 1.084 — that's the inverse). Verify direction on first live call before
hardcoding sign.

### Refresh cadence

The ledger has the **same** ~3 sec risk-engine cadence as the portfolio
summary. Cash balance moves with fills but with a similar delay. **No
documented HTTP throttle**, but IBKR's general rate-limit guidance applies:
keep HTTP polling ≤ 1 Hz per endpoint per account.

---

## 4. Computing live USD-tradable for an EUR-base account

### The three-step recipe

```
usd_tradable = eur_availablefunds × usd_per_eur  +  usd_cashbalance
```

Where:
- `eur_availablefunds` = base-currency buying-power proxy from
  `/portfolio/.../summary`, field `availablefunds.amount`
- `usd_per_eur` = derived from `/portfolio/.../ledger` USD row's
  `exchangerate` (taking the inverse if the rate is stored as base/quote)
- `usd_cashbalance` = `/portfolio/.../ledger` USD row's `cashbalance`

### Why both terms?

- IBKR's universal-account model lets you trade USD products against EUR
  free cash — the broker auto-FX's at fill time. So the **EUR avail × FX**
  term tells you the EUR free cash you could deploy after auto-conversion.
- The **USD cash** term captures any already-converted USD sitting in the
  account (from prior credits, dividend payments on USD-listed positions,
  realized P&L on USD products that hasn't been swept back to EUR).
- Adding them gives the **total USD you could spend right now** on a new SPX
  trade — the closest CP API analog to TWS API
  `reqAccountSummary($LEDGER:USD, BuyingPower)` from `08_orf_and_ledger_usd.md`.

### ibind code

```python
def usd_tradable(client, account_id: str) -> float:
    summary = client.portfolio_summary(account_id).data
    ledger  = client.get_ledger(account_id).data

    eur_avail   = float(summary["availablefunds"]["amount"])
    usd_row     = ledger["USD"]
    # exchangerate is base-per-quote per IBKR convention — empirically verify:
    eur_per_usd = float(usd_row["exchangerate"])
    usd_per_eur = 1.0 / eur_per_usd
    usd_cash    = float(usd_row["cashbalance"])

    return eur_avail * usd_per_eur + usd_cash
```

**Empirical verification step required on first run:** print both rates,
multiply EUR cash × `exchangerate`, and confirm the product equals BASE
`cashbalance` minus other-currency contributions. The direction convention
is not documented unambiguously and has flipped between CP API versions.

---

## 5. Real-time vs stale — push subscriptions

### HTTP cadence summary

| Endpoint                     | Practical staleness   | TWS-equivalent throttle    |
|------------------------------|-----------------------|----------------------------|
| `/portfolio/.../summary`     | ~3 sec                | TWS: 3-min on `reqAccountSummary` (<https://interactivebrokers.github.io/tws-api/account_summary.html>) |
| `/portfolio/.../ledger`      | ~3 sec                | TWS: subscribes to account ledger updates inside the 3-min envelope |
| `/iserver/account/orders`    | sub-second after fill | TWS: `reqOpenOrders` is event-driven |

**Big finding:** the CP API does **not** carry the TWS 3-minute throttle.
The HTTP endpoints can be polled at 1 Hz comfortably. **But** the underlying
risk engine that produces the numbers still updates at its own ~3 sec
cadence — polling faster than that is pointless. The 3-min number from the
TWS world was a **client-library subscription throttle, not a server-side
refresh cadence**, and it is gone on the CP API HTTP path.

### WebSocket push — the real-time deltas

ibind's `IbkrWsClient` exposes these subscription channels
(<https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_ws_client.py>):

| `IbkrWsKey` constant    | Channel | Subscribe verb | Use for                                |
|-------------------------|---------|----------------|----------------------------------------|
| `ACCOUNT_SUMMARY`       | `sd`    | `ssd+{}`       | Real-time AvailableFunds/Excess push   |
| `ACCOUNT_LEDGER`        | `ld`    | `sld+{}`       | Real-time per-currency cash push       |
| `MARKET_DATA`           | `md`    | `smd+...`      | Quotes                                 |
| `MARKET_HISTORY`        | `mh`    | `smh+...`      | Historical bars                        |
| `PRICE_LADDER`          | `bd`    | `sbd+...`      | DOM                                    |
| `ORDERS`                | `or`    | `sor+{}`       | **Order state changes (fills, cxl)**   |
| `PNL`                   | `pl`    | `spl+{}`       | Real-time P&L deltas                   |
| `TRADES`                | `tr`    | `str+{}`       | Trade-list updates                     |

Subscription pattern (per `ibkr_ws_client.py`):

```python
from ibind import IbkrWsClient, IbkrWsKey

ws = IbkrWsClient(...)
ws.start()
ws.subscribe(channel=IbkrWsKey.ORDERS.channel,          data={})   # -> sor+{}
ws.subscribe(channel=IbkrWsKey.ACCOUNT_LEDGER.channel,  data={})   # -> sld+{}
ws.subscribe(channel=IbkrWsKey.ACCOUNT_SUMMARY.channel, data={})   # -> ssd+{}
ws.subscribe(channel=IbkrWsKey.PNL.channel,             data={})   # -> spl+{}

# Pull from per-channel thread-safe queues:
order_q = ws.new_queue_accessor(IbkrWsKey.ORDERS)
while True:
    msg = order_q.get(timeout=1.0)
    if msg: handle_order_update(msg)
```

### Recommended hybrid pattern

For our bot:
1. **HTTP poll** `/portfolio/.../summary` and `/portfolio/.../ledger` once
   on startup, then again every 10 sec as a safety re-sync.
2. **WS subscribe** to `sor`, `sld`, `ssd`, `spl` for live deltas.
3. **Always re-poll HTTP after a fill**, then trust WS deltas until the next
   re-sync. WS deltas can be dropped on transient disconnects; HTTP is
   authoritative on resync.

This matches the pattern in `07_key_rotation_and_index_sub.md` (re-sync on
reconnect) and `08_orf_and_ledger_usd.md` (ledger as cash-of-record).

---

## 6. Positions queries

### Paginated

`GET /portfolio/{accountId}/positions/{pageId}` — ibind:

```python
positions = client.positions(
    account_id=account_id,
    page=0,                # 0-indexed, 100 positions per page
    sort="position",       # one of position, conid, etc.
    direction="d",         # a=asc, d=desc
)
```
Source:
<https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_client_mixins/portfolio_mixin.py>.

### Single contract

`GET /portfolio/{accountId}/position/{conid}` — ibind:

```python
pos = client.positions_by_conid(account_id, conid="416904").data
# pos[0] is the position record
```

### "Real-time" positions endpoint

`GET /portfolio2/{accountId}/positions` — ibind:

```python
pos_now = client.positions2(account_id=account_id).data
```

The `portfolio2` path is documented as "near real-time, no caching" — uses it
for the post-fill reconciliation check after a `sor` WS message.

### Response shape (one position)

```json
{
  "acctId": "U1234567",
  "conid": 416904,
  "contractDesc": "SPX 16JUN26 5500 C",
  "position": -10,
  "mktPrice": 12.45,
  "mktValue": -12450.00,
  "currency": "USD",
  "avgCost": 1145.00,
  "avgPrice": 11.45,
  "realizedPnl": 0.0,
  "unrealizedPnl": -1000.00,
  "exchs": null,
  "expiry": "20260616",
  "putOrCall": "C",
  "strike": 5500.0,
  "ticker": "SPX",
  "assetClass": "OPT",
  ...
}
```

Note `currency` is per-position (USD for SPX options) — the position is
stored in its native currency, **not** translated to base. The
`mktValue` × ledger USD `exchangerate` gives the base-currency contribution.

### Filtering to SPX options

CP API has no server-side symbol filter on positions; do it client-side:

```python
all_pos = []
page = 0
while True:
    chunk = client.positions(account_id=account_id, page=page).data
    if not chunk: break
    all_pos.extend(chunk)
    page += 1

spx_options = [
    p for p in all_pos
    if p["ticker"] == "SPX" and p["assetClass"] == "OPT"
]
```

---

## 7. Open orders — `/iserver/account/orders`

### Endpoint

`GET /iserver/account/orders` (no accountId in path — account is on the
session). ibind:

```python
def live_orders(self, filters=None, force=None, account_id=None) -> Result:
    # GET iserver/account/orders
```

`filters` is a comma-separated list of statuses; server-side filtering by
status only. Ticker/conid filtering is client-side.
(<https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_client_mixins/order_mixin.py>)

### Response shape

```json
{
  "orders": [
    {
      "acct": "U1234567",
      "orderId": 1234567890,
      "conid":   28012345,
      "ticker":  "SPX",
      "secType": "BAG",
      "side":    "BUY",
      "origOrderType": "LIMIT",
      "status":  "Submitted",      // PreSubmitted, Submitted, Filled, Cancelled, etc.
      "filledQuantity":   0,
      "remainingQuantity": 10,
      "totalSize":        10,
      "price":     1.50,
      "avgPrice":   null,
      "timeInForce": "DAY",
      "lastExecutionTime_r": 1715592300,
      ...
    }
  ],
  "snapshot": true
}
```

`snapshot: true` means this is the cached snapshot; pass `force=True` to bust
the cache on reconciliation runs.

### Reconciliation pattern on reconnect

Orders **persist broker-side** across our process restarts. On startup:

```python
def reconcile_open_orders(client, account_id):
    snapshot = client.live_orders(force=True, account_id=account_id).data
    open_orders = [
        o for o in snapshot.get("orders", [])
        if o["status"] in {"PreSubmitted", "Submitted", "PendingSubmit"}
    ]
    return open_orders
```

Match against our local order book by `orderId`; orphaned broker-side orders
(not in our book) should be flagged for human review, not auto-cancelled —
they may belong to a manual TWS desk trade.

---

## 8. Order status WebSocket (`sor` topic)

### Subscribe

```python
ws.subscribe(channel=IbkrWsKey.ORDERS.channel, data={})  # -> sor+{}
```

`sor+{}` (empty body) subscribes to **all** account order updates. There is
no documented filter syntax — filter client-side.

### Message shape

```json
{
  "topic": "sor",
  "args": [
    {
      "orderId":   "1234567890",
      "status":    "Filled",
      "filled":    "10",
      "remaining": "0",
      "avgPrice":  "1.50",
      "lastExecutionTime": "230515142359",
      "conid":     28012345,
      "ticker":    "SPX",
      ...
    }
  ]
}
```

Push is event-driven (fires on every order state change). Per the ibind WS
table, the `or` channel is one of the ones that **does NOT confirm subscribe
or unsubscribe** — so the bot must assume the subscription is live the moment
the connect handshake completes; there is no "subscribed" ack to wait for.
(<https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_ws_client.py>)

### Reliability — fall back to HTTP polling

The Voyz/ibind tracker has a known issue (#145) where WS subscriptions can
silently drop and need refresh every 15 min
(<https://github.com/Voyz/ibind/issues/145>). For order status this is the
single most safety-critical signal we consume, so the recommended pattern is:

1. WS `sor` as primary, latency-sensitive path (fires <100 ms after fill).
2. HTTP `live_orders(force=True)` poll **every 30 sec** as a fail-safe
   reconciliation — diffs against last-known-state per orderId; any drift
   triggers an alert + WS reconnect.
3. After a WS reconnect, immediately re-poll HTTP once to backfill any
   events missed during the gap.

---

## 9. Reply prompts (CP API quirk)

### What they are

When you POST `/iserver/account/{id}/orders`, IBKR may respond with a list of
**risk-warning prompts** instead of an order acknowledgement. Each prompt
gets a UUID `id` and a `message` body; you must POST
`/iserver/reply/{replyId}` with `{"confirmed": true}` to proceed
(or `false` to abort).
(<https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-trading/>)

### Typical prompts

- "The following order will be executed outside of regular trading hours.
  Are you sure you want to submit this order?"
- "This order will use a substantial portion of your available funds."
- "Order price is significantly outside the current bid/ask range."
- "You are trading a thinly-traded instrument."
- "Cross-currency execution: this order requires automatic FX conversion."

### Endpoint

`POST /iserver/reply/{replyId}` with body `{"confirmed": true}`.

Successful confirmation returns the actual order ack:
```json
{"order_id": "1234567890", "order_status": "Submitted", "encrypt_message": "1"}
```
(<https://gist.github.com/theloniusmunch/9b14d320fd1c3aca550fc8d54c446ce0>)

### ibind handling — **semi-automatic** via `Answers`

ibind's `place_order` and `modify_order` take an `answers: Answers` parameter:

```python
from ibind.client.ibkr_utils import QuestionType, Answers

answers: Answers = {
    QuestionType.PRICE_PERCENTAGE_CONSTRAINT: True,
    QuestionType.ORDER_VALUE_LIMIT:           True,
    QuestionType.MISSING_MARKET_DATA:         True,
    QuestionType.STOP_ORDER_RISKS:            True,
    QuestionType.CASH_QUANTITY_ORDER:         True,
    # ... see ibind.client.ibkr_utils.QuestionType for the full enum
}
client.place_order(order, answers=answers, account_id=account_id)
```

ibind transparently handles the reply loop: it POSTs the order, sees the
prompt response, looks up your boolean answer by question type, POSTs
`/iserver/reply/{id}` with that boolean, and chains until either the order
acks or you've declined a prompt. **You never see the raw replyId** unless
the prompt type isn't in `QuestionType`, in which case ibind raises and you
fall back to manual reply.

For the bot, the safe pattern is to pre-declare answers for every known
question type as either `True` (proceed) or `False` (abort) — never `None`,
which forces a manual prompt. Whatif, by contrast, does **not** trigger
prompts, so `whatif_order` has no `answers` parameter.

---

## 10. Practical code skeleton

```python
"""
Bot-side pre-trade margin check + order placement for SPX iron condors,
against IBIE EUR-base via ibind + OAuth 1.0a.
"""
from ibind import IbkrClient, IbkrWsClient, IbkrWsKey
from ibind.client.ibkr_utils import OrderRequest, QuestionType

ACCOUNT_ID = "U1234567"

client = IbkrClient()  # picks up IBIND_OAUTH1A_* env vars
ws     = IbkrWsClient()
ws.start()
ws.subscribe(channel=IbkrWsKey.ORDERS.channel,          data={})
ws.subscribe(channel=IbkrWsKey.ACCOUNT_LEDGER.channel,  data={})
ws.subscribe(channel=IbkrWsKey.ACCOUNT_SUMMARY.channel, data={})

DEFAULT_ANSWERS = {q: True for q in QuestionType}  # auto-confirm everything

# --- 1. Pre-trade margin check ---
def margin_preview(combo_conid: int, qty: int, limit_price: float):
    req = OrderRequest(
        conid=combo_conid, side="BUY", quantity=qty,
        order_type="LMT", price=limit_price, tif="DAY",
        account_id=ACCOUNT_ID,
    )
    return client.whatif_order(req, account_id=ACCOUNT_ID).data

# --- 2. USD-tradable on EUR base ---
def usd_tradable() -> float:
    s = client.portfolio_summary(ACCOUNT_ID).data
    l = client.get_ledger(ACCOUNT_ID).data
    eur_avail   = float(s["availablefunds"]["amount"])
    usd_row     = l["USD"]
    eur_per_usd = float(usd_row["exchangerate"])
    usd_cash    = float(usd_row["cashbalance"])
    return eur_avail / eur_per_usd + usd_cash

# --- 3. List open orders (incl. orphans) ---
def list_open() -> list:
    snap = client.live_orders(force=True, account_id=ACCOUNT_ID).data
    return [o for o in snap.get("orders", [])
            if o["status"] in {"PreSubmitted", "Submitted"}]

# --- 4. List open SPX option positions ---
def spx_positions() -> list:
    out, page = [], 0
    while True:
        chunk = client.positions(account_id=ACCOUNT_ID, page=page).data
        if not chunk: break
        out.extend(chunk); page += 1
    return [p for p in out if p["ticker"] == "SPX" and p["assetClass"] == "OPT"]

# --- 5. Cancel a working limit ---
def cancel(order_id: str):
    return client.cancel_order(order_id=order_id, account_id=ACCOUNT_ID)

# --- 6. Place an IC (reply prompts handled automatically) ---
def place_ic(combo_conid: int, qty: int, credit: float):
    req = OrderRequest(
        conid=combo_conid, side="BUY", quantity=qty,
        order_type="LMT", price=credit, tif="DAY",
        account_id=ACCOUNT_ID,
    )
    return client.place_order(req, answers=DEFAULT_ANSWERS, account_id=ACCOUNT_ID)
```

---

## 11. Reg-T margin specifics for SPX IC on IBIE Ireland

### Reg-T IC formula

For a defined-risk iron condor at IBKR LLC (US) under Reg-T:

```
margin_required = max(call_spread_width, put_spread_width) × $100 × contracts
                  − net_credit × $100 × contracts
```

This is the **standard Reg-T defined-risk options formula** — IBKR applies it
uniformly across SPX/SPY/RUT etc.
(<https://www.interactivebrokers.com/en/trading/margin-requirements.php>)

### IBIE Ireland — does it differ?

**Short answer:** for SPX index options, no material divergence — IBIE
applies the same Reg-T-equivalent formula for defined-risk option spreads on
US-listed index options. IBIE's Options Margin page confirms the formula and
notes the 15% × leverage-factor floor only applies to **leveraged options**
(not standard index spreads).
(<https://www.interactivebrokers.ie/en/trading/margin-options.php>)

The whatif endpoint is the ground truth — always preview before placing,
don't trust local calc.

### PDT rule — does NOT apply to IBIE

Pattern Day Trader designation is a **FINRA rule (US)**, which applies to
IBKR LLC clients. IBIE Ireland clients are regulated by the Central Bank of
Ireland under MiFID II and the local equivalent of CRD-IV margin rules — PDT
does not exist there. Confirmed in IBIE's account-rules KB.
(<https://www.ibkrguides.com/kb/en-us/article-3513.htm>)

### ESMA leverage caps — DO NOT apply to options

ESMA's 2018 leverage caps (3.33% on major FX pairs, etc.) apply **only to
CFDs**, not to options. Retail option spreads at IBIE are governed by the
standard exchange-driven margin formula above. There is no ESMA
retail-leverage cap that touches our SPX IC strategy.
(<https://www.ibkrguides.com/kb/en-us/article-3513.htm>)

The only ESMA-driven constraint relevant to us is **negative balance
protection**, which we benefit from passively (IBKR force-liquidates before
NAV goes negative; we never owe more than we deposited).

---

## 12. Open questions / verify on first live run

1. **`USD.exchangerate` direction.** Print and verify base-per-quote vs
   quote-per-base on first call. Easy to confirm:
   `EUR.cashbalance + USD.cashbalance × USD.exchangerate ≈ BASE.cashbalance`.
2. **Whatif response number formatting under EUR locale.** IBKR may return
   `"-1.500,00"` (European decimal/grouping) on EUR-base accounts. Test once
   and harden `_parse_money` accordingly.
3. **`sor` push for combo orders.** Confirm the WS message for an IC fill
   carries the **combo conid** or breaks out into 4 leg fills. This affects
   how we update our local order book on partial fills.
4. **HTTP rate limits.** ibind respects `Retry-After` headers; the empirical
   ceiling for `/portfolio/.../*` endpoints is ~1 Hz/account. Confirm in
   load-test before relying on 10-sec re-sync polling.

---

## 13. Sources

- IBKR Campus Web API v1.0 doc: <https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/>
- IBKR Campus Web API Reference: <https://www.interactivebrokers.com/campus/ibkr-api-page/webapi-ref/>
- IBKR Campus Trading Web API: <https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-trading/>
- IBKR Campus Margin Mobile Preview: <https://www.interactivebrokers.com/campus/trading-lessons/looking-at-margin-on-ibkr-mobile/>
- IBKR Campus Excess Liquidity Glossary: <https://www.interactivebrokers.com/campus/glossary-terms/excess-liquidity/>
- IBKR Available for Trading: <https://www.ibkrguides.com/traderworkstation/available-for-trading.htm>
- IBKR Base Currency: <https://www.ibkrguides.com/clientportal/basecurrency.htm>
- IBIE Margin Options: <https://www.interactivebrokers.ie/en/trading/margin-options.php>
- IBIE Account-Rules KB: <https://www.ibkrguides.com/kb/en-us/article-3513.htm>
- IBKR LLC Margin Requirements: <https://www.interactivebrokers.com/en/trading/margin-requirements.php>
- TWS API reqAccountSummary cadence: <https://interactivebrokers.github.io/tws-api/account_summary.html>
- TWS API whatIfOrder reference: <https://interactivebrokers.github.io/tws-api/margin.html>
- ibind GitHub: <https://github.com/Voyz/ibind>
- ibind IbkrClient wiki: <https://github.com/Voyz/ibind/wiki/API-Reference-%E2%80%90-IbkrClient>
- ibind WS client source: <https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_ws_client.py>
- ibind portfolio mixin: <https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_client_mixins/portfolio_mixin.py>
- ibind order mixin: <https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_client_mixins/order_mixin.py>
- ibind PyPI (versioning): <https://pypi.org/project/ibind/>
- ibind issue #145 (WS 15-min refresh): <https://github.com/Voyz/ibind/issues/145>
- Theloniusmunch IB-Client-Web-API gist (reply example): <https://gist.github.com/theloniusmunch/9b14d320fd1c3aca550fc8d54c446ce0>
