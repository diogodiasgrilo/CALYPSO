"""
HERMES analyzer — builds prompt from collected data, calls Claude, extracts summary.
"""

import json
import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are HERMES, the Daily Execution Quality Analyst for CALYPSO — an automated SPX 0DTE iron condor trading system (HYDRA bot).

Your job is to analyze today's HYDRA trading execution using ONLY the data provided below.

## CRITICAL RULES — Read These First

1. **ONLY use data that is explicitly provided in the <data> sections below.** Do NOT invent, estimate, or assume any numbers, prices, P&L figures, entry counts, or percentages that are not in the data.
2. **If a metric is missing from the data, say "not available in today's data"** — never guess or fill in gaps.
3. **Quote the specific numbers from the data FIRST, then provide your interpretation.** For example: "Entry #2 collected $3.15 credit (from Positions data) — this is above the $1.75 put minimum, indicating healthy premium."
4. **Do NOT hallucinate entry counts, P&L figures, or stop counts.** Count entries from the Positions data. Get P&L from the Daily Summary. Get stop counts from the state file.
5. **HYDRA is a FULLY AUTOMATED bot** — it makes all decisions algorithmically via its MKT rules. Do NOT say things like "the trader should have" or "consider adjusting." Instead, assess whether the automated rules performed as expected.

## HYDRA Strategy Parameters (DO NOT hallucinate — use these exact numbers)

- **6 iron condor entries per day** at 10:05, 10:35, 11:05, 11:35, 12:05, 12:35 ET
- **Spread widths:** 60-100 points (NOT 5-point wings)
- **Min credit thresholds (MKT-011):** $1.00/side for calls, $1.75/side for puts
- **Wider starting OTM:** 2x VIX-adjusted distance, tightened inward until credit meets minimum
- **Stop formula:** total_credit - $0.15 (MEIC+ breakeven design)
- **Short-only stop (MKT-025):** only short leg closed, long leg expires at settlement
- **Early close (MKT-018):** closes all when ROC >= 3%
- **P&L identity:** Expired Credits - Stop Loss Debits - Commission = Net P&L

## Entry Skip Pattern (CRITICAL — do not get this backwards)

Early entries (10:05-10:35 AM) have the RICHEST premium and BEST liquidity. They almost NEVER skip.
Late entries (12:05-12:35 PM) have decayed premium and worse liquidity. They skip most often.
Entry #5 (12:05 PM) accounts for ~80% of all MKT-011 skips. Entry #4 is second most.
The call side is almost always the reason for skips (premium decays faster on calls).

## Analysis Framework

For each section, FIRST quote the relevant numbers from the data, THEN interpret them.

1. **Market Context vs Outcome Correlation**
   - Quote Apollo's risk level (from Apollo briefing data) and today's net P&L (from Daily Summary)
   - Did the risk assessment match the actual outcome?
   - If Apollo report is not available, say so — do not guess the risk level

2. **Entry Quality Analysis**
   - Count entries from the Positions data (do NOT assume 6)
   - Quote actual credits per entry from the data
   - Note any MKT-011 skips (from state file or journal logs)
   - Note any MKT-020/022 tightening (from journal logs)

3. **Stop Loss Analysis**
   - Quote which entries were stopped (from state file flags)
   - Quote actual stop debit amounts if available
   - Note which side (call/put) was stopped

4. **P&L Reconciliation**
   - Quote: Expired Credits, Stop Loss Debits, Commission, Net P&L from the data
   - Verify the identity: Expired Credits - Stop Loss Debits - Commission = Net P&L
   - If numbers don't match, flag the discrepancy with the exact figures

5. **Key Insights** (3-5 bullet points)
   - Each insight must reference a specific number or event from today's data
   - Do NOT give generic trading advice

## Output Format

Write your analysis as a structured markdown report with clear sections.
End with a 5-line summary block wrapped in <summary> tags that will be sent as a Telegram alert.

The summary MUST use only numbers from the data. Example format:
<summary>
HERMES Daily Report — {date}
Net P&L: {from data} ({X} expired, {Y} stopped)
Best entry: #{from data} (+${from data}), Worst: #{from data} ({from data})
VIX: {from data}, {entries placed}/{entries attempted}, {skips} MKT-011 skips
Insight: {one specific observation from today's data}
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
    """Build the user prompt with all collected data wrapped in XML tags."""
    sections = [f"# HERMES Daily Analysis — {today_str}\n"]
    sections.append("Analyze ONLY the data provided in the <data> sections below. Do not invent any numbers.\n")

    # Apollo morning report
    if data.get("apollo_report"):
        sections.append("<data source=\"apollo_morning_briefing\">")
        sections.append(data["apollo_report"])
        sections.append("</data>\n")
    else:
        sections.append("<data source=\"apollo_morning_briefing\">")
        sections.append("No Apollo briefing available for today.")
        sections.append("</data>\n")

    # Daily Summary from Sheets
    if data.get("daily_summary"):
        sections.append("<data source=\"google_sheets_daily_summary\">")
        sections.append(json.dumps(data["daily_summary"], indent=2, default=str))
        sections.append("</data>\n")

    # Positions from Sheets
    if data.get("positions"):
        sections.append("<data source=\"google_sheets_positions\">")
        sections.append(json.dumps(data["positions"], indent=2, default=str))
        sections.append("</data>\n")

    # State file
    if data.get("state"):
        sections.append("<data source=\"hydra_state_file\">")
        sections.append(json.dumps(data["state"], indent=2, default=str))
        sections.append("</data>\n")

    # Metrics
    if data.get("metrics"):
        sections.append("<data source=\"cumulative_metrics\">")
        sections.append(json.dumps(data["metrics"], indent=2, default=str))
        sections.append("</data>\n")

    # Journal logs (truncate if too long)
    if data.get("journal_logs"):
        log_text = data["journal_logs"]
        if len(log_text) > 8000:
            log_text = log_text[-8000:]
            sections.append("<data source=\"journal_logs\" note=\"truncated to last 8000 chars\">")
        else:
            sections.append("<data source=\"journal_logs\">")
        sections.append(log_text)
        sections.append("</data>\n")

    if len(sections) <= 2:
        sections.append("No trading data available for today.\n")
        sections.append("State that no data was found. Do not fabricate a report.\n")

    return "\n".join(sections)


def _extract_summary(report: str) -> Optional[str]:
    """Extract text between <summary> tags."""
    start = report.find("<summary>")
    end = report.find("</summary>")
    if start >= 0 and end > start:
        return report[start + len("<summary>"):end].strip()
    return None
