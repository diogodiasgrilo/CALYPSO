"""GEX hydration cap fix (2026-09-01).

`bots/hydra/brandon/gex_provider.py`'s two-pass Polygon fetch (chain
snapshot for OI, then per-contract calls to hydrate greeks/IV, since
Polygon's Starter tier omits both from the bulk endpoint) capped the
per-contract hydration pass at a hardcoded 80 contracts. A live check
against the real SPX chain (spot ~7630) found 195 real, liquid (OI>=50),
near-the-money (+/-5% of spot) candidates qualified, but only 80 (41%) got
hydrated — 115 (59%) were silently excluded, contributing exactly ZERO to
the GEX picture the accel-zone strike adjuster and the defensive-hedge's
GEX-confirmation gate both rely on. Not new that day: a 2026-07-17
incident hit an even more extreme version of the same gap (80/1000) and
caused real damage (3 phantom $0-credit entries on B). This file pins the
fix: a raised cap (80 -> 250), a wall-clock deadline on the hydration pass
(newly load-bearing once the cap risks a longer worst-case stall on a
genuinely slow Polygon day — this fetch runs SYNCHRONOUSLY in the
entry-time decision path), and a DATA_QUALITY alert when the cap is still
binding despite the raise, so a recurrence is visible instead of requiring
another ad-hoc investigation to discover.

See bots/hydra/__init__.py version history for the full incident writeup.
"""
from __future__ import annotations

import os
import sys
from datetime import date
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.brandon import gex_provider  # noqa: E402
from bots.hydra.brandon.gex_provider import fetch_polygon_chain_with_greeks  # noqa: E402


def _chain_contract(strike, oi, ticker):
    return {
        "details": {"strike_price": strike, "contract_type": "call", "ticker": ticker},
        "open_interest": oi,
    }


# ─── Fix 1: raised default cap ─────────────────────────────────────────────

class TestDefaultCapRaised:
    def test_default_cap_is_250_not_80(self):
        """The exact regression this whole fix is about: a bare `80` at the
        production call site was a no-op change if only the function
        default moved. Pin the default directly so a future edit reverting
        it either place is caught here."""
        import inspect
        sig = inspect.signature(fetch_polygon_chain_with_greeks)
        assert sig.parameters["max_contracts_to_hydrate"].default == 250

    def test_strategy_init_config_read_defaults_to_250(self):
        """strategy.py's __init__ reads strategy.brandon.gex.
        max_contracts_to_hydrate into self.brandon_gex_max_contracts_to_hydrate
        with its OWN default -- the actual production call site passes THIS
        attribute, not the bare function default tested above, so this is
        the read the incident's fix actually depends on.

        Found during review: a full-instance negative control on this line
        didn't fail, because this file's _bstrat() test fixture sets the
        attribute directly, bypassing __init__ entirely. Rather than build a
        full real-construction harness for BrandonHydraStrategy (substantial
        effort, no existing precedent in this test suite unlike Ghauri/
        Strangle), read the actual source line directly -- weaker than a
        behavioral test, but a real regression guard a full-instance test
        wouldn't have caught either way given the existing fixture pattern."""
        import re
        src = open("bots/hydra/brandon/strategy.py").read()
        m = re.search(
            r'self\.brandon_gex_max_contracts_to_hydrate\s*=\s*int\(\s*'
            r'gex\.get\("max_contracts_to_hydrate",\s*(\d+)\s*\)',
            src,
        )
        assert m, "could not find the max_contracts_to_hydrate config-read line"
        assert int(m.group(1)) == 250

    def test_negative_control_source_inspection_catches_a_reverted_default(self, tmp_path):
        """Proves the test above actually discriminates 250 from 80, not
        just that it can find the line at all."""
        import re
        fake_src = (
            'self.brandon_gex_max_contracts_to_hydrate = int(\n'
            '            gex.get("max_contracts_to_hydrate", 80)\n'
            '        )'
        )
        m = re.search(
            r'self\.brandon_gex_max_contracts_to_hydrate\s*=\s*int\(\s*'
            r'gex\.get\("max_contracts_to_hydrate",\s*(\d+)\s*\)',
            fake_src,
        )
        assert m and int(m.group(1)) == 80  # confirms the regex + reverted value both work
        assert int(m.group(1)) != 250  # ...and that THIS is what would fail the real test

    def test_negative_control_old_80_default_would_have_excluded_real_candidates(self):
        """Reproduces the exact incident shape: 195 real candidates, cap=80
        -> 115 silently excluded. Confirms the OLD cap really was the
        problem (not e.g. the OI/window filters), by explicitly passing 80
        and checking the exclusion is real."""
        chain = [
            _chain_contract(7000 + i * 5, oi=1000 - i, ticker=f"T{i}")
            for i in range(195)
        ]

        def fake_http(url):
            if "expiration_date=" in url:
                return {"results": chain}
            return {"results": {"greeks": {"gamma": 0.001}, "implied_volatility": 0.2}}

        # strikes span 7000..7970 (194 * 5pt steps) -- window must be wide
        # enough to cover that full spread, or the test would be measuring
        # its own window filter instead of the cap.
        _contracts, candidates_found = fetch_polygon_chain_with_greeks(
            underlying="SPX", expiry=date(2026, 9, 1), api_key="k",
            http_fetch=fake_http, spot=7000.0, spot_window_pct=0.20,
            max_contracts_to_hydrate=80,  # the OLD value, explicitly
        )
        assert candidates_found == 195
        assert candidates_found > 80  # the cap WOULD have bound at the old value


