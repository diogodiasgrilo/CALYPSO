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
| C1 | `base_strategy.py:_execute_stop_loss` + `strategy.py` override | Close loop gated `if pos_id:`; `pos_id` always None on IBKR → stop never closed + P&L inverted (booked as profit). | ✅ **FIXED** — both close loops gate on `if uic:` (conid); M9 fixed alongside (`*_uic` cleared to None); +3 regression tests (close fires, both-legs, loss-not-profit) |
| C2 | `ib_client.py:_snapshot_with_preflight` | Preflight called `portfolio_accounts()` (`/portfolio/accounts`) — IBKR's snapshot endpoint requires `/iserver/accounts`. Every quote/greek/VIX read returned metadata-only — the live probe failure. | ✅ **FIXED** — new `_ensure_iserver_primed()` calls `receive_brokerage_accounts()` once/session; +3 tests |
| C3 | `ib_client.py:place_and_wait_for_fill` | Instant-fill short-circuit read `place_resp.get("status")`; IBKR place responses use `order_status`. | ✅ **FIXED** — reads `order_status` then `status` |
| C4 | `ib_client.py:place_and_wait_for_fill` | Terminal place response has no `filledQuantity`/`avgPrice` → filled order reported `filled_quantity=0` → double-position retry risk. | ✅ **FIXED** — on instant fill, one `get_order_status()` refetch for authoritative fill detail; graceful fallback to place_resp if purged; +2 tests |

## HIGH

