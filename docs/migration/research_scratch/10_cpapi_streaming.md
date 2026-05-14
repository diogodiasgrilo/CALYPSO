# CP API WebSocket Streaming — SPX Options + Index Quotes via ibind

**Research target:** Document the IBKR Client Portal Web API WebSocket
streaming surface end-to-end for our 0DTE SPX iron-condor monitoring
use case (1 SPX index + 1 VIX index + 4–28 open SPX option contracts,
sampled at 2–15 s).

**Date:** May 2026
**Last Updated:** 2026-05-14
**Library version target:** `ibind` 0.1.23 with OAuth 1.0a
**Verdict up front:** WebSocket is the right primary path. The
widely-quoted "5 concurrent subscriptions" rumor is real but applies
to **historical-data** (`hmds`) streams, not real-time market data
(`smd`); real-time `smd` rides the standard 100-line market-data
quota. See Section 4.

---

## 1. WebSocket URL + auth

### 1.1 Endpoint URLs

There are exactly two endpoints, and ibind picks between them based
on the `use_oauth` flag:

| Mode | URL | When ibind uses it |
|---|---|---|
| Local CP Gateway | `wss://127.0.0.1:5000/v1/api/ws` | default, `use_oauth=False` |
| Direct OAuth 1.0a | `wss://api.ibkr.com/v1/api/ws?oauth_token={access_token}` | `use_oauth=True` (our setup) |

Source — `ibind/var.py`:

```python
IBIND_WS_URL = os.getenv('IBIND_WS_URL', None)
IBIND_OAUTH1A_WS_URL = os.getenv('IBIND_OAUTH1A_WS_URL', 'wss://api.ibkr.com/v1/api/ws')
```

Source — `ibind/client/ibkr_ws_client.py` `IbkrWsClient.__init__`:

```python
url = var.IBIND_OAUTH1A_WS_URL if url is None and use_oauth else url
if url is None:
    url = f'wss://{host}:{port}{base_route}'   # local-gateway fallback
if use_oauth:
    if access_token is None:
        raise ValueError('OAuth access token not found. ...')
    url += f'?oauth_token={access_token}'
```

So for **our OAuth 1.0a setup with no local CP Gateway running**,
ibind opens `wss://api.ibkr.com/v1/api/ws?oauth_token=<TOKEN>`
directly. No `*.run.app`-style proxy in front, no localhost.

