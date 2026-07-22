"""Full-primary-move (2026-07-22): the dashboard's CANONICAL views (main page /
WebSocket / iOS widget / legacy /api/hydra/*) follow the LIVE seat (dry_run=false
among the Brandon seats b/c), not a hardcoded 'c'. So a C<->B live-paper swap
re-points "the bot" onto the new live seat.
"""
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

pytest.importorskip("fastapi")

from dashboard.backend.config import settings  # noqa: E402
from dashboard.backend.services import variant_readers as VR  # noqa: E402


def _point_configs(tmp_path, monkeypatch, b_dry: bool, c_dry: bool):
    b = tmp_path / "config_variant_b.json"
    c = tmp_path / "config_variant_c.json"
    b.write_text(json.dumps({"dry_run": b_dry}))
    c.write_text(json.dumps({"dry_run": c_dry}))
    monkeypatch.setattr(settings, "variant_b_config_file", b, raising=False)
    monkeypatch.setattr(settings, "variant_c_config_file", c, raising=False)
    # Give each seat distinct canonical paths so we can prove the resolvers move.
    for vid in ("b", "c"):
        for field, val in (
            ("state_file", tmp_path / f"{vid}_state.json"),
            ("metrics_file", tmp_path / f"{vid}_metrics.json"),
            ("log_file", tmp_path / f"{vid}_bot.log"),
            ("backtesting_db", tmp_path / f"{vid}_bt.db"),
            ("label", f"{vid.upper()} label"),
        ):
            monkeypatch.setattr(settings, f"variant_{vid}_{field}", val, raising=False)


def test_live_seat_id_follows_dry_run(tmp_path, monkeypatch):
    _point_configs(tmp_path, monkeypatch, b_dry=True, c_dry=False)   # C live today
    assert VR.live_seat_id() == "c"
    _point_configs(tmp_path, monkeypatch, b_dry=False, c_dry=True)   # after the swap
    assert VR.live_seat_id() == "b"


def test_canonical_paths_move_with_the_seat(tmp_path, monkeypatch):
    _point_configs(tmp_path, monkeypatch, b_dry=True, c_dry=False)   # C live
    assert VR.live_state_file().name == "c_state.json"
    assert VR.live_metrics_file().name == "c_metrics.json"
    assert VR.live_log_file().name == "c_bot.log"
    assert VR.live_backtesting_db().name == "c_bt.db"
    assert VR.live_label() == "C label"

    _point_configs(tmp_path, monkeypatch, b_dry=False, c_dry=True)   # swap -> B live
    assert VR.live_state_file().name == "b_state.json"
    assert VR.live_metrics_file().name == "b_metrics.json"
    assert VR.live_log_file().name == "b_bot.log"
    assert VR.live_backtesting_db().name == "b_bt.db"
    assert VR.live_label() == "B label"


def test_reader_for_treats_the_live_seat_as_canonical(tmp_path, monkeypatch):
    _point_configs(tmp_path, monkeypatch, b_dry=False, c_dry=True)   # B is live now
    # Empty id -> canonical (the live seat, B), is_canonical True.
    _, is_canonical_empty = VR.reader_for("")
    assert is_canonical_empty is True
    # Picking B (the live seat) is canonical; picking C (now non-live) is not.
    _, is_canonical_b = VR.reader_for("b")
    _, is_canonical_c = VR.reader_for("c")
    assert is_canonical_b is True
    assert is_canonical_c is False


def test_falls_back_to_c_when_ambiguous(tmp_path, monkeypatch):
    _point_configs(tmp_path, monkeypatch, b_dry=True, c_dry=True)    # neither live
    assert VR.live_seat_id() == "c"
    _point_configs(tmp_path, monkeypatch, b_dry=False, c_dry=False)  # both live (shouldn't happen)
    assert VR.live_seat_id() == "c"
