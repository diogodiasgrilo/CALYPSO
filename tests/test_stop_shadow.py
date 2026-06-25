"""Tests for bots/hydra/stop_shadow.py — the Brandon %-of-width stop aggregator.

Unit-tests the pure per-side counterfactual + the aggregation, plus a real
sqlite round-trip (trade_entries ⨝ spread_snapshots ⨝ trade_stops). The headline
case is reproduced from real data: 2026-06-24 E#2 put closed at −$2,225 on the
credit+buffer stop, while a 25%-of-width stop would have capped it ~−$805.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.stop_shadow import (  # noqa: E402
    analyze,
    analyze_pct,
    format_report,
    simulate_side,
    _confirmed_cross_sv,
)


class TestConfirmedCross:
    def test_raw_fires_on_first_crossing(self):
        assert _confirmed_cross_sv([100, 900, 200], trigger=875, confirm_snaps=0) == 900

    def test_confirm1_needs_two_consecutive(self):
        # one tick over then drops → confirm_snaps=1 never confirmed
        assert _confirmed_cross_sv([100, 900, 200, 100], 875, 1) is None
        # two consecutive over → fires at the 2nd
        assert _confirmed_cross_sv([100, 900, 950, 100], 875, 1) == 950

    def test_spike_resets_the_run(self):
        # 900,(drop),900,950 → only the trailing run of 2 confirms → 950
        assert _confirmed_cross_sv([900, 100, 900, 950], 875, 1) == 950

    def test_confirmed_filters_a_whipsaw_in_simulate_side(self):
        # SV spikes one tick over the trigger then recovers; actual rode to +20.
        # RAW would fire (premature loss); CONFIRMED (snaps=1) does NOT fire.
        raw = simulate_side(70, 5, 7, [100, 945, 200, 50], None, 0.25, confirm_snaps=0)
        conf = simulate_side(70, 5, 7, [100, 945, 200, 50], None, 0.25, confirm_snaps=1)
        assert raw["fired"] is True and raw["impact"] < 0       # premature loss
        assert conf["fired"] is False and conf["impact"] == 0   # whipsaw filtered out


# ── per-side counterfactual ───────────────────────────────────────────────────

class TestSimulateSide:
    def test_tail_capping_win(self):
        # today's E#2 put: 5pt/7c, credit $105; SV ramps to 2330; actual stop −2225.
        # pct 0.25 → trigger 875 → shadow fires at first SV≥875 (=910) → −805.
        r = simulate_side(105, 5, 7, [100, 500, 910, 1500, 2330], actual_net_pnl=-2225, pct=0.25)
        assert r["fired"] is True and r["actual_stopped"] is True
        assert r["shadow"] == pytest.approx(105 - 910)      # −805
        assert r["actual"] == -2225
        assert r["impact"] == pytest.approx(1420)           # capped ~$1,420 earlier

    def test_premature_stop_on_recoverer(self):
        # SV spikes over the trigger intraday then recovers; actual rode to ~$20 kept.
        r = simulate_side(70, 5, 7, [100, 945, 200, 50], actual_net_pnl=None, pct=0.25)
        assert r["fired"] is True and r["actual_stopped"] is False
        assert r["shadow"] == pytest.approx(70 - 945)       # −875
        assert r["actual"] == pytest.approx(70 - 50)        # +20 (credit − last SV)
        assert r["impact"] == pytest.approx(-895)           # premature stop cost

    def test_never_fires_is_zero_impact(self):
        r = simulate_side(60, 5, 7, [10, 50, 100, 30], actual_net_pnl=None, pct=0.25)  # all < 875
        assert r["fired"] is False and r["impact"] == 0.0

    def test_sv_clamped_to_max_loss(self):
        # a garbage SV spike above max loss (5*100*7=3500) is clamped.
        r = simulate_side(105, 5, 7, [9999], actual_net_pnl=-2225, pct=0.25)
        assert r["fired"] is True
        assert r["shadow"] == pytest.approx(105 - 3500)     # clamped, not 105−9999

    def test_high_pct_trigger_above_credit_buffer(self):
        # pct 0.65 → trigger 2275 > the actual ~2100 stop → actual fires first/lower
        # → shadow worse (negative impact even though actual stopped).
        r = simulate_side(105, 5, 7, [100, 2300], actual_net_pnl=-2000, pct=0.65)
        assert r["fired"] is True
        assert r["impact"] < 0

    def test_no_credit_or_width_or_snaps_returns_none(self):
        assert simulate_side(0, 5, 7, [100], None, 0.25) is None
        assert simulate_side(60, 0, 7, [100], None, 0.25) is None
        assert simulate_side(60, 5, 7, [], None, 0.25) is None
        assert simulate_side(60, 5, 7, [None, None], None, 0.25) is None


# ── aggregation ───────────────────────────────────────────────────────────────

class TestAnalyzePct:
    def _fixture(self):
        entries = {
            ("2026-06-24", 2): {"entry_type": "full_ic", "contracts": 7,
                                "call": {"credit": 175, "width": 5}, "put": {"credit": 105, "width": 5}},
            ("2026-06-24", 1): {"entry_type": "put_only", "contracts": 7,
                                "call": {"credit": 0, "width": 0}, "put": {"credit": 70, "width": 5}},
        }
        sv = {
            ("2026-06-24", 2): [(0, 100), (0, 910), (0, 2330)],   # call safe, put ramps
            ("2026-06-24", 1): [(0, 100), (0, 945), (0, 50)],     # put spikes then recovers
        }
        stops = {("2026-06-24", 2, "put"): -2225}  # only E#2 put actually stopped
        return entries, sv, stops

    def test_aggregates_tail_and_premature(self):
        e, sv, st = self._fixture()
        r = analyze_pct(e, sv, st, 0.25)
        # E#2 put: tail-capping win (+1420). E#1 put: premature (−895). E#2 call: never fires.
        assert r["sides_fired"] == 2
        assert r["tail_capping_count"] == 1
        assert r["tail_capping_total"] == pytest.approx(1420)
        assert r["premature_count"] == 1
        assert r["premature_total"] == pytest.approx(-895)
        assert r["net_impact"] == pytest.approx(1420 - 895)  # +525

    def test_width_max_filter_excludes_wide(self):
        e, sv, st = self._fixture()
        e[("2026-06-24", 2)]["put"]["width"] = 75  # make E#2 put a WIDE spread
        r = analyze_pct(e, sv, st, 0.25, width_max=10)
        # E#2 put now excluded → only E#1 put fires
        assert r["tail_capping_count"] == 0
        assert r["premature_count"] == 1


# ── real sqlite round-trip ────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE trade_entries (date TEXT, entry_number INTEGER, entry_type TEXT, contracts INTEGER,
  call_credit REAL, put_credit REAL, call_spread_width REAL, put_spread_width REAL,
  PRIMARY KEY (date, entry_number));
CREATE TABLE spread_snapshots (timestamp TEXT, entry_number INTEGER, call_spread_value REAL,
  put_spread_value REAL, date TEXT, PRIMARY KEY (timestamp, entry_number));
CREATE TABLE trade_stops (date TEXT, entry_number INTEGER, side TEXT, net_pnl REAL);
"""


