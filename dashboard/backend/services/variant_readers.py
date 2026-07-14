"""Single source of truth for resolving a picked strategy id → its
``BacktestingDBReader``.

Every router that renders **per-variant** History / Analytics / day-detail data
MUST resolve the picked strategy through :func:`reader_for` so they all read the
SAME variant the SAME way.

Why this module exists (the 2026-07-13 header-vs-table bug): ``/api/metrics/daily``
was variant-aware (it had a *private* ``_reader_for``), but ``/api/hydra/entries``
and ``/api/market/replay_pnl`` were hard-wired to ``settings.backtesting_db`` (the
primary = variant C). So the History day-detail HEADER cards (variant-aware) and
the ENTRIES / STOP-LOSSES tables (primary-only) disagreed whenever a non-primary
variant was picked — e.g. picking B showed B's counts (7 entries / 4 stops) atop
C's 2 entry rows. Centralizing the resolution here removes that whole drift class:
add a new per-variant endpoint and it resolves identically by construction.

Isolation is by FILE PATH (each variant has its own SQLite DB); the trade tables
carry no variant column, so a reader pointed at the wrong file silently returns
the wrong variant's rows. Resolving through one helper is the guard against that.
"""

from dashboard.backend.config import settings
from dashboard.backend.services.db_reader import BacktestingDBReader

# The primary / canonical variant. ``settings.backtesting_db`` points at its DB,
# and only for this id is today's live-state augmentation valid (the live state
# file tracks the same variant). Currently variant C (the LIVE paper strategy).
PRIMARY_ID = "c"

# The one canonical reader (``settings.backtesting_db`` == the live primary's DB).
canonical_reader = BacktestingDBReader(settings.backtesting_db)

# Per-variant readers, built lazily and cached by lowercased id.
_variant_readers: dict[str, BacktestingDBReader] = {}


def reader_for(strategy_id: str) -> tuple[BacktestingDBReader, bool]:
    """Return ``(reader, is_canonical)`` for a picked strategy id.

    Empty / unknown / the primary id → the canonical reader with
    ``is_canonical=True`` (callers may graft today's live-state augmentation). A
    known non-primary variant → its own DB reader with ``is_canonical=False`` (we
    never graft the primary's live ``today`` onto another variant's history). An
    unknown id falls back to canonical rather than raising, so a bad query param
    degrades gracefully instead of 500-ing.
    """
    sid = (strategy_id or "").strip().lower()
    if not sid or sid == PRIMARY_ID:
        return canonical_reader, True
    path = getattr(settings, f"variant_{sid}_backtesting_db", None)
    if path is None:
        return canonical_reader, True
    if sid not in _variant_readers:
        _variant_readers[sid] = BacktestingDBReader(path)
    return _variant_readers[sid], False
