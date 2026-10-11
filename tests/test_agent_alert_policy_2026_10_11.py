"""Silencing an agent must stop its NOTIFICATION, never its report.

The operator reads none of the four narrative agents' reports but is unsure
whether they will later. The reports are therefore worth keeping — decisively
so for APOLLO, whose inputs are never stored (`fetch_market_snapshot()` is
live, there is no historical mode), making an unwritten day permanently
unreconstructable. What is NOT worth keeping is ~32 Telegram messages a month
that nobody opens.

Those were fused: APOLLO, HERMES and CLIO all gated on the shared top-level
`alerts.enabled`, so the only way to stop the pings was to stop the reports.
This splits them.

The two tests that matter most are the back-compat ones. A config that says
nothing per-agent must behave EXACTLY as before, and HOMER's pre-existing
`telegram_alert` key must keep working — otherwise this change silently
alters alerting for agents it was never meant to touch, which is the one way
a notification tweak could become a real incident.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.agent_alert_policy import agent_alerts_enabled  # noqa: E402

SHARED_ON = {"alerts": {"enabled": True, "email": "x@y.z"}}
SHARED_OFF = {"alerts": {"enabled": False}}


class TestBackCompat:
    """Nothing per-agent configured => exactly the old behaviour."""

    @pytest.mark.parametrize("agent", ["apollo", "hermes", "clio", "homer"])
    def test_shared_on_means_on(self, agent):
        assert agent_alerts_enabled(SHARED_ON, agent) is True

    @pytest.mark.parametrize("agent", ["apollo", "hermes", "clio", "homer"])
    def test_shared_off_means_off(self, agent):
        assert agent_alerts_enabled(SHARED_OFF, agent) is False

    def test_homers_existing_telegram_alert_key_still_wins(self):
        """HOMER shipped with `telegram_alert`. If this change broke it, the
        journal-failure alert would go silent — the one notification that
        actually matters."""
        cfg = dict(SHARED_ON, homer={"telegram_alert": False})
        assert agent_alerts_enabled(cfg, "homer") is False
        cfg = dict(SHARED_OFF, homer={"telegram_alert": True})
        assert agent_alerts_enabled(cfg, "homer") is True

    def test_a_config_with_no_alerts_block_at_all_is_off(self):
        assert agent_alerts_enabled({}, "apollo") is False

    def test_a_non_dict_config_does_not_explode(self):
        assert agent_alerts_enabled(None, "apollo") is False
        assert agent_alerts_enabled([], "apollo") is False


class TestPerAgentOverride:

    def test_an_agent_can_be_silenced_while_the_shared_flag_stays_on(self):
        cfg = dict(SHARED_ON, apollo={"alerts": {"enabled": False}})
        assert agent_alerts_enabled(cfg, "apollo") is False
        assert agent_alerts_enabled(cfg, "hermes") is True, "silencing one must not touch the others"

    def test_an_agent_can_be_enabled_while_the_shared_flag_is_off(self):
        cfg = dict(SHARED_OFF, clio={"alerts": {"enabled": True}})
        assert agent_alerts_enabled(cfg, "clio") is True

    def test_a_bare_bool_is_accepted(self):
        assert agent_alerts_enabled(dict(SHARED_ON, apollo={"alerts": False}), "apollo") is False
        assert agent_alerts_enabled(dict(SHARED_OFF, apollo={"alerts": True}), "apollo") is True

    def test_other_keys_in_the_agent_block_are_ignored(self):
        """An agent block full of unrelated settings must not be read as a
        silencing instruction."""
        cfg = dict(SHARED_ON, apollo={"report_dir": "intel/apollo", "model": "x"})
        assert agent_alerts_enabled(cfg, "apollo") is True

    def test_an_empty_alerts_dict_falls_through_to_shared(self):
        cfg = dict(SHARED_ON, apollo={"alerts": {}})
        assert agent_alerts_enabled(cfg, "apollo") is True


class TestTheAgentsActuallyUseIt:
    """Source checks are weak evidence on their own — but these assert the
    OLD gate is gone, which is what would silently keep the pings coming."""

    @pytest.mark.parametrize("agent", ["apollo", "hermes", "clio"])
    def test_the_agent_calls_the_policy_with_its_own_name(self, agent):
        src = (ROOT / "services" / agent / "main.py").read_text()
        assert f'agent_alerts_enabled(config, "{agent}")' in src

    @pytest.mark.parametrize("agent", ["apollo", "hermes", "clio"])
    def test_the_old_shared_only_gate_is_gone(self, agent):
        src = (ROOT / "services" / agent / "main.py").read_text()
        assert 'config.get("alerts", {}).get("enabled", False)' not in src

    def test_no_agent_got_another_agents_name(self):
        """Copy-paste between three near-identical blocks is the obvious way
        to get this wrong, and it would silence the wrong agent."""
        for agent in ("apollo", "hermes", "clio"):
            src = (ROOT / "services" / agent / "main.py").read_text()
            for other in ("apollo", "hermes", "clio"):
                if other != agent:
                    assert f'agent_alerts_enabled(config, "{other}")' not in src


class TestSilencingDoesNotStopTheReport:
    """The whole point. If silencing skipped the write, APOLLO's record would
    be permanently lost — its inputs are not stored anywhere."""

    @pytest.mark.parametrize("agent", ["apollo", "hermes", "clio"])
    def test_the_gate_sits_after_the_report_is_written(self, agent):
        src = (ROOT / "services" / agent / "main.py").read_text()
        gate = src.index(f'agent_alerts_enabled(config, "{agent}")')
        writes = [src.find(tok) for tok in ("f.write(", ".write_text(", "save_report", "_write_report")]
        writes = [w for w in writes if w != -1]
        assert writes, f"{agent}: no report-write call found to order against"
        assert min(writes) < gate, (
            f"{agent}: the alert gate precedes the report write — silencing would "
            "stop the report, which is exactly what must not happen")
