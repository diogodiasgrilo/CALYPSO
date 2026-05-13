"""Filesystem-backed GEX cache shared across HYDRA Brandon variants.

B and C run as separate systemd processes (hydra_variant_b.service /
hydra_variant_c.service), so an in-process module-level singleton can't
share state between them. This module persists the most-recent GEX profile
to disk via an atomic write so both variant processes read the same chain
snapshot, eliminating the cross-variant divergence seen on 2026-05-13
(B got SKIP using a 12-min-stale profile while C got KEEP from a 3-sec
fresh one — different chains, different decisions on near-identical
strikes at near-identical times).

Cache layout (POSIX atomic-rename writes):
  $CALYPSO_GEX_CACHE_DIR/brandon_gex_profile.json   — most recent profile
  $CALYPSO_GEX_CACHE_DIR/brandon_gex_profile.lock   — fcntl lock file

Fetch coordination: load_shared_profile is lock-free (best-effort read);
fetch_lock() serializes concurrent fetches across variant processes so the
second variant entering a shared slot reuses the first one's just-written
profile instead of issuing its own Polygon round trip.

All errors are caught and logged — the cache is opportunistic. If the
filesystem path is unavailable or the file is corrupt, callers fall
through to their own per-variant fetch path.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from .gex_provider import GEXProfile, StrikeDelta, StrikeGEX

logger = logging.getLogger(__name__)


_DEFAULT_CACHE_DIR = "/opt/calypso/data/shared"


def _cache_dir() -> Optional[Path]:
    """Resolve cache dir from env or default. Best-effort: returns None if
    the path is unwritable (dev machine without /opt/calypso, container
    without write perms, etc.) so callers can degrade to per-variant cache
    without raising.
    """
    p = Path(os.environ.get("CALYPSO_GEX_CACHE_DIR", _DEFAULT_CACHE_DIR))
    try:
        p.mkdir(parents=True, exist_ok=True)
        return p
    except OSError:
        return None


def _cache_file() -> Optional[Path]:
    d = _cache_dir()
    return d / "brandon_gex_profile.json" if d else None


def _lock_file() -> Optional[Path]:
    d = _cache_dir()
    return d / "brandon_gex_profile.lock" if d else None


def load_shared_profile(
    *,
    underlying: str,
    expiry: date,
    max_age_seconds: float,
) -> Optional[GEXProfile]:
    """Read the shared cache. Returns None if missing / corrupt / mismatched / stale.

    Lock-free: writes are atomic via os.replace, so any reader either sees
    the previous file in full or the new file in full — never a torn write.
    """
    path = _cache_file()
    if path is None or not path.exists():
        return None
    try:
        with open(path, "r") as f:
            data = json.load(f)
        if data.get("underlying") != underlying:
            return None
        if data.get("expiry") != expiry.isoformat():
            return None
        fetched_at = datetime.fromisoformat(data["fetched_at"])
        age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
        if age > max_age_seconds:
            return None
        return GEXProfile(
            spot=float(data["spot"]),
            expiry=expiry,
            fetched_at=fetched_at,
            strikes=tuple(
                StrikeGEX(strike=float(s[0]), gex=float(s[1]))
                for s in data.get("strikes", [])
            ),
            deltas=tuple(
                StrikeDelta(
                    strike=float(d["strike"]),
                    contract_type=str(d["contract_type"]),
                    delta=float(d["delta"]) if d.get("delta") is not None else None,
                    iv=float(d["iv"]) if d.get("iv") is not None else None,
                )
                for d in data.get("deltas", [])
            ),
        )
    except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        logger.warning("Brandon GEX shared cache read failed (%s): %s", path, exc)
        return None


def save_shared_profile(profile: GEXProfile, *, underlying: str) -> None:
    """Atomically write the profile to the shared cache. Silent on failure.

    Tempfile + os.replace gives POSIX-atomic visibility — concurrent readers
    see either the old file or the new one, never partial bytes.
    """
    try:
        cache_dir = _cache_dir()
        if cache_dir is None:
            return
        target = _cache_file()
        if target is None:
            return
        data = {
            "underlying": underlying,
            "expiry": profile.expiry.isoformat(),
            "fetched_at": profile.fetched_at.isoformat(),
            "spot": float(profile.spot),
            "strikes": [
                [float(s.strike), float(s.gex)] for s in profile.strikes
            ],
            "deltas": [
                {
                    "strike": float(d.strike),
                    "contract_type": d.contract_type,
                    "delta": float(d.delta) if d.delta is not None else None,
                    "iv": float(d.iv) if d.iv is not None else None,
                }
                for d in profile.deltas
            ],
        }
        fd, tmp = tempfile.mkstemp(dir=str(cache_dir), prefix=".gex_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
            os.replace(tmp, target)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError as exc:
        logger.warning("Brandon GEX shared cache write failed: %s", exc)


@contextlib.contextmanager
def fetch_lock(timeout_seconds: float = 30.0, poll_interval_seconds: float = 0.25):
    """Exclusive lock serializing GEX fetches across variant processes.

    Two B/C processes hitting the same scheduled slot would otherwise both
    fetch in parallel — wasteful and (more importantly) producing
    near-identical-but-not-identical snapshots that lead to divergent
    decisions. Under the lock, the second variant enters after the first
    finishes and finds the first's write in the shared cache.

    Non-blocking with bounded wait: uses LOCK_EX | LOCK_NB and polls until
    timeout_seconds elapses. If the lock can't be acquired in that window
    (sibling hanging, crashed mid-fetch, or kernel quirk), the contextmanager
    yields WITHOUT holding the lock so the caller's fetch path proceeds
    unlocked. That keeps the bot's monitor loop alive — better to do a
    parallel double-fetch than freeze waiting on a sibling. Default 30s
    cap is well above a normal Polygon round-trip (~5-10s) but well below
    any user-visible monitor-loop stall threshold.

    The lock is best-effort: if filesystem isn't writable or fcntl isn't
    available (non-POSIX), it falls through to a no-op contextmanager.
    Callers should still handle their own fetch failures.
    """
    lock_path = _lock_file()
    if lock_path is None:
        # Cache dir unwritable — degrade to no-op so the fetch path still works
        yield
        return
    try:
        lock_fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as exc:
        logger.warning("Brandon GEX shared cache lock unavailable: %s", exc)
        yield
        return

    held = False
    try:
        # Poll for LOCK_EX | LOCK_NB up to timeout. Yields once acquired OR
        # once timeout elapses (unlocked fall-through — see docstring).
        import time as _time
        deadline = _time.monotonic() + timeout_seconds
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held = True
                break
            except (BlockingIOError, OSError):
                # Lock held by sibling — wait briefly and retry
                if _time.monotonic() >= deadline:
                    logger.warning(
                        "Brandon GEX fetch_lock timeout after %.0fs; proceeding "
                        "without lock (sibling may be hung; double-fetch acceptable)",
                        timeout_seconds,
                    )
                    break
                _time.sleep(poll_interval_seconds)
        yield
    finally:
        if held:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
        try:
            os.close(lock_fd)
        except OSError:
            pass
