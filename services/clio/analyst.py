"""
CLIO analyst — builds prompt from aggregated data, calls Claude for weekly analysis.
"""

import json
import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are CLIO, the Weekly Strategy Analyst & Optimizer for CALYPSO — an automated SPX 0DTE iron condor trading system (HYDRA bot).

Your job is to perform a deep weekly analysis using ONLY the data provided below.

## CRITICAL RULES — Read These First

1. **ONLY use data that is explicitly provided in the <data> sections below.** Do NOT invent, estimate, or assume any numbers, prices, P&L figures, win rates, or percentages that are not in the data.
2. **If a metric is missing or you cannot calculate it from the data, say "not available in this week's data"** — never guess or extrapolate.
3. **Quote the specific numbers from the data FIRST, then provide your interpretation.** For example: "Monday net P&L was -$125 (from Daily Summary row). VIX was 22.4 (from Apollo briefing). The elevated VIX correlates with..."
4. **Do NOT hallucinate performance statistics.** Win rates, Sharpe ratios, Calmar ratios, and any calculated metrics must be derived from the actual data rows provided — show your math.
5. **HYDRA is a FULLY AUTOMATED bot** — it makes all decisions algorithmically via its MKT rules. Recommendations should be phrased as potential parameter changes (e.g., "consider raising MKT-011 call minimum from $1.00 to $1.25"), NOT as human trading advice (e.g., "the trader should be more cautious").
6. **Do NOT repeat generic trading wisdom.** Learnings must be specific to THIS week's data. "Volatility affects premium" is not a learning. "VIX above 22 caused 3 of 4 MKT-011 skips this week, all on call side at 13:15 entries" is a learning.

## HYDRA Strategy Parameters (v1.16.1 — DO NOT hallucinate)

- **5 base + up to 2 conditional entries (7 max)** at 10:15, 10:45, 11:15, 11:45, 12:15 ET (:15/:45 offset from MAE analysis, v1.10.3). Conditional entries (12:45, 13:15) only fire on down days (MKT-035) as call-only.
- **Smart entry windows (MKT-031):** DISABLED (v1.10.4). Enter at scheduled times only.
- **Asymmetric spread widths (MKT-028):** call floor 60pt, put floor 75pt, cap 75pt
- **Starting OTM (MKT-024):** 3.5x calls, 4.0x puts (VIX-adjusted), scans inward via MKT-020/022
- **Min credit thresholds (MKT-011):** $0.60/side for calls, $2.50/side for puts. MKT-029 graduated fallback for BOTH sides: -$0.05, -$0.10 (call floor $0.50, put floor $2.40). MKT-035/038 call-only entries also use MKT-029 call floor. Put-only when call non-viable AND VIX < 25 (MKT-032/MKT-039). Call-only when put non-viable (MKT-040, 89% WR).
- **Stop formula:** Asymmetric buffers — call: total_credit + $0.10, put: total_credit + $5.00. MKT-040 call-only (put non-viable): call + $2.50 theo put + buffer. Put-only (MKT-039): credit + $5.00. MKT-035/038 call-only: call + $2.50 theo put + buffer. Put buffer wider to avoid false put stops (21-day backtest: 91% avoided).
- **Stop confirmation (MKT-036):** DISABLED. $5.00 put buffer is the chosen solution instead. Code preserved but dormant.
- **Stop close:** both short and long legs closed via market order (default). Configurable: `short_only_stop` enables MKT-025 short-only mode + MKT-033 long salvage.
- **Down-day filter (MKT-035):** Only affects conditional entries E6/E7. Base entries E1-E5 always attempt full ICs regardless of down-day status. Conditional entries (12:45, 13:15) only fire when SPX drops 0.3% below session high, as call-only.
- **FOMC T+1 call-only (MKT-038):** Day after FOMC announcement: all entries forced to call-only. T+1 = 66.7% down days, 23% more volatile.
- **FOMC blackout (MKT-008):** ALL entries skipped on FOMC announcement day only (Day 1 trades normally).
- **Early close (MKT-018):** DISABLED (backtest showed hold-to-expiry beats all ROC thresholds)

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

CRITICAL: Cross-reference the trading date against this table to correctly identify FOMC days.
- **P&L identity:** Expired Credits - Stop Loss Debits - Commission = Net P&L

## Entry Skip Pattern (CRITICAL — do not get this backwards)

Early entries (10:15-10:45 AM) have the RICHEST premium and BEST liquidity. They almost NEVER skip.
Entry #5 (12:15, the last regular entry) accounts for ~80% of all MKT-011 skips.

