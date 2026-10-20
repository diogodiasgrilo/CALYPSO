"""H's VIX history collapsed 252 -> 7 days when `bl` was created, blocking it.

`_vix_history_db` picked the FIRST candidate with >= 1 usable VIX close, with
the live seat ahead of variant A. On 2026-10-09 the live seat became `bl`,
whose database was brand new. A guard added that same day required "usable
content" — but at `minimum=1`, while the CONSUMER requires
`iv_percentile_min_history_days` (60). So it held for exactly one day: as soon
as bl recorded its own first VIX row it passed the guard, won on candidate
order, and H ranked against a handful of days while variant A sat on 170.

Measured from H's own `ls_skipped.iv_percentile_n`:

    2026-10-02 .. 10-08   252     <- healthy
    2026-10-09            163     <- bl created
    2026-10-12 .. 10-20   1,2,3,5,7   <- bl's own age, counting up

H is dry-run-locked so no money was at risk, but it could not have taken a
single entry again until roughly January, silently, while CLAUDE.md still
described its blocker as the range-expansion filter.

THE FIX IS NOT A BIGGER THRESHOLD. This has now bitten twice for the same
reason — "first candidate past a bar" is defeated by any new variant, and the
right bar is a judgement that can be wrong again. VIX is MARKET-WIDE and
identical across variants, so there is no correctness reason to prefer one
recorder, only a data-quantity one: pick the candidate with the MOST history.
That is immune to a new variant appearing, to the live seat moving, and to the
floor being retuned.

An explicit operator pin (`iv_percentile_source_db`) still wins outright — a
heuristic must not overrule someone who deliberately named a database.
"""

from __future__ import annotations

import os
import sqlite3
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from bots.hydra.long_strangle_strategy import LongStrangleStrategy as LS  # noqa: E402


def _db(path, n_days, start_day=1):
    """A database with `n_days` of VIX closes in market_ticks."""
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE market_ticks (timestamp TEXT, vix_level REAL)")
    for i in range(n_days):
        d = f"2026-{((start_day + i) // 28) % 12 + 1:02d}-{((start_day + i) % 28) + 1:02d}"
        con.execute("INSERT INTO market_ticks VALUES (?,?)", (f"{d} 15:59:00", 15.0 + i * 0.01))
    con.commit(); con.close()
    return str(path)


class _Strat(LS):
    """Only the surface `_vix_history_db` touches."""
    def __init__(self, candidates, cfg=None):
        self._candidates = candidates
        self._cfg = cfg or {}
    def _ls_config(self):
        return self._cfg


def _layout(tmp_path, live_days, a_days, h_days=0):
    """Build the real on-disk layout the resolver discovers by itself.

    root/                     <- DATA_DIR's parent when HYDRA_VARIANT_ID is set
      backtesting.db          <- variant A
      variant_<live>/backtesting.db
      variant_h/backtesting.db
    """
    from shared import strategy_taxonomy as tax
    live = next(v for v in tax.available_ids() if tax.STRATEGIES[v].status == "live")
    root = tmp_path / "data"
    (root / f"variant_{live}").mkdir(parents=True)
    (root / "variant_h").mkdir(parents=True)
    paths = {
        "live": _db(root / f"variant_{live}" / "backtesting.db", live_days),
        "a": _db(root / "backtesting.db", a_days),
        "h": _db(root / "variant_h" / "backtesting.db", h_days),
    }
    return root, paths


def _resolve(monkeypatch, strat, tmp_path, live_days, a_days, h_days=0):
    """Drive the REAL _vix_history_db — no reimplementation.

    An earlier version of this file reimplemented the resolver in the test and
    asserted against that copy, which would have passed against the unfixed
    production code. The whole point is to exercise the shipped method.
    """
    import bots.hydra.long_strangle_strategy as mod
    root, paths = _layout(tmp_path, live_days, a_days, h_days)
    monkeypatch.setattr(mod, "DATA_DIR", str(root / "variant_h"), raising=False)
    monkeypatch.setattr(mod, "HYDRA_VARIANT_ID", "h", raising=False)
    return LS._vix_history_db(strat), paths


