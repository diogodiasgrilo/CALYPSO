# 09 — CP API Combo Orders for SPX Iron Condors via ibind

**Research brief for the CALYPSO Saxo→IB migration, second pivot: TWS API → Client Portal Web API ("CP API") accessed through OAuth 1.0a + the `Voyz/ibind` Python client (v0.1.23). Replaces the `BAG`/`ComboLeg[]`/`smartComboRoutingParams` material from `03_ib_orders_positions.md` §3 with the CP API equivalent.**

All citations dated **2026-05-13**. Primary sources are the IBKR Campus
CP API v1 docs, the `Voyz/ibind` GitHub repo (`master` branch), and
the IBKR TWS users-guide pages that carry the canonical pricing-sign
and non-guaranteed-flag rules (those rules predate the CP API and the
CP API documentation simply re-cites them).

---

## 1. The endpoint and payload — `POST /iserver/account/{accountId}/orders`

Combo / spread / multi-leg orders use the **same** endpoint as
single-leg orders. The only thing that changes is **`conidex` replaces
`conid`** in the order body. From the IBKR Campus Web API page: *"In
combo orders, the conid field is replaced with 'conidex' instead, which
is a string representation of combo order parameters following the
format: `{spread_conid};;;{leg_conid1}/{ratio},{leg_conid2}/{ratio}`"*
([IBKR Campus — Web API v1.0](https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/), §Orders → "Place Combo / Spread Order"). The Client Portal API
inherits the **TWS 6-leg limit**, well above our 4-leg condor
requirement.

### 1.1 `conidex` for a USD iron condor

