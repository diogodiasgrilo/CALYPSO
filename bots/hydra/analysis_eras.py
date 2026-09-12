"""Date boundaries every historical analyzer has to respect (2026-09-10).

These live in one module because two analyzers already needed them and were
applying different (or no) floors. A constant duplicated across analyzers drifts
silently, and the failure mode is not a crash — it is a plausible-looking number
computed over incomparable data.

LIVE_ERA_SINCE
--------------
2026-07-24, the B<->C live-seat swap. Every IC variant's regime changed that
day, so it is a genuine boundary for all of them rather than a B-specific one:

  * B went dry-run-shadow -> LIVE, 10 contracts -> 7, and a 4-slot grid -> 7.
  * C went LIVE -> dry-run-shadow.

Rows either side of that date are not the same experiment. Before it, B's fills
are SIMULATED, at a different size, on a different schedule; after it they are
real broker fills. Pooling them and ranking slots produces a confident-looking
answer to a question nobody asked. This is not hypothetical — the 2026-09-02
decision to cut the 11:15 slot was made on pooled data and had to be reversed.

PER_ENTRY_RELIABLE_SINCE
------------------------
2026-07-02. Per-entry ``realized_pnl`` booking (schema v12 plus the Brandon
overlay fold) shipped 2026-07-01 and took effect on the first close after, so
earlier rows carry an unbooked 0.0. Anything that reads ``realized_pnl``
directly must floor here or it will read those zeros as real losses. This is a
DATA-AVAILABILITY floor, distinct from the REGIME floor above — they answer
different questions and must not be collapsed into one constant.

Both are deliberately overridable. An analyzer studying the pre-swap era is a
legitimate thing to want; doing it BY ACCIDENT is not.
"""

from __future__ import annotations

#: Regime boundary — the B<->C live-seat swap. Default floor for any
#: cross-day performance comparison on an IC variant.
LIVE_ERA_SINCE = "2026-07-24"

#: Data-availability boundary — first date per-entry realized_pnl is trustworthy.
PER_ENTRY_RELIABLE_SINCE = "2026-07-02"

#: Passed as ``since`` to mean "no floor, I know what I am doing".
NO_FLOOR = ""


def era_banner(since: str | None) -> str:
    """One-line provenance stamp for an analyzer's report header.

    Every report that applies a floor should print this. A number without its
    window is not interpretable, and the whole point of these constants is that
    the window stops being invisible.
    """
    if not since:
        return (
            "WINDOW: ALL DATES (no era floor) — pre-2026-07-24 rows are a "
            "DIFFERENT REGIME (B was dry-run/10c/4-slot, C was live). Cross-era "
            "comparisons are not valid."
        )
    if since == LIVE_ERA_SINCE:
        return (
            f"WINDOW: >= {since} (live-era floor — the B<->C seat swap; earlier "
            f"rows are a different regime and are EXCLUDED)"
        )
    return f"WINDOW: >= {since} (custom floor)"
