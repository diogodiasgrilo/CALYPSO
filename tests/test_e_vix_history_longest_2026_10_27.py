"""E was silently unable to enter for 18 days — H's bug, in a second file.

`SpyDoubleCalendarStrategy._spy_dc_vix_history` returned the FIRST candidate
that yielded any history, with the live seat first in the order. A brand-new
variant's database is non-empty within a day of its first start, so when the
live seat became `bl` on 2026-10-09, E began ranking today's VIX against bl's
age instead of against the market.

Measured on the VM 2026-10-27, using the production helper:

    data/variant_bl/backtesting.db    12 closes   <- E returned THIS
    data/backtesting.db  (variant A) 175 closes   <- would have passed
    data/variant_e/backtesting.db     91 closes   <- would have passed

E requires 60. Its skip rows read "VIX-percentile proxy unavailable — N prior
days" with N climbing 8, 9, 10, 11, 12 across 10-21..10-27: bl's age, counting
up. E would not have recovered until roughly January.

The sting is that E's own docstring cited H's incident as the reason for its
design. The REASONING was copied; the implementation was "first non-empty" the
whole time. So the selection now lives once, in
`iv_percentile.pick_longest_vix_history`, and both strategies call it.
"""

from __future__ import annotations

import os
import sqlite3
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from bots.hydra.iv_percentile import pick_longest_vix_history  # noqa: E402
from bots.hydra.spy_double_calendar_strategy import (  # noqa: E402
    SpyDoubleCalendarStrategy as E,
)


def _db(path, n_days):
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE market_ticks (timestamp TEXT, vix_level REAL)")
    for i in range(n_days):
        d = f"2026-{(i // 28) % 12 + 1:02d}-{(i % 28) + 1:02d}"
        con.execute("INSERT INTO market_ticks VALUES (?,?)",
                    (f"{d} 15:59:00", 15.0 + i * 0.01))
    con.commit(); con.close()
    return str(path)


class _Strat(E):
    """Only the surface `_spy_dc_vix_history` touches."""
    def __init__(self, lookback=252):
        self.spy_dc_iv_lookback = lookback


def _layout(tmp_path, live_days, a_days, e_days):
    from shared import strategy_taxonomy as tax
    live = next(v for v in tax.available_ids() if tax.STRATEGIES[v].status == "live")
    root = tmp_path / "data"
    (root / f"variant_{live}").mkdir(parents=True)
    (root / "variant_e").mkdir(parents=True)
    return root, {
        "live": _db(root / f"variant_{live}" / "backtesting.db", live_days),
        "a": _db(root / "backtesting.db", a_days),
        "e": _db(root / "variant_e" / "backtesting.db", e_days),
    }


def _history(monkeypatch, tmp_path, live_days, a_days, e_days):
    """Drive the REAL _spy_dc_vix_history, not a reimplementation."""
    import bots.hydra.spy_double_calendar_strategy as mod
    root, paths = _layout(tmp_path, live_days, a_days, e_days)
    monkeypatch.setattr(mod, "DATA_DIR", str(root / "variant_e"), raising=False)
    monkeypatch.setattr(mod, "HYDRA_VARIANT_ID", "e", raising=False)
    return E._spy_dc_vix_history(_Strat()), paths


class TestTheIncident:
    def test_the_measured_shape_now_yields_the_longest(self, monkeypatch, tmp_path):
        """The exact 12 / 175 / 91 split measured on the VM."""
        hist, _ = _history(monkeypatch, tmp_path, live_days=12, a_days=175, e_days=91)
        assert len(hist) == 175, (
            "E must rank against the longest record, not the live seat's age")

    def test_the_old_rule_reproduces_the_bug(self, monkeypatch, tmp_path):
        """Negative control: first-non-empty picks 12 on the same layout.

        Without this, a test asserting '175' could pass for the wrong reason
        (e.g. the live-seat DB simply missing from the candidate list).
        """
        _, paths = _layout(tmp_path, 12, 175, 91)
        from bots.hydra.iv_percentile import vix_history_from_db
        first_non_empty = next(
            vix_history_from_db(p, "2026-12-31", 252)
            for p in (paths["live"], paths["a"], paths["e"])
            if vix_history_from_db(p, "2026-12-31", 252))
        assert len(first_non_empty) == 12, "the old rule must reproduce the bug"

    def test_clears_the_sixty_day_minimum_it_was_failing(self, monkeypatch, tmp_path):
        hist, _ = _history(monkeypatch, tmp_path, live_days=12, a_days=175, e_days=91)
        assert len(hist) >= 60, "this is the gate E was failing every day"

    def test_a_new_live_seat_cannot_shorten_it(self, monkeypatch, tmp_path):
        """The failure mode generalised: a seat move must not regress E."""
        hist, _ = _history(monkeypatch, tmp_path, live_days=1, a_days=200, e_days=5)
        assert len(hist) == 200


class TestSelectorItself:
    def test_picks_longest_regardless_of_order(self, tmp_path):
        a = _db(tmp_path / "a.db", 10)
        b = _db(tmp_path / "b.db", 99)
        for order in ((a, b), (b, a)):
            best, hist, _ = pick_longest_vix_history(order, "2026-12-31", 252)
            assert best == b and len(hist) == 99

    def test_missing_and_junk_paths_are_skipped_not_fatal(self, tmp_path):
        junk = tmp_path / "junk.db"
        junk.write_text("not a database")
        good = _db(tmp_path / "good.db", 7)
        best, hist, _ = pick_longest_vix_history(
            [None, str(tmp_path / "nope.db"), str(junk), good], "2026-12-31", 252)
        assert best == good and len(hist) == 7

    def test_no_history_anywhere_reports_a_fallback_to_read(self, tmp_path):
        empty = tmp_path / "empty.db"
        sqlite3.connect(str(empty)).close()
        best, hist, fallback = pick_longest_vix_history(
            [str(empty)], "2026-12-31", 252)
        assert best is None and hist == []
        assert fallback == str(empty), (
            "caller must still be able to say 'n prior days', not 'unavailable'")
