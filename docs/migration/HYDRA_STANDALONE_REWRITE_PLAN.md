# HYDRA Standalone Rewrite — Implementation Plan

**Status**: ✅ **F1–F7 + P1–P7 code-complete (2026-05-22).** See
`P7_GO_LIVE_PLAN.md` for the go-live sequence and
`P7_AUDIT_FINDINGS.md` for the multi-agent audit register. (Earlier
"F5 in progress" status was stale — corrected 2026-05-22.)
**Created**: 2026-05-19
**Owner**: Diogo Dias

This is the implementation contract for converting HYDRA from a Saxo-backed MEIC-inheritor into a standalone IB-only strategy, with mass deletion of every other bot and every Saxo trace in the repo.

---

## 0. Executive summary

| Metric | Value |
|---|---|
| **Goal** | HYDRA = the only bot, IB-only, no MEIC inheritance, no Saxo code anywhere |
| **LOC deleted** | ~25,000 (bots/meic, iron_fly, delta_neutral, rolling_put_diagonal, saxo_client.py, token_coordinator.py, external_price_feed.py, shared/broker/, related tests + docs) |
| **LOC rewritten** | ~5,000–7,000 (HYDRA strategy gains MEIC-inherited methods + IB rewiring; tests; docs sweep) |
| **Net repo change** | Repo shrinks by ~20,000 LOC |
| **Risk vector** | HYDRA is in dry-run since 2026-04-27 — no real money exposure during rewrite |
| **Plan structure** | 15 incremental commits on branch `hydra-ibkr-standalone`, each leaving tests passing |
| **Timeline** | Realistic 9–12 working days with debugging |
| **Approval gate** | This doc reviewed + approved before Phase NEW-2 begins |

---

## 1. End-state architecture

### Files that survive

```
bots/
  hydra/
    main.py            ← uses IBClient directly (no broker abstraction)
    strategy.py        ← standalone HydraStrategy class (no MEIC inheritance)
    brandon/           ← standalone overlay, mocks IBClient in tests
    config/

shared/
  ib_client.py         IB CP API client (with new helpers added)
  ib_oauth.py          OAuth 1.0a for IBKR
  ib_streaming.py      WS streaming manager
  ib_retry.py          Retry + per-family circuit breakers
  ib_reconcile.py      Order reconciliation
  ib_constants.py      Field codes + spread template
  alert_service.py     Telegram/Email via Pub/Sub (broker-agnostic)
  event_calendar.py    FOMC + economic calendar
  market_hours.py      US market hours + holidays
  logger_service.py    Trade logging (Google Sheets, local files)
  position_registry.py Multi-bot position isolation (kept even though single bot, for future-proofing)
  secret_manager.py    GCP Secret Manager
  sheets_reader.py     Google Sheets reads (for agents)
  technical_indicators.py  EMA, ATR
  config_loader.py     Config loading
  market_status_monitor.py  Market open/close notifications
  claude_client.py     Anthropic API (for agents)
  data_recorder.py     SQLite recording

services/
  homer/               Trading journal writer (broker-agnostic)
  (other agents: apollo, argus, clio, hermes — each is broker-agnostic, reads logs/sheets)

cloud_functions/
  alert_processor/     Pub/Sub → Telegram/Gmail

dashboard/
  backend/             FastAPI + WebSocket (read-only)
  frontend/            React 19 + Vite
  scriptable/          iOS widget

tests/
  test_ib_client*.py, test_ib_oauth.py, test_ib_streaming.py, test_ib_reconcile.py, test_ib_retry.py  ← keep all
  test_brandon_*.py    ← rewrite mocks: SaxoClient → IBClient
  test_daily_summary_v127.py  ← rewrite mocks
  test_position_registry.py  ← keep (broker-agnostic)
  (delete: test_saxo_broker_adapter.py, test_broker_*.py, test_ibkr_broker_adapter.py)

docs/
  HYDRA_STRATEGY_SPECIFICATION.md   ← rewritten for IBKR
  HYDRA_TRADING_JOURNAL.md          ← keep
  HYDRA_EARLY_CLOSE_ANALYSIS.md     ← keep
  HYDRA_BUFFER_OPTIMIZATION.md      ← keep
  HYDRA_VARIANT_TESTING_PLAN.md     ← rewritten for IB-only variants
  ALERTING_SETUP.md                 ← keep (broker-agnostic)
  GOOGLE_SHEETS.md                  ← keep
  DEPLOYMENT.md                     ← rewritten for single bot
  VM_COMMANDS.md / VM_COMMAND_REFERENCE.md  ← rewritten
  migration/
    HYDRA_STANDALONE_REWRITE_PLAN.md     ← this file
    SAXO_TO_IB_MIGRATION_PLAN.md         ← historical (the journey to here)
    SAXO_API_PATTERNS.md                 ← historical
    SAXO_BOTS_SAFETY_MEASURES_GUIDELINE.md  ← historical
    IRON_FLY_*.md, MEIC_*.md, DELTA_NEUTRAL_*.md, ROLLING_PUT_DIAGONAL_*.md  ← historical (bots deleted)
    research_scratch/                    ← keep all (IB research notes)

deploy/
  hydra.service        (verify on VM what's currently there)
  apollo.service/.timer, argus.service/.timer, clio.service/.timer, hermes.service/.timer, homer.service/.timer
  db_backup.service/.timer
  dashboard-hydra.json
  calypso.service      (verify purpose)
  ops-agent-config.yaml
  polygon.env.example
  setup_vm.sh, setup-monitoring-dashboard.sh
```

### Files that get deleted