class TestTheIncident:
    def test_a_nearly_empty_live_seat_does_NOT_win_over_variant_A(self, tmp_path, monkeypatch):
        """The exact 2026-10-20 shape: live seat 7 days, A 170."""
        got, p = _resolve(monkeypatch, _Strat(None), tmp_path, live_days=7, a_days=170)
        assert got == p["a"]

    def test_negative_control_first_past_a_bar_of_ONE_picks_the_live_seat(self, tmp_path):
        """Reinstates the defect, proving the test above is not vacuous."""
        bl = _db(tmp_path / "bl.db", 7)
        a = _db(tmp_path / "a.db", 170)
        picked = next(c for c in [bl, a] if LS._db_has_vix_history(c, 1))
        assert picked == bl, "the old rule must reproduce the bug"
        assert _Strat(None)._vix_history_len(bl) < _Strat(None)._vix_history_len(a)

    def test_the_2026_10_09_empty_db_case_still_works(self, tmp_path, monkeypatch):
        """An EMPTY new seat must still lose — the original guard's purpose."""
        got, p = _resolve(monkeypatch, _Strat(None), tmp_path, live_days=0, a_days=170)
        assert got == p["a"]

    def test_a_LONGER_live_seat_is_correctly_preferred(self, tmp_path, monkeypatch):
        """Not a blanket 'always A' — it genuinely picks the longest."""
        got, p = _resolve(monkeypatch, _Strat(None), tmp_path, live_days=300, a_days=170)
        assert got == p["live"]

    def test_the_chosen_history_clears_the_consumers_floor(self, tmp_path, monkeypatch):
        """What actually unblocks H: >= iv_percentile_min_history_days (60)."""
        s = _Strat(None)
        got, _ = _resolve(monkeypatch, s, tmp_path, live_days=7, a_days=170)
        assert s._vix_history_len(got) >= 60


class TestOperatorPinWins:
    def test_an_explicit_pin_beats_a_longer_database(self, tmp_path, monkeypatch):
        pin = _db(tmp_path / "pin.db", 80)
        s = _Strat(None, {"iv_percentile_source_db": pin})
        got, _ = _resolve(monkeypatch, s, tmp_path, live_days=7, a_days=170)
        assert got == pin, "a deliberate pin must not be overruled"

    def test_an_UNUSABLE_pin_degrades_to_the_automatic_choice(self, tmp_path, monkeypatch):
        """A typo must not disable the filter."""
        s = _Strat(None, {"iv_percentile_source_db": str(tmp_path / "typo.db")})
        got, p = _resolve(monkeypatch, s, tmp_path, live_days=7, a_days=170)
        assert got == p["a"]

    def test_an_EMPTY_pin_degrades_too(self, tmp_path, monkeypatch):
        empty = _db(tmp_path / "pin.db", 0)
        s = _Strat(None, {"iv_percentile_source_db": empty})
        got, p = _resolve(monkeypatch, s, tmp_path, live_days=7, a_days=170)
        assert got == p["a"]


class TestDegradation:
    def test_all_empty_returns_a_readable_fallback_not_None(self, tmp_path, monkeypatch):
        """So the skip reason still reports 'n prior days', not 'unavailable'."""
        got, _ = _resolve(monkeypatch, _Strat(None), tmp_path, live_days=0, a_days=0)
        assert got is not None and os.path.exists(got)

    def test_a_corrupt_candidate_counts_as_zero_and_does_not_raise(self, tmp_path):
        junk = tmp_path / "junk.db"; junk.write_bytes(b"not a database")
        assert _Strat(None)._vix_history_len(str(junk)) == 0

    def test_length_helper_agrees_with_the_boolean_guard(self, tmp_path):
        """One query, two readers — they must never disagree."""
        d = _db(tmp_path / "d.db", 12)
        assert (_Strat(None)._vix_history_len(d) > 0) is LS._db_has_vix_history(d, 1)
