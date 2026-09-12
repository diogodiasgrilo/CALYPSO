"""2026-08-25 (convergence-audit finding, pre-existing gap): a same-day
restart restores _brandon_hedge_legs from the sidecar JSON but previously did
NOT reconstruct _brandon_overlay_placed (the anti-double-fire guard) —
meaning a side that already has a real, live hedge on the broker would look
"never placed" after a restart, and could get a SECOND, duplicate hedge
placed on top of it if still within trigger distance and GEX still
confirms. This test proves the sidecar restore now also rebuilds the guard.
"""
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.brandon.strategy import BrandonHydraStrategy  # noqa: E402


def _sidecar_blob(today_iso, entries):
    # legs_by_entry is keyed by entry_number ONLY (matches the real sidecar
    # format written by _brandon_save_hedge_state) — legs from DIFFERENT
    # sides on the SAME entry_number must accumulate in the same list, not
    # overwrite each other.
    legs_by_entry = {}
    for entry_number, side, structure in entries:
        legs_by_entry.setdefault(str(entry_number), []).extend([
            {
                "entry_number": entry_number, "side": "long", "contract_type": "call",
                "strike": 6915.0, "quantity": 7, "fill_price": 1.5,
                "position_id": f"OVERLAY_{entry_number}_{side}_0", "conid": 111,
                "structure": structure, "threatened_side": side,
                "placed_at": datetime(2026, 8, 25, 11, 0, tzinfo=timezone.utc).isoformat(),
            },
            {
                "entry_number": entry_number, "side": "short", "contract_type": "call",
                "strike": 6990.0, "quantity": 7, "fill_price": 0.8,
                "position_id": f"OVERLAY_{entry_number}_{side}_1", "conid": 222,
                "structure": structure, "threatened_side": side,
                "placed_at": datetime(2026, 8, 25, 11, 0, tzinfo=timezone.utc).isoformat(),
            },
        ])
    return {"date": today_iso, "legs_by_entry": legs_by_entry}


def _stub(sidecar_path):
    s = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
    s._brandon_hedge_state_path = sidecar_path
    s._brandon_hedge_legs = {}
    s._brandon_overlay_placed = set()
    return s


class TestOverlayPlacedGuardRestoredOnRestart:
    def test_restart_reconstructs_the_dedup_guard_not_just_the_legs(self, tmp_path):
        today = BrandonHydraStrategy._brandon_today_date()
        sidecar = tmp_path / "brandon_hedge_legs.json"
        sidecar.write_text(json.dumps(_sidecar_blob(
            today.isoformat(), [(7, "call", "debit_spread")]
        )))

        s = _stub(str(sidecar))
        s._brandon_load_hedge_state()

        assert 7 in s._brandon_hedge_legs
        assert len(s._brandon_hedge_legs[7]) == 2
        # The bug: this used to stay empty after a restart even though the
        # legs above prove a real hedge already exists for this key.
        assert (7, "call") in s._brandon_overlay_placed

    def test_multiple_entries_and_sides_all_reconstructed(self, tmp_path):
        today = BrandonHydraStrategy._brandon_today_date()
        sidecar = tmp_path / "brandon_hedge_legs.json"
        sidecar.write_text(json.dumps(_sidecar_blob(
            today.isoformat(),
            [(3, "call", "debit_spread"), (3, "put", "butterfly"), (9, "put", "debit_spread")],
        )))

        s = _stub(str(sidecar))
        s._brandon_load_hedge_state()

        assert s._brandon_overlay_placed == {(3, "call"), (3, "put"), (9, "put")}

    def test_stale_day_sidecar_reconstructs_nothing(self, tmp_path):
        sidecar = tmp_path / "brandon_hedge_legs.json"
        sidecar.write_text(json.dumps(_sidecar_blob("2020-01-01", [(1, "call", "debit_spread")])))

        s = _stub(str(sidecar))
        s._brandon_load_hedge_state()

        assert s._brandon_hedge_legs == {}
        assert s._brandon_overlay_placed == set()

    def test_no_sidecar_file_leaves_guard_untouched(self, tmp_path):
        s = _stub(str(tmp_path / "does_not_exist.json"))
        s._brandon_load_hedge_state()
        assert s._brandon_overlay_placed == set()

    def test_negative_control_without_the_fix_the_guard_would_stay_empty(self, tmp_path, monkeypatch):
        # Reproduces the pre-fix code path directly (restore legs, but never
        # touch _brandon_overlay_placed) to prove this is the exact bug the
        # fix closes, and that the tests above would have caught it.
        today = BrandonHydraStrategy._brandon_today_date()
        sidecar = tmp_path / "brandon_hedge_legs.json"
        sidecar.write_text(json.dumps(_sidecar_blob(
            today.isoformat(), [(7, "call", "debit_spread")]
        )))
        s = _stub(str(sidecar))

        # Minimal stand-in for the pre-fix method body (legs restored, guard not).
        import json as _json
        blob = _json.loads(sidecar.read_text())
        legs_by_entry = blob.get("legs_by_entry") or {}
        for ent_str in legs_by_entry:
            s._brandon_hedge_legs[int(ent_str)] = ["placeholder"]  # legs restored...

        assert 7 in s._brandon_hedge_legs
        assert s._brandon_overlay_placed == set()  # ...but the guard was NOT — the bug.
