"""Tests for bots.hydra.brandon.gex_provider."""

import math
import os
import sys
from datetime import date, datetime, timezone

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.brandon.gex_provider import (
    GEXCluster,
    GEXProfile,
    StrikeDelta,
    StrikeGEX,
    black_scholes_gamma,
    build_profile,
    fetch_per_contract_snapshot,
    fetch_polygon_chain,
    fetch_polygon_chain_with_greeks,
    find_strike_at_delta,
    time_to_expiry_years,
)


def _contract(strike, ctype, oi, *, gamma=None, iv=None, delta=None):
    out = {
        "details": {"strike_price": strike, "contract_type": ctype},
        "open_interest": oi,
    }
    greeks = {}
    if gamma is not None:
        greeks["gamma"] = gamma
    if delta is not None:
        greeks["delta"] = delta
    if greeks:
        out["greeks"] = greeks
    if iv is not None:
        out["implied_volatility"] = iv
    return out


class TestBlackScholesGamma:
    def test_positive_for_atm_short_dated(self):
        # SPX 6800, 0.18 IV, 1 day to expiry → small but positive gamma
        g = black_scholes_gamma(spot=6800, strike=6800, iv=0.18, t_years=1 / 365.0)
        assert g > 0
        assert math.isfinite(g)

    def test_zero_at_expiry(self):
        # T=0 is a degenerate case; refuse to compute.
        assert black_scholes_gamma(6800, 6800, 0.18, 0.0) == 0.0

    def test_zero_iv(self):
        assert black_scholes_gamma(6800, 6800, 0.0, 0.001) == 0.0

    def test_otm_gamma_decays(self):
        # Far-OTM should have much lower gamma than ATM
        atm = black_scholes_gamma(6800, 6800, 0.18, 1 / 365.0)
        otm = black_scholes_gamma(6800, 7000, 0.18, 1 / 365.0)
        assert otm < atm

    def test_negative_inputs_safe(self):
        assert black_scholes_gamma(-100, 6800, 0.18, 0.001) == 0.0
        assert black_scholes_gamma(6800, -100, 0.18, 0.001) == 0.0


class TestTimeToExpiry:
    def test_full_day(self):
        now = datetime(2026, 5, 4, 9, 30, tzinfo=timezone.utc)
        expiry = datetime(2026, 5, 5, 9, 30, tzinfo=timezone.utc)
        t = time_to_expiry_years(now, expiry)
        assert t == pytest.approx(1.0 / 365.0, abs=1e-6)

    def test_already_past_expiry_returns_zero(self):
        now = datetime(2026, 5, 4, 17, 0, tzinfo=timezone.utc)
        expiry = datetime(2026, 5, 4, 16, 0, tzinfo=timezone.utc)
        assert time_to_expiry_years(now, expiry) == 0.0