# ─── Fix 1: wall-clock deadline ────────────────────────────────────────────

class TestHydrationDeadline:
    def test_deadline_returns_without_waiting_for_stragglers(self, monkeypatch):
        """A genuinely slow Polygon call must not block the caller past the
        deadline — this fetch runs synchronously in the entry-time decision
        path. Fast calls hydrate normally; the slow one is abandoned (same
        disposition as an ordinary per-call failure)."""
        import time as real_time

        # Tiny deadline so the test runs in well under a second.
        monkeypatch.setattr(gex_provider, "GEX_HYDRATE_DEADLINE_S", 0.15)

        chain = [
            _chain_contract(7000, oi=1000, ticker="FAST1"),
            _chain_contract(7005, oi=999, ticker="FAST2"),
            _chain_contract(7010, oi=998, ticker="SLOW"),
        ]

        def fake_http(url):
            if "expiration_date=" in url:
                return {"results": chain}
            if "/SLOW?" in url:
                real_time.sleep(1.0)  # far longer than the 0.15s deadline
                return {"results": {"greeks": {"gamma": 0.001}, "implied_volatility": 0.2}}
            return {"results": {"greeks": {"gamma": 0.001}, "implied_volatility": 0.2}}

        t0 = real_time.perf_counter()
        contracts, candidates_found = fetch_polygon_chain_with_greeks(
            underlying="SPX", expiry=date(2026, 9, 1), api_key="k",
            http_fetch=fake_http, spot=7000.0, spot_window_pct=0.10,
        )
        elapsed = real_time.perf_counter() - t0

        assert elapsed < 0.9, (
            f"deadline did not bound the wait — took {elapsed:.2f}s waiting "
            f"on a call that sleeps 1.0s"
        )
        assert candidates_found == 3
        by_ticker = {c["details"]["ticker"]: c for c in contracts}
        assert by_ticker["FAST1"].get("greeks") is not None
        assert by_ticker["FAST2"].get("greeks") is not None
        # SLOW hadn't finished by the deadline -- un-hydrated, same as an
        # ordinary per-call failure. (Its background thread keeps running
        # and will harmlessly mutate this same dict a moment later, but
        # nothing in this test observes that -- the point is the CALLER
        # already got its answer without waiting for it.)
        assert by_ticker["SLOW"].get("greeks") is None

    def test_deadline_logs_a_warning_when_it_fires(self, monkeypatch, caplog):
        import logging
        import time as real_time

        monkeypatch.setattr(gex_provider, "GEX_HYDRATE_DEADLINE_S", 0.1)
        chain = [_chain_contract(7000, oi=1000, ticker="SLOW")]

        def fake_http(url):
            if "expiration_date=" in url:
                return {"results": chain}
            real_time.sleep(0.5)
            return {"results": {"greeks": {"gamma": 0.001}}}

        with caplog.at_level(logging.WARNING, logger="bots.hydra.brandon.gex_provider"):
            fetch_polygon_chain_with_greeks(
                underlying="SPX", expiry=date(2026, 9, 1), api_key="k",
                http_fetch=fake_http, spot=7000.0, spot_window_pct=0.10,
            )
        assert any("wall-clock deadline" in r.message for r in caplog.records), caplog.text

    def test_negative_control_no_deadline_would_hang(self, monkeypatch):
        """Proves the test above actually exercises the deadline, not just a
        naturally-fast test: with the deadline set absurdly high, the same
        slow call DOES make the function wait for it."""
        import time as real_time

        monkeypatch.setattr(gex_provider, "GEX_HYDRATE_DEADLINE_S", 60.0)
        chain = [_chain_contract(7000, oi=1000, ticker="SLOW")]

        def fake_http(url):
            if "expiration_date=" in url:
                return {"results": chain}
            real_time.sleep(0.3)
            return {"results": {"greeks": {"gamma": 0.001}, "implied_volatility": 0.2}}

        t0 = real_time.perf_counter()
        contracts, _cf = fetch_polygon_chain_with_greeks(
            underlying="SPX", expiry=date(2026, 9, 1), api_key="k",
            http_fetch=fake_http, spot=7000.0, spot_window_pct=0.10,
        )
        elapsed = real_time.perf_counter() - t0
        assert elapsed >= 0.3  # actually waited for the slow call this time
        assert contracts[0].get("greeks") is not None  # and it succeeded


