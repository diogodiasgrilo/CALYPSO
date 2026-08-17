"""2026-08-15: overlays reach the surfaces that actually render for the user.

The 2026-07-21 reader (test_dashboard_brandon_overlays_2026_07_21.py) and its
first call site (`_ic_snapshot`, pinned by test_dashboard_ic_snapshot_mirror.py)
were both correct — but they only fed `GET /api/strategies/{id}/snapshot`,
which the frontend polls ONLY when a non-primary variant is picked. The three
surfaces that actually render by default were never wired up:

  1. The primary/live WebSocket path (`ws/broadcaster.py`'s `get_snapshot()`
     and `_poll_state()`) — what renders on page load for the live variant.
  2. `/api/variants/{id}/state` and the legacy `/comparison` page
     (`routers/variants.py:_variant_payload()`).

Both always returned `overlays: []` even when a real hedge sidecar existed.
This file pins the fix for both. Runs in the dashboard env; skips cleanly
where it isn't installed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from dashboard.backend.config import settings  # noqa: E402
from dashboard.backend.services.market_status import get_today_et  # noqa: E402

TODAY = get_today_et()

# B's real 07-21 butterfly (entry #5) — same fixture data as the reader's own
# tests, so the P&L this file asserts is independently cross-checked.
_BFLY_LEGS = [
    {"entry_number": 5, "side": "long", "contract_type": "call", "strike": 7500.0,
     "quantity": 10, "fill_price": 16.89801641694885, "position_id": "DRY_5_0",
     "conid": None, "structure": "butterfly", "threatened_side": "call",
     "placed_at": f"{TODAY}T13:03:48-04:00"},
    {"entry_number": 5, "side": "short", "contract_type": "call", "strike": 7510.0,
     "quantity": 20, "fill_price": 10.822554851553832, "position_id": "DRY_5_1",
     "conid": None, "structure": "butterfly", "threatened_side": "call",
     "placed_at": f"{TODAY}T13:03:48-04:00"},
    {"entry_number": 5, "side": "long", "contract_type": "call", "strike": 7520.0,
     "quantity": 10, "fill_price": 6.333267513374722, "position_id": "DRY_5_2",
     "conid": None, "structure": "butterfly", "threatened_side": "call",
     "placed_at": f"{TODAY}T13:03:48-04:00"},
]


def _write_sidecar(data_dir: Path) -> Path:
    p = data_dir / "brandon_hedge_legs.json"
    p.write_text(json.dumps({"date": TODAY, "legs_by_entry": {"5": _BFLY_LEGS}}))
    return p


def _seed_state(path: Path, spx: float = 7507.44) -> None:
    """Minimal state file with entry #5 (the butterfly's parent IC) present,
    plus a couple of OTHER entries with no overlay — the merge must not
    invent overlays for entries the sidecar doesn't mention."""
    path.write_text(
        json.dumps(
            {
                "date": TODAY,
                "state": "MONITORING",
                "last_spx_price": spx,
                "entries": [
                    {
                        "entry_number": 1,
                        "entry_time": f"{TODAY}T10:45:03-04:00",
                        "short_call_strike": 7550, "long_call_strike": 7555,
                        "short_put_strike": 7450, "long_put_strike": 7445,
                        "call_spread_credit": 120, "put_spread_credit": 130,
                    },
                    {
                        "entry_number": 5,
                        "entry_time": f"{TODAY}T13:00:00-04:00",
                        "short_call_strike": 7510, "long_call_strike": 7530,
                        "short_put_strike": 7400, "long_put_strike": 7390,
                        "call_spread_credit": 90, "put_spread_credit": 95,
                    },
                ],
                "total_credit_received": 435.0,
                "total_realized_pnl": 0.0,
                "total_commission": 5.0,
                "pnl_history": [],
            }
        )
    )


# ── _variant_payload() / GET /api/variants/{id}/state ──────────────────────


