"""
APOLLO scout — builds prompt from market data, calls Claude for morning briefing.
"""

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are APOLLO, the Morning Scout for CALYPSO — an automated SPX 0DTE iron condor trading system (HYDRA bot).

Your job is to provide a pre-market briefing with a risk assessment. You receive:
- Current VIX, SPY, and S&P futures levels
- Today's economic calendar (FOMC, CPI, Jobs Report, earnings)
- Yesterday's HERMES execution report (how the bot actually performed)
- Cumulative strategy memory (learnings from past weeks)

## Risk Assessment Framework

Assign a risk level for today's trading:

**GREEN** — Normal conditions, expect standard HYDRA performance
- VIX 12-20, no major events, SPX in normal range
- HYDRA should run all 6 entries with standard parameters

**YELLOW** — Elevated caution, possible wider spreads or fewer fills
- VIX 20-25, minor economic data, pre-FOMC positioning
- HYDRA may see MKT-011 skips or wider tightening (MKT-020/022)

**RED** — High risk, significant market-moving events
- VIX > 25, FOMC announcement day, major economic surprise
- HYDRA may skip entries or see multiple stops

## Output Format

Write a concise morning briefing (200-400 words) with:

1. **Market Snapshot** — VIX level interpretation, SPX direction, overnight range
2. **Today's Calendar** — Events from the economic calendar
3. **Risk Level** — GREEN/YELLOW/RED with reasoning
4. **Yesterday's Context** — Key takeaway from HERMES report (if available)
5. **HYDRA Expectations** — Expected spread widths, credit levels, potential issues

Start with the risk level on the first line:

```
RISK: GREEN | YELLOW | RED
```

Keep it actionable and specific to HYDRA's iron condor strategy.
"""


def generate_briefing(
    client, market_data: Dict[str, Any], context: Dict[str, Any], config: Dict[str, Any]
) -> Optional[Tuple[str, str]]:
    """
    Build prompt and call Claude for morning briefing.

    Args:
        client: Anthropic client.
        market_data: Dict with vix, spy, es_futures.
        context: Dict with hermes_report, strategy_memory, events.
        config: Agent config.

    Returns:
        Tuple of (full_briefing, risk_level), or None on error.
    """
    from shared.claude_client import ask_claude

    user_prompt = _build_user_prompt(market_data, context)

    model = config.get("anthropic", {}).get("model", "claude-sonnet-4-6")
    max_tokens = config.get("anthropic", {}).get("max_tokens", 4096)

    logger.info(f"Sending {len(user_prompt)} chars to Claude ({model})")

    briefing = ask_claude(
        client,
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        model=model,
        max_tokens=max_tokens,
    )

    if not briefing:
        logger.error("Claude returned no response")
        return None

    # Extract risk level from first line
    risk_level = "UNKNOWN"
    first_line = briefing.strip().split("\n")[0]
    for level in ["GREEN", "YELLOW", "RED"]:
        if level in first_line.upper():
            risk_level = level
            break

    return briefing, risk_level


def _build_user_prompt(market_data: Dict[str, Any], context: Dict[str, Any]) -> str:
    """Build the user prompt with market data and context."""
    from shared.market_hours import get_us_market_time

    now_et = get_us_market_time()
    sections = [f"# Apollo Morning Scout — {now_et.strftime('%Y-%m-%d %H:%M ET')}\n"]

    # Market snapshot
    sections.append("## Current Market Data\n")
    vix = market_data.get("vix")
    spy = market_data.get("spy")
    es = market_data.get("es_futures")
    sections.append(f"- VIX: {vix if vix else 'unavailable'}")
    sections.append(f"- SPY: ${spy:.2f}" if spy else "- SPY: unavailable")
    sections.append(f"- ES Futures: {es:.2f}" if es else "- ES Futures: unavailable")
    sections.append("")

    # Economic calendar
    events = context.get("events", [])
    if events:
        sections.append("## Economic Calendar (Next 7 Days)\n")
        for event in events:
            days = event.get("days_until", "?")
            desc = event.get("description", "Unknown event")
            event_type = event.get("type", "")
            prefix = "TODAY" if days == 0 else f"in {days}d"
            sections.append(f"- [{prefix}] {event_type}: {desc}")
        sections.append("")
    else:
        sections.append("## Economic Calendar\nNo major events in the next 7 days.\n")

    # Yesterday's HERMES report
    if context.get("hermes_report"):
        sections.append("## Yesterday's HERMES Report\n")
        sections.append(context["hermes_report"])
        sections.append("")

    # Strategy memory
    if context.get("strategy_memory"):
        sections.append("## Strategy Memory (Cumulative Learnings)\n")
        sections.append(context["strategy_memory"])
        sections.append("")

    return "\n".join(sections)
