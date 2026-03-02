"""
CLIO analyst — builds prompt from aggregated data, calls Claude for weekly analysis.
"""

import json
import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are CLIO, the Weekly Strategy Analyst & Optimizer for CALYPSO — an automated SPX 0DTE iron condor trading system (HYDRA bot).

Your job is to perform a deep weekly analysis and identify actionable improvements. You receive:
- All daily HERMES execution reports from the past week
- All APOLLO morning briefings from the past week
- Full Daily Summary history from Google Sheets
- Cumulative metrics (total P&L, entries, stops, winning/losing days)
- Previous CLIO weekly report (for continuity)
- Strategy memory (cumulative learnings from past weeks)

## Analysis Framework

### Part 1: Weekly Synthesis
- **P&L Attribution**: Break down net P&L by day, entry slot, and outcome type
- **VIX Regime**: How did VIX level affect performance? Which VIX ranges work best?
- **Entry Slot Analysis**: Which of the 6 entry times (10:05-12:35) performed best/worst?
- **Equity Curve**: Is the cumulative P&L trending up, down, or flat?
- **Benchmark vs SPX**: How did HYDRA perform vs simply holding SPX?

### Part 2: Apollo Accuracy Review
- Were morning risk assessments (GREEN/YELLOW/RED) predictive?
- GREEN days that lost money — what went wrong?
- RED days that made money — was the warning unnecessary?
- Suggestions to refine Apollo's risk model

### Part 3: Strategy Recommendations
- Specific parameter changes with confidence level (LOW/MEDIUM/HIGH)
- Supporting data for each recommendation
- Expected impact if implemented
- Only recommend changes with clear evidence (not speculation)

### Part 4: New Learnings for Strategy Memory
- 3-5 bullet points of durable knowledge discovered this week
- Must be specific and actionable (not generic trading wisdom)
- Format each learning as a standalone fact that will be useful in future weeks

## Output Format

Structure your report with clear markdown sections for Parts 1-4.

End with the new learnings wrapped in <learnings> tags:

<learnings>
- [specific learning 1]
- [specific learning 2]
- [specific learning 3]
</learnings>

These will be automatically appended to the strategy memory file.
"""


def analyze_weekly_data(
    client, data: Dict[str, Any], week_label: str, config: Dict[str, Any]
) -> Optional[Tuple[str, str]]:
    """
    Build prompt from aggregated data, call Claude, return (report, learnings).

    Args:
        client: Anthropic client.
        data: Aggregated data from data_aggregator.
        week_label: Week label like "2026-W09".
        config: Agent config.

    Returns:
        Tuple of (full_report, learnings_text), or None on error.
    """
    from shared.claude_client import ask_claude

    user_prompt = _build_user_prompt(data, week_label)

    model = config.get("anthropic", {}).get("model", "claude-sonnet-4-6")
    # Clio needs more output tokens for deep analysis
    max_tokens = 12288

    logger.info(f"Sending {len(user_prompt)} chars to Claude ({model}, max_tokens={max_tokens})")

    report = ask_claude(
        client,
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        model=model,
        max_tokens=max_tokens,
    )

    if not report:
        logger.error("Claude returned no response")
        return None

    # Extract learnings from <learnings> tags
    learnings = _extract_learnings(report)
    if not learnings:
        logger.warning("No <learnings> tags found in report")
        learnings = ""

    return report, learnings


def _build_user_prompt(data: Dict[str, Any], week_label: str) -> str:
    """Build the user prompt with all aggregated data."""
    sections = [f"# CLIO Weekly Analysis — {week_label}\n"]

    # HERMES reports
    hermes = data.get("hermes_reports", [])
    if hermes:
        sections.append(f"## Daily HERMES Reports ({len(hermes)} days)\n")
        for report in hermes:
            sections.append(f"### {report['date']}\n")
            # Truncate long reports
            content = report["content"]
            if len(content) > 3000:
                content = content[:3000] + "\n... (truncated)"
            sections.append(content)
            sections.append("")
    else:
        sections.append("## Daily HERMES Reports\nNo HERMES reports available.\n")

    # APOLLO reports
    apollo = data.get("apollo_reports", [])
    if apollo:
        sections.append(f"## Morning APOLLO Briefings ({len(apollo)} days)\n")
        for report in apollo:
            sections.append(f"### {report['date']}\n")
            content = report["content"]
            if len(content) > 2000:
                content = content[:2000] + "\n... (truncated)"
            sections.append(content)
            sections.append("")

    # Daily Summary history
    history = data.get("daily_summary_history")
    if history:
        sections.append(f"## Daily Summary History ({len(history)} rows)\n")
        sections.append("```json")
        # Last 20 rows to keep prompt manageable
        recent = history[-20:] if len(history) > 20 else history
        sections.append(json.dumps(recent, indent=2, default=str))
        sections.append("```\n")

    # Metrics
    if data.get("metrics"):
        sections.append("## Cumulative Metrics\n")
        sections.append("```json")
        sections.append(json.dumps(data["metrics"], indent=2, default=str))
        sections.append("```\n")

    # Previous Clio report
    if data.get("previous_clio"):
        prev = data["previous_clio"]
        if len(prev) > 4000:
            prev = prev[:4000] + "\n... (truncated)"
        sections.append("## Previous CLIO Report\n")
        sections.append(prev)
        sections.append("")

    # Strategy memory
    if data.get("strategy_memory"):
        sections.append("## Current Strategy Memory\n")
        sections.append(data["strategy_memory"])
        sections.append("")

    return "\n".join(sections)


def _extract_learnings(report: str) -> Optional[str]:
    """Extract text between <learnings> tags."""
    start = report.find("<learnings>")
    end = report.find("</learnings>")
    if start >= 0 and end > start:
        return report[start + len("<learnings>"):end].strip()
    return None
