# P7 — Multi-Agent Audit Findings Register

**Status**: 🔴 Round 1 complete — fixes in progress
**Date**: 2026-05-22
**Audit**: 6 domain-focused audit agents (opus) over the entire
`hydra-ibkr-standalone` branch (90 files, +9,690/−46,075, 78 commits),
cross-confirmed. This register tracks every finding to closure.

The headline: the migration's **read / reconciliation** layer is sound,
but several **order-action paths still gate on `*_position_id`**, which
is always `None` on IBKR — so on the live IBKR path they silently no-op.
Dry-run never executes these paths (`_simulate_entry`), which is why the
848-test suite stayed green and the bugs were invisible until this audit.

Severity: **Critical** = loses money / unmonitored position / wrong P&L
in live mode. **High** = breaks the bot or a safety gate. **Medium/Low**
= correctness/robustness/observability. **Nit** = cosmetic.

---

## CRITICAL

| ID | File:line | Issue | Status |
|----|-----------|-------|--------|
| C1 | `base_strategy.py:_execute_stop_loss` ~3010 + `strategy.py` override ~6009 | Close loop gated `if pos_id:`; `pos_id` is always None on IBKR → stop NEVER places the closing order (breached short rides naked to expiry) AND books the stop as a profit (P&L inverted: `actual_close_cost=0` → `net_loss` negative → credit added). | OPEN |
| C2 | `ib_client.py:_snapshot_with_preflight` ~1284 | Preflight calls `portfolio_accounts()` (`/portfolio/accounts`) — IBKR's snapshot endpoint requires `/iserver/accounts` (`receive_brokerage_accounts()`). Every quote/greek/VIX read returns metadata-only. **This is the live probe failure.** `get_closed_position_price` already does it correctly — copy that. | OPEN |
| C3 | `ib_client.py:place_and_wait_for_fill` ~2236 | Instant-fill short-circuit reads `place_resp.get("status")`; IBKR place responses use `order_status`. An instantly-filled MKT order is missed → wasteful poll, possible mis-handling. | OPEN |
| C4 | `ib_client.py:place_and_wait_for_fill` ~2237 / `_build_fill_result_dict` | When the place response is terminal, `raw=place_resp` is passed to `_build_fill_result_dict` but the place response has no `filledQuantity`/`avgPrice` → a filled order is reported `filled_quantity=0` → strategy thinks the entry failed → retries → double position. Refetch order status before building the result. | OPEN |

## HIGH