class TestBuildProfile:
    def test_single_call_strike_negative_gex(self):
        # SpotGamma convention: dealer short calls → call OI contributes NEGATIVE
        c = [_contract(strike=6800, ctype="call", oi=1000, gamma=0.001)]
        p = build_profile(c, spot=6800, expiry=date(2026, 5, 4), time_to_expiry=1 / 365.0)
        assert len(p.strikes) == 1
        assert p.strikes[0].gex < 0
        # GEX = -1 × 1000 × 0.001 × 6800^2 × 100 = -4,624,000,000
        assert p.strikes[0].gex == pytest.approx(-1000 * 0.001 * 6800**2 * 100, rel=1e-9)

    def test_single_put_strike_positive_gex(self):
        # SpotGamma convention: dealer long puts → put OI contributes POSITIVE
        c = [_contract(strike=6700, ctype="put", oi=2000, gamma=0.001)]
        p = build_profile(c, spot=6800, expiry=date(2026, 5, 4), time_to_expiry=1 / 365.0)
        assert p.strikes[0].gex > 0

    def test_call_and_put_same_strike_aggregate(self):
        c = [
            _contract(strike=6800, ctype="call", oi=1000, gamma=0.001),
            _contract(strike=6800, ctype="put", oi=1500, gamma=0.001),
        ]
        p = build_profile(c, spot=6800, expiry=date(2026, 5, 4), time_to_expiry=1 / 365.0)
        assert len(p.strikes) == 1
        # Convention: GEX = (puts − calls) × γ × S² × 100. Net put-heavy → POSITIVE.
        expected = (1500 - 1000) * 0.001 * 6800**2 * 100
        assert p.strikes[0].gex == pytest.approx(expected)
        assert p.strikes[0].gex > 0

    def test_strikes_sorted_ascending(self):
        c = [
            _contract(strike=6900, ctype="call", oi=100, gamma=0.001),
            _contract(strike=6700, ctype="put", oi=100, gamma=0.001),
            _contract(strike=6800, ctype="call", oi=100, gamma=0.001),
        ]
        p = build_profile(c, spot=6800, expiry=date(2026, 5, 4), time_to_expiry=1 / 365.0)
        strikes = [sg.strike for sg in p.strikes]
        assert strikes == sorted(strikes)

    def test_zero_oi_dropped(self):
        c = [
            _contract(strike=6800, ctype="call", oi=0, gamma=0.001),
            _contract(strike=6810, ctype="call", oi=100, gamma=0.001),
        ]
        p = build_profile(c, spot=6800, expiry=date(2026, 5, 4), time_to_expiry=1 / 365.0)
        assert len(p.strikes) == 1
        assert p.strikes[0].strike == 6810

    def test_falls_back_to_bs_gamma_when_greeks_missing(self):
        # No 'greeks' key, only IV → BS gamma is computed
        c = [_contract(strike=6800, ctype="call", oi=1000, iv=0.18)]
        p = build_profile(c, spot=6800, expiry=date(2026, 5, 4), time_to_expiry=1 / 365.0)
        assert len(p.strikes) == 1
        # Calls negated under SpotGamma convention
        expected_gamma = black_scholes_gamma(6800, 6800, 0.18, 1 / 365.0)
        expected_gex = -1.0 * 1000 * expected_gamma * 6800**2 * 100
        assert p.strikes[0].gex == pytest.approx(expected_gex, rel=1e-9)

    def test_drops_contract_with_no_gamma_or_iv(self):
        c = [_contract(strike=6800, ctype="call", oi=100)]  # no greeks, no iv
        p = build_profile(c, spot=6800, expiry=date(2026, 5, 4), time_to_expiry=1 / 365.0)
        assert len(p.strikes) == 0


class TestProfileQueries:
    @pytest.fixture
    def profile(self):
        c = [
            _contract(6700, "put", 1000, gamma=0.001),
            _contract(6750, "put", 500, gamma=0.001),
            _contract(6800, "call", 2000, gamma=0.001),
            _contract(6850, "call", 100, gamma=0.001),
            _contract(6900, "call", 50, gamma=0.001),
        ]
        return build_profile(c, spot=6790, expiry=date(2026, 5, 4), time_to_expiry=1 / 365.0)

    def test_gex_at_known_strike(self, profile):
        # Convention: calls → negative, puts → positive
        assert profile.gex_at(6800) < 0  # call OI
        assert profile.gex_at(6700) > 0  # put OI

    def test_gex_at_unknown_strike(self, profile):
        assert profile.gex_at(7000) == 0.0

    def test_sum_gex_between_handles_reverse_args(self, profile):
        a = profile.sum_gex_between(6700, 6800)
        b = profile.sum_gex_between(6800, 6700)
        assert a == b

    def test_sum_gex_between_window(self, profile):
        # The call cluster above spot — under SpotGamma convention, calls are negative.
        s = profile.sum_gex_between(6800, 6900)
        assert s < 0
        assert s == pytest.approx(profile.gex_at(6800) + profile.gex_at(6850) + profile.gex_at(6900))

    def test_total_abs_gex(self, profile):
        t = profile.total_abs_gex()
        assert t == pytest.approx(sum(abs(sg.gex) for sg in profile.strikes))

    def test_negative_clusters_detect_call_wall(self, profile):
        # Under SpotGamma convention, a "call wall" (calls dominating above spot)
        # is a NEGATIVE cluster — dealer short gamma, accel zone.
        clusters = profile.negative_clusters(min_strength_pct=0.01)
        assert len(clusters) == 1
        assert clusters[0].strike_low == 6800
        assert clusters[0].strike_high == 6900
        assert clusters[0].sign == "negative"

    def test_positive_clusters_detect_put_wall(self, profile):
        # Put wall below spot is a POSITIVE cluster — dealer long gamma, decel.
        clusters = profile.positive_clusters(min_strength_pct=0.01)
        assert len(clusters) == 1
        assert clusters[0].strike_low == 6700
        assert clusters[0].strike_high == 6750
        assert clusters[0].sign == "positive"

    def test_strength_filter_removes_noise(self):
        # One big put-OI cluster (positive cluster under correct convention)
        # plus one tiny put-OI noise strike.
        c = [
            _contract(6800, "put", 100000, gamma=0.001),  # huge positive wall
            _contract(7100, "put", 5, gamma=0.001),       # tiny noise
        ]
        p = build_profile(c, spot=6800, expiry=date(2026, 5, 4), time_to_expiry=1 / 365.0)
        # min_strength_pct 5% drops the noise strike
        assert len(p.positive_clusters(min_strength_pct=0.05)) == 1


