# F3 — Option Chain / Credit-Estimation Flow: IBKR Design

**Status**: 📋 design — awaiting approval before implementation
**Date**: 2026-05-21
**Probe evidence**: `scripts/probe_ibkr_chain.py` run 2026-05-21 (log:
`scripts/probe_chain_*.log`)

F3 is the hardest flow of the HYDRA IB-only rewrite. It covers HYDRA's
`get_option_chain` ×3 + chain-coupled `get_quotes_batch` ×3 +
`get_option_greeks` ×1 — the MKT-020 / MKT-022 / MKT-045 credit-
estimation + strike-tightening machinery.

---

## 1. Verified IBKR secdef behavior (from the probe)

| Fact | Evidence | Design consequence |
|---|---|---|
| `search_strikes_by_conid(conid, OPT, month, exchange)` returns `{"call":[strikes], "put":[strikes]}` in ~2.2s | Probe 1 | One cheap call gets the **month-level strike list** |
| You CANNOT fetch the whole chain's conids in one call — `secdef_info` rejects a missing strike with `400: "strike is required for warrant and option"` | Probe 5 | Conid resolution is **per-strike** |
| `secdef_info(conid, OPT, month, exchange, strike)` WITHOUT `right` returns BOTH call + put for that strike (`list[2]`) | Probe 4 | One call per strike resolves **both rights** — halves the call count vs per-(strike,right) |
| `secdef_info` entries carry `conid`, `strike`, `right`, `maturityDate` (YYYYMMDD), `tradingClass` | Probe 3/4 | We filter to the exact expiry via `maturityDate` — same pattern as the Phase A.10 `qualify_contract` fix |
| A `month=MAY26` query mixes ALL May expiries — strike 6745 came back with `maturityDate=20260529` | Probe 3 | The month-level strike list is a **superset** of any single expiry's strikes; the per-strike `secdef_info` result must be expiry-filtered |

## 2. The cost problem + the solution

**Naive approach**: HYDRA's MKT-020/022 scans candidate strikes inward
from a starting OTM distance. Resolving a conid for every candidate via
`secdef_info` would be ~20-40 calls × ~1s = 20-40s per chain fetch.
**Far too slow for a 0DTE entry decision.**

**Key realization**: HYDRA does NOT need conids for the full strike
universe. It needs:
1. The **strike list** — to know which strikes exist (for snapping
   non-5pt-aligned candidates to real strikes). → 1 cheap call.
2. Conids for the **candidate strikes it actually evaluates** — a
   bounded set. → N calls, but N is bounded and the calls are
   independent (parallelizable) + cached.

**Three cost mitigations, stacked**:

| Mitigation | Effect |
|---|---|
| One `secdef_info` call per strike returns BOTH rights | Halves call count (probe 4) |
| `qualify_contract`'s existing conid cache | Repeated strikes across entries E#2/E#3 are free after E#1 |
| Parallel resolution via a bounded thread pool | ~30 calls drop from ~30s serial to ~3-4s wall-clock |

With all three: first entry of the day pays ~3-4s for conid resolution;
subsequent entries are near-instant (cache hits). Acceptable for a
strategy whose entry slots are minutes apart.

## 3. New IBClient method

```python
def qualify_option_strikes(
    self,
    *,
    symbol: str,
    expiry: date,
    strikes: list[float],
    trading_class: str = "SPXW",
    max_workers: int = 8,
) -> dict[tuple[float, str], int]:
    """Batch-resolve conids for many (strike, right) pairs at one expiry.

    Returns {(strike, right): conid} for every (strike, "C") and
    (strike, "P") that exists at `expiry`. Strikes with no listed
    option at that exact expiry are simply absent from the result.

    Implementation:
      - One secdef_info call per strike (returns both rights — probe 4)
      - Filtered to entries whose maturityDate == expiry
      - Parallelized across a bounded ThreadPoolExecutor (max_workers)
      - Each underlying secdef_info call still routes through _ib_call
        (retry + per-family circuit breaker) for resilience
      - Results populate the same conid cache qualify_contract uses, so
        a later single qualify_contract(symbol, expiry, strike, right)
        is a cache hit
    """
```

Design notes:
- `max_workers=8` keeps us well under IBKR's ~5 req/s sustained guidance
  while cutting wall-clock ~8×. Tunable.
- Thread-safety: the conid cache writes are dict assignments under the
  existing `_call_lock`; `_ib_call` already serializes the ibind calls.