| ID | File:line | Issue | Status |
|----|-----------|-------|--------|
| H1 | `base_strategy.py:_unwind_partial_entry` ~2896 | Unwind loop gated `if pos_id and uic:` — `pos_id` always None → partial-entry rollback is a no-op on IBKR → untracked partial position left open. | OPEN |
| H2 | `strategy.py:check_after_hours_settlement` ~10446 | Settlement gates on `registry.get_positions()` which is always empty on IBKR (registry never populated — `_register_position` early-returns on None pos_id). Always takes the empty-registry branch and unconditionally sets `_settlement_reconciliation_complete=True` — can finalize while a leg is still open → dropped expired credit, broken P&L identity. The conid→quantity `still_open_conids` retry logic is dead code. | OPEN |
| H3 | `base_strategy.py:_validate_config` ~5288 | Still requires a `saxo_api` config section + Saxo UIC keys; fatal `raise` on `__init__`. Passes only because configs still carry a vestigial `saxo_api` block — fails the bot to start once that's removed. | OPEN |
| H4 | `main.py:219,747` | `if not broker.connect():` is dead code — `connect()` never returns False, it raises `IBAuthError`/`IBConnectionError`. A failed connect escapes the try/except, crashes without `trade_logger.shutdown()`. | OPEN |
| H5 | `deploy/hydra.service:40-41` | `StartLimitInterval`/`StartLimitBurst` are in `[Service]`; they are `[Unit]`-section directives → silently ignored → restart rate-limiter inert → with H4, a credential failure = unbounded 30s crash-loop. | OPEN |
| H6 | `main.py:443` | Morning re-auth gate runs once/calendar-day; never re-arms. A mid-session 401/410 (dead brokerage session intraday) is never proactively recovered. | OPEN |
| H7 | `base_strategy.py:_estimate_entry_credit_ib` ~1948 | `_quote_mid` returns 0.0 for a missing leg quote → credit computed from a partially-quoted spread = full short premium, overstated → MKT-011 gate can admit a sub-viable trade. | OPEN |
| H8 | `ib_client.py:get_balance` ~1534/1546 | Raises `IBClientError` on a zero/NaN exchange rate → a transient missing ledger field blocks ALL trading. Also the FX composition direction is admitted-unverified (could be inverted → tradable off by rate²). | OPEN |
| H9 | `base_strategy.py:_get_current_position_size` ~2564 | Counts open legs via `if entry.short_call_position_id:` → always 0 on IBKR → ORDER-006 `max_contracts_per_underlying` cap silently disabled. | OPEN |
| H10 | `ib_client.py:_snapshot_with_preflight` ~1309 | Warmup budget 8×0.25s=2s too short for cold conids/greeks; batch (1 preflight + 8 polls) can exceed IBKR's 10 req/s → 429 + 10-min penalty box. | OPEN |
| H11 | `ib_client.py:modify_order` ~2354 | Hardcodes 0.05 tick (vs `place_order`'s `price_increment`); combo modify omits `conidex`/`sec_type=BAG` → effectively single-leg-only despite docstring claiming combo support. | OPEN |
| H12 | `tests/` | The `_ib_call` retry + circuit-breaker layer and `connect()`/`disconnect()`/`_discover_account_id` mechanics are nearly untested; `_unwrap` error-raise path never exercised. The resilience net is unverified. | OPEN |
| H13 | `docs/migration/STATE_SCHEMA_DESIGN.md`, `HYDRA_STANDALONE_REWRITE_PLAN.md`, `F4_POSITION_FLOW_DESIGN.md` | STATE_SCHEMA_DESIGN documents a `*_uic`→`*_instrument_id` / `position_id`→`order_id` rename that never happened (code keeps `_uic`/`_position_id`). REWRITE_PLAN status says "F5 in progress" when F1-F7+P1-P7 are done. F4 doc repeats the false rename claim. Materially misleads. | OPEN |

## MEDIUM

| ID | File:line | Issue | Status |
|----|-----------|-------|--------|
| M1 | `main.py:655` | `consecutive_errors >= 5` logs CRITICAL but never `break`s → bot spins forever on a persistent fault instead of letting systemd restart. | OPEN |
| M2 | `ib_oauth.py:_read_credential_file` | Swallows all `OSError` → a permission/IO error is indistinguishable from "credential absent" → misleading downstream error. | OPEN |
| M3 | `ib_oauth.py:234` | `if creds_dir:` — empty-string `CREDENTIALS_DIRECTORY` silently falls through to the dev path. Use `is not None`. | OPEN |
| M4 | `IBKR_CREDENTIALS_SETUP.md` | No `systemd-creds decrypt` round-trip verification step; no `systemd-analyze verify`. | OPEN |
| M5 | `deploy/hydra.service` | No sandboxing (`NoNewPrivileges`, `ProtectSystem`, `PrivateTmp`, `ReadWritePaths`) on a service holding decrypted private keys. | OPEN |
| M6 | `base_strategy.py:_verify_entry_fill_prices` ~2724, `_get_total_saxo_pnl` ~4166, `_handle_naked_short` ~2847, `_unwind_partial_entry` ~2905 | The 4 P4 vacuous `if self.broker is not None:` guards leave variables (`legs`, `result`) scoped inside the guard but referenced outside, and `_get_total_saxo_pnl` can return None from a `->float`. Latent NameError/None (dormant — broker always set). Flatten the guards. | OPEN |
| M7 | `strategy.py:9899` | FIX #82 / STATE-004 overnight-0DTE check gated on the always-empty registry → dead on IBKR. | OPEN |
| M8 | `strategy.py:_reconcile_positions` override ~10333 | MKT-033 AUTO external-long-salvage path still `pos_id`-gated → `pre_longs` always empty → external long-sale revenue never captured. | OPEN |
| M9 | `strategy.py:6032` | `short_call_uic`/`short_put_uic` cleared to `0` instead of `None` (Fix #86 path) — inconsistent with every other clear site; latent trap. | OPEN |
| M10 | `strategy.py:_read_index_price` ~1881 | `q.get("mid") or q.get("last") or q.get("mark")` — `or`-chain drops a legitimate falsy value; use the explicit `is not None` ladder like `get_vix_price`. | OPEN |
| M11 | `ib_client.py:what_if_order` ~2408 | Omits the required snapshot preflight (ibind doc: snapshot the conid before whatif) → unreliable margin/commission blocks → weak BP gate. | OPEN |
| M12 | `ib_client.py:place_and_wait_for_fill` poll loop | `get_order_status` can raise `CircuitBreakerOpen` (not `IBClientError`) → escapes uncaught, contradicting the documented `Raises:`. | OPEN |
| M13 | `ib_client.py:_discover_account_id` ~692 | `data[0]["accountId"]` unguarded `KeyError` instead of `IBAuthError`. | OPEN |
| M14 | `ib_client.py:get_fx_rate` ~1599 | Guesses the response key `f"{source}_{target}"`; returns None silently on parse miss. | OPEN |
| M15 | `ib_client.py:_submit_order` ~2461 | For a combo/list place response, blindly promotes `data[0]` — may grab a reply object, not the order. Needs live combo verification. | OPEN |
| M16 | `ib_client.py:2500` | `IbkrWsClient(start=True)` then `StreamingManager.start()` — possible double-start/race. | OPEN |
| M17 | `ib_client.py:_snapshot_with_preflight` ~1318 | Returns `data or []` — metadata-only is truthy → caller can't distinguish "no entitlement" from "preflight bug" from "no quote". Stamp a `populated: False` flag. | OPEN |

## LOW / NIT

| ID | File:line | Issue | Status |
|----|-----------|-------|--------|
| L1 | `base_strategy.py:_place_option_order_ib` ~2306 | `quote.get("bid") or 0` masks a legitimate 0.0 bid. | OPEN |
| L2 | `strategy.py:_read_recent_bars` ~1495 | 8h lookback cap clamps silently — log a warning when it clamps. | OPEN |
| L3 | `strategy.py:_read_option_quotes_batch` ~1614 | Silently drops rows with no `conid` — log when `len(out) < len(chunk)`. | OPEN |
| L4 | `base_strategy.py:3620` | Dead Saxo-shaped `_extract_price` / `_check_quote_freshness` (zero callers) — P2 missed them; delete. | OPEN |
| L5 | `base_strategy.py:905` | `__init__` still reads dead Saxo UIC config keys (`underlying_uic` etc.). | OPEN |
| L6 | `ib_client.py:1018` | `qualify_contract` underlying-conid `or` precedence is fragile; `conidEx` is a compound id — `int()` on it would raise. | OPEN |
| L7 | `ib_client.py:525` | LST-failure classification substring-matches `"401"` in arbitrary error text. | OPEN |
| L8 | `ib_client.py:1773` | `get_option_chain` `calls + puts` assumes both are lists. | OPEN |
| L9 | `ib_client.py:1440` | `mid` computed even on a crossed market (`bid > ask`). | OPEN |
| L10 | `main.py:868` | `--live` help text + epilog still advertise live trading that doesn't exist. | OPEN |
| L11 | docs | `strategy.py`/`base_strategy.py` docstrings reference non-existent `docs/MEIC_STRATEGY_SPECIFICATION.md`. | OPEN |
| L12 | `bots/hydra/__init__.py` | Not updated for the migration — module docstring still MEIC-only, version history has no IBKR-migration entry; `main.py` banner version `1.24.0` stale. | OPEN |
| L13 | `base_strategy.py:158,3627,1978` + `strategy.py:10581` + `base_strategy.py:3868` (operator-visible log) | Stale "Saxo" comments/docstrings/log strings describing current behaviour. | OPEN |
| L14 | tests | `get_vix_price` tests use exact float `==` instead of `pytest.approx`. | OPEN |
| L15 | tests | `MEICStrategy.__new__` + bare `MagicMock` for `daily_state` — unknown-attribute access fabricated; use `spec=`. | OPEN |

## Root-cause clusters (fix these patterns, not just instances)

1. **`*_position_id`-gated action paths** — C1, H1, H2, H9, M7, M8. IBKR has no per-leg id. Every *action* path must key on `*_uic` (conid), exactly as F4 did for the read paths.
2. **`ib_client.py` order/market-data API field mismatches** — C2, C3, C4, H10, M11 — verify against IBKR's real Web API response shapes.
3. **Vacuous P4 guards** — M6 — flatten.
4. **Docs describing un-built designs** — H13, L11, L12, L13.

## Resolution protocol

Round 1 (this register) → fix Critical, then High, then Medium, then
Low, each with a regression test → Round 2 re-audit of the fixes →
Round 3 senior-overseer full-branch verification. No finding closes
without a test or an explicit, documented "accepted" rationale.