class TestFetchPolygonChain:
    def test_single_page(self):
        captured_urls = []

        def fake_http(url):
            captured_urls.append(url)
            return {
                "status": "OK",
                "results": [
                    _contract(6800, "call", 100, gamma=0.001),
                    _contract(6700, "put", 100, gamma=0.001),
                ],
            }

        out = fetch_polygon_chain(
            underlying="SPX",
            expiry=date(2026, 5, 4),
            api_key="TESTKEY",
            http_fetch=fake_http,
        )
        assert len(out) == 2
        assert len(captured_urls) == 1
        assert "expiration_date=2026-05-04" in captured_urls[0]
        assert "apiKey=TESTKEY" in captured_urls[0]

    def test_pagination_follows_next_url(self):
        responses = [
            {
                "status": "OK",
                "results": [_contract(6800, "call", 100, gamma=0.001)],
                "next_url": "https://api.polygon.io/v3/snapshot/options/SPX?cursor=abc",
            },
            {
                "status": "OK",
                "results": [_contract(6700, "put", 100, gamma=0.001)],
            },
        ]
        idx = {"i": 0}
        captured_urls = []

        def fake_http(url):
            captured_urls.append(url)
            r = responses[idx["i"]]
            idx["i"] += 1
            return r

        out = fetch_polygon_chain(
            underlying="SPX",
            expiry=date(2026, 5, 4),
            api_key="TESTKEY",
            http_fetch=fake_http,
        )
        assert len(out) == 2
        # Second URL should have apiKey appended even though next_url didn't include it
        assert "apiKey=TESTKEY" in captured_urls[1]
        assert "cursor=abc" in captured_urls[1]

    def test_max_pages_safety(self):
        # Always returns next_url → would loop forever without max_pages cap
        def fake_http(url):
            return {
                "status": "OK",
                "results": [_contract(6800, "call", 1, gamma=0.001)],
                "next_url": "https://api.polygon.io/v3/foo?cursor=x",
            }

        out = fetch_polygon_chain(
            underlying="SPX",
            expiry=date(2026, 5, 4),
            api_key="TESTKEY",
            http_fetch=fake_http,
            max_pages=3,
        )
        assert len(out) == 3

    def test_error_envelope_raises(self):
        def fake_http(url):
            return {"status": "ERROR", "error": "Unauthorized"}

        with pytest.raises(ValueError, match="polygon"):
            fetch_polygon_chain(
                underlying="SPX",
                expiry=date(2026, 5, 4),
                api_key="BADKEY",
                http_fetch=fake_http,
            )


# ---------------------------------------------------------------------------
# Per-contract snapshot + 2-pass hydration (Polygon Starter Greeks workaround)
# ---------------------------------------------------------------------------


class TestFetchPerContractSnapshot:
    def test_returns_results_block(self):
        def fake_http(url):
            assert "I:SPX/O:SPXW260508C06800000" in url
            assert "apiKey=KEY" in url
            return {
                "status": "OK",
                "results": {
                    "details": {"strike_price": 6800, "contract_type": "call",
                                "ticker": "O:SPXW260508C06800000"},
                    "open_interest": 1058,
                    "greeks": {"gamma": 0.0009, "delta": 0.55},
                    "implied_volatility": 0.21,
                },
            }
        r = fetch_per_contract_snapshot(
            underlying="SPX", ticker="O:SPXW260508C06800000",
            api_key="KEY", http_fetch=fake_http,
        )
        assert r is not None
        assert r["greeks"]["gamma"] == 0.0009
        assert r["implied_volatility"] == 0.21

    def test_returns_none_on_error(self):
        def fake_http(url):
            return {"status": "ERROR", "error": "Not found"}
        r = fetch_per_contract_snapshot(
            underlying="SPX", ticker="O:SPXW260508C06800000",
            api_key="KEY", http_fetch=fake_http,
        )
        assert r is None

    def test_returns_none_on_exception(self):
        def fake_http(url):
            raise ConnectionError("polygon down")
        r = fetch_per_contract_snapshot(
            underlying="SPX", ticker="O:SPXW260508C06800000",
            api_key="KEY", http_fetch=fake_http,
        )
        assert r is None