| Path | LOC | Reason |
|---|---|---|
| `shared/saxo_client.py` | 5,152 | Saxo Bank API wrapper — no longer used |
| `shared/token_coordinator.py` | 349 | Saxo OAuth token coordination — IBKR uses different flow |
| `shared/external_price_feed.py` | 202 | Yahoo VIX fallback — IBKR has native VIX |
| `shared/broker/interface.py` | ~250 | Broker abstraction — single broker means no abstraction needed |
| `shared/broker/factory.py` | ~80 | build_broker() factory — N/A |
| `shared/broker/saxo_adapter.py` | ~300 | Saxo adapter — N/A |
| `shared/broker/ibkr_adapter.py` | ~200 | IB adapter — HYDRA calls IBClient directly |
| `shared/broker/streaming_proxies.py` | ~150 | Streaming proxies — N/A |
| `bots/meic/` | 8,777 + main.py + config | MEIC bot — kill-switched, never trading again |
| `bots/iron_fly_0dte/` | ~5,000 | Iron Fly bot — kill-switched |
| `bots/delta_neutral/` | ~10,800 | Delta Neutral bot — kill-switched |
| `bots/rolling_put_diagonal/` | ~5,100 | Rolling Put Diagonal bot — kill-switched |
| `services/token_keeper/` | ~200 | Saxo token refresher daemon — N/A |
| `tests/test_saxo_broker_adapter.py` | ~500 | Tests for deleted module |
| `tests/test_broker_factory.py`, `test_broker_interface.py`, `test_broker_streaming_proxies.py`, `test_ibkr_broker_adapter.py` | ~1,000 | Tests for deleted modules |
| `deploy/meic.service`, `iron_fly_0dte.service`, `delta_neutral.service`, `rolling_put_diagonal.service`, `token_keeper.service`, `deploy-hydra-rename.sh` | ~150 | Systemd units for deleted bots |
| `docs/MEIC_*.md`, `IRON_FLY_*.md`, `DELTA_NEUTRAL_*.md`, `ROLLING_PUT_DIAGONAL_*.md`, `SAXO_*.md` | Various | MOVED to `docs/migration/` as historical records (not deleted — preserved) |
| **Total deletions** | **~38,000 LOC** | |

---

## 2. Section A — MEIC methods HYDRA inherits + uses

These methods live in `bots/meic/strategy.py` (the `MEICStrategy` class). HYDRA either calls them via `super()`, relies on them being called by inherited state-machine logic, or overrides them. **All must be ported into the standalone HydraStrategy class.**

| Method | MEIC file location | HYDRA usage | Complexity |
|---|---|---|---|
| `__init__` | line ~828 | super().__init__() called from HydraStrategy.__init__ | Large (config validation, state init) |
| `run_strategy_check()` | ~1098 | Entry point — state machine loop | Large |
| `_run_strategy_check_internal()` | ~1121 | Inner loop | Large |
| `_handle_idle_state()` | ~1524 | Wait-for-market-open path | Moderate |
| `_should_attempt_entry()` | ~1693 | Time-window check | Moderate |
| `_get_next_entry_time()` | ~1721 | Schedule lookup | Trivial |
| `_skip_missed_entries()` | ~1727 | Catch-up logic on restart | Moderate |
| `_is_entry_time()` | ~1783 | Window check (HYDRA overrides) | Trivial |
| `_initiate_entry()` | ~1810 | Pre-entry filters + dispatch | Large (many filters) |
| `_execute_entry()` | ~2782 | Place 4-leg IC | **Large + Saxo-fundamental** |
| `_place_option_order()` | ~2934 | Place single option leg | **Large + Saxo-fundamental** (300+ LOC, BuySell, OrderType, activity-stream fill detection, 5-level retry) |
| `_close_position_with_retry()` | ~4276 | Close position | **Large + Saxo-fundamental** (400+ LOC, similar complexity) |
| `_execute_stop_loss()` | ~4035 | Stop trigger (HYDRA overrides parts) | Large |
| `_check_stop_losses()` | ~3877 | Stop monitoring scan | Large |
| `_get_option_uic()` | ~3478 | Strike→UIC lookup | Moderate — **Saxo concept (UIC); rewrite as `_get_option_conid()`** |
| `_simulate_entry_prices()` | ~4018 | Dry-run price simulation | Moderate |
| `_validate_pnl_sanity()` | ~8444 | DATA-003 sanity bounds | Moderate |
| `_validate_config()` | ~8349 | Config schema check | Moderate |
| `_get_cached_price()` | ~7646 | WS price cache read | Trivial |
| `update_ws_price_cache()` | ~7636 | WS price cache write | Trivial |
| `_save_state_to_disk()` | ~7513 | State persistence | Moderate |
| `_recover_positions_from_saxo()` | ~5348 | Startup recovery — **named for Saxo, rename `_recover_positions_from_broker()`** | Large |
| `_reconcile_positions()` | ~6202 | Position registry vs broker reconciliation | Large |
| `log_account_summary()` | ~7271 | Logging | Moderate |
| `log_daily_summary()` | ~7357 | Daily P&L summary | Moderate |
| `get_status_summary()` | ~6704 | Telegram /status output | Moderate |
| `get_detailed_position_status()` | ~6730 | Telegram /snapshot output | Moderate |
| `get_daily_summary()` | ~6656 | Daily metrics dict | Moderate |
| `_send_daily_summary()` | ~6643 | Alert delivery | Moderate |
| `_log_entry()` | ~6545 | Per-entry log | Trivial |
| `_log_stop_loss()` | ~6576 | Per-stop log | Trivial |
| `_log_safety_event()` | ~6614 | Safety event log | Trivial |
| `check_after_hours_settlement()` | ~6447 | EOD settlement | Moderate |
| `_reset_for_new_day()` | ~6282 | Daily counter reset | Moderate |
| `_process_expired_credits()` | ~6364 | EOD expired-credit handling | Moderate |

