"""The status line must show the window that is ACTUALLY governing the stop.

Found reviewing B's 2026-10-01 stops: the log said `MKT-046 ... threshold 0.0s`
while the status line said `CONF 49s/75s`. The display read
`stop_confirmation_seconds` (MKT-036's window) unconditionally, ignoring
`stop_confirmation_enabled`. On the live seat MKT-036 is DISABLED and
`mkt046_confirm_seconds` is 0, so the line promised 26 more seconds of grace
when the stop could fire on the next poll.

These call the REAL function with a stub `self` — no stand-in for the thing
under test, which is how an earlier guard in this project passed against a
reintroduced bug.
"""
import re
import pathlib
import pytest

from bots.hydra.base_strategy import MEICStrategy


class _Stub:
    """Only the three attributes the helper reads."""
    def __init__(self, enabled, mkt036_window, mkt046_window):
        self.stop_confirmation_enabled = enabled
        self.stop_confirmation_seconds = mkt036_window
        self.mkt046_confirm_seconds = mkt046_window


def test_mkt036_disabled_reports_the_mkt046_window():
    """The live-seat shape: MKT-036 off, MKT-046 at 0 -> must report 0, not 75."""
    s = _Stub(enabled=False, mkt036_window=75, mkt046_window=0.0)
    assert MEICStrategy._confirm_window_seconds(s) == 0.0


def test_mkt036_enabled_reports_the_mkt036_window():
    s = _Stub(enabled=True, mkt036_window=75, mkt046_window=0.0)
    assert MEICStrategy._confirm_window_seconds(s) == 75.0


def test_mkt046_default_when_disabled():
    """A's shape: MKT-036 off, MKT-046 left at its 10s default."""
    s = _Stub(enabled=False, mkt036_window=75, mkt046_window=10.0)
    assert MEICStrategy._confirm_window_seconds(s) == 10.0


def test_missing_attributes_fall_back_without_raising():
    """A partially-built strategy must not crash the status line."""
    class Bare:
        pass
    assert MEICStrategy._confirm_window_seconds(Bare()) == 10.0


def test_display_sites_use_the_helper_not_the_raw_field():
    """NEGATIVE CONTROL, structural.

    The bug was a raw `getattr(self, 'stop_confirmation_seconds', 75)` at the
    display sites. Assert it is gone from them, so reintroducing the bug fails
    here even if the helper itself stays correct.
    """
    src = pathlib.Path("bots/hydra/base_strategy.py").read_text()
    # The display sites are the lines that build the CONF string.
    conf_lines = [l for l in src.splitlines() if "CONF {elapsed" in l]
    assert len(conf_lines) == 2, f"expected 2 CONF display sites, found {len(conf_lines)}"
    # And no display site may read the MKT-036 field directly any more.
    window_assigns = re.findall(
        r"conf_window = (.+)", src)
    assert window_assigns, "no conf_window assignment found at all"
    for a in window_assigns:
        assert a.strip() == "self._confirm_window_seconds()", (
            f"a display site reads the window directly: {a!r} — this is the "
            "2026-10-01 bug reintroduced"
        )
