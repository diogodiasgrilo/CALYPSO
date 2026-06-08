"""Safety-net regression tests for HYDRA entry state serialization (item 1, commit 2).

These pin the CURRENT behavior of `_save_state_to_disk` + `_load_state_file_history`
BEFORE the IronCondorEntry property-bridge refactor touches the entry model. A
mid-day restart must reload every leg field of an open entry — if it doesn't, the
bot loses its positions. There is no existing coverage of the per-entry
serialization block (the heartbeat test runs save with entries=[]), so these
tests are written against today's code and must pass as-is; the bridge commit
must keep them green.

G1 = round-trip a populated entry through real save → real load.
G2 = a legacy flat-key state file (the on-disk schema) loads unchanged.

The strategy is built with HydraStrategy.__new__ + injected attributes (the
pattern from test_hydra_state_heartbeat.py), extended with everything the
load path reads. OHLC is given real floats on purpose: the loader does
`if spx_low > 0` which TypeErrors on None.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.strategy import HydraIronCondorEntry, HydraStrategy
from shared.market_hours import get_us_market_time

# The 16 leg-scoped fields that are persisted (4 legs × strike/position_id/uic/
# fill_price). `price` and `mid_at_fill` are transient and intentionally NOT
# serialized — they're repopulated by the next price tick.
_PERSISTED_LEG_FIELDS = [
    f"{leg}_{prop}"
    for leg in ("short_call", "long_call", "short_put", "long_put")
    for prop in ("strike", "position_id", "uic", "fill_price")
]


def _today() -> str:
    return get_us_market_time().strftime("%Y-%m-%d")


def _make_strategy(tmp_path: Path, *, date: str, entries=None) -> HydraStrategy:
    """Minimal HydraStrategy exposing everything save+load read."""
    s = HydraStrategy.__new__(HydraStrategy)

    ds = SimpleNamespace(
        date=date,
        entries_completed=0, entries_failed=0, entries_skipped=0,
        total_credit_received=0.0, total_realized_pnl=0.0, total_commission=0.0,
        call_stops_triggered=0, put_stops_triggered=0, double_stops=0,
        circuit_breaker_opens=0, one_sided_entries=0, trend_overrides=0,
        credit_gate_skips=0, stops_avoided_mkt036=0,
        entries=entries if entries is not None else [],
    )
    s.daily_state = ds

    s.state = SimpleNamespace(value="MONITORING")
    s._next_entry_index = 0
    s.contracts_per_entry = 1
    s.entry_times = []
    s._base_entry_count = 0
    s._conditional_entry_times = []
    s.strategy_config = {}
    s._pnl_history = []
    s.short_only_stop = False
    s._early_close_time = None
    s.vix_gate_enabled = False  # skip the vix-gate restore branch on load
    # Real floats: the loader's `if spx_low > 0` TypeErrors on None.
    s.market_data = SimpleNamespace(
        spx_open=6000.0, spx_high=6010.0, spx_low=5990.0,
        vix_open=18.0, vix_high=18.5, vix_low=17.5,
    )
    s.state_file = str(tmp_path / "hydra_state.json")
    s._get_effective_stop_level = lambda *a, **kw: None
    return s


def _populated_entry() -> HydraIronCondorEntry:
    """A full IC with every persisted leg field + credits/stops/flags set to
    distinct, non-default values — so a silently-dropped field is detected.

    entry_time is left None on purpose: that skips the dashboard pnl-history
    block in save (which is not part of leg serialization and has its own
    landmines), keeping this test focused on the entry round-trip.
    """
    e = HydraIronCondorEntry(entry_number=2)
    e.short_call_strike, e.long_call_strike = 7520.0, 7595.0
    e.short_put_strike, e.long_put_strike = 7350.0, 7275.0
    e.short_call_position_id, e.long_call_position_id = "DRY_SC", "DRY_LC"
    e.short_put_position_id, e.long_put_position_id = "DRY_SP", "DRY_LP"
    e.short_call_uic, e.long_call_uic = 111, 222
    e.short_put_uic, e.long_put_uic = 333, 444
    e.short_call_fill_price, e.long_call_fill_price = 1.25, 0.40
    e.short_put_fill_price, e.long_put_fill_price = 1.60, 0.55
    e.call_spread_credit, e.put_spread_credit = 85.0, 105.0
    e.call_side_stop, e.put_side_stop = 3.10, 4.20
    e.is_complete = True
    e.contracts = 1
    return e


class TestStateRoundTrip:
    """G1 — real save → real load preserves every leg field of an open entry."""

    def test_full_ic_round_trips(self, tmp_path):
        orig = _populated_entry()
        s1 = _make_strategy(tmp_path, date=_today(), entries=[orig])
        s1._save_state_to_disk()
        assert Path(s1.state_file).exists(), "save did not write the state file"

        s2 = _make_strategy(tmp_path, date="1970-01-01", entries=[])
        assert s2._load_state_file_history() is True
        assert len(s2.daily_state.entries) == 1
        r = s2.daily_state.entries[0]

        for field in _PERSISTED_LEG_FIELDS:
            assert getattr(r, field) == getattr(orig, field), field

        assert r.call_spread_credit == orig.call_spread_credit
        assert r.put_spread_credit == orig.put_spread_credit
        assert r.call_side_stop == orig.call_side_stop
        assert r.put_side_stop == orig.put_side_stop
        assert r.total_credit == orig.total_credit
        assert r.contracts == orig.contracts
        assert r.is_complete is True

    def test_uic_survives_round_trip(self, tmp_path):
        # The F4 reconcile key. The 2026-05-05 fix exists precisely because these
        # were once dropped on restore, leaving active_entries with no positions.
        orig = _populated_entry()
        s1 = _make_strategy(tmp_path, date=_today(), entries=[orig])
        s1._save_state_to_disk()
        s2 = _make_strategy(tmp_path, date="1970-01-01")
        assert s2._load_state_file_history() is True
        r = s2.daily_state.entries[0]
        assert (r.short_call_uic, r.long_call_uic, r.short_put_uic, r.long_put_uic) == (111, 222, 333, 444)


class TestLegacyFileLoad:
    """G2 — a hand-written flat-key file (the on-disk schema) loads unchanged."""

    def _legacy_entry_dict(self):
        return {
            "entry_number": 1,
            "short_call_strike": 7520.0, "long_call_strike": 7595.0,
            "short_put_strike": 7350.0, "long_put_strike": 7275.0,
            "short_call_uic": 111, "long_call_uic": 222,
            "short_put_uic": 333, "long_put_uic": 444,
            "short_call_position_id": "DRY_SC", "long_call_position_id": "DRY_LC",
            "short_put_position_id": "DRY_SP", "long_put_position_id": "DRY_LP",
            "short_call_fill_price": 1.25, "long_call_fill_price": 0.40,
            "short_put_fill_price": 1.60, "long_put_fill_price": 0.55,
            "call_spread_credit": 85.0, "put_spread_credit": 105.0,
            "call_side_stop": 3.10, "put_side_stop": 4.20,
            "is_complete": True, "contracts": 1,
        }

    def test_flat_key_file_reconstructs(self, tmp_path):
        state = {"date": _today(), "entries": [self._legacy_entry_dict()]}
        s = _make_strategy(tmp_path, date="1970-01-01")
        Path(s.state_file).write_text(json.dumps(state))

        assert s._load_state_file_history() is True
        assert len(s.daily_state.entries) == 1
        r = s.daily_state.entries[0]
        for field in _PERSISTED_LEG_FIELDS:
            assert getattr(r, field) is not None
        assert r.short_call_strike == 7520.0
        assert r.short_call_uic == 111
        assert r.short_call_position_id == "DRY_SC"
        assert r.total_credit == 190.0

    def test_load_tolerates_missing_new_keys(self, tmp_path):
        # Pins the dict.get(default) contract: an entry dict missing the uic keys
        # must default them to None, not crash. This is what lets an OLD file load
        # after the bridge adds the legs model.
        entry = self._legacy_entry_dict()
        for k in ("short_call_uic", "long_call_uic", "short_put_uic", "long_put_uic"):
            del entry[k]
        state = {"date": _today(), "entries": [entry]}
        s = _make_strategy(tmp_path, date="1970-01-01")
        Path(s.state_file).write_text(json.dumps(state))

        assert s._load_state_file_history() is True
        r = s.daily_state.entries[0]
        assert r.short_call_uic is None
        assert r.short_call_strike == 7520.0  # present keys still load

    def test_other_day_file_is_ignored(self, tmp_path):
        # The date gate bounds the bridge's blast radius to same-day restarts.
        state = {"date": "2000-01-01", "entries": [self._legacy_entry_dict()]}
        s = _make_strategy(tmp_path, date="1970-01-01")
        Path(s.state_file).write_text(json.dumps(state))
        assert s._load_state_file_history() is False
        assert s.daily_state.entries == []
