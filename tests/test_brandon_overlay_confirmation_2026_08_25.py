"""2026-08-25: hedge-arm confirmation delay, severity bypass, and the staged
(C-first) GEX-gate rollout switch.

Before this, _brandon_check_overlay placed a hedge the instant evaluate_overlay
returned a qualifying proposal — no persistence check, unlike every other
trigger-sensitive path in this codebase (MKT-046's stop-loss confirmation,
_brandon_check_pctwidth_shadow_stop's %-of-width shadow). These tests cover
the new confirmation state machine (mirroring _brandon_check_pctwidth_shadow_
stop's placed-only-on-confirm pattern — NOT MKT-046, which an earlier design
draft incorrectly reached for and which adversarial review caught as wrong),
the severity bypass, and the use_adjuster_gex_gate staging switch that keeps
B's hedge-arming behavior unchanged until a deliberate promotion decision.
"""
import os
import sys
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.brandon.gex_provider import GEXCluster, build_profile  # noqa: E402
from tests.test_brandon_strategy_integration import _make_instance  # noqa: E402


class _FakeProfile:
    """Minimal duck-typed GEXProfile stand-in — lets a test hand-pick an
    exact cluster peak location instead of reverse-engineering build_profile's
    real OI-weighted-gamma clustering (which, with uniform OI, concentrates
    its peak near spot — the opposite of what a "distant peak" test needs)."""

    def __init__(self, negative=(), positive=(), expiry=date(2026, 5, 5)):
        self._negative = negative
        self._positive = positive
        self.expiry = expiry
        self.fetched_at = datetime(2026, 5, 5, 10, 0, tzinfo=timezone.utc)

    def negative_clusters(self, min_strength_pct=0.05):
        return self._negative

    def positive_clusters(self, min_strength_pct=0.05):
        return self._positive


def _entry(entry_number=1):
    from unittest.mock import MagicMock
    e = MagicMock()
    e.entry_number = entry_number
    e.contracts = 1
    e.short_call_strike = 6840
    e.long_call_strike = 6915
    e.short_put_strike = 6760
    e.long_put_strike = 6685
    e.call_side_stopped = False
    e.put_side_stopped = False
    e.call_side_expired = False
    e.put_side_expired = False
    e.call_side_skipped = False
    e.put_side_skipped = False
    e.call_side_pivot_closed = False
    e.put_side_pivot_closed = False
    return e


def _profile_with_call_accel(peak_near=6840, fetched_at=None):
    """Real accel cluster whose contiguous run — and peak — sit close to the
    threatened 6840 short (within any reasonable locality gate)."""
    return build_profile(
        [
            {"details": {"strike_price": peak_near - 10, "contract_type": "call"},
             "open_interest": 80000, "greeks": {"gamma": 0.001}},
            {"details": {"strike_price": peak_near, "contract_type": "call"},
             "open_interest": 80000, "greeks": {"gamma": 0.001}},
            {"details": {"strike_price": peak_near + 10, "contract_type": "call"},
             "open_interest": 80000, "greeks": {"gamma": 0.001}},
        ],
        spot=6820, expiry=date(2026, 5, 5), time_to_expiry=1 / 365.0,
        fetched_at=fetched_at,
    )


def _profile_with_distant_call_accel():
    """A cluster covering (and thus, under the ungated legacy check, trivially
    confirming) the 6840 threatened short — but whose actual |GEX| peak sits
    ~300pt away, the shape the audit found the original check's missing
    locality gate would rubber-stamp."""
    cluster = GEXCluster(strike_low=6830, strike_high=7150, total_gex=-1e9, peak_strike=7140)
    return _FakeProfile(negative=(cluster,))


def _make_overlay_instance(**overrides):
    base = dict(
        brandon_gex_enabled=True, brandon_overlay_enabled=True,
        brandon_overlay_butterfly_cutoff_hour=23,  # force morning → debit spread
        brandon_overlay_butterfly_cutoff_minute=59,
        current_price=6820,
    )
    base.update(overrides)
    inst = _make_instance(**base)
    inst._brandon_estimate_t_years_to_close = lambda: 1.0 / 365.0
    inst._brandon_get_gex_profile = lambda d, **_kw: _profile_with_call_accel()
    return inst


