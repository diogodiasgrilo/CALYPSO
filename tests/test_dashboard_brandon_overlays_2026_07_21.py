"""2026-07-21: dashboard overlay reader — surface Brandon defensive overlays
(previously invisible) with a display P&L that matches what the bot booked.

Uses B's REAL 07-21 butterfly (entry #5: LC 7500x10 / SC 7510x20 / LC 7520x10)
which the bot settled at +$5,853.83 against SPX close 7507.44 — the reader must
reproduce that from the sidecar legs alone.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dashboard.backend.services.brandon_hedge_reader import read_overlays_by_entry  # noqa: E402


def _leg(en, side, ct, strike, qty, fill, structure, threatened, placed):
    return {"entry_number": en, "side": side, "contract_type": ct, "strike": strike,
            "quantity": qty, "fill_price": fill, "position_id": f"DRY_{en}", "conid": None,
            "structure": structure, "threatened_side": threatened, "placed_at": placed}


def _write(tmp_path, legs_by_entry):
    p = tmp_path / "brandon_hedge_legs.json"
    p.write_text(json.dumps({"date": "2026-07-21", "legs_by_entry": legs_by_entry}))
    return str(p)


# B's real 07-21 #5 butterfly.
_BFLY = [
    _leg(5, "long", "call", 7500.0, 10, 16.89801641694885, "butterfly", "call", "2026-07-21T13:03:48-04:00"),
    _leg(5, "short", "call", 7510.0, 20, 10.822554851553832, "butterfly", "call", "2026-07-21T13:03:48-04:00"),
    _leg(5, "long", "call", 7520.0, 10, 6.333267513374722, "butterfly", "call", "2026-07-21T13:03:48-04:00"),
]


class TestOverlayReader:
    def test_butterfly_pnl_matches_bot(self, tmp_path):
        path = _write(tmp_path, {"5": _BFLY})
        out = read_overlays_by_entry(path, spx=7507.44)
        assert "5" in out and len(out["5"]) == 1
        ov = out["5"][0]
        assert ov["structure"] == "butterfly"
        assert ov["threatened_side"] == "call"
        assert ov["debit"] == pytest.approx(1586.17, abs=0.5)     # what B paid
        assert ov["pnl"] == pytest.approx(5853.83, abs=0.5)       # what B booked at close
        assert len(ov["legs"]) == 3

    def test_pnl_is_max_at_the_pin(self, tmp_path):
        # At the 7510 pin the 10-wide x10 butterfly is worth its full $10,000.
        path = _write(tmp_path, {"5": _BFLY})
        ov = read_overlays_by_entry(path, spx=7510.0)["5"][0]
        assert ov["pnl"] == pytest.approx(10000.0 - ov["debit"], abs=0.5)

    def test_pnl_none_when_no_spx(self, tmp_path):
        path = _write(tmp_path, {"5": _BFLY})
        assert read_overlays_by_entry(path, spx=None)["5"][0]["pnl"] is None

    def test_missing_file_is_empty(self, tmp_path):
        assert read_overlays_by_entry(str(tmp_path / "nope.json"), spx=7500.0) == {}
        assert read_overlays_by_entry(None, spx=7500.0) == {}

    def test_multiple_overlays_per_entry_grouped_by_placement(self, tmp_path):
        # A call debit spread in the morning + a put butterfly in the afternoon on #1.
        legs = [
            _leg(1, "long", "call", 7525.0, 10, 3.7, "debit_spread", "call", "AM"),
            _leg(1, "short", "call", 7530.0, 10, 2.8, "debit_spread", "call", "AM"),
            _leg(1, "long", "put", 7480.0, 10, 5.0, "butterfly", "put", "PM"),
            _leg(1, "short", "put", 7470.0, 20, 3.0, "butterfly", "put", "PM"),
            _leg(1, "long", "put", 7460.0, 10, 1.5, "butterfly", "put", "PM"),
        ]
        out = read_overlays_by_entry(_write(tmp_path, {"1": legs}), spx=7500.0)
        assert len(out["1"]) == 2  # two distinct overlays, grouped by placement
        sides = sorted(o["threatened_side"] for o in out["1"])
        assert sides == ["call", "put"]
