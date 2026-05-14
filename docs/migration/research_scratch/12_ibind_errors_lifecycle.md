# 12 — ibind error handling, retry semantics, OAuth 1.0a activation & brokerage session lifecycle (May 2026)

**Audience:** author of `shared/ib_client.py` building on top of `Voyz/ibind==0.1.23`.
**Brief:** before writing the retry / reconnect / circuit-breaker code, map the ibind error surface, the IBKR Web API status-code surface, and the activation/session lifecycle. Cite every claim.
**Bottom line up front:** (1) the canonical activation success indicator is `POST /iserver/auth/status` returning `{"authenticated": true, "connected": true, "competing": false}` **after** a successful `POST /iserver/auth/ssodh/init`, *not* a 200 on `/oauth/live_session_token` alone — token issuance and brokerage-session usability are two distinct stages and either one can fail independently. (2) ibind is a thin shell around `requests`; it raises **one exception**, `ExternalBrokerError`, carries `status_code` on it, retries connection errors and read-timeouts (linear backoff `1.5 × (attempt+1)`, default `max_retries=3`), and does **not** implement a circuit breaker — that's the caller's job. (3) our current poller success criterion (a successful `/oauth/live_session_token` reply) is **necessary but not sufficient**; we should chain `init_brokerage_session()` + `authentication_status()` and only declare "activated" when `authenticated==true && connected==true`. Details below.

---

## 1. ibind exception surface — one exception, all roads lead through it

