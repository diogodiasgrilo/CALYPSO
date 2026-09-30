"""The analyzer must score breaches correctly and refuse to invent a P&L.

`shadow_entries` stores the shadow's STRIKES but never the credit it would have
collected — `actual_call_credit` belongs to the actual entry. So any profit
number for the shadow would rest on an assumed premium. The tests below pin
that it reports breach only, and that it keeps saying so.

The second property is the guard rail: Section B scores entries the live rule
skipped, 133 of which are GEX accel-zone suppressions, and the data is entirely
in-sample. PREREG_GEX_GATE fixes a 2026-09-29 cut-off precisely so that history
cannot be used to settle the question it generated. If the in-sample warning
ever stops printing, this script quietly becomes a way to backdoor that test.
"""
from __future__ import annotations

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import analyze_shadow_entries as ase  # noqa: E402


class TestBreachScoring:
    def test_a_call_is_breached_when_the_high_reaches_it(self):
        c, _ = ase._breached(7700, 7600, hi=7705, lo=7650)
        assert c is True

    def test_a_call_is_not_breached_when_the_high_stops_short(self):
        c, _ = ase._breached(7700, 7600, hi=7695, lo=7650)
        assert c is False

    def test_a_put_is_breached_when_the_low_reaches_it(self):
        _, p = ase._breached(7700, 7600, hi=7650, lo=7595)
        assert p is True

    def test_touching_the_strike_exactly_counts_as_breached(self):
        """A short at the money is in danger; > vs >= is a real choice and the
        conservative one is the right default for a safety statistic."""
        c, p = ase._breached(7700, 7600, hi=7700, lo=7600)
        assert c is True and p is True

    def test_a_missing_strike_scores_None_not_False(self):
        """None means 'that side had no strike'. False would silently count it
        as a survived side and dilute every breach rate."""
        c, p = ase._breached(None, 7600, hi=7705, lo=7650)
        assert c is None and p is False


class TestTheBinomial:
    def test_zero_of_twenty_against_a_twenty_percent_base_is_significant(self):
        assert ase._binom_p(0, 20, 0.20) < 0.02

    def test_a_matching_rate_is_not_significant(self):
        assert ase._binom_p(4, 20, 0.20) > 0.30

    def test_an_empty_sample_is_never_significant(self):
        assert ase._binom_p(0, 0, 0.20) == 1.0


def _db(tmp_path, rows):
    """A real database: shadow_entries joined to daily_summaries."""
    from shared.data_recorder import DataRecorder
    d = tmp_path / "data" / "variant_t"
    d.mkdir(parents=True)
    p = str(d / "backtesting.db")
    DataRecorder(p).ensure_schema()
    con = sqlite3.connect(p)
    for r in rows:
        con.execute("INSERT OR REPLACE INTO daily_summaries "
                    "(date, spx_high, spx_low) VALUES (?,?,?)",
                    (r["date"], r["hi"], r["lo"]))
        con.execute(
            "INSERT INTO shadow_entries (date, entry_number, spx_at_entry, "
            "shadow_short_call_strike, shadow_short_put_strike, "
            "actual_short_call_strike, actual_short_put_strike, "
            "is_skipped, skip_reason) VALUES (?,?,?,?,?,?,?,?,?)",
            (r["date"], r["en"], r.get("spx", 7650),
             r.get("ssc"), r.get("ssp"), r.get("asc"), r.get("asp"),
             1 if r.get("skipped") else 0, r.get("reason")))
    con.commit(); con.close()
    return tmp_path


