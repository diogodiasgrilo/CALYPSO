# PR Scoping Document — Leg/LegSet Abstraction + Instrument Parameterization

> Scopes **items 1 & 2** of the [Modularity Audit](MODULARITY_AUDIT.md) roadmap. Produced 2026-06-07 by a 6-agent investigation+design workflow (5 parallel code investigators → staff-engineer design pass), all grounded in re-read file:line.

**Branch base:** `hydra-ibkr-standalone` · **Status:** scoping
**Constraint:** live (paper) trading bot. Correctness > cleverness. **Zero behavior change** in every commit. Small commits, tests-with-code.

> **Audit corrections surfaced during this scoping (re-read against source):**
> - The audit's "**561** leg-name references in `strategy.py`" is **unreproducible**. Real counts: `strategy.py` ~576, `base_strategy.py` ~435 (~1,011 in-file; ~940 tree-wide). Do not anchor on 561.
> - The audit assumed per-leg `*_stop` / `*_expired` fields. **False** — those are *side*-scoped (`call_side_stop`, `put_side_expired`, …) and stay exactly where they are. Only **6 leg-scoped props × 4 legs = 24 fields** move behind the bridge (`strike`, `position_id`, `uic`, `price`, `fill_price`, `mid_at_fill`).

---

## 1. Goal & Non-Goals

### Goals
- **Item 1 — Leg/LegSet abstraction.** Make an entry's four legs first-class objects (`legs["short_call"].strike` etc.) so the entry becomes "a list of legs." `IronCondorEntry` keeps its 4-leg flat attribute surface as a **backward-compat property bridge** over the leg objects. This unblocks strangle / butterfly / ratio / vertical / calendar later *without* changing the entry's public surface now.
- **Item 2 — Instrument parameterization.** Thread `underlying_symbol` / `volatility_symbol` / `strike_increment` / `trading_class` / `exchange` / a DTE knob from config through every call site that currently hardcodes `SPX` / `VIX` / `SPXW` / `CBOE` / 5pt-grid / today-expiry. Add a startup assertion that the data path carries no banned literal.

### Non-Goals (explicit)
- **No new strategy** (no strangle/butterfly placement logic). Item 1 only builds the *substrate*.
- **No behavior change.** Every default equals today's literal; an absent/legacy config produces byte-identical behavior. (`underlying_symbol` default `"SPX"` — `base_strategy.py:992`; `trading_class` default `"SPXW"` — `ib_client.py:1732`; `strike_increment` default `5`; DTE default `0` = today, `base_strategy.py:3109`.)
- **No on-disk state-schema change.** The JSON keys stay flat (`"short_call_strike"`, …) — `strategy.py:9937`, `:11534`. A mid-day restart must reload an *old* file unchanged.
- **No big-bang rename.** The ~1,011 in-file leg-name refs keep working via the bridge. We do **not** touch the SQLite columns (`shared/data_recorder.py`), Sheets columns (`shared/logger_service.py`), HOMER (`services/homer/db_manager.py`), or the dashboard reader (`dashboard/backend/services/live_state.py`) — those are external wire contracts, name-pinned forever.
- **No `__slots__`** on the entry — transient dynamic attrs (`{leg}_bid`/`_ask`, set via `setattr` at `strategy.py:7209-7211`) ride on `__dict__` and must keep doing so.

---

## 2. Design — Item 1 (Leg / LegSet)

### 2.1 The data model

Today every leg-scoped field is a flat `{leg}_{prop}` field on the `@dataclass IronCondorEntry` (`base_strategy.py:298-417`). Six leg-scoped props × 4 legs = 24 fields (verified `:322-373`): `strike`, `position_id`, `uic`, `price`, `fill_price`, `mid_at_fill`. Everything else (`call_spread_credit`, `call_side_stop`, `call_side_stopped`, …) is **side-scoped, not leg-scoped** — it stays exactly where it is.

