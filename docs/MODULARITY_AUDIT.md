# CALYPSO Modularity Audit

> **Question audited:** *How modular is the codebase (1–10) for standing up a NEW trading strategy by picking pre-built "lego pieces" off a shelf and wiring them together?*

| | |
|---|---|
| **Date** | 2026-06-07 |
| **Branch** | `hydra-ibkr-standalone` |
| **Method** | 28-agent unbiased workflow audit: 12 modularity dimensions scored independently against a shared rubric → an adversarial skeptic re-read the cited code and adjusted each score → 3 concrete "add a new strategy" ground-truth walkthroughs → synthesis. All claims grounded in file:line. |
| **Scope at audit time** | `strategy.py` 11,816 lines · `base_strategy.py` 6,293 lines · `ib_client.py` 3,719 lines · `logger_service.py` 4,779 lines |

---

## 1. Overall score: **5 / 10**

CALYPSO sits squarely in the "base class + decent shared infra, but significant hardcoding" band — it is a **subclass-and-override codebase, not a pick-pieces-off-a-shelf one**. The single fact that resolves the central tension (an ~18k-line monolith vs. a working Brandon subclass) is this: *`BrandonHydraStrategy` works precisely because it is still a 4-leg SPX 0DTE iron condor.* It proves the inheritance seam is real and usable, but it only ever swaps strike-selection and stop logic on top of the exact same structure, instrument, expiry, and leg-count the base assumes. Every walkthrough that pushes outside that frame (a naked strangle, a different underlying, a multi-day hold) lands at grade 4–5 and requires editing the shared base class in place, not composing components.

Weighting the dimensions that actually gate a new strategy — base-class design (5), broker abstraction (4), config (3), and the three reuse ratios (~65%/~70%/~55%, but with the new code concentrated in high-risk seams inside `strategy.py`/`base_strategy.py`) — the honest number is a **5**. The bottom of the stack (order placement 7, market-data primitives 6, alerting 8) is genuinely strong and keeps it from being a 4; the absence of any registry, any leg abstraction, any config schema, and an instrument knob that is wired only to a log line keeps it well short of 7.

---

## 2. Scorecard (adversarially-adjusted)

| Dimension | Score | One-line verdict |
|---|---|---|
| Strategy base-class & inheritance | **5** | Real 3-level chain and thin Brandon seam, but undeclared template-method hooks, zero `abc`/`abstractmethod`, non-instantiable base, Saxo names in the API. |
| Broker abstraction layer | **4** | The neutral `BrokerInterface` was deleted (P5c); base is nominally typed to concrete `IBClient`, conid woven through 189 sites — welded to IBKR. |
| Strike selection building blocks | **5** | Brandon's pure GEX/delta modules are clean legos, but HYDRA's strike core is inline instance methods on the monolith with duplicated call/put mirrors. |
| Order placement & execution primitives | **7** | `place_and_wait_for_fill`, cOID dedup, normalized fill dicts, 117 tests — genuinely instrument-agnostic; Brandon reuses execution untouched. |
| Stop / exit management | **5** | Brandon's pure exit legos (take-profit, breach, overlay) are excellent; HYDRA's stop dispatch has *no* base abstraction at all and is inline/IC-shaped. |
| Credit/risk gates & position sizing | **5** | Gates are config-overridable and carry forward for IC-family, but bound to the 4-leg dataclass and fused to the entry-index/schedule machinery. |
| Market data / quote / chain helpers | **6** | IBClient primitives are 8-tier clean; the strategy-side `_read_*` adapters are SPX/SPXW-hardcoded and trapped in `strategy.py`, not on the shelf. |
| Config system & per-strategy params | **3** | Flat IC-shaped JSON, no schema/validation, `underlying_symbol` reads only for a log line, cloud config load-coupled to dead Saxo creds. |
| State persistence & recovery | **4** | Field-by-field IC serializer; the one generic asset (`ib_reconcile`) has a sole caller that is itself dead code; `position_registry` is vestigial Saxo weight. |
| Logging / metrics / data recording | **4** | 32 `strategy_type ==` branches across 7 methods; IC-shaped SQLite tables with no `strategy_type` column; silent `delta_neutral` fallback. |
| Alerting | **8** | `AlertService(config, bot_name)` is cleanly generic and reused by 9 services; only ceiling is hardcoded `'HYDRA'` in the alert-hooks bridge + frozen enum. |
| Operational scaffolding | **5** | Variant infra (env isolation, settings-driven router, dashboard auto-discovery) is reusable; DB/HOMER/ARGUS/journal reads are HYDRA/IC-coupled. |