**~35 methods to port.** Note: line numbers are approximate (from agent inventory); verify during implementation.

**Additionally**: HYDRA's own methods that override or shadow MEIC methods (e.g. HYDRA's `_execute_stop_loss` calls `super()._execute_stop_loss(...)`) — when MEIC inheritance is removed, the super() calls must be rewritten to call HYDRA's own inlined version of the parent logic.

---

## 3. Section B — Saxo concept leaks in HYDRA

Every Saxo-specific concept HYDRA's code knows about. Each becomes an IBKR replacement during the rewrite.

### B.1 — `BuySell` enum (Saxo directive: BUY/SELL)
**Usage in HYDRA** (`bots/hydra/strategy.py`):
- Line 45: `from shared.saxo_client import BuySell` import
- Lines 5023, 5041, 5168, 5186: `buy_sell=BuySell.BUY` / `BuySell.SELL` in `_place_option_order()` calls

**Usage in Brandon** (`bots/hydra/brandon/strategy.py`):
- Line 760-762: Lazy import comment
- Line 774: `buy_sell = BuySell.BUY if leg.side == "long" else BuySell.SELL`

**IBKR replacement**: IBClient uses string `side="BUY"`/`"SELL"` directly in `place_iron_condor()` / `place_vertical_spread()` / `place_order()`. No enum needed. Strategy code passes strings.

### B.2 — `asset_type="StockIndexOption"` (Saxo classification)
**Usage in HYDRA**: lines 1969, 3264, 3512, 3924, 6107, 6147 (in `get_quote`, `get_quotes_batch`, `get_option_greeks` calls)

**IBKR replacement**: IBClient doesn't take asset_type — instead, contract identity is encoded in the conid itself (via `qualify_contract(symbol, expiry, strike, right, trading_class="SPXW")`). Remove the parameter entirely.

### B.3 — `Uic` (Saxo Unique Instrument Code)
**Usage in HYDRA**: ~10 sites — `pos.get("PositionBase", {}).get("Uic")`, `opt.get("Uic")`, etc.

**IBKR replacement**: `conid` (Contract ID). Different integer scheme. Positions return flat dicts; rewrite position-dict access.

### B.4 — `_make_request` raw HTTP (Saxo REST bypass)
**Usage in HYDRA**: lines 9506-9508 — raw call to Saxo's closed-positions report endpoint:
```python
response = self.client._make_request(
    "GET",
    f"/cs/v1/reports/closedPositions/{self.client.client_key}/{today}/{today}"
)
```

**IBKR replacement**: IBClient has `/port/v1/closedpositions` endpoint or use `get_positions` with date filter. Add `IBClient.get_closed_positions_report(from_date, to_date)` helper.

### B.5 — `client_key` property (Saxo account ID concept)
**Usage in HYDRA**: line 9508 — used only in `_make_request` URL above.

**IBKR replacement**: `account_id` property on IBClient. URL construction goes inside the IBClient helper (Step B.4 above), not in strategy code.

### B.6 — `check_order_filled_by_activity` (Saxo binary WS activity stream)
**Usage in HYDRA**: line 2084 — `filled, fill_details = self.client.check_order_filled_by_activity(order_id, uic)`

**IBKR replacement**: IBClient has `get_order_status(order_id)` + `check_order_filled_by_activity()` (already implemented per Phase A code). Activity-stream is Saxo-specific anti-pattern; IBKR's order status is broker-authoritative. Rewrite to `get_order_status` + parse `Filled` status.

### B.7 — `get_closed_position_price` (Saxo settlement price helper)
**Usage in HYDRA**: lines 2099, 5557, 9615 — `closed = self.client.get_closed_position_price(uic, buy_or_sell=...)`

**IBKR replacement**: IBClient already has `get_closed_position_price(conid, buy_or_sell)` (added during Phase A.10 fix work). Translate UIC arg → conid arg. Direct mapping.

### B.8 — Position dict shape (`PositionBase`, `OptionsData`, nested keys)
**Usage in HYDRA**: ~20+ sites — `pos.get("PositionBase", {})`, `pos_base.get("OptionsData", {})`, `pos_base.get("Amount")`, etc.

**IBKR replacement**: IBKR positions are flat dicts: `{"conid": ..., "position": <amount>, "avgCost": ..., "mktPrice": ..., "unrealizedPnl": ...}`. Add `IBClient._normalize_position_dict()` helper that returns shape HYDRA can use, OR rewrite HYDRA's position-dict access to expect IBKR's flat shape.

### B.9 — Order response dict shape (`OrderId`, fill-price extraction)
**Usage in HYDRA**: ~5 sites where `_place_option_order` (inherited from MEIC) returns Saxo's dict.

**IBKR replacement**: IBClient `place_order` returns `{"order_id": "...", "status": "...", ...}` already. Adapt MEIC port to use IBKR's shape.

### B.10 — `OrderType.MARKET` / `OrderType.LIMIT` enums (Saxo)
**Usage**: mostly in inherited MEIC `_place_option_order`. Once ported, replace with `order_type="MKT"` / `order_type="LMT"` strings (IBClient convention).

### B.11 — Saxo VIX session-capability dance
**Usage**: lives in `saxo_client.py`, not in HYDRA directly. When we delete `saxo_client.py`, this dies with it.

