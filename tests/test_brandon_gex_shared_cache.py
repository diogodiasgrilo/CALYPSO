"""Tests for bots.hydra.brandon.gex_shared_cache."""

import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.brandon.gex_provider import GEXProfile, StrikeDelta, StrikeGEX
from bots.hydra.brandon import gex_shared_cache


@pytest.fixture(autouse=True)
def _tmp_cache_dir(tmp_path, monkeypatch):
    """Redirect the shared cache to a per-test tempdir so tests don't
    collide with production state or each other."""
    monkeypatch.setenv("CALYPSO_GEX_CACHE_DIR", str(tmp_path))
    yield tmp_path


def _sample_profile(*, spot=7400.0, expiry=date(2026, 5, 13), fetched_at=None):
    return GEXProfile(
        spot=spot,
        expiry=expiry,
        fetched_at=fetched_at or datetime.now(timezone.utc),
        strikes=(
            StrikeGEX(strike=7390.0, gex=-1.5e12),
            StrikeGEX(strike=7400.0, gex=-2.0e12),
            StrikeGEX(strike=7410.0, gex=-1.0e12),
            StrikeGEX(strike=7340.0, gex=+1.2e12),
        ),
        deltas=(
            StrikeDelta(strike=7390.0, contract_type="call", delta=0.12, iv=0.15),
            StrikeDelta(strike=7340.0, contract_type="put", delta=-0.08, iv=0.16),
        ),
    )


class TestSharedCacheRoundTrip:
    def test_save_then_load_recovers_profile_intact(self):
        p = _sample_profile()
        gex_shared_cache.save_shared_profile(p, underlying="SPX")
        loaded = gex_shared_cache.load_shared_profile(
            underlying="SPX", expiry=p.expiry, max_age_seconds=300,
        )
        assert loaded is not None
        assert loaded.spot == p.spot
        assert loaded.expiry == p.expiry
        assert loaded.fetched_at == p.fetched_at
        assert tuple((s.strike, s.gex) for s in loaded.strikes) == tuple(
            (s.strike, s.gex) for s in p.strikes
        )
        assert tuple((d.strike, d.contract_type, d.delta, d.iv) for d in loaded.deltas) == tuple(
            (d.strike, d.contract_type, d.delta, d.iv) for d in p.deltas
        )

    def test_load_returns_none_when_file_missing(self):
        loaded = gex_shared_cache.load_shared_profile(
            underlying="SPX", expiry=date(2026, 5, 13), max_age_seconds=300,
        )
        assert loaded is None

    def test_load_returns_none_when_stale(self):
        old = datetime.now(timezone.utc) - timedelta(seconds=600)
        p = _sample_profile(fetched_at=old)
        gex_shared_cache.save_shared_profile(p, underlying="SPX")
        # 5-min TTL — saved profile is 10 min old → stale
        loaded = gex_shared_cache.load_shared_profile(
            underlying="SPX", expiry=p.expiry, max_age_seconds=300,
        )
        assert loaded is None

    def test_load_returns_none_on_underlying_mismatch(self):
        p = _sample_profile()
        gex_shared_cache.save_shared_profile(p, underlying="SPX")
        loaded = gex_shared_cache.load_shared_profile(
            underlying="NDX", expiry=p.expiry, max_age_seconds=300,
        )
        assert loaded is None

    def test_load_returns_none_on_expiry_mismatch(self):
        p = _sample_profile(expiry=date(2026, 5, 13))
        gex_shared_cache.save_shared_profile(p, underlying="SPX")
        loaded = gex_shared_cache.load_shared_profile(
            underlying="SPX", expiry=date(2026, 5, 14), max_age_seconds=300,
        )
        assert loaded is None

    def test_corrupt_file_returns_none(self, _tmp_cache_dir):
        cache_file = _tmp_cache_dir / "brandon_gex_profile.json"
        cache_file.write_text("{not valid json")
        loaded = gex_shared_cache.load_shared_profile(
            underlying="SPX", expiry=date(2026, 5, 13), max_age_seconds=300,
        )
        assert loaded is None

    def test_save_is_atomic_no_partial_file(self, _tmp_cache_dir):
        # If save crashes mid-write, the cache file should still hold the
        # previous good copy (atomic rename), and tmp file should be cleaned.
        p1 = _sample_profile(spot=7400.0)
        gex_shared_cache.save_shared_profile(p1, underlying="SPX")
        loaded1 = gex_shared_cache.load_shared_profile(
            underlying="SPX", expiry=p1.expiry, max_age_seconds=300,
        )
        assert loaded1 is not None and loaded1.spot == 7400.0

        # No stray .tmp files left behind from the previous save
        tmp_files = list(_tmp_cache_dir.glob(".gex_*.tmp"))
        assert tmp_files == []

    def test_none_delta_or_iv_survives_round_trip(self):
        # Strikes without greeks come back as None on both fields.
        p = GEXProfile(
            spot=7400.0,
            expiry=date(2026, 5, 13),
            fetched_at=datetime.now(timezone.utc),
            strikes=(),
            deltas=(
                StrikeDelta(strike=7390.0, contract_type="call", delta=None, iv=None),
                StrikeDelta(strike=7340.0, contract_type="put", delta=-0.08, iv=None),
            ),
        )
        gex_shared_cache.save_shared_profile(p, underlying="SPX")
        loaded = gex_shared_cache.load_shared_profile(
            underlying="SPX", expiry=p.expiry, max_age_seconds=300,
        )
        assert loaded is not None
        assert loaded.deltas[0].delta is None
        assert loaded.deltas[0].iv is None
        assert loaded.deltas[1].delta == -0.08
        assert loaded.deltas[1].iv is None


class TestFetchLock:
    def test_lock_can_be_acquired_and_released(self):
        # Smoke test — the lock just needs to acquire and release cleanly.
        # Real cross-process serialization is covered by integration usage.
        with gex_shared_cache.fetch_lock():
            pass
        # Second acquire works after first releases
        with gex_shared_cache.fetch_lock():
            pass

    def test_lock_releases_on_exception(self):
        # If the body raises, the lock is released and the next acquirer
        # gets it without hanging.
        with pytest.raises(RuntimeError):
            with gex_shared_cache.fetch_lock():
                raise RuntimeError("boom")
        # Subsequent acquire works
        with gex_shared_cache.fetch_lock():
            pass