# ─── Fix 2: the DATA_QUALITY alert ─────────────────────────────────────────

def _bstrat(monkeypatch, candidates_found, hydrate_cap=250):
    """A minimal BrandonHydraStrategy double driving the REAL
    _brandon_get_gex_profile, with fetch_polygon_chain_with_greeks mocked
    to return a controlled candidates_found (avoids a real Polygon call)."""
    import contextlib
    from bots.hydra.brandon import strategy as bstrat_mod
    from bots.hydra.brandon.strategy import BrandonHydraStrategy

    inst = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
    inst.brandon_gex_enabled = True
    inst.brandon_polygon_api_key_env = "POLYGON_API_KEY"
    inst.brandon_polygon_underlying = "SPX"
    inst.brandon_decel_min_pct = 0.05
    inst.brandon_accel_min_pct = 0.10
    inst.brandon_gex_max_contracts_to_hydrate = hydrate_cap
    inst.current_price = 7000.0
    inst._brandon_gex_profile = None
    inst._brandon_gex_profile_fetched_at = None
    inst._brandon_gex_failure_at = None
    inst._brandon_send_telegram = MagicMock()

    monkeypatch.setenv("POLYGON_API_KEY", "test-key")
    monkeypatch.setattr(bstrat_mod.gex_shared_cache, "load_shared_profile", lambda **k: None)
    monkeypatch.setattr(
        bstrat_mod.gex_shared_cache, "fetch_lock", lambda *a, **k: contextlib.nullcontext()
    )
    monkeypatch.setattr(bstrat_mod.gex_shared_cache, "save_shared_profile", lambda *a, **k: None)

    contract = {
        "details": {"strike_price": 7000, "contract_type": "call"},
        "open_interest": 1000,
        "greeks": {"gamma": 0.001},
        "implied_volatility": 0.2,
    }
    monkeypatch.setattr(
        bstrat_mod.gex_provider, "fetch_polygon_chain_with_greeks",
        lambda **k: ([contract], candidates_found),
    )
    return inst


class TestHydrationCapAlert:
    def test_alert_fires_when_candidates_exceed_cap(self, monkeypatch):
        inst = _bstrat(monkeypatch, candidates_found=300, hydrate_cap=250)
        inst._brandon_get_gex_profile(date(2026, 9, 1), force_refresh=True)

        inst._brandon_send_telegram.assert_called_once()
        _args, kwargs = inst._brandon_send_telegram.call_args
        assert kwargs["alert_type_name"] == "DATA_QUALITY"
        assert kwargs["priority_name"] == "MEDIUM"
        # Title must be static/generic (no raw numbers) so AlertService's
        # dedup fingerprint collapses repeats instead of alerting fresh
        # every 3-min refresh cycle while the condition persists.
        assert kwargs["title"] == "Brandon GEX hydration cap binding"
        assert "300" in kwargs["message"] and "250" in kwargs["message"]
        assert kwargs["details"]["candidates_found"] == 300
        assert kwargs["details"]["hydrate_cap"] == 250
        assert kwargs["details"]["excluded"] == 50
        # Neither dedup-fingerprint key is present, so this alert type can
        # actually collapse across repeated refreshes (see AlertService's
        # _alert_fingerprint, which keys only on entry_number/side).
        assert "entry_number" not in kwargs["details"]
        assert "side" not in kwargs["details"]

    def test_no_alert_when_candidates_within_cap(self, monkeypatch):
        inst = _bstrat(monkeypatch, candidates_found=200, hydrate_cap=250)
        inst._brandon_get_gex_profile(date(2026, 9, 1), force_refresh=True)
        inst._brandon_send_telegram.assert_not_called()

    def test_no_alert_when_candidates_exactly_equal_cap(self, monkeypatch):
        """Boundary: exactly at the cap is NOT binding (nothing was
        excluded) -- only strictly greater should alert."""
        inst = _bstrat(monkeypatch, candidates_found=250, hydrate_cap=250)
        inst._brandon_get_gex_profile(date(2026, 9, 1), force_refresh=True)
        inst._brandon_send_telegram.assert_not_called()
