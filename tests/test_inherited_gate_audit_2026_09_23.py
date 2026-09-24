"""Inherited gates: does each strategy run only what its SOURCE specifies?

Prompted by variant H, which was built by subclassing the iron-condor family and
silently inherited an anti-whipsaw filter and an FOMC blackout its source never
mentions — the whipsaw one actively contrary to the thesis, since it skips
exactly the wide-range days a long strangle exists for. The operator asked the
obvious follow-up: *did that happen anywhere else?*

The audit (2026-09-23) found it had not, behaviourally — D and E carry the same
config keys but their entry path never reads them, and F is self-contained by
design. What it DID find was two things this file now pins:

1. **Documented schedules that the VIX regime silently overrides.** C and G both
   configure three entry slots and both logged `capped to 2 base entries` on
   2026-09-23. CLAUDE.md listed three for each. A's row had always shown the
   EFFECTIVE schedule, so the fleet documented the same fact two different ways.
2. **Dead config keys that state the opposite of policy.** D and E shipped
   `fomc_t1_skip_enabled: true` — "this strategy blacks out the day after FOMC" —
   while the operator decision of 2026-06-17 reads "Calendars D/E: keep trading
   FOMC". Inert, but one careless "fix" from becoming a live bug.

These tests are DERIVED over the taxonomy and the committed configs, not written
per-variant, because the lesson from the `variant_h_baseline_date` CI break is
that a guard naming the thing it guards protects only that thing.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import shared.strategy_taxonomy as tax  # noqa: E402

CONFIG_DIR = ROOT / "bots" / "hydra" / "config"
CALENDAR_SOURCES = (
    ROOT / "bots" / "hydra" / "calendar_strategy_base.py",
    ROOT / "bots" / "hydra" / "double_calendar_strategy.py",
    ROOT / "bots" / "hydra" / "spy_double_calendar_strategy.py",
)


def _cfg(vid: str):
    p = CONFIG_DIR / ("config.json" if vid == "a" else f"config_variant_{vid}.json")
    if not p.exists():                     # variant A's config is gitignored
        return None
    return json.load(p.open())["strategy"]


def _effective_slots(s) -> int:
    """How many base entries a variant ACTUALLY gets, after the VIX regime.

    `_apply_vix_regime_overrides` caps base entries and drops the EARLIEST, so
    the configured list is the canonical schedule and not necessarily the one
    that runs.
    """
    times = s.get("entry_times") or []
    vr = s.get("vix_regime") or {}
    if not vr.get("enabled"):
        return len(times)
    caps = vr.get("max_entries") or []
    return min(len(times), caps[0]) if caps else len(times)


# ======================================================================
# The calendars really do not read the IC gates
# ======================================================================

class TestTheCalendarsDoNotRunICGates:
    """D and E override `_initiate_entry`, and `_dc_pre_entry_gates` is
    deliberately "simpler than HYDRA's IC gates". The config keys are inert —
    asserted, because the whole audit turned on whether they were."""

    def test_whipsaw_appears_nowhere_in_calendar_code(self):
        for f in CALENDAR_SOURCES:
            assert "whipsaw" not in f.read_text().lower(), (
                f"{f.name} references the whipsaw filter — the calendars are "
                f"multi-day and an intraday-range gate does not apply to them")

    def test_the_fomc_t1_check_is_not_in_the_calendar_entry_path(self):
        for f in CALENDAR_SOURCES:
            assert "fomc_t1_skip_enabled" not in f.read_text(), f.name

    @pytest.mark.parametrize("vid", ["d", "e"])
    def test_the_dead_keys_are_explicit_and_off(self, vid):
        """Kept rather than deleted, so nobody re-adds them thinking the absence
        was an oversight — and set to the OFF value so a stray reader cannot
        find a live-looking number."""
        s = _cfg(vid)
        assert s["whipsaw_range_skip_mult"] is None
        assert s["fomc_t1_skip_enabled"] is False

    @pytest.mark.parametrize("vid", ["d", "e"])
    def test_the_fomc_key_agrees_with_the_documented_policy(self, vid):
        """2026-06-17: "Calendars D/E: keep trading FOMC — long-vega, benefit
        from elevated IV". Both FOMC knobs must therefore be false."""
        s = _cfg(vid)
        assert s["fomc_announcement_skip"] is False
        assert s["fomc_t1_skip_enabled"] is False

    @pytest.mark.parametrize("vid", ["d", "e"])
    def test_each_dead_key_carries_its_reason(self, vid):
        raw = (CONFIG_DIR / f"config_variant_{vid}.json").read_text()
        assert "_comment_whipsaw_INERT" in raw
        assert "_comment_fomc_t1_INERT" in raw


# ======================================================================
# Documented schedules must match the schedule that actually runs
# ======================================================================

