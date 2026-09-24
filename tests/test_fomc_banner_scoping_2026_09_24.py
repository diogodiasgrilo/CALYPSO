"""The FOMC banner must describe the variant you are LOOKING at.

Found by the line-by-line dashboard audit of D/E/F/G — the one the operator
asked for after H's page had had one and the others never had.

**THE DEFECT.** ``FOMCBanner`` read ``fomc_announcement_skip`` /
``fomc_t1_skip_enabled`` from ``useHydraStore`` — the PRIMARY seat's state — for
every selection. The live seat is B, which **does** skip announcement days.
D, E, F, G and H all have ``fomc_announcement_skip: false`` and trade straight
through them.

So on an FOMC announcement day, selecting any of those five showed
*"HYDRA: All entries skipped (fomc_announcement_skip)"* while the strategy was
actively taking positions. The banner told the operator a strategy was flat on
precisely the day it was most exposed — and **G is an undefined-risk naked
strangle**, the one variant in the fleet whose downside is not bounded by
construction.

Same class as H's band chart plotting SPX against SPY strikes: a surface that is
confidently describing a different thing than the one on screen.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import shared.strategy_taxonomy as tax  # noqa: E402
import dashboard.backend.routers.strategies as sr  # noqa: E402

CFG = ROOT / "bots" / "hydra" / "config"
BANNER = ROOT / "dashboard" / "frontend" / "src" / "components" / "market" / "MarketContextBanner.tsx"


def _policy(vid: str):
    sr.settings.__dict__[f"variant_{vid}_config_file"] = str(
        CFG / (f"config_variant_{vid}.json" if vid != "a" else "config.json"))
    return sr._header_chrome(vid, tax.STRATEGIES[vid])["fomc_policy"]


class TestTheFleetGenuinelyDisagreesAboutFOMC:
    """The fact that makes a primary-scoped banner wrong rather than merely
    imprecise."""

    def test_the_live_seat_skips(self):
        assert _policy("b")["announcement_skip"] is True

    @pytest.mark.parametrize("vid", ["d", "e", "f", "g", "h"])
    def test_every_other_variant_TRADES_announcement_days(self, vid):
        assert _policy(vid)["announcement_skip"] is False, (
            f"{vid} was expected to trade FOMC announcement days; if this "
            f"changed, the banner's premise changed with it")

    def test_G_the_undefined_risk_variant_trades_them(self):
        """Called out separately because it is the one whose downside is not
        bounded by construction — the worst variant to misreport as flat."""
        assert _policy("g")["announcement_skip"] is False
        assert tax.STRATEGIES["g"].pnl_shape == "credit"


class TestThePolicyIsPublishedPerVariant:

    def test_the_snapshot_carries_it(self):
        p = _policy("g")
        assert set(p) == {"announcement_skip", "t1_skip"}

    def test_it_is_read_from_THAT_variants_config(self):
        """B and G disagree, so a shared source cannot produce both."""
        assert _policy("b")["announcement_skip"] != _policy("g")["announcement_skip"]

    def test_t1_is_carried_too(self):
        """G skips T+1 but not T+0 — a single boolean cannot express that."""
        g = _policy("g")
        assert g["t1_skip"] is True and g["announcement_skip"] is False


LIB = ROOT / "dashboard" / "frontend" / "src" / "lib" / "fomcPolicy.ts"


def _resolve(selected, primary):
    """Run the REAL resolver under node.

    The first version of this class asserted on the banner's SOURCE TEXT, and a
    build with `const policy = null` — the bug restored — kept two of three
    assertions green. String presence is not wiring. The logic now lives in a
    pure module so it can be executed, the same move `strangleVerdict.ts` made.
    """
    import json, shutil, subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node unavailable")
    probe = ROOT / "tests" / "_fomc_probe.mts"
    probe.write_text(
        f'import {{ resolveFomcPolicy }} from {json.dumps(str(LIB))};\n'
        f"console.log(JSON.stringify(resolveFomcPolicy("
        f"{json.dumps(selected)}, {json.dumps(primary)})));\n")
    try:
        r = subprocess.run([node, "--no-warnings", str(probe)],
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        probe.unlink(missing_ok=True)


class TestTheResolverPrefersTheSelection:
    """Executed, not grepped."""

    #: The live seat: skips announcement days.
    PRIMARY = {"fomc_announcement_skip": True, "fomc_t1_skip_enabled": True}

    def test_a_selected_variant_that_TRADES_overrides_the_skipping_primary(self):
        """The defect, stated exactly. Viewing G while B is the live seat must
        report G's policy, not B's."""
        out = self._g()
        assert out["announcement_skip"] is False

    def _g(self):
        return _resolve({"announcement_skip": False, "t1_skip": True}, self.PRIMARY)

    def test_the_selected_variants_T1_flag_is_used_too(self):
        assert self._g()["t1_skip"] is True

    def test_a_selected_variant_that_SKIPS_is_reported_as_skipping(self):
        out = _resolve({"announcement_skip": True, "t1_skip": True}, self.PRIMARY)
        assert out["announcement_skip"] is True

    def test_nothing_selected_falls_back_to_the_primary(self):
        """The default dashboard view must keep working rather than losing the
        banner entirely."""
        assert _resolve(None, self.PRIMARY) == {
            "announcement_skip": True, "t1_skip": True}

    def test_the_primary_fallback_keeps_its_long_standing_defaults(self):
        """t1 is ON unless explicitly false (the VM's config); announcement is
        OFF unless explicitly true. Changing either silently would alter what
        the default view has always claimed."""
        assert _resolve(None, {}) == {"announcement_skip": False, "t1_skip": True}

    def test_a_missing_primary_does_not_crash_the_banner(self):
        assert _resolve(None, None)["t1_skip"] is True

    def test_the_helper_imports_nothing(self):
        offenders = [ln for ln in LIB.read_text().splitlines()
                     if ln.lstrip().startswith(("import ", "require(", "from "))]
        assert not offenders, offenders

    def test_the_banner_uses_the_helper_rather_than_its_own_copy(self):
        src = BANNER.read_text()
        assert "resolveFomcPolicy" in src
        assert "hydraState?.fomc_announcement_skip === true" not in src, (
            "two copies of the resolution rule is how they drift apart")
