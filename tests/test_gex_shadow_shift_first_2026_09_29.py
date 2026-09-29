"""SHIFT-FIRST shadow: what the adjuster would do if it moved instead of skipping.

WHY. `adjust_call_strike` / `adjust_put_strike` evaluate acceleration zones
first and `return SKIP` immediately, so the decel/SHIFT block below that return
is unreachable whenever a zone fires. Measured 2026-09-28 across 148 live
adjuster decisions on variant B: 23 SKIP, 125 KEEP, and **SHIFT has never once
executed**.

That inverts the source. The only placement behaviour the transcript describes
is moving the band; "skip" never appears in it. And every one of the 21
evaluable SKIPs was wrong — the vetoed strike was never breached.

`shadow_shift_first` computes the alternative without touching the live path.
These tests pin that it is a SHADOW (cannot raise, cannot trade) and that its
arithmetic is symmetric between calls and puts.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from datetime import date, datetime  # noqa: E402

from bots.hydra.brandon.gex_provider import (  # noqa: E402
    GEXProfile, StrikeGEX,
)
from bots.hydra.brandon.gex_strike_adjuster import (  # noqa: E402
    AdjusterConfig, shadow_shift_first,
)


def _profile(neg_band=None, spot=7700.0, filler=1.0):
    """A REAL GEXProfile whose own clustering produces the wanted accel zone.

    Built from strikes rather than by patching `negative_clusters` — the
    dataclass is frozen, and more importantly a patched profile would test the
    stub instead of the clustering the production path actually runs.

    `neg_band` is (low, high, peak_strike): every 5pt strike in [low, high]
    gets negative GEX, the peak gets the largest magnitude, and a positive
    filler band sits far away so the normalization base is non-trivial.
    """
    strikes = []
    if neg_band:
        low, high, peak = neg_band
        k = low
        while k <= high:
            mag = -1e9 if abs(k - peak) < 1e-6 else -3e8
            strikes.append(StrikeGEX(strike=float(k), gex=mag))
            k += 5
    # far-away positive filler, so clusters are computed against a real base
    for k in range(int(spot) - 300, int(spot) - 260, 5):
        strikes.append(StrikeGEX(strike=float(k), gex=filler * 1e7))
    return GEXProfile(spot=spot, expiry=date(2026, 9, 29),
                      fetched_at=datetime(2026, 9, 29, 10, 0, 0),
                      strikes=tuple(sorted(strikes, key=lambda s: s.strike)))


CFG = AdjusterConfig()


class TestItShiftsWhereTheLiveAdjusterSkips:
    def test_call_shift_clears_the_zone(self):
        """The 2026-09-28 shape: short inside a zone, room to move past it."""
        v = shadow_shift_first(side="call", spot=7703, proposed_short=7755,
                               profile=_profile((7750, 7760, 7755)), config=CFG)
        assert v["action"] == "SHIFT"
        assert v["target"] == 7765.0, v          # 7760 high + 5pt buffer
        assert v["target"] > 7755

    def test_put_shift_is_the_mirror(self):
        v = shadow_shift_first(side="put", spot=7700, proposed_short=7645,
                               profile=_profile((7640, 7650, 7645)), config=CFG)
        assert v["action"] == "SHIFT"
        assert v["target"] == 7635.0, v          # 7640 low - 5pt buffer
        assert v["target"] < 7645

    def test_it_agrees_with_SKIP_when_the_zone_is_too_wide_to_clear(self):
        """Not 'delete the SKIP' — a zone wider than max_shift still skips."""
        # zone runs 7750-7900, so clearing it needs 155pt of shift
        v = shadow_shift_first(side="call", spot=7703, proposed_short=7755,
                               profile=_profile((7750, 7900, 7755)), config=CFG)
        assert v["action"] == "SKIP"
        assert "max_shift" in v["reason"]


class TestItOnlySpeaksWhereThePoliciesCanDiffer:
    def test_no_accel_zone_is_not_a_verdict(self):
        """Live KEEPs and shift-first would too — counting it would dilute
        the comparison with decisions that cannot disagree."""
        v = shadow_shift_first(side="call", spot=7700, proposed_short=7760,
                               profile=_profile(None), config=CFG)
        assert v["action"] == "n/a"

    def test_zone_hit_but_peak_out_of_locality_is_not_a_verdict(self):
        """The live adjuster would not SKIP here either (locality gate)."""
        # peak 135pt from the proposed short -> outside locality
        v = shadow_shift_first(side="call", spot=7690, proposed_short=7755,
                               profile=_profile((7700, 7900, 7890)), config=CFG)
        assert v["action"] == "n/a"

    def test_short_on_the_wrong_side_of_spot_is_not_a_verdict(self):
        v = shadow_shift_first(side="call", spot=7800, proposed_short=7755,
                               profile=_profile(None), config=CFG)
        assert v["action"] == "n/a"


class TestItIsAShadow:
    def test_it_never_raises_on_a_broken_profile(self):
        """A shadow that can throw would take the entry path down with it."""
        class _Bad:
            expiry = "x"
            def negative_clusters(self, **k): raise RuntimeError("boom")
            def positive_clusters(self, **k): raise RuntimeError("boom")
        v = shadow_shift_first(side="call", spot=7700, proposed_short=7755,
                               profile=_Bad(), config=CFG)
        assert v["action"] == "error"
        assert "RuntimeError" in v["reason"]

    def test_the_verdict_is_json_safe(self):
        import json
        v = shadow_shift_first(side="call", spot=7703, proposed_short=7755,
                               profile=_profile((7750, 7760, 7755)), config=CFG)
        json.dumps(v)                             # must not raise
        assert set(v) == {"action", "target", "reason"}

    def test_it_returns_a_strike_on_the_configured_grid(self):
        """A shifted short must be tradeable, not a fractional strike."""
        v = shadow_shift_first(side="call", spot=7700, proposed_short=7755,
                               profile=_profile((7750, 7760, 7755)), config=CFG)
        assert v["action"] == "SHIFT"
        assert v["target"] % CFG.strike_increment == 0, v["target"]


class TestItIsPersistedInItsOwnColumn:
    """The verdict must reach the DB, and NOT by hijacking shadow_json.

    The first attempt appended it to `shadow_json`, which holds the GATE
    shadow arms (`gex_shadow.SHADOW_VARIANTS`). An existing test —
    `test_decision_record_writes_both_predicates_and_shadow_json` — pins that
    array's exact membership and caught it. The two shadows answer different
    questions and belong in different columns.
    """

    def test_schema_declares_the_column(self):
        from shared import data_recorder as dr
        assert dr.SCHEMA_VERSION >= 18
        sql = " ".join(dr.MIGRATION_V18_SQL)
        assert "gex_decisions" in sql and "shift_first_json" in sql

    def test_the_writer_persists_it(self):
        """A column nothing writes to is worse than none — it looks collected."""
        import inspect
        from shared import data_recorder as dr
        src = inspect.getsource(dr.DataRecorder.record_gex_decision)
        assert '"shift_first_json"' in src, (
            "record_gex_decision does not write shift_first_json, so the "
            "verdict would be silently dropped on every decision")

    def test_it_is_not_smuggled_into_shadow_json(self):
        import inspect
        import bots.hydra.brandon.strategy as m
        src = inspect.getsource(m.BrandonHydraStrategy._brandon_record_gex_decision)
        i = src.index('"shadow_json"')
        j = src.index('"shadow_disagrees"')
        assert "shift_first" not in src[i:j], (
            "shift_first is being appended to shadow_json again — that array "
            "is the GATE shadow and its membership is pinned elsewhere")

    def test_the_strategy_actually_populates_it(self):
        from unittest.mock import MagicMock
        import json as _json
        from tests.test_gex_shadow_and_cluster_fixes_2026_09_05 import (
            _real_0904_shape, TestTelemetryIsNonFatal,
        )
        s = TestTelemetryIsNonFatal()._strat()
        s._data_recorder = MagicMock()
        s._brandon_record_gex_decision(
            consumer="adjuster", entry_number=3, side="call", spot=7718.34,
            reference_strike=7715.0, live_action="SKIP",
            live_adjuster_predicate=True, live_overlay_predicate=False,
            profile=_real_0904_shape(),
        )
        payload = s._data_recorder.record_gex_decision.call_args[0][0]
        assert "shift_first_json" in payload
        v = _json.loads(payload["shift_first_json"])
        assert set(v) == {"action", "target", "reason"}
        assert v["action"] in {"SHIFT", "SKIP", "n/a", "error"}
        assert v["action"] != "error", v["reason"]
