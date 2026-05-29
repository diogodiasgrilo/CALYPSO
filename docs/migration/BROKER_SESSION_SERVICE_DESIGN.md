# ADR + Design — `calypso-broker`: a single shared IBKR session service

**Status:** ✅ IMPLEMENTED + DEPLOYED 2026-05-29. `calypso-broker` is live on
`calypso-bot`; strategies A, B, C all run via `BrokerClient` against the broker's
single IBKR session — zero contention, NRestarts=0 all round, ~0.9 IBKR req/s
funneled (well under the ~10/s cap). Supersedes the Option-2 (per-username) plan.
**Context doc:** [`IBKR_MULTI_SESSION.md`](./IBKR_MULTI_SESSION.md) (the
one-session-per-username constraint, from 5-agent research).

> **Open follow-up (P5a):** breaker/warmup ALERTING is temporarily silenced.
> `BrokerClient.circuit_breakers` is an empty mapping (the real per-family
> breakers live in the broker process now), so the strategy-side `IBKRAlertHooks`
> breaker/warmup poll is a no-op. **The breakers still fully protect** the
> order/market/session paths inside the broker — only the Telegram *alerts* on
> trips/warmup-exhaustion don't fire. Fix: run `IBKRAlertHooks` (breaker +
> warmup) inside `calypso-broker` (one alerter, where the breakers are) before
> relying on those notifications. `ensure_connected`-failure alerts are
> unaffected (still fire from each strategy via the broker's /health).

---

## 1. Context / problem

IBKR allows **one brokerage session per username**. A/B/C each construct their
own `IBClient(use_oauth=True)` and call `ssodh/init(compete:true)`, so three
processes on one username (`DUR049068` / consumer key `CALYPSOPP`) perpetually
evict each other (`410 Gone` / `competing` / crash-loop). Observed at cutover
2026-05-29. The Saxo era avoided this with a shared `token_keeper`/coordinator,
deleted on this branch.

We want all three strategies to run concurrently **on one account** (shared
quote stream + one position/P&L view for true A-vs-B-vs-C comparison, and a
single funnel for risk/reconciliation when we go live). That requires sharing
one session, not three.

## 2. Decision

Introduce **`calypso-broker`**: a single long-lived process that owns the *only*
`IBClient` (one LST, one `ssodh/init`, one Tickler, one morning re-auth gate)
and exposes the broker surface over a **loopback HTTP API**. Strategies A/B/C
stop owning sessions and talk to the broker via a drop-in `BrokerClient` stub.
This is the production-grade single-account pattern and the clean IBKR-era
successor to `token_keeper`.

## 3. The seam (why this is tractable)

The strategies touch the broker through exactly **16 methods** on `self.broker`,
constructed at 2 sites in `bots/hydra/main.py` and assigned once at
`base_strategy.py:857`. No other `self.client.*` usage remains. So:

- **Strategy logic does not change.** We only swap the object assigned to
  `self.broker`: `IBClient(...)` → `BrokerClient(base_url=...)`.
- `BrokerClient` implements the same 16 methods, each an HTTP call to the broker,
  returning the **same shapes** `IBClient` returns (contract: byte-for-byte
  parity on return types — enforced by reusing IBClient's own response models /
  a shared serialization).

The 16 methods (the API surface):

| Group | Methods |
|---|---|
| Market data | `get_quote`, `get_quotes_batch`, `get_vix_price`, `get_option_chain`, `get_option_greeks`, `get_chart_data` |
| Contracts | `qualify_contract`, `qualify_option_strikes` |
| Account/positions | `get_positions`, `get_balance`, `get_fx_rate`, `get_open_orders`, `get_order_status`, `get_closed_position_price` |
| Orders (writes) | `place_and_wait_for_fill`, `cancel_order` |

## 4. Architecture

```
            ┌─────────────────────────── calypso-bot VM ───────────────────────────┐
            │                                                                       │
  hydra (A) ─┐                                                                      │
  variant_b ─┼──HTTP 127.0.0.1:8788──▶  calypso-broker  ──OAuth1a──▶ api.ibkr.com   │
  variant_c ─┘   (BrokerClient stub)     • ONE IBClient (use_oauth=True)            │
            │                            • LST + ssodh/init(compete:true) ONCE      │
            │                            • Tickler (60s) + morning re-auth gate     │
            │                            • retry + per-family circuit breakers      │
            │                            • (v2) global risk gate + reconciliation   │
            └───────────────────────────────────────────────────────────────────────┘
```

- **Transport:** HTTP/JSON on `127.0.0.1:8788` (FastAPI/uvicorn — same stack as
  the dashboard, so no new dependency). Loopback-only bind (single-tenant VM;
  same posture as the locked-down dashboard). Localhost HTTP adds sub-ms–low-ms
  latency — negligible for HYDRA's 2–15 s decision cadence. (ZeroMQ DEALER/ROUTER
  is a future option if we ever need sub-ms; not needed now.)