class TestFetchPolygonChainWithGreeks:
    def _make_chain_response(self, contracts):
        """Wrap a list of contracts into a single chain-page response."""
        return {"status": "OK", "results": contracts}

    def _chain_contract(self, strike, ctype, oi, ticker=None):
        return {
            "details": {
                "strike_price": strike,
                "contract_type": ctype,
                "ticker": ticker or f"O:SPXW260508{ctype[0].upper()}{int(strike*1000):08d}",
            },
            "open_interest": oi,
            # No greeks / iv — Starter chain endpoint behavior
        }

    def _per_contract_response(self, ticker, oi, gamma=0.001, iv=0.20):
        return {
            "status": "OK",
            "results": {
                "details": {"ticker": ticker},
                "open_interest": oi,
                "greeks": {"gamma": gamma, "delta": 0.5},
                "implied_volatility": iv,
            },
        }

    def test_two_pass_hydrates_liquid_strikes(self):
        from datetime import date
        # Chain returns 4 contracts: 2 liquid (OI > threshold), 2 illiquid
        chain = [
            self._chain_contract(6800, "call", oi=1000, ticker="A"),
            self._chain_contract(6810, "call", oi=200, ticker="B"),
            self._chain_contract(6820, "call", oi=10, ticker="C"),  # below 50 threshold
            self._chain_contract(6830, "call", oi=30, ticker="D"),  # below threshold
        ]
        per_contract_calls = []

        def fake_http(url):
            if "expiration_date=" in url:
                # Chain endpoint
                return self._make_chain_response(chain)
            # Per-contract endpoint
            for t in ("A", "B", "C", "D"):
                if f"/{t}?" in url:
                    per_contract_calls.append(t)
                    return self._per_contract_response(t, oi=1000, gamma=0.0012, iv=0.21)
            return {"status": "ERROR"}

        result = fetch_polygon_chain_with_greeks(
            underlying="SPX", expiry=date(2026, 5, 8),
            api_key="KEY", http_fetch=fake_http,
            oi_threshold=50, spot=None,
        )
        # Only A and B should have been hydrated (OI >= 50); C and D skipped
        assert sorted(per_contract_calls) == ["A", "B"]
        # All 4 contracts in result, but only A/B have greeks
        assert len(result) == 4
        a = next(c for c in result if c["details"]["ticker"] == "A")
        c_skipped = next(c for c in result if c["details"]["ticker"] == "C")
        assert a.get("greeks") is not None
        assert a.get("implied_volatility") == 0.21
        assert c_skipped.get("greeks") is None  # not hydrated

    def test_spot_window_filter(self):
        from datetime import date
        # Chain has strikes far from spot — they should be skipped
        chain = [
            self._chain_contract(6800, "call", oi=1000, ticker="ATM"),
            self._chain_contract(8000, "call", oi=1000, ticker="FAR"),  # 17% above 6800
        ]
        per_contract_calls = []

        def fake_http(url):
            if "expiration_date=" in url:
                return self._make_chain_response(chain)
            for t in ("ATM", "FAR"):
                if f"/{t}?" in url:
                    per_contract_calls.append(t)
                    return self._per_contract_response(t, oi=1000)
            return {"status": "ERROR"}

        fetch_polygon_chain_with_greeks(
            underlying="SPX", expiry=date(2026, 5, 8),
            api_key="KEY", http_fetch=fake_http,
            oi_threshold=50, spot=6800, spot_window_pct=0.05,
        )
        # Only ATM was within ±5% of spot
        assert per_contract_calls == ["ATM"]

    def test_max_contracts_cap(self):
        from datetime import date
        # 100 liquid contracts, all near spot — cap should fire
        chain = [
            self._chain_contract(6800 + i * 5, "call", oi=1000, ticker=f"T{i}")
            for i in range(100)
        ]
        per_contract_calls = []

        def fake_http(url):
            if "expiration_date=" in url:
                return self._make_chain_response(chain)
            per_contract_calls.append(url.split("/")[-1].split("?")[0])
            ticker = url.split("/")[-1].split("?")[0]
            return self._per_contract_response(ticker, oi=1000)

        fetch_polygon_chain_with_greeks(
            underlying="SPX", expiry=date(2026, 5, 8),
            api_key="KEY", http_fetch=fake_http,
            oi_threshold=50, spot=6800, spot_window_pct=0.50,  # generous window
            max_contracts_to_hydrate=10,
        )
        assert len(per_contract_calls) == 10

    def test_per_contract_failure_does_not_break_chain(self):
        from datetime import date
        chain = [
            self._chain_contract(6800, "call", oi=1000, ticker="OK"),
            self._chain_contract(6810, "call", oi=1000, ticker="FAIL"),
        ]

        def fake_http(url):
            if "expiration_date=" in url:
                return self._make_chain_response(chain)
            if "/FAIL?" in url:
                return {"status": "ERROR", "error": "Not found"}
            return self._per_contract_response("OK", oi=1000)

        result = fetch_polygon_chain_with_greeks(
            underlying="SPX", expiry=date(2026, 5, 8),
            api_key="KEY", http_fetch=fake_http,
            oi_threshold=50, spot=None,
        )
        ok = next(c for c in result if c["details"]["ticker"] == "OK")
        fail = next(c for c in result if c["details"]["ticker"] == "FAIL")
        assert ok.get("greeks") is not None
        assert fail.get("greeks") is None  # hydration failed cleanly

    def test_empty_chain_returns_empty(self):
        from datetime import date
        def fake_http(url):
            return self._make_chain_response([])
        result = fetch_polygon_chain_with_greeks(
            underlying="SPX", expiry=date(2026, 5, 8),
            api_key="KEY", http_fetch=fake_http,
        )
        assert result == []


