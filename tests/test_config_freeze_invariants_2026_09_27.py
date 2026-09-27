"""Invariants locked at the 2026-09-27 config freeze.

WHY THESE ARE TESTS AND NOT JUST CONFIG. The freeze exists because B carries
**154 free parameters against 32 live trading days**, and its headline Sharpe
decayed 4.96 -> 2.37 purely as a tail day arrived (the daily MEAN was flat:
$208.74 first half vs $196.05 second half). Under those conditions any knob
that drifts back silently is indistinguishable from fitting. A test makes a
change deliberate: you have to come here and argue with the reasoning.

Each invariant below is a SAFETY property that holds regardless of P&L — the
test for "is this a fix or a tune" is *would I make this change if the P&L had
come out the other way*, and every one of these passes it.

See docs/GO_LIVE_MASTER.md, the config-freeze section.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_CFG = Path(__file__).resolve().parents[1] / "bots" / "hydra" / "config"

# The ic_0dte variants that hold, or will hold, a real seat: the live paper
# seat (b), its shadow (c), and the real-money variant (bm).
SEATED = ["config_variant_b.json", "config_variant_c.json", "config_variant_bm.json"]


def _load(name):
    with open(_CFG / name) as f:
        return json.load(f)


@pytest.mark.parametrize("name", SEATED)
class TestCalmEntryIsGone:
    """MKT-043 fired ZERO times in 101 days and was removed 2026-09-27.

    Not for the parameter count — because a path that has never executed, and
    only activates in the most stressed conditions, is not protection. It is
    untested code waiting for a crisis, the same class of thing as the
    over-fill correction found dead on the morning of 2026-09-27.

    There is no `enabled` flag: the gate in strategy.py is "all three keys
    present and not None", so removing the keys IS the off switch, and it is
    behaviourally a no-op because it never fired.

    To bring it back, calibrate the threshold so it fires a few times a MONTH
    and therefore gets exercised — then delete this test deliberately.
    """

    def test_no_calm_entry_keys_remain(self, name):
        s = _load(name).get("strategy", {})
        present = sorted(k for k in s if k.startswith("calm_entry"))
        assert not present, (
            f"{name}: MKT-043 keys are back: {present}. The gate is "
            "'all three present', so this re-arms an untested code path.")

    def test_all_three_keys_are_required_to_arm_it(self, name):
        """Pin the gate's shape, so a partial re-add cannot half-arm it."""
        s = _load(name).get("strategy", {})
        armed = all(s.get(k) is not None for k in (
            "calm_entry_lookback_min", "calm_entry_threshold_pts",
            "calm_entry_max_delay_min"))
        assert not armed


class TestEntryCapInUnobservedVixZones:
    """B ran max_entries [7,7,7,7] — no cap in ANY regime.

    B has never traded above **VIX 19.0**. Zones 2 (22-28) and 3 (>=28) have
    ZERO observed days, so the upper half of that array is configuration that
    has never executed.

    It also compounds: above VIX 22, Brandon widens spreads 5pt -> 10pt, so the
    A2 %-of-width stop doubles from $1,400 to $2,800 per side while the entry
    count stays at 7 — 7 entries x 2 sides x $2,800 = **$39,200** theoretical
    daily max, in a regime never observed.

    The cap [7,7,3,1] holds DOLLAR exposure roughly flat across the width
    doubling instead of letting it double silently. That rule is mechanical,
    not fitted to any P&L — which is exactly why it belongs in a freeze.

    The tested zones (0 and 1) are deliberately UNCHANGED at 7: freezing means
    not touching what has actually been running.
    """

    def test_b_and_bm_cap_the_unobserved_zones(self):
        for name in ("config_variant_b.json", "config_variant_bm.json"):
            me = _load(name)["strategy"]["vix_regime"]["max_entries"]
            assert len(me) == 4, f"{name}: expected 4 VIX zones, got {me}"
            assert me[0] == 7 and me[1] == 7, (
                f"{name}: zones 0/1 are the OBSERVED ones and must stay at 7 "
                f"— the freeze does not re-tune what has been running. Got {me}")
            assert me[2] <= 3, (
                f"{name}: zone 2 (VIX 22-28) is unobserved AND runs 10pt "
                f"spreads. Got {me[2]}, expected <= 3.")
            assert me[3] <= 1, (
                f"{name}: zone 3 (VIX >= 28) is unobserved. Got {me[3]}.")

    def test_the_cap_holds_dollar_exposure_flat_across_the_width_doubling(self):
        """The RULE, not the number — so a future edit has to break the logic.

        Zone 0/1 run 5pt spreads; zone 2+ run 10pt. Per-entry structural risk
        therefore doubles. Max daily exposure must not increase with it.
        """
        for name in ("config_variant_b.json", "config_variant_bm.json"):
            me = _load(name)["strategy"]["vix_regime"]["max_entries"]
            narrow = me[0] * 5     # entries x width, observed zones
            wide = me[2] * 10      # entries x width, unobserved zone 2
            assert wide <= narrow, (
                f"{name}: zone 2 permits {wide}pt of total width vs {narrow}pt "
                f"in the observed zones — exposure RISES where we have no data.")

    def test_c_stays_capped(self):
        me = _load("config_variant_c.json")["strategy"]["vix_regime"]["max_entries"]
        assert me == [2, 2, 2, 1], f"C's cap drifted: {me}"


class TestTheRealMoneyVariantTracksTheLiveSeat:
    """bm runs the same Brandon code as B, so it must not quietly diverge.

    bm inherited B's 10pt cushion and B's uncapped [7,7,7,7] precisely because
    it was copied and then forgotten. It is the one variant where an ITM
    settlement is not paper money.
    """

    def test_bm_matches_b_on_the_frozen_safety_knobs(self):
        b = _load("config_variant_b.json")["strategy"]
        bm = _load("config_variant_bm.json")["strategy"]
        assert (bm["eod_flatten"]["skip_otm_pts"]
                == b["eod_flatten"]["skip_otm_pts"])
        assert (bm["vix_regime"]["max_entries"]
                == b["vix_regime"]["max_entries"])
        assert not [k for k in bm if k.startswith("calm_entry")]

    def test_bm_is_still_dry_run(self):
        """Nothing in this change may arm real money."""
        assert _load("config_variant_bm.json").get("dry_run") is True