---

## 3. The lego shelf today (the real wins)

**Tier 1 — true off-the-shelf, no strategy coupling:**
- **Order execution primitives.** `place_and_wait_for_fill` (`ib_client.py:3068`) — conid/side/qty/price in, normalized `{order_id,status,filled_quantity,avg_fill_price,raw}` out; cOID dedup auto-generated (`_ensure_coid`, `ib_client.py:2775`); `AmbiguousOrderError` retry-safety; idempotent `cancel_order`. 117 write-path tests.
- **Market-data / contract primitives.** `qualify_contract` / `qualify_option_strikes` (shared conid cache, `ib_client.py:1466`, `:1726`), `get_option_chain`/`get_chart_data` (both parameterized on `symbol`), `_snapshot_with_preflight`, `_parse_quote_row` (crossed-market `mid=None` guard), `get_vix_price`, `get_option_greeks`. 110 read-path tests.
- **Alerting.** `AlertService(config, bot_name)` + single `send_alert()` entry point; generic email/Telegram routing; fully generic Cloud Function renderer. Reused by 9 distinct services.
- **The broker session proxy.** `calypso-broker` + `BrokerClient` `__getattr__` RPC + `ALLOWED_METHODS` allowlist (`broker_service.py:28-41`) — strategy-neutral, 0 changes needed for a new strategy.
- **Brandon's pure decision modules.** `take_profit.py`, `gex_breach_exit.py`, `defensive_overlay.py`, `hedge_position.py`, `gex_strike_adjuster.py`, `narrow_spread.py`, `gex_provider.find_strike_at_delta` — all frozen-dataclass-config, dollar-in/decision-out, zero strategy/broker imports, each with its own isolation test.
- **Stateless utilities.** `technical_indicators.py`, `ib_constants.py`, `ib_retry.py` (RetryPolicy + per-family circuit breakers), `market_hours.py`, `event_calendar.py`.

**Tier 2 — reusable-with-config (within the IC family):**
- The proven **subclass seam** itself: `BrandonHydraStrategy(HydraStrategy)` overriding ~7 methods, calling `super()` for fallback.
- VIX-regime engine, credit gate (MKT-011), buffer/decay/anti-spike stop math, entry scheduler, settlement orchestration — all **value-driven and config-tunable** for another iron condor.
- **Variant isolation infra:** `HYDRA_VARIANT_ID` data-path namespacing, settings-driven variant router (`variants.py`), dashboard auto-discovery, copy-tweak systemd units, env-parameterized `pre_start_snapshot.sh`.
- `ib_reconcile.py` (generic, schema-lenient, injected-callback reconciliation) — reusable in principle (caveat below).

---

## 4. Missing / broken lego (forces rewrites)

**Structural — the 4-leg iron condor is welded into the base class, not just config:**
- `IronCondorEntry` hardwires the 4 named legs as flat `{leg}_{prop}` fields (`base_strategy.py:298-417`); **no list-of-legs abstraction exists**. Leg-name references run ~576 in `strategy.py` + ~435 in `base_strategy.py` (~1,011 in-file; ~940 tree-wide). A strangle/butterfly/ratio/calendar can only be expressed as a degenerate IC. *(Correction, verified during PR scoping: the "561" figure first cited here is unreproducible; the real counts are above. Also, only **6 leg-scoped props × 4 legs = 24 fields** are truly leg-scoped — `strike, position_id, uic, price, fill_price, mid_at_fill`; the `*_stop`/`*_expired` flags are **side**-scoped (`call_side_stop`, …), not per-leg. See [PR_SCOPE_LEG_INSTRUMENT.md](PR_SCOPE_LEG_INSTRUMENT.md).)*
- The base `MEICStrategy` **has no `_calculate_strikes` and no `_check_stop_losses`** — both first appear in `HydraStrategy` (`strategy.py:892`, `:7454`), already coupled to `HydraIronCondorEntry`. There is no neutral strike-selection or exit abstraction to inherit.