class TestRoundTrip:
    def _db(self, tmp_path):
        p = tmp_path / "v.db"
        con = sqlite3.connect(str(p))
        con.executescript(_SCHEMA)
        con.execute("INSERT INTO trade_entries VALUES ('2026-06-24',2,'full_ic',7,175,105,5,5)")
        for i, (c, pv) in enumerate([(0, 100), (0, 910), (0, 2330)]):
            con.execute("INSERT INTO spread_snapshots VALUES (?,?,?,?,?)",
                        (f"2026-06-24 14:{i:02d}:00", 2, c, pv, "2026-06-24"))
        con.execute("INSERT INTO trade_stops VALUES ('2026-06-24',2,'put',-2225)")
        con.commit(); con.close()
        return str(p)

    def test_analyze_reads_db_and_finds_the_tail_win(self, tmp_path):
        r = analyze(self._db(tmp_path), pcts=(0.25,))
        assert r["db_status"] == "ok" and r["n_entries"] == 1
        by = r["by_pct"][0]
        assert by["tail_capping_count"] == 1
        assert by["tail_capping_total"] == pytest.approx(1420)

    def test_missing_db_is_graceful(self, tmp_path):
        r = analyze(str(tmp_path / "nope.db"))
        assert r["db_status"] == "not_found"
        assert "not_found" in format_report(r)

    def test_report_renders(self, tmp_path):
        out = format_report(analyze(self._db(tmp_path), pcts=(0.25, 0.40)))
        assert "%-of-width stop SHADOW" in out and "25%" in out
