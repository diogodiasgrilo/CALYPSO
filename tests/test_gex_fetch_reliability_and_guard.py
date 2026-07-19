"""2026-06-10: Polygon GEX fetch reliability + stale-greeks guard.

Root cause of the live GEX timeouts (which placed Entry #1's "8δ" put at ~35δ
off a stale profile): ~80 SERIAL per-contract Polygon calls (5s timeout each,
no retry) + a chain pull whose timeout aborts the whole fetch. Fixes:
  - parallelize the per-contract hydration (ThreadPoolExecutor);
  - retry the chain pull with backoff;
  - age-gate the profile at strike selection so a failed live fetch falls back
    to the conservative OTM-multiplier instead of trading off stale greeks.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.brandon import gex_provider  # noqa: E402
from bots.hydra.brandon.gex_provider import fetch_polygon_chain_with_greeks  # noqa: E402


def _chain_contract(strike, ticker):
    return {"details": {"strike_price": strike, "contract_type": "call", "ticker": ticker},
            "open_interest": 100}


# ---------------------------------------------------------------- reliability
class TestParallelHydration:
    def test_hydration_is_parallel_and_complete(self):
        spot = 7000.0
        strikes = list(range(6900, 7101, 10))  # 21 strikes within ±5% of spot
        chain = [_chain_contract(s, f"O:SPX..C{s}") for s in strikes]
        calls = {"contract": 0}

        def fake_http(url):
            if "/I:" in url:                       # per-contract snapshot
                calls["contract"] += 1
                time.sleep(0.05)                   # 50ms per call
                return {"results": {"greeks": {"gamma": 0.001, "delta": 0.1},
                                    "implied_volatility": 0.2}}
            return {"results": chain}              # chain snapshot

        t0 = time.perf_counter()
        out = fetch_polygon_chain_with_greeks(
            underlying="SPX", expiry=date(2026, 6, 10), api_key="k",
            http_fetch=fake_http, spot=spot, max_contracts_to_hydrate=80,
        )
        elapsed = time.perf_counter() - t0
        assert calls["contract"] == len(strikes)
        assert sum(1 for c in out if c.get("greeks")) == len(strikes)
        # serial would be 21×50ms ≈ 1.05s; parallel (8 workers) ≈ 3 waves ≈ 150ms
        assert elapsed < 0.5, f"hydration not parallelized: {elapsed:.2f}s"

    def test_per_contract_failures_dropped_no_raise(self):
        spot = 7000.0
        chain = [_chain_contract(s, f"O:SPX..C{s}") for s in (6950, 7000, 7050)]

        def fake_http(url):
            if "/I:" in url:
                if "7000" in url:
                    raise RuntimeError("polygon read timeout")
                return {"results": {"greeks": {"gamma": 0.001}, "implied_volatility": 0.2}}
            return {"results": chain}

        out = fetch_polygon_chain_with_greeks(
            underlying="SPX", expiry=date(2026, 6, 10), api_key="k",
            http_fetch=fake_http, spot=spot,
        )
        by = {c["details"]["strike_price"]: c for c in out}
        assert by[6950].get("greeks") and by[7050].get("greeks")   # neighbors hydrated
        assert not by[7000].get("greeks")                          # failing one dropped, no raise


class TestChainPullRetry:
    def test_chain_retries_on_transient_failure(self):
        chain = [_chain_contract(7000, "O:SPX..C7000")]
        state = {"chain_calls": 0}

        def fake_http(url):
            if "/I:" in url:
                return {"results": {"greeks": {"gamma": 0.001}, "implied_volatility": 0.2}}
            state["chain_calls"] += 1
            if state["chain_calls"] == 1:
                raise RuntimeError("The read operation timed out")
            return {"results": chain}

        with patch.object(gex_provider._time, "sleep"):   # skip the backoff
            out = fetch_polygon_chain_with_greeks(
                underlying="SPX", expiry=date(2026, 6, 10), api_key="k",
                http_fetch=fake_http, spot=7000.0,
            )
        assert state["chain_calls"] == 2                  # retried once, then succeeded
        assert len(out) == 1

    def test_chain_raises_after_all_retries(self):
        def fake_http(url):
            raise RuntimeError("The read operation timed out")

        with patch.object(gex_provider._time, "sleep"):
            with pytest.raises(Exception):
                fetch_polygon_chain_with_greeks(
                    underlying="SPX", expiry=date(2026, 6, 10), api_key="k",
                    http_fetch=fake_http, spot=7000.0,
                )


# ----------------------------------------------------------- stale-greeks guard
from bots.hydra.strategy import HydraStrategy  # noqa: E402
from bots.hydra.brandon.strategy import BrandonHydraStrategy  # noqa: E402


def _gstrat():
    s = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
    s.brandon_delta_target_enabled = True
    s.current_price = 7366.45
    s.current_vix = 20.87
    s.brandon_delta_target_pct = 0.08
    s.strike_increment = 5
    s._brandon_today_date = lambda: date(2026, 6, 10)
    s._brandon_send_telegram = MagicMock()
    s._brandon_estimate_t_years_to_close = MagicMock(return_value=0.001)
    s._get_vix_adjusted_spread_width = MagicMock(return_value=5)
    return s


def _profile(age_s):
    return SimpleNamespace(deltas={7290.0: -0.08},
                           fetched_at=datetime.now(timezone.utc) - timedelta(seconds=age_s))


class TestStaleGreeksGuard:
    def test_stale_profile_falls_back_and_alerts(self):
        s = _gstrat()
        s._brandon_get_gex_profile = MagicMock(return_value=_profile(120))   # 120s old = failed live fetch
        with patch.object(HydraStrategy, "_calculate_strikes", return_value="FALLBACK") as sup:
            out = s._calculate_strikes(SimpleNamespace(entry_number=1))
        assert out == "FALLBACK"            # routed to the conservative OTM-multiplier
        sup.assert_called_once()
        s._brandon_send_telegram.assert_called_once()   # operator alerted to the degrade

    def test_none_profile_falls_back_no_alert(self):
        s = _gstrat()
        s._brandon_get_gex_profile = MagicMock(return_value=None)
        with patch.object(HydraStrategy, "_calculate_strikes", return_value="FALLBACK") as sup:
            assert s._calculate_strikes(SimpleNamespace(entry_number=1)) == "FALLBACK"
        sup.assert_called_once()
        s._brandon_send_telegram.assert_not_called()     # plain no-data isn't a "stale" alert

    def test_fresh_profile_uses_delta_target(self):
        s = _gstrat()
        s._brandon_get_gex_profile = MagicMock(return_value=_profile(5))     # fresh
        entry = SimpleNamespace(entry_number=1, short_call_strike=0.0, long_call_strike=0.0,
                                short_put_strike=0.0, long_put_strike=0.0)
        # return_delta=True → (strike, achieved_delta); near-target 8δ so the
        # degraded-data floor guard does NOT fire.
        with patch.object(HydraStrategy, "_calculate_strikes", return_value="FALLBACK") as sup, \
             patch("bots.hydra.brandon.gex_provider.find_strike_at_delta",
                   side_effect=[(7425.0, 0.08), (7290.0, -0.08)]):
            s._calculate_strikes(entry)
        sup.assert_not_called()             # delta-target used, NOT the fallback
        assert entry.short_call_strike == 7425.0 and entry.short_put_strike == 7290.0
        assert not getattr(entry, "abort_entry_reason", None)
