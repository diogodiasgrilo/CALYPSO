# HYDRA Variant Testing — Design Plan

**Status:** Design / scoping document. Not yet implemented.
**Author:** Diogo Dias Grilo
**Created:** 2026-04-28
**Prerequisites:** Single-variant dry mode validated end-to-end (Tuesday Apr 28 smoke test)
**Estimated effort:** 5-7 days for full system, 2-3 days for proof-of-concept

---

## The Vision

Run **N HYDRA strategy variants in parallel** during a single trading day, all in dry-run mode (no real money), all observing the same market data, but each with **different config values** so we can compare strategies side-by-side on identical conditions.

Example variant lineup:

| ID | Name | Config delta from baseline | Hypothesis |
|---|---|---|---|
| **A** | Baseline | None — current live config | Control |
| **B** | Tight Tammy | `max_spread_width: 50`, MKT-024 mults `1.0/1.5` | Tammy spec, properly tuned |
| **C** | Wider call buffers | `call_stop_buffer: 1.50` globally (no VIX adapt) | Test asymmetric protection floor |
| **D** | Aggressive entries | `min_credit` floors lowered ~50% | Volume vs quality |
| **E** | EMA-driven | `trend_filter` upgraded to drive entry-side | Activate dormant feature |

A new dashboard at e.g. `/variants` shows 5 panels side-by-side: live entries, current P&L, stops fired, trend signal, statistics. End-of-day comparison: which variant won today, by how much, and why.

---

## The Refined Architecture (User's Insight)

**Original sketch (multi-process)** spun up 5 separate processes each running a full HYDRA. That works but multiplies Saxo API load 5×.

**User's refinement:** at ENTRY TIMES (10:45, 11:15, 14:00 ET), all variants try to place an entry simultaneously. They share the SAME option-chain + quote data fetched once at the start of the entry window. Only the strategy logic (which strikes / which side / what credit threshold) varies per variant.

This insight is **the unlock**. Sharing entry-time API calls reduces the API load from 5× to ~1×. That changes which architecture is best.

---

## Architecture Comparison (Updated)

### Option 1 — Multi-process, no sharing

Each variant runs as its own systemd service with its own Saxo client.