class TestTheDocumentedScheduleMatchesReality:
    """C and G each configured three slots, ran two, and were documented as
    three. A was documented as its EFFECTIVE two. Same fact, two presentations,
    and the one that was wrong stayed wrong for weeks."""

    def test_the_cap_is_computed_not_assumed(self):
        """Pins the mechanism this whole class depends on."""
        for vid in ("c", "g"):
            s = _cfg(vid)
            if s is None:
                continue
            assert len(s["entry_times"]) == 3
            assert _effective_slots(s) == 2, (
                f"{vid}: expected the VIX regime to cap 3 slots to 2")

    def test_CLAUDE_md_does_not_advertise_a_capped_slot_as_running(self):
        """DERIVED: any variant whose regime caps it must not have its FIRST
        canonical (dropped) entry time presented as its schedule."""
        doc = (ROOT / "CLAUDE.md").read_text()
        offenders = []
        for vid in tax.available_ids():
            s = _cfg(vid)
            if s is None:
                continue
            times = s.get("entry_times") or []
            if not times or _effective_slots(s) >= len(times):
                continue                      # nothing is dropped
            dropped = times[: len(times) - _effective_slots(s)]
            row = next((ln for ln in doc.splitlines()
                        if ln.startswith(f"| {vid.upper()} |")), None)
            if row is None:
                continue
            # The row may MENTION the canonical schedule, but must also say the
            # cap applies — otherwise a reader takes the dropped slot as live.
            if any(d in row for d in dropped) and "cap" not in row.lower():
                offenders.append(vid)
        assert not offenders, (
            f"CLAUDE.md presents dropped entry slots as live for {offenders}. "
            f"The VIX regime caps base entries and drops the EARLIEST, so the "
            f"canonical list is not the schedule that runs.")

    def test_the_two_corrected_rows_say_so(self):
        doc = (ROOT / "CLAUDE.md").read_text()
        assert doc.count("capped to 2 base entries") >= 2, (
            "C's and G's rows should cite the log line that proves the cap")


# ======================================================================
# Only strategies whose SOURCE specifies a gate may run it
# ======================================================================

class TestSourceFaithfulness:
    """H's source (Tompkins) specifies no whipsaw filter and no event-day rule;
    F's (Ghauri) specifies neither either, and F is self-contained. G has NO
    source video at all — it was built as the modularity-audit driver — so it is
    the one variant for which "inherited unchanged" is a defensible choice, and
    its config says so explicitly rather than by omission."""

    def test_H_runs_neither_inherited_gate(self):
        s = _cfg("h")
        assert s["whipsaw_range_skip_mult"] is None
        assert s["fomc_t1_skip_enabled"] is False
        assert s["fomc_announcement_skip"] is False
        assert (s["vix_regime"] or {}).get("enabled") is False

    def test_F_is_self_contained(self):
        s = _cfg("f")
        assert s["whipsaw_range_skip_mult"] is None
        assert s["fomc_t1_skip_enabled"] is False
        src = (ROOT / "bots" / "hydra" / "ghauri_strategy.py").read_text()
        # Matched without spanning the docstring's line wrap.
        assert "super() on any of them" in src
        assert "fully self-contained" in src

    def test_G_inheriting_is_a_STATED_choice_not_an_omission(self):
        """G may keep the inherited block — it has no source to contradict — but
        the config must say that is deliberate, because the same silence on H
        was a defect."""
        raw = (CONFIG_DIR / "config_variant_g.json").read_text()
        assert "Inherited unchanged" in raw
        s = _cfg("g")
        assert (s["vix_regime"] or {}).get("enabled") is True

    def test_no_debit_strategy_runs_the_whipsaw_filter(self):
        """DERIVED over pnl_shape. Every net-debit strategy here wants MOVEMENT
        (H is long gamma; D/E are long vega), and the whipsaw filter skips
        wide-range days. It must never be live on one of them."""
        offenders = []
        for vid in tax.available_ids():
            if tax.STRATEGIES[vid].pnl_shape != "debit":
                continue
            s = _cfg(vid)
            if s is None:
                continue
            if s.get("whipsaw_range_skip_mult") is not None:
                offenders.append(vid)
        assert not offenders, (
            f"{offenders} are net-debit strategies with a live whipsaw filter — "
            f"it skips exactly the wide-range sessions they exist to capture.")


# ======================================================================
# F now reads the expected move its source actually means
# ======================================================================

