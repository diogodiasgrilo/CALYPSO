"""
HERMES analyzer — builds prompt from collected data, calls Claude, extracts summary.
"""

import json
import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are HERMES, the Daily Execution Quality Analyst for CALYPSO — an automated SPX 0DTE iron condor trading system (HYDRA bot).

Your job is to analyze today's trading execution and provide actionable insights. You receive:
- Apollo's morning market briefing (if available)
- Google Sheets data (Daily Summary + individual position entries)
- HYDRA's state file and cumulative metrics
- Journal logs from the trading session

## Analysis Framework

1. **Market Context vs Outcome Correlation**
   - Did Apollo's morning risk assessment match actual results?
   - Were GREEN days profitable? Were RED warnings heeded?

2. **Entry Quality Analysis**
   - Fill slippage per entry (estimated credit vs actual fill)
   - Credit gate activity (MKT-011 skips, MKT-020/022 tightening steps)
   - Entry timing — which of the 6 slots performed best/worst?

3. **Stop Loss Analysis**
   - Stop loss slippage (trigger level vs actual close cost)
   - Which sides (call/put) were stopped more often?
   - Were stops appropriate given market conditions?

4. **P&L Reconciliation**
   - Verify: Expired Credits - Stop Loss Debits - Commission = Net P&L
   - Flag any discrepancies

5. **Key Insights** (3-5 bullet points)
   - What worked well today?
   - What could be improved?
   - Any patterns worth tracking?

## Output Format

Write your analysis as a structured markdown report with clear sections.
End with a 5-line summary block wrapped in <summary> tags that will be sent as a Telegram alert.

Example:
<summary>
HERMES Daily Report — Mar 01
Net P&L: +$285 (4 expired, 2 stopped)
Best entry: #2 (+$95), Worst: #4 (stopped, -$45)
VIX: 18.2, all entries full IC, no MKT-011 skips
Insight: Put stops clustered in 11:30-12:00 window
</summary>
"""


def analyze_daily_data(
    client, data: Dict[str, Any], today_str: str, config: Dict[str, Any]
) -> Optional[Tuple[str, str]]:
    """
    Build prompt from collected data, call Claude, return (full_report, summary).

    Args:
        client: Anthropic client.
        data: Collected data dict from data_collector.
        today_str: Date string "YYYY-MM-DD".
        config: Agent config.

    Returns:
        Tuple of (full_report_markdown, summary_text), or None on error.
    """
    from shared.claude_client import ask_claude

    user_prompt = _build_user_prompt(data, today_str)

    model = config.get("anthropic", {}).get("model", "claude-sonnet-4-6")
    max_tokens = config.get("anthropic", {}).get("max_tokens", 4096)

    logger.info(f"Sending {len(user_prompt)} chars to Claude ({model})")

    full_report = ask_claude(
        client,
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        model=model,
        max_tokens=max_tokens,
    )

    if not full_report:
        logger.error("Claude returned no response")
        return None

    # Extract summary from <summary> tags
    summary = _extract_summary(full_report)
    if not summary:
        # Fallback: use first 5 lines of report
        lines = full_report.strip().split("\n")
        summary = "\n".join(lines[:5])
        logger.warning("No <summary> tags found, using first 5 lines")

    return full_report, summary


def _build_user_prompt(data: Dict[str, Any], today_str: str) -> str:
    """Build the user prompt with all collected data."""
    sections = [f"# HERMES Daily Analysis — {today_str}\n"]

    # Apollo morning report
    if data.get("apollo_report"):
        sections.append("## Apollo Morning Briefing\n")
        sections.append(data["apollo_report"])
        sections.append("")

    # Daily Summary from Sheets
    if data.get("daily_summary"):
        sections.append("## Daily Summary (Google Sheets)\n")
        sections.append("```json")
        sections.append(json.dumps(data["daily_summary"], indent=2, default=str))
        sections.append("```\n")

    # Positions from Sheets
    if data.get("positions"):
        sections.append("## Position Entries (Google Sheets)\n")
        sections.append("```json")
        sections.append(json.dumps(data["positions"], indent=2, default=str))
        sections.append("```\n")

    # State file
    if data.get("state"):
        sections.append("## HYDRA State File\n")
        sections.append("```json")
        sections.append(json.dumps(data["state"], indent=2, default=str))
        sections.append("```\n")

    # Metrics
    if data.get("metrics"):
        sections.append("## Cumulative Metrics\n")
        sections.append("```json")
        sections.append(json.dumps(data["metrics"], indent=2, default=str))
        sections.append("```\n")

    # Journal logs (truncate if too long)
    if data.get("journal_logs"):
        log_text = data["journal_logs"]
        if len(log_text) > 8000:
            log_text = log_text[-8000:]
            sections.append("## Journal Logs (last 8000 chars)\n")
        else:
            sections.append("## Journal Logs\n")
        sections.append("```")
        sections.append(log_text)
        sections.append("```\n")

    if len(sections) <= 1:
        sections.append("No trading data available for today.\n")
        sections.append("Provide a brief note that no data was found.\n")

    return "\n".join(sections)


def _extract_summary(report: str) -> Optional[str]:
    """Extract text between <summary> tags."""
    start = report.find("<summary>")
    end = report.find("</summary>")
    if start >= 0 and end > start:
        return report[start + len("<summary>"):end].strip()
    return None
