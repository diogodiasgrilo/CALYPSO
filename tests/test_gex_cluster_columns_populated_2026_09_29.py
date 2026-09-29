"""`gex_decisions` has five cluster columns and nothing ever filled them.

cluster_low / cluster_high / cluster_peak / cluster_n_strikes /
cluster_strength_pct have existed since v16. The recorder accepts a `cluster=`
argument and defaults it to None — and **neither adjuster call site passed
one**, so every row wrote NULL in all five.

It surfaced on 2026-09-29, when five of B's seven entries were blocked by one
acceleration zone and answering "what zone?" meant parsing it back out of the
`shadow_json` blob, because the columns built for exactly that question were
empty. Same family as the overlay telemetry that recorded nothing for 23 days:
instrumentation that exists, looks collected, and isn't.

The cluster is RE-DERIVED for telemetry rather than threaded through
`AdjustResult`, so the live decision path's return shape is untouched.
"""
from __future__ import annotations

import inspect
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import bots.hydra.brandon.strategy as bs  # noqa: E402


class TestBothAdjusterCallSitesPassACluster:
    """One side wired and the other not would leave a permanent blind half —
    and the put side is the one the 2026-09-04 audit found near-blind."""

    def _record_calls(self):
        src = inspect.getsource(bs)
        out = []
        for side in ('side="call"', 'side="put"'):
            i = src.index(f'consumer="adjuster", entry_number=entry.entry_number, {side}')
            out.append((side, src[i:src.index(")", src.index("profile=profile", i))]))
        return out

    def test_call_side_passes_a_cluster(self):
        side, block = self._record_calls()[0]
        assert "cluster=" in block, f"{side} adjuster record passes no cluster"

    def test_put_side_passes_a_cluster(self):
        side, block = self._record_calls()[1]
        assert "cluster=" in block, f"{side} adjuster record passes no cluster"

    def test_each_side_looks_up_its_OWN_strike(self):
        """Copy-paste would hand the put side the call strike and silently
        record the wrong zone — worse than recording none."""
        call_block, put_block = (b for _s, b in self._record_calls())
        assert "entry.short_call_strike, cfg" in call_block
        assert "entry.short_put_strike, cfg" in put_block


class TestTheLookupIsTelemetryOnly:
    def test_it_cannot_raise(self):
        """It runs inside entry selection. A telemetry fault must not be able
        to stop a trade being placed."""
        src = inspect.getsource(bs.BrandonHydraStrategy._brandon_accel_cluster_for)
        assert "except Exception" in src
        assert "return None" in src

    def test_it_reads_the_same_cluster_set_the_adjuster_judged(self):
        """A different threshold would report a second opinion rather than
        the decision that was actually made."""
        src = inspect.getsource(bs.BrandonHydraStrategy._brandon_accel_cluster_for)
        assert "negative_clusters" in src
        assert "cfg.accel_min_pct" in src

    def test_it_returns_none_on_missing_inputs(self):
        s = bs.BrandonHydraStrategy.__new__(bs.BrandonHydraStrategy)

        class _Cfg:
            accel_min_pct = 0.1

        assert s._brandon_accel_cluster_for(None, 7755.0, _Cfg()) is None
        assert s._brandon_accel_cluster_for(object(), None, _Cfg()) is None

    def test_it_does_not_change_the_adjust_result_shape(self):
        """Threading the cluster through AdjustResult would alter a value the
        live decision path returns. It is re-derived precisely to avoid that."""
        from bots.hydra.brandon.gex_strike_adjuster import AdjustResult
        assert set(AdjustResult.__dataclass_fields__) == {
            "action", "new_strike", "reason"}


class TestTheRecorderStillWritesThem:
    def test_the_five_columns_are_written(self):
        src = inspect.getsource(bs.BrandonHydraStrategy._brandon_record_gex_decision)
        for col in ("cluster_low", "cluster_high", "cluster_peak",
                    "cluster_n_strikes", "cluster_strength_pct"):
            assert col in src, f"{col} is no longer written"
