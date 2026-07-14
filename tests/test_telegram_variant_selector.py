"""Item 6 (2026-06-22): per-command Telegram variant selectors (/status <name>).

The pragmatic implementation: `/status`, `/snapshot`, `/stops` accept an
optional variant token; for a named NON-primary variant the command renders a
single unified view read from that variant's OWN state file (reusing the
existing _load_variant_state + _build_variant_summary cross-variant infra). The
poller's own variant (A) and the calendar group (D/E) route elsewhere.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.strategy import HydraStrategy  # noqa: E402
from bots.hydra.telegram_commands import TelegramCommandHandler  # noqa: E402


def _strat():
    return HydraStrategy.__new__(HydraStrategy)


_IC_STATE = {
    "date": "2026-06-22", "entries_completed": 2,
    "total_credit_received": 350.0, "total_realized_pnl": 285.0,
    "total_commission": 64.0, "call_stops_triggered": 1, "put_stops_triggered": 0,
    "entries": [],
}


class TestVariantView:
    def test_unknown_variant(self):
        out = _strat()._build_telegram_variant_view("zzz")
        assert "Unknown variant" in out

    def test_calendar_variant_points_to_calendars(self):
        # D is a calendar — the IC-shaped view doesn't apply; point at /calendars.
        out = _strat()._build_telegram_variant_view("d")
        assert "/calendars" in out
        assert "DC Time Machine" in out  # D's display_name

    def test_ic_variant_renders_from_state(self):
        s = _strat()
        s._load_variant_state = lambda vid: dict(_IC_STATE)
        out = s._build_telegram_variant_view("c")
        assert "Brandon Narrow (live)" in out  # C's display_name
        assert "(C)" in out
        assert "Net P&L: $221.00" in out       # 285 realized − 64 commission
        assert "Stops: 1 call / 0 put" in out

    def test_ic_variant_no_fresh_state(self):
        s = _strat()
        s._load_variant_state = lambda vid: None
        out = s._build_telegram_variant_view("b")
        assert "no fresh state" in out
        assert "Brandon Narrow (7-slot)" in out  # B's display_name


class TestDelegation:
    def test_status_delegates_for_named_variant(self):
        s = _strat()
        s._load_variant_state = lambda vid: None
        # A non-A name routes build_telegram_status to the variant view.
        assert "no fresh state" in s.build_telegram_status("b")

    def test_snapshot_delegates_for_named_variant(self):
        s = _strat()
        s._load_variant_state = lambda vid: dict(_IC_STATE)
        assert "Brandon Narrow (live)" in s.build_telegram_snapshot("c")

    def test_stops_delegates_for_named_variant(self):
        s = _strat()
        s._load_variant_state = lambda vid: dict(_IC_STATE)
        assert "(C)" in s.build_telegram_stops("c")

    def test_primary_id_does_not_delegate_to_variant_view(self):
        # "a" is the poller's own bot — build_telegram_status("a") must NOT take
        # the variant-view branch (it would recurse / mis-render). We assert the
        # guard lets it fall through by checking it does NOT return the variant
        # view's "no fresh state" string even when _load_variant_state is stubbed.
        s = _strat()
        s._load_variant_state = lambda vid: None
        # Patch the variant-view so a delegation would be observable.
        s._build_telegram_variant_view = lambda vid: "VARIANT_VIEW"
        # Full status needs lots of state; we only care that the guard does not
        # delegate for "a" / None. Stub the rest of build_telegram_status's deps
        # minimally by catching the AttributeError that proves it went PAST the
        # guard into the real body (i.e. did not return "VARIANT_VIEW").
        import pytest
        with pytest.raises(Exception) as ei:
            s.build_telegram_status("a")
        assert "VARIANT_VIEW" not in str(ei.value)


class TestArgParse:
    def test_variant_arg_extracts_token(self):
        assert TelegramCommandHandler._variant_arg("/status c") == "c"
        assert TelegramCommandHandler._variant_arg("/status  D ") == "d"

    def test_variant_arg_none_when_bare(self):
        assert TelegramCommandHandler._variant_arg("/status") is None
        assert TelegramCommandHandler._variant_arg("") is None