class TestItRunsAndKeepsItsWarnings:
    def test_it_reports_breach_and_never_a_shadow_pnl(self, tmp_path, capsys):
        root = _db(tmp_path, [
            dict(date="2026-08-01", en=1, hi=7700, lo=7600,
                 ssc=7690, ssp=7610, asc=7710, asp=7590),
        ])
        assert ase.main(["--variant", "t", "--root", str(root),
                         "--since", "2026-01-01"]) == 0
        out = capsys.readouterr().out
        assert "breached" in out
        assert "never recorded" in out, (
            "the analyzer stopped declaring that no shadow P&L exists — the "
            "next reader will assume one could be computed")
        # the shadow was closer in, so it must show the breach the actual avoided
        assert "LIVE PLACED" in out

    def test_the_in_sample_warning_is_always_printed(self, tmp_path, capsys):
        root = _db(tmp_path, [
            dict(date="2026-08-01", en=1, hi=7700, lo=7600,
                 ssc=7690, ssp=7610, skipped=True, reason="GEX accel-zone skip"),
        ])
        ase.main(["--variant", "t", "--root", str(root), "--since", "2026-01-01"])
        out = capsys.readouterr().out
        assert "PREREG_GEX_GATE" in out and "IN-SAMPLE" in out, (
            "Section B lost its warning. 133 of the skips it scores are GEX "
            "suppressions and this data formed the hypothesis — without the "
            "warning the script is a backdoor around the registered test.")

    def test_a_missing_database_exits_cleanly(self, tmp_path, capsys):
        assert ase.main(["--variant", "zz", "--root", str(tmp_path)]) == 2


class TestTheDistanceConfoundIsHandled:
    """A strike further out breaches less by construction.

    The first run of this analyzer reported the shadow breaching 12.3% against
    live's 17.6% at p = 0.023 — and the shadow sat at 47.9pt against 41.2pt.
    That is not a finding about rule quality, it is arithmetic about width, and
    reported without the caveat it would have read as "fixed-OTM is safer".

    So the analyzer must warn when the rules sat at different distances, and
    must also report the matched-distance view — the same correction
    PREREG_GEX_GATE applies by restricting to vetoed-distance +/-10pt.
    """

    def test_it_warns_when_the_two_rules_sat_at_different_distances(self, tmp_path, capsys):
        rows = [dict(date=f"2026-08-{d:02d}", en=1, hi=7700, lo=7600, spx=7650,
                     ssc=7750, ssp=7550,     # shadow: 100pt out
                     asc=7680, asp=7620)     # actual:  30pt out
                for d in range(1, 12)]
        root = _db(tmp_path, rows)
        ase.main(["--variant", "t", "--root", str(root), "--since", "2026-01-01"])
        out = capsys.readouterr().out
        assert "DIFFERENT distances" in out, (
            "no confound warning: a wider rule breaching less would be "
            "reported as if it were safer")
        assert "says" in out and "nothing about rule quality" in out

    def test_matched_view_excludes_incomparable_sides(self, tmp_path, capsys):
        """All sides here are 70pt apart, so none is comparable — the matched
        section must say it has too few, not silently compare them anyway."""
        rows = [dict(date=f"2026-08-{d:02d}", en=1, hi=7700, lo=7600, spx=7650,
                     ssc=7750, ssp=7550, asc=7680, asp=7620)
                for d in range(1, 12)]
        root = _db(tmp_path, rows)
        ase.main(["--variant", "t", "--root", str(root), "--since", "2026-01-01"])
        out = capsys.readouterr().out
        assert "DISTANCE-MATCHED" in out
        assert "too few comparable sides (0)" in out, out[-400:]

    def test_matched_view_compares_when_distances_line_up(self, tmp_path, capsys):
        """Same distance, different side of the spot: comparable, so it must
        actually run rather than bail."""
        rows = [dict(date=f"2026-08-{d:02d}", en=1, hi=7700, lo=7600, spx=7650,
                     ssc=7700, ssp=7600, asc=7702, asp=7598)
                for d in range(1, 16)]
        root = _db(tmp_path, rows)
        ase.main(["--variant", "t", "--root", str(root), "--since", "2026-01-01"])
        out = capsys.readouterr().out
        assert "too few comparable sides" not in out, out[-500:]
        assert "shadow breached" in out

    def test_it_always_states_that_wider_is_not_free(self, tmp_path, capsys):
        rows = [dict(date="2026-08-01", en=1, hi=7700, lo=7600, spx=7650,
                     ssc=7750, ssp=7550, asc=7680, asp=7620)]
        root = _db(tmp_path, rows)
        ase.main(["--variant", "t", "--root", str(root), "--since", "2026-01-01"])
        out = capsys.readouterr().out
        assert "collects less premium" in out, (
            "the analyzer stopped saying that sitting further out costs "
            "premium — without it, 'safer' reads as 'better'")