- **Session ownership:** ONLY the broker authenticates to IBKR. The morning
  re-auth gate (currently in `main.py`) moves into the broker — one place owns
  session lifecycle, so there is never an eviction war.
- **Credentials:** ONLY `calypso-broker.service` gets
  `LoadCredentialEncrypted=ibkr_*` (`/etc/calypso/ibkr`). `hydra.service` +
  variants **drop** the IBKR creds entirely (they no longer auth to IBKR).
- **Failure isolation:** broker is a single point of failure → `Restart=always`
  + strategies degrade gracefully when it's unavailable (BrokerClient surfaces a
  clean error; strategies already handle "no data" ticks — they must NOT
  crash-loop if the broker is down). `hydra*` units gain `After=calypso-broker`
  (+ `Wants=`, not hard `Requires`, so a broker blip doesn't kill strategies).
- **Concurrency:** broker serializes IBKR access behind its existing
  retry+breaker; per-strategy request tagging (`X-Strategy: A|B|C`) for logging
  + the future risk gate.

## 5. Implementation plan (phased; each phase verifiable, live-A protected)

**P1 — Broker service + client stub (code, testable on laptop).**
- `shared/broker_service.py` (or `services/broker/`): FastAPI app wrapping ONE
  `IBClient`; one endpoint per the 16 methods (`POST /rpc/<method>` with JSON
  args, or REST routes); `/health` reporting session/auth status. Reuse
  IBClient's response models for serialization parity.
- `shared/broker_client.py`: `BrokerClient` implementing the 16 methods via
  `requests` to the broker; identical return shapes; clean timeout/retry +
  "broker down" handling.
- Unit tests: `BrokerClient` ↔ broker round-trip with a mocked IBClient;
  contract test asserting return-shape parity for all 16.

**P2 — Deploy broker to VM (non-disruptive).**
- `deploy/calypso-broker.service` (owns `/etc/calypso/ibkr` creds + sandboxing).
- Start broker; verify it holds ONE session (`/health` → authenticated), and
  the 16 endpoints return live data — WHILE strategy A still runs on its own
  session (two sessions briefly: A + broker — they'd compete, so do P3 in the
  same window / off-hours, OR point A at the broker first).

**P3 — Cut A onto the broker.** Swap `main.py` construction → `BrokerClient`;
A stops owning a session. Now A + broker = one session (the broker's). Verify A
trades through the broker (dry-run heartbeat, quotes, order path).

**P4 — Bring up B and C** via `BrokerClient` → all three share the broker's one
session. Verify all three healthy, `competing:false`, no eviction.

**P5 — (optional v2)** centralize a global risk gate + order/fill reconciliation
in the broker (all order flow now passes through it).

## 6. Risks / mitigations
- **Return-shape drift** between BrokerClient and IBClient → strategy bugs.
  Mitigate: shared serialization + a contract test over all 16 methods.
- **Broker outage halts all 3** → `Restart=always`, graceful degradation, alerts
  via the existing alert hooks (move breaker/warmup alerts into the broker).
- **Cutting A over** is the one delicate step (A is the live strategy). It's
  paper/dry-run, low stakes; do it in a watched window with rollback (revert
  `main.py` to `IBClient`, restart A on its own session).
- **Latency** — measured at P2; expected negligible on loopback.

## 7. Alternatives rejected
- **Option 2 (username per strategy):** works for paper but splits accounts/P&L
  and doesn't give one funnel for live risk — see `IBKR_MULTI_SESSION.md`.
- **Consolidate A/B/C into one process:** simplest session-wise but loses
  crash-isolation and merges 3 services/configs into 1.
- **More consumer keys / IB Gateway clientIds:** ruled out in
  `IBKR_MULTI_SESSION.md`.
