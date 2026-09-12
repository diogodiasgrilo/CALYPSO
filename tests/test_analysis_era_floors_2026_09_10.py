"""
Historical analyzers must not pool incomparable eras (2026-09-10).

THE DEFECT. `slot_edge.analyze_slots` and `stop_shadow.analyze` both read EVERY
row in the database with no date floor. On variant B that pools two different
experiments:

    before 2026-07-24 : dry-run shadow, 10 contracts, 4-slot grid, SIMULATED fills
    after  2026-07-24 : LIVE paper seat,  7 contracts, 7-slot grid, REAL fills

Ranking slots across that boundary produces a confident-looking answer to a
question nobody asked. Not hypothetical: the 2026-09-02 decision to cut B's
11:15 slot was taken on pooled data and had to be reversed.

The floor defaults to the live-era boundary and is overridable, because
analysing the pre-swap era on purpose is legitimate — doing it BY ACCIDENT is
not.

THE SUBTLE PART — the two floors must COMPOSE. `since` is a REGIME floor (which
experiment); `PER_ENTRY_RELIABLE_SINCE` is a DATA floor (when per-entry
realized_pnl started being booked, 2026-07-02). slot_edge's daily cross-check
compares summed per-entry P&L against summed day totals. Flooring the ENTRY set
at 07-24 while still summing DAY totals from 07-02 reports a "drift" exactly
equal to the P&L in the gap — a fabricated reconciliation failure caused purely
by mismatched windows. The cross-check therefore uses max(since, DATA floor).
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra import slot_edge, stop_shadow  # noqa: E402
from bots.hydra.analysis_eras import (  # noqa: E402
    LIVE_ERA_SINCE,
    PER_ENTRY_RELIABLE_SINCE,
    era_banner,
)

PRE_DATA = "2026-06-20"    # before both floors
MID = "2026-07-10"         # after the DATA floor, before the REGIME floor
LIVE = "2026-08-01"        # after both


def _db(tmp_path, dates):
    p = tmp_path / "t.db"
    con = sqlite3.connect(str(p))
    con.executescript(
        """
        CREATE TABLE trade_entries (date TEXT, entry_number INT, entry_time TEXT,
          entry_type TEXT, contracts INT, call_credit REAL, put_credit REAL,
          total_credit REAL, call_spread_width REAL, put_spread_width REAL,
          realized_pnl REAL);
        CREATE TABLE spread_snapshots (date TEXT, entry_number INT, timestamp TEXT,
          call_spread_value REAL, put_spread_value REAL);
        CREATE TABLE trade_stops (date TEXT, entry_number INT, side TEXT,
          net_pnl REAL, stop_time TEXT);
        CREATE TABLE daily_summaries (date TEXT, gross_pnl REAL, net_pnl REAL);
        """
    )
    for d in dates:
        con.execute(
            "INSERT INTO trade_entries VALUES (?,1,?,'full_ic',7,100,100,200,5,5,50)",
            (d, f"{d} 10:45:00"))
        con.execute("INSERT INTO spread_snapshots VALUES (?,1,'t',1.0,1.0)", (d,))
        con.execute("INSERT INTO trade_stops VALUES (?,1,'call',-10,?)",
                    (d, f"{d} 11:00:00"))
        con.execute("INSERT INTO daily_summaries VALUES (?,50.0,40.0)", (d,))
    con.commit()
    con.close()
    return str(p)


class TestStopShadowFloors:
    def test_the_default_excludes_the_pre_swap_era(self, tmp_path):
        db = _db(tmp_path, [PRE_DATA, MID, LIVE])
        assert stop_shadow.analyze(db)["n_entries"] == 1     # only LIVE

    def test_no_floor_sees_everything(self, tmp_path):
        db = _db(tmp_path, [PRE_DATA, MID, LIVE])
        assert stop_shadow.analyze(db, since="")["n_entries"] == 3

    def test_the_floor_applies_to_stops_too(self, tmp_path):
        """A floored entry set with unfloored stops would attribute pre-swap
        stops to live-era entries."""
        db = _db(tmp_path, [PRE_DATA, MID, LIVE])
        _, _, stops, _ = stop_shadow._load(db, since=LIVE_ERA_SINCE)
        assert all(d >= LIVE_ERA_SINCE for (d, _, _) in stops)

    def test_the_floor_applies_to_snapshots_too(self, tmp_path):
        db = _db(tmp_path, [PRE_DATA, MID, LIVE])
        _, sv, _, _ = stop_shadow._load(db, since=LIVE_ERA_SINCE)
        assert all(d >= LIVE_ERA_SINCE for (d, _) in sv)

    def test_the_window_is_reported(self, tmp_path):
        """A number without its window is not interpretable."""
        db = _db(tmp_path, [PRE_DATA, LIVE])
        res = stop_shadow.analyze(db)
        assert res["since"] == LIVE_ERA_SINCE
        assert LIVE_ERA_SINCE in res["era_banner"]

    def test_it_still_works_on_an_all_live_db(self, tmp_path):
        db = _db(tmp_path, [LIVE])
        assert stop_shadow.analyze(db)["n_entries"] == 1


class TestSlotEdgeFloors:
    def test_the_default_excludes_the_pre_swap_era(self, tmp_path):
        db = _db(tmp_path, [PRE_DATA, MID, LIVE])
        res = slot_edge.analyze_slots(db)
        assert res.get("ok") is not False
        total = sum(r["n"] for r in res.get("slots", res.get("rows", [])))
        assert total == 1, "only the live-era entry should be scored"

    def test_no_floor_sees_everything(self, tmp_path):
        db = _db(tmp_path, [PRE_DATA, MID, LIVE])
        res = slot_edge.analyze_slots(db, since="")
        total = sum(r["n"] for r in res.get("slots", res.get("rows", [])))
        assert total == 3

    def test_a_custom_floor_is_honoured(self, tmp_path):
        db = _db(tmp_path, [PRE_DATA, MID, LIVE])
        res = slot_edge.analyze_slots(db, since=MID)
        total = sum(r["n"] for r in res.get("slots", res.get("rows", [])))
        assert total == 2


class TestTheTwoFloorsCompose:
    """The interaction that would otherwise fabricate a reconciliation failure."""

    def test_no_spurious_drift_when_the_regime_floor_is_later(self, tmp_path):
        """MID sits between the DATA floor (07-02) and the REGIME floor (07-24).
        With mismatched windows the cross-check counts MID's DAY total but not
        MID's ENTRY P&L, and reports the difference as drift.

        Asserts on the REAL keys. The first version of this test used
        res.get("xcheck_drift", res.get("drift")) — neither key exists, so it
        got None, skipped the assertion, and passed while testing nothing."""
        db = _db(tmp_path, [MID, LIVE])
        res = slot_edge.analyze_slots(db, since=LIVE_ERA_SINCE)
        drift = res["scored_total_xcheck"] - res["daily_gross_xcheck"]
        assert abs(drift) < 0.01, (
            f"fabricated drift of {drift}: scored={res['scored_total_xcheck']} "
            f"vs daily={res['daily_gross_xcheck']} — the windows disagree"
        )

    def test_the_cross_check_window_actually_moved(self, tmp_path):
        """Proves the composition is doing work rather than coinciding: with
        MID excluded, the day total must be ONE day's worth, not two."""
        db = _db(tmp_path, [MID, LIVE])
        res = slot_edge.analyze_slots(db, since=LIVE_ERA_SINCE)
        assert res["daily_gross_xcheck"] == pytest.approx(50.0)
        unfloored = slot_edge.analyze_slots(db, since="")
        assert unfloored["daily_gross_xcheck"] == pytest.approx(100.0)

    def test_the_regime_floor_is_later_than_the_data_floor(self):
        """If this ever inverts, max() silently becomes a no-op and the
        composition above stops being exercised."""
        assert LIVE_ERA_SINCE > PER_ENTRY_RELIABLE_SINCE

    def test_the_data_floor_still_applies_when_since_is_earlier(self, tmp_path):
        """Asking for everything must NOT drag the cross-check below the date
        realized_pnl became trustworthy."""
        db = _db(tmp_path, [PRE_DATA, MID, LIVE])
        res = slot_edge.analyze_slots(db, since="")
        # PRE_DATA is below the DATA floor, so it must be excluded from the
        # cross-check even though the caller asked for "everything".
        assert res["daily_gross_xcheck"] == pytest.approx(100.0)
        drift = res["scored_total_xcheck"] - res["daily_gross_xcheck"]
        assert abs(drift) < 0.01


