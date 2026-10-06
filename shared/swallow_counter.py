"""Count the exceptions we deliberately swallow, so they stop being invisible.

WHY. Every bug found in the 2026-09-30 -> 2026-10-06 review was hiding behind a
`except ...: logger.debug(...)` handler — the swallow was correct (analytics
must not break trading) but it left no trace at INFO, which is the level the
bots actually run at. A sample of what those handlers were covering:

    day_type classification failed        -> the column was NULL for 10 weeks
    realized_volatility failed            -> same
    DataRecorder stop write failed        -> silently lost an entire stop row
    Greeks fetch failed                   -> ~1 entry in 4 lost its delta
    VIX staleness check failed            -> still-unexplained VIX retries

There are **33** such handlers in the trading path.

WHY NOT JUST RAISE THEM TO WARNING. Some fire every ~11 seconds. Raising all 33
would bury the signal in exactly the noise that teaches people to stop reading
the logs — the failure mode that let these hide in the first place. Counting is
the alternative: a handler that never fires costs nothing and prints nothing; a
handler that fires 47 times becomes one line in the nightly telemetry report.

Deliberately NOT a metrics system. In-memory, flushed once per day at
settlement, read by `scripts/audit_telemetry_health.py`. Thread-safe because
the greeks fetch and the Telegram poller both run off the main thread.
"""
from __future__ import annotations

import threading
from typing import Any, Dict, Optional

_lock = threading.Lock()
_counts: Dict[str, Dict[str, Any]] = {}


def note(site: str, exc: BaseException, logger=None, msg: Optional[str] = None) -> None:
    """Record one swallowed exception at `site`. Never raises.

    `site` is a stable short name — it is the key the report groups on, so it
    must not contain the exception text or anything else that varies per call.
    """
    try:
        with _lock:
            c = _counts.get(site)
            if c is None:
                c = {"n": 0, "type": "", "last": ""}
                _counts[site] = c
            c["n"] += 1
            c["type"] = type(exc).__name__
            c["last"] = str(exc)[:200]
    except Exception:  # noqa: BLE001 - a counter must never break its caller
        pass
    if logger is not None:
        try:
            logger.debug("%s: %s: %s", msg or site, type(exc).__name__, exc)
        except Exception:  # noqa: BLE001
            pass


def snapshot() -> Dict[str, Dict[str, Any]]:
    with _lock:
        return {k: dict(v) for k, v in _counts.items()}


def reset() -> None:
    with _lock:
        _counts.clear()


def total() -> int:
    with _lock:
        return sum(v["n"] for v in _counts.values())