@pytest.fixture
def variants_client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "dashboard_auth_db", tmp_path / "dashboard_auth.db", raising=False)
    monkeypatch.setattr(settings, "comparison_mode_enabled", True, raising=False)

    vb = tmp_path / "variant_b"
    vb.mkdir()
    _seed_state(vb / "hydra_state.json")
    _write_sidecar(vb)

    va = tmp_path / "variant_a"
    va.mkdir()
    _seed_state(va / "hydra_state.json")
    # Variant A: same state, but NO sidecar — a non-Brandon (or quiet-day)
    # variant must not fail or fabricate overlays.

    # dashboard.backend.routers.variants builds _VARIANTS / _state_readers /
    # _db_readers ONCE at module import time (module-level dict comprehensions
    # over `settings`, not re-read per-request — unlike routers/strategies.py's
    # per-request getattr(settings, ...)). By the time this fixture runs, that
    # module has near-certainly already been imported by an earlier test in
    # the same session, so patching `settings.variant_b_state_file` etc. here
    # would silently do nothing. Patch the actual live objects the router
    # reads instead — this is what makes the fixture work regardless of
    # import/test order.
    import dashboard.backend.routers.variants as variants_module

    monkeypatch.setitem(variants_module._VARIANTS["b"], "state_file", vb / "hydra_state.json")
    monkeypatch.setitem(variants_module._VARIANTS["b"], "backtesting_db", vb / "backtesting.db")
    monkeypatch.setattr(variants_module._state_readers["b"], "file_path", vb / "hydra_state.json")

    monkeypatch.setitem(variants_module._VARIANTS["a"], "state_file", va / "hydra_state.json")
    monkeypatch.setitem(variants_module._VARIANTS["a"], "backtesting_db", va / "backtesting.db")
    monkeypatch.setattr(variants_module._state_readers["a"], "file_path", va / "hydra_state.json")

    from dashboard.backend.main import app
    with TestClient(app) as c:
        yield c


class TestVariantPayloadOverlays:
    def test_entry_with_hedge_carries_overlays(self, variants_client):
        r = variants_client.get("/api/variants/b/state")
        assert r.status_code == 200
        body = r.json()
        entries = {e["entry_number"]: e for e in body["entries"]}
        assert entries[5]["overlays"], "entry #5's real butterfly sidecar must surface as overlays"
        ov = entries[5]["overlays"][0]
        assert ov["structure"] == "butterfly"
        assert ov["threatened_side"] == "call"
        assert ov["pnl"] == pytest.approx(5853.83, abs=0.5)

    def test_entry_without_hedge_has_empty_overlays_not_missing_key(self, variants_client):
        r = variants_client.get("/api/variants/b/state")
        entries = {e["entry_number"]: e for e in r.json()["entries"]}
        assert entries[1]["overlays"] == []

    def test_variant_with_no_sidecar_at_all_degrades_cleanly(self, variants_client):
        r = variants_client.get("/api/variants/a/state")
        assert r.status_code == 200
        entries = {e["entry_number"]: e for e in r.json()["entries"]}
        assert entries[5]["overlays"] == []
        assert entries[1]["overlays"] == []

    def test_comparison_endpoint_also_carries_overlays(self, variants_client):
        # /comparison uses the same _variant_payload() builder, nested under "variants".
        r = variants_client.get("/api/variants/comparison")
        assert r.status_code == 200
        body = r.json()
        b_entries = {e["entry_number"]: e for e in body["variants"]["B"]["entries"]}
        assert b_entries[5]["overlays"], "the legacy comparison page must see the same fix"


# ── Broadcaster (primary/live WS path) ──────────────────────────────────────


@pytest.fixture
def broadcaster(tmp_path, monkeypatch):
    """A real Broadcaster wired at a fixed 'live seat' under tmp_path, so
    live_state_file()/live_backtesting_db() (which broadcaster.py's __init__
    calls) resolve to our fixture tree instead of /opt/calypso."""
    vb = tmp_path / "variant_b"
    vb.mkdir()
    _seed_state(vb / "hydra_state.json")
    _write_sidecar(vb)

    monkeypatch.setattr(settings, "variant_b_state_file", vb / "hydra_state.json", raising=False)
    monkeypatch.setattr(settings, "variant_b_backtesting_db", vb / "backtesting.db", raising=False)
    monkeypatch.setattr(settings, "variant_b_metrics_file", vb / "hydra_metrics.json", raising=False)
    monkeypatch.setattr(settings, "variant_b_log_file", vb / "bot.log", raising=False)

    # Pin the live seat to B regardless of any dry_run config on disk (there is
    # none under tmp_path — live_seat_id() would fall back to FALLBACK_SEAT_ID
    # anyway, but pin explicitly so this test doesn't depend on that fallback).
    import dashboard.backend.services.variant_readers as variant_readers
    monkeypatch.setattr(variant_readers, "live_seat_id", lambda: "b")

    monkeypatch.setattr(settings, "market_data_db", vb / "backtesting.db", raising=False)

    from dashboard.backend.ws.broadcaster import Broadcaster
    from dashboard.backend.ws.manager import ConnectionManager

    return Broadcaster(ConnectionManager())