**Instrument hardcoding bypasses the config knob:**
- `underlying_symbol` is set at `base_strategy.py:992` but consumed **only for a log line** (`:1145`). The real instrument is the literal `"SPX"` at the call sites: `strategy.py:1537, 3774, 3803, 10752`; `base_strategy.py:4158, 5641, 4171`.
- Even the shared `get_option_chain` defaults `trading_class='SPXW'` (`ib_client.py:2616`) and callers never pass it → a full SPX-0DTE-weekly assumption rides through.
- 0DTE is hardcoded: `_get_todays_expiry` returns today with no DTE parameter (`base_strategy.py:3109`), fanned across ~15 call sites.
- Strike grid hardcoded `/5*5` in `_calculate_strikes` (`strategy.py:924,931,939,941`) and the spread-width formula (`:880`); SPX 5pt grid also re-hardcoded in the "modular" Brandon layer (`gex_strike_adjuster.py:28`).
- For NDX: `exchange="CBOE"` hardcoded at 6 sites inside the P7-audited `ib_client.py` (`1551,1586,1597,1643,1826,2636`).

**Broker coupling:**
- Constructor annotated `broker: IBClient` (concrete, `base_strategy.py:921`) — a second broker fails the nominal type unless it subclasses `IBClient`. Residual `saxo_client=self.broker` on the live stop path (`base_strategy.py:4453`). Saxo names in the base API: `_recover_positions_from_saxo()` (`:1138`), `_get_total_saxo_pnl()` (`:4483,:4532`).

**No registry / no contract / no types:**
- Strategy selection is a hardcoded `if brandon.enabled / else` (`main.py:303-308`). Zero `abc.ABC`/`abstractmethod`/`NotImplementedError` in any of the 3 strategy files. No `TypedDict`/dataclass for fill/execution results anywhere — untyped dicts at every layer.

**Config & persistence:**
- No schema/validation (no pydantic/jsonschema anywhere in the config path). Cloud config load is **coupled to dead Saxo creds** — `_load_cloud_config()` calls `get_saxo_credentials()` and raises `ValueError` if absent (`config_loader.py:149-151`), reached on the GCP VM via `main.py:181`.
- State serializer is field-by-field IC (`strategy.py:9851-10040`). `ib_reconcile`'s only consumer, `IBClient.reconcile_orders` (`ib_client.py:3674`), **has zero callers** — reusable in principle but never exercised. `position_registry.py` is vestigial Saxo-shaped dead weight.

**Logging/DB:**
- `DataRecorder` tables hardwired to 4-leg IC with **no `strategy_type` column** (`data_recorder.py:278-289`). 32 `strategy_type ==` branches across 7 setup+write methods in the 4,779-line `logger_service.py`. Unrecognized `strategy_type` silently falls through to `delta_neutral` column shapes.

**Safety machinery hostile to non-IC:**
- `_handle_naked_short` (`base_strategy.py:3113`) fires CRITICAL on every unhedged short — by definition every leg of a strangle. `_reset_for_new_day` HALTS on any overnight position (`strategy.py:10463-10489`) — fires every night for a multi-day hold. Settlement conflates "position gone from broker" with "expired worthless today" (`_side_positions_gone:10707` + `_process_expired_credits:10811`).

---

## 5. What it takes to add a strategy today

| New strategy | Reuse ratio | Grade | Biggest obstacle |
|---|---|---|---|
| Short strangle (subclass) | ~65–70% | 5 | Naked-short safety machinery fires CRITICAL on every entry; defined-risk margin gate understates strangle margin by ~10×; must zero-out longs in the IC dataclass. |
| IC on NDX/RUT (new underlying) | ~60–80% | 5 | ~10 hardcoded `"SPX"`/`"SPXW"`/`"VIX"` literals bypassing `underlying_symbol`; 5pt strike grid; for NDX, exchange surgery in the P7-audited `ib_client.py`. |
| 30–45 DTE swing put-credit-spread | ~55–65% | 4 | Whole temporal model welded to 0DTE: overnight-halt reset, always-today expiry, same-day settlement, intraday-only schedule, one-row-per-day DB — all edited *in place* in the base. |