**IBKR replacement**: None needed — IBKR's VIX entitlement is via the CSMI subscription (bundled in US Securities bundle). No upgrade dance.

### B.12 — Yahoo VIX fallback
**Usage**: `external_price_feed.py` is the fallback. Imported but not directly called by HYDRA code paths after recent updates.

**IBKR replacement**: Delete fallback entirely. If IBKR VIX ever fails (entitlement-related), bot should log + halt rather than fall back to delayed Yahoo data.

**Total Saxo-leak sites in HYDRA itself: ~50 (mostly Uic/PositionBase shape + asset_type strings + 4 BuySell uses).** When MEIC methods are ported in, this expands by another ~100+ sites inside the ported methods (mostly inside `_place_option_order` and `_close_position_with_retry`).

---

## 4. Section C — `shared/` module classification (verified)

| File | LOC | Status | Action |
|---|---|---|---|
| `saxo_client.py` | 5,152 | Used only by deleted bots + Saxo path | ✅ DELETE |
| `token_coordinator.py` | 349 | Used only by Saxo-related modules | ✅ DELETE |
| `external_price_feed.py` | 202 | Yahoo fallback for Saxo VIX | ✅ DELETE |
| `broker/interface.py` | ~250 | Abstraction layer | ✅ DELETE |
| `broker/factory.py` | ~80 | build_broker() | ✅ DELETE |
| `broker/saxo_adapter.py` | ~300 | Saxo→broker shim | ✅ DELETE |
| `broker/ibkr_adapter.py` | ~200 | IB→broker shim — HYDRA will call IBClient directly | ✅ DELETE |
| `broker/streaming_proxies.py` | ~150 | Streaming abstraction | ✅ DELETE |
| `ib_client.py` | ~1,600 | IB CP API client | ✅ KEEP (expand with helpers needed by HYDRA) |
| `ib_oauth.py` | ~230 | OAuth 1.0a | ✅ KEEP |
| `ib_streaming.py` | ~440 | WS manager | ✅ KEEP |
| `ib_retry.py` | ~310 | Retry + breakers | ✅ KEEP |
| `ib_reconcile.py` | ~310 | Order reconcile | ✅ KEEP |
| `ib_constants.py` | ~50 | Field codes | ✅ KEEP |
| `alert_service.py` | — | Telegram/Email via Pub/Sub | ✅ KEEP (broker-agnostic) |
| `market_hours.py` | — | US market hours | ✅ KEEP |
| `event_calendar.py` | — | FOMC calendar | ✅ KEEP |
| `logger_service.py` | — | Trade logging | ✅ KEEP (update bot-name references) |
| `position_registry.py` | — | Multi-bot isolation | ✅ KEEP (future-proof) |
| `secret_manager.py` | — | GCP secrets | ✅ KEEP |
| `sheets_reader.py` | — | Google Sheets reads | ✅ KEEP (for agents) |
| `technical_indicators.py` | — | EMA, ATR | ✅ KEEP |
| `config_loader.py` | — | Config loading | ✅ KEEP |
| `market_status_monitor.py` | — | Market notifications | ✅ KEEP (used by Delta Neutral originally; verify HYDRA usage; may delete if unused) |
| `claude_client.py` | — | Anthropic API | ✅ KEEP (agents) |
| `data_recorder.py` | — | SQLite recording | ✅ KEEP (HYDRA's backtesting DB) |

**Verification step before deletion** (Phase NEW-3b):
```bash
# Confirm no surviving file imports the to-be-deleted modules:
grep -rl "from shared.saxo_client\|import shared.saxo_client" --include="*.py" .
grep -rl "from shared.token_coordinator\|import shared.token_coordinator" --include="*.py" .
grep -rl "from shared.external_price_feed\|import shared.external_price_feed" --include="*.py" .
grep -rl "from shared.broker\|import shared.broker" --include="*.py" .
# Expected output AFTER bots/meic, iron_fly, delta_neutral, rolling_put_diagonal are deleted: empty
```

---

## 5. Section D — Tests to rewrite/delete

| Test file | Action |
|---|---|
| `test_ib_client.py`, `test_ib_client_reads.py`, `test_ib_client_writes.py`, `test_ib_oauth.py`, `test_ib_streaming.py`, `test_ib_reconcile.py`, `test_ib_retry.py` | ✅ KEEP — already IBClient-only, 430/430 green |
| `test_brandon_defensive_overlay.py`, `test_brandon_gex_*.py`, `test_brandon_hedge_position.py`, `test_brandon_narrow_spread.py`, `test_brandon_strategy_integration.py`, `test_brandon_take_profit.py` | 🔄 REWRITE — mock IBClient instead of SaxoClient where applicable |
| `test_daily_summary_v127.py` | 🔄 REWRITE — known to have 18 pre-existing failures; fix while we're rewriting mocks |
| `test_position_registry.py` | ✅ KEEP (broker-agnostic) |
| `test_saxo_broker_adapter.py` | ❌ DELETE |
| `test_broker_factory.py`, `test_broker_interface.py`, `test_broker_streaming_proxies.py`, `test_ibkr_broker_adapter.py` | ❌ DELETE |
| All HYDRA-specific tests (test_hydra_*.py if any) | 🔄 REWRITE / KEEP per file |

**Verification step**: run `grep -l "SaxoClient\|saxo_client\|MEICStrategy" tests/*.py` to surface every test that needs rewriting.

---

## 6. Section E — Documentation sweep

### Documents to KEEP (with edits to remove Saxo references where they exist)
- `CLAUDE.md` — major rewrite (see Section F below)
- `docs/HYDRA_STRATEGY_SPECIFICATION.md` — rewrite for IBKR concepts (conid not UIC, order combo semantics, etc.)
- `docs/HYDRA_TRADING_JOURNAL.md` — keep, may add IB section
- `docs/HYDRA_EARLY_CLOSE_ANALYSIS.md` — keep
- `docs/HYDRA_BUFFER_OPTIMIZATION.md` — keep
- `docs/HYDRA_VARIANT_TESTING_PLAN.md` — rewrite for IB variants
- `docs/ALERTING_SETUP.md` — keep (broker-agnostic)
- `docs/GOOGLE_SHEETS.md` — keep
- `docs/DEPLOYMENT.md` — rewrite for single-bot world
- `docs/VM_COMMANDS.md`, `docs/VM_COMMAND_REFERENCE.md` — rewrite (remove deleted bot commands)
- `docs/migration/SAXO_TO_IB_MIGRATION_PLAN.md` — preserved as historical

### Documents to MOVE to `docs/migration/` (preserved as historical record)
- `docs/SAXO_API_PATTERNS.md`
- `docs/SAXO_BOTS_SAFETY_MEASURES_GUIDELINE.md`
- `docs/MEIC_STRATEGY_SPECIFICATION.md`
- `docs/MEIC_EDGE_CASES.md`
- `docs/IRON_FLY_STRATEGY_SPECIFICATION.md`
- `docs/IRON_FLY_EDGE_CASES.md`
- `docs/IRON_FLY_CODE_AUDIT.md`
- `docs/DELTA_NEUTRAL_STRATEGY_SPECIFICATION.md`
- `docs/DELTA_NEUTRAL_EDGE_CASES.md`
- `docs/ROLLING_PUT_DIAGONAL_EDGE_CASES.md`
- `docs/THETA_PROFITS_STRATEGY_ANALYSIS.md` (multi-bot analysis)
- `docs/PORTFOLIO_ALLOCATION_ANALYSIS.md` (multi-bot allocation)
- `docs/MULTI_BOT_POSITION_MANAGEMENT.md` (multi-bot)

### bots/*/README.md files
- `bots/hydra/README.md` — keep, rewrite for IBKR specifics
- `bots/meic/README.md`, `bots/iron_fly_0dte/README.md`, `bots/delta_neutral/README.md`, `bots/rolling_put_diagonal/README.md` — deleted with their parent dirs

### Docstrings + comments sweep
Every `.py` file kept in the repo gets its docstrings and comments reviewed. Specifically:
- Module-level docstrings that mention Saxo, MEIC, "5 bots", "multi-bot", etc. — rewrite
- Method docstrings that say "SaxoClient X" → "IBClient X"
- Inline comments referencing Saxo concepts (UIC, BuySell, etc.) → IBKR equivalents
- Migration plan references (`docs/migration/...`) — keep as historical pointers

This sweep happens **incrementally inside the per-commit work**, not as a separate cleanup pass at the end.

---

## 7. Section F — CLAUDE.md rewrite plan

CLAUDE.md is 154KB+. Per-section verdicts:

| Heading | Action |
|---|---|
| CRITICAL: Bot Control Warning | Rewrite: HYDRA only |
| CRITICAL: Shared Code Change Policy | Major rewrite: single-bot world, much simpler |
| Project Overview | Major rewrite: IBKR, IB-only, no broker abstraction |
| Trading Bots (5 Total) → Trading Bot (1) | Collapse to one row |
| Dry-Run vs Live Mode | Simplify — HYDRA only |
| Token Keeper Service | DELETE entire section |
| Iron Fly / Delta Neutral / MEIC / Rolling Put Diagonal bot details | DELETE entire subsections |
| HYDRA Bot Details | KEEP, audit for any Saxo concept that leaked in |
| Alert System | Keep, update bot list |
| Agent Suite | Keep — agents are broker-agnostic |
| HYDRA Dashboard | Keep — read-only, broker-agnostic |
| Quick Reference Commands | Rewrite — remove deleted bot commands |
| Troubleshooting | Delete Saxo-specific sections (VIX fallback, binary WS, session capabilities, etc.), keep IB-specific (will be added as we encounter them) |
| Running Diagnostic Scripts on VM | Keep — generic pattern |
| Important Notes (1–75+) | Most are Saxo bug fixes; delete 90%+; keep IBKR-relevant ones (only Phase A.10 fixes 2026-05-16 to 2026-05-19 stay) |
| Documentation links | Update — remove deleted bot specs, point to migration/ for historical |
| Config Files | HYDRA only |
| Google Secret Manager | Keep (still used for IBKR creds) |
| Key Saxo Bank Symbols | DELETE — replace with "Key IBKR Symbols" |
| VM Details | Keep |
| Important Notes (final block, items 11+) | Most reference HYDRA variants — keep |

**Estimated CLAUDE.md final size**: ~50KB (down from 154KB)

---

## 8. Section G — Top 5 risks

### Risk 1: MEIC method porting depth (CRITICAL)
`_place_option_order()` and `_close_position_with_retry()` are 300–400 LOC each with intricate Saxo coupling: BuySell enum, OrderType enum, activity-stream fill detection, 5-level retry sequence, ORDER-006 bid-ask spread checks, ORDER-007 slippage monitoring. Porting carelessly = missed retry levels, naked positions, wrong fill price detection.

**Mitigation**:
1. BEFORE touching HYDRA, extend `IBClient` with high-level helpers: `place_option_with_retry(conid, side, price, timeout, ...)`, `close_position_with_retry(conid, ...)`. Unit-test these against IBClient mocks.
2. Port MEIC methods into HYDRA one at a time, with regression tests for each.
3. Dry-run on VM after each port; compare to a "before" Saxo dry-run snapshot for sanity (entries fire at same times, strikes within tolerance, etc.).

### Risk 2: Position registry mismatch (HIGH)
HYDRA's `PositionRegistry` (and reconciliation logic in `_reconcile_positions`) expects Saxo's nested position dict shape (`PositionBase.Uic`, `PositionBase.OptionsData.Strike`). IBKR returns flat dicts (`conid`, `position`, `avgCost`). Wrong adapter logic = stale registry = orphan positions on VM.

**Mitigation**:
1. Add `IBClient._normalize_position_dict()` returning shape PositionRegistry expects.
2. Rewrite reconciliation logic in HYDRA with IBKR-flat-dict access patterns.
3. Test reconciliation against a hand-crafted mixed scenario (some positions in registry, some only on broker, dry-run reconcile expectations).

### Risk 3: Order response shape (HIGH)
Saxo `place_order` returns `{"OrderId": ...}`, then `check_order_filled_by_activity(order_id, uic)` returns fill details. IBKR `place_order` returns full status object directly. Strategy code at MEIC `_place_option_order` line ~3040-3078 expects Saxo shape — must rewrite or wrap.

**Mitigation**: Build a single `IBClient.place_and_wait_for_fill(...)` helper that returns a normalized dict matching what HYDRA's downstream code expects. Bury the Saxo→IB difference inside this helper.

### Risk 4: Streaming model difference (MEDIUM)
Saxo streams via bulk subscription (`start_price_streaming(list_of_uics, callback)`); IBKR streams per-instrument via `ib_streaming.StreamingManager`. HYDRA's WS price cache and health checks need updating.

**Mitigation**: IBClient.streaming sub-namespace is already shipped; rewrite HYDRA's streaming integration to use it. Already proven working in Phase A.10 smoke tests.

### Risk 5: Backtesting DB schema (LOW-MEDIUM)
HYDRA's `data_recorder.py` writes to `data/backtesting.db` with assumptions about field availability. Some fields are Saxo-specific (bid_ask_width from Saxo's reply prompt schema). IBKR may not have direct equivalents.