| Metric | Cost |
|---|---|
| Saxo API calls (heartbeat) | **5×** (each variant fetches own quotes) |
| Saxo API calls (entry time) | **5×** (each variant fetches own chain + quotes) |
| Memory / CPU | 5× idle, ~5× active |
| Process isolation | ★★★★★ (one variant crash doesn't affect others) |
| Refactor cost | Low (HYDRA already designed as single-process bot) |

**Risk: rate limits.** Saxo per-token rate limits aren't perfectly documented. 5× heartbeat load (currently ~2,340 quote calls/day → 11,700 across 5 variants) might trip throttling.

### Option 2 — Multi-process, shared quote cache

Each variant runs as its own service, but a separate `quote_cache_daemon` fetches quotes once and serves all variants.

| Metric | Cost |
|---|---|
| Saxo API calls (heartbeat) | ~1× (cache deduplicates UIC quote calls) |
| Saxo API calls (entry time) | ~1× (cache also serves chain data) |
| Memory / CPU | 5× per variant + 1 daemon |
| Process isolation | ★★★★ (cache daemon is shared dependency) |
| Refactor cost | Medium (~50-80 LOC for daemon + cache client) |

### Option 3 — Single process, multi-variant orchestrator (RECOMMENDED)

One process hosts a `VariantOrchestrator` that owns:
- Shared `SaxoClient` with built-in quote cache
- `N` `StrategyInstance` objects (one per variant)
- Single heartbeat scheduler that ticks all variants

| Metric | Cost |
|---|---|
| Saxo API calls (heartbeat) | **1×** (orchestrator collects all UICs from all variants, makes one batch call) |
| Saxo API calls (entry time) | **1×** (one chain fetch + one batch quote call shared by all variants) |
| Memory / CPU | ~1.5× one-bot (variant logic is small; chain/quote data is the heavy part) |
| Process isolation | ★★★ (one process — bug in variant code can affect others) |
| Refactor cost | Higher (need to extract `StrategyInstance` from current monolithic class) |

**Recommended.** The user's entry-time sharing insight is naturally implemented this way. API load is identical to a single live bot. Resource usage is barely higher than running one HYDRA. Trade-off: more upfront refactor for cleaner long-term architecture.

---

## API Load Math (Why Option 3 Has Zero Rate-Limit Risk)

Current single-bot HYDRA, real numbers per trading day:

| Operation | Count/day | Rate |
|---|---|---|
| Option chain fetch (entry times) | 3 | 1 per entry time |
| Strike-quote batch (entry times) | 3 | 1 per entry time |
| Heartbeat batch quote | ~2,340 | 1 every ~10s × 6.5h |
| Position lookup (reconciliation) | ~12 | 1 every ~30min |

**Total: ~2,358 API calls/day.** Saxo's per-token rate limit comfortably absorbs this — we've been running at this rate for weeks.

With Option 3 + 5 variants:

| Operation | Count/day with Option 3 |
|---|---|
| Option chain fetch (entry times) | 3 (shared) |
| Strike-quote batch (entry times) | 3 (shared, but ~5× the UIC count per call — still one call) |
| Heartbeat batch quote | ~2,340 (shared, batched UICs from all variants) |
| Position lookup (reconciliation) | 0 in dry mode — already gated yesterday |

**Total: ~2,346 API calls/day.** Identical to single-bot. Zero rate-limit risk.

The "magic" is two existing facts:
1. **Saxo's batch quote endpoint** accepts up to ~50-100 UICs per call. 5 variants × 4 legs = 20 UICs ≪ batch limit.
2. **Quote caching** at the orchestrator means duplicate UIC requests across variants collapse to one API call.

---

## What Needs to Be Built (Option 3 Scope)

### Phase 1 — `StrategyInstance` extraction (~1.5 days)

Refactor `HydraStrategy` so it can be instantiated multiple times in one process:
- Pure constructor (no global state references)
- Configurable state file path / DB path / metrics path
- Configurable variant ID for namespacing (logs, position IDs, registry entries)
- Verify thread/async-safe (no shared mutables between instances)

### Phase 2 — `VariantOrchestrator` (~1 day)

New top-level class:
```python
class VariantOrchestrator:
    def __init__(self, variants: List[VariantConfig]):
        self.saxo = SharedSaxoClient(quote_ttl_seconds=5)
        self.instances = [
            StrategyInstance(
                variant_id=v.id,
                config=v.materialized_config,
                saxo_client=self.saxo,  # shared!
                state_path=f"data/variant_{v.id}/state.json",
                db_path=f"data/variant_{v.id}/backtesting.db",
                metrics_path=f"data/variant_{v.id}/metrics.json",
            )
            for v in variants
        ]

    async def heartbeat_tick(self):
        # 1. Collect all UICs from all instances' active entries
        all_uics = set()
        for inst in self.instances:
            all_uics.update(inst.active_uics())

        # 2. Single batched quote fetch (cached for 5s)
        quotes = await self.saxo.get_quotes_batch(list(all_uics))

        # 3. Distribute to each instance, each runs its own logic
        await asyncio.gather(*(inst.tick(quotes) for inst in self.instances))

    async def entry_window_tick(self):
        # 1. Single chain fetch for the day's expiry
        chain = await self.saxo.get_option_chain()

        # 2. Each variant independently runs MKT-024/020/022/011
        #    using shared chain data, picks its own strikes
        await asyncio.gather(*(
            inst.run_entry_logic(chain) for inst in self.instances
        ))
```

### Phase 3 — Shared `SaxoClient` with quote cache (~½ day)

The existing `SaxoClient` is fine; just wrap it with a TTL cache:

```python
class SharedSaxoClient:
    def __init__(self, ttl_seconds=5):
        self._inner = SaxoClient(...)
        self._cache = {}  # uic -> (timestamp, quote)
        self._lock = asyncio.Lock()

    async def get_quotes_batch(self, uics: list) -> dict:
        now = time.time()
        cached = {u: q for u, (t, q) in self._cache.items()
                  if u in uics and now - t < self.ttl}
        missing = [u for u in uics if u not in cached]
        if missing:
            fresh = self._inner.get_quotes_batch(missing)
            for u, q in fresh.items():
                self._cache[u] = (now, q)
            cached.update(fresh)
        return cached
```

### Phase 4 — Variant config DSL (~½ day)

A way to define variants without copying entire config files:

```yaml
# bots/hydra/variants/registry.yaml
base_config: bots/hydra/config/config.json   # baseline
variants:
  - id: A
    name: Baseline
    description: Current live config — control
    overrides: {}

  - id: B
    name: Tight Tammy
    description: 50pt spread + lowered MKT-024 multipliers
    overrides:
      strategy.max_spread_width: 50
      strategy.call_starting_otm_multiplier: 1.0
      strategy.put_starting_otm_multiplier: 1.5

  - id: C
    name: Wider call buffers
    description: Test asymmetric protection floor
    overrides:
      strategy.call_stop_buffer: 1.50
      strategy.vix_regime.call_stop_buffer: [null, null, null, null]

  - id: D
    name: Aggressive entries
    description: Lower credit floors → take more entries
    overrides:
      strategy.vix_regime.min_call_credit: [0.50, 0.25, 0.15, 0.15]
      strategy.vix_regime.min_put_credit: [0.65, 0.40, 0.25, 0.20]

  - id: E
    name: EMA-driven
    description: Trend signal flips entry-side
    overrides:
      trend_filter.drive_entry_type: true
      trend_filter.ema_neutral_threshold: 0.003
```

A `materialize_variant_config(variant_id) -> dict` function applies overrides on top of the base config. JSON path-based overlay.

### Phase 5 — Per-variant data isolation (~½ day)

Each variant gets its own:
- State file: `data/variant_<id>/hydra_state.json`
- Backtesting DB: `data/variant_<id>/backtesting.db` (independent SQLite, WAL mode)
- Metrics: `data/variant_<id>/metrics.json`
- Position IDs prefixed `DRY_<id>_<timestamp>_SC` (registry already gated for DRY_*)

### Phase 6 — Telegram routing (~½ day)

Variant alerts become noise if all 5 send to the same chat. Options:

- **Tag prefix**: `[A] Position Opened`, `[B] Position Opened` etc. — works but spam.
- **Severity gating**: only critical events (variant crashed) go to Telegram; routine entry/stop events go to logs only.
- **Per-variant channel**: 5 group chats — clean separation but more setup.

**Recommended for v1**: tag prefix + severity gating. Most "informational" events skip Telegram entirely; failures still alert.

### Phase 7 — Variants dashboard (~2 days)

Backend (`dashboard/backend/routers/variants.py`):
```
GET /api/variants/list                 # all configured + their description
GET /api/variants/{id}/state           # mirror of /api/hydra/state
GET /api/variants/{id}/summary         # mirror of /api/hydra/summary
GET /api/variants/aggregate            # all in one response (for dashboard)
GET /api/variants/leaderboard          # ranked by current P&L
```

Frontend (`dashboard/frontend/src/pages/Variants.tsx`):
- 5-column grid (or vertical stack on mobile)
- Each column: variant name + description, current P&L, entries grid, stops list, key stats
- Top: live "leaderboard" — which variant is winning today
- Bottom: aggregate stats per variant (avg P&L, max DD, win rate)

### Phase 8 — Skip HOMER/HERMES for variant runs (~½ day)

HOMER and HERMES are not variant-aware and would corrupt the live HYDRA journal if they ran. Two options:

- **Disable timers while variants run** — manual journal entry for analysis. Simplest.
- **Build variant-aware HOMER** that writes 5 separate journal sections. Bigger scope.

**Recommended**: disable for v1. The new variants dashboard IS the analysis tool — HOMER can stay focused on the live HYDRA journal.

---

## Total Effort & Schedule

| Phase | Effort | Cumulative |
|---|---|---|
| 1. StrategyInstance extraction | 1.5 days | 1.5 |
| 2. VariantOrchestrator | 1 day | 2.5 |
| 3. Shared SaxoClient | 0.5 day | 3.0 |
| 4. Variant config DSL | 0.5 day | 3.5 |
| 5. Per-variant data isolation | 0.5 day | 4.0 |
| 6. Telegram routing | 0.5 day | 4.5 |
| 7. Variants dashboard | 2 days | 6.5 |
| 8. HOMER/HERMES handling | 0.5 day | 7.0 |
| Integration testing + bug fixes | 1 day | 8.0 |

**~7-8 days for a polished v1.**

---

## Real Risks (Updated With Single-Process Architecture)

### Resolved by Option 3
- ~~5× Saxo API calls~~ → 1× via shared client + cache
- ~~5× systemd services to manage~~ → one service
- ~~Per-variant rate limit guess work~~ → identical to single-bot load

### New / Remaining
1. **One process, one fault domain.** A bug in variant E's logic can crash the whole orchestrator and take down all 5 variants. Mitigation: per-variant try/except wrapping each `tick()` call so one variant's exception doesn't propagate.
2. **HYDRA was never designed multi-instance.** Hidden globals/singletons may surface during the StrategyInstance extraction. Mitigation: integration testing as we go, not all at once at the end.
3. **Variant logic divergence over time.** If we tune Variant A but not B/C/D/E, B-E start to feel "old" — and the comparison loses meaning. Mitigation: when tuning, decide explicitly whether to propagate or keep variants frozen as references.
4. **Quote cache TTL is a tuning knob.** Too low = no API savings. Too high = stale prices that mislead stop monitoring. Mitigation: start at 5s (well under heartbeat cadence), tune empirically.
5. **DB I/O contention.** 5 SQLite files writing simultaneously on same disk. Mitigation: WAL mode, separate files. SQLite handles this fine.

---

## What This Is and Isn't

### What it IS

- A way to **observe** 5 strategy variants on the **same** market day, side-by-side, in real time
- A **complement** to backtesting, not a replacement — backtest gives years of data per variant; this gives one same-day comparison
- A way to **catch behaviors** that backtest doesn't simulate (Saxo execution dynamics, MKT-046 timing, real bid/ask widening)
- A natural **promotion path** — when a variant proves out over weeks, swap its config into live HYDRA

### What it ISN'T

- **Not** a substitute for backtesting (one trading day is high noise)
- **Not** a way to make money — variants are dry mode, no real positions
- **Not** plug-and-play — non-trivial refactor of HYDRA's strategy class
- **Not** validated yet — proof-of-concept with 2 variants should come first before all 5

---

## Recommended Build Order

### Phase A (this week, no variant work yet)
- ✅ Validate single-variant dry mode end-to-end (Tuesday Apr 28 smoke test)
- ✅ Confirm yesterday's 12 commits actually work as intended
- Without this, scaling up is premature

### Phase B (next week, if Phase A succeeds)
- **Proof-of-concept**: 2 variants, no dashboard yet, just per-variant log files
- Phases 1-3 + 5 (StrategyInstance extraction, orchestrator, shared client, data isolation)
- Validate: do they actually run in parallel without stepping on each other?
- Validate: does the shared quote cache work as designed?
- Validate: API call rate matches single-bot (i.e. caching is hitting)
- ~2-3 days

### Phase C (after Phase B is stable, ~1-2 weeks later)
- Build out remaining phases: config DSL, telegram routing, dashboard, HOMER handling
- Scale from 2 variants to 5
- ~3-4 days

### Phase D (long-term, if meaningful learning surfaces)
- 4-week observation period running 5 variants
- Compare to backtest predictions for each variant
- Promote winning variant to live HYDRA when statistically significant

---

## Open Questions (Decide Before Building)

1. **Variant lineup.** Are the 5 candidates above actually the most informative? Worth a separate scoping conversation.
2. **Promotion criteria.** What metric + sample size says "Variant B beats Baseline, ship it to live"? Pre-define this so it's not ambiguous post-hoc.
3. **Should variants share entries when configs match?** E.g. if Variant A and Variant B happen to pick the same strikes, should they share the position record (deduplicating storage) or independently track? Recommend independent for clean comparison.
4. **What happens at 4 PM expiry?** Each variant settles independently. End-of-day P&L per variant. Easy.
5. **Where does HOMER fit?** Skip for variant runs. Live HYDRA journal stays canonical.

---

## Decision: Build or Defer?

**Honest cost-benefit:**

- **Cost**: ~7-8 days of engineering for the full system
- **Benefit**:
  - Real-time same-day comparison (intuitive, demonstrative)
  - Catches Saxo-execution behaviors backtests miss
  - Future tooling for any new strategy idea — instant A/B test infrastructure
  - Cool engineering project

- **Alternative use of time**: 7-8 days could go into 2-3 backtested strategy improvements with much higher data density (months of historical data per variant)

**My read**: build it AFTER tomorrow's smoke test passes. The infrastructure is genuinely useful long-term — every future strategy tweak can be tested in parallel with baseline before committing. But it's not the highest-leverage P&L work, so don't rush it.

If we proceed, **start with Phase B (proof-of-concept, 2 variants, no dashboard)** to validate the architecture before investing in the full UI.

---

## Sign-off

**Decision**: Pending — defer to post-smoke-test
**Earliest reasonable start**: Wednesday Apr 29 if Tuesday Apr 28 dry mode validates clean
**Owner**: Diogo Dias Grilo
**Reviewer**: pending