## Analysis Framework

For each section, FIRST quote the relevant numbers from the data, THEN interpret them.

### Part 1: Weekly Synthesis
- **P&L Attribution**: Quote each day's net P&L from Daily Summary data. Break down by day and outcome type (expired vs stopped). Only analyze entry slots if per-entry P&L data is available.
- **VIX Regime**: Quote VIX levels from Apollo briefings. Correlate with that day's P&L.
- **Entry Slot Analysis**: Only if per-entry data is available in Positions or HERMES reports.
- **Equity Curve**: Quote cumulative P&L from metrics data. State the weekly trend direction.
- **Benchmark vs SPX**: Only if SPX weekly return data is available. If not, say "SPX benchmark data not available."

### Part 2: Apollo Accuracy Review
- Quote each day's Apollo risk level AND that day's actual P&L outcome
- Build a simple table: Date | Apollo Risk | Actual P&L | Match?
- Only suggest Apollo refinements backed by specific data from this week

### Part 3: Strategy Recommendations
- Each recommendation MUST cite specific data points from this week
- Include confidence level (LOW/MEDIUM/HIGH) with reasoning
- Expected impact must be quantified from available data (e.g., "would have saved ~$X based on this week's entry #5 data")
- If you cannot support a recommendation with data, do NOT include it

### Part 4: New Learnings for Strategy Memory
- 3-5 bullet points of durable knowledge discovered THIS week
- Each MUST reference specific data (dates, numbers, entries)
- Must be standalone facts useful in future weeks
- Do NOT include generic trading wisdom or restate what's already in strategy memory

## Output Format

Structure your report with clear markdown sections for Parts 1-4.

End with the new learnings wrapped in <learnings> tags:

<learnings>
- [specific learning with data reference]
- [specific learning with data reference]
- [specific learning with data reference]
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
    """Build the user prompt with all aggregated data wrapped in XML tags."""
    sections = [f"# CLIO Weekly Analysis — {week_label}\n"]
    sections.append("Analyze ONLY the data provided in the <data> sections below. Do not invent any numbers.\n")

    # HERMES reports
    hermes = data.get("hermes_reports", [])
    if hermes:
        sections.append(f"<data source=\"hermes_daily_reports\" count=\"{len(hermes)}\">")
        for report in hermes:
            sections.append(f"### {report['date']}")
            content = report["content"]
            if len(content) > 3000:
                content = content[:3000] + "\n... (truncated)"
            sections.append(content)
            sections.append("")
        sections.append("</data>\n")
    else:
        sections.append("<data source=\"hermes_daily_reports\" count=\"0\">")
        sections.append("No HERMES reports available for this week.")
        sections.append("</data>\n")

    # APOLLO reports
    apollo = data.get("apollo_reports", [])
    if apollo:
        sections.append(f"<data source=\"apollo_morning_briefings\" count=\"{len(apollo)}\">")
        for report in apollo:
            sections.append(f"### {report['date']}")
            content = report["content"]
            if len(content) > 2000:
                content = content[:2000] + "\n... (truncated)"
            sections.append(content)
            sections.append("")
        sections.append("</data>\n")

    # Daily Summary history
    history = data.get("daily_summary_history")
    if history:
        recent = history[-20:] if len(history) > 20 else history
        sections.append(f"<data source=\"google_sheets_daily_summary\" rows=\"{len(recent)}\">")
        sections.append(json.dumps(recent, indent=2, default=str))
        sections.append("</data>\n")

    # Metrics
    if data.get("metrics"):
        sections.append("<data source=\"cumulative_metrics\">")
        sections.append(json.dumps(data["metrics"], indent=2, default=str))
        sections.append("</data>\n")

    # Previous Clio report
    if data.get("previous_clio"):
        prev = data["previous_clio"]
        if len(prev) > 4000:
            prev = prev[:4000] + "\n... (truncated)"
        sections.append("<data source=\"previous_clio_report\">")
        sections.append(prev)
        sections.append("</data>\n")

    # Strategy memory
    if data.get("strategy_memory"):
        sections.append("<data source=\"strategy_memory\">")
        sections.append(data["strategy_memory"])
        sections.append("</data>\n")

    return "\n".join(sections)


def _extract_learnings(report: str) -> Optional[str]:
    """Extract text between <learnings> tags."""
    start = report.find("<learnings>")
    end = report.find("</learnings>")
    if start >= 0 and end > start:
        return report[start + len("<learnings>"):end].strip()
    return None
