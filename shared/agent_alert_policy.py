"""Which agents are allowed to send you a Telegram message.

Added 2026-10-11, for a reason that is about attention rather than money. The
four narrative agents cost ~$10/month in API calls — 0.8% of one day's standard
deviation on B — but between them they sent ~32 Telegram messages a month that
the operator does not read. Those are two different costs, and until now they
were fused: APOLLO, HERMES and CLIO all gated on the SHARED top-level
``alerts.enabled``, so the only way to stop the pings was to stop the reports.

The reports are worth keeping even unread. **APOLLO's inputs are never stored**
— it calls ``fetch_market_snapshot()`` live and has no historical mode, so a
day not written is a day that cannot be reconstructed, ever. CLIO's inputs are
persisted, so its gaps are recoverable; HERMES's daily report is read by HOMER
into the journal. Silencing is reversible in both directions; not writing is
not.

HOMER already had a per-agent key (``telegram_alert``) and is honoured here so
the two patterns do not drift apart.

Resolution order, most specific first:

    config[agent]["alerts"]["enabled"]   explicit per-agent switch
    config[agent]["alerts"]              a bare bool, for brevity
    config[agent]["telegram_alert"]      HOMER's existing spelling
    config["alerts"]["enabled"]          the shared default
    False                                nothing said anywhere

This file is deliberately NOT part of ``shared/alert_service.py``: that module
is on the trading path and is imported by the bot, where a mistake costs more
than a missed notification.
"""

from __future__ import annotations

from typing import Any, Dict

__all__ = ["agent_alerts_enabled"]


def agent_alerts_enabled(config: Dict[str, Any], agent: str) -> bool:
    """Should ``agent`` send Telegram/email alerts?

    Args:
        config: the whole ``agents_config.json`` mapping.
        agent:  the agent's config key — "apollo", "hermes", "clio", "homer".

    Returns:
        True if this agent may alert. Silencing an agent does NOT stop it
        running or writing its report; it only stops the notification.
    """
    if not isinstance(config, dict):
        return False

    agent_cfg = config.get(agent)
    if isinstance(agent_cfg, dict):
        if "alerts" in agent_cfg:
            sub = agent_cfg["alerts"]
            if isinstance(sub, bool):
                return sub
            if isinstance(sub, dict) and "enabled" in sub:
                return bool(sub["enabled"])
        # HOMER's pre-existing spelling, honoured so the patterns don't diverge.
        if "telegram_alert" in agent_cfg:
            return bool(agent_cfg["telegram_alert"])

    shared = config.get("alerts")
    if isinstance(shared, dict):
        return bool(shared.get("enabled", False))
    return False