class TestConfirmationDelay:
    def test_confirm_seconds_zero_fires_immediately(self):
        inst = _make_overlay_instance(brandon_overlay_confirm_seconds=0.0)
        inst._brandon_now_et = lambda: datetime(2026, 5, 5, 11, 0)
        e = _entry()
        inst._brandon_check_overlay(e)

        assert (1, "call") in inst._brandon_overlay_placed
        assert len(inst._brandon_hedge_legs.get(1, [])) == 2

    def test_confirm_seconds_positive_does_not_fire_on_first_tick(self):
        inst = _make_overlay_instance(brandon_overlay_confirm_seconds=10.0)
        inst._brandon_now_et = lambda: datetime(2026, 5, 5, 11, 0, 0)
        e = _entry()
        inst._brandon_check_overlay(e)

        assert (1, "call") not in inst._brandon_overlay_placed
        assert inst._brandon_hedge_legs.get(1, []) == []
        assert (1, "call") in inst._brandon_overlay_trigger_first_seen_at

    def test_pending_timer_does_not_block_reevaluation(self):
        # The bug an earlier design draft would have shipped: adding the key
        # to _brandon_overlay_placed on the FIRST qualifying tick (before
        # confirmation) would make the dedup check at the top of the loop
        # permanently skip this (entry, side) — the pending timer could then
        # never reach its threshold. Verify a second tick, still short of
        # confirm_seconds, is actually re-evaluated (pending, not skipped).
        inst = _make_overlay_instance(brandon_overlay_confirm_seconds=10.0)
        t0 = datetime(2026, 5, 5, 11, 0, 0)
        inst._brandon_now_et = lambda: t0
        e = _entry()
        inst._brandon_check_overlay(e)
        first_seen = inst._brandon_overlay_trigger_first_seen_at[(1, "call")]

        inst._brandon_now_et = lambda: datetime(2026, 5, 5, 11, 0, 5)  # +5s, still < 10s
        inst._brandon_check_overlay(e)

        assert (1, "call") not in inst._brandon_overlay_placed
        assert inst._brandon_hedge_legs.get(1, []) == []
        # Timer must NOT have been reset by the second (still-qualifying) tick.
        assert inst._brandon_overlay_trigger_first_seen_at[(1, "call")] == first_seen

    def test_fires_once_confirm_seconds_elapses(self):
        inst = _make_overlay_instance(brandon_overlay_confirm_seconds=10.0)
        inst._brandon_now_et = lambda: datetime(2026, 5, 5, 11, 0, 0)
        e = _entry()
        inst._brandon_check_overlay(e)

        inst._brandon_now_et = lambda: datetime(2026, 5, 5, 11, 0, 10)  # exactly 10s
        inst._brandon_check_overlay(e)

        assert (1, "call") in inst._brandon_overlay_placed
        assert len(inst._brandon_hedge_legs.get(1, [])) == 2
        # Timer entry cleared once confirmed.
        assert (1, "call") not in inst._brandon_overlay_trigger_first_seen_at

    def test_recovery_clears_pending_timer_and_a_later_retrigger_waits_full_window(self):
        inst = _make_overlay_instance(brandon_overlay_confirm_seconds=10.0)
        e = _entry()

        inst._brandon_now_et = lambda: datetime(2026, 5, 5, 11, 0, 0)
        inst._brandon_check_overlay(e)
        assert (1, "call") in inst._brandon_overlay_trigger_first_seen_at

        # Threat recovers (no accel zone / profile None) — pending timer clears.
        inst._brandon_get_gex_profile = lambda d, **_kw: None
        inst._brandon_now_et = lambda: datetime(2026, 5, 5, 11, 0, 8)
        inst._brandon_check_overlay(e)
        assert (1, "call") not in inst._brandon_overlay_trigger_first_seen_at
        assert (1, "call") not in inst._brandon_overlay_placed

        # Threat re-appears 1s later — must start a FRESH confirmation window,
        # not inherit the earlier (now-cleared) elapsed time.
        inst._brandon_get_gex_profile = lambda d, **_kw: _profile_with_call_accel()
        inst._brandon_now_et = lambda: datetime(2026, 5, 5, 11, 0, 9)
        inst._brandon_check_overlay(e)
        assert (1, "call") not in inst._brandon_overlay_placed
        second_first_seen = inst._brandon_overlay_trigger_first_seen_at[(1, "call")]
        assert second_first_seen == datetime(2026, 5, 5, 11, 0, 9)

        # Even 9s after the ORIGINAL first sighting (11:00:00), only 1s has
        # elapsed on the fresh timer — must not fire yet.
        inst._brandon_now_et = lambda: datetime(2026, 5, 5, 11, 0, 10)
        inst._brandon_check_overlay(e)
        assert (1, "call") not in inst._brandon_overlay_placed


