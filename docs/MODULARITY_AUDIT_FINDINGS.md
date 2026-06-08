# Modularity Refactor + Strangle — Deep Audit Findings (2026-06-08)

> Produced by a 25-agent adversarial audit (12 dimensions → per-finding verification → synthesis) after the modularity work (items 1/2/4 + strangle) was merged and deployed to the live-paper VM. Companion to [MODULARITY_AUDIT.md](MODULARITY_AUDIT.md) and [MODULARITY_REMAINING_PLAN.md](MODULARITY_REMAINING_PLAN.md).

## Verdict

- **The IC refactor is behavior-preserving** — re-derived byte-identical for the iron condor (Leg property bridge, `_snap_to_grid`, instrument threading, market-data kernels, naked-short gate, abc conversion). The deployed code does **not** place wrong orders on the IC/Brandon path; it fails *safe* (skips entries) on any fault.
- **A/B/C are GO** on the new code. Pre-open checks passed 2026-06-08: broker resolves `qualify_contract(exchange=…)` (SPX→416904, VIX→13455763); A/B/C configs all `underlying_symbol: "SPX"`; A→`hydra` (descriptive name safely ignored), B/C→`brandon` (`enabled: true`); no config selects `strangle`.

## Three deploy bugs (found on deploy, FIXED) — all one root class

Unit tests (1245 green) structurally couldn't catch these — they're integration/config, not logic:

1. **Broker code-version coupling** — `IBClient.qualify_contract`/`get_option_chain`/`qualify_option_strikes` gained an `exchange` kwarg; `calypso-broker` (which owns the real `IBClient`) ran old bytecode → `TypeError` over `/rpc`. **Fixed:** restarted `calypso-broker`.
2. **Registry name-compat** — registry treated `strategy.name` as the selector key, but A's config carries a descriptive label (`"HYDRA (Trend Following Hybrid)"`) → crash-loop. **Fixed:** hotfix PR #5 (only honor *registered* names; descriptive → default).
3. **Dead-knob-now-live** — instrument parameterization made `underlying_symbol` live; A had a stale Saxo `"US500.I"` → "No symbol found" → blind. **Fixed:** corrected A's VM config to `"SPX"` (backed up).

## Fixed in PR (this `audit-fixes-safety` branch)

| ID | Issue | Fix |
|---|---|---|
| **M2** | Deploy runbook never restarts `calypso-broker` on `shared/` changes → bug #1 recurs | Added mandatory broker-restart + signature-proof step + newly-live-config-knob check to CLAUDE.md "Push local → VM" |
| **M4** | `config.json.template` comment said `underlying_symbol` "read only for the startup log line" — false, re-arms bug #3 | Rewrote: it's the LIVE lookup key; a stale value blinds the bot; documented the other now-live knobs |
| **S-HIGH-3** | Registry arms the strangle with no in-code dry-run guard — a VM `strategy.name="strangle"` on a `dry_run=false` variant would silently arm un-stopped naked-short trading | `StrangleStrategy.__init__` raises `ConfigError` unless `dry_run=True` (until S-CRIT-1 + S2 land) |
| **S-HIGH-1** | Strangle never stamps `entry.contracts` → desyncs credits/commission/stops/reconciliation at >1 contract | `entry.contracts = self.contracts_per_entry` in `_initiate_entry` |
| (doc) | `strangle_strategy.py` docstring claimed "NOT registered … inert" — false (it IS registered) | Corrected to "registered, dry-run-gated, not hardened for live" |

## Deferred — gate before the strangle is EVER run live (it's dry-run-locked until then)

These bite only when the strangle runs; it is now `ConfigError`-gated to dry-run, so they are dormant. Required before any strangle go-live:

- **S-CRIT-1 — the strangle stop is structurally DEAD.** A strangle sets neither `call_only` nor `put_only` → `_check_stop_losses` routes to the full-IC branch → `_validate_pnl_sanity` (`strategy.py:7118-7119`) → the base DATA-004 partial-zero guard (`base_strategy.py:6090,6106`) returns `False` every tick because `long_*_price` is permanently `0.0` → `_check_stop_losses` `continue`s (`strategy.py:7528-7531`). **Two naked undefined-risk shorts with no working stop.** *Fix:* override `_validate_pnl_sanity` in `StrangleStrategy` to validate only the two short legs (treat `long_*_strike==0` as "no wing", not a data error); add a stop-fire test.
- **S-HIGH-2 — dry-run settlement books full credit as profit for an ITM naked short** (`strategy.py:10816-10820,10766-10767`), poisoning the very dry-run dataset the strangle exists to produce. *Fix:* for `requires_protective_wings=False`, compute intrinsic from last-known SPX even in dry-run (credit − intrinsic).
- **S2 — broker-authoritative margin** not wired; `_check_buying_power` uses the defined-risk IC floor (~$5000/contract), which is wrong for undefined risk. *Fix:* wire `what_if_order` (and add it to `broker_service.ALLOWED_METHODS` — RPC-serialize the order) into `_check_buying_power` for the strangle.
- **Strangle mediums:** `spread_width=0` → $0 capital/max-loss telemetry for an unbounded-risk position; strategy-level circuit breaker blind to strangle entry failures; entries mislabeled "Iron Condor"/"full_ic" with phantom 0.0 longs in DB/Sheets/dashboard; `google_sheets.strategy_type` silently defaults to `delta_neutral` schema if unset; no min-premium gate; `target_dte>0` would break the 0DTE-assuming settlement.

## Deferred — durability + coverage (close the bug-classes for good)

- **M3-durable** — `_assert_instrument_parameterized` (`base_strategy.py:5956-5959`) is truthiness-only; it does NOT catch a stale-but-non-empty symbol (the exact bug #3). *Fix:* resolve `underlying_symbol`/`volatility_symbol` via the broker post-connect and raise `ConfigError` on failure; at minimum escalate a `0.0`/`None` startup SPX price to CRITICAL and refuse to enter the loop.
- **Coverage gaps** (why the 3 deploy bugs slipped 1245 tests):
  1. broker-RPC contract test mocks `IBClient` with a bare `MagicMock` → can't catch a new-kwarg `TypeError`. *Fix:* `MagicMock(spec=IBClient)`/autospec + a static `inspect.signature` test asserting every `self.broker.*` call's kwargs are accepted (CI-catches drift independent of any restart).
  2. No test loads a committed config. *Fix:* load every config + assert `resolve_strategy_name` + instrument-param load; a pre-deploy config-lint runnable against the gitignored VM `config.json`.
  3. instrument-param tests assert empty-string rejection only. *Fix:* drive a stale/non-pinned symbol through a fake `IBClient` returning `[]`; assert the bot refuses to enter.
  4. no strangle driven through `_check_stop_losses` / `build_strategy`+real `__init__` / non-None settlement. *Fix:* add those (would have exposed S-CRIT-1/S-HIGH-1/2).

## Long-term
Broker advertises a per-method signature/version hash that `BrokerClient` validates on connect — turns the deploy-coordination class into a startup error instead of a runtime blind-spot.
