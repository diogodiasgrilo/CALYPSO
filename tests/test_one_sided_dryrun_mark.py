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

    def test_full_ic_fallback_total_value_near_credit_not_7x(self):
        # The same units bug lived in the full-IC fallback (total_credit/200) and
        # surfaced when a restart-recovery dropped a full IC's conids (the −$1378
        # regression). Total IC value should start ≈ total credit, not ~7×.
        s = HydraStrategy.__new__(HydraStrategy)
        s.contracts_per_entry = 10
        e = HydraIronCondorEntry(entry_number=1)
        e.put_only = False
        e.call_only = False
        e.call_spread_credit = 200.0
        e.put_spread_credit = 200.0   # total_credit = $400
        e.contracts = 10
        e.short_call_strike = 7480.0
        e.long_call_strike = 7485.0
        e.short_put_strike = 7320.0
        e.long_put_strike = 7315.0
        e.entry_time = get_us_market_time()
        s._simulate_hydra_entry_prices(e)
        total = e.call_spread_value + e.put_spread_value
        assert 0.0 <= total <= 430.0, f"expected ~$400, got ${total:.0f}"  # not ~$2800


class TestConidReResolve:
    """_repopulate_dry_conids: recovered / legacy entries get their conids back
    from the persisted strikes so the heartbeat marks from REAL quotes."""

    def _ns(self, **kw):
        base = dict(
            short_call_strike=0.0, long_call_strike=0.0,
            short_put_strike=0.0, long_put_strike=0.0,
            short_call_uic=0, long_call_uic=0, short_put_uic=0, long_put_uic=0,
        )
        base.update(kw)
        return types.SimpleNamespace(**base)

    def test_resolves_all_missing_conids_from_strikes(self):
        s = _strat()
        e = self._ns(short_call_strike=7480.0, long_call_strike=7475.0,
                     short_put_strike=7325.0, long_put_strike=7320.0)
        s._repopulate_dry_conids(e)
        assert (e.short_call_uic, e.long_call_uic) == (211, 212)
        assert (e.short_put_uic, e.long_put_uic) == (111, 112)

    def test_skips_inactive_side(self):
        # put-only: call strikes 0 → call legs left untouched
        s = _strat()
        e = self._ns(short_put_strike=7325.0, long_put_strike=7320.0,
                     short_put_uic=None, long_put_uic=None)
        s._repopulate_dry_conids(e)
        assert (e.short_call_uic, e.long_call_uic) == (0, 0)
        assert (e.short_put_uic, e.long_put_uic) == (111, 112)

    def test_skips_already_resolved(self):
        s = _strat()
        s._get_option_uic = lambda *a: (_ for _ in ()).throw(AssertionError("called"))
        e = self._ns(short_put_strike=7325.0, long_put_strike=7320.0,
                     short_put_uic=999, long_put_uic=998)
        s._repopulate_dry_conids(e)  # all set / inactive → _get_option_uic unused
        assert e.short_put_uic == 999

    def test_no_expiry_is_noop(self):
        s = _strat()
        s._get_todays_expiry = lambda: None
        e = self._ns(short_put_strike=7325.0, long_put_strike=7320.0,
                     short_put_uic=None, long_put_uic=None)
        s._repopulate_dry_conids(e)
        assert e.short_put_uic is None


class TestDryRunBrokerPnlGuard:
    """Fix 4 (2026-06-23): a DRY-RUN bot shares the IBKR account with the LIVE
    variant (C). `_get_broker_pnl_for_entry` must NOT match a simulated entry's
    conids against that shared account — it would return C's real (losing) P&L.
    Dry-run returns the SIMULATED mark (`entry.unrealized_pnl`) instead."""

    def _entry(self):
        # put-only entry: short_put 7325 / long_put 7320, conids overlap C's
        return types.SimpleNamespace(
            entry_number=1, call_side_stopped=True, put_side_stopped=False,
            short_call_uic=None, long_call_uic=None,
            short_put_uic=111, long_put_uic=112,
            unrealized_pnl=75.0,   # the simulated mark
        )

    def test_dry_run_ignores_shared_account_positions(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.dry_run = True
        # C's real losing positions sit at the SAME conids in the shared account
        s._read_open_positions = lambda: [
            {"instrument_id": 111, "unrealized_pnl": -1450.0},
            {"instrument_id": 112, "unrealized_pnl": -2.0},
        ]
        assert s._get_broker_pnl_for_entry(self._entry()) == 75.0  # sim mark, NOT −1452

    def test_live_still_reads_broker(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.dry_run = False
        s._read_open_positions = lambda: [
            {"instrument_id": 111, "unrealized_pnl": -1450.0},
            {"instrument_id": 112, "unrealized_pnl": -2.0},
        ]
        # live: real broker P&L of the entry's (own) positions
        assert s._get_broker_pnl_for_entry(self._entry()) == -1452.0
