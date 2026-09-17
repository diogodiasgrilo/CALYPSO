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

import json
from pathlib import Path

from shared import strategy_taxonomy as tax

from dashboard.backend.config import settings
from dashboard.backend.services.db_reader import BacktestingDBReader

# ── Canonical / primary variant = whichever Brandon seat is LIVE ──────────────
# The main page, WebSocket, iOS widget, and legacy /api/hydra/* views follow the
# "canonical" strategy. It now tracks the LIVE seat (dry_run=false) among the two
# Brandon seats b/c, so a C<->B live-paper swap moves the whole canonical view
# onto the new live seat with NO code change — request endpoints follow instantly
# (they call live_seat_id() per request), the WS broadcaster at its next start.
LIVE_SEAT_IDS = ("b", "c")
FALLBACK_SEAT_ID = "c"


def live_seat_id() -> str:
    """The live Brandon seat (b or c) — SINGLE SOURCE OF TRUTH for "the bot".

    Reads each seat's config ``dry_run``; the single live one (dry_run=false)
    wins. Falls back to ``FALLBACK_SEAT_ID`` when neither/both are live or a
    config is unreadable (e.g. briefly during a swap restart). Never raises.
    ``routers.strategies._primary_id`` delegates here so both agree.
    """
    live = []
    for vid in LIVE_SEAT_IDS:
        cfg = getattr(settings, f"variant_{vid}_config_file", None)
        if cfg is None:
            continue
        try:
            with open(cfg) as fh:
                if not bool(json.load(fh).get("dry_run", True)):
                    live.append(vid)
        except Exception:
            continue
    return live[0] if len(live) == 1 else FALLBACK_SEAT_ID


# Backwards-compat static alias (a few call sites still read this name). Prefer
# live_seat_id() anywhere that must follow the swap.
PRIMARY_ID = FALLBACK_SEAT_ID


def _seat_field(field: str, default):
    """settings.variant_<live_seat>_<field>, falling back to ``default``."""
    return getattr(settings, f"variant_{live_seat_id()}_{field}", default)


def live_state_file() -> Path:
    return _seat_field("state_file", settings.hydra_state_file)


def live_metrics_file() -> Path:
    return _seat_field("metrics_file", settings.hydra_metrics_file)


def live_log_file() -> Path:
    return _seat_field("log_file", settings.hydra_log_file)


def live_config_file() -> Path:
    return _seat_field("config_file", settings.bot_config_file)


def live_backtesting_db() -> Path:
    return _seat_field("backtesting_db", settings.backtesting_db)


def live_label() -> str:
    """Header label for the live seat (e.g. B's config label after a swap)."""
    return _seat_field("label", settings.primary_label)


def live_baseline_date() -> str:
    """The rebase baseline for the PRIMARY cumulative card = the LIVE seat's own
    baseline. After a C->B swap, B's card must start at B's go-live date (so its
    pre-live dry-run history doesn't present as the live record) — NOT the global
    settings.baseline_date, which was C's. Falls back to the global baseline when
    the live seat has none set."""
    return _seat_field("baseline_date", "") or settings.baseline_date


# Per-variant DB readers, built lazily and cached by lowercased id.
_variant_readers: dict[str, BacktestingDBReader] = {}


def _capital_basis_for(sid: str) -> str:
    """The strategy's capital basis, straight from the taxonomy.

    Without this the reader computes deployed capital with the DEFINED-RISK
    formula for every strategy, so a wingless one (G) reports capital 0 and
    therefore ROI 0 / avg-capital 0 — defect D2, which Phase 3 fixed in
    base_strategy but not here.
    """
    try:
        return getattr(tax.meta(sid), "capital_basis", "defined_risk") or "defined_risk"
    except Exception:          # unknown id → the historical default
        return "defined_risk"


def _broker_margin_for(sid: str) -> float:
    """Per-contract naked-margin floor from the variant's own config, so the
    dashboard divides by the SAME number the entry gate sized with."""
    default = BacktestingDBReader.DEFAULT_BROKER_MARGIN_PER_CONTRACT
    cfg_path = getattr(settings, f"variant_{sid}_config_file", None)
    if not cfg_path:
        return default
    try:
        cfg = json.loads(Path(cfg_path).read_text()).get("strategy", {})
        return float(cfg.get("min_buying_power_per_strangle", default))
    except (OSError, ValueError, TypeError):
        return default