The literal payload for a 4-leg SPXW IC looks like (formatted for
readability — on the wire it's one string):

```
28812380;;;{short_call_conid}/-1,{long_call_conid}/1,{short_put_conid}/-1,{long_put_conid}/1
```

Component-by-component:

| Token | Value | Meaning |
|---|---|---|
| Prefix | `28812380` | The USD **spread template conid** — fixed for all US-currency combos. Non-USD legs use a different prefix (e.g. EUR `28812381`, GBP `58666491`, JPY `61227069`). The ibind options-chain example (`examples/rest_06_options_chain.py`) lists the full table. |
| Separator | `;;;` | **Exactly three** semicolons — anything else is parsed as a different spread type. |
| Leg | `{leg_conid}/{ratio}` | `leg_conid` is the per-strike option conid resolved via `/iserver/secdef/info`. **`ratio` sign encodes the side:** positive = BUY that leg, negative = SELL that leg. Magnitude is the leg multiplier in the spread template (always `1` for a vanilla 1:1:1:1 IC). |
| Joiner | `,` (comma) | Separates legs. |

Source: the conidex grammar is documented in the same IBKR Campus
Web API page cited above; the ratio-sign rule is confirmed verbatim
in the IBKR Trading API Documentation Guide ([Scribd mirror](https://www.scribd.com/document/876499086/Trading-Web-API-Ibkr-API-Ibkr-Campus)):
*"A positive ratio integer indicates a Buy, while a negative ratio
integer represents a Sell."* The 6-leg cap is documented at the same
location.

### 1.2 Full order body — short iron condor at $0.30 net credit, 10 lots

```json
{
  "orders": [
    {
      "acctId": "U1234567",
      "conidex": "28812380;;;111111111/-1,222222222/1,333333333/-1,444444444/1",
      "secType": "BAG",
      "orderType": "LMT",
      "price": 0.30,
      "side": "SELL",
      "tif": "DAY",
      "quantity": 10,
      "cOID": "ic-0dte-20260513-141522"
    }
  ]
}
```

Field-level notes:

- **`acctId`** — required, string. Same as path param.
- **`conidex`** — see §1.1. Mutually exclusive with `conid` (ibind raises
  `ValueError` at `parse_order_request` if both are set; see [`ibind/client/ibkr_utils.py`](https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_utils.py)).
- **`secType: "BAG"`** — optional but recommended. IBKR's combo-routing
  guide (see [Advanced Combo Routing](https://www.ibkrguides.com/traderworkstation/advanced-combo-routing.htm))
  treats any conidex-bearing order as a BAG; passing `secType` makes
  it explicit and helps `whatif` previews resolve correctly.
- **`orderType: "LMT"`** — only viable type for IC entry/stop. `MKT` on
  a 4-leg SPX IC will get murdered by the bid-ask cone.
- **`price`** — net credit/debit per spread. **Sign convention is
  load-bearing** (see §3).
- **`side`** — `"SELL"` to *sell* the spread template (short IC,
  credit received) or `"BUY"` to *buy* the spread template (long IC,
  debit paid). Note: side applies to the **combo as a whole**, not to
  individual legs. The leg directions are already encoded in each
  leg's `ratio` sign in `conidex`.
- **`tif: "DAY"`** — mandatory for 0DTE. `GTC` is ibind's default in
  `OrderRequest` and is wrong for our use case; always override.
- **`quantity: 10`** — number of *spreads*, **not** the per-leg
  contract count. 10 ICs = 10×4 = 40 option contracts working.
- **`cOID`** — customer order ID, unique within 24h, max 40 chars.
  Use to deduplicate retries on network failure.

### 1.3 Response & reply-prompt flow

The first response from `POST /iserver/account/{accountId}/orders` is
**not** necessarily a confirmation — it's frequently a precautionary
warning that must be acknowledged via `POST /iserver/reply/{id}`.
From IBKR Campus: *"In order confirmation responses, the API returns a
messageIds field … which is used when the system requires order
confirmation through the /iserver/reply/ endpoint."*

A typical first-response shape requiring acknowledgement:

```json
[
  {
    "id": "abc123-def456",
    "message": [
      "The following order will use SmartRouting. Are you sure you want to submit this order?"
    ],
    "isSuppressed": false,
    "messageIds": ["o354"]
  }
]
```

The client must `POST /iserver/reply/abc123-def456` with body
`{"confirmed": true}`. There can be **multiple** sequential prompts —
each `reply` may itself return another `id`+`message` pair. The flow
terminates when the response contains an order-confirmation object
(`order_id`, `local_order_id`, `order_status` keys, no `message`).
ibind's [`handle_questions`](https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_utils.py)
loops up to 20 times and raises `RuntimeError("Too many questions")`
beyond that. Suppressible message IDs (e.g. `o354`, `o451`, `o10153`)
can be silenced once-per-session via
`POST /iserver/questions/suppress` so subsequent orders skip the
prompt — see ibind's `suppress_messages` mixin method.

---

## 2. Leg conid resolution — mandatory three-step dance

There is **no shortcut** for resolving option conids on CP API. The
canonical sequence is mandated by IBKR Campus: *"the Client Portal API
requires that users query the `/iserver/secdef/search`,
`/iserver/secdef/strikes`, and `/iserver/secdef/info` sequentially,
with no means around this process."* ([Handling Options Chains, IBKR
Quant](https://www.interactivebrokers.com/campus/ibkr-quant-news/handling-options-chains/)).

### 2.1 Step 1 — Underlying conid for SPX

```python
spx = client.search_contract_by_symbol('SPX').data[0]
spx_conid = spx['conid']   # 416904
```

The SPX index underlying conid is **stable** (it's the cash-settled
S&P 500 index). Cache for the life of the process — never re-look-up.

### 2.2 Step 2 — Available strikes for an expiry

```python
strikes = client.search_strikes_by_conid(
    conid=spx_conid,
    sec_type='OPT',
    month='MAY26',          # 3-char month + 2-digit year (e.g. AUG23)
    exchange='SMART',
).data
# {'call': [4900, 4905, ..., 5500], 'put': [4900, ..., 5500]}
```

`month` resolution is at calendar-month granularity. **Daily/weekly
expiries inside that month all resolve from the same `strikes` call**
— the per-expiry split happens in step 3.

### 2.3 Step 3 — Per-strike conid + expiry filter

```python
info_list = client.search_secdef_info_by_conid(
    conid=spx_conid,
    sec_type='OPT',
    month='MAY26',
    strike='5200',
    right='C',
    exchange='SMART',
).data
# returns a list — each element has 'maturityDate' (YYYYMMDD), 'conid', 'tradingClass' ('SPX' vs 'SPXW'), etc.

# Filter to the 0DTE SPXW row
today = '20260513'
leg = next(r for r in info_list if r['maturityDate'] == today and r['tradingClass'] == 'SPXW')
short_call_conid = leg['conid']
```

**Critical for 0DTE:** the same strike/right pair returns *both*
the monthly AM-settled SPX and the weekly/daily PM-settled SPXW. We
need `tradingClass == 'SPXW'`. See `05_ib_spx_0dte_edge_cases.md` §2
for the AM-vs-PM-settlement rabbit hole — it bites just as hard on
CP API as on TWS API.

### 2.4 Bulk resolution + caching

There is a **`POST /iserver/secdef/info`** variant accepting up to
**200 conids** per request as a JSON body (per the IBKR Campus
endpoint reference — exact body schema undocumented in the public
HTML pages; the IBKR Quant blog post on derivative contract details
references it). ibind does not yet wrap the POST variant — the
`search_secdef_info_by_conid` method is GET-only. For our 4-leg IC,
just call the GET four times; sub-100ms each, well under any latency
budget.

**Caching strategy:** conids for `(underlying, expiry, strike, right,
trading_class)` tuples are **stable across the trading day**. Cache
once per process startup. Refresh on the daily process restart (we
already restart at 09:25 ET pre-market). Never query mid-flight on
the hot path — that's the entry-latency disaster waiting to happen.

---

## 3. Price sign — credit vs debit on a SELL combo

This is the trap that catches every CP-API combo newcomer. The IBKR
spec is **counter-intuitive** but consistent across TWS GUI, TWS API,
and CP API. From the IBKR users-guide
[Notes on Combination Orders](https://www.ibkrguides.com/traderworkstation/notes-on-combination-orders.htm):

> *"If you BUY a spread and you owe cash (debit spread), enter a
> positive limit price. If you BUY a spread and you receive cash (a
> credit spread), you must enter a negative limit price. Conversely,
> if you SELL a spread and receive cash, enter a positive limit
> price."*

Truth table for a 4-leg IC:

| Action | `side` | Net cash | `price` sign | Example |
|---|---|---|---|---|
| **Open short IC** (sell premium) | `"SELL"` | We receive credit | **Positive** | `price: 0.30` (= we collect $0.30/spread × 100 × 10 lots = $300) |
| Stop-out close (buy back short IC) | `"BUY"` | We pay debit | **Positive** | `price: 0.45` (= we pay up to $0.45/spread to flatten) |
| (Unusual) Long IC entry | `"BUY"` | We pay debit | Positive | n/a for our strategy |
| (Unusual) Long IC close | `"SELL"` | We receive credit | Positive | n/a for our strategy |

**For CALYPSO's actual flow we only ever use positive `price`** —
SELL-to-open at credit, BUY-to-close at debit. The negative-price
scenario only matters if we ever invert the spread template's natural
direction (which we won't). The asymmetry is the contract template's
choice — `28812380` is defined such that its "natural" buy direction
results in a debit, so any other combination needs a sign flip.

**$0.05 increment rule on CBOE COB.** Carried over from the TWS-API
research file: SPX/SPXW complex orders on the CBOE Complex Order Book
must price in $0.05 increments. CP API does **not** auto-round —
mispriced orders are rejected with a `messageIds: ["o382"]`
(`TICK_SIZE_LIMIT`). Round client-side before submit:

```python
def round_to_tick(price: float, tick: float = 0.05) -> float:
    return round(round(price / tick) * tick, 2)
```

---

## 4. Routing flags — the "NonGuaranteed" question

**This is where CP API and TWS API diverge.** On TWS API, atomic-fill
versus legging-risk-OK is expressed via
`smartComboRoutingParams=[TagValue("NonGuaranteed", "1")]` on the
`Order` object. On CP API the **`smartComboRoutingParams` field is
not exposed** in the `/iserver/account/{accountId}/orders` body
schema — the public Campus docs do not list it among the order body
fields.

### 4.1 What we know

- IBKR's [Advanced Combo Routing](https://www.ibkrguides.com/traderworkstation/advanced-combo-routing.htm)
  page documents the tag/value pair (`NonGuaranteed=1` legs OK,
  `=0` atomic). It applies to BAG orders regardless of API.
- Multiple community sources report that **the CP API defaults BAG
  orders to `NonGuaranteed=1` (legs-OK)** for SMART-routed combos
  because that's the default IBKR account preference for retail
  accounts; the QuantRocket forum thread on `whatif` for combos
  ([thread](https://support.quantrocket.com/t/what-if-on-combo-order/2181/4))
  confirms that CP API combo orders behave as non-guaranteed
  SmartRouted unless the user has changed their TWS preset to require
  guaranteed routing.
- **SPX specifically — directed-CBOE-COB orders are atomic by venue
  rule**, not by API flag. SPX complex orders sent to CBOE's Complex
  Order Book fill as one ticket or not at all. Direct the order to
  CBOE (not SMART) and atomic fill is the exchange-level guarantee.

### 4.2 What we'll do in practice

| Variant | Routing approach | Atomic? |
|---|---|---|
| **(a) Entry** — legging OK | Send `conidex` with the default `28812380` USD-spread prefix → SMART-routed → `NonGuaranteed=1` (CP-API default) → legs may fill independently | No — we accept this for better fills on entry |
| **(b) Stop-out close** — atomic required | Same `conidex`, but for the stop-out path we accept that **CP API cannot express per-order atomicity directly**. Worst-case fallback: monitor leg fills in real-time and convert to per-leg market orders if the combo doesn't fill within N seconds. Alternative: explicitly direct the combo to CBOE by appending `@CBOE` to the spread prefix in conidex (e.g. `28812380@CBOE;;;…`) — IBKR Campus documents the `spread_conid@exchange` syntax for non-US combos and the same syntax routes US combos to a specific exchange. **This needs paper-trading validation before we commit.** |

The hard truth: **if atomic fill is non-negotiable on the close, the
robust pattern is client-side enforcement** — submit the combo, watch
the `sor` WebSocket stream for per-leg fills, and if any leg fills
without the rest filling within a few seconds, slam market orders on
the remaining legs to flatten. This is the same defensive pattern we'd
need on TWS API for the rare cases when SMART routing splits legs
across venues anyway.

---

## 5. ibind wrappers — minimum viable IC submission

ibind's [`order_mixin.py`](https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_client_mixins/order_mixin.py)
exposes `place_order(order_request, answers, account_id)` which
internally:

1. Holds `OrderMixin.order_submission_lock` (a class-level
   `threading.Lock`) to enforce the "only one order at a time"
   constraint enforced by the reply-prompt mechanism.
2. Calls `parse_order_request()` to map snake_case `OrderRequest`
   fields to the camelCase JSON the API expects (see
   `_ORDER_REQUEST_MAPPING` in `ibkr_utils.py`).
3. POSTs to `iserver/account/{account_id}/orders`.
4. Pipes the response into `handle_questions(result, answers, self.reply)`
   which iterates through reply prompts up to 20 times.

The `OrderRequest` dataclass already supports `conidex` directly —
no monkey-patching needed for combos. The repo's
[`examples/rest_06_options_chain.py`](https://github.com/Voyz/ibind/blob/master/examples/rest_06_options_chain.py)
demonstrates a 2-leg spread; the 4-leg IC is a direct extension.

### 5.1 Skeleton — works against ibind 0.1.23

```python
"""
SPX 0DTE iron condor via CP API + ibind.
Submits a 10-lot short IC at $0.30 net credit; handles reply prompts;
exposes a cancel hook.

Assumes:
  - OAuth 1.0a session already established (see examples/rest_08_oauth.py)
  - IBIND_ACCOUNT_ID set in env
  - SPX option conids pre-resolved (call resolve_ic_legs() at startup)
"""
from datetime import datetime
import os
from ibind import IbkrClient, OrderRequest, QuestionType, ibind_logs_initialize

ibind_logs_initialize(log_to_file=True)

ACCOUNT_ID = os.environ['IBIND_ACCOUNT_ID']
USD_SPREAD_CONID = '28812380'      # IBKR-published USD spread template

client = IbkrClient(
    cacert=os.getenv('IBIND_CACERT', False),
    use_session=False,             # OAuth handled out-of-band
)


def round_to_nickel(price: float) -> float:
    """SPX CBOE COB requires $0.05 increments."""
    return round(round(price / 0.05) * 0.05, 2)


def build_ic_conidex(short_call: int, long_call: int,
                     short_put: int, long_put: int) -> str:
    """Encode the 4 legs into the conidex string IBKR expects."""
    legs = [
        f'{short_call}/-1',     # SELL short call
        f'{long_call}/1',       # BUY  long  call (wing)
        f'{short_put}/-1',      # SELL short put
        f'{long_put}/1',        # BUY  long  put (wing)
    ]
    return f'{USD_SPREAD_CONID};;;' + ','.join(legs)


def submit_short_ic(legs: dict, net_credit: float, lots: int = 10) -> dict:
    """
    Submit a SELL-to-open short iron condor at limit net credit.

    Returns the IBKR order envelope: {order_id, local_order_id, order_status, ...}
    """
    coid = f'ic-0dte-{datetime.utcnow().strftime("%Y%m%d-%H%M%S")}'

    order = OrderRequest(
        conid=None,                              # MUST be None when conidex is set
        conidex=build_ic_conidex(**legs),
        sec_type='BAG',
        side='SELL',                             # selling the spread template
        order_type='LMT',
        price=round_to_nickel(net_credit),       # positive: SELL credit spread
        quantity=lots,                           # number of *spreads*
        tif='DAY',                               # 0DTE — never GTC
        acct_id=ACCOUNT_ID,
        coid=coid,
    )

    answers = {
        QuestionType.PRICE_PERCENTAGE_CONSTRAINT: True,
        QuestionType.MISSING_MARKET_DATA: True,
        QuestionType.ORDER_VALUE_LIMIT: True,
        QuestionType.MANDATORY_CAP_PRICE: True,
        # Anything we DON'T want to auto-accept (e.g. STOP_ORDER_RISKS)
        # should map to False or be omitted entirely so handle_questions
        # raises and we bail out.
    }

    result = client.place_order(order, answers, ACCOUNT_ID)
    return result.data       # {'order_id': '...', 'order_status': 'Submitted', ...}


def cancel(order_id: str) -> None:
    client.cancel_order(order_id, account_id=ACCOUNT_ID)


# --- usage --------------------------------------------------------------
legs = {
    'short_call': 654321111,
    'long_call':  654321112,
    'short_put':  654321113,
    'long_put':   654321114,
}
result = submit_short_ic(legs, net_credit=0.30, lots=10)
print(result)
# {'order_id': '1234567890', 'local_order_id': 'ic-0dte-20260513-141522',
#  'order_status': 'PreSubmitted', ...}
```

The same builder works for the stop-out close — flip `side='BUY'`,
flip `price` to the debit you're willing to pay (still positive), and
keep `tif='DAY'`. The leg `ratio` signs **stay the same** in
`conidex` because the spread template's direction is invariant; the
account-level direction is encoded in `side`.

---

## 6. Order status & fill tracking

### 6.1 REST polling

```python
client.live_orders(filters=['submitted', 'pre_submitted'],
                   account_id=ACCOUNT_ID).data
client.order_status('1234567890').data
```

`live_orders` returns an array of order envelopes. **For combo orders,
the envelope is the combo** — there is one entry, not four. Fields:
`orderId`, `status` (PreSubmitted / Submitted / Filled /
PendingCancel / Cancelled), `filledQuantity`, `remainingQuantity`,
`avgPrice`, plus a `legs` array on combo orders.

Per the order_mixin docstring: *"filtering orders using the
/iserver/account/orders endpoint will prevent order details from
coming through over the websocket 'sor' topic. To resolve this issue,
developers should set 'force=true' in a follow-up
/iserver/account/orders call to clear any cached behavior."* In
practice: **don't filter on the WebSocket-active path** — let `sor`
push everything.

### 6.2 WebSocket `sor` topic

From IBKR Campus: *"The 'sor' topic relays back real-time updates of
your open orders … syntax is `sor+{}` to subscribe and `uor+{}` to
unsubscribe."* On a combo, the `sor` payload reports a **single order
envelope** with leg-level fill detail nested inside. The standard
status transitions on a working IC:

```
PreSubmitted  →  Submitted  →  Filled         (full-fill happy path)
                            ↘  PendingCancel  →  Cancelled  (cancel path)
                            ↘  PartiallyFilled  (CP API surfaces this on the combo when one or more legs but not all have filled — only happens on non-guaranteed routing)
```

ibind's `IbkrWsClient` (in [`ibind/client/ibkr_ws_client.py`](https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_ws_client.py))
wraps the WebSocket subscription. Subscribe with
`ws.subscribe(channel='sor', data={})`; messages arrive on the queue
with `topic='sor'` and `args` containing the order array. **Detect
"fully filled"** by `status == 'Filled'` AND
`remainingQuantity == 0`. Detect "partially filled" by
`status == 'Submitted'` AND `filledQuantity > 0` — this is the
trigger to invoke the client-side atomic-fill fallback from §4.2.

---

## 7. Modify and cancel

### 7.1 Modify (price change on a working IC)

```python
client.modify_order(
    order_id='1234567890',
    order_request=OrderRequest(
        conid=None,
        conidex=build_ic_conidex(**legs),   # same legs
        sec_type='BAG',
        side='SELL',
        order_type='LMT',
        price=0.25,                          # adjusted credit target
        quantity=10,
        tif='DAY',
        acct_id=ACCOUNT_ID,
    ),
    answers=answers,
    account_id=ACCOUNT_ID,
)
```

CP API supports **in-place modify** on working combo limit orders —
no cancel-and-replace dance required. The request mirrors the
original; only the changed field (typically `price`) is meaningful.
The reply-prompt mechanism applies just as on placement, so pass the
same `answers` map. Note ibind's `modify_order` also holds
`order_submission_lock`.

### 7.2 Cancel

```python
client.cancel_order('1234567890', account_id=ACCOUNT_ID)
```

Trivial. Sends `DELETE /iserver/account/{accountId}/order/{orderId}`.
Returns `{"msg": "Request was submitted"}` — confirmation of
**cancellation** comes through the `sor` topic as a status transition
to `Cancelled`. **Race condition to be aware of:** if the combo fills
in the millisecond between your decision-to-cancel and the cancel
arriving at IBKR, you get a fill not a cancel. Always treat cancel as
best-effort and reconcile against `sor`/`live_orders` before acting on
the assumption the order is gone. Pass `-1` as `order_id` to cancel
**all** open orders on the account (kill-switch path).

---

## 8. Known gotchas (CP-API-specific)

1. **Reply-prompt cascade can deadlock.** ibind's
   `order_submission_lock` is held for the entire `place_order` call
   including all reply iterations. If a prompt cascade hangs (e.g.
   the server doesn't respond), no other order can be submitted. Set
   an HTTP timeout on the `IbkrClient` and handle the resulting
   exception as "broker connection broken — circuit-break the
   strategy."
2. **Session keep-alive.** CP API sessions expire after **~5 min of
   inactivity**. Even with OAuth 1.0a (so no daily re-auth), the
   `/iserver/auth/status` ping must fire every 60–90s to keep the
   brokerage session warm. ibind has `tickle()` (`session_mixin.py`)
   — wire it on a background scheduler. If the session drops while a
   combo is working, **the order itself persists broker-side** (IBKR
   holds it until DAY expiry or explicit cancel) — only our ability
   to monitor it via API breaks until we re-auth.
3. **`conidex` parsing is strict.** Wrong semicolon count, missing
   forward slash, decimal ratio (e.g. `1.0` instead of `1`) → silent
   400 with an unhelpful "Order couldn't be submitted" error. ibind
   re-raises this as `ExternalBrokerError` (see `handle_questions`
   error branches). Validate the conidex string format with a regex
   before submit.
4. **One-order-at-a-time.** From the `place_order` docstring:
   *"Developers should not attempt to place another order until the
   previous order has been fully acknowledged, that is, when no
   further warnings are received deferring the client to the reply
   endpoint."* ibind enforces this via `order_submission_lock` but
   the practical implication is that **bulk-submitting a basket of
   ICs across strikes serializes on the lock** — measure the
   total-submission latency in your paper-trading runs. For our
   "one IC per signal" flow this is a non-issue.
5. **$0.05 tick-size rule on CBOE COB** — see §3.
6. **SPX has no 6-leg combos.** Documented IBKR limit for CBOE
   complex orders is 5 legs on SPX. Our 4-leg IC is comfortably
   inside; flagged for completeness in case a future "ratio
   condor" or "broken-wing butterfly + hedge" idea blows the limit.
7. **`coid` collisions within 24h** return a `400 Bad Request` with
   `"Order couldn't be submitted: Local order ID=…"` — ibind's
   `handle_questions` has an explicit branch for this string. Always
   timestamp the `coid` to microsecond precision (or use a UUID
   fragment).
8. **`force=true` on `live_orders`.** A subtle one — if any code path
   calls `live_orders(filters=[...])`, subsequent WebSocket `sor`
   subscriptions silently lose data until we call
   `live_orders(force=True)` to reset the server-side filter state.
   Either always pass `force=True` on REST polls or never filter on
   the REST path; mixing the two is the bug.

---

## 9. Summary — what changes vs. the TWS-API research file

| Concept | TWS API (`03_ib_orders_positions.md`) | CP API (this doc) |
|---|---|---|
| Combo contract spec | `Contract` with `secType='BAG'`, `comboLegs=[ComboLeg(...)]` | `conidex` string field on the order body |
| Per-leg direction | `ComboLeg.action='BUY'/'SELL'` | Sign of `ratio` in `conidex` (`/1` vs `/-1`) |
| Atomic fill toggle | `smartComboRoutingParams=[TagValue('NonGuaranteed', '0')]` | **Not directly exposed** — exchange-routed (`@CBOE`) for hard atomic, client-side reconciliation otherwise (§4.2) |
| Order submission | `client.placeOrder(orderId, contract, order)` | `client.place_order(OrderRequest(...), answers, account_id)` |
| Async fill tracking | `EWrapper.orderStatus()` callback | `sor` WebSocket topic |
| Reply prompts | None — TWS API has no prompt mechanism | `POST /iserver/reply/{id}` cascade; ibind handles via `handle_questions` |
| Sign of net credit | `lmtPrice` positive for natural debit, negative for natural credit on `BUY` side | **Same rule** — IBKR's price-sign convention is identical across APIs (§3) |

**The key insight:** the CP API combo model is _flatter_ than TWS —
no `Contract`/`ComboLeg[]` object graph, just a string-encoded
`conidex`. The trade-off is that fine-grained routing controls
(`smartComboRoutingParams`, `algoStrategy`, etc.) are either absent or
documented only in the TWS-API path; we accept the CP API's defaults
and engineer atomic-fill semantics client-side when required.

---

## Sources

- IBKR Campus — Web API v1.0 Documentation: <https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/>
- IBKR Campus — Order Types: <https://www.interactivebrokers.com/campus/ibkr-api-page/order-types/>
- IBKR Campus — Trading Web API: <https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-trading/>
- IBKR Campus — Handling Options Chains: <https://www.interactivebrokers.com/campus/ibkr-quant-news/handling-options-chains/>
- IBKR Campus — Complex Orders: <https://www.interactivebrokers.com/campus/trading-lessons/complex-orders/>
- IBKR Users Guide — Advanced Combo Routing: <https://www.ibkrguides.com/traderworkstation/advanced-combo-routing.htm>
- IBKR Users Guide — Notes on Combination Orders: <https://www.ibkrguides.com/traderworkstation/notes-on-combination-orders.htm>
- IBKR Trading API Documentation Guide (Scribd mirror with full body schemas): <https://www.scribd.com/document/876499086/Trading-Web-API-Ibkr-API-Ibkr-Campus>
- Voyz/ibind — repo root: <https://github.com/Voyz/ibind>
- Voyz/ibind — `order_mixin.py`: <https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_client_mixins/order_mixin.py>
- Voyz/ibind — `contract_mixin.py`: <https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_client_mixins/contract_mixin.py>
- Voyz/ibind — `ibkr_utils.py` (OrderRequest, handle_questions, QuestionType): <https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_utils.py>
- Voyz/ibind — `ibkr_ws_client.py`: <https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_ws_client.py>
- Voyz/ibind — `examples/rest_06_options_chain.py` (2-leg spread combo): <https://github.com/Voyz/ibind/blob/master/examples/rest_06_options_chain.py>
- Voyz/ibind — `examples/rest_04_place_order.py` (single-leg pattern, reply flow): <https://github.com/Voyz/ibind/blob/master/examples/rest_04_place_order.py>
- Voyz/ibind — IbkrClient wiki: <https://github.com/Voyz/ibind/wiki/API-Reference-%E2%80%90-IbkrClient>
- QuantRocket forum — whatif on combo orders (NonGuaranteed default behaviour on CP API): <https://support.quantrocket.com/t/what-if-on-combo-order/2181/4>