**Mitigation**: Audit `data_recorder.py` schema (schema v7 currently), keep most fields broker-agnostic, mark Saxo-only fields nullable, set them NULL on IBKR.

---

## 9. Section H — Commit sequence (Phase NEW-2)

All on branch `hydra-ibkr-standalone`. Each commit must leave repo tests passing (no skipped tests other than the integration smoke, which is environmental).

### Pre-flight (still on `main`)
0. **Create branch**: `git checkout -b hydra-ibkr-standalone`
0a. **Snapshot reference**: capture a current Saxo HYDRA dry-run log for 1 trading day → save under `docs/migration/saxo_hydra_reference_run.log` so we can compare entry timing/strikes during validation.

### Commits 1–4: IBClient extensions (foundation, no HYDRA code touched)
1. **Add IBClient helpers used by HYDRA**:
   - `place_and_wait_for_fill(conid, side, order_type, limit_price, timeout, ...) → normalized dict`
   - `place_option_with_retry(conid, ...) → normalized dict` (5-level retry sequence ported from MEIC)
   - `close_position_with_retry(conid, ...) → normalized dict`
   - `_normalize_position_dict(ib_position) → flat dict`
   - `get_closed_positions_report(from_date, to_date) → list[dict]`
   - All with unit tests against IBClient mocks; aim for 95%+ coverage on these helpers.

