# Atomic Combo (BAG) Iron-Condor Entry — Live-Cutover Plan

> **Status (2026-06-10):** DEFERRED to the live (real-money, COB-routed) account.
> The interim fix shipped on this paper branch is the **SELL-leg net-credit
> prevention floor** (`base_strategy._sell_credit_floor_price` +
> `_place_option_order_ib`) layered under **GUARD-INVERT**
> (`base_strategy._validate_realized_credit`). This doc is the implementation-ready
> plan for the structural fix when there is a live account to validate it on.

---

## 1. Why combos are the structural fix

HYDRA places each iron-condor as **four independent single-leg orders** (longs first
per ORDER-002, then shorts) up a progressive-slippage rung ladder over ~3 minutes.
That non-atomic placement is the root cause of a family of problems:

| Problem | Cause | Combo fixes it because… |
|---|---|---|
| **Leg inversion** (Entry #2, 2026-06-10: short put sold @8.80 *after* long put bought @9.60 → −$560 net debit) | shorts priced in isolation, market moves between legs | one net-credit limit; fills atomically at-or-better, or not at all |
| **Naked-short margin reject** (06-04) + the **7c cap** | a standalone short leg pre-checks as a NAKED short (~$110k/contract on SPX); 10c naked ≈ $1.1M > the paper account's ~$996k BP → rejected → contracts cut to 7 | IBKR nets the spread at order-check → **defined-risk margin** (width×$100×contracts ≈ $5k at 10c) → **restores 10c** |
| **3-minute legging window** | sequential rungs | single order |

The combo doesn't change the strategy — it makes execution faithful to what the
strategy already is (a net-credit instrument). Use **per-vertical** combos (call
spread + put spread), NOT a single 4-leg combo, so one-sided entries (MKT-011
conversion, Brandon GEX-skip, E6) still work.

## 2. Empirical findings on the PAPER account (2026-06-10 probe)

Probed via `place_vertical_spread` through `calypso-broker` (far-OTM, non-marketable, cancelled):

- **ACCEPTANCE: YES.** Paper accepts a BAG ticket (`order_id`, `secType:BAG`,
  `PendingSubmit`) at both 1c and 10c. The earlier "Riskless combination orders are
  not allowed" rejection was a **mispriced probe** ($2.00 credit on a ~$0.00 spread),
  not a combo ban — at a fair price it sails through.
- **MARGIN: defined-risk.** A **10c** combo passed the submission precheck (would be
  rejected as naked at $1.1M > $996k BP) → confirms combos margin defined-risk →
  **combos would restore 10c.**
- **ORDER LIFECYCLE: BROKEN on paper.** The accepted combos stick in phantom
  `PendingSubmit`, never reach the book, and IBKR returns
  **`400 Bad Request: "OrderID … doesn't exist"`** on cancel. Per IBKR's own paper
  docs: *"Limited combo trading… fills are simulated from top of book… complex order
  types are always simulated."* **You cannot operate or validate combo fills on the
  paper account.** They also cannot fill (IBKR has no real order), so they're benign.

**Conclusion:** combos are the correct LIVE fix and their margin/acceptance benefits
are real, but the **atomic-fill guarantee only exists on a live, CBOE-COB-routed
account** — it is NOT validatable on this paper-only branch. Hence: floor now, combo
at the live cutover.

## 3. What already exists vs. what's missing

- **On `hydra-ibkr-standalone` (HEAD):** the combo *submit* primitives —
  `ib_client.build_vertical_conidex`, `build_ic_conidex`, `place_vertical_spread`
  (3045, SUBMIT-only), `place_iron_condor`, `_round_to_increment`;
  `ib_constants.SPREAD_TEMPLATE_CONID` (28812380).
- **Missing:** a combo **place-AND-poll-to-fill** primitive (`place_and_wait_for_fill`
  is single-conid only).
- **Reference impl (DO NOT MERGE):** branch `hydra-combo-entry` (commit `5517ea7`)
  has a near-complete `place_vertical_spread_and_wait` + `_build_combo_fill_result` +
  an `_execute_entry` rewrite + a 917-line test file. **It is 64 commits stale, LACKS
  GUARD-INVERT, deletes the strangle/registry/leg refactor now on HEAD, and REPLACES
  `what_if_naked_margin` in the broker allowlist.** Re-implement its combo hunks on
  HEAD; do not cherry-pick/merge.

## 4. Implementation plan (live cutover)

1. **Feature branch** off `hydra-ibkr-standalone`.
2. **`shared/ib_client.py`:** port `place_vertical_spread_and_wait` (model on
   `place_and_wait_for_fill`: same `_TERMINAL_ORDER_STATUSES`, `order_status` vs
   `status` precedence per P7-audit C3, `CircuitBreakerOpen`→`timed_out`, cancel the
   working combo on timeout) + `_build_combo_fill_result` (read per-leg PER-SHARE
   `avg_price`, NOT `avg_cost` which is ×100). **Fix the FALSE `allOrNone` docstring**
   on `place_iron_condor` — ibind 0.1.23 has no such field; the BAG complex-book
   atomic fill is the real guarantee, not a ticket flag.
3. **`bots/hydra/base_strategy.py`:** rewrite `_execute_entry` to place ONE combo per
   ACTIVE side at a net-credit limit (`min(mid−$0.10, natural net_bid)` floored at
   $0.05), **fail-closed** if the net bid collapsed; after a `BrokerError`/timeout,
   reconcile via `get_positions` and adopt live legs ONLY if `short qty<0`, `long qty>0`,
   `|qty|==contracts`, else fail closed. Keep GUARD-INVERT enabled. Entry is
   **fill-or-skip** — no MARKET fallback on entry (a sub-floor combo simply doesn't
   trade, which is the faithful MKT-011 behavior).
4. **`bots/hydra/strategy.py` + `brandon/strategy.py`:** thread the MKT-011 estimate
   into the new `_execute_entry`; forward the signature in the Brandon override
   (**REGRESSION-CRITICAL** — a 2-arg/old-form mismatch TypeErrors on EVERY variant-C
   entry = outage). `strangle_strategy.py` keeps its own naked-short path.
5. **`shared/broker_service.py`:** ADD `place_vertical_spread_and_wait` (+ `place_vertical_spread`)
   to `ALLOWED_METHODS` **alongside** `what_if_naked_margin` (do not replace it).
6. **`shared/broker_client.py`:** extend the per-call HTTP-timeout special-case (keyed
   on `place_and_wait_for_fill` today) to include the new method; 50s default.
7. **Tests:** port `test_combo_entry.py` (unit, mocked); full `pytest tests/ -q` green;
   re-read `P7_AUDIT_FINDINGS.md`.

## 5. Deploy discipline (non-negotiable)

- `git pull` on VM + clear `__pycache__`.
- **Restart `calypso-broker` FIRST** (it owns the one IBClient + holds loaded
  bytecode; a `git pull` does not reload it). **Prove the new `/rpc` method resolves**
  before restarting strategies — else an un-restarted broker returns MethodNotAllowed →
  `BrokerError` → every combo entry fails → bot blind (the 2026-06-08 modularity trap).
- Then restart the `hydra*` units; verify VM config non-stale (`underlying_symbol: SPX` etc.).
- **GATE: a real FILLED-combo paper smoke is NOT sufficient** (paper simulates). The
  authoritative atomic-fill validation requires a **live, COB-routed account**. Do a
  1c live combo characterization (atomic fill, ~1× net credit, sane stop, forced-timeout
  reconcile-adopt path) before flipping a variant to combos at size.
- **GUARD-INVERT stays enabled throughout** as the permanent backstop (only thing that
  covers the un-floorable MARKET rung + the naked-strangle path).

## 6. Layered defense (end state)

1. **Atomic combo** (this plan) — primary entry path on live; inversion impossible by construction; restores 10c.
2. **SELL-leg net-credit floor** (shipped 2026-06-10) — prevention on any legged path (strangle, fallbacks); redundant for the combo IC path but retained.
3. **GUARD-INVERT** (shipped 2026-06-10) — post-fill detect+unwind backstop; permanent.
