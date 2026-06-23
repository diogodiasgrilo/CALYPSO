"""One-sided (put-only / call-only) dry-run marking — 2026-06-23 fix.

The reported B bug: put-only dry-run entries showed a deep-negative P&L even
while SPX moved AWAY from the short (so they should have been profitable). Root
cause: `_simulate_put_spread_only` / `_simulate_call_spread_only` never populated
the leg conids, so the heartbeat couldn't fetch real quotes and fell back to
`_simulate_hydra_entry_prices` — a moneyness-blind model whose units bug made the
spread VALUE start at ~7× the credit (instant deep-negative P&L that then only
time-decayed).

Fix 1: the one-sided sims now populate the active side's conids + the real
estimated credit (so the heartbeat marks from real quotes, like a full IC).
Fix 2: the fallback's initial price now makes the spread value start at the
credit (not 7×), so the rare no-conid case re-marks sanely.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.strategy import HydraStrategy, HydraIronCondorEntry  # noqa: E402
from shared.market_hours import get_us_market_time  # noqa: E402


_CONIDS = {7325.0: 111, 7320.0: 112, 7480.0: 211, 7475.0: 212}


def _strat():
    s = HydraStrategy.__new__(HydraStrategy)
    s.contracts_per_entry = 10
    s.current_vix = 18.0
    s._get_todays_expiry = lambda: "2026-06-23"
    s._get_option_uic = lambda strike, right, expiry: _CONIDS.get(float(strike), 999)
    s._get_vix_adjusted_spread_width = lambda vix, side: 5.0
    return s


# ── Fix 1: the one-sided sims populate conids + real credit ───────────────────

class TestPutOnlySim:
    def _entry(self):
        return types.SimpleNamespace(
            entry_number=1, put_only=True, call_only=False,
            short_put_strike=7325.0, long_put_strike=7320.0,
            short_put_uic=None, long_put_uic=None,
            put_spread_credit=0.0, call_spread_credit=0.0,
            short_put_position_id=None, long_put_position_id=None,
        )

    def test_populates_conids_and_real_credit(self):
        s = _strat()
        s._estimate_entry_credit = lambda e: (0.0, 14.0)  # est_put per-contract
        e = self._entry()
        assert s._simulate_put_spread_only(e) is True
        assert e.short_put_uic == 111 and e.long_put_uic == 112   # real conids
        assert e.put_spread_credit == 140.0                       # 14 × 10 contracts
        assert e.call_spread_credit == 0
        assert e.short_put_position_id and e.long_put_position_id

    def test_crude_credit_fallback_when_estimate_zero(self):
        s = _strat()
        s._estimate_entry_credit = lambda e: (0.0, 0.0)  # estimate failed
        e = self._entry()
        s._simulate_put_spread_only(e)
        # falls back to width × 2.5% × 100 × contracts = 5 × 0.025 × 100 × 10
        assert e.put_spread_credit == 125.0
        # conids still attempted (real), credit just used the fallback
        assert e.short_put_uic == 111

    def test_no_expiry_uses_fallback_credit_and_leaves_conids_none(self):
        s = _strat()
        s._get_todays_expiry = lambda: None
        s._estimate_entry_credit = lambda e: (0.0, 14.0)
        e = self._entry()
        s._simulate_put_spread_only(e)
        assert e.put_spread_credit == 125.0   # crude fallback
        assert e.short_put_uic is None         # no expiry → no conid lookup


class TestCallOnlySim:
    def _entry(self):
        return types.SimpleNamespace(
            entry_number=2, call_only=True, put_only=False,
            short_call_strike=7480.0, long_call_strike=7475.0,
            short_call_uic=None, long_call_uic=None,
            call_spread_credit=0.0, put_spread_credit=0.0,
            short_call_position_id=None, long_call_position_id=None,
        )

    def test_populates_conids_and_real_credit(self):
        s = _strat()
        s._estimate_entry_credit = lambda e: (8.0, 0.0)  # est_call per-contract
        e = self._entry()
        assert s._simulate_call_spread_only(e) is True
        assert e.short_call_uic == 211 and e.long_call_uic == 212
        assert e.call_spread_credit == 80.0   # 8 × 10
        assert e.put_spread_credit == 0


# ── Fix 2: the fallback math no longer starts at 7× the credit ────────────────

class TestFallbackMark:
    def _hydra_entry(self, *, credit=125.0, contracts=10):
        e = HydraIronCondorEntry(entry_number=1)  # proper ctor inits the legs
        e.put_only = True
        e.call_only = False
        e.put_spread_credit = credit
        e.contracts = contracts
        e.short_put_strike = 7325.0
        e.long_put_strike = 7320.0
        e.entry_time = get_us_market_time()  # ~now → decay ≈ 1
        return e

    def test_put_only_value_starts_near_credit_not_7x(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.contracts_per_entry = 10
        e = self._hydra_entry(credit=125.0, contracts=10)
        s._simulate_hydra_entry_prices(e)
        v = e.put_spread_value  # = (short − long) × 100 × contracts, clamped
        # The OLD bug started this at ~$875 (= 7× the $125 credit). The fix makes
        # it start at ≈ the credit and decay.
        assert 0.0 <= v <= 130.0, f"expected ~credit ($125), got ${v:.0f}"
        # ...and it is NOT the old 7× value.
        assert v < 300.0