class TestBuildProfileDeltas:
    """build_profile should preserve per-strike per-side delta from greeks."""

    def test_delta_captured_alongside_gex(self):
        # 3 strikes, both sides, all with greeks (delta + gamma)
        contracts = [
            _contract(7250, "put",  oi=1000, gamma=0.0008, delta=-0.05),
            _contract(7300, "put",  oi=1000, gamma=0.0010, delta=-0.10),
            _contract(7350, "put",  oi=1000, gamma=0.0012, delta=-0.30),
            _contract(7350, "call", oi=1000, gamma=0.0012, delta=+0.70),
            _contract(7400, "call", oi=1000, gamma=0.0010, delta=+0.30),
            _contract(7450, "call", oi=1000, gamma=0.0008, delta=+0.10),
        ]
        prof = build_profile(
            contracts, spot=7350, expiry=date(2026, 5, 8), time_to_expiry=1 / 365.0,
        )
        # All 6 contracts should be in deltas (preserved regardless of GEX).
        assert len(prof.deltas) == 6
        # Spot-check a put and a call.
        put_7300 = next(d for d in prof.deltas if d.strike == 7300 and d.contract_type == "put")
        assert put_7300.delta == pytest.approx(-0.10)
        call_7400 = next(d for d in prof.deltas if d.strike == 7400 and d.contract_type == "call")
        assert call_7400.delta == pytest.approx(+0.30)

    def test_delta_none_when_greeks_missing(self):
        # OI present, IV present, but no delta — should preserve None
        contracts = [
            _contract(7300, "put", oi=1000, iv=0.18),  # no greeks dict
        ]
        prof = build_profile(
            contracts, spot=7350, expiry=date(2026, 5, 8), time_to_expiry=1 / 365.0,
        )
        assert len(prof.deltas) == 1
        assert prof.deltas[0].delta is None

    def test_zero_oi_still_recorded_in_deltas(self):
        # Zero OI is dropped from gex aggregation but deltas should still
        # carry it — we want full chain shape for delta-target lookups.
        contracts = [
            _contract(7300, "put", oi=0, gamma=0.001, delta=-0.10),
            _contract(7400, "call", oi=0, gamma=0.001, delta=+0.10),
        ]
        prof = build_profile(
            contracts, spot=7350, expiry=date(2026, 5, 8), time_to_expiry=1 / 365.0,
        )
        assert len(prof.strikes) == 0  # zero OI — not in GEX
        assert len(prof.deltas) == 2  # but kept in deltas


