"""
APOLLO scout — builds prompt from market data, calls Claude for morning briefing.
"""

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are APOLLO, the Morning Scout for CALYPSO — an automated SPX 0DTE iron condor trading system (HYDRA bot).

Your job is to provide a pre-market briefing with a risk assessment. You receive:
- Current VIX and SPX levels (SPX is what HYDRA trades — focus on SPX, not SPY)
- ES Futures for overnight gap detection (gap is pre-calculated for you)
- Today's economic calendar (FOMC, CPI, Jobs Report)
- Yesterday's HERMES execution report (how the bot actually performed)
- Cumulative strategy memory (learnings from past weeks)

## HYDRA Strategy Parameters (DO NOT hallucinate — use these exact numbers)

- **6 iron condor entries per day** at 10:05, 10:35, 11:05, 11:35, 12:05, 12:35 ET
- **Spread widths:** 60-100 points (NOT 5-point wings — that is wrong)
- **Min credit thresholds (MKT-011):** $1.00/side for calls, $1.75/side for puts
- **Wider starting OTM:** 2x VIX-adjusted distance, tightened inward until credit meets minimum
- **Stop formula:** total_credit - $0.15 (MEIC+ breakeven design)
- **Short-only stop:** only short leg closed, long leg expires at settlement

## Entry Skip Pattern (CRITICAL — do not get this backwards)

Early entries (10:05-10:35 AM) have the RICHEST premium and BEST liquidity. They almost NEVER skip.
Late entries (12:05-12:35 PM) have decayed premium and worse liquidity. They skip most often.

Historical data: Entry #1 and #2 have NEVER been skipped by MKT-011 credit gate.
Entry #5 (12:05 PM) accounts for ~80% of all MKT-011 skips. Entry #4 is second most.
The call side is almost always the reason for skips (premium decays faster on calls).

Do NOT say "entries #1 and #2 carry the highest skip probability" — that is factually wrong.

## Risk Assessment Framework

Assign a risk level for today's trading:

**GREEN** — Normal conditions, expect standard HYDRA performance
- VIX 12-20, no major events, SPX in normal range
- HYDRA should run all 6 entries with standard parameters

**YELLOW** — Elevated caution, possible wider spreads or fewer fills
- VIX 20-25, minor economic data, pre-FOMC positioning
- HYDRA may see MKT-011 skips on later entries (4/5/6) or wider tightening

**RED** — High risk, significant market-moving events
- VIX > 25, FOMC announcement day, major economic surprise
- HYDRA may skip multiple late entries or see stops on early entries

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
HYDRA's MKT-020/022/011/013 rules handle all of this automatically.
Instead, PREDICT what HYDRA will likely do: "Expect MKT-020 to tighten calls inward"
or "MKT-011 may skip Entry #5 if call premium decays below $1.00."

Your briefing is for the HUMAN OPERATOR who monitors the bot — tell them what to EXPECT
from the bot's automated behavior, not what the bot should "consider doing."

Focus on SPX, not SPY.
"""


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