### Commits 5–11: HYDRA rewrite
2. **HYDRA `__init__` accepts IBClient** (still inherits from MEIC, but adds IBClient as alt path).
3. **Port MEIC's `_place_option_order` into HydraStrategy** (override, calls IBClient.place_option_with_retry). Tests pass.
4. **Port MEIC's `_close_position_with_retry`** into HydraStrategy. Tests pass.
5. **Port MEIC's `_execute_entry`, `_execute_stop_loss`, `_check_stop_losses`** into HydraStrategy. Tests pass.
6. **Port remaining MEIC methods** HYDRA depends on (state machine, logging, reconciliation, settlement). Tests pass.
7. **Rewrite HYDRA's direct `self.client.X` (28 Saxo read sites)** to use IBClient methods. Tests pass.
8. **Remove `class HydraStrategy(MEICStrategy)` inheritance** — replace with `class HydraStrategy:`. Tests pass.

### Commits 12–14: Deletion + cleanup
9. **Delete `bots/meic/`** entirely.
10. **Delete `bots/iron_fly_0dte/`, `bots/delta_neutral/`, `bots/rolling_put_diagonal/`** entirely.
11. **Delete `shared/saxo_client.py`, `shared/token_coordinator.py`, `shared/external_price_feed.py`, `shared/broker/`** entirely. Update any surviving imports.
12. **Delete tests** (`test_saxo_broker_adapter.py`, `test_broker_*.py`, `test_ibkr_broker_adapter.py`). Rewrite remaining HYDRA-affected tests (`test_brandon_*.py`, `test_daily_summary_v127.py`).
13. **Delete services**: `services/token_keeper/`, related deploy files. Document VM-side `systemctl stop/disable/rm` actions.

