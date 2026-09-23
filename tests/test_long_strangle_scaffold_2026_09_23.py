"""Variant H (0DTE long strangle) — Step 1 scaffold: it must be registered and INERT.

Step 1 of `docs/NEW_STRATEGY_PLAYBOOK.md` is called "the riskiest step — not
because of the new strategy, but because adding a variant perturbs shared/global
state the live variants depend on." These tests are that claim's guard rails.

Two things are being protected:

1. **H cannot arm.** It is a scaffold with no entry logic, so a non-dry_run
   construction must raise BEFORE `super().__init__` reaches any broker I/O.
   Note the lock exists for a *different* reason from G's: G is locked because it
   carries undefined risk, H because it is unfinished. Its risk is bounded by the
   debit either way.

2. **A/B/C/F/G are untouched.** Adding a taxonomy row and a new comparability
   group must not move the live seat, must not change any existing variant's
   group, and must not let a net-debit strategy leak into the IC comparison math
   — the failure that hit the 2026-09-17 visual audit when F silently vanished
   from a comparison built on a hand-maintained id list.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bots.hydra.base_strategy import ConfigError  # noqa: E402
from bots.hydra.registry import available_strategies  # noqa: E402
from shared import strategy_taxonomy as tax  # noqa: E402

CONFIG = ROOT / "bots" / "hydra" / "config" / "config_variant_h.json"
UNIT = ROOT / "deploy" / "hydra_variant_h.service"


class TestItCannotArm:
    def test_non_dry_run_construction_raises(self):
        """THE lock. Must raise before super().__init__ touches the broker."""
        from bots.hydra.long_strangle_strategy import LongStrangleStrategy
        with pytest.raises(ConfigError, match="dry-run-LOCKED"):
            LongStrangleStrategy(config={}, dry_run=False)

    def test_missing_dry_run_kwarg_also_raises(self):
        """Absent must be treated as False, not as 'probably fine'."""
        from bots.hydra.long_strangle_strategy import LongStrangleStrategy
        with pytest.raises(ConfigError):
            LongStrangleStrategy(config={})

    def test_the_lock_names_the_reason_it_is_locked(self):
        """G is locked for undefined risk; H because it is unfinished. A future
        reader deciding whether to unlock must be able to tell which applies.

        The reason MOVES as the build progresses — at Step 1 it was "no entry
        logic", at Step 4 the missing exits, and since Step 5 it is that the code
        has never executed. What must not change is that the message states a
        specific unfinished thing AND points at where the gate lives, so nobody
        unlocks on the strength of a generic "not ready yet"."""
        src = (ROOT / "bots" / "hydra" / "long_strangle_strategy.py").read_text()
        assert "THIS CODE HAS NEVER RUN" in src
        # Step 10: every lock, docstring and the systemd unit name the audit.
        assert "H_GOLIVE_SCOPE_AND_AUDIT.md" in src

    def test_every_lock_points_at_the_go_live_gate(self):
        """Playbook Step 10: "wire the code to it" — both ConfigError lock
        messages, the module/class docstrings, and the systemd Description."""
        src = (ROOT / "bots" / "hydra" / "long_strangle_strategy.py").read_text()
        unit = UNIT.read_text()
        assert src.count("H_GOLIVE_SCOPE_AND_AUDIT.md") >= 4
        assert "H_GOLIVE_SCOPE_AND_AUDIT.md" in unit

    def test_the_audit_returns_an_explicit_verdict(self):
        """A NO-GO is a successful, honest outcome — but it has to be STATED,
        not implied by the absence of a GO."""
        audit = (ROOT / "docs" / "migration" / "H_GOLIVE_SCOPE_AND_AUDIT.md").read_text()
        assert "VERDICT — **NO-GO**" in audit
        assert "HG-1" in audit and "HG-10" in audit

    def test_config_ships_dry_run_true(self):
        cfg = json.loads(CONFIG.read_text())
        assert cfg["dry_run"] is True
        assert cfg["strategy"]["name"] == "long_strangle"

    def test_unit_is_not_installed_anywhere_in_repo_automation(self):
        """The unit file exists but nothing should auto-install or enable it."""
        assert UNIT.exists()
        assert "HYDRA_VARIANT_ID=h" in UNIT.read_text()


class TestItIsRegistered:
    def test_registry_resolves_the_name(self):
        assert "long_strangle" in available_strategies()

    def test_the_class_imports_and_is_a_hydra_strategy(self):
        from bots.hydra.long_strangle_strategy import LongStrangleStrategy
        from bots.hydra.strategy import HydraStrategy
        assert issubclass(LongStrangleStrategy, HydraStrategy)
        assert LongStrangleStrategy.BOT_NAME == "LONGSTRANGLE"

    def test_it_declares_no_protective_wings(self):
        """Both legs are LONG — there are no shorts for the base's naked-short
        guard to act on, and a 2-leg structure must not be read as half a broken
        iron condor."""
        from bots.hydra.long_strangle_strategy import LongStrangleStrategy
        assert LongStrangleStrategy.requires_protective_wings is False


class TestTheTaxonomyIsCoherent:
    def test_h_is_registered_as_debit_and_locked(self):
        m = tax.STRATEGIES["h"]
        assert m.strategy_class == "long_strangle"
        assert m.pnl_shape == "debit"
        assert m.status == "dry_run_locked"
        assert m.bot_name_base == "LONGSTRANGLE"

    def test_h_has_its_own_group_and_that_group_is_debit(self):
        """The group's pnl_shape is what excludes H from the IC comparison
        readers — if it ever reads 'credit', H's numbers get rendered with the
        sign inverted, because 'expired worthless' means profit there and MAXIMUM
        LOSS here."""
        assert tax.STRATEGIES["h"].group_id == "long_gamma_0dte"
        assert tax.GROUPS["long_gamma_0dte"].pnl_shape == "debit"

    def test_h_does_not_share_a_structure_family_with_g(self):
        """Identical leg geometry, opposite sign. Sharing G's family would invite
        IC-shaped consumers to render a debit position as a credit one."""
        assert tax.STRATEGIES["h"].structure_family != tax.STRATEGIES["g"].structure_family

    def test_the_new_group_is_not_comparable_yet(self):
        """One member and no renderer built — same call as undefined_risk_0dte."""
        assert tax.GROUPS["long_gamma_0dte"].comparable is False


class TestTheLiveVariantsAreUntouched:
    def test_the_live_seat_is_still_b(self):
        live = [v for v, m in tax.STRATEGIES.items() if m.status == "live"]
        assert live == ["b"], f"adding H moved the live seat: {live}"

    @pytest.mark.parametrize("vid,group", [
        ("a", "ic_0dte"), ("b", "ic_0dte"), ("c", "ic_0dte"),
        ("f", "ic_0dte"), ("g", "undefined_risk_0dte"),
        ("d", "calendar_multiday"), ("e", "calendar_multiday"),
    ])
    def test_existing_variants_kept_their_group(self, vid, group):
        assert tax.STRATEGIES[vid].group_id == group

    def test_h_is_excluded_from_the_ic_comparison_readers(self):
        """routers/variants.py::_readable_ids() skips any variant whose GROUP is
        debit — the long-standing mechanism that excludes D/E. H must be caught
        by the same rule rather than by a new hand-maintained exclusion, which is
        what silently dropped F in the 2026-09-17 audit."""
        assert tax.group("h").pnl_shape == "debit"

    def test_h_writes_to_its_own_isolated_paths(self):
        """No shared state with a live variant: its own data dir, log dir, config."""
        cfg = json.loads(CONFIG.read_text())
        assert cfg["logging"]["log_dir"] == "logs/hydra_variant_h"
        unit = UNIT.read_text()
        assert "data/variant_h" in unit or "variant_h" in unit
        assert "variant_g" not in unit


class TestItCannotPolluteTheCanonicalRecord:
    def test_alerts_and_sheets_are_off(self):
        cfg = json.loads(CONFIG.read_text())
        assert cfg["alerts"]["enabled"] is False
        assert cfg["google_sheets"]["enabled"] is False
        assert cfg["logging"]["google_sheets_enabled"] is False

    def test_it_paces_gently_on_the_shared_broker(self):
        """A/B/C/D/E/F/G/H all proxy through the one calypso-broker; a new variant
        must not push the combined rate toward the ~10 req/s ceiling."""
        cfg = json.loads(CONFIG.read_text())
        assert cfg["api_pacing_multiplier"] >= 2.0

    def test_no_stop_knobs_were_copied_in(self):
        """A long strangle's max loss is the debit. A stop buffer or a margin
        floor in this config would mean G's config was copied without thinking."""
        s = json.loads(CONFIG.read_text())["strategy"]
        assert not [k for k in s if "stop_buffer" in k or "buying_power" in k]