class TestFindStrikeAtDelta:
    """Brandon-faithful strike selection — find the strike at target delta."""

    def _profile_with(self, deltas):
        """Build a GEXProfile from a list of (strike, side, delta) tuples."""
        return GEXProfile(
            spot=7350.0,
            expiry=date(2026, 5, 8),
            fetched_at=datetime.now(timezone.utc),
            strikes=tuple(),
            deltas=tuple(StrikeDelta(strike=s, contract_type=t, delta=d) for s, t, d in deltas),
        )

    def test_finds_closest_put_to_target_delta(self):
        # Targeting 8δ put. Strike 7290 has |delta| 0.07, closest to 0.08.
        prof = self._profile_with([
            (7250, "put", -0.04),
            (7290, "put", -0.07),
            (7310, "put", -0.12),
            (7330, "put", -0.20),
        ])
        result = find_strike_at_delta(prof, side="put", target_delta_abs=0.08)
        assert result == 7290.0

    def test_finds_closest_call_to_target_delta(self):
        # Targeting 8δ call.
        prof = self._profile_with([
            (7400, "call", +0.20),
            (7430, "call", +0.12),
            (7460, "call", +0.07),
            (7490, "call", +0.03),
        ])
        result = find_strike_at_delta(prof, side="call", target_delta_abs=0.08)
        assert result == 7460.0

    def test_snaps_to_5pt_grid(self):
        # If chain has off-grid strikes (rare but possible), snap to nearest 5.
        prof = self._profile_with([(7287.5, "put", -0.08)])
        result = find_strike_at_delta(prof, side="put", target_delta_abs=0.08)
        # Python's round() uses banker's rounding: 7287.5/5=1457.5 → 1458
        # (nearest even) → 7290.
        assert result == 7290.0

    def test_excludes_wrong_side_of_spot(self):
        # A put with stale data showing positive delta above spot should not
        # be selected. spot=7350; put at 7400 ignored.
        prof = self._profile_with([
            (7300, "put", -0.08),  # below spot, valid
            (7400, "put", -0.08),  # above spot, gating block excludes
        ])
        result = find_strike_at_delta(prof, side="put", target_delta_abs=0.08)
        assert result == 7300.0

    def test_returns_none_when_no_deltas_on_side(self):
        # Only puts in chain — call request returns None
        prof = self._profile_with([(7300, "put", -0.08)])
        assert find_strike_at_delta(prof, side="call", target_delta_abs=0.08) is None

    def test_returns_none_when_all_deltas_missing(self):
        # Side present but every contract has delta=None
        prof = self._profile_with([(7300, "put", None), (7290, "put", None)])
        assert find_strike_at_delta(prof, side="put", target_delta_abs=0.08) is None

    def test_rejects_invalid_target_delta(self):
        prof = self._profile_with([(7300, "put", -0.08)])
        with pytest.raises(ValueError):
            find_strike_at_delta(prof, side="put", target_delta_abs=0.0)
        with pytest.raises(ValueError):
            find_strike_at_delta(prof, side="put", target_delta_abs=1.5)

    def test_rejects_invalid_side(self):
        prof = self._profile_with([(7300, "put", -0.08)])
        with pytest.raises(ValueError):
            find_strike_at_delta(prof, side="straddle", target_delta_abs=0.08)

    def test_brandon_real_world_2026_05_07_scenario(self):
        # Reproduce yesterday's setup: spot ~7345, GEX wall at 7330. With
        # delta-target the bot should pick a put short well below the wall
        # (high probability of expiring OTM), NOT 7340 like the tightener
        # walked it to.
        deltas = [
            (7250, "put", -0.04),  # 8δ candidate
            (7270, "put", -0.06),
            (7280, "put", -0.08),  # closest to target
            (7300, "put", -0.14),
            (7320, "put", -0.22),
            (7330, "put", -0.30),  # right at the wall — DEFINITELY not 8δ
            (7340, "put", -0.42),  # what the tightener picked yesterday
        ]
        prof = GEXProfile(
            spot=7345.0,
            expiry=date(2026, 5, 8),
            fetched_at=datetime.now(timezone.utc),
            strikes=tuple(),
            deltas=tuple(StrikeDelta(strike=s, contract_type=t, delta=d) for s, t, d in deltas),
        )
        result = find_strike_at_delta(prof, side="put", target_delta_abs=0.08)
        # Brandon: 8δ short = 7280, 50pt below the wall and well outside it.
        assert result == 7280.0