**Pattern across all three:** the *bottom* of the stack (broker, order, market-data, retry, alerting, sessions) reuses cleanly — that's where the high reuse % comes from. But the new code, though small in line count, is **concentrated in high-risk seams inside the two monoliths** and frequently requires editing the shared base class rather than passing a value to an extension point. You never "compose legos"; you subclass the monolith and parameterize hardcoded literals. The reuse ratios are flattered by the fact that infra dominates the ~24k lines and you touch almost none of it — but the *strategy-defining* logic is mostly rewrite.

---

## 6. Extraction roadmap to reach 8+/10

Ordered by leverage (highest first). Each item is independently shippable. **None require a rewrite — they are extractions of logic that already works, into shapes that can be picked off a shelf.**

1. **Introduce a `Leg`/`LegSet` abstraction and make the entry a list of legs.** *(highest leverage — unblocks strangle, butterfly, ratio, vertical, calendar.)* Add `Leg(side, right, strike, position_id, uic, price, fill_price, mid_at_fill)` and have `IronCondorEntry` become a thin convenience view over a `legs` set, with the ~1,011 in-file leg-name references shielded by a backward-compat `@property` bridge (24 fields). This is the single change that converts "degenerate-IC workarounds" into real structures. Until this lands, the ceiling is ~6. **→ Fully scoped, commit-by-commit, in [PR_SCOPE_LEG_INSTRUMENT.md](PR_SCOPE_LEG_INSTRUMENT.md).**
2. **Parameterize the instrument completely.** Thread `self.underlying_symbol`, a new `volatility_symbol`, `strike_increment`, `trading_class`, and `exchange` from config through every call site. Replace the literals at `strategy.py:1537,3774,3803,10752` and `base_strategy.py:4158,5641,4171`; add `exchange` as a param to `qualify_contract`/`qualify_option_strikes`/`get_option_chain` (replacing the 6 hardcoded `"CBOE"`); replace `/5*5` rounding with `strike_increment`. Add a startup assertion that fails if any `"SPX"` literal remains in the data path. **→ Fully scoped in [PR_SCOPE_LEG_INSTRUMENT.md](PR_SCOPE_LEG_INSTRUMENT.md).**
3. **Pull strike selection, stop logic, and market-data adapters out of `strategy.py` into shelf modules.** Promote the 3 thin `_read_*` chain/bar adapters into a `shared/market_data_adapter.py`. Extract a `StrikeSelector` protocol (free function over a chain + config — the Brandon GEX modules already prove this shape). Extract an `ExitRule`/`StopPolicy` protocol from `_check_stop_losses` so exit logic is iterable/composable rather than a 160-line inline monolith.
4. **Add a strategy registry + abstract interface.** Replace the `main.py:303-308` if/else with a registry (`@register_strategy("strangle")` or entry-points). Define `abc.ABC` `Strategy` with declared `abstractmethod` hooks for the template-method seam the base already calls (`_initiate_entry`, `_check_stop_losses`, `_calculate_strikes`).
5. **Decouple the temporal model from 0DTE.** Replace `_get_todays_expiry` with a DTE-aware `_get_target_expiry`; separate "new calendar day" from "position lifecycle" in `_reset_for_new_day` so overnight positions are reconciled-and-kept, not treated as a CRITICAL fault; gate settlement on `today == entry.expiry` rather than "positions gone."
6. **Make safety policies pluggable, not hardcoded-hostile.** Gate `_handle_naked_short` on a `requires_protective_wings` strategy flag; wire `what_if_order` (already exists at `ib_client.py:3395`, currently dead) into `_check_buying_power` for broker-authoritative margin instead of the defined-risk `min_buying_power_per_ic` floor.
7. **Config schema + decouple config from credentials.** Add a typed schema (pydantic) over the ~80 strategy keys with validation and defaults in one place; split `_load_cloud_config()` so strategy-config loading no longer depends on `get_saxo_credentials()` (`config_loader.py:149`).
8. **Add a `strategy_type`/`schema_type` dimension to persistence + logging.** Add a `strategy_type` column to `DataRecorder` tables and a per-schema column registry to replace the 32-branch `strategy_type ==` ladder in `logger_service.py`. Add typed `FillResult`/`ExecutionResult` dataclasses to replace the untyped dicts.
9. **Finish the broker neutrality the deletion of `shared/broker/` undid.** Re-introduce a `BrokerProtocol` (`typing.Protocol`) covering the ~15 methods the strategy calls, annotate the constructor to it instead of concrete `IBClient`, and rename the residual `_recover_positions_from_saxo`/`_get_total_saxo_pnl`/`saxo_client=` identifiers.

