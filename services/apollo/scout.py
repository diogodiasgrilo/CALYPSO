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

## HYDRA Strategy Parameters (v1.19.0 — DO NOT hallucinate)

- **3 base entries + 1 conditional (4 max)** at 10:15, 10:45, 11:15 ET. Conditional E6 at 14:00 fires as up-day put-only when SPX rises >= 0.48% above open (Upday-035). E7 is DISABLED.
- **Smart entry windows (MKT-031):** DISABLED (v1.10.4). Enter at scheduled times only.
- **VIX-scaled entry time shifting (MKT-034):** DISABLED (v1.10.3).
- **VIX-scaled spread width (MKT-027):** Continuous formula `round(VIX * 5.3 / 5) * 5`, floor 25pt, cap 85pt.
- **Starting OTM (MKT-024):** 3.5x calls, 4.0x puts (VIX-adjusted), scans inward via MKT-020/022
- **Min credit thresholds (MKT-011):** $1.35/side for calls, $2.10/side for puts. MKT-029 graduated fallback for BOTH sides: -$0.05, -$0.10 (call floor $0.75, put floor $2.07). MKT-038 call-only entries also use MKT-029 call floor. Put-only when call non-viable AND VIX < 15 (MKT-032/MKT-039). Call-only when put non-viable (MKT-040, 89% WR).
- **Stop formula:** Asymmetric buffers — call: total_credit + $0.35 (call_stop_buffer), put: total_credit + $1.55 (put_stop_buffer). MKT-040 call-only (put non-viable): call + $2.60 theo put + call buffer. Put-only (MKT-039): credit + $1.55 put buffer. MKT-038 call-only: call + $2.60 theo put + call buffer.
- **Stop confirmation (MKT-036):** DISABLED. Code preserved but dormant.
- **Stop close:** both legs closed via market order (default; configurable short_only_stop for MKT-025 mode)
- **Whipsaw filter:** whipsaw_range_skip_mult = 1.50 — skip entry if SPX intraday range > 1.5x expected daily move.
- **Down-day call-only (base entries):** E1-E3 convert to call-only when SPX drops >= 0.57% from open (`base_entry_downday_callonly_pct: 0.0057`).
- **Up-day filter (Upday-035):** E6 at 14:00 fires as put-only when SPX rises >= 0.48% above open. E7 is DISABLED.
- **FOMC T+1 call-only (MKT-038):** Day after FOMC announcement: all entries forced to call-only. T+1 = 66.7% down days, 23% more volatile.
- **FOMC announcement skip (MKT-008):** DISABLED (fomc_announcement_skip=false). HYDRA now trades on FOMC days. Day 1 trades normally.
- **Early close (MKT-018):** INTENTIONALLY DISABLED (backtest showed no ROC-based close beats hold-to-expiry)
- **Account:** $35K margin, 1 contract per entry

## 2026 FOMC Calendar (GROUND TRUTH — use these dates, do NOT guess)

| Meeting | Day 1 (trade normally) | Day 2 / Announcement (skip) | T+1 (call-only MKT-038) |
|---------|----------------------|----------------------------|--------------------------|
| Jan     | Jan 27 Tue           | Jan 28 Wed                 | Jan 29 Thu               |
| Mar     | Mar 17 Tue           | Mar 18 Wed                 | Mar 19 Thu               |
| Apr     | Apr 28 Tue           | Apr 29 Wed                 | Apr 30 Thu               |
| Jun     | Jun 16 Tue           | Jun 17 Wed                 | Jun 18 Thu               |
| Jul     | Jul 28 Tue           | Jul 29 Wed                 | Jul 30 Thu               |
| Sep     | Sep 15 Tue           | Sep 16 Wed                 | Sep 17 Thu               |
| Oct     | Oct 27 Tue           | Oct 28 Wed                 | Oct 29 Thu               |
| Dec     | Dec 8 Tue            | Dec 9 Wed                  | Dec 10 Thu               |

CRITICAL: Cross-reference today's date against this table. If today is NOT listed, it is NOT an FOMC day.

## Entry Skip Pattern (CRITICAL — do not get this backwards)

Entry #1 (10:15) has the RICHEST premium and BEST liquidity. It almost NEVER skips.
Entry #3 (11:15, the last base entry) is most likely to see MKT-011 skips as premium decays.
The call side is almost always the reason for skips (premium decays faster on calls).

Do NOT say "entries #1 and #2 carry the highest skip probability" — that is factually wrong.

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
or "MKT-011 may skip Entry #3 if call premium decays below $1.35."

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