class TestSeverityBypass:
    def test_severity_bypass_fires_immediately_despite_confirm_seconds(self):
        inst = _make_overlay_instance(
            brandon_overlay_confirm_seconds=10.0,
            brandon_overlay_severity_bypass_distance_pts=5.0,
            brandon_overlay_trigger_distance_pts=25.0,
            current_price=6836,  # 4pt from the 6840 short — inside the 5pt severity band
        )
        # Cluster must sit entirely above spot (6836) for the call-side accel
        # check to register — the default fixture's cluster (peak_near=6840,
        # spans 6830-6850) straddles this spot, so use one shifted further out.
        inst._brandon_get_gex_profile = lambda d, **_kw: _profile_with_call_accel(peak_near=6850)
        e = _entry()
        inst._brandon_check_overlay(e)

        assert (1, "call") in inst._brandon_overlay_placed
        assert len(inst._brandon_hedge_legs.get(1, [])) == 2
        assert (1, "call") not in inst._brandon_overlay_trigger_first_seen_at

    def test_outside_severity_band_still_waits_for_confirmation(self):
        inst = _make_overlay_instance(
            brandon_overlay_confirm_seconds=10.0,
            brandon_overlay_severity_bypass_distance_pts=5.0,
            brandon_overlay_trigger_distance_pts=25.0,
            current_price=6820,  # 20pt from short — within trigger, outside the 5pt band
        )
        e = _entry()
        inst._brandon_check_overlay(e)

        assert (1, "call") not in inst._brandon_overlay_placed
        assert (1, "call") in inst._brandon_overlay_trigger_first_seen_at

    def test_zero_severity_band_never_bypasses(self):
        inst = _make_overlay_instance(
            brandon_overlay_confirm_seconds=10.0,
            brandon_overlay_severity_bypass_distance_pts=0.0,
            current_price=6839,  # 1pt from short — would be "severe" under any positive band
        )
        e = _entry()
        inst._brandon_check_overlay(e)
        assert (1, "call") not in inst._brandon_overlay_placed