Proposed mutable `Leg` (mirrors Brandon's `HedgeLeg` discriminators `side`+`contract_type` at `hedge_position.py:22`, but **mutable** — IC legs are re-struck/filled in place):

```python
# bots/hydra/leg.py  (new file)
from dataclasses import dataclass
from typing import Optional

LEG_NAMES = ("short_call", "long_call", "short_put", "long_put")  # canonical order

@dataclass
class Leg:
    """One option leg. side ∈ {short, long}, right ∈ {call, put}."""
    side: str            # "short" | "long"
    right: str           # "call"  | "put"
    strike: float = 0.0
    position_id: Optional[str] = None   # Saxo legacy; None on IBKR
    uic: Optional[int] = None           # = conid on IBKR (the F4 reconcile key)
    price: float = 0.0                  # live monitoring (transient, not persisted)
    fill_price: float = 0.0
    mid_at_fill: float = 0.0            # slippage ref (transient, not persisted)

    @property
    def name(self) -> str:              # "short_call" etc. — the legacy key
        return f"{self.side}_{self.right}"
```

`LegSet` is intentionally thin — a `dict[str, Leg]` keyed by the legacy name so the four name-strings remain valid keys (load-bearing: `_reconcile_positions` iterates `["short_call","long_call","short_put","long_put"]` at `base_strategy.py:4352`):

```python
def _new_legset() -> dict[str, Leg]:
    return {
        "short_call": Leg("short", "call"), "long_call": Leg("long", "call"),
        "short_put":  Leg("short", "put"),  "long_put":  Leg("long", "put"),
    }
```

### 2.2 The backward-compat property bridge (the heart of the refactor)

`IronCondorEntry` gains a `legs` field and **24 paired `@property`/`@setter`** delegating to it. The 24 flat fields are *removed as dataclass fields* and *re-added as properties* — so `entry.short_call_strike` (read), `entry.short_call_strike = X` (write), and `getattr/setattr(entry, "short_call_strike")` (dynamic) all resolve transparently.

Generate the 24 with a class-decorator loop to avoid 48 hand-written stanzas:

```python
def _bind_leg_bridge(cls):
    for leg in LEG_NAMES:
        for prop in ("strike", "position_id", "uic", "price", "fill_price", "mid_at_fill"):
            def _get(self, _l=leg, _p=prop): return getattr(self.legs[_l], _p)
            def _set(self, v, _l=leg, _p=prop): setattr(self.legs[_l], _p, v)
            setattr(cls, f"{leg}_{prop}", property(_get, _set))
    return cls

@_bind_leg_bridge
@dataclass
class IronCondorEntry:
    entry_number: int
    entry_time: Optional[datetime] = None
    legs: dict[str, Leg] = field(default_factory=_new_legset)
    # ... all SIDE-scoped + entry-scoped fields stay EXACTLY as today ...
    # short_call_strike, ... (24 flat leg fields) are now PROPERTIES via the decorator
```

**Why the setter is non-negotiable (verified write sites):** A getter-only `@property` raises `AttributeError: can't set attribute` at the live position-clearing paths `base_strategy.py:4361-4363` (`setattr(entry, f"{leg_name}_uic", None)`), `strategy.py:11232-11233`, `:7209-7211`, `:2636-2648`, and the strike writer `strategy.py:956-961`. Failure there = a vanished leg never cleared = phantom monitoring or naked short. **Both** read and write must bridge.

**MagicMock guard (verified):** Brandon's tests build entries as `MagicMock()` (`test_brandon_strategy_integration.py:530`), but production Brandon entries are real `IronCondorEntry`s. A properties-on-class approach is transparent to MagicMock. Pinned with G6.

### 2.3 Serialization stays old-state-file-compatible (verified)

**Untouched.** The writer (`strategy.py:9937-9950`, `:9993-9996`) reads `entry.short_call_strike` → now hits the property → returns `legs["short_call"].strike`. Same JSON keys, same values. The reader (`:11534-11537`, `:11594-11597`, `:11612-11619`) assigns `restored_entry.short_call_uic = entry_data.get("short_call_uic")` → now hits the setter → writes into the leg. Because the reader already uses `dict.get(key, default)`, an **old flat-key file loads into the new model with zero reader changes**. The date gate (`strategy.py:11421-11423`) means only *same-day* files matter — exactly the mid-day-restart "must not drop positions" case. **No on-disk schema change, no `state_version`.**

### 2.4 What the bridge shields vs. what must be rewritten

| Bridge-shielded (no change in Phase 0) | Must be rewritten later (bridge can't save them) |
|---|---|
| All ~330 read-only logging/Sheets/DB-dict reads | String-literal leg vocab in `_execute_entry` (`base_strategy.py:2143`): `filled_legs.append(("long_call", …))` `:2229`, `"long_" + leg_name[6:]` `:2328`, `leg_name.startswith("short_")` `:2326` |
| ~30 strike writes (`strategy.py:956-961`, snap `:3956-3991`, tighten `:4012/4259/5581-5582`) | `for leg_name in [...]` loops in `_reconcile_positions` (`base_strategy.py:4352`), `_handle_position_discrepancies` (`strategy.py:10280`), mid-day reconcile (`:11230`) — these *work as-is* through the bridge; rewrite is optional cleanup |
| ~50 dynamic `getattr/setattr(entry, f"{leg}_…")` sites | (none need rewriting — they resolve through the bridge by name) |
| Derived props `total_credit`/`spread_width`/`*_spread_value`/`all_position_ids`/`unrealized_pnl` (`base_strategy.py:419-532`), Hydra `total_credit`/`is_one_sided` (`strategy.py:203-215`) | — |
| Brandon strike adjuster `_brandon_apply_strike_adjuster` (`brandon/strategy.py:272-275`) — bridged automatically | — |

**Key insight:** the dynamic `getattr(entry, f"{leg}_uic")` sites are *helped* by the bridge, not broken — a property named `short_call_uic` resolves under `getattr` identically to a field. So **Phase 0 alone (bridge + leg model) makes the entire codebase compile and behave identically.** The string-literal vocabulary in `_execute_entry` is the only thing the bridge can't cover, and it doesn't need touching in this PR (it keeps using literal leg names that are still valid keys).

---

## 3. Design — Item 2 (Instrument Parameterization)

### 3.1 Config keys to add (defaults = today's literals)

In `config.json` `strategy` block (and `config.json.template`). **`underlying_symbol` already exists but is DEAD** — read at `base_strategy.py:992`, consumed only by the startup log line `:1145`. This PR gives it teeth.

| Key | Default | Replaces |
|---|---|---|
| `underlying_symbol` | `"SPX"` (exists, dead) | the 6 active `"SPX"` literals (§3.2a) |
| `volatility_symbol` | `"VIX"` | `"VIX"` literals (§3.2b) |
| `trading_class` | `"SPXW"` | the 5 `ib_client.py` defaults (§3.2c) |
| `exchange` | `"CBOE"` | the 6 `ib_client.py` literals (§3.2d) |
| `strike_increment` | `5` | the 13 `round(x/5)*5` grid sites + Brandon constant (§3.2e) |
| `target_dte` | `0` | `_get_todays_expiry` (§3.2f) |

### 3.2 Literal → config replacements (grouped by file)

**(a) `"SPX"` trading symbol** → `self.underlying_symbol`:
`strategy.py:1537` (`get_chart_data`), `:3774` (`get_option_chain`), `:3803` (`qualify_option_strikes` — the hot path; every entry leg conid), `:10752` (halt check); `base_strategy.py:4158` (`_update_market_data` SPX read), `:5641` (settlement SPX read).

**(b) `"VIX"`** → `self.volatility_symbol`:
`base_strategy.py:4171` (`_read_index_price("VIX")`). Plus `ib_client.py:2200` (`get_vix_price` literal) — parametrize the signature `get_vix_price(self, symbol="VIX")` so the strategy passes `volatility_symbol`. (`_read_index_price` is already symbol-agnostic — `strategy.py:1980` — only the call-site arg is hardcoded.)

**(c) `trading_class="SPXW"` defaults** → thread param:
`ib_client.py:1472` (`qualify_contract`), `:1732` (`qualify_option_strikes` — live scan relies on this default since caller `strategy.py:3802` passes none), `:2616` (`get_option_chain`), `:2840`/`:2938` (combo paths). The live caller at `strategy.py:3802-3806` must start passing `trading_class=self.trading_class`.

**(d) `"CBOE"`** → thread `exchange` param (default `"CBOE"`):
`ib_client.py:1551`, `:1586`, `:1597`, `:1643`, `:1826`, `:2636`. (Exclude FX `exchangerate` fields at `:2294-2446`.)

**(e) 5pt grid** → `self.strike_increment` (helper `_snap(x) = round(x/inc)*inc`):
`strategy.py:880` (MKT-027 width), `:924/931/939/941` (strike calc), `:4043/4066/4291/4314` (MKT-020/022 tighten + 5pt step), `:4710-4711` (shadow), `:5581-5582` (MKT-040 retry). Brandon: replace the module constant `gex_strike_adjuster.py:28` (`SPX_STRIKE_INCREMENT = 5.0`, used at `:76`) with a constructor arg fed from config. **Secondary (flag, do not change here):** snap tolerances `strategy.py:3792` (`<=25`), `:3829`, `:3884` are 25pt-spacing windows, not the increment.

**(f) today-expiry** → parametrize the single chokepoint `_get_todays_expiry` (`base_strategy.py:3109-3111`); all 11 call sites inherit it (`base_strategy.py:1912/1998/2094/2163`, `strategy.py:964/3915/4056/4304/5973/6120`, `brandon/strategy.py:859`):

```python
def _get_todays_expiry(self) -> Optional[str]:
    """Target expiry (0DTE today by default; target_dte shifts forward)."""
    base = get_us_market_time()
    if self.target_dte == 0:
        return base.strftime("%Y-%m-%d")
    return _add_trading_days(base, self.target_dte).strftime("%Y-%m-%d")  # weekend-aware
```
With `target_dte=0` (default) this is byte-identical to today. (Name stays `_get_todays_expiry` to avoid churning 11 call sites — rename is a non-goal.)

### 3.3 The startup assertion (banned-literal guard)

A pure-Python self-check run once in `__init__` *after* config load. It asserts the *resolved instrument fields* are populated (catching a future edit that re-hardcodes):

```python
def _assert_instrument_parameterized(self):
    bad = [n for n, v in {
        "underlying_symbol": self.underlying_symbol,
        "volatility_symbol": self.volatility_symbol,
        "trading_class": self.trading_class,
        "exchange": self.exchange,
    }.items() if not v]
    if bad:
        raise ConfigError(f"instrument params unset: {bad}")
    if not (isinstance(self.strike_increment, (int, float)) and self.strike_increment > 0):
        raise ConfigError(f"strike_increment must be positive, got {self.strike_increment!r}")
    if not (isinstance(self.target_dte, int) and self.target_dte >= 0):
        raise ConfigError(f"target_dte must be >=0, got {self.target_dte!r}")
```

A complementary **CI-only ratchet test** greps the data-path files for banned literals outside default-parameter positions, asserting the count only drops. That is the real "fails if any banned literal remains" guard — in the test suite, not the hot loop.

---

## 4. Phased Commit Plan

Item 1 (commits 1–5) and Item 2 (commits 6–11) are **fully separable**. Each commit is independently green (`python -m pytest tests/ -q`) and ships its tests.

### Item 1 — Leg/LegSet

| # | Commit | Net effect | Tests in same commit |
|---|---|---|---|
| **1** | Add `bots/hydra/leg.py` (`Leg`, `LEG_NAMES`, `_new_legset`) | new file, imported nowhere | unit tests for `Leg` defaults + `.name` |
| **2** | **Safety net FIRST (pure no-op).** Add G1 + G2 regression tests against *today's* code — state round-trip with populated legs + legacy-file load. | no source change; proves the net is green before refactor | G1, G2 (must pass on current code) |
| **3** | **The bridge (pure no-op).** Add `legs` field + `_bind_leg_bridge`; convert the 24 flat fields to properties. `HydraIronCondorEntry` unchanged. | behavior identical; all refs + dynamic sites + serialization resolve through bridge | G3 leg-accessor equivalence; re-run G1/G2; full suite |
| **4** | (optional cleanup) Rewrite `_reconcile_positions` / `_handle_position_discrepancies` loops to iterate `entry.legs.values()`. | internal only; names still valid keys | extend G1; reconcile-path test |
| **5** | (optional cleanup) Give `_execute_entry` an explicit leg-key iterable, replacing `"long_" + leg_name[6:]` slicing. | internal only | `_execute_entry` leg-vocab test |

> Commits 4–5 are *optional* — the bridge alone (commit 3) leaves the codebase fully behavior-preserving and ships the unblock-substrate. Deferrable to a follow-up PR.

### Item 2 — Instrument params

| # | Commit | Net effect | Tests |
|---|---|---|---|
| **6** | Add config keys + `__init__` reads + `_assert_instrument_parameterized`. Defaults = today's literals. | no call-site change yet; assertion passes | assertion test; defaults-equal-literals test |
| **7** | Thread `underlying_symbol` through the 6 `"SPX"` sites (§3.2a). | gives the dead key teeth | G4 strike-calc still SPX; mock-broker call-arg test |
| **8** | Thread `volatility_symbol` (§3.2b) + `get_vix_price(symbol=…)`. | | VIX-arg test |
| **9** | Thread `trading_class` + `exchange` through `ib_client.py` (§3.2c/d) + live caller `strategy.py:3802`. | **P7-audit-sensitive — see §6** | ib_client defaults unchanged; live-caller passes config |
| **10** | Parameterize the 5pt grid via `strike_increment` + `_snap` (§3.2e) incl. Brandon constant. | | G4 `test_strike_increment_comes_from_instrument_param` |
| **11** | Parameterize `_get_todays_expiry` via `target_dte` (§3.2f). | `target_dte=0` byte-identical | expiry=today at dte 0; shifts at dte 1 |

---

## 5. Test Plan

### No-behavior-change regression tests (build G1/G2 FIRST, must pass on today's code)

- **G1 — state round-trip with populated legs (CRITICAL; currently zero coverage).** `tests/test_state_serialization_roundtrip.py`. Build a real `HydraIronCondorEntry` with all 24 leg fields + side flags + credits (reuse the `test_aud5_fixes.py:398` `_entry` pattern), call the real `_save_state_to_disk()` then `_load_state_file_history()`, assert every leg field + side flag survives. The per-entry serialization block (`strategy.py:9930-10040`) is today executed by *zero* tests. Add one-sided + partial-entry variants (pins FIX #77 `strategy.py:11488`).
- **G2 — legacy-file load.** Commit a sample state JSON in *today's* flat-key schema (none exists). Feed it to the real loader. After commit 3, proves the bridge reads old files. Add `test_load_tolerates_missing_new_keys`.
- **G3 — leg-accessor equivalence (the bridge guarantee).** For each of 24 names assert read equivalence, write-through, and dynamic `setattr/getattr` route to the leg. Plus the currently-untested derived props: `unrealized_pnl` branches (`base_strategy.py:459-518`), `total_credit` base vs Hydra override, `spread_width`, `*_spread_value` scaling with `contracts`, `all_position_ids` None-filter, `is_one_sided`.
- **G6 — Brandon MagicMock + real-entry compatibility.** Mutate `short_call_strike` via Brandon's adjuster path and read back. Re-run all `tests/test_brandon_*.py` — `TestSubclassRelationship` pins the override set.

### Instrument-param tests
- **Defaults-equal-literals**, **G4 `_calculate_strikes` real invocation** (currently zero coverage; pins grid + OTM clamp 25–180 + `test_strike_increment_comes_from_instrument_param`), **call-arg threading** (mock broker receives config not literal), **`ib_client` signature defaults unchanged** (P7 guard), **startup assertion**, **`_get_todays_expiry`** dte 0==today / dte 1 shifts.

### Capability tests (substrate works, no new strategy)
- `Leg` round-trips through a `legs` dict; a `LegSet` of 2 (strangle) and 3 (butterfly) constructs without touching `IronCondorEntry` — *construction only, no placement*.

**Baseline today:** 386 tests pass across the 5 core files in ~1.3s.

---

## 6. Risk & Rollback

| Risk | Why | Mitigation |
|---|---|---|
| **State-file recovery drops a position on mid-day restart** (highest) | bridge setter / default-factory mis-wire → loader writes into the void | G1/G2 built **first** against today's code, re-run after commit 3. Reader is *unchanged*. Date gate limits blast radius to same-day. |
| **Getter-only property** breaks live leg-clearing | `setattr(entry, f"{leg}_uic", None)` is a naked-short-prevention path | every bridge prop ships a setter; G3 asserts dynamic `setattr`. |
| **P7-audited `ib_client.py` paths** regress | Item 2 touches `qualify_*` / `get_option_chain` / secdef `exchange` filter (closed P7 C2/H7/L8) | re-read `docs/migration/P7_AUDIT_FINDINGS.md` before commit 9. **Keep all defaults `"SPXW"`/`"CBOE"`.** Only add params; don't touch the secdef filter *logic*, only the literal it compares against. |
| **Brandon subclass** breaks | mutates `*_strike` in place; tests use MagicMock | G6; bridge is properties-on-class (transparent to MagicMock + in-place mutation). Brandon adds zero leg fields. |
| **External wire contracts** drift | SQLite/Sheets/HOMER/dashboard read flat *key strings* | never rename keys; serialization emits identical JSON; no change to those modules. |
| **DTE shift accidentally trades non-0DTE** | `target_dte` mis-set | default `0`; assertion rejects negatives; live config stays `0` (ship the knob, not a config change). |

### Reversibility
Each commit is a clean `git revert`. Commits 1–2 are inert. Commit 3 (the bridge) is the only one touching the entry model — reverting restores the 24 flat fields verbatim. Item 2 commits revert independently (each swaps a literal for `self.<field>` whose default equals the literal). **No on-disk migration** → rollback never strands a state file.

### Watch in paper after deploy
1. First **mid-day restart** after commit 3: confirm `active_entries` reload with non-None `*_uic` (heartbeat `Active=N`, not 0).
2. First **stop / settlement**: confirm close fires on `*_uic` (F4 reconcile) and P&L books.
3. **Strikes still land on the grid**; conids resolve via `SPXW`/`CBOE`.
4. **Variant B/C** place narrow spreads unchanged.

---

## 7. Effort Estimate

| Phase | Commits | Size | Notes |
|---|---|---|---|
| Item 1 — Leg model + safety net + bridge | 1–3 | **M** (~1.5 d) | Commit 2 (G1/G2 against today's code) is the long pole — no fixtures exist; must author a realistic populated-entry state file. Commit 3 is small/mechanical once green. |
| Item 1 — optional internal cleanup | 4–5 | **S** (~0.5 d) | Deferrable; bridge alone ships the unblock. |
| Item 2 — config + assertion | 6 | **S** (~0.5 d) | `__init__` wiring + assertion + defaults test. |
| Item 2 — thread literals | 7–11 | **M** (~1.5 d) | P7 re-read gates commit 9; G4 is new test infra. |
| **Total** | **9–11 commits** | **~4–4.5 d** | ~3 d if commits 4–5 deferred. |

**Recommended slice:** ship commits 1–3 (Item 1 substrate + bridge) and 6–11 (Item 2 full) as one PR (~3.5 d); defer the optional internal-loop cleanup (4–5) to a fast follow-up. Maximum unblock with minimum surface touched on a live trading bot.

---

**Files touched:**
- New: `bots/hydra/leg.py`; `tests/test_state_serialization_roundtrip.py`, `tests/test_entry_leg_accessors.py`, `tests/test_strike_calculation.py` (+ instrument-param tests).
- Modified — Item 1: `bots/hydra/base_strategy.py` (entry dataclass `:298`), `bots/hydra/strategy.py` (subclass `:150`; serialization untouched but exercised).
- Modified — Item 2: `bots/hydra/base_strategy.py` (`:992`, `:3109`, `:4158/4171/5641`), `bots/hydra/strategy.py` (`:1537/3774/3803/10752`, grid sites `:880/924/931/939/941/4043/4066/4291/4314/5581-5582`), `shared/ib_client.py` (`:1472/1551/1586/1597/1643/1726/1826/2200/2616/2636`), `bots/hydra/brandon/gex_strike_adjuster.py:28`, `bots/hydra/config/config.json.template`.