### Commits 15–17: Docs + final validation
14. **Move historical docs** to `docs/migration/`. **Rewrite kept docs** (`HYDRA_STRATEGY_SPECIFICATION.md`, `DEPLOYMENT.md`, `VM_COMMANDS.md`).
15. **Major CLAUDE.md rewrite** per Section F.
16. **Doc-string + comment sweep** on surviving `.py` files — every reference to Saxo, MEIC, "5 bots" etc. removed or rewritten.
17. **Final validation**: 1 trading day VM dry-run with new HYDRA against IB paper. Compare entries/strikes to the reference Saxo run captured pre-flight. Investigate any deviation.

### Merge gate
- All tests passing locally
- 1 day clean VM dry-run on `hydra-ibkr-standalone`
- Diff vs reference Saxo run shows expected differences only (broker timing, slippage), no structural divergence
- User approval to merge

### Post-merge
- VM redeploy from `main`
- VM systemctl cleanup (remove dead bot units)
- 1 trading day post-merge VM dry-run validation

---

## 10. Section I — Open questions for user approval gate

Before any code changes on the branch:

1. **Confirm the 4 deletion verdicts:**
   - `bots/meic/`, `bots/iron_fly_0dte/`, `bots/delta_neutral/`, `bots/rolling_put_diagonal/` — all deleted ✅?
   - `shared/saxo_client.py`, `shared/token_coordinator.py`, `shared/external_price_feed.py` — all deleted ✅?
   - `shared/broker/` (entire dir) — deleted? Or keep as a thin layer for future-proofing test mocks ✅?
   - `services/token_keeper/` — deleted ✅?

2. **Move-vs-delete for historical docs**: My recommendation is MOVE Saxo/dead-bot docs to `docs/migration/` rather than `rm` so the history survives. Alternative: delete entirely. **Move = recommended.**

3. **`market_status_monitor.py`**: Was used by Delta Neutral. Need to verify HYDRA usage. Delete if unused?

4. **`data_recorder.py` schema**: Saxo-only fields (e.g. binary WS bid_ask_width) — null out on IBKR, or drop columns entirely (schema v8 migration)?

5. **Branch name**: `hydra-ibkr-standalone` OK?

6. **VM behavior during rewrite**: Keep Saxo HYDRA running on main during the rewrite (continues dry-run data collection)? Or stop it during the rewrite period?

7. **Reference Saxo log capture**: When do you want me to capture the 1-day reference run? Today/tomorrow during market hours?

---

## 11. Self-audit corrections (2026-05-19 evening)

After writing this plan, I audited it against the actual code + verified ibind's internals. 8 corrections:

### 11.1 — Tighten MEIC method list via call-chain audit ✅ DONE 2026-05-19

Phase NEW-2 commit 1 completed the call-chain audit. See [HYDRA_MEIC_CALL_CHAIN_AUDIT.md](HYDRA_MEIC_CALL_CHAIN_AUDIT.md).

**Result**: HYDRA reaches **101 of MEIC's 160 methods** (not 35 as I'd guessed, not 15-25 as I'd revised). 4,473 LOC of MEIC code must be ported into the standalone HydraStrategy class.

**Scope impact**:
- HYDRA strategy.py grows: 11,143 → ~15,500 LOC
- Repo net deletion still substantial: ~38K LOC deleted, ~4,500 LOC added → ~33K LOC net shrinkage
- Days estimate revised: **9 → 12-15 working days realistic**

The 14 explicit `super()` delegations are the strict surface contract (Section A of audit). The other 87 methods are pure inherited dependencies — silently broken if not ported.

### 11.2 — State file schema migration (was missed)
HYDRA's `data/hydra_state.json` stores `uic` fields. The Dashboard backend reads these. After cutover they'd be IBKR `conid` integers — same data, semantically different name. **Decision: rename `uic` → `instrument_id` everywhere** (state file, dashboard, logs). Adds:
- Phase NEW-2 commit N+1: state schema migration (one-shot script + read/write code updates)
- Phase NEW-2 commit N+2: dashboard backend field rename

### 11.3 — Brandon variant's BuySell import (was missed)
`bots/hydra/brandon/strategy.py:762` lazily imports `BuySell` from `shared.saxo_client`. When we delete saxo_client, Brandon breaks. **Fix**: when porting `_place_option_order` (commit ~7), simultaneously update Brandon to use IBClient's string `side="BUY"`/`"SELL"` directly. No enum dependency after.

### 11.4 — Reorder commits: reads BEFORE MEIC writes port
Original sequence had reads at step 7, after MEIC method porting. But HYDRA's 28 direct read calls are independent of MEIC inheritance — they can be rewritten earlier.

### 11.9 — COURSE CORRECTION (2026-05-21): retire "28 isolated read swaps", adopt flow-by-flow

After completing the first 2 read rewrites (chart, quote), it became clear the
"28 isolated read swaps" model in §11.4 was WRONG for the harder reads. The
reads are NOT independent units — they're entangled inside HYDRA strategy
flows. Specifically:

- `get_option_chain` ×3 + chain-coupled `get_quotes_batch` ×3 +
  `get_option_greeks` ×1 form ONE cohesive "credit estimation / strike
  tightening" flow (MKT-020/022/045). They share data and can't be
  swapped one-method-at-a-time without broken intermediate states.
- `get_quotes_batch` ×2 (entry-price-update path) are coupled to the
  state-schema `uic → instrument_id` rename.
- `get_positions` ×8 are coupled to the position-dict-shape change.

**New model**: rewrite HYDRA flow-by-flow, where each flow = its reads +
its logic + its tests, committed as a cohesive unit. This is identical
in substance to "port the 101 MEIC methods by family" — the reads live
INSIDE those method families. The §11.4 "reads before MEIC port" split
was an artificial separation of the same work.

