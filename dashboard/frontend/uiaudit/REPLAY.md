# Replaying a real past session

The dashboard's live view reads **state-file** entries, and the state file only
ever holds *today*. So the interesting states — live cushions, stops firing,
one-sided entries, a large loss — are invisible unless you happen to be watching
on a day that produces them.

Every historical session survives in `backtesting.db`. `build-replay.py`
reconstructs one into the shape the UI consumes, so any past day can be
rendered on demand.

```bash
# 1. pull the raw session from the VM (see the script header for the query)
#    -> /tmp/replay_raw.json
# 2. build fixtures from it
python3 build-replay.py
# 3. serve and audit
FIXTURES=./fixtures_replay/ node mock-server.mjs &
node audit-replay.mjs          # desktop + mobile, console errors, screenshots
```

## Why 2026-07-16 is the default subject

B's worst session (−$7,074): 7 entries, **all `put_only`**, 6 stops mixing
`early_close` and `stop_loss`, at 10 contracts. That exercises one-sided
rendering, loss formatting, multi-contract display and distinct exit states in
one pass. Result: **zero console errors, zero overflow, zero NaN** on both
desktop and mobile, with every one-sided guard holding ("C: skipped",
"P:7490/7485").

## What the replay is faithful about, and what it is not

Faithful: entries, strikes, credits, contracts, stop times, exit reasons,
one-sided flags, the SPX series (derived from `market_ticks`, since B records
ticks rather than `market_ohlc_1min`), and ISO-with-offset timestamps.

**Not** faithful: commission and the intraday P&L series are not reconstructed,
so the card's TODAY reads gross (−$6,949) against the database's net
(−$7,074), and the INTRADAY P&L panel stays empty. The `market_status` fixture
is still *today's*, so an unrelated FOMC banner may appear. None of these are
product defects — they are gaps in the reconstruction, and they are listed here
so nobody mistakes one for a bug.

## The trap it surfaced

The first replay rendered entries at 04:46 where the database says 09:46.
Cause: the database stores **naive** wall-clock ET (`2026-07-16 09:46:04`) while
the state file stores **ISO with offset**. `formatTime` does `new Date()` and
converts to New York — correct for the latter, silently 4–5 hours wrong for the
former. It was the fixture's fault, not the product's (the real database
consumers extract the clock time by regex), but the hazard is pinned by
`tests/test_dashboard_timestamp_contract_2026_09_17.py`.
