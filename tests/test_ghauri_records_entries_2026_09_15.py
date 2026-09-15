"""Variant F must RECORD the entries it places (2026-09-15).

THE DEFECT, measured on the live VM 2026-09-14:

    variant_f  trade_entries: 0 rows (ever)
    variant_f  trade_stops  : 1 row
    variant_f  lifetime P&L : -$14.80

F had actually traded that day — its heartbeat showed the position and the stop.
But `GhauriMeanReversionStrategy._initiate_entry` deliberately bypasses
`HydraStrategy._initiate_entry` (for sound reasons, see its docstring), and in
doing so dropped the `_record_entry_to_db` call that method makes after a
successful placement.

Stops were unaffected, because they run through the INHERITED
`_execute_stop_loss`. So the asymmetry was: **entries invisible, stops
recorded** — and F's entire recorded history was losses. Every analyzer, the
dashboard, and HOMER would have read F as a strategy that only ever loses money,
indefinitely, no matter how it actually performed.

This is the hazard of a self-contained override: it inherits the parts you
didn't think about, and silently omits the parts you did.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.ghauri_strategy import GhauriMeanReversionStrategy  # noqa: E402
from bots.hydra.strategy import HydraStrategy  # noqa: E402

SRC = inspect.getsource(GhauriMeanReversionStrategy._initiate_entry)


class TestItRecordsTheEntry:
    def test_it_calls_the_recorder_at_all(self):
        """The whole defect in one assertion."""
        assert "_record_entry_to_db(entry)" in SRC

    def test_it_records_only_AFTER_a_successful_placement(self):
        """A failed placement returns early via `if not success:`. Recording
        before that would write entries for trades that never opened."""
        assert SRC.index("if not success:") < SRC.index("_record_entry_to_db(entry)")

    def test_it_stamps_spx_first(self):
        """_record_entry_to_db reads entry._spx_at_entry; the parent sets it
        immediately before the call. Without it the row loses its spot price."""
        i = SRC.index("_spx_at_entry")
        j = SRC.index("_record_entry_to_db(entry)")
        assert i < j

    def test_it_also_persists_state(self):
        """The parent saves state immediately on entry; F relied on the main
        loop's periodic save, leaving a crash window where a placed position
        existed only in memory."""
        assert "_save_state_to_disk()" in SRC
        assert SRC.index("_save_state_to_disk()") < SRC.index("_record_entry_to_db(entry)")

    def test_the_inherited_recorder_exists_and_is_not_overridden(self):
        """F must use the parent's implementation — a divergent copy would drift."""
        assert "_record_entry_to_db" not in GhauriMeanReversionStrategy.__dict__
        assert hasattr(HydraStrategy, "_record_entry_to_db")


class TestTelemetryCanNeverCostAPlacedEntry:
    """`_initiate_entry` is `try/finally` with NO `except`. An exception raised
    after placement would escape past the POSITION_OPENED alert on a position
    that is already open — strictly worse than the missing row it fixes. The
    parent can call these bare only because its own method has a surrounding
    except; F cannot."""

    def test_the_recording_is_wrapped(self):
        i = SRC.index("_save_state_to_disk()")
        window = SRC[i - 400:i + 600]
        assert "try:" in window
        assert "except Exception" in window

    def test_the_handler_does_not_swallow_silently(self):
        i = SRC.index("_record_entry_to_db(entry)")
        assert "logger.warning" in SRC[i:i + 600]

    def test_the_method_still_has_no_bare_except_around_placement(self):
        """Guard against 'fixing' this by wrapping the whole method — that would
        hide real placement failures."""
        assert SRC.count("except Exception") == 1


class TestItDoesNotDisturbThePlacementPath:
    def test_the_alert_still_fires_after_recording(self):
        assert SRC.index("_record_entry_to_db(entry)") < SRC.index("POSITION_OPENED")

    def test_entry_is_still_appended_to_daily_state_first(self):
        assert SRC.index("daily_state.entries.append(entry)") < SRC.index("_save_state_to_disk()")

    def test_the_pending_fire_side_is_still_cleared(self):
        assert "_ghauri_pending_fire_side = None" in SRC
