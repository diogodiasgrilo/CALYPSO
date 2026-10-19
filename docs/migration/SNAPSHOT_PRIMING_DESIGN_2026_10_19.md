# Design — snapshot priming is paid on every read, not once per conid

**Status:** designed 2026-10-19 (market open, so implemented but NOT deployed;
deploy after the close). Supersedes the assumption that `portfolio:positions`
was the next thing to attack.

## How we got here

The per-label rate-gate attribution shipped earlier today
(`feat(rategate): attribute gate pressure per family:method`) turned an
unmeasurable question into a reading. Before it, 84% of 558k gate acquisitions
were unattributed, and the one previous attempt at demand reduction (the
2026-10-06 pacing change) achieved nothing.

Two reductions followed immediately and are **confirmed with live
market-open traffic**:

| label | before | after |
|---|---|---|
| `portfolio:get_ledger` | 13.8% | **0 calls** (lazy; kept for the reconcile anchor) |
| `history:marketdata_history_by_conid` | 16.7% | **1.8%** (broker-side chart cache, 58.4% hit) |

The market-open reading then **redirected the design**:

```
acquisitions 4003 | waited 91.3% | mean 978.85ms | max 1929.06ms

market:live_marketdata_snapshot   2843   71.0%   wait 2764.2s
portfolio:positions               1050   26.2%   wait 1051.7s
history:...                         74    1.8%   wait   72.4s
```

Snapshots are **71%** — nearly 3× positions. So positions is *not* the binding
constraint, and the design effort belongs on snapshots.

## Why snapshots are not simply cacheable

The quote cache covers **underlying conids only** (it shows 3 entries). Option
legs are deliberately excluded because the stop monitor reads them, and that
exclusion is correct — it must not be relaxed. So the 2,843 calls cannot be
reduced by caching the *data*.

They can be reduced by not making **redundant calls for the same data**.

## Root cause

`_snapshot_poll` does two things per logical quote read:

```python
# Pre-flight 2: ibind's snapshot priming — first call arms the
# conid's snapshot cache, it returns no data itself.
self._ib_call("market", self._client.live_marketdata_snapshot, ...)   # (1)

for attempt in range(_SNAPSHOT_MAX_WARMUP_POLLS):                      # (2) up to 12
    data = self._ib_call("market", self._client.live_marketdata_snapshot, ...)
    if requested.issubset(_snapshot_ready_conids(data)):
        return data
    time.sleep(_SNAPSHOT_POLL_INTERVAL_S)
```

So **one logical read costs between 2 and 13 gate acquisitions**, each queueing
~978ms at the measured saturation. Call (1) is **unconditional** — paid again on
every read of a conid that was read seconds ago.

But the comment states the actual requirement: *"first call arms the conid's
snapshot cache"*. Priming is a **per-conid, per-session** need, not a per-read
one. The monitoring loop re-reads the same legs every tick, so it pays the prime
every tick for conids that have been warm for hours.

Minimum cost per read is therefore 2 slots where 1 would do, and the floor
dominates because the warm case is the common case.

## The fix: follow the two precedents already in this file

`ib_client.py` already solves exactly this shape, twice:

| mechanism | state | scope | escape hatch | cleared on |
|---|---|---|---|---|
| `_ensure_iserver_primed` | `_iserver_primed: bool` | per session | `force=True` | `disconnect()` |
| `_ensure_secdef_search_primed` | `_secdef_search_primed: set[tuple]` | per symbol | `force=True` | `disconnect()`, lockstep with `_conid_cache` |
| **`_snapshot_primed` (new)** | `set[str]` of conids | **per conid** | `force=True` | `disconnect()` |

Skip call (1) for conids already in the set. Add them after a poll that returns
data for them.

## Why this is safe — the warmup loop is the net

The decisive property: **skipping the prime cannot return a wrong value.** If a
conid is marked primed but IBKR's server-side cache has lapsed, the first data
poll returns metadata-only, the existing warmup loop keeps polling, and the data
arrives. The failure mode is *one extra poll*, self-corrected by machinery that
already exists and is already counted
(`snapshot_warmup_exhausted_count`).

Contrast a data cache, which can hand back a stale *price* — the reason option
legs are excluded from the quote cache. This change never serves old data; it
only removes a redundant request for fresh data.

### Invalidation obligations

1. **`disconnect()`** must clear the set. The IBKR session owns the priming; a
   new session has none. Mirrors `_iserver_primed = False` and
   `_secdef_search_primed.clear()`.
2. **The daily-reset self-heal** must clear it. The 01:00 ET reset wipes
   server-side priming while OAuth stays authenticated — which is exactly why
   `_snapshot_with_preflight` already re-primes `/iserver/accounts` with
   `force=True` on `_is_accounts_preflight_error`. Snapshot priming has to be
   invalidated on that same path or it would claim primed against a reset
   session.
3. **Bounded growth.** Keyed on conid; 0DTE conids churn daily, so the set must
   be cleared on disconnect (it is) and is naturally bounded by a session's
   working set (tens to low hundreds).

## Expected effect

Snapshots are 71% of demand and the warm-conid floor is 2 slots. Removing one of
those two on every warm read is up to a **~35% reduction in total gate demand**,
on top of today's ~30%. That is a projection, not a measurement — the actual
figure gets read off `by_label` after deploy, the same way the ledger and chart
reductions were confirmed.

## Alternatives considered and rejected

- **Cache option-leg quotes.** Rejected outright: the stop monitor reads them,
  and the quote cache excludes them for that reason. Serving a stale leg price
  is the one failure this system must not have.
- **Raise the gate cap 5 → 7.** Rejected, and it is not a new idea: the cap was
  **8 and was lowered to 5 on 2026-06-01 after a 429**, and IBKR penalty-boxes
  the IP for ~10 minutes on repeat. Reduce demand before raising the ceiling.
- **Broker-side positions cache.** Deferred, not rejected. Positions is 26%, and
  B2 (2026-09-24) already added a per-strategy 3s micro-cache with two
  load-bearing safety properties (`strict=True` never reads the cache; any order
  action invalidates). A broker-side version must preserve both **across
  processes** — the invalidation would have to hang off the broker's own write
  proxying (`place_and_wait_for_fill`, `cancel_order`), and `strict` would have
  to be threaded through `BrokerClient` → `/rpc` → `IBClient` as a `fresh`
  kwarg. That is a real design, but it touches the stop monitor's data source
  for a smaller prize than snapshots, so snapshots go first and positions gets
  re-measured afterwards. It may not be worth doing at all once the snapshot
  floor is removed.
- **Lower `_SNAPSHOT_MAX_WARMUP_POLLS`.** Rejected: 12 × 0.5s is calibrated
  (P7-audit H10, "less is brittle"), and the warm case already exits on the
  first poll, so the ceiling is not what is being paid.

## Deploy constraints

- `shared/ib_client.py` → **`calypso-broker` must restart.** No signature change
  crosses `/rpc`, so strategies need no restart and no ordering constraint.
- Market was open when this was written: **do not restart the broker during
  RTH** — it holds the one IBKR session every strategy trades through.
- Verify afterwards on `by_label`: `market:live_marketdata_snapshot` share and
  `acquisitions` per unit time, against the 71% / 978ms baseline above.