- The method does NOT raise on individual-strike failure — a strike
  that 400s (doesn't exist at that expiry) is logged at debug and
  omitted. Callers see "this strike isn't tradable today" via absence.

`IBClient.get_option_chain(symbol, expiry)` (existing, returns
`list[float]`) stays as-is — it's the cheap strike-list call HYDRA
uses for snapping.

## 4. How HYDRA's three call sites adapt

All three currently do: Saxo `get_option_chain` → parse `OptionSpace`
→ build `{strike: uic}` maps → snap candidates against the maps.

New flow (broker-agnostic helper on HydraStrategy, `_read_option_chain`):

```
_read_option_chain(expiry, candidate_strikes) -> ChainMaps
  if self.broker:                       # IB path
     strike_list = broker.get_option_chain("SPX", expiry)   # 1 call
     snapped     = snap(candidate_strikes, strike_list)
     conid_map   = broker.qualify_option_strikes(            # N calls, parallel
                       symbol="SPX", expiry=expiry, strikes=snapped)
     return ChainMaps(call={s: conid_map[(s,"C")] ...},
                      put ={s: conid_map[(s,"P")] ...})
  else:                                 # Saxo legacy path
     resp = client.get_option_chain(option_root_id=..., expiry_dates=[expiry])
     ... parse OptionSpace into call/put maps (unchanged) ...
```

- MKT-045 (chain snapping): builds full call/put maps for the entry's
  4 strikes + small neighborhood. Candidate set ≈ 4-8 strikes.
- MKT-020 (call tightening) / MKT-022 (put tightening): the candidate
  set is the inward-scan range. We resolve conids for the WHOLE scan
  range up-front in one `qualify_option_strikes` batch (parallel),
  rather than one-at-a-time inside the scan loop. The scan then reads
  from the resolved map — no per-iteration I/O.

The chain-coupled `get_quotes_batch` ×3 stay structurally the same —
they take a list of conids (was UICs) and return quotes. The conids now
come from `qualify_option_strikes` output. `get_quotes_batch` on
IBClient already exists and works (Phase A.10).

`get_option_greeks` ×1: IBClient.get_option_greeks(conid) already
exists. The call site swaps uic→conid + drops asset_type.

## 5. Commit breakdown for F3

F3 is too big for one commit. Proposed sequence, each tested + green:

| Commit | Scope |
|---|---|
| F3.1 | `IBClient.qualify_option_strikes` batch resolver + unit tests (mocked secdef) |
| F3.2 | `HydraStrategy._read_option_chain` broker-agnostic helper + unit tests (both paths) |
| F3.3 | Rewrite MKT-045 chain-snapping site to use `_read_option_chain` |
| F3.4 | Rewrite MKT-020 call-tightening site (chain + batch quotes) |
| F3.5 | Rewrite MKT-022 put-tightening site (chain + batch quotes) |
| F3.6 | Rewrite the `get_option_greeks` site + remaining chain-coupled batch quote |

## 6. Multi-expiry strikes — confirmed, handled

The 2026-05-21 extended probe (PROBE 7) confirmed a strike CAN return
many entries: strike 6750 right=C returned **7 entries** — listed
across 7 different May expiries. The `maturityDate == expiry` filter
handles this correctly: even a 7-entry response yields the 1 entry for
our target expiry. No design change — the filter is the safety net.

## 7. PROBE 6 + 7 results (2026-05-21 extended run)

- **PROBE 6 — CSV multi-strike**: ❌ NOT supported. `secdef_info` with
  `strike="6730.0,6735.0,6740.0"` → `500 Internal Server Error:
  Invalid Strike=...`. We cannot batch strikes into one call.
- **PROBE 7 — concurrent secdef thread-safety**: ✅ CONFIRMED SAFE.
  6 concurrent `search_secdef_info_by_conid` calls fired from 6 threads
  through ONE ibind client: wall-clock 1.85s vs 11.05s serial sum =
  **6.0× concurrency factor**, and every response was strike-correct
  (no cross-thread data corruption). ibind's REST path handles
  concurrent secdef reads cleanly.

**Design consequence**: F3 uses genuine parallelism via a ThreadPool,
but the secdef calls must bypass `IBClient._call_lock` (which would
serialize them). `_ib_call` gains a `_serialize` kwarg (default True =
unchanged behavior); the batch resolver passes `_serialize=False`.
This is safe ONLY for the read-only secdef batch — verified by PROBE 7
— not a general license to drop the lock elsewhere.

## 8. Rate-limit safety (verified)

IBKR Client Portal Web API global limit: **10 requests/second**
(verified — `/iserver/secdef/info` is not in the per-endpoint table so
it inherits the 10/s global). 429 → 10-minute IP penalty box; repeat →
permanent block. `max_workers=8` → ~8 req/s peak burst, **under the 10
limit with 20% headroom**. The A.8 retry+breaker absorbs any rare 429.

## 9. Implementation status

- `_ib_call` `_serialize` kwarg + `IBClient.qualify_option_strikes`:
  F3.1 ✅ committed (8092ae9)
- `HydraStrategy._read_option_chain`: F3.2 ✅ committed
- MKT-045/020/022 rewrites + greeks: F3.3-F3.6 (pending)

### F3.2 — `_read_option_chain` shipped

Broker-agnostic helper on `HydraStrategy`. Returns
`(call_map, put_map)` — each `{strike: instrument_id}` (IBKR conid or
Saxo UIC). IB path: 1 strike-list call → snap candidates to real
strikes (≤25pt) → `qualify_option_strikes` batch → split by right.
Saxo path: legacy `get_option_chain` + `OptionSpace` parse, unchanged,
`candidate_strikes` ignored (superset harmless). `({}, {})` on any
failure — every call site already handles empty maps. 18 unit tests
cover both paths + snapping/dedup/tolerance/failure isolation.

## 7. Decision requested

Approve this design? Specifically:
1. The `qualify_option_strikes` batch-resolver approach (parallel
   per-strike secdef, both-rights-per-call, expiry-filtered, cached)
2. `max_workers=8` for the thread pool (vs IBKR's ~5 req/s sustained —
   bursty but short; tune down if we see 429s)
3. The 6-commit F3.1-F3.6 breakdown

Once approved, F3.1 (IBClient batch resolver) starts.