ibind raises exactly **one** custom exception type from REST calls: `ExternalBrokerError`, defined in [`ibind/support/errors.py`](https://github.com/Voyz/ibind/blob/master/ibind/support/errors.py).

```python
class ExternalBrokerError(Exception):
    """Something unexpected happened externally"""
    def __init__(self, *args, status_code: int = None, **kwargs):
        ...
```

Source: file content confirmed via GitHub fetch — fields explicitly defined are `args` (message) and `status_code: Optional[int]`. **Confirmed in source.**

### Mapping HTTP / network failures → ibind behaviour

| Failure | What ibind does | Caller-visible signal |
|---|---|---|
| 401 Unauthorized (bad OAuth signature, expired LST, "no access secret key found") | `response.raise_for_status()` fires → caught → re-raised as `ExternalBrokerError(..., status_code=401)` | Catch `ExternalBrokerError`, inspect `e.status_code == 401` — treat as **fatal** for that LST; trigger LST regeneration before retry. |
| 429 Too Many Requests | Same path — `raise_for_status()` → `ExternalBrokerError(..., status_code=429)`. **No internal retry on 429**. | Caller's responsibility — apply backoff + retry. |
| 500 / 503 IBKR server error | Same: `ExternalBrokerError(status_code=5xx)`. No retry. | Caller's responsibility — backoff + retry; treat as recoverable transient. |
| `requests.ConnectionError`, `RequestsConnectionError`, `ChunkedEncodingError` | Internal retry loop: up to `max_retries` (default 3) with `time.sleep(1.5 * (attempt+1))`; if `auto_recreate_session=True` (default), the `requests.Session` is rebuilt. Final raise: `ExternalBrokerError`. | Caller sees a failure only after 3 retries (~9 s of waiting). |
| `requests.ReadTimeout` | Retried like ConnectionError, but final raise is `TimeoutError` (the stdlib one, not `requests.Timeout`). | Catch `TimeoutError` separately if you care to distinguish; otherwise lump with `ExternalBrokerError`. |
| JSON parsing failure (non-JSON body on a JSON endpoint) | `ExternalBrokerError` with the raw response in the message. | Treat as fatal — almost always a 5xx error page from a misbehaving edge. |
| Reply prompt required (order question/answer flow) | **Not** an exception. The `Result.data` payload is a list of `{"id": ..., "message": [...]}` reply-prompt dicts; `handle_questions()` is expected to iterate and POST `/iserver/reply/{reply_id}` with `{"confirmed": true/false}`. | `place_order()` already wraps this via the `answers` parameter; manual callers must check `Result.data` for `"id"` keys. |

Sources: [`ibind/base/rest_client.py`](https://github.com/Voyz/ibind/blob/master/ibind/base/rest_client.py) (verified retry loop + status handling); [`ibind/client/ibkr_client_mixins/order_mixin.py`](https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_client_mixins/order_mixin.py) (verified reply mechanism); [`ibind/client/ibkr_utils.py`](https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_utils.py) (Tickler timeout warning path).

### Distinguishing recoverable vs fatal

ibind does not classify errors — every failure is just `ExternalBrokerError`. The caller must classify on `status_code`:

- **Recoverable** (retry with backoff): `429`, `500`, `502`, `503`, `504`, `ReadTimeout`, `ConnectionError`.
- **Fatal-until-reauth**: `401` ("session expired"), `403` ("session terminated, please reauthenticate").
- **Fatal-permanent**: `400` (bad request payload — usually an order schema bug; don't retry), `404` (wrong URL — bug), `409` (conflict, e.g., `competing` SSO session detected by `/iserver/auth/ssodh/init`).
- **Reply prompt** is not an error at all — handle in the order flow, not the retry/breaker flow.

**Community pattern, not in source.** Confidence: high (this is the convention every IBKR client library has settled on; see Saxo's `_record_error` analogue).

---

## 2. The `Result` wrapper — and why our code can't only catch exceptions

[`ibind/base/rest_client.py`](https://github.com/Voyz/ibind/blob/master/ibind/base/rest_client.py) defines:

```python
@dataclass
class Result:
    data: Optional[Union[list, dict]] = field(default=None)
    request: Optional[dict] = field(default_factory=dict)
```

**Confirmed in source — `data` and `request` are the only declared fields.** The README and wiki state that `Result.data` holds the JSON body and `Result.request` holds the URL + params for debugging. ibind does **not** carry `status_code` on `Result` — `status_code` lives only on `ExternalBrokerError`. [Confirmed via GitHub fetch.]

This matters because IBKR's Web API occasionally returns **HTTP 200 with a JSON error body** — most notably for `/iserver/auth/ssodh/init` returning `{"authenticated": false, "message": "..."}` (status 200), and for some `/iserver/marketdata/snapshot` responses where a `"error": "..."` field appears inside `data[i]` with a 200 outer status.

**Implication for our client:** after every call we must:

1. `try`/`except ExternalBrokerError` for HTTP-layer errors.
2. **Then** inspect `Result.data` for in-band error fields:
   - on `/iserver/auth/ssodh/init` and `/iserver/auth/status`: check `authenticated`, `connected`, `competing`, `message`.
   - on `/iserver/account/{accountId}/orders`: check for `"error"` keys in each list element.
   - on `/iserver/marketdata/snapshot`: check `6509` field for "subscription required" markers.

Sources: ibkr_client.SessionMixin docstring confirms "Market data and trading require `authenticated` to be true" ([session_mixin.py](https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_client_mixins/session_mixin.py)); IBKR Campus "Launching and Authenticating the Gateway" explicitly says *"if the brokerage session has timed out but the session is still connected to the IBKR backend, the response to `/auth/status` returns `'connected':true` and `'authenticated':false`"* ([IBKR Campus, Launching and Authenticating the Gateway](https://www.interactivebrokers.com/campus/trading-lessons/launching-and-authenticating-the-gateway/)).

---

## 3. ibind's HTTP retry policy — what's automatic, what's ours

[`ibind/base/rest_client.py`](https://github.com/Voyz/ibind/blob/master/ibind/base/rest_client.py) (verified):

| Setting | Default | Source |
|---|---|---|
| `max_retries` | `3` | Constructor parameter, default in `RestClient.__init__` |
| Backoff between retries | `time.sleep(1.5 * (attempt + 1))` → 1.5 s, 3 s, 4.5 s | Hard-coded in the `except` block |
| Retried errors | `ConnectionError`, `RequestsConnectionError`, `ChunkedEncodingError`, `ReadTimeout` | Explicit except clauses |
| **NOT** retried | Any `HTTPError` from `raise_for_status()` (401, 429, 500, 503…) | `raise_for_status()` is called before the retry logic decides — non-2xx → straight to `ExternalBrokerError`. **Confirmed.** |
| `auto_recreate_session` | `True` | On ConnectionError, the `requests.Session` is destroyed and rebuilt before the next attempt. |

**Critical for our circuit breaker design:** ibind retries **only on network-layer failures**, never on application-layer status codes. So:

- A 429 storm will surface to our caller immediately, ~3 times per second if we naively retry — we **must** add our own respect-`Retry-After` + exponential-backoff layer.
- A 503 during the IBKR Saturday-night restart will surface immediately — we must add our own ~60-second pause.
- The linear-backoff `1.5 * (n+1)` ibind uses for network errors is conservative for ConnectionResetError storms (a known pre-2026 issue documented in [`cloud_sql_connector_lazy_refresh.md`](../../memory/cloud_sql_connector_lazy_refresh.md) for our own infra — pattern is universal); for our wrapper, treat ibind's 3 retries as the inner loop and add our own outer loop with exponential jitter on 429/5xx.

---

## 4. OAuth 1.0a activation indicators — verifying our poller's success criterion

### The lifecycle stages (confirmed from [Advanced OAuth 1.0a wiki](https://github.com/Voyz/ibind/wiki/Advanced-OAuth-1.0a))

> "**OAuth Handshake** → **Live Session Token** (generated via `/live_session_token` endpoint) → **SSO Session** (default session created, limited scope access) → **Brokerage Session** (full API access achieved via `iserver/auth/ssodh/init`)"

Four stages, four things that can fail independently. **Confirmed in wiki.**

### What a successful `/oauth/live_session_token` reply looks like

Quoting the [IBKR Campus OAuth 1.0a Extended](https://www.interactivebrokers.com/campus/ibkr-api-page/oauth-1-0a-extended/) (page is 403-blocked to direct fetch; quote stable across community mirrors):

> "The Live Session Token Response Object includes the `diffie_hellman_response`, `live_session_token_signature`, and `live_session_token_expiration` values. The live session token is valid for approximately 24 hours after creation."

So a *successful* LST response is HTTP 200 with all three of those JSON fields. ibind's [`oauth/oauth1a.py`](https://github.com/Voyz/ibind/blob/master/ibind/oauth/oauth1a.py) `validate_live_session_token()` then HMAC-verifies the signature; if validation fails it raises `ExternalBrokerError`. **Confirmed in source.**

### What "not yet activated" looks like — and the 19030 question

The brief asks whether `id: 19030, error: invalid consumer` is the canonical "not yet activated" signature. **Verdict: unconfirmed as a *canonical* signature** — here's the evidence.

What IS confirmed:

- The most cited "not yet activated" error in the ibind issue tracker is plain **HTTP 401** with body containing the string `"No access secret key found for ***"`. Quote from [ibind issue #113](https://github.com/Voyz/ibind/issues/113):

  > "ibind.support.errors.ExternalBrokerError: IbkrClient: response error Result(data=None, request={'url': 'https://api.ibkr.com/v1/api/oauth/live_session_token'}) :: 401" — error message: "No access secret key found for ***".

- Same error documented in [issue #58](https://github.com/Voyz/ibind/issues/58) and [issue #98](https://github.com/Voyz/ibind/issues/98) (the latter as 403 on the *configuration* upload, not the LST request).

What is **not** confirmed:

- I could not retrieve a single ibind issue, IBKR Campus page, or TWS error reference that documents a numeric code `19030` with text `"invalid consumer"`. The [TWS API message codes page](https://interactivebrokers.github.io/tws-api/message_codes.html) (which lists codes from 200 up through the 10000-range and 20000-range warnings) does **not** define 19030, 19031, or 19032. **[unconfirmed]** — it may be a code surfaced by the OAuth subsystem specifically (which historically uses different code ranges than the TWS socket API), or it may be a value our poller is reading from a legacy stale path. We should verify by capturing the actual response body from a known-unactivated key and grepping for `"19030"`.

- A general IBKR convention worth noting: error response bodies on `/v1/api/oauth/*` endpoints are typically plain JSON `{"error": "..."}` or `{"message": "..."}`, **not** numeric IDs. Numeric `code`/`id` keys appear mostly in `/iserver/*` order endpoints and in TWS socket frames.

### Recommended success criterion (revised from current poller)

The current poller calls `IbkrClient(..., init_brokerage_session=False)` and treats a non-401 on `/oauth/live_session_token` as "activated." **This is necessary but not sufficient.** Three things can still fail after a successful LST:

1. The SSO session can fail to materialise (`/sso/validate` returns 401).
2. `/iserver/auth/ssodh/init` can return `{"authenticated": false, "message": "consumer key has not yet been authorized for trading"}` — HTTP 200 with an in-band failure, which is invisible to a "did the call succeed" check.
3. `/iserver/auth/ssodh/init` can return `{"competing": true}` — another session is using this consumer key (common at the moment activation flips on, because the IBKR side may have an internal probe session warm).

**Proposed 3-step activation check** (each gated on the previous):

```python
# Step 1: LST issuance
c = IbkrClient(use_oauth=True, init_oauth=True, init_brokerage_session=False, oauth_config=cfg)
# c.oauth_init() runs inside the constructor; raises ExternalBrokerError on 401.
# If we get past this, LST stage is green.

# Step 2: brokerage session
init = c.initialize_brokerage_session(compete=True)   # POST /iserver/auth/ssodh/init
# init.data should be {"authenticated": True, "connected": True, ...}
assert init.data and init.data.get("authenticated") is True

# Step 3: confirmation
status = c.authentication_status()                    # POST /iserver/auth/status
assert status.data.get("authenticated") is True
assert status.data.get("connected") is True
assert status.data.get("competing") is False   # we're the only session
```

Only when all three pass should the poller declare activation complete. This is also the right gate to flip the bot to live trading.

Sources: [`session_mixin.py`](https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_client_mixins/session_mixin.py) (method signatures + docstrings); [IBKR Campus, Launching and Authenticating the Gateway](https://www.interactivebrokers.com/campus/trading-lessons/launching-and-authenticating-the-gateway/) (response shape of `/auth/status`).

---

## 5. `/iserver/auth/ssodh/init` — the brokerage session opener

Endpoint: `POST /iserver/auth/ssodh/init?compete=true&publish=true` (from [`session_mixin.py`](https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_client_mixins/session_mixin.py); `publish` is always `True`).

ibind wrapper: `IbkrClient.initialize_brokerage_session(compete: bool = True) -> Result`.

**Purpose:** elevates the SSO-authenticated session (which can only read public-ish stuff) to a fully-authenticated brokerage session that can call `/iserver/account/*`, `/iserver/marketdata/*`, `/iserver/contract/*`, place orders, read positions. **Confirmed in source.**

**When to call:**
- Once at startup, after `oauth_init()` (ibind does this automatically if `init_brokerage_session=True` — the default).
- Again after any 401 on a `/iserver/*` endpoint (the session timed out — typically ~6 min idle after the last tickle).
- **Not** before each trade; the session stays warm as long as `/tickle` is firing.

**Response shape** (HTTP 200 either way; the meaning is in the JSON):

```jsonc
{
  "authenticated": true,    // false = not yet, retry or recheck OAuth
  "connected": true,        // false = IBKR backend connectivity issue
  "competing": false,       // true = another session owns this consumer key
  "message": "",            // populated on failure
  "fail": "",
  "MAC": "...",
  "serverInfo": { "serverName": "...", "serverVersion": "..." }
}
```

**Failure modes** (community + IBKR Campus):

- `authenticated: false` — most commonly happens immediately post-handshake during initial activation, or when the LST has expired in the background. Retry after re-issuing LST.
- `competing: true` — another process is using the same consumer key. With `compete=true` this *should* steal the session; if it still returns `competing: true`, the other process is mid-handshake itself. Sleep ~5s, retry.
- HTTP 401 — almost always a stale LST. Force `generate_live_session_token()` and retry.

Source: [IBKR Campus, Launching and Authenticating the Gateway](https://www.interactivebrokers.com/campus/trading-lessons/launching-and-authenticating-the-gateway/) — explicit field semantics. **Confirmed.**

---

## 6. `/tickle` heartbeat — how often, what happens if you miss

Endpoint: `POST /tickle`. ibind wrapper: `IbkrClient.tickle()`.

**Default cadence in ibind:** `IBIND_TICKLER_INTERVAL = 60` seconds (from [`ibind/var.py`](https://github.com/Voyz/ibind/blob/master/ibind/var.py) — confirmed). The Tickler thread (started by `IbkrClient.start_tickler()` or auto-started when `maintain_oauth=True`) calls `tickle()` every 60 s.

**IBKR's max idle:** the brokerage session times out after **~5 minutes** of no `/iserver/*` calls and no `/tickle`. So 60 s is well inside the safe window. **Confirmed via [IBKR Campus authentication doc](https://www.interactivebrokers.com/campus/traders-insight/authenticating-with-the-ibkr-client-portal-rest-api/) and the ibind wiki [IbkrClient API reference](https://github.com/Voyz/ibind/wiki/API-Reference-%E2%80%90-IbkrClient).**

**If you miss a tickle:** the session does not "die at 6:00 sharp." Empirically (community pattern, from ibind issues including #25 status-loss reports): the session may time out anywhere between ~5 and ~10 min idle. After timeout, the next `/iserver/*` call returns HTTP 401 *or* a 200 with `authenticated: false`. Recovery is **call `initialize_brokerage_session()` again** — no need to re-do the OAuth handshake (LST is still valid for 24 h).

**Tickler failure handling** ([`ibkr_utils.py`](https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_utils.py), confirmed): the worker thread catches `TimeoutError` (logged as warning — "could indicate the servers are restarting"), generic `Exception` (logged as error), and `KeyboardInterrupt` (gracefully exits). **It does not stop on error** — it keeps trying every 60 s.

**Best practice for our wrapper:** use ibind's Tickler (start with `client.start_tickler(interval=60)`); add an external watchdog that checks `client.authentication_status()` every ~300 s and force-reinitialises the brokerage session if `authenticated: false` is observed. The Tickler does NOT re-init on `authenticated: false`; it just keeps pinging.

---

## 7. Brokerage session lifecycle — what each stage unlocks

Pulling stages + capabilities from [`session_mixin.py`](https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_client_mixins/session_mixin.py) + [Advanced OAuth 1.0a wiki](https://github.com/Voyz/ibind/wiki/Advanced-OAuth-1.0a) + [Authenticating with the IBKR Client Portal REST API (IBKR Campus)](https://www.interactivebrokers.com/campus/traders-insight/authenticating-with-the-ibkr-client-portal-rest-api/):

| Stage | What it gives you | How to (re-)achieve it | How long it lasts |
|---|---|---|---|
| 1. OAuth handshake (signing keys + access token) | The *right* to request an LST. | Done at registration; permanent until access token revoked or keys rotated. | Indefinite. |
| 2. **Live Session Token (LST)** | Ability to sign authenticated calls to `/v1/api/*`. | `generate_live_session_token()` — ibind does this automatically. | **~24 hours**, then must be regenerated. ibind's Tickler does not regenerate LST; that's `handle_auth_status()`'s job and the client constructor's job on next start. |
| 3. **SSO session** | Read access to: account list, `/sso/validate`, profile info. **NOT enough for trading or market data.** | Implicit after LST is issued. | Couples with LST; ~24 h. |
| 4. **Brokerage session** | Full `/iserver/*`: orders, positions, market data subscription, contract lookup, order status. | `initialize_brokerage_session()` — `POST /iserver/auth/ssodh/init`. | **~5 min idle timeout** unless `/tickle` keeps it warm. |

**Trade placement requires stage 4.** Reading positions requires stage 4 (`/portfolio/{accountId}/positions/*` is technically stage-3 but `/iserver/account/{accountId}/orders` is stage-4 — we should treat both as stage-4-dependent for safety). **Confirmed in `session_mixin.py` docstrings.**

**Re-init conditions:**

- LST expired (>~24 h, or signature rejected) → re-run from stage 2. Restart the client.
- Brokerage session timeout (~5–10 min idle) → re-run from stage 4 only. Cheap (~1 request).
- `competing: true` (someone else stole the consumer key) → re-run stage 4 with `compete=True`.
- HTTP 401 on `/iserver/*` → try stage 4 once; if still 401, force a full restart from stage 2.

---

## 8. WebSocket disconnect & reconnection

Source: [`ibind/client/ibkr_ws_client.py`](https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_ws_client.py); [`var.py`](https://github.com/Voyz/ibind/blob/master/ibind/var.py).

| Setting | Default | Purpose |
|---|---|---|
| `IBIND_WS_PING_INTERVAL` | `45` s | Send `ping` to IBKR this often. |
| `IBIND_WS_MAX_PING_INTERVAL` | `300` s | If no heartbeat received in 300 s → hard reset. |
| `IBIND_WS_TIMEOUT` | `5` s | State-change verification timeout. |
| `IBIND_WS_SUBSCRIPTION_RETRIES` | `5` | Subscription creation attempts. |
| `IBIND_WS_SUBSCRIPTION_TIMEOUT` | `2` s | Subscription verification timeout. |
| `recreate_subscriptions_on_reconnect` | `True` | On reconnect, ibind re-issues all known subscriptions. |
| `restart_on_close` | `True` | Reconnect on clean close. |
| `restart_on_critical` | `True` | Reconnect on critical errors. |

**Auto-reconnect: yes.** ibind reconnects automatically when:
- The socket closes (with or without close frame).
- A critical error is raised.
- The last heartbeat exceeds `IBIND_WS_MAX_PING_INTERVAL` (300 s).

**Auto-resubscribe: yes**, gated by `recreate_subscriptions_on_reconnect=True`. ibind tracks active subscriptions internally and replays them after reconnect.

**Known issue, not fixed by ibind:** [issue #25](https://github.com/Voyz/ibind/issues/25) — tick frames for specific contract IDs can silently stop arriving with no close frame, no error, no message; everything at the WS/session layer looks healthy. Per IBKR support, this is expected; the user must implement a per-contract refresh loop. **Confirmed via issue + community summary.** Our wrapper will need a per-contract liveness watchdog (e.g., "if no tick for SPX in >60 s during RTH, unsubscribe + resubscribe").

**Status-stream noise:** issue #25 also reports a recurring loop of `"Status unauthenticated: {'authenticated': False}"` and `"Unknown status response: {'topic': 'sts', 'args': {'competing': False}}"` messages — these are IBKR's `sts` topic frames, sometimes spurious. Our handler should not treat a single `authenticated: False` `sts` as a session-dead signal; cross-check against REST `/iserver/auth/status` before reacting.

---

## 9. IBKR error-code dictionary — codes we will actually see

Pulling from [TWS API message codes](https://interactivebrokers.github.io/tws-api/message_codes.html) (canonical for the codes that overlap with the Client Portal Web API), and from ibind / Web API context for the rest:

| Code | Surface | Meaning | Recoverable? | Typical cause |
|---|---|---|---|---|
| 200 | Order placement | "No security definition has been found for the request" | Y (caller bug) | conid wrong, symbol mismatched, expired option contract |
| 201 | Order placement | "Order rejected" | Y (caller bug, sometimes margin) | broad bucket — read the message; can be margin, contract halt, regulatory |
| 202 | Order placement | "Order cancelled" | Informational | system cancellation |
| 502 | TWS-style (not seen on Web API) | "Couldn't connect to TWS" | n/a | TWS-only, not for us |
| 504 | TWS-style | "Not Connected" | n/a | TWS-only |
| 1100 | Status stream | "Connectivity between IB and TWS has been lost" | Y | upstream issue, will recover |
| 1101 | Status stream | "Connectivity restored — data lost" | Y, must resubscribe | post-recovery, market-data state lost |
| 1102 | Status stream | "Connectivity restored — data maintained" | Y, no action | post-recovery, no resubscribe needed |
| 2103/2104/2105/2106 | Status stream | Market-data / historical-data farm up/down | Informational | normal noise |
| 2110 | Status stream | "Connectivity between TWS and server is broken" | Y | usually IBKR nightly restart |
| 10090 | Snapshot/subscription | "Part of requested market data is not subscribed" | N (fix subscriptions) | missing market-data line of business |
| 10148 | Order | "OrderId that needs to be cancelled cannot be cancelled" | N | already filled |
| 19030 | OAuth (claimed) | "invalid consumer" / not yet activated | **[unconfirmed]** — not in TWS code reference; possibly OAuth-subsystem-internal. Our poller treats this as the activation signal; should verify by capturing live payload. | n/a |
| 19031, 19032 | OAuth (claimed) | n/a | **[unconfirmed]** — not in any reference I could retrieve. |
| HTTP 401 + "No access secret key found" | OAuth | Consumer key registered but access secret not yet bound to it | Wait for IBKR to finish provisioning (sometimes 24 h, sometimes 1 min after token regeneration — see [issue #113](https://github.com/Voyz/ibind/issues/113)) | new key, server hasn't restarted |
| HTTP 403 on /key upload | OAuth setup | "Failed to set Key" | Regenerate token via OAuth portal — see [issue #98](https://github.com/Voyz/ibind/issues/98) | DH param re-upload, token expired |

**Recommendation:** capture the actual response body from our poller in a debug log (we're not doing this currently — verify) so the `19030` / `19031` / `19032` claim can be validated. If they turn out not to exist as numeric codes in IBKR's actual response, our poller is fragile.

---

## 10. Order-placement edge cases (cross-reference: agent 9 covers reply prompts in detail)

Surface: [`order_mixin.py`](https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_client_mixins/order_mixin.py).

- **Reply prompts**: returned as a 200 with `data` being a list of `{"id": "...", "message": ["..."]}`. The `place_order(answers=...)` signature takes a dict of `QuestionType → bool` answers; `handle_questions()` iterates internally.
- **`suppress_messages(message_ids)`**: pre-emptively disables specific warnings server-side. ibind docstring: *"The majority of the message IDs are based on the TWS API Error Codes with an 'o' prepended to the id."* So `o202` etc. — but check the actual ID list before suppressing anything in production.
- **Order placement lock**: ibind enforces `order_submission_lock` because the reply-prompt mechanism is per-session, not per-order. **Only one order can be placed at a time per `IbkrClient`.** If we want concurrency, instantiate multiple `IbkrClient`s (NOT recommended — one consumer key, one session).
- **"Order would exceed maximum allowed position"**: appears as a reply-prompt question (not a hard error); answer `false` to reject, `true` to override. SPX 0dte often triggers this if the account has SPX position limits.
- **Insufficient margin**: typically a reply-prompt (`message_type` ≈ "MARGIN") if it's a soft warning; a hard 200-with-`error` payload if rejected outright. Map to `error_code` from the response body.

**Confirmed in source.**

---

## 11. Recommendations for our retry + circuit-breaker design

ibind gives us:
- One retry layer (3× linear backoff) — **for network errors only**.
- One Tickler keeping the brokerage session warm.
- One WebSocket reconnector with subscription replay.

We have to add:

1. **Outer retry layer for application-status errors.** Treat `ExternalBrokerError.status_code in {429, 500, 502, 503, 504}` as recoverable; treat `{400, 401, 403, 404, 409}` as fatal-for-this-attempt.
2. **Exponential backoff with full jitter** for 429/5xx — `min(60, 0.5 * 2**n) + uniform(0, 0.5 * 2**n)` seconds, max 5 retries. Pattern from [AWS Architecture Blog "Exponential Backoff and Jitter"](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/) — community standard.
3. **Respect `Retry-After`** if IBKR ever sends it (they sometimes do on `/iserver/*` 429s).
4. **Circuit breaker.** Open on either: 5 consecutive `5xx`/timeout errors **OR** ≥50% failure rate over the last 20 requests in a 60-second window. Half-open probe: every 30 seconds, allow one request through. Close on a successful response.
5. **Distinct breaker per endpoint family.** A `/iserver/marketdata/snapshot` outage should not stop `/iserver/account/orders` polling. Group: `oauth` (LST/auth), `session` (init/status/tickle), `marketdata`, `orders`, `portfolio`. Five breakers.
6. **401 handler is special** — it should *not* trip the breaker. Instead it should trigger a forced `initialize_brokerage_session()` retry (with a single-flight guard so concurrent 401s don't all try to reinit). If that re-init also returns 401, *then* the breaker opens.
7. **No retry on order-placement endpoints** without an idempotency check. IBKR's `/iserver/account/{accountId}/orders` accepts a client-side `cOID` (Client Order ID); we should always set it and retry safely. Without `cOID`, never retry — risk of duplicate fills.

The Saxo client's `_record_error` / `_open_circuit` pattern maps directly; the only adjustment for IB is the per-endpoint-family split (Saxo is one monolithic surface; IB Web API is more endpoint-heterogeneous in its failure modes).

---

## 12. Activation-poll script audit — verdict

**Current state:** poller calls `IbkrClient(..., init_brokerage_session=False)` and treats `id: 19030, error: invalid consumer` as "not yet activated."

**Problems:**

1. **`19030` is unconfirmed as an IBKR canonical code.** Not in [TWS message codes](https://interactivebrokers.github.io/tws-api/message_codes.html); not in any ibind issue I retrieved; not in any Web API reference. The actual "not yet activated" response observed in [issue #113](https://github.com/Voyz/ibind/issues/113) is plain HTTP 401 with body `"No access secret key found for <consumer_key>"`. Our poller may be matching a string that IBKR's modern response no longer emits.
   - **Action:** add a `log.info("poll response: status=%s body=%s", resp.status_code, resp.text)` line to the poller and verify the exact failure body. If it really is `{"id": 19030, "error": "invalid consumer"}`, document the source. If it's actually a 401 with the access-secret message, update the matcher.

2. **A successful `/oauth/live_session_token` is necessary but not sufficient.** It only proves OAuth signing + LST issuance work. It does **not** prove:
   - the SSO session was created;
   - the brokerage session can be initialised (`/iserver/auth/ssodh/init` may still return `authenticated: false`);
   - `competing` is clean.

   **Action:** add a 2nd and 3rd check to the poller — after LST succeeds, call `initialize_brokerage_session()` and then `authentication_status()`. Declare activation only when `authenticated: true && connected: true && competing: false`.

3. **A successful LST response, BUT brokerage-session init failure, IS possible.** This happens when:
   - The consumer key is activated for OAuth handshake but not yet for trading entitlements (rare, but documented in [ibind #111](https://github.com/Voyz/ibind/issues/111) — *"OAuth works but not for placing orders"*, where the error was "invalid account id" / "Session is misconfigured").
   - Paper-trading consumer keys sometimes activate for OAuth before the paper account is wired up.

   So the 2-step check is not paranoia; it catches a real failure mode.

**Verdict on current poller:** the success criterion is **incomplete**. Upgrade to the 3-step check. The error-pattern match (`19030`) is unverified and should be replaced (or at minimum supplemented) with an HTTP-401 + body-string check on the LST endpoint.

---

## Sources

- [Voyz/ibind GitHub repo (master branch, v0.1.23)](https://github.com/Voyz/ibind)
- [`ibind/support/errors.py`](https://github.com/Voyz/ibind/blob/master/ibind/support/errors.py)
- [`ibind/base/rest_client.py`](https://github.com/Voyz/ibind/blob/master/ibind/base/rest_client.py)
- [`ibind/client/ibkr_client.py`](https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_client.py)
- [`ibind/client/ibkr_client_mixins/session_mixin.py`](https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_client_mixins/session_mixin.py)
- [`ibind/client/ibkr_client_mixins/order_mixin.py`](https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_client_mixins/order_mixin.py)
- [`ibind/client/ibkr_ws_client.py`](https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_ws_client.py)
- [`ibind/client/ibkr_utils.py`](https://github.com/Voyz/ibind/blob/master/ibind/client/ibkr_utils.py) — Tickler + questions/answers
- [`ibind/oauth/oauth1a.py`](https://github.com/Voyz/ibind/blob/master/ibind/oauth/oauth1a.py)
- [`ibind/var.py`](https://github.com/Voyz/ibind/blob/master/ibind/var.py)
- [ibind wiki — OAuth 1.0a](https://github.com/Voyz/ibind/wiki/OAuth-1.0a)
- [ibind wiki — Advanced OAuth 1.0a](https://github.com/Voyz/ibind/wiki/Advanced-OAuth-1.0a)
- [ibind wiki — IbkrClient](https://github.com/Voyz/ibind/wiki/Ibkr-Client)
- [ibind wiki — API Reference IbkrClient](https://github.com/Voyz/ibind/wiki/API-Reference-%E2%80%90-IbkrClient)
- [ibind issue #25 — Disconnect / Errors from stream](https://github.com/Voyz/ibind/issues/25)
- [ibind issue #58 — OAuth 1.0a 401 :: Unauthorized](https://github.com/Voyz/ibind/issues/58)
- [ibind issue #98 — OAuth setup 403 error](https://github.com/Voyz/ibind/issues/98)
- [ibind issue #111 — OAuth works but not for placing orders](https://github.com/Voyz/ibind/issues/111)
- [ibind issue #113 — Zero wait time if OAuth access tokens are regenerated](https://github.com/Voyz/ibind/issues/113)
- [ibind issue #143 — OAUTH Registration URL issue international IB instances](https://github.com/Voyz/ibind/issues/143)
- [TWS API v9.72+ Message Codes](https://interactivebrokers.github.io/tws-api/message_codes.html)
- [IBKR Campus — Launching and Authenticating the Gateway](https://www.interactivebrokers.com/campus/trading-lessons/launching-and-authenticating-the-gateway/)
- [IBKR Campus — Authenticating with the IBKR Client Portal REST API](https://www.interactivebrokers.com/campus/traders-insight/authenticating-with-the-ibkr-client-portal-rest-api/)
- [IBKR Campus — Web API Reference](https://www.interactivebrokers.com/campus/ibkr-api-page/webapi-ref/)
- [IBKR Campus — OAuth 1.0A Extended](https://www.interactivebrokers.com/campus/ibkr-api-page/oauth-1-0a-extended/) (403 to fetcher; quotes via community mirrors)
- [AWS Architecture Blog — Exponential Backoff and Jitter](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/)
