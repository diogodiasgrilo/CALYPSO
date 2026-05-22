# Deferred Work — HYDRA IB-Only Rewrite

**Purpose**: Explicit log of work items that we chose NOT to ship now,
with the reason and the trigger that should bring them back. World-class
delivery means deferring with intent rather than shipping unvalidated
code.

---

> **Superseded by F5.** `docs/migration/F5_SETTLEMENT_FX_FLOW_DESIGN.md`
> is now the concrete plan for DEF-1 and DEF-2: DEF-1 → F5.2
> (`IBClient.get_closed_position_price`), DEF-2 → F5.5 (Fix #87 rework).
> F5.1 (the trade-execution probe) is the verification step both
> entries' "trigger" sections call for. These two entries are kept for
> history; track active status in the F5 design doc.

## DEF-1: `IBClient.get_closed_position_price(conid, buy_or_sell)`

**Why HYDRA needs it**: Called from 3 sites in current HYDRA strategy
(grep `get_closed_position_price(` — currently ~lines 2670, 6224,
10415; strategy.py churns, so use the grep anchor not the numbers) to
get the actual closing fill price for a specific position that closed
today. Used in P&L attribution + Fix #87 settlement reconciliation.

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

## DEF-3: MKT-033 long-leg salvage gated on the Saxo-only `*_position_id`

> ✅ **RESOLVED — F6.6.** `_try_sell_long_leg` and `_check_long_salvage`
> now gate on the long leg's `*_uic` (broker-agnostic instrument id),
> not `*_position_id`. The downstream `_close_position_with_retry`
> keys on the conid on the IB path (F6.3). MKT-033 now fires on IBKR.

**What**: `_try_sell_long_leg` guards with `not long_pos_id` and
`_check_long_salvage` gates each call on `entry.long_call_position_id
and entry.long_call_uic`. F4.3 correctly swapped the *existence check*
to the broker-agnostic `_position_is_open`, but these `*_position_id`
truthiness guards remain. IBKR has no per-leg position id, so once the
IB entry-placement flow lands (and `*_position_id` is `None` from
creation), MKT-033 long salvage will never fire in live IBKR mode —
forfeiting real directional-day salvage revenue ($5–$65/leg).

**Why deferred, not fixed now**: `_try_sell_long_leg` is `dry_run`-only-
skipped at its first line, so MKT-033 never runs in the current dry-run
build. And the method's *downstream* calls — `_close_position_with_retry`,
`registry.unregister` — are the order-close flow, not yet rewritten for
IBKR. Flipping only the gate to `long_uic` would make MKT-033 *attempt*
to run and then hit un-rewritten close code — a half-fix. The correct
fix lands with the order-close flow.

**Trigger**: the IBKR order-close / entry-placement flow rewrite — at
that point gate MKT-033 on `long_uic` (broker-agnostic) and verify the
whole salvage path end-to-end on IBKR.

## DEF-4: STATE-002 `_check_state_consistency` keyed on registry + `PositionId`

**What**: `_check_state_consistency` computes `expected_positions` from
`sum(len(e.all_position_ids) …)` and compares against the Position
Registry count. `all_position_ids` is built from `*_position_id`; on
IBKR those are `None`, so STATE-002 becomes a no-op (no protection) in
live IBKR mode.

**Why deferred**: The Position Registry is vestigial in a single-bot
world and F4 §7 explicitly scoped it out — its fate (delete vs keep)
is decided in the dead-code phase. STATE-002's rewrite belongs with
that decision.

**Trigger**: the dead-code phase / Position-Registry decision. If the
registry is retired, STATE-002 should be re-expressed in the
conid→quantity model (it can reuse `_expected_position_quantities` —
the F4.4 machinery) or removed.

## DEF-5: `_process_expired_credits` settlement detection on `*_position_id`

> ✅ **RESOLVED — F6.6.** New `_side_positions_gone(entry, side)`
> detects a settled side in the conid→quantity model: dry-run → always
> gone; IBKR → each leg gone when its `*_uic` is cleared OR the broker
> shows nothing open at that conid (`_position_is_open`); Saxo → the
> legacy `_position_is_settled(*_position_id)` pair. `_process_expired_credits`
> now uses it, so on IBKR a still-open leg is no longer marked expired
> the moment POS-004 runs.

**What**: F4.6 rewrote POS-004 to detect settlement via the
conid→quantity model (`*_uic` cleared), but it then calls
`_process_expired_credits()`, which still decides "position gone" via
`_position_is_settled(entry.short_call_position_id)` — the legacy
`*_position_id` field. POS-004's main path papers over this by clearing
both `*_uic` and `*_position_id`; the empty-registry path does not.
Once IB entry placement leaves `*_position_id` `None` from creation,
the empty-registry path could mark sides expired prematurely.

**Why deferred**: cross-slice — depends on the IB entry-placement flow
defining what (if anything) replaces `*_position_id`.

**Trigger**: the IB entry-placement flow rewrite — migrate
`_process_expired_credits` to the conid→quantity model at the same time.

## DEF-6: settlement P&L value-verification (Fix #87) has no IBKR equivalent

**What**: `_verify_settlement_pnl_from_saxo` (Fix #87) cross-checks the
bot's computed daily P&L against Saxo's `/cs/v1/reports/closedPositions`
report (`PnLAccountCurrency` per closed position) and corrects
`total_realized_pnl` on a mismatch — catching the case where a 0DTE
option settles ITM at non-zero. F5.5 made it a logged skip on the IB
path: IBKR's Client Portal Web API has **no real-time per-day
closed-positions P&L report**. Per-position realized P&L lives only in
Portfolio Analyst / Flex queries, which are not real-time.

**What still works on IBKR**: POS-004 (`check_after_hours_settlement`,
F4.6) verifies every tracked leg actually settled (position-level).
What's lost is the settled-P&L-*value* cross-check.

**Why deferred**: the F5 design (§3) leaned option **B** — an
account-summary realized-P&L delta (start-of-day vs end-of-day). That
needs (a) a probe of IBKR's balance/summary fields to find a usable
realized-P&L field, and (b) a start-of-day baseline-capture hook in
the entry path. Disproportionate to build now for a verification-only,
dry-run-skipped nicety — and shipping a guessed verification would not
be world-class.

**Trigger**: before the live IBKR cutover — probe `get_balance()` /
account-summary for a "today realized P&L" field; if one exists,
implement option B (snapshot at first entry, compare at settlement);
if not, accept POS-004's leg-level check as sufficient and retire
Fix #87 on IBKR explicitly.

## DEF-7 — POS-003 mid-session reconciliation not ported to conids

`_reconcile_positions` (base) detects legs closed manually during the
session. Its per-leg loop keys on Saxo `*_position_id`, which is
always None on IBKR — so on the IBKR path the loop is dormant and
mid-session POS-003 reconciliation does nothing. HYDRA's settlement
path (`_side_positions_gone`, conid-based) and the stop monitor still
function; only the *hourly* manual-close detection is inert.

P4.3b wired the method's `get_positions` call to `_read_open_positions`
(so it no longer references `self.client`) but did not redesign the
detection logic — that is a behavioural change, out of P4's
"remove Saxo code" scope.

**Trigger**: if mid-session manual position closes need detecting on
IBKR, rewrite the `_reconcile_positions` per-leg loop to test each
leg's `*_uic` against `_position_is_open` (the same conid predicate
`_side_positions_gone` uses).

## How to maintain this doc

- When deferring something, add an entry here with the trigger.
- When the trigger fires, implement the item, add a commit reference,
  and move the entry to the "Resolved" section below.
- Don't add items that are just "TODO" without a concrete trigger —
  those belong in commit messages or inline comments at the call site.

## Resolved

- **DEF-3** — MKT-033 salvage gate → `*_uic`. Resolved in F6.6
  (commit on the `hydra-ibkr-standalone` branch).
- **DEF-5** — `_process_expired_credits` settlement detection →
  conid-model `_side_positions_gone`. Resolved in F6.6.

DEF-1 / DEF-2 are superseded by the F5 design (F5.2 / F5.5). DEF-4
(STATE-002), DEF-6 (settlement P&L value-verification) and DEF-7
(POS-003 conid reconciliation) remain open — see their entries above.