class TestUseAdjusterGexGateStaging:
    """The master switch that keeps B's hedge-arming behavior unchanged
    (flat 0.05 threshold, no locality gate) until use_adjuster_gex_gate is
    deliberately turned on — required because brandon_accel_min_pct /
    accel_peak_locality_pts are ALREADY live-tuned, non-default values on
    both B and C, so reusing them for the hedge unconditionally would be a
    live behavior change on B, not a no-op."""

    def test_gate_off_preserves_legacy_rubber_stamp_on_a_distant_peak(self):
        inst = _make_overlay_instance(
            brandon_overlay_use_adjuster_gex_gate=False,
            brandon_accel_peak_locality_pts=25.0,
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: _profile_with_distant_call_accel()
        e = _entry()
        inst._brandon_check_overlay(e)

        # Legacy behavior: any qualifying cluster confirms, however far its
        # peak sits — the hedge fires despite the distant peak.
        assert (1, "call") in inst._brandon_overlay_placed

    def test_gate_on_rejects_the_same_distant_peak(self):
        inst = _make_overlay_instance(
            brandon_overlay_use_adjuster_gex_gate=True,
            brandon_accel_peak_locality_pts=25.0,
            brandon_accel_min_pct=0.10,
            brandon_accel_peak_persistence_enabled=False,
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: _profile_with_distant_call_accel()
        e = _entry()
        inst._brandon_check_overlay(e)

        assert (1, "call") not in inst._brandon_overlay_placed
        assert inst._brandon_hedge_legs.get(1, []) == []

    def test_gate_on_still_confirms_a_local_peak(self):
        inst = _make_overlay_instance(
            brandon_overlay_use_adjuster_gex_gate=True,
            brandon_accel_peak_locality_pts=25.0,
            brandon_accel_min_pct=0.10,
        )
        # _profile_with_call_accel's cluster peak sits right at the 6840 short.
        e = _entry()
        inst._brandon_check_overlay(e)
        assert (1, "call") in inst._brandon_overlay_placed


class TestOverlayGexProfileRotation:
    """Regression coverage for a real bug the 2026-08-25 audit-after pass
    found in the FIRST version of this fix: a single-pointer "rotate on
    first touch" design self-consumes at the overlay's actual call cadence
    (_brandon_check_overlay runs every ~2-5s monitoring tick, for EVERY
    active entry — up to 7 concurrent slots on B — while a GEX profile is
    only refetched every ~180s). Only the very first call after a fresh
    fetch got a real "prior != current" comparison; every later call that
    tick, and every tick until the next refresh, saw prior==current and got
    force_unconfirmed=True — starving persistence confirmation almost
    permanently once enabled (exactly C's current staged trial:
    use_adjuster_gex_gate=True + accel_peak_persistence_enabled=True). The
    fix is a true 2-slot ring buffer (_current/_prior) that only advances on
    a genuinely NEW fetch, so _prior stays valid for the whole ~180s
    lifetime of the current profile."""

    def test_prior_stays_available_across_many_calls_on_the_same_profile(self):
        inst = _make_instance()
        p1 = SimpleNamespace(fetched_at=datetime(2026, 8, 25, 10, 0, tzinfo=timezone.utc))
        p2 = SimpleNamespace(fetched_at=datetime(2026, 8, 25, 10, 3, tzinfo=timezone.utc))

        # First-ever fetch of the day: no prior yet (legacy-trust path).
        prior, force = inst._brandon_overlay_rotate_prior_gex_profile(p1)
        assert prior is None and force is False

        # A genuinely NEW fetch (p2) arrives — p1 becomes the prior.
        prior, force = inst._brandon_overlay_rotate_prior_gex_profile(p2)
        assert prior is p1 and force is False

        # Simulate many more calls still seeing p2 (more entries the same
        # tick, or later ticks before the NEXT refresh) — none may lose
        # access to p1 as a valid, non-force_unconfirmed prior. This is the
        # exact scenario the bug broke: only the call above (the "first
        # touch" of p2) used to get a real prior.
        for _ in range(10):
            prior, force = inst._brandon_overlay_rotate_prior_gex_profile(p2)
            assert prior is p1 and force is False

    def test_a_third_independent_fetch_rotates_again(self):
        inst = _make_instance()
        p1 = SimpleNamespace(fetched_at=datetime(2026, 8, 25, 10, 0, tzinfo=timezone.utc))
        p2 = SimpleNamespace(fetched_at=datetime(2026, 8, 25, 10, 3, tzinfo=timezone.utc))
        p3 = SimpleNamespace(fetched_at=datetime(2026, 8, 25, 10, 6, tzinfo=timezone.utc))

        inst._brandon_overlay_rotate_prior_gex_profile(p1)
        inst._brandon_overlay_rotate_prior_gex_profile(p2)
        prior, force = inst._brandon_overlay_rotate_prior_gex_profile(p3)
        assert prior is p2 and force is False  # p1 rotated out, p2 is now the prior

    def test_none_profile_does_not_disturb_the_ring_buffer(self):
        inst = _make_instance()
        p1 = SimpleNamespace(fetched_at=datetime(2026, 8, 25, 10, 0, tzinfo=timezone.utc))
        p2 = SimpleNamespace(fetched_at=datetime(2026, 8, 25, 10, 3, tzinfo=timezone.utc))
        inst._brandon_overlay_rotate_prior_gex_profile(p1)
        inst._brandon_overlay_rotate_prior_gex_profile(p2)

        prior, force = inst._brandon_overlay_rotate_prior_gex_profile(None)  # e.g. Polygon outage tick
        assert prior is p1 and force is False  # unchanged, not wiped

        # Recovery — p2 is still "current", so calling with p2 again changes nothing.
        prior, force = inst._brandon_overlay_rotate_prior_gex_profile(p2)
        assert prior is p1 and force is False


class TestPersistenceGateSurvivesMultipleEvaluationsOnSameProfile:
    """End-to-end reproduction of the starvation bug through the real
    _brandon_check_overlay entry point, matching C's actual staged-trial
    config (use_adjuster_gex_gate + accel_peak_persistence_enabled both
    True). A THIRD entry evaluated on the same cached profile as a second
    entry (i.e., the profile has already been "touched" once since its own
    arrival) must still confirm — under the pre-fix rotation it would not."""

    def test_third_entry_on_an_already_touched_profile_still_confirms(self):
        inst = _make_overlay_instance(
            brandon_overlay_use_adjuster_gex_gate=True,
            brandon_accel_peak_locality_pts=25.0,
            brandon_accel_min_pct=0.10,
            brandon_accel_peak_persistence_enabled=True,
            brandon_accel_peak_persistence_tolerance_pts=10.0,
            brandon_overlay_confirm_seconds=0.0,  # isolate the GEX gate from the timer
        )
        profile_a = _profile_with_call_accel(
            peak_near=6840, fetched_at=datetime(2026, 5, 5, 10, 0, tzinfo=timezone.utc))
        profile_b = _profile_with_call_accel(  # a new, independent read agreeing with A
            peak_near=6842, fetched_at=datetime(2026, 5, 5, 10, 3, tzinfo=timezone.utc))

        inst._brandon_get_gex_profile = lambda d, **_kw: profile_a
        inst._brandon_check_overlay(_entry(1))  # tick 1: no prior yet, legacy-trust, fires
        assert (1, "call") in inst._brandon_overlay_placed

        inst._brandon_get_gex_profile = lambda d, **_kw: profile_b
        inst._brandon_check_overlay(_entry(2))  # profile_b is NEW -> rotates, prior=profile_a, confirms
        assert (2, "call") in inst._brandon_overlay_placed

        # Entry #3, evaluated on the SAME profile_b that entry #2 just
        # "touched" — this is exactly where the bug forced non-confirmation.
        inst._brandon_check_overlay(_entry(3))
        assert (3, "call") in inst._brandon_overlay_placed


class TestIndependentStructureTogglesEndToEnd:
    """End-to-end (via the real _brandon_check_overlay) coverage of the
    debit_spread_enabled/butterfly_enabled config wiring — complements the
    pure evaluate_overlay-level tests in test_brandon_defensive_overlay.py
    by proving the strategy actually threads its config-read attributes
    into OverlayConfig correctly."""

    def test_debit_spread_disabled_on_b_style_config_places_nothing_in_the_morning(self):
        inst = _make_overlay_instance(
            brandon_overlay_debit_spread_enabled=False,
            brandon_overlay_butterfly_cutoff_hour=23,  # keep it in the morning window
            brandon_overlay_butterfly_cutoff_minute=59,
        )
        e = _entry()
        inst._brandon_check_overlay(e)

        assert (1, "call") not in inst._brandon_overlay_placed
        assert inst._brandon_hedge_legs.get(1, []) == []

    def test_butterfly_disabled_places_nothing_in_the_afternoon(self):
        inst = _make_overlay_instance(
            brandon_overlay_butterfly_enabled=False,
            brandon_overlay_butterfly_cutoff_hour=0,  # force afternoon window
            brandon_overlay_butterfly_cutoff_minute=0,
        )
        e = _entry()
        inst._brandon_check_overlay(e)

        assert (1, "call") not in inst._brandon_overlay_placed
        assert inst._brandon_hedge_legs.get(1, []) == []

    def test_default_config_unaffected_still_places_the_morning_debit_spread(self):
        inst = _make_overlay_instance(
            brandon_overlay_butterfly_cutoff_hour=23,
            brandon_overlay_butterfly_cutoff_minute=59,
        )
        e = _entry()
        inst._brandon_check_overlay(e)
        assert (1, "call") in inst._brandon_overlay_placed

    def test_watch_log_reports_disabled_structure_instead_of_a_misleading_gex_confirmed(self, caplog):
        # Audit-after finding: computing gex_confirmed on a window whose
        # structure is disabled logged a misleading "gex_confirmed=True" for
        # a hedge that could never fire regardless of GEX. The watch line
        # must say the structure is disabled instead.
        inst = _make_overlay_instance(
            brandon_overlay_debit_spread_enabled=False,
            brandon_overlay_butterfly_cutoff_hour=23,
            brandon_overlay_butterfly_cutoff_minute=59,
            current_price=6822,  # 18pt from the 6840 short — inside the 2x=50pt watch band
        )
        e = _entry()
        with caplog.at_level("INFO"):
            inst._brandon_check_overlay(e)

        watch_lines = [r.message for r in caplog.records if "BRANDON-OVERLAY-WATCH" in r.message]
        assert len(watch_lines) == 1
        assert "debit_spread DISABLED for this window" in watch_lines[0]
        assert "gex_confirmed" not in watch_lines[0]