class TestFUsesTheStraddleNotAFudgeFactor:
    """Ghauri marks the boundary from the OPTIONS MARKET'S OWN expected move,
    which on a 0DTE chain is the ATM straddle.

    F computed `spx_open x (vix/100)/sqrt(252)` instead — a de-annualised
    THIRTY-DAY implied vol, a different quantity that F's own config records a
    139-session replay measuring as overstating the real daily move by ~48% —
    and then corrected it with `em_multiplier: 0.50`. **That multiplier was a
    fudge factor standing in for a number the repo can now read directly**, via
    the `expected_move_from_straddle` helper built for variant H, which has the
    same source concept.
    """

    def test_the_config_selects_the_straddle(self):
        assert _cfg("f")["ghauri"]["expected_move_source"] == "straddle"

    def test_the_helper_is_shared_with_H_not_reimplemented(self):
        """Two strategies reading 'the expected move' must never disagree about
        the arithmetic — that can only ever be a config difference."""
        src = (ROOT / "bots" / "hydra" / "ghauri_strategy.py").read_text()
        assert "from bots.hydra.long_strangle_chain import" in src
        assert "expected_move_from_straddle" in src

    def test_there_is_no_silent_fallback_to_the_vix_formula(self):
        """Same rule as H. A VIX-derived boundary and a straddle-derived one are
        different distances; substituting one for the other yields a record that
        cannot be split back into two strategies."""
        src = (ROOT / "bots" / "hydra" / "ghauri_strategy.py").read_text()
        assert "NOT substituting the VIX formula" in src
        assert "NO FALLBACK BETWEEN THE TWO" in src

    def test_an_unpriceable_chain_costs_a_tick_not_the_day(self):
        """F sets boundaries ONCE per day but re-checks every heartbeat, so
        returning 0.0 simply retries — which is why no fallback is needed."""
        src = (ROOT / "bots" / "hydra" / "ghauri_strategy.py").read_text()
        assert "retrying next heartbeat" in src

    def test_the_old_behaviour_is_still_reachable_for_an_A_B(self):
        src = (ROOT / "bots" / "hydra" / "ghauri_strategy.py").read_text()
        assert 'if source == "vix":' in src
        assert "ghauri_em_multiplier" in src

    def test_an_unknown_source_refuses_rather_than_guessing(self):
        src = (ROOT / "bots" / "hydra" / "ghauri_strategy.py").read_text()
        assert "refusing to guess" in src

    def test_the_em_source_is_recorded_per_day(self):
        """Which definition drew today's boundary must be stored, or a later
        analysis cannot separate the two regimes."""
        src = (ROOT / "bots" / "hydra" / "ghauri_strategy.py").read_text()
        assert "_ghauri_em_source_today" in src


# ======================================================================
# 2026-09-24 sweep: the calm-entry filter is inert on the calendars too
# ======================================================================

class TestTheCalmEntryFilterIsInertOnTheCalendars:
    """Found by the "check every strategy for open items" sweep.

    MKT-043 delays an entry while the underlying is moving fast. It is applied
    inside ``HydraStrategy._initiate_entry``, and **D and E override that method
    in full**, so it never runs for them — yet both configs carried a live-looking
    ``calm_entry_threshold_pts: 15.0``.

    Same shape as the whipsaw and FOMC keys above, and the same reasoning: a
    premium-SELLING concept on a positive-vega calendar whose thesis is about IV
    rather than the last three minutes of price. **Neither source mentions it** —
    Burnich says only *"I put this on in the morning"*, and the OptionsKit video
    gives no timing condition at all.
    """

    @pytest.mark.parametrize("vid", ["d", "e"])
    def test_the_calendars_override_the_method_that_applies_it(self, vid):
        """The behavioural claim the whole finding rests on."""
        src = (ROOT / "bots" / "hydra" /
               ("double_calendar_strategy.py" if vid == "d"
                else "spy_double_calendar_strategy.py")).read_text()
        assert "def _initiate_entry" in src, (
            f"{vid} must override _initiate_entry, or MKT-043 DOES reach it")

    @pytest.mark.parametrize("vid", ["d", "e"])
    def test_the_keys_are_nulled_not_merely_unused(self, vid):
        s = _cfg(vid)
        for k in ("calm_entry_threshold_pts", "calm_entry_lookback_min",
                  "calm_entry_max_delay_min"):
            assert s.get(k) is None, f"{vid}.{k} still carries a live-looking value"

    @pytest.mark.parametrize("vid", ["d", "e"])
    def test_each_carries_its_reason(self, vid):
        raw = (CONFIG_DIR / f"config_variant_{vid}.json").read_text()
        assert "_comment_calm_entry_INERT" in raw


class TestDRunsBurnichsEndOfDayHalfClose:
    """The last rule of his D did not have — see
    tests/test_d_eod_scale_out_2026_09_24.py for the behaviour."""

    def test_it_is_enabled(self):
        assert _cfg("d")["double_calendar"]["eod_scale_out_enabled"] is True

    def test_D_is_sized_so_it_can_fire(self):
        assert _cfg("d")["contracts_per_entry"] >= 2