class TestBroadcasterMergeOverlays:
    """Unit-level: the shared merge helper both get_snapshot() and
    _poll_state() call. Covers the actual risk (wrong data reaching the
    client) without needing to drive either async method's full I/O surface."""

    def test_merges_overlays_onto_matching_entry_only(self, broadcaster):
        entries = [
            {"entry_number": 1, "entry_time": f"{TODAY}T10:45:03-04:00"},
            {"entry_number": 5, "entry_time": f"{TODAY}T13:00:00-04:00"},
        ]
        state = {"date": TODAY, "last_spx_price": 7507.44}
        merged = broadcaster._merge_overlays(entries, state)
        by_num = {e["entry_number"]: e for e in merged}
        assert by_num[1]["overlays"] == []
        assert len(by_num[5]["overlays"]) == 1
        assert by_num[5]["overlays"][0]["pnl"] == pytest.approx(5853.83, abs=0.5)

    def test_does_not_mutate_input_entries(self, broadcaster):
        # Broadcaster.get_snapshot()'s `entries` list may be reused elsewhere
        # (e.g. logged) — the merge must copy, not mutate in place.
        entries = [{"entry_number": 5, "entry_time": f"{TODAY}T13:00:00-04:00"}]
        state = {"date": TODAY, "last_spx_price": 7507.44}
        broadcaster._merge_overlays(entries, state)
        assert "overlays" not in entries[0]

    def test_no_sidecar_is_a_cheap_noop(self, tmp_path, monkeypatch):
        # A variant with no brandon_hedge_legs.json at all (e.g. non-Brandon A).
        va = tmp_path / "variant_a"
        va.mkdir()
        _seed_state(va / "hydra_state.json")
        monkeypatch.setattr(settings, "variant_a_state_file", va / "hydra_state.json", raising=False)
        monkeypatch.setattr(settings, "variant_a_backtesting_db", va / "backtesting.db", raising=False)
        monkeypatch.setattr(settings, "variant_a_metrics_file", va / "hydra_metrics.json", raising=False)
        monkeypatch.setattr(settings, "variant_a_log_file", va / "bot.log", raising=False)
        monkeypatch.setattr(settings, "market_data_db", va / "backtesting.db", raising=False)

        import dashboard.backend.services.variant_readers as variant_readers
        monkeypatch.setattr(variant_readers, "live_seat_id", lambda: "a")

        from dashboard.backend.ws.broadcaster import Broadcaster
        from dashboard.backend.ws.manager import ConnectionManager

        b = Broadcaster(ConnectionManager())
        entries = [{"entry_number": 1, "entry_time": f"{TODAY}T10:45:03-04:00"}]
        result = b._merge_overlays(entries, {"date": TODAY})
        assert result is entries  # identity, not just equality — the no-op path


class TestBroadcasterGetSnapshot:
    """Integration: get_snapshot() (the WS 'snapshot' message on connect) must
    carry overlays on today_entries — this is what actually reaches the
    primary/live dashboard view."""

    def test_snapshot_today_entries_carry_overlays(self, broadcaster):
        import asyncio

        snapshot = asyncio.run(broadcaster.get_snapshot())
        by_num = {e["entry_number"]: e for e in snapshot["today_entries"]}
        assert 5 in by_num, "entry #5 should come through the state-file fallback (empty DB)"
        assert by_num[5]["overlays"], "the live/primary snapshot must carry the real overlay"
        assert by_num[5]["overlays"][0]["structure"] == "butterfly"


class TestBroadcasterPollStateMergesOverlays:
    """_poll_state() broadcasts raw hydra_state.json content on every change —
    it must run entries through the SAME merge before broadcasting, not just
    on the initial snapshot, so a hedge placed mid-day shows up live."""

    def test_poll_state_broadcasts_entries_with_overlays(self, broadcaster):
        import asyncio

        sent = []

        async def fake_broadcast(message):
            sent.append(message)
            # Stop the infinite poll loop after the first broadcast.
            raise asyncio.CancelledError()

        broadcaster.manager.broadcast = fake_broadcast

        async def run_one_iteration():
            try:
                await broadcaster._poll_state()
            except asyncio.CancelledError:
                pass

        asyncio.run(run_one_iteration())

        state_updates = [m for m in sent if m.get("type") == "state_update"]
        assert state_updates, "expected at least one state_update broadcast"
        entries = state_updates[0]["data"]["entries"]
        by_num = {e["entry_number"]: e for e in entries}
        assert by_num[5]["overlays"], "state_update entries must carry the overlay, not just the initial snapshot"