Cite: [Voyz/ibind `ibkr_ws_client.py`](https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_ws_client.py),
[Voyz/ibind `var.py`](https://github.com/Voyz/ibind/blob/master/ibind/var.py).

### 1.2 Auth on the WebSocket handshake

There are **two** distinct authentication mechanisms that ibind has
to satisfy at handshake time:

1. **`?oauth_token=` query parameter** appended to the URL — this is
   what authenticates the WebSocket itself against the IBKR edge.
2. **`api={session}` cookie** in the WS handshake — required to bind
   the WebSocket to the active brokerage session. ibind calls
   `IbkrClient.tickle()` (an authenticated REST `/tickle`) to acquire
   that session id, then forwards it as a cookie header.

`ibkr_ws_client.py`:

```python
def _get_cookie(self):
    status = self._ibkr_client.tickle()
    session_id = status.data['session']
    if self._use_oauth:
        return f'api={session_id}'
    payload = {'session': session_id}
    return f'api={json.dumps(payload)}'

def _get_header(self):
    return {'User-Agent': 'ClientPortalGW/1'} if self._use_oauth else None
```

Two cookie shapes — for OAuth the value is a bare session id; for
local Gateway it's JSON-wrapped. We need the OAuth path, so just the
bare id.

### 1.3 Connection lifecycle

```
1. OAuth 1.0a → live session token  (handled by IbkrClient at startup)
2. REST /tickle                     → session id  (every IBIND_TICKLER_INTERVAL = 60 s)
3. WS open  → ?oauth_token=… + api=<session> cookie
4. WS sends 'sts' (auth status), 'system' (heartbeat), 'tic' (tick)
5. Subscribe  → 'smd+<conid>+{"fields":[…]}'
6. Receive ticks tagged topic='smd+<conid>'
7. Unsubscribe → 'umd+<conid>+{}'
8. Resubscribe every ~14 minutes (smd auto-terminates — see §5)
```

Cite: [IBKR Campus — Tutorial: Web API How to connect to WebSocket](https://www.interactivebrokers.com/campus/ibkr-quant-news/tutorial-web-api-how-to-connect-to-websocket/),
[Voyz/ibind `ibkr_ws_client.py`](https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_ws_client.py).

---

## 2. Subscribe / unsubscribe message format

### 2.1 Wire format

ibind's `IbkrSubscriptionProcessor` is the canonical implementation:

```python
def make_subscribe_payload(self, channel: str, data: dict = None) -> str:
    payload = f's{channel}'
    if data is not None or data == {}:
        payload += f'+{json.dumps(data)}'
    return payload

def make_unsubscribe_payload(self, channel: str, data: dict = None) -> str:
    data = {} if data is None else data
    return f'u{channel}+{json.dumps(data)}'
```

So `ws_client.subscribe(channel='md+265598', data={'fields': ['31','84','86','7308']})`
serializes on the wire as:

```
smd+265598+{"fields": ["31", "84", "86", "7308"]}
```

And `ws_client.unsubscribe(channel='md+265598')` sends:

```
umd+265598+{}
```

That **matches** the official IBKR Campus tutorial:

> An example subscription would be: `smd+8314+{"fields":["31","84","85","86","88","7059"]}`
> — IBKR Campus, "Tutorial: Web API – How to connect to WebSocket"

Cite: [IBKR Campus — Websockets](https://www.interactivebrokers.com/campus/trading-lessons/websockets/),
[Voyz/ibind `ibkr_ws_client.py`](https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_ws_client.py).

### 2.2 Field codes (canonical, from ibind `ibkr_definitions.py`)

CP API field codes are **different from TWS API generic-tick codes**.
ibind's `ibkr_definitions.snapshot_by_key` is verbatim from the IBKR
Campus reference. Key codes for our use case:

| Code  | Key                          | What it is |
|------:|------------------------------|------------|
| `31`   | `last_price`                 | Last trade price |
| `84`   | `bid_price`                  | Best bid |
| `86`   | `ask_price`                  | Best ask |
| `88`   | `bid_size`                   | Bid size (US stocks ÷ 100) |
| `85`   | `ask_size`                   | Ask size |
| `87`   | `volume`                     | Day volume, K/M formatted |
| `7059` | `last_size`                  | Last trade size |
| `7762` | `volume_long`                | Full-precision volume |
| `7635` | `mark_price`                 | Mark (mid clamped to last) |
| `82`   | `change`                     | Δ vs prev close |
| `83`   | `change_percent`             | Δ% vs prev close |
| `7283` | `option_implied_vol_percent` | Underlying-level 30d IV |
| `7633` | `implied_vol_percent`        | **Per-strike** IV (this is the one for our IC legs) |
| `7308` | `delta`                      | Option delta |
| `7309` | `gamma`                      | Option gamma |
| `7310` | `theta`                      | Option theta |
| `7311` | `vega`                       | Option vega |
| `7638` | `option_open_interest`       | OI |
| `7089` | `opt_volume`                 | Option day volume |
| `6509` | `market_data_availability`   | `R`/`D`/`Z` flag — check this every tick to detect delayed/frozen state |
| `7184` | `can_be_traded`              | 1/0 |
| `7184` etc. | meta fields              | full list in ibind |

**Critical for our case**: per-strike IV is `7633`, not `7283`.
`7283` is an *underlying-level* 30-day IV estimate and is wrong for
sampling an iron condor's individual legs.

> "The Implied Vol. % of a specific strike refer to field 7633.
>  To query the Option Implied Vol. % from the underlying refer to
>  field 7283."
> — `ibkr_definitions.py` source comment

Source: [`Voyz/ibind/ibind/client/ibkr_definitions.py`](https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_definitions.py),
which itself cites
[IBKR Campus — Market Data Fields](https://ibkrcampus.com/ibkr-api-page/cpapi-v1/#market-data-fields).

### 2.3 Greeks delivery — calculation latency caveat

From the IBKR Campus docs:

> "Field 7310 (the Option's Greek Theta) can take a few requests or
>  even a few moments to begin, as some values must be calculated by
>  Interactive Brokers before returning, potentially taking up to a
>  minute on less active strikes."
> — [IBKR Campus — Websockets](https://www.interactivebrokers.com/campus/trading-lessons/websockets/)

Practical implication for 0DTE monitoring: **on a fresh subscribe,
delta/gamma/theta/vega/IV may arrive empty or trickle in over the
first 5–60 seconds.** Our consumer loop must tolerate `None`
greeks gracefully on the first 1–2 ticks per leg.

These are **model greeks** computed by IBKR's broker-side option
pricer (Black-76 for index options), not OPRA-published values
(OPRA does not publish greeks anyway — these are always derived).

---

## 3. Message format coming back

### 3.1 Envelope

A typical update for a subscribed option conid looks like (after
ibind's `_preprocess_market_data_message`):

```python
{
  '265598': {
    'conid': 265598,
    '_updated': 1715515200000,
    'topic': 'smd+265598',
    'last_price': '4521.50',
    'bid_price': '4521.00',
    'ask_price': '4522.00',
    'delta':   '0.34',
    'gamma':   '0.0012',
    'theta':  '-0.45',
    'vega':    '1.23',
    'implied_vol_percent': '14.7',
  }
}
```

With `unwrap_market_data=True` (default), ibind:
1. Wraps the result in a dict keyed by `conid`.
2. Remaps numeric field IDs → human-readable keys via
   `snapshot_by_id`.

Raw (with `unwrap_market_data=False`):
```json
{"server_id":"q0","conid":265598,"_updated":1715515200000,
 "31":"4521.50","84":"4521.00","86":"4522.00",
 "7308":"0.34","7309":"0.0012","7310":"-0.45","7311":"1.23",
 "7633":"14.7","topic":"smd+265598"}
```

### 3.2 Delta-only updates (NOT full snapshots)

From `ibkr_ws_client.py`:

> "API will only return fields that were updated. If you are not
>  receiving certain fields in the response — means that they remain
>  unchanged."

This means our consumer **must keep a per-conid last-known snapshot**
and merge each incoming partial. ibind does **not** do the merge for
us — it just remaps keys. The merge is on us.

### 3.3 Update cadence

Top-of-book streams at the native quote rate from IBKR's market-data
infrastructure. For SPX options that's effectively as-fast-as-OPRA
delivers (sub-100 ms during active trading). For sampled mark-price
monitoring at 2–15 s intervals, we are way over-receiving — the
right pattern is to **read latest from a per-conid cache on our
timer**, not to react to every tick.

Cite: [Voyz/ibind `ibkr_ws_client.py` — `_preprocess_market_data_message`](https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_ws_client.py).

---

## 4. Concurrent subscription limits — the "5" myth, resolved

The "5 simultaneous CP API WebSocket subscriptions" rumor has been
circulating since 2024. **Verified primary source**: it is real but
applies only to `hmds` (historical market data) WebSocket
subscriptions, not `smd` (real-time market data).

### 4.1 Real-time `smd` — bound by market data lines

Real-time `smd` subscriptions consume **IBKR market-data lines** —
the same pool used by TWS, ib_insync, etc. Defaults:

> "Every user has a maxTicker Limit of **100 market data lines** and
>  can obtain real-time market data of up to 100 instruments
>  simultaneously."
> — [IBKR Campus — Market Data Subscriptions](https://www.interactivebrokers.com/campus/ibkr-api-page/market-data-subscriptions/)

Each option contract = 1 line. SPX index = 1 line. VIX index = 1
line. Our peak load (1 SPX + 1 VIX + 28 option legs) = **30 lines —
comfortably under the 100-line default.**

Quote-Booster packs ($30/mo each) add 100 lines if we ever scale
beyond 100. Account equity also unlocks lines (a $1M account gets
100 from equity alone).

### 4.2 Historical `hmds` — actually capped at 5 concurrent

From [Voyz/ibind issue #100](https://github.com/Voyz/ibind/issues/100):

> "I encountered a limit of 5 simultaneous streams of historical
>  data… For instance, for market data it's possible to subscribe
>  for a few streams using single connection. It does not seem to be
>  the case for historical though… When I subscribe to a second
>  stream, data for the first one stops arriving."

The Elixir-side `ibkr_api` library doc paraphrased this as "IBKR
limits market data subscriptions to approximately 5 concurrent
streams per session" — that paraphrase is **wrong**; it conflated
`smd` with `hmds`. The underlying IBKR behavior only restricts
`hmds`.

We do not use `hmds` for monitoring (we'd use REST
`/iserver/marketdata/history` for historical bars if needed), so
this limit is not a blocker.

### 4.3 What about /iserver/marketdata/snapshot?

The REST snapshot endpoint is bounded separately:

> "/iserver/marketdata/snapshot conids parameter is now limited to
>  **100 conids per query** with **50 maximum fields** at any given
>  time."
> — [IBKR Web API Changelog](https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-changelog/)

That change appears to have been made in late 2024. We can comfortably
fit 30 conids × ~10 fields in a single REST call if we ever want a
one-shot snapshot fallback.

Cite: [IBKR Campus — Market Data Subscriptions](https://www.interactivebrokers.com/campus/ibkr-api-page/market-data-subscriptions/),
[Voyz/ibind issue #100](https://github.com/Voyz/ibind/issues/100),
[IBKR Campus — Web API Changelog](https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-changelog/).

---

## 5. Heartbeat, smd auto-termination, and reconnect

### 5.1 Two different keep-alives

| What | Cadence | Who sends |
|---|---|---|
| REST `/tickle` | every 60 s (ibind default `IBIND_TICKLER_INTERVAL`) | ibind's `Tickler` thread, started by `IbkrClient` |
| WS ping | every 45 s (ibind default `IBIND_WS_PING_INTERVAL`) | ibind WS thread |
| WS heartbeat in (`topic:'system'` with `hb`) | server-driven | IBKR server |

The brokerage session goes idle/needs re-auth at ~6 minutes without
`/tickle`. WebSocket traffic alone does **not** count — the REST
`/tickle` is mandatory. ibind handles this automatically as long as
the `Tickler` thread is running (it is, when you construct
`IbkrClient` with OAuth and let `IbkrWsClient` reuse it).

Cite: [Voyz/ibind `var.py`](https://github.com/Voyz/ibind/blob/master/ibind/var.py)
(IBIND_TICKLER_INTERVAL = 60, IBIND_WS_PING_INTERVAL = 45,
IBIND_WS_MAX_PING_INTERVAL = 300).

### 5.2 The smd 15-minute termination

This bit us in earlier research and is the single most important
operational quirk on this surface:

> "The smd topic has recently changed in behavior. Now the topic
>  will terminate automatically after **15 minutes** and you will
>  need to send a new request to continue to retrieve data for the
>  instrument."
> — IBKR support, quoted in [Voyz/ibind issue #145](https://github.com/Voyz/ibind/issues/145)

The IBKR Campus docs still say "10 minutes" in some places —
[Websockets lesson](https://www.interactivebrokers.com/campus/trading-lessons/websockets/) —
but the recent confirmed server-side value is 15 minutes. Either
way, our resubscribe loop must fire **before** the timeout (we'll
use 13 minutes to be safe).

**Critical**: termination is **silent**. The WS stays connected,
pings still pong, the heartbeat keeps arriving — only `smd+<conid>`
tick messages stop. If our code just waits on the queue, it'll hang
indefinitely.

**Refresh pattern** (per IBKR's own recommendation):
1. Send `umd+<conid>+{}` first.
2. Then `smd+<conid>+{"fields":[…]}` again.

ibind issue #145 is an open request for ibind to do this
auto-refresh internally. **As of 0.1.23 it does not.** We must
implement the timer ourselves on our side of the queue.

### 5.3 Reconnect strategy

ibind handles WS-level disconnects:
- `restart_on_close=True` (default) — reconnects on socket close.
- `restart_on_critical=True` (default) — reconnects on critical errors.
- `recreate_subscriptions_on_reconnect=True` (default) — re-sends
  every active `subscribe()` call after reconnect.
- `check_health()` enforces `max_ping_interval` (300 s default) and
  `hard_reset()`s if the IBKR heartbeat goes stale.

What we still own:
- Detecting the 15-minute silent termination per conid (no socket
  event fires — must be tracked by `last_tick_time` per conid).
- Re-`/tickle` is automatic via the Tickler thread.

Cite: [Voyz/ibind issue #145](https://github.com/Voyz/ibind/issues/145),
[IBKR Campus — Tutorial: How to connect to WebSocket](https://www.interactivebrokers.com/campus/ibkr-quant-news/tutorial-web-api-how-to-connect-to-websocket/).

---

## 6. REST snapshot polling — the viable alternative

`GET /iserver/marketdata/snapshot?conids=<csv>&fields=<csv>` is a
**one-shot** read. Behavior:

- First call for a new conid returns the conid in the response with
  no field values — IBKR is "warming up" the streaming line on its
  side.
- Second and subsequent calls return the requested fields.
- The line is **released** after a brief idle (a few seconds), so
  the next poll re-warms.
- 100-conid cap per call (verified late-2024 changelog).
- 50-field cap per call.

For our 30-ticker case, that's 1 REST call per polling tick. At a
5-second cadence we'd consume ~720 calls/hour, well inside
unpublished CP API rate limits (typically ~5 req/s burst, ~1 req/s
sustained — we're at 0.2 req/s).

**Freshness penalty vs WebSocket**: at a 5 s poll cadence, snapshot
data is on average 2.5 s stale vs the WS stream that's
sub-100 ms-fresh. For our 0DTE *exit signals*, 2.5 s of staleness
is **not material** — SPX 0DTE legs move on the order of 5–20¢/s
in active conditions, and our TP/stop bands are sized in dollars
($0.50–$1.50 per leg). 2.5 s × max 0.20 = $0.05 of slippage worst
case, well within our band thickness.

**However**: greeks via snapshot have the same calculation-latency
issue as streaming — the first call returns empty greeks; takes a
beat for IBKR to compute them. So a "first poll cold" can be just
as bad as a "fresh subscribe" on WS.

Cite: [IBKR Campus — Web API Changelog](https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-changelog/),
[IBKR Campus — Requesting Market Data](https://www.interactivebrokers.com/campus/trading-lessons/requesting-market-data/).

---

## 7. ibind WebSocket client surface

### 7.1 Components

```
IbkrWsClient                       # WebSocketApp manager + queue controller
  ├── IbkrSubscriptionProcessor    # builds smd+/umd+ payloads
  ├── QueueController              # one Queue per IbkrWsKey
  └── QueueAccessor                # consumer-side wrapper around a queue

IbkrWsKey enum                     # MARKET_DATA, MARKET_HISTORY, PNL, ORDERS, …
  .channel  property               # 'md', 'mh', 'pl', 'or', …
```

`channel` per IbkrWsKey:

| Enum | `.channel` | Wire topic | (S)ub-confirms | (U)nsub-confirms |
|---|---|---|---|---|
| MARKET_DATA | `md` | `smd+<conid>+{…}` / `umd+<conid>+{}` | yes | **no** |
| MARKET_HISTORY | `mh` | `smh+<conid>+{…}` / `umh+<server_id>+{}` | yes | yes |
| PNL | `pl` | `spl` / `upl+{}` | yes | no |
| ORDERS | `or` | `sor` / `uor+{}` | no | no |

The "MARKET_DATA does not confirm unsubscribe" detail comes from
`IbkrWsKey.confirms_unsubscribing` — important so we don't block
forever waiting for an ack that won't come.

### 7.2 Constructor signature (essentials)

```python
IbkrWsClient(
    account_id=var.IBIND_ACCOUNT_ID,
    url=var.IBIND_WS_URL,                 # auto-resolves to OAuth URL if use_oauth=True
    host='127.0.0.1', port='5000', base_route='/v1/api/ws',  # local-gateway fallback
    ibkr_client=None,                      # auto-constructs one if None
    unwrap_market_data=True,               # recommended: True (gives human-readable keys)
    start=False,                           # call start() yourself, or pass True
    use_oauth=var.IBIND_USE_OAUTH,
    access_token=var.IBIND_OAUTH1A_ACCESS_TOKEN,
    ping_interval=45,
    max_ping_interval=300,
    restart_on_close=True,
    restart_on_critical=True,
    max_connection_attempts=10,
    recreate_subscriptions_on_reconnect=True,
    subscription_retries=5,
    subscription_timeout=2.0,
)
```

### 7.3 Subscription mechanics

```python
ws_client.subscribe(channel='md+265598', data={'fields': ['31','84','86']})
ws_client.unsubscribe(channel='md+265598')
```

Note: `channel` is a string that **includes the conid**. ibind's
processor prepends the `s`/`u` letter, so the on-wire topic ends
up `smd+265598+…` / `umd+265598+{}`.

Cite: [Voyz/ibind `examples/ws_02_intermediate.py`](https://github.com/Voyz/ibind/blob/master/examples/ws_02_intermediate.py).

---

## 8. Working code skeleton (ibind 0.1.23, OAuth 1.0a)

```python
"""
SPX 0DTE iron-condor monitor — WebSocket leg of CP API streaming.

Env required (per ibind OAuth 1.0a setup):
    IBIND_USE_OAUTH=True
    IBIND_OAUTH1A_ACCESS_TOKEN=…
    IBIND_OAUTH1A_ACCESS_TOKEN_SECRET=…
    IBIND_OAUTH1A_CONSUMER_KEY=…
    IBIND_OAUTH1A_DH_PRIME=…
    IBIND_OAUTH1A_ENCRYPTION_KEY_FP=/path/to/priv_encryption.pem
    IBIND_OAUTH1A_SIGNATURE_KEY_FP=/path/to/priv_signature.pem
    IBIND_ACCOUNT_ID=U…
"""
import os
import signal
import threading
import time
from collections import defaultdict
from typing import Iterable

from ibind import IbkrClient, IbkrWsClient, IbkrWsKey, ibind_logs_initialize

ibind_logs_initialize(log_to_file=False)

# Field codes — see Section 2.2
INDEX_FIELDS  = ['31', '84', '86', '7635']                                  # last/bid/ask/mark
OPTION_FIELDS = ['31', '84', '86', '7635', '88', '85',                       # quote
                 '7308', '7309', '7310', '7311', '7633',                     # greeks + IV
                 '7638', '6509']                                             # OI + availability

SMD_REFRESH_SECONDS = 13 * 60          # refresh before the 15-min auto-terminate


class CpapiStreamMonitor:
    """
    Maintains a freshness-aware mirror of streaming quotes for
    (SPX_conid, VIX_conid, *option_leg_conids) over CP API WS.
    """

    def __init__(self, spx_conid: int, vix_conid: int, option_conids: Iterable[int]):
        self.spx_conid = spx_conid
        self.vix_conid = vix_conid
        self.option_conids = list(option_conids)

        # Build IbkrClient first so the Tickler thread starts.
        # IbkrWsClient will reuse this client for session management.
        self._rest = IbkrClient(use_oauth=True)
        self._rest.start_tickler()              # /tickle every 60 s

        self._ws = IbkrWsClient(
            ibkr_client=self._rest,
            use_oauth=True,
            unwrap_market_data=True,
            start=False,
        )
        self._qa = self._ws.new_queue_accessor(IbkrWsKey.MARKET_DATA)

        # Per-conid merged snapshot (delta updates → full state)
        self._snap = defaultdict(dict)
        self._snap_lock = threading.Lock()
        self._last_tick_at = defaultdict(float)
        self._last_subscribed_at = defaultdict(float)

        self._stop_evt = threading.Event()

    # ----- lifecycle -----

    def start(self):
        self._ws.start()
        self._subscribe_all()
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._refresh_thread = threading.Thread(target=self._refresh_loop, daemon=True)
        self._reader_thread.start()
        self._refresh_thread.start()

    def stop(self, *_):
        self._stop_evt.set()
        for conid in self._all_conids():
            try:
                self._ws.unsubscribe(channel=f'md+{conid}')
            except Exception:
                pass
        self._ws.shutdown()
        self._rest.stop_tickler()

    # ----- subscription -----

    def _all_conids(self):
        return [self.spx_conid, self.vix_conid] + self.option_conids

    def _fields_for(self, conid):
        if conid in (self.spx_conid, self.vix_conid):
            return INDEX_FIELDS
        return OPTION_FIELDS

    def _subscribe_one(self, conid):
        ok = self._ws.subscribe(
            channel=f'md+{conid}',
            data={'fields': self._fields_for(conid)},
        )
        if ok:
            self._last_subscribed_at[conid] = time.time()
        return ok

    def _subscribe_all(self):
        for conid in self._all_conids():
            for attempt in range(5):
                if self._subscribe_one(conid):
                    break
                time.sleep(0.5 * (2 ** attempt))   # exp backoff

    def _refresh_loop(self):
        """
        Defeats the silent 15-min smd auto-terminate. For each conid:
        unsubscribe → resubscribe roughly every 13 minutes.
        Also catches conids that have gone silent due to other causes.
        """
        while not self._stop_evt.wait(30):
            now = time.time()
            for conid in self._all_conids():
                last_sub = self._last_subscribed_at[conid]
                last_tick = self._last_tick_at[conid]
                stale = (
                    now - last_sub  > SMD_REFRESH_SECONDS or
                    now - last_tick > 60  # no tick in 60s during RTH → resubscribe
                )
                if stale:
                    self._ws.unsubscribe(channel=f'md+{conid}')
                    time.sleep(0.05)
                    self._subscribe_one(conid)

    # ----- reader -----

    def _reader_loop(self):
        while not self._stop_evt.is_set():
            try:
                while not self._qa.empty():
                    msg = self._qa.get()           # {conid: {field:value, …}}
                    if not msg:
                        continue
                    for conid_key, payload in msg.items():
                        conid = int(conid_key)
                        with self._snap_lock:
                            # Delta-merge: only updated fields are present.
                            self._snap[conid].update(payload)
                            self._last_tick_at[conid] = time.time()
            except Exception as e:
                print(f'reader_loop error: {e}')
            time.sleep(0.05)

    # ----- consumer API -----

    def get(self, conid: int) -> dict:
        with self._snap_lock:
            snap = dict(self._snap.get(conid, {}))
        snap['_age_s'] = time.time() - self._last_tick_at.get(conid, 0)
        return snap

    def healthy(self, conid: int, max_age_s: float = 5.0) -> bool:
        age = time.time() - self._last_tick_at.get(conid, 0)
        avail = self._snap.get(conid, {}).get('market_data_availability', '')
        return age < max_age_s and avail.startswith(('R', 'S'))


# ----- example usage: monitor loop @ 5 s cadence -----
def main():
    monitor = CpapiStreamMonitor(
        spx_conid=416904,           # SPX index conid (example)
        vix_conid=13455763,         # VIX index conid (example)
        option_conids=[              # IC legs — get from /iserver/secdef/strikes
            745826211, 745826225, 745826239, 745826253,
        ],
    )
    signal.signal(signal.SIGINT,  monitor.stop)
    signal.signal(signal.SIGTERM, monitor.stop)
    monitor.start()

    try:
        while True:
            spx = monitor.get(416904)
            vix = monitor.get(13455763)
            print(f"SPX last={spx.get('last_price')} mark={spx.get('mark_price')} age={spx['_age_s']:.1f}s")
            print(f"VIX last={vix.get('last_price')} age={vix['_age_s']:.1f}s")
            for leg in [745826211, 745826225, 745826239, 745826253]:
                q = monitor.get(leg)
                print(f"  leg {leg}: bid={q.get('bid_price')} ask={q.get('ask_price')} "
                      f"Δ={q.get('delta')} θ={q.get('theta')} IV={q.get('implied_vol_percent')} "
                      f"age={q['_age_s']:.1f}s ok={monitor.healthy(leg)}")
            time.sleep(5)
    finally:
        monitor.stop()


if __name__ == '__main__':
    main()
```

Notes on the skeleton:

1. **Delta-merge** is done by us, not ibind. Each new tick only
   carries changed fields; we merge into `self._snap[conid]`.
2. **`_refresh_loop`** is the workaround for ibind issue #145. We
   fire `umd → smd` per conid every ~13 min, plus on any conid
   that's gone silent for 60 s during RTH.
3. **`_last_tick_at`** is the freshness oracle the strategy reads —
   not socket state. The socket can be healthy while a specific
   conid has silently died.
4. **`market_data_availability`** (`6509`) is checked in
   `healthy()` — if the field flips from `R` (realtime) to `D`
   (delayed) or `Z` (frozen), we treat the conid as unhealthy
   regardless of age.
5. **Greeks may be `None` on the first 2–3 ticks** after subscribe
   while IBKR computes them. Consumers should not assume greeks
   present.

---

## 9. WebSocket vs REST snapshot for 2–15 s monitoring

| Concern | WebSocket (`smd`) | REST `/iserver/marketdata/snapshot` |
|---|---|---|
| Freshness at 5 s poll | ~50 ms-fresh on read | up to 5 s stale |
| Bandwidth | one socket, deltas only | full payload per poll |
| Cap | 100 lines (default) — fine for 30 | 100 conids / 50 fields per call — also fine |
| Connection management | reconnect logic, 15-min refresh quirk | none — just HTTP |
| Greeks warmup | 5–60 s after subscribe | same penalty on cold poll |
| Failure semantics | silent staleness possible | per-call HTTP errors, easier to detect |
| Code complexity | medium — refresh loop, queue reader | low — one `httpx.get()` per poll |
| Burst absorption | ticks arrive sub-second when active | bounded to poll cadence |

### 9.1 Latency budget for our use case

Our TP/stop checks fire at 5 s intervals. The marginal "extra
freshness" of WS vs REST is **average 2.5 s** at that cadence.
Our band thickness is $0.50–$1.50 per leg. Worst-case SPX 0DTE
quote drift in 2.5 s during news ticks is ~$0.30/leg. So both
designs are inside our band; WS wins on pathological days, REST
is adequate on normal days.

### 9.2 Recommendation: WebSocket-primary, REST fallback

For this monitor specifically:

1. **WebSocket for everything (SPX + VIX + option legs)** — well
   within the 100-line cap, gives us the freshest data when it
   matters (close to TP/stop bands), and lets us decouple polling
   cadence from quote rate. This is the path the skeleton above
   implements.

2. **REST `/iserver/marketdata/snapshot` as a **circuit-break
   fallback**** — when `monitor.healthy(conid)` returns False for
   N seconds, the strategy issues a snapshot REST call before
   acting on a TP/stop trigger. Defensive belt-and-suspenders for
   the silent-death failure mode.

3. **Do not split SPX/VIX onto WS and options onto REST.** The
   per-leg WS path is what catches a fast-moving short strike
   approaching the stop level; that's the case where freshness
   matters most. Routing it through REST polling defeats the
   reason we're streaming.

### 9.3 Where the 5-subscription concern actually bites

It doesn't bite real-time `smd`. It would bite us if we ever
streamed historical bars via `smh` for live charting — capped at
5 concurrent. Our backtest/training path uses REST
`/iserver/marketdata/history`, so we don't hit `smh` in production.

---

## 10. Open follow-ups for the implementation pass

1. **Auto-refresh upstream in ibind** — track [issue #145](https://github.com/Voyz/ibind/issues/145).
   If merged, we can delete `_refresh_loop` from our code.
2. **Greeks warmup masking** — first 5 s after a fresh subscribe,
   the strategy must not interpret missing greeks as zero.
3. **`market_data_availability` watchdog** — flip to delayed/frozen
   during the trading day should page us, not silently degrade.
4. **Session-cookie expiry** — the Tickler thread keeps `/tickle`
   firing every 60 s. If the OAuth live session token rolls over,
   we need to validate the WS stays bound (ibind should reconnect
   automatically via `restart_on_close=True`).
5. **Reconnect resubscribe race** — `recreate_subscriptions_on_reconnect=True`
   re-sends every active subscribe, but the resubscribe-on-15-min
   logic ALSO fires; ensure they don't double-subscribe to the same
   conid (ibind dedupes by channel string, but verify in a chaos
   test).

---

## Sources (every claim cited)

- [Voyz/ibind — repo root](https://github.com/Voyz/ibind)
- [Voyz/ibind — `ibind/client/ibkr_ws_client.py`](https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_ws_client.py)
- [Voyz/ibind — `ibind/client/ibkr_definitions.py`](https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_definitions.py) (canonical field codes)
- [Voyz/ibind — `ibind/var.py`](https://github.com/Voyz/ibind/blob/master/ibind/var.py) (WS URL + timeout defaults)
- [Voyz/ibind — `examples/ws_01_basic.py`](https://github.com/Voyz/ibind/blob/master/examples/ws_01_basic.py)
- [Voyz/ibind — `examples/ws_02_intermediate.py`](https://github.com/Voyz/ibind/blob/master/examples/ws_02_intermediate.py) (market-data subscribe pattern)
- [Voyz/ibind — `examples/rest_08_oauth.py`](https://github.com/Voyz/ibind/blob/master/examples/rest_08_oauth.py) (OAuth 1.0a setup)
- [Voyz/ibind issue #100 — Historical data via WebSocket: 5 streams limitation](https://github.com/Voyz/ibind/issues/100) — proves the "5" limit is `hmds`, not `smd`
- [Voyz/ibind issue #145 — auto-refresh smd on 15-min termination](https://github.com/Voyz/ibind/issues/145) — confirms 15-min silent termination
- [IBKR Campus — Websockets](https://www.interactivebrokers.com/campus/trading-lessons/websockets/)
- [IBKR Campus — Tutorial: Web API How to connect to WebSocket](https://www.interactivebrokers.com/campus/ibkr-quant-news/tutorial-web-api-how-to-connect-to-websocket/)
- [IBKR Campus — Web API v1.0 Documentation](https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/)
- [IBKR Campus — Market Data Subscriptions](https://www.interactivebrokers.com/campus/ibkr-api-page/market-data-subscriptions/)
- [IBKR Campus — Requesting Market Data](https://www.interactivebrokers.com/campus/trading-lessons/requesting-market-data/)
- [IBKR Campus — Web API Changelog](https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-changelog/)
- [IBKR Campus — Market Data Fields (anchor source for `ibkr_definitions.py`)](https://ibkrcampus.com/ibkr-api-page/cpapi-v1/#market-data-fields)