**Net:** items 1–4 alone move the codebase from "subclass-and-edit" (5) to "compose-with-thin-glue" (7); items 5–9 close the temporal, safety, config, and broker-neutrality gaps that take it to a genuine 8+.

---

## 7. Completeness sweep (2026-06-08) — status of items 1 & 2 + 3 newly-found gaps

After Items 1 & 2 shipped (PR #1), a 6-agent adversarial completeness sweep verified they are byte-identical for SPX/0DTE and found **6 in-scope residual misses** (all now **CLOSED** on the branch — commits 12 & 13): the Brandon `find_strike_at_delta` 8δ picker (was hard-snapped to a 5pt grid), the MKT-007/008 illiquidity step, the MKT-012/013/015 overlap shifts, `get_vix_price`'s hardcoded `"VIX"`, `_read_index_price` not threading `exchange`, and the dead `LEG_NAMES` constant (now the leg-order source of truth).

The sweep also surfaced **3 gaps not captured by items 1–9** — added to the roadmap:

10. **Parameterize the option *price tick* (N1).** `place_order`/`place_and_wait_for_fill` default `price_increment=_SPX_TIERED_TICK` (the CBOE SPX $0.05/$0.10 rule) and the entry caller passes no override (`strategy.py` entry path; `ib_client.py:551`). This is a *separate* axis from `strike_increment` — NDX/RUT use different price-tick rules → `PriceNotInTickSizeIncrements` rejections. Add a `price_tick` field to `_load_instrument_params` and thread it. *(Natural extension of Item 2.)*
11. **Replace the SPX-scaled point-distance constants (N2).** `_calculate_strikes` uses absolute point distances (`base_distance_at_vix15 = 40`, clamps `25..120` / `25..180`) calibrated to SPX's ~5–6k level; on NDX (~20k) they collapse to ATM. Needs a level-relative (% of spot) or config-driven distance model. *(Pairs with Item 3's `StrikeSelector` extraction.)*
12. **Allowlist `what_if_order` on the broker (N3).** Item 6 proposes wiring `what_if_order` into `_check_buying_power`, but in deployed broker mode `BrokerClient.__getattr__` raises for non-allowlisted names — Item 6 must also add `what_if_order` to `ALLOWED_METHODS` + the dispatcher (`shared/broker_service.py`). *(Scope note on Item 6.)*

Also deferred (noted in the version history): `_get_todays_expiry` is weekday-aware only — an exchange-holiday calendar is needed before any real multi-day strategy (pairs with Item 5).

---

## Relevant files

- `bots/hydra/strategy.py` (11,816 lines) — the monolith; strike calc, stops, serialization, settlement
- `bots/hydra/base_strategy.py` (6,293 lines) — `MEICStrategy` base; entry dataclass, recovery, safety machinery
- `bots/hydra/brandon/strategy.py` + `bots/hydra/brandon/` — the pure lego modules (the proof-of-concept for the target architecture)
- `bots/hydra/main.py` — strategy selection (the hardcoded if/else)
- `shared/ib_client.py` — broker primitives (strong) + hardcoded exchange literals
- `shared/config_loader.py` — config load (no schema; Saxo-cred coupling)
- `shared/logger_service.py` / `shared/data_recorder.py` — IC-shaped logging/recording
- `shared/alert_service.py` — the cleanest generic service