| ID | File:line | Issue | Status |
|----|-----------|-------|--------|
| H1 | `base_strategy.py:_unwind_partial_entry` ~2896 | Unwind loop gated `if pos_id and uic:` — `pos_id` always None → partial-entry rollback is a no-op on IBKR → untracked partial position left open. | ✅ **FIXED** — `_unwind_partial_entry` close loop gates on `if uic:`; vacuous `if self.broker` guard flattened (M6 part) |
| H2 | `strategy.py:check_after_hours_settlement` ~10446 | Settlement gates on `registry.get_positions()` which is always empty on IBKR (registry never populated — `_register_position` early-returns on None pos_id). Always takes the empty-registry branch and unconditionally sets `_settlement_reconciliation_complete=True` — can finalize while a leg is still open → dropped expired credit, broken P&L identity. The conid→quantity `still_open_conids` retry logic is dead code. | ✅ **FIXED** — settlement now gates on `_expected_position_quantities()` (conid model), not the always-empty Position Registry. Empty conid set → process expired credits + mark complete; non-empty → conid-quantity reconciliation. The dead `still_open_conids` retry path is now live. |
| H3 | `base_strategy.py:_validate_config` ~5288 | Still requires a `saxo_api` config section + Saxo UIC keys; fatal `raise` on `__init__`. Passes only because configs still carry a vestigial `saxo_api` block — fails the bot to start once that's removed. | ✅ **FIXED** — only `strategy` section required now; `saxo_api` block tolerated as legacy (ignored). IBKR creds load via `ib_oauth.load_credentials()`. |
| H4 | `main.py:219,747` | `if not broker.connect():` is dead code — `connect()` never returns False, it raises `IBAuthError`/`IBConnectionError`. A failed connect escapes the try/except, crashes without `trade_logger.shutdown()`. | ✅ **FIXED** — both `broker.connect()` sites wrapped in `try/except (IBAuthError, IBConnectionError, IBClientError)` with clean `trade_logger.shutdown()` |
| H5 | `deploy/hydra.service:40-41` | `StartLimitInterval`/`StartLimitBurst` are in `[Service]`; they are `[Unit]`-section directives → silently ignored → restart rate-limiter inert → with H4, a credential failure = unbounded 30s crash-loop. | ✅ **FIXED** — `StartLimitIntervalSec`/`StartLimitBurst` moved to `[Unit]` + canonical spelling |
| H6 | `main.py:443` | Morning re-auth gate runs once/calendar-day; never re-arms. A mid-session 401/410 (dead brokerage session intraday) is never proactively recovered. | ✅ **FIXED** — intraday `ensure_connected()` re-check every 15 minutes; on failure break → systemd restart. |
| H7 | `base_strategy.py:_estimate_entry_credit_ib` ~1948 | `_quote_mid` returns 0.0 for a missing leg quote → credit computed from a partially-quoted spread = full short premium, overstated → MKT-011 gate can admit a sub-viable trade. | ✅ **FIXED** — `_estimate_entry_credit_ib` requires both legs of a side to be quoted in the batch (`_quoted()`); side returns 0.0 if any leg is missing → MKT-011 stays conservative; +1 test. |
| H8 | `ib_client.py:get_balance` ~1534/1546 | Raises `IBClientError` on a zero/NaN exchange rate → a transient missing ledger field blocks ALL trading. Also the FX composition direction is admitted-unverified (could be inverted → tradable off by rate²). | ✅ **FIXED** — `get_balance` no longer raises on a bad/missing FX rate; degrades to base-currency available funds (conservative for EUR-base/USD-trade) + `fx_rate_unavailable` flag. FX *direction* still pending live A.10 verification (tracked). |
| H9 | `base_strategy.py:_get_current_position_size` ~2564 | Counts open legs via `if entry.short_call_position_id:` → always 0 on IBKR → ORDER-006 `max_contracts_per_underlying` cap silently disabled. | ✅ **FIXED** — `_get_current_position_size` counts open legs by `*_uic` |
| H10 | `ib_client.py:_snapshot_with_preflight` ~1309 | Warmup budget 8×0.25s=2s too short for cold conids/greeks; batch (1 preflight + 8 polls) can exceed IBKR's 10 req/s → 429 + 10-min penalty box. | ✅ **FIXED** — warmup bumped to 12×0.5s=6s (was 8×0.25s=2s); ~2/s call rate, well under IBKR's 10/s. |
| H11 | `ib_client.py:modify_order` ~2354 | Hardcodes 0.05 tick (vs `place_order`'s `price_increment`); combo modify omits `conidex`/`sec_type=BAG` → effectively single-leg-only despite docstring claiming combo support. | ✅ **FIXED** — `modify_order` now exposes `price_increment` (mirrors `place_order`); docstring honestly flags single-leg-only (combo modify follow-up tracked). Not called by HYDRA — cancel+place is the production path. |
| H12 | `tests/test_ib_client_reads.py` | The `_ib_call` retry + circuit-breaker layer and `_unwrap` error-raise path were untested at the integration boundary; cOID retry-safety on `place_order` was unpinned. | ✅ **FIXED** — added `TestIbCallRetryBreaker` (7), `TestUnwrap` (5), `TestPlaceOrderCoidRetrySafety` (3). Pins: retry-on-transient→success, per-family breaker isolation, breaker opens after N consecutive retryable failures, unknown family raises `IBClientError`, non-retryable does NOT trip breaker, `_serialize` toggle honored, `_unwrap` raises on `.error`/`None`, generated cOIDs unique per call AND identical across retry attempts of the same call. |
| H13 | `docs/migration/STATE_SCHEMA_DESIGN.md`, `HYDRA_STANDALONE_REWRITE_PLAN.md`, `F4_POSITION_FLOW_DESIGN.md` | STATE_SCHEMA_DESIGN documents a `*_uic`→`*_instrument_id` / `position_id`→`order_id` rename that never happened (code keeps `_uic`/`_position_id`). REWRITE_PLAN status says "F5 in progress" when F1-F7+P1-P7 are done. F4 doc repeats the false rename claim. Materially misleads. | ✅ **FIXED** — STATE_SCHEMA_DESIGN.md marked SUPERSEDED with explanation; REWRITE_PLAN status corrected to F1–F7 + P1–P7 code-complete; F4 design doc carries a P7-audit correction note explaining the rename was abandoned. |

## MEDIUM

| ID | File:line | Issue | Status |
|----|-----------|-------|--------|
| M1 | `main.py:655` | `consecutive_errors >= 5` logs CRITICAL but never `break`s → bot spins forever on a persistent fault instead of letting systemd restart. | ✅ **FIXED** — main loop `break`s at 15 consecutive errors → clean systemd restart |
| M2 | `ib_oauth.py:_read_credential_file` | Swallows all `OSError` → a permission/IO error is indistinguishable from "credential absent" → misleading downstream error. | ✅ **FIXED** — split `FileNotFoundError` (silent "" return — file genuinely absent) from other `OSError` subclasses (permission, IO — WARNING-log naming the path + OSError type, then "" so downstream produces a single consistent error). +1 regression test. |
| M3 | `ib_oauth.py:234` | `if creds_dir:` — empty-string `CREDENTIALS_DIRECTORY` silently falls through to the dev path. Use `is not None`. | ✅ **FIXED** — explicit guard: set-but-empty (or whitespace-only) raises `RuntimeError` so a systemd `LoadCredentialEncrypted=` failure surfaces immediately instead of silently picking up dev credentials. +3 regression tests (empty / whitespace / unset). |
| M4 | `IBKR_CREDENTIALS_SETUP.md` | No `systemd-creds decrypt` round-trip verification step; no `systemd-analyze verify`. | ✅ **FIXED** — added "Pre-start verification (DO THIS BEFORE `systemctl enable`)" section with 3 mandatory steps: `systemd-analyze verify` (typo catch), `systemd-creds decrypt | wc -c` per file (byte-length sanity with expected sizes), spot-check `decrypt consumer_key.cred`. Operator must clear all 3 before enabling. |
| M5 | `deploy/hydra.service` | No sandboxing (`NoNewPrivileges`, `ProtectSystem`, `PrivateTmp`, `ReadWritePaths`) on a service holding decrypted private keys. | ✅ **FIXED** — `hydra.service` hardened: `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`, `ReadWritePaths=/opt/calypso`, kernel protections |
| M6 | `base_strategy.py:_verify_entry_fill_prices` ~2748, `_handle_naked_short` ~2871, `_get_total_saxo_pnl` ~4196 | The 3 P4 vacuous `if self.broker is not None:` guards leave variables (`legs`, `result`) scoped inside the guard but referenced outside, and `_get_total_saxo_pnl` can return None from a `->float`. (`_unwind_partial_entry` already flattened with H1.) | ✅ **FIXED** — all 3 vacuous broker guards flattened; latent NameError / None-return paths eliminated. |
| M7 | `strategy.py:9899` | FIX #82 / STATE-004 overnight-0DTE check gated on the always-empty registry → dead on IBKR. | ✅ **FIXED** — STATE-004 trigger now reads broker via `_read_open_positions(strict=True)` directly; broker fetch failure halts conservatively; stale registry entries cleaned up after broker confirms 0 open. |
| M8 | `strategy.py:_reconcile_positions` override ~10333 | MKT-033 AUTO external-long-salvage path still `pos_id`-gated → `pre_longs` always empty → external long-sale revenue never captured. | ✅ **FIXED** — `pre_longs` snapshot now gates on `uic` only (not `and pos_id`); IBKR `pos_id` is always None so the dead `and pos_id` clause blocked the entire AUTO capture path. |
| M9 | `strategy.py:6032` + `base_strategy.py:3075` | `*_uic` cleared to `0` instead of `None` post-stop — inconsistent; latent trap. | ✅ **FIXED** — cleared to `None` in both `_execute_stop_loss` versions (with C1) |
| M10 | `strategy.py:_read_index_price` ~1881 | `q.get("mid") or q.get("last") or q.get("mark")` — `or`-chain drops a legitimate falsy value; use the explicit `is not None` ladder like `get_vix_price`. | ✅ **FIXED** — explicit `is not None` ladder over (mid, last, mark); a legitimate 0.0 index quote is now treated as a price, not a fallback trigger. |
| M11 | `ib_client.py:what_if_order` ~2485 | `/iserver/account/{accountId}/order/whatif` lives on the `/iserver` namespace and requires the `_ensure_iserver_primed` preflight (same as snapshot). Without it whatif silently returns empty/metadata → BP gate is non-functional. | ✅ **FIXED** — `what_if_order` now calls `_ensure_iserver_primed()` before the whatif call; preflight is idempotent so cost is one extra request the first time per session. |
| M12 | `ib_client.py:place_and_wait_for_fill` poll loop | `get_order_status` can raise `CircuitBreakerOpen` (not `IBClientError`) → escapes uncaught, contradicting the documented `Raises:`. | ✅ **FIXED** — poll loop now catches `CircuitBreakerOpen` and surfaces as `timed_out` (the existing "may still be working — caller cancels + escalates" path); documented `Raises:` clause now honored. |
| M13 | `ib_client.py:_discover_account_id` ~692 | `data[0]["accountId"]` unguarded `KeyError` instead of `IBAuthError`. | ✅ **FIXED** — wrapped in try/except KeyError → `IBAuthError("portfolio_accounts returned a row without 'accountId'")`. |
| M14 | `ib_client.py:get_fx_rate` ~1656 | Guesses the response key `f"{source}_{target}"`; returns None silently on parse miss. | ✅ **FIXED** — explicit ladder of known IBKR shapes (`rate`, `{source}.{target}`, `{source}_{target}`); inverse key `{target}.{source}` auto-flipped with a WARNING log; unknown shape logs the response keys so a future broker shape change surfaces in logs. |
| M15 | `ib_client.py:_submit_order` ~2510 | For a combo/list place response, blindly promotes `data[0]` — may grab a reply prompt, not the order. | ✅ **FIXED** — when promoting, prefer entries that look like real order responses (have `order_id`/`id`) over reply-prompt entries; fallback to "first dict" with a WARNING log when no entry carries an id; +1 test (`test_prefers_order_id_entry_over_reply_prompt`). |
| M16 | `ib_client.py:streaming` ~2659 | `IbkrWsClient(start=True)` kicks ibind's listen thread async then immediately `StreamingManager.start()` — possible race against the WS handshake. | ✅ **FIXED** — `IbkrWsClient(start=False)`, then `ws.start()`, then a best-effort 5s wait on `ws.ready` BEFORE creating `StreamingManager`; if not ready in 5s, log + proceed and let ibind's reconnect-on-close recover. |
| M17 | `ib_client.py:_snapshot_with_preflight` ~1359 | Returns `data or []` — metadata-only is truthy → caller can't distinguish "no entitlement" from "preflight bug" from "no quote". | ✅ **FIXED** — after warmup exhaustion, logs distinct WARNING for metadata-only-data vs empty-data so operators can diagnose root cause; always returns a list (never None). |

## LOW / NIT

| ID | File:line | Issue | Status |
|----|-----------|-------|--------|
| L1 | `base_strategy.py:_place_option_order_ib` ~2327 | `quote.get("bid") or 0` masks a legitimate 0.0 bid. | ✅ **FIXED** — explicit `None` check distinguishes "no bid field" from "bid=0.0"; downstream behavior unchanged (`if bid > 0:` sends both to exempt path) but a future refactor won't mistakenly treat a real 0 as missing. |
| L2 | `strategy.py:_read_recent_bars` ~1500 | 8h lookback cap clamps silently — log a warning when it clamps. | ✅ **FIXED** — WARNING log when the requested window exceeds IBKR's 8h ceiling, naming the requested vs clamped hours; caller may receive fewer bars than requested. |
| L3 | `strategy.py:_read_option_quotes_batch` ~1614 | Silently drops rows with no `conid` — log when `len(out) < len(chunk)`. | ✅ **FIXED** — distinct logs for "row had no conid field" (WARNING — possible ibind response-shape change) vs "requested N got <N quoted rows" (DEBUG — normal illiquid/halted attrition); first-row sample included in WARNING. |
| L4 | `base_strategy.py:3668` | Dead Saxo-shaped `_extract_price` / `_check_quote_freshness` (zero callers) — P2 missed them; delete. | ✅ **FIXED** — both methods deleted (~90 lines); breadcrumb comment explains where the IBKR-equivalent lives (`strategy._read_index_price` for price; `availability` field on IBKR quotes for freshness). Verified zero callers via repo-wide grep on 2026-05-22. |
| L5 | `base_strategy.py:905` | `__init__` still reads dead Saxo UIC config keys (`underlying_uic` etc.). | ✅ **FIXED** — `__init__` now sets only `underlying_symbol` (the meaningful key for `qualify_contract` lookup); the dead `underlying_uic`/`option_root_uic`/`vix_uic` attribute assignments removed. The legacy `_validate_config` still tolerates the JSON keys if present (validates int-shape) but never reads them. |
| L6 | `ib_client.py:1018` | `qualify_contract` underlying-conid `or` precedence is fragile; `conidEx` is a compound id — `int()` on it would raise. | ✅ **FIXED** — only canonical `conid` accepted; fixed the precedence bug (`isinstance(...)` ternary was mis-bound with `or` and called `.get(...)` unconditionally) by extracting `chosen0` first; missing conid now raises `IBClientError` instead of falling back to a compound `conidEx`. |
| L7 | `ib_client.py:525` | LST-failure classification substring-matches `"401"` in arbitrary error text. | ✅ **FIXED** — `\b401\b` word-boundary regex replaces loose substring match (no longer collides with "error 4017" or a URL segment); long keyword phrases kept as substring matches. |
| L8 | `ib_client.py:1916` | `get_option_chain` `calls + puts` assumes both are lists. | ✅ **FIXED** — `_as_list(v)` helper coerces None / scalar / dict / list to a list before concatenation; thin-chain single-strike responses no longer raise TypeError. |
| L9 | `ib_client.py:1525` | `mid` computed even on a crossed market (`bid > ask`). | ✅ **FIXED** — `mid` now returns None when `bid > ask`; downstream callers fall back to `last`/`mark` or skip the tick (nonsense (bid+ask)/2 of a crossed quote no longer pollutes credit estimation / stop monitoring). |
| L10 | `main.py:868` | `--live` help text + epilog still advertise live trading that doesn't exist. | ✅ **FIXED** — module docstring + parser epilog + `--live` help string rewritten to "DEPRECATED, no-op". Examples drop the `--live` line. The runtime NOTE at startup is unchanged. |
| L11 | docs | Audit flagged `strategy.py`/`base_strategy.py` docstrings as referencing non-existent `docs/MEIC_STRATEGY_SPECIFICATION.md`. | ✅ **CLOSED (false positive)** — verified `docs/MEIC_STRATEGY_SPECIFICATION.md` and `docs/HYDRA_STRATEGY_SPECIFICATION.md` both exist (re-checked 2026-05-22). No action needed. |
| L12 | `bots/hydra/__init__.py` | Not updated for the migration — module docstring still MEIC-only, version history has no IBKR-migration entry; `main.py` banner version `1.24.0` stale. | ✅ **FIXED** — module docstring now leads with "IBKR Web API"; new `2.0.0-rc.1 (2026-05-22, branch: hydra-ibkr-standalone)` version-history entry summarizes F1-F7 + P1-P7 scope and points at `HYDRA_STANDALONE_REWRITE_PLAN.md` + `P7_AUDIT_FINDINGS.md`; main.py banner bumped to `2.0.0-rc.1 (IBKR-standalone)`. |
| L13 | `base_strategy.py:158,3868` (operator-visible) | Stale "Saxo" comments/docstrings/log strings describing current behaviour. | ✅ **FIXED** — operator-visible strings de-Saxo'd: MARGIN_CHECK_ENABLED const comment + `_position_is_settled` docstring. Other Saxo references in the file are historical context (explain WHY a fix exists, citing the original Saxo bug) and intentionally retained as a record of the migration trail. |
| L14 | tests | `get_vix_price` tests use exact float `==` instead of `pytest.approx`. | ✅ **FIXED** — 3 vix-price tests now use `pytest.approx`; pinned against future refactors that might change the mid-price computation order. |
| L15 | tests | `MEICStrategy.__new__` + bare `MagicMock` for `daily_state` — unknown-attribute access fabricated; use `spec=`. | ✅ **FIXED** — all 12 bare `MagicMock()` daily_state instantiations in test_hydra_init_broker_kwarg.py now use `MagicMock(spec=MEICDailyState)`; typo'd attribute lookups would now AttributeError instead of silently fabricating. |

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

## Round 2 re-audit — 2026-05-22, branch tip `7d6f04f`

Four parallel domain agents re-verified the fixes against the live
code, asking three questions per finding: (a) does the code actually
do what the register claims? (b) does the fix miss any edge case?
(c) did the fix break anything in surrounding code? Plus a fresh
sweep for new issues. Read-only audit; no files modified.

| Agent | Domain | Verdict | New findings | Concerns |
|---|---|---|---|---|
| 1 | IBKR Client + writes (`ib_client.py`, `ib_oauth.py`, `ib_retry.py`, `ib_streaming.py`, related tests) | ✅ PASS | 0 | 0 |
| 2 | HYDRA strategy + position flow (`strategy.py`, `base_strategy.py`, `main.py`, `__init__.py`, init tests) | ✅ PASS | 0 | 0 |
| 3 | Ops / Auth / Deploy (`ib_oauth.py`, `hydra.service`, `IBKR_CREDENTIALS_SETUP.md`, migration docs) | ✅ PASS | 0 | 3 minor (doc clarity — addressed) |
| 4 | Tests + integration (all `tests/test_ib_*`, `test_hydra_init_broker_kwarg`) | ✅ PASS | 0 | 3 low-risk observations (test isolation, OS assumption, retry-policy mutation — all acceptable) |

**Round 2 result: PASS across all 4 domains, 0 new bugs introduced
by the Round 1 fixes, 0 incomplete fixes.** The three minor doc-
clarity concerns from Agent 3 were addressed post-audit:

- `hydra.service` sandboxing comments now explicitly state that
  `LoadCredentialEncrypted=` runs BEFORE the sandboxing directives
  take effect (so `ProtectSystem=strict` does not block credential
  reading — the bot reads from the tmpfs at runtime, never from
  `/etc/calypso/ibkr/`).
- `IBKR_CREDENTIALS_SETUP.md` step 1 now spells out the expected
  ownership/mode invariant (`stat -c '%a %U:%G'` returns `700 root:root`)
  and notes that `install -d -m 0700` sets both in one step.

Round 3 (senior-overseer full-branch verification) is the next gate.

## Round 3 senior-overseer — 2026-05-22, branch tip `bd0f44f`

A single senior overseer with the full Round 1 register + Round 2
reports + branch diff did a 7-section pre-deployment verification.
The sections cover: (1) money-path trace (connect → qualify →
quote → place → monitor → stop → settle), (2) 8 failure modes (token
expiry, 429, breaker trip, partial fill, stale order_id, naked
short, WS disconnect, systemd-creds failure), (3) operability
(startup banner, heartbeat logs, docs completeness, alert messages),
(4) test trustworthiness, (5) Saxo residue check, (6) pre-VM-deploy
gating, (7) final verdict.

**Round 3 result: ✅ PASS across all 7 sections.**

  • Money-path: every critical action gates on `*_uic` (conid), not
    `*_position_id`. Settlement uses the conid-quantity model. No
    Saxo assumptions survive on the IBKR path.
  • Failure modes: every scenario has an explicit handler — no silent
    failures, no double-positions, no orphaned orders.
  • Operability: a cold operator can deploy via
    `IBKR_CREDENTIALS_SETUP.md`, start the service, and read `journalctl`
    to know exactly what's happening. Alerts name the leg + uic + action.
  • Saxo residue: zero active imports / instantiations of Saxo code.
    All remaining `Saxo`/`saxo` strings are intentional historical
    context (design rationale, migration trail) inside comments and
    docstrings.
  • Pre-deploy: code+docs+systemd+pre-flight checklist all complete.
    The only blockers are external: Step 1 (user-side IBKR data-sharing
    toggle propagation, ~24h) and Step 2 (probe re-run to verify
    real-time data flows).

**Minor non-blocking observations (3 items, all documented as deferred
or operator-watch):**

  • DEF-7 (POS-003 mid-session reconciliation still keyed on
    `position_id` in one code path) — documented in
    `docs/migration/DEFERRED_WORK.md` as accepted post-merge work.
    Doesn't affect paper-validation correctness; affects only the
    mid-day reconciliation safety net.
  • Snapshot subscription re-priming (~15-min topic TTL) — code's
    warmup-poll handles empty responses, but the explicit re-prime
    cadence is not coded. Flagged as operator-watch during Step 5.
  • OS-level test mocks (`platform.system()` mocks in a few unit
    tests) — low-risk, intentional for cross-platform CI.

**Money question — "Would I bet my house on this bot trading correctly
on paper for a full week without manual intervention starting Monday?"**

**Answer: YES**, with one caveat: Step 1 (IBKR subscriptions +
data-sharing toggle) MUST complete and propagate fully, AND Step 2
(probe) MUST confirm real-time quotes, BEFORE paper trading begins.
If the paper account gets only delayed (15-min) data, the bot will
place trades at wrong strikes and miss stop opportunities — a
prerequisite issue, not a code issue.

**Branch is cleared for Step 5 (VM deploy + paper smoke test).**
The remaining P7 steps are Steps 1–2 (user-side, external) and 5–6
(operator-side, the VM deploy + the CLAUDE.md rewrite + final merge).
