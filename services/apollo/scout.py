"""
APOLLO scout — builds prompt from market data, calls Claude for morning briefing.
"""

import logging
from typing import Any, Dict, Optional, Tuple

# Shared strategy context (single source of truth for all agents)
from services.agents_shared import inject_strategy_context

logger = logging.getLogger(__name__)

_PROMPT_TEMPLATE = """You are APOLLO, the Morning Scout for CALYPSO — an automated SPX 0DTE iron condor trading system (HYDRA bot).

Your job is to provide a pre-market briefing with a risk assessment. You receive:
- Current VIX and SPX levels (SPX is what HYDRA trades — focus on SPX, not SPY)
- ES Futures for overnight gap detection (gap is pre-calculated for you)
- Today's economic calendar (FOMC, CPI, Jobs Report)
- Yesterday's HERMES execution report (how the bot actually performed)
- Cumulative strategy memory (learnings from past weeks)

{STRATEGY_CONTEXT}

## APOLLO-Specific Guidance

When reporting pre-market VIX, identify which VIX regime will apply today and state the expected entry count (e.g., "VIX at 24 places HYDRA in regime 2: 2 entries will fire with lowered credits").

## Risk Assessment Framework

Assign a risk level for today's trading:

**GREEN** — Normal conditions, expect standard HYDRA performance
- VIX 12-20, no major events, SPX in normal range
- HYDRA should run all 3 base entries with standard parameters

**YELLOW** — Elevated caution, possible wider spreads or fewer fills
- VIX 20-25, minor economic data, pre-FOMC positioning
- HYDRA may see MKT-011 skip on Entry #3 or wider tightening

**RED** — High risk, significant market-moving events
- VIX > 25, major economic surprise
- HYDRA may skip entries or see stops on early entries

## Output Format

Write a concise morning briefing (200-400 words) with:

1. **Market Snapshot** — VIX interpretation, SPX level, overnight gap (use the pre-calculated gap data)
2. **Today's Calendar** — Events from the economic calendar
3. **Risk Level** — GREEN/YELLOW/RED with reasoning
4. **Yesterday's Context** — Key takeaway from HERMES report (if available)
5. **HYDRA Expectations** — Expected behavior, which entries may skip (usually late ones), potential stops

Start your response with EXACTLY this format on the FIRST line (no code blocks):

RISK: GREEN
or
RISK: YELLOW
or
RISK: RED

IMPORTANT: HYDRA is a FULLY AUTOMATED bot — it makes all decisions algorithmically.
Do NOT say things like "consider pushing strikes wider" or "HYDRA should be prepared to skip."
HYDRA's MKT-020/022/011/013/035/038 rules handle all of this automatically.
Instead, PREDICT what HYDRA will likely do: "Expect MKT-020 to tighten calls inward"
or "MKT-011 may skip Entry #3 if call premium decays below $2.00."

Your briefing is for the HUMAN OPERATOR who monitors the bot — tell them what to EXPECT
from the bot's automated behavior, not what the bot should "consider doing."

Focus on SPX, not SPY.
"""

# Inject shared strategy context from services/hydra_strategy_context.md
SYSTEM_PROMPT = inject_strategy_context(_PROMPT_TEMPLATE)


def generate_briefing(
    client, market_data: Dict[str, Any], context: Dict[str, Any], config: Dict[str, Any]
) -> Optional[Tuple[str, str]]:
    """
    Build prompt and call Claude for morning briefing.

    Args:
        client: Anthropic client.
        market_data: Dict with vix, spx, es_futures.
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

    # Extract risk level from first few lines (Claude may wrap in code block)
    risk_level = "UNKNOWN"
    for line in briefing.strip().split("\n")[:5]:
        line_upper = line.strip().upper()
        for level in ["GREEN", "YELLOW", "RED"]:
            if f"RISK: {level}" in line_upper or f"RISK:{level}" in line_upper:
                risk_level = level
                break
        if risk_level != "UNKNOWN":
            break

    return briefing, risk_level


def _build_user_prompt(market_data: Dict[str, Any], context: Dict[str, Any]) -> str:
    """Build the user prompt with market data and context wrapped in XML tags."""
    from shared.market_hours import get_us_market_time

    now_et = get_us_market_time()
    sections = [f"# Apollo Morning Scout — {now_et.strftime('%Y-%m-%d %H:%M ET')}\n"]
    sections.append("Analyze ONLY the data provided in the <data> sections below. Do not invent any numbers.\n")

    # Market snapshot
    vix = market_data.get("vix")
    spx = market_data.get("spx")
    es = market_data.get("es_futures")
    gap_pts = market_data.get("gap_points")
    gap_pct = market_data.get("gap_pct")
    sections.append("<data source=\"market_snapshot\">")
    sections.append(f"- VIX: {vix if vix else 'unavailable'}")
    sections.append(f"- SPX (last close): {spx:.2f}" if spx else "- SPX: unavailable")
    sections.append(f"- ES Futures (trading now): {es:.2f}" if es else "- ES Futures: unavailable")
    if gap_pts is not None and gap_pct is not None:
        direction = "UP" if gap_pts > 0 else "DOWN" if gap_pts < 0 else "FLAT"
        sections.append(f"- Overnight gap: {gap_pts:+.1f} points ({gap_pct:+.2f}%) — {direction}")
    sections.append("</data>\n")

    # Economic calendar
    events = context.get("events", [])
    sections.append("<data source=\"economic_calendar\">")
    if events:
        for event in events:
            days = event.get("days_until", "?")
            desc = event.get("description", "Unknown event")
            event_type = event.get("type", "")
            prefix = "TODAY" if days == 0 else f"in {days}d"
            sections.append(f"- [{prefix}] {event_type}: {desc}")
    else:
        sections.append("No major events in the next 7 days.")
    sections.append("</data>\n")

    # Yesterday's HERMES report
    if context.get("hermes_report"):
        sections.append("<data source=\"yesterday_hermes_report\">")
        sections.append(context["hermes_report"])
        sections.append("</data>\n")

    # Strategy memory
    if context.get("strategy_memory"):
        sections.append("<data source=\"strategy_memory\">")
        sections.append(context["strategy_memory"])
        sections.append("</data>\n")

    return "\n".join(sections)