def _reader_for_id(sid: str) -> BacktestingDBReader:
    if sid not in _variant_readers:
        path = getattr(settings, f"variant_{sid}_backtesting_db", None) or settings.backtesting_db
        _variant_readers[sid] = BacktestingDBReader(
            path,
            capital_basis=_capital_basis_for(sid),
            broker_margin_per_contract=_broker_margin_for(sid),
        )
    return _variant_readers[sid]


def db_reader_for_variant(vid: str, db_path=None) -> BacktestingDBReader:
    """A basis-aware reader for ONE variant — the single place that knows how a
    strategy's deployed capital is computed.

    Every per-variant construction site must come through here. Building
    ``BacktestingDBReader(path)`` directly silently gets the DEFINED-RISK
    formula, which reports capital 0 (and therefore ROI 0) for a wingless
    strategy. That is exactly how defect D2 survived in a second AND third
    location after Phase 3 fixed base_strategy: three separate call sites each
    constructed their own reader.
    """
    path = db_path or getattr(settings, f"variant_{vid}_backtesting_db", None) \
        or settings.backtesting_db
    return BacktestingDBReader(
        path,
        capital_basis=_capital_basis_for(vid),
        broker_margin_per_contract=_broker_margin_for(vid),
    )


def canonical_db_reader() -> BacktestingDBReader:
    """The canonical (live-seat) backtesting-DB reader — follows the swap."""
    return _reader_for_id(live_seat_id())


# Backwards-compat: a module-level reader on the FALLBACK seat's DB for any
# legacy importer. NEW code must call canonical_db_reader() so it follows a swap.
canonical_reader = BacktestingDBReader(settings.backtesting_db)


def resolve_for(strategy_id: str) -> tuple[str, bool]:
    """``(variant_id, is_canonical)`` for a picked strategy id.

    The file-path twin of :func:`reader_for`, for the endpoints that read STATE /
    CONFIG / METRICS files rather than a database. Same degradation contract: an
    empty, unknown, or live-seat id resolves to the canonical seat with
    ``is_canonical=True``, so a bad query param renders the live seat instead of
    500-ing (dashboard rebuild Phase 1).
    """
    canonical = live_seat_id()
    sid = (strategy_id or "").strip().lower()
    if not sid or sid == canonical:
        return canonical, True
    if getattr(settings, f"variant_{sid}_state_file", None) is None:
        return canonical, True
    return sid, False


def state_file_for(strategy_id: str) -> Path:
    vid, is_canon = resolve_for(strategy_id)
    return live_state_file() if is_canon else Path(getattr(settings, f"variant_{vid}_state_file"))


def config_file_for(strategy_id: str) -> Path:
    vid, is_canon = resolve_for(strategy_id)
    p = None if is_canon else getattr(settings, f"variant_{vid}_config_file", None)
    return live_config_file() if p is None else Path(p)


def metrics_file_for(strategy_id: str) -> Path:
    vid, is_canon = resolve_for(strategy_id)
    p = None if is_canon else getattr(settings, f"variant_{vid}_metrics_file", None)
    return live_metrics_file() if p is None else Path(p)


def reader_for(strategy_id: str) -> tuple[BacktestingDBReader, bool]:
    """Return ``(reader, is_canonical)`` for a picked strategy id.

    Empty / unknown / the LIVE-SEAT id → the canonical (live-seat) reader with
    ``is_canonical=True`` (callers may graft today's live-state augmentation). A
    known non-canonical variant → its own DB reader with ``is_canonical=False``
    (we never graft the live seat's ``today`` onto another variant's history). An
    unknown id falls back to canonical rather than raising, so a bad query param
    degrades gracefully instead of 500-ing.
    """
    canonical = live_seat_id()
    sid = (strategy_id or "").strip().lower()
    if not sid or sid == canonical:
        return canonical_db_reader(), True
    path = getattr(settings, f"variant_{sid}_backtesting_db", None)
    if path is None:
        return canonical_db_reader(), True
    return _reader_for_id(sid), False