class TestTheBanner:
    def test_the_default_floor_names_the_swap(self):
        assert LIVE_ERA_SINCE in era_banner(LIVE_ERA_SINCE)
        assert "regime" in era_banner(LIVE_ERA_SINCE).lower()

    @pytest.mark.parametrize("empty", ["", None])
    def test_no_floor_warns_loudly(self, empty):
        """Running unfloored is allowed but must never look routine."""
        b = era_banner(empty)
        assert "ALL DATES" in b
        assert "DIFFERENT REGIME" in b

    def test_a_custom_floor_is_labelled_custom(self):
        assert "custom" in era_banner("2026-08-01")


class TestTheConstantsAreSharedNotCopied:
    """A constant duplicated across analyzers drifts silently, and the failure
    mode is a plausible number over incomparable data — not a crash."""

    def test_both_analyzers_import_from_analysis_eras(self):
        for mod in ("slot_edge", "stop_shadow"):
            src = (Path(__file__).resolve().parents[1]
                   / "bots" / "hydra" / f"{mod}.py").read_text()
            assert "from bots.hydra.analysis_eras import" in src, mod

    def test_neither_redefines_the_date_literal(self):
        for mod in ("slot_edge", "stop_shadow"):
            src = (Path(__file__).resolve().parents[1]
                   / "bots" / "hydra" / f"{mod}.py").read_text()
            assert f'= "{LIVE_ERA_SINCE}"' not in src, f"{mod} redefines the era date"
