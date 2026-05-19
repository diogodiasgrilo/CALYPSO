# Deferred Work — HYDRA IB-Only Rewrite

**Purpose**: Explicit log of work items that we chose NOT to ship now,
with the reason and the trigger that should bring them back. World-class
delivery means deferring with intent rather than shipping unvalidated
code.

---

## DEF-1: `IBClient.get_closed_position_price(conid, buy_or_sell)`

**Why HYDRA needs it**: Called from 3 sites in current HYDRA strategy
(`bots/hydra/strategy.py` lines 2099, 5557, 9615) to get the actual
closing fill price for a specific position that closed today. Used in
P&L attribution + Fix #87 settlement reconciliation paths.

**Why deferred**: IBKR Client Portal Web API's per-conid closed
positions endpoint is not clearly documented in publicly accessible
sources, and ibind 0.1.23 doesn't wrap it. Shipping a best-guess
endpoint path now would be untested speculation. Unit tests against
mocks would verify our mocking, not actual IBKR behavior.

**Trigger to implement**:
1. Phase NEW-2 final stage runs HYDRA dry-run against IBKR paper for 1
   trading day
2. During that run, query IBKR's actual closed-positions endpoints
   directly (via `IbkrClient.get()` against candidate paths) to
   discover the working endpoint + response shape
3. Implement `get_closed_position_price(conid, buy_or_sell)` with the
   verified path + add unit tests against the actual response shape

**Stub for the rewrite phase**: When we port the 3 HYDRA call sites,
they become `try/except` blocks that call `self.client.get_closed_position_price(...)`,
expecting an `AttributeError` / `NotImplementedError` and logging a
warning. HYDRA's settlement code is already defensive (Fix #87 has a
generic `except Exception` that logs + continues). For 0DTE settlement,
the fallback is to assume credit-kept (the dominant case anyway —
expired worthless).

**Estimated effort to implement once unblocked**: 0.5 day (~100 LOC +
unit tests, once we have the verified endpoint + response shape).

---

## DEF-2: `IBClient.get_closed_positions_report(from_date, to_date)`

**Why HYDRA needs it**: Fix #87 (settlement P&L verification) sums
`PnLAccountCurrency` across all closed positions for the day to
cross-check the bot's calculated P&L. Currently fetches via Saxo's
`/cs/v1/reports/closedPositions/{client_key}/{from}/{to}` endpoint.

**Why deferred**: Same reason as DEF-1. IBKR's bulk closed-positions
endpoint isn't well documented; ibind doesn't wrap it. Plus Fix #87
already has a graceful-degradation path (`except Exception as e:
logger.warning(...)`), so the strategy survives without this.

**Trigger to implement**:
1. Same as DEF-1 — verify endpoint during live paper testing
2. Decide whether to implement at all: the verification is nice-to-have
   for live trading where P&L attribution matters; HYDRA stays in
   dry-run for now, so this is genuinely lower priority

**Stub for the rewrite phase**: Fix #87 path becomes a no-op + comment
pointing to this doc. Same as Saxo dry-run skip already does at line
9500-9501.

**Estimated effort to implement once unblocked**: 0.5 day (~150 LOC +
unit tests + integration test against paper account).

---

## How to maintain this doc

- When deferring something, add an entry here with the trigger.
- When the trigger fires, implement the item, add a commit reference,
  and move the entry to the "Resolved" section below.
- Don't add items that are just "TODO" without a concrete trigger —
  those belong in commit messages or inline comments at the call site.

## Resolved

(none yet — populated as we work through DEF-N items)
