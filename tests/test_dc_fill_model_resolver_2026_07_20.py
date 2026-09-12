"""2026-07-20: dry-run fill-model resolver (root-cause fix for the silently-ignored
fill model).

D's config wrote ``strategy.double_calendar.dry_run_fill_model: 0.5`` (a scalar, in the
sub-block next to the other DC knobs), but the inline read only accepted a dict at the
strategy level (``cfg.get("dry_run_fill_model", {}).get("aggressiveness")``), so the 0.5
was silently ignored and D ran at full-touch 1.0 for 11 days. ``_resolve_dc_fill_model``
honors the value at either location and in either format, and always logs it.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.calendar_strategy_base import CalendarStrategyBase  # noqa: E402


class _Stub:
    """Bare object to receive the resolver's writes (the method only touches
    self._dc_fill_agg / self._dc_fill_slippage + the module logger)."""


def _resolve(cfg, sub):
    s = _Stub()
    CalendarStrategyBase._resolve_dc_fill_model(s, cfg, sub)
    return s._dc_fill_agg, s._dc_fill_slippage


class TestDcFillModelResolver:
    def test_scalar_under_subblock_is_the_D_bug(self):
        # The exact D scenario: scalar 0.5 in the double_calendar sub-block.
        agg, slip = _resolve({}, {"dry_run_fill_model": 0.5})
        assert agg == 0.5 and slip == 0.0

    def test_dict_at_strategy_level(self):
        agg, slip = _resolve(
            {"dry_run_fill_model": {"aggressiveness": 0.5, "extra_slippage_per_leg": 0.1}}, {}
        )
        assert agg == 0.5 and slip == 0.1

    def test_dict_under_subblock(self):
        agg, slip = _resolve({}, {"dry_run_fill_model": {"aggressiveness": 0.3}})
        assert agg == 0.3 and slip == 0.0

    def test_subblock_wins_over_strategy_level(self):
        agg, _ = _resolve(
            {"dry_run_fill_model": {"aggressiveness": 1.0}},
            {"dry_run_fill_model": 0.25},
        )
        assert agg == 0.25

    def test_absent_defaults_to_full_touch(self):
        # E's real config (no fill model anywhere) must stay at 1.0.
        agg, slip = _resolve({}, {})
        assert agg == 1.0 and slip == 0.0

    def test_bool_is_not_interpreted_as_a_number(self):
        # bool is a subclass of int; False must NOT become aggressiveness 0.0.
        assert _resolve({}, {"dry_run_fill_model": False}) == (1.0, 0.0)
        assert _resolve({}, {"dry_run_fill_model": True}) == (1.0, 0.0)
