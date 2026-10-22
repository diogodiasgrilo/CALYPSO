"""H's BINDING gate was the one gate that could not be scored.

Measured 2026-10-22 over variant H's 19 ``ls_skipped`` rows:

    gate that fired         rows    input recorded
    range-expansion            8    0/19   <-- the binding gate
    vix-percentile             6    13/19
    cost-cap                   3    19/19
    skew                       2    19/19

Of the 13 skips that passed BOTH skew and the cost cap, **8 were stopped by
range-expansion** and 5 by the IV gate. So range-expansion decides whether H
trades at all — and it recorded nothing, including on the 8 occasions it was
itself the cause.

Cause: a writer asymmetry, not a missing reading. ``_range_expansion_gate``
sets ``entry.ls_range_expansion`` (to a detail string carrying BOTH halves of
the compound condition), ``_record_entry_to_db`` passed it, ``record_skip``
accepted it — and ``_skip`` simply never passed it, so the parameter defaulted
to None on every declined entry.

This is measurement, not tuning: the gate's behaviour is unchanged. H has
placed nothing since 2026-09-25 and its entire lifetime +$139.80 predates this
filter, so the next question is whether the gate is right — and that cannot be
asked until the gate's own inputs are on the record.
"""

from __future__ import annotations

import inspect
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from bots.hydra.long_strangle_strategy import LongStrangleStrategy  # noqa: E402
from bots.hydra.ls_recorder import LongStrangleDataRecorder  # noqa: E402

SKIP_SRC = inspect.getsource(LongStrangleStrategy._skip)
ENTRY_SRC = inspect.getsource(LongStrangleStrategy._record_entry_to_db)


class TestTheWriterAsymmetryIsClosed:
    def test_skip_writer_passes_range_expansion(self):
        assert "range_expansion=getattr(entry, \"ls_range_expansion\", None)" in SKIP_SRC

    def test_the_recorder_accepts_it(self):
        assert "range_expansion" in inspect.signature(
            LongStrangleDataRecorder.record_skip).parameters

    def test_every_field_the_ENTRY_writer_records_is_also_recorded_on_a_SKIP(self):
        """The invariant, not the instance. This defect was a single omitted
        kwarg; the same omission in any other field would be just as silent, so
        pin the SET rather than the one member that happened to be missing.

        ``iv_percentile`` was retro-fitted here once already (its stray
        indentation in ``_skip`` is the scar), which is the second instance of
        exactly this shape."""
        fields = {"em_source", "expected_move", "skew_gap_pct",
                  "iv_percentile", "iv_percentile_n", "range_expansion"}
        for f in fields:
            assert f"ls_{f}" in ENTRY_SRC, f"fixture stale: ls_{f} not in entry writer"
            assert f"ls_{f}" in SKIP_SRC, (
                f"{f} is recorded for entries but NOT for skips — the declined "
                f"decision is the one that needs a counterfactual")


class TestItReachesTheDatabase:
    """End-to-end against a real SQLite file: the kwarg being present in source
    proves nothing about the column landing (a source-grep test is blind)."""

    def _recorder(self, tmp_path):
        return LongStrangleDataRecorder(str(tmp_path / "ls.db"))

    def test_the_detail_string_lands_in_the_column(self, tmp_path):
        rec = self._recorder(tmp_path)
        detail = "narrow median 61.0 vs baseline 54.5 (112%), latest 70.0 (115% of narrow)"
        rec.record_skip("2026-10-22", 1, "10:45:00", f"no range expansion ({detail})",
                        range_expansion=detail)
        row = rec._conn.execute(
            "SELECT range_expansion FROM ls_skipped WHERE date='2026-10-22'").fetchone()
        assert row[0] == detail

    def test_a_skip_recorded_without_it_is_NULL_not_an_error(self, tmp_path):
        """A gate ordered BEFORE range-expansion (the IV gate short-circuits)
        legitimately has no reading. NULL must keep meaning 'not evaluated'."""
        rec = self._recorder(tmp_path)
        rec.record_skip("2026-10-22", 2, "11:15:00", "IV percentile unavailable")
        row = rec._conn.execute(
            "SELECT range_expansion FROM ls_skipped WHERE entry_number=2").fetchone()
        assert row[0] is None


class TestTheDetailIsActuallySCOREABLE:
    """Recording a bare verdict would satisfy the tests above and still leave
    the gate unscoreable. The compound condition has TWO halves — compression
    then expansion — and the skip message names the half that PASSED, so the
    detail must carry both ratios or the record cannot distinguish them."""

    def test_both_halves_of_the_compound_condition_are_in_the_detail(self):
        from bots.hydra.long_strangle_chain import range_expansion_signal
        # a quiet stretch that never compressed — H's actual 2026-09-28+ shape
        vals = [50.0] * 60 + [56.0] * 9 + [64.0]
        fires, detail = range_expansion_signal(
            vals, narrow_n=10, baseline_n=60,
            compression_max=0.90, expansion_mult=1.10)
        assert fires is False, "fixture: this must NOT fire (no compression)"
        assert "% of narrow" in detail, "expansion half missing"
        assert "vs baseline" in detail, "compression half missing"
        # and the numbers are the ones a reader would score against
        assert "56.0" in detail and "64.0" in detail

    def test_negative_control_the_old_skip_writer_recorded_NULL(self, tmp_path):
        """Proves the tests above are not vacuous by reproducing the defect:
        the pre-fix call omitted the kwarg entirely, so the SAME gate detail
        that exists on the entry was absent from the row."""
        rec = LongStrangleDataRecorder(str(tmp_path / "ctl.db"))
        rec.record_skip(  # verbatim pre-fix kwarg set
            "2026-10-22", 9, "10:45:00", "no range expansion (narrow 61.0 ...)",
            spx=7700.0, vix=15.0, em_source="straddle", expected_move=60.0,
            iv_percentile=20.0, iv_percentile_n=40, skew_gap_pct=1.0)
        row = rec._conn.execute(
            "SELECT range_expansion FROM ls_skipped WHERE entry_number=9").fetchone()
        assert row[0] is None, "the old call shape did record NULL"
