# F5 — Settlement / FX Flow: IBKR Design

**Status**: ⏳ in progress — design approved; F5.1 (probe script) committed; F5.2–F5.5 pending the probe run
**Date**: 2026-05-21
**Predecessors**: F3 (option chain) ✅, F4 (position flow) ✅

F5 is the last and smallest of the broker-flow rewrites. It covers the
remaining Saxo-coupled calls in HYDRA: actual close-price lookups for
P&L accuracy, the USD→EUR reporting rate, and the end-of-day P&L
verification against the broker's closed-position record.

---

## 1. The F5 call sites (audited 2026-05-21)

| # | Line | Method | Saxo call | Purpose |
|---|---|---|---|---|
| 1 | ~2670 | MKT-018 deferred-fill lookup | `get_closed_position_price(uic, buy_or_sell)` | Tier-2 close-price fallback |
| 2 | ~6224 | `_try_sell_long_leg` external close | `get_closed_position_price(uic, "Sell")` | Sale revenue of an externally-closed long |
| 3 | ~10415 | MKT-033 auto-salvage settlement | `get_closed_position_price(uic, "Sell")` | Same, post-settlement reconciliation |
| 4 | ~9189 | `_log_*` snapshot / sheets | `get_fx_rate(base, account)` | USD→EUR reporting rate |
| 5 | ~10306 | `_verify_settlement_pnl_from_saxo` (Fix #87) | `client._make_request` → `/cs/v1/reports/closedPositions/...` | EOD net-P&L cross-check |

Also adjacent (not strictly F5 — order-fill flow): `check_order_filled_by_activity`
at ~2655. Tracked separately; not rewritten here.

## 2. What IBKR offers — and what it doesn't

- **FX rate** — `IBClient.get_fx_rate(source, target)` already exists
  (Phase A.10, `currency_exchange_rate` endpoint). Site 4 is a clean
  one-line swap.
- **Close price of a settled/closed leg** — IBKR has **no** direct
  Saxo-`/port/v1/closedpositions` equivalent. The closest is the CP
  API trade-execution history (`/iserver/account/trades`, ibind
  `IbkrClient.trades()`) — recent executions carrying conid, side,
  price, time. A "what price did conid X close at" lookup becomes:
  fetch recent executions, filter to that conid + side, take the
  matching execution's price.
- **Per-position realized P&L report** (Fix #87's
  `PnLAccountCurrency` per closed position) — IBKR has **no clean
  equivalent** in the CP Web API. `/iserver/account/trades` gives
  execution prices but not a tidy per-position net-P&L line; true
  per-position realized P&L lives in Portfolio Analyst / Flex queries,
  which are heavier and not real-time.

**Probe needed.** The exact shape of `/iserver/account/trades` (field
names, how far back it reaches, whether 0DTE same-day executions
appear, conid vs contract id) must be verified on the paper account
before the IBClient method is designed — same discipline as the F3
secdef probe. That is F5.1.

## 3. The Fix #87 question

`_verify_settlement_pnl_from_saxo` is an EOD safety cross-check: it
compares the bot's own P&L accounting against Saxo's authoritative
closed-position report and applies a correction on mismatch. IBKR has
no equivalent authoritative same-day report.

Options for the IBKR era (decide after the probe):
- **A.** Rebuild it from `/iserver/account/trades`: sum
  (close − open) execution prices per conid. Possible but the
  reconstruction is fiddly and re-derives what the bot already
  computes — limited extra assurance.
- **B.** Replace it with an account-summary realized-P&L delta check
  (`get_balance()` realized-P&L field, start-of-day vs end-of-day).
  Coarser but genuinely independent of the bot's per-trade math.
- **C.** Retire the verification. F4.6 settlement already books
  expired credits correctly in the conid→quantity model; Fix #87 was
  a Saxo-era belt-and-braces check. The bot's own accounting is the
  record.

Leaning **B** — an independent, broker-sourced sanity bound without
the brittle per-trade reconstruction — but this is a genuine fork to
settle once the probe shows what IBKR exposes.

## 4. Broker-agnostic helpers (HydraStrategy)

```
_read_fx_rate(base, account) -> Optional[float]
    IB:   broker.get_fx_rate(base, account)
    Saxo: client.get_fx_rate(base, account)

_read_closed_position_price(instrument_id, *, buy_or_sell) -> Optional[dict]
    Returns {"closing_price": float} or None.
    IB:   broker.get_closed_position_price(conid, buy_or_sell)  (new — F5.2)
    Saxo: client.get_closed_position_price(uic, buy_or_sell)    (legacy)
```

The 3 close-price sites and the 1 FX site then become broker-agnostic
one-liners, the same pattern as F3/F4.

## 5. F5.1 probe results (run 2026-05-21, paper account)

`scripts/probe_ibkr_trades.py` run outcome:
- ✅ **`/iserver/account/trades` is reachable** — returns a JSON
  `list`, no error.
- ✅ **Session priming is mandatory** — the first run failed with
  `500 "Please query /accounts first"`; calling
  `receive_brokerage_accounts()` (the `/iserver/accounts` endpoint)
  first fixes it. `connect()` only primes `/portfolio/accounts` — a
  different endpoint. `get_closed_position_price` primes explicitly.
- ✅ **`days` param accepted** — `days=1` and `days=7` both succeed.
- ⚠️ **Zero trade records** — the paper account has no execution
  history (HYDRA runs dry-run, places no real orders), so the probe
  could NOT capture a real record's field shape or confirm same-day
  0DTE visibility.

**Consequence**: F5.2's per-record field names (`conid`, `side`,
`price`, `size`, `trade_time`, `trade_time_r`) are taken from IBKR's
documented `/iserver/account/trades` schema, with defensive
multi-variant lookups — the same pattern the codebase already uses for
IBKR field-name uncertainty (`_build_fill_result_dict`,
`_normalize_position_dict`). Flagged for live-verification when HYDRA
places its first live order.

## 6. Commit breakdown for F5

| Commit | Scope |
|---|---|
| F5.1 | Probe `/iserver/account/trades` on paper (read-only diagnostic) — ✅ done |
| F5.2 | `IBClient.get_closed_position_price` built on the probe + doc schema + unit tests — ✅ done |
| F5.3 | `HydraStrategy._read_fx_rate` + `_read_closed_position_price` helpers + tests — ✅ done |
| F5.4 | Rewire the 3 close-price sites + the FX site — ✅ done |
| F5.5 | `_verify_settlement_pnl_from_saxo` — IBKR rework (option A/B/C, decided post-probe) |

## 7. Decision record (resolved)

Approved 2026-05-21:
1. The probe-first approach for IBKR trade-execution history (F5.1).
2. The `_read_fx_rate` / `_read_closed_position_price` helper pair.
3. Fix #87 direction — provisional **B** (account-summary realized-P&L
   delta); confirmed at F5.5.
4. Post-probe: since the paper account had no trade history, F5.2 is
   built against IBKR's documented schema with defensive field lookups
   rather than empirically-verified field names (see §5).