class TestItIsFaithfulToTheSource:
    """The inherited gating stack is an insurance-SELLER's, and two of its gates
    contradict a long-gamma strategy outright. Running H with them on would
    measure "the source's strategy minus its winners" — so they are off, and
    pinned here because turning one back on silently would invalidate the whole
    observation window without any test failing.
    """

    def _cfg(self):
        return json.loads(CONFIG.read_text())["strategy"]

    def test_the_whipsaw_filter_is_OFF(self):
        """It skips entries when the intraday range is wide. A wide range is the
        day H exists for. This is the single most consequential line in H's
        config."""
        assert self._cfg()["whipsaw_range_skip_mult"] is None

    def test_the_FOMC_next_day_blackout_is_OFF(self):
        """The day after a Fed announcement is frequently a big-move day, and
        the source specifies no FOMC handling at all."""
        assert self._cfg()["fomc_t1_skip_enabled"] is False

    def test_it_trades_the_announcement_day_too(self):
        assert self._cfg()["fomc_announcement_skip"] is False

    def test_the_expected_move_source_is_the_SOURCE_FAITHFUL_one(self):
        """The video reads the expected move off the option chain (the ATM
        straddle). The VIX formula is F's, and it disagrees by ~3x."""
        assert self._cfg()["long_strangle"]["expected_move_source"] == "straddle"

    def test_a_single_entry_near_the_open(self):
        """The source enters once, near the open — not on HYDRA's slot grid."""
        assert self._cfg()["entry_times"] == ["09:45"]

    def test_the_profit_target_is_the_sources_fifty_percent(self):
        assert self._cfg()["long_strangle"]["profit_target_pct_of_debit"] == 50

    def test_no_stop_knob_has_crept_in(self):
        """Max loss is the debit. A stop buffer appearing here would mean
        something was copied in from a credit variant."""
        cfg = self._cfg()
        for banned in ("call_stop_buffer", "put_stop_buffer", "narrow_spread_stop"):
            assert banned not in cfg, banned

    def test_the_reasoning_lives_in_the_config_not_only_in_a_test(self):
        """A future reader edits the config, not this file."""
        raw = CONFIG.read_text()
        assert "DISABLED (null) ON PURPOSE" in raw
        assert "a wide range is the day it EXISTS FOR" in raw