**The five flows**:

| Flow | Contents | Status |
|---|---|---|
| F1 — Trend/chart | `get_chart_data` ×2 + EMA/ATR parsing | ✅ commit 7a |
| F2 — Leg quote checks | `get_quote` ×2 (worthless-leg + salvage) | ✅ commit 7b |
| F3 — Credit estimation / strike tightening | `get_option_chain` ×3 + chain-coupled `get_quotes_batch` ×3 + `get_option_greeks` ×1 + MKT-020/022/045 logic | ✅ complete — F3.1–F3.7 (see `F3_OPTION_CHAIN_DESIGN.md`) |
| F4 — Position reconcile / recovery | `get_positions` ×8 + `_normalize_position_dict` + conid→quantity reconciliation | ✅ complete — F4.1–F4.9 (see `F4_POSITION_FLOW_DESIGN.md`) |
| F5 — Settlement / FX | `get_fx_rate` ×1 + closed-positions (DEF-1/2) | ⏳ in progress — design + F5.1 probe done (see `F5_SETTLEMENT_FX_FLOW_DESIGN.md`) |

> Post-F4 a 5-agent world-class audit ran over all branch commits; the
> CRITICAL (recovery state-machine) + HIGH (chart `period` range)
> findings were fixed (commit `5044651`), and two live-readiness gaps
> were logged in `DEFERRED_WORK.md` (DEF-3/DEF-4).

The commit numbering "7a, 7b, ..." is retired. Subsequent commits are
labelled by flow (F3, F4, F5) and by MEIC-method-family.

### 11.10 — F3 is NOT deferred (decision 2026-05-21)

An earlier draft proposed deferring F3 (credit-estimation flow) because
the IBKR `get_option_chain` rewrite needs a design decision: does
`search_secdef_info_by_conid` return a full strike→conid chain in one
call, or must we call `qualify_contract` per strike (~40 calls, cached)?

**Decision: do NOT defer.** The unknown is resolvable NOW by probing the
live IBKR paper account (already working — 15/15 smoke passed). Process:
1. Run a diagnostic script against IBKR paper that queries `secdef`
   with + without strike filters, captures the response shape + counts +
   timing.
2. Based on the verified behavior, design the `get_option_chain`
   rewrite properly (1-call vs N-call).
3. Implement F3 with the verified design + full tests.

This is the world-class approach — verify against reality, then build.
DEF-3 is therefore NOT created; F3 proceeds in-sequence after the probe.

### 11.5 — LOC estimate corrected
My original "5,000–7,000 LOC rewritten" was wrong. Reality: HYDRA strategy.py GROWS from 11,143 → ~12-14K LOC (gains MEIC methods, loses Saxo workarounds). The repo shrinkage comes ENTIRELY from deletions:
- bots/meic/ (~9K LOC)
- bots/iron_fly_0dte/ (~5K LOC)
- bots/delta_neutral/ (~10.8K LOC)
- bots/rolling_put_diagonal/ (~5.1K LOC)
- shared/saxo_client.py (5,152 LOC)
- shared/token_coordinator.py (349 LOC)
- shared/external_price_feed.py (202 LOC)
- shared/broker/ (~980 LOC)
- Related tests (~1500 LOC)
- **Total deleted: ~38K LOC**
- **Total added/rewritten: ~3K LOC** (HYDRA gains MEIC methods)
- **Net repo shrinkage: ~35K LOC**

### 11.6 — Variant configs A/B/C explicitly in scope
HYDRA has 3 variants (`config_variant_a.json`, `_b.json`, `_c.json`) running as separate systemd services. All need IBKR config blocks. Brandon variants B/C use overrides. Add explicit commit for variant config migration.

### 11.7 — Memory file (`MEMORY.md`) added to doc sweep
The Claude Code auto-memory at `~/.claude/projects/-Users-ddias-Desktop-CALYPSO-Git-Repo/memory/MEMORY.md` references "HYDRA = MEIC + Trend Following hybrid" and other bot-architecture concepts that change after the rewrite. Add to Phase NEW-3c sweep.

### 11.8 — Reference Saxo log comparison schema
The "compare to reference Saxo run" validation was vague. Explicit comparison points:

| Field | Should match | Tolerance |
|---|---|---|
| Entry slot times | Yes | ±2s (broker timing variance) |
| Strike selection (call/put short + long) | Exact | 0 (deterministic math) |
| Skip decisions (whipsaw, MKT-011, conditional) | Exact | 0 (deterministic filters) |
| Stop level calculations | Exact | 0 (deterministic math) |
| Credit per side (expected) | Within 5% | Broker pricing variance |
| Order timing (place to acknowledge) | Within 1s | API latency |

What WON'T match (and shouldn't):
- Order IDs (broker-specific)
- Position IDs (broker-specific)
- Actual fill prices (slippage differs)
- Settlement times (broker EOD timing)

If structural divergence (entries fire at different times, strikes off by 5pt+, skip decisions differ) → STOP and investigate.

---

## 12. Approval gate

Approve this updated plan (with §11 corrections applied)?
- ☐ Yes, proceed to Phase NEW-2 commit 1
- ☐ Modify — propose changes
- ☐ Pause — review later

**Once approved, the implementation contract is fixed.** Changes mid-implementation = scope creep; surface them as a separate decision point, don't quietly absorb.

---

**Last updated**: 2026-05-22 (F1–F7 + P1–P7 code-complete; 6-agent P7 audit found 4 Critical + 13 High + Medium/Low — Criticals + most Highs fixed; see `P7_AUDIT_FINDINGS.md`)
