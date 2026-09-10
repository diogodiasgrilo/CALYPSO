# Atomic Combo (BAG) Iron-Condor Entry — Live-Cutover Plan

> ## ⚠️ CORRECTIONS 2026-09-10 — read before acting on anything below
>
> A verification pass against IBKR primary sources and the installed `ibind`
> 0.1.23 confirmed this plan's direction but found **three material errors** and
> one **potentially trade-inverting ambiguity**. The body below is otherwise
> unchanged; these override it where they conflict.
>
> **C1 — §2's margin finding is INFERRED, NOT MEASURED.** §2 states a 10c combo
> "passed the submission precheck … confirms combos margin defined-risk". It
> confirms no such thing. `place_vertical_spread` (`ib_client.py:3126`) is
> **submit-only** — it never calls `what_if_order`, so **no margin figure was
> ever read**. The same probe found the order never reached IBKR's
> order-management system (phantom `PendingSubmit`; `OrderID doesn't exist` on
> cancel), so "it wasn't rejected" is consistent with *no margin check having
> run*. No naked control was run at the probe's own far-OTM strikes, and the
> "$1.1M naked" counterfactual is imported from a **different**, near-the-money
> 2026-06-04 single-leg rejection.
> **The position-level defined-risk treatment IS verified** from IBKR's published
> Reg-T table (naked short SPX put ≈ $107,150/contract at SPX 7,630 vs an iron
> condor's `width × 100` = $500/contract — a 214× ratio). The **order-check-level
> netting is theoretically expected but empirically unmeasured.**
> **SETTLE IT FIRST, FREE:** call `what_if_order` with a conidex/BAG
> `OrderRequest` on the LIVE account. Both the method (`ib_client.py:3629`) and
> the field exist today. It places **no order** and returns IBKR's own
> initial-margin block. Do this before sizing to 10c.
>
> **C2 — the `allOrNone` claim in the code is FALSE.** `ib_client.py:3050-3055`
> asserts the ticket "DOES expose `allOrNone` … pass `allOrNone=True` via
> OrderRequest rather than building a partial-fill watcher." Verified against the
> installed package: **zero occurrences** of `allOrNone`/`all_or_none` anywhere in
> ibind 0.1.23, and `OrderRequest` has no such field. The CP API order schema has
> no AON field and TIF offers only GTC/OPG/DAY/IOC — **no FOK, no AON.**
> This matters: a guaranteed >2-leg combo cannot partial by *leg*, but it CAN
> partial by *quantity* (6 of 10 spreads fill, 4 keep working) and there is no way
> to prevent it. `entry.contracts` is set from config (`strategy.py:6856`) and
> **never** from the broker's fill count, so a 6-of-10 fill would be stopped,
> marked and booked as 10. A combo path needs its own fill-count reconcile.
>
> **C3 — the reference branch is far staler than stated.** §3 says
> `hydra-combo-entry` is "64 commits stale". It is **386 commits behind HEAD**. It
> also lacks GUARD-INVERT, drops `what_if_naked_margin` from the broker allowlist
> (which would break variant G's margin gate), predates the strangle/registry
> refactor, and contains a defect where `_unwind_partial_entry` sells the long
> before buying back the short — **re-creating the naked window the combo exists
> to remove.** Re-implement on HEAD; do not merge.
>
> **C4 — UNRESOLVED: a side/price convention that could INVERT the trade.**
> IBKR's own CP Web API worked example submits a **credit spread as
> `side: "BUY"` with a NEGATIVE price**, letting the leg ratio signs define the
> structure. This repo does the opposite — shorts encoded as `-1` **and**
> `side="SELL"` with a **positive** price (`ib_client.py:3113-3117`). Under TWS
> BAG semantics a SELL on the combo **reverses every leg's action**, which would
> turn this short iron condor into a **long** one. Which convention the CP Web API
> applies is **UNVERIFIED**. Resolve with a `what_if`/preview before a single real
> contract.
>
> **Also unverified, and material:** whether a **4-leg** SPX combo is permitted at
> all (IBKR: "the number of legs permissible … varies by exchange"; this plan's
> two-2-leg-vertical design side-steps it), and whether a USD **index-option**
> combo takes the bare `28812380` prefix or needs `@CBOE` (`ib_constants.py:49`
> hardcodes the bare form and the tests only assert self-consistency with it).
>
> **Two pre-existing items to fix before 10c regardless:** `min_buying_power_per_ic:
> 500` is a flat per-contract floor correct only for 5pt wings — at VIX ≥ 22 B uses
> 10pt wings needing $1,000/contract, so ORDER-004 under-provisions 2×. And **keep
> the wings equal**: per IBKR KB-600, unequal put/call strike distances are
> margined as *two separate spreads* (~double), and both MKT-045 chain snapping and
> the Brandon GEX adjuster mutate strikes *after* width selection.
>
> **Capital, corrected:** ~$60k worst case at 10c across all slots vs ~$1,071,500
> today — an 80–95% reduction. But **fund $150k–$250k, not $60k**: IBKR enforces
> margin in real time and auto-liquidates in an order you do not control, which can
> close the long wings first and manufacture the exact naked short the combo
> prevents. **Stay on Reg-T** — Portfolio Margin gives roughly the same IC
> requirement while adding a $110k NLV floor and a $100k restricted-trading cliff.
>
> **Effort:** ~8–12 focused working days. ~60% of the submit primitives already
> exist at HEAD; none of it is reachable from a strategy (no `bots/` caller, and
> `BrokerClient.__getattr__` raises for anything outside `ALLOWED_METHODS`).

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
- **MARGIN: defined-risk.** ⚠️ **SEE CORRECTION C1 — this is INFERRED, NOT MEASURED.** A **10c** combo passed the submission precheck (would be
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
