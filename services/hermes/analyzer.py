"""
HERMES analyzer — builds prompt from collected data, calls Claude, extracts summary.
"""

import json
import logging
from typing import Any, Dict, Optional, Tuple

# Shared strategy context (single source of truth for all agents)
from services.agents_shared import inject_strategy_context

logger = logging.getLogger(__name__)

_PROMPT_TEMPLATE = """You are HERMES, the Daily Execution Quality Analyst for CALYPSO — an automated SPX 0DTE iron condor trading system (HYDRA bot).

Your job is to analyze today's HYDRA trading execution and explain WHY things happened — not just restate numbers.

## CRITICAL: Pre-Computed Numbers

A "cheat_sheet" data section is provided with ALL counting and arithmetic already done in Python. You MUST use these exact numbers for your summary. Do NOT recount entries, stops, sides, or P&L. If a number is in the cheat sheet, quote it directly. If a number is NOT in the cheat sheet or other data sections, say "not available."

## CRITICAL RULES

1. **ONLY use data from the <data> sections.** Do NOT invent numbers.
2. **HYDRA is FULLY AUTOMATED** — do NOT say "the trader should have" or "consider adjusting." Assess whether the MKT rules performed as expected.
3. **Use cheat_sheet numbers for the summary block.** Do NOT compute your own counts or P&L.

{STRATEGY_CONTEXT}

## HERMES-Specific FOMC Guidance

**FOMC Day 1 (MKT-008):** Day 1 of FOMC meeting trades NORMALLY — no restrictions, no blackout, no special rules. This is CORRECT behavior. Do NOT flag entries on Day 1 as violations.

**FOMC Day 2 / Announcement day (MKT-008):** `fomc_announcement_skip` is FALSE — HYDRA now trades on FOMC announcement days. Check `cheat_sheet.fomc_announcement_skip`:
- If `true`: Day 2 is a blackout. Entries placed = enforcement failure.
- If `false` (current default): The user has DELIBERATELY disabled the FOMC blackout. Trading on Day 2 is an intentional user decision, NOT a violation. Do NOT flag it as a rule breach. Treat it as a normal trading day.

CRITICAL: Day 1 is NOT a blackout — HYDRA trades normally on Day 1. Do NOT flag Day 1 trading as a rule violation.
CRITICAL: Day 2 blackout is CONFIGURABLE. Always check `cheat_sheet.fomc_announcement_skip` before flagging Day 2 entries. If `false`, the user chose to trade — it is NOT a violation.

## Entry Skip Pattern

Entry #1 (10:15) is AUTO-DROPPED at VIX ≥ 18 by the VIX regime cap (breakpoints [18, 22, 28],
max_entries [null, 2, 2, 1]). Per-entry analysis of 37 days showed E#1 is the worst-performing
slot (24% WR, -$79/entry avg) — morning directional uncertainty. If you see "0 entries placed"
or only E#2/E#3 on a VIX ≥ 18 day, that is INTENDED behavior, not a skip to flag.
Entry #3 (11:15) is the best-performing slot (42% WR) and is preserved at all VIX levels.
MKT-011 credit-gate skips (cheat_sheet.entries_skipped) tend to cluster on the call side
(premium decays faster on calls).

## Analysis Framework

Focus on NARRATIVE — explain WHY, not just WHAT. Use journal logs for timing context.

1. **Story of the Day** (3-5 sentences)
   What was the market narrative? Connect SPX movement (from cheat_sheet.spx) to stop outcomes. If stops clustered on one side, describe the directional move that caused it. Use journal logs for timing of key events.

2. **Apollo Accuracy**
   Use cheat_sheet.apollo for risk level and accuracy. Did the pre-market assessment match actual outcome? If Apollo unavailable, say so.

3. **Entry Quality**
   Look for MKT-020/MKT-022 tightening events in journal logs. Note credit levels from cheat_sheet.entry_outcomes. Were late entries weaker? Any MKT-011 skips?

4. **Stop Analysis**
   Use cheat_sheet.stop_side_pattern, best_entry, worst_entry. Don't recompute — quote directly. What caused the stops? Connect to market movement.

5. **Cumulative Context**
   Use cheat_sheet.cumulative. How does today compare to avg_win/avg_loss? What's the streak? Is this an outlier or typical day?

## Output Format

Write a structured markdown report with the 5 sections above.

End with a summary block in <summary> tags for Telegram. The summary MUST use ONLY cheat_sheet numbers — do NOT compute your own. Do NOT include a title line (AlertService adds one automatically).

<summary>
{net_pnl} net | {clean_entries} clean, {entries_with_stops} stopped ({call_stops}C/{put_stops}P) | Day {day_number}
Best #{best_num} ({best_outcome}), Worst #{worst_num} ({worst_outcome})
Stops: {stop_side_pattern} | VIX {vix_open}→{vix_low} | {placed}/{total_scheduled} placed
{winning_days}W-{losing_days}L cumul {cumulative_pnl} | Streak: {streak}
Salvage: {long_salvage_count} longs sold for +${long_salvage_revenue} (omit line if 0)
{one_sentence_narrative_insight — the WHY behind today's result}
</summary>

Line 5 is YOUR value-add: one sentence explaining WHY today went the way it did, referencing specific market action.
"""

# Inject shared strategy context from services/hydra_strategy_context.md
SYSTEM_PROMPT = inject_strategy_context(_PROMPT_TEMPLATE)


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
    """Build the user prompt with cheat sheet first, then supporting data."""
    sections = [f"# HERMES Daily Analysis — {today_str}\n"]
    sections.append(
        "Analyze ONLY the data provided below. Use cheat_sheet numbers for "
        "all counts and P&L in your summary. Do not invent any numbers.\n"
    )

    # Cheat sheet FIRST — pre-computed by Python, Claude must use these
    if data.get("cheat_sheet") and "error" not in data["cheat_sheet"]:
        sections.append(
            '<data source="cheat_sheet" note="Pre-computed by Python. '
            'Use these numbers exactly. Do NOT recount or recalculate.">'
        )
        sections.append(json.dumps(data["cheat_sheet"], indent=2, default=str))
        sections.append("</data>\n")

    # Apollo morning report
    if data.get("apollo_report"):
        sections.append('<data source="apollo_morning_briefing">')
        sections.append(data["apollo_report"])
        sections.append("</data>\n")
    else:
        sections.append('<data source="apollo_morning_briefing">')
        sections.append("No Apollo briefing available for today.")
        sections.append("</data>\n")

    # Daily Summary from Sheets
    if data.get("daily_summary"):
        sections.append('<data source="google_sheets_daily_summary">')
        sections.append(json.dumps(data["daily_summary"], indent=2, default=str))
        sections.append("</data>\n")

    # Positions from Sheets
    if data.get("positions"):
        sections.append('<data source="google_sheets_positions">')
        sections.append(json.dumps(data["positions"], indent=2, default=str))
        sections.append("</data>\n")

    # Trimmed state file (strip UICs, position IDs, merge flags to save tokens)
    if data.get("state"):
        trimmed = _trim_state_for_prompt(data["state"])
        sections.append('<data source="hydra_state_file" note="trimmed for relevance">')
        sections.append(json.dumps(trimmed, indent=2, default=str))
        sections.append("</data>\n")

    # Skip raw metrics — cheat_sheet.cumulative has the curated subset

    # Journal logs (truncate if too long)
    if data.get("journal_logs"):
        log_text = data["journal_logs"]
        if len(log_text) > 8000:
            log_text = log_text[-8000:]
            sections.append(
                '<data source="journal_logs" note="truncated to last 8000 chars">'
            )
        else:
            sections.append('<data source="journal_logs">')
        sections.append(log_text)
        sections.append("</data>\n")

    if len(sections) <= 2:
        sections.append("No trading data available for today.\n")
        sections.append("State that no data was found. Do not fabricate a report.\n")

    return "\n".join(sections)


def _trim_state_for_prompt(state: Dict[str, Any]) -> Dict[str, Any]:
    """Strip position IDs, UICs, and merge flags from state to save tokens."""
    # Keep top-level fields that provide context
    keep_top = [
        "date", "state", "entries_completed", "entries_skipped", "entries_failed",
        "total_credit_received", "total_realized_pnl", "total_commission",
        "call_stops_triggered", "put_stops_triggered", "double_stops",
        "early_close_triggered", "early_close_time", "early_close_pnl",
        "market_data_ohlc", "entry_schedule",
    ]
    trimmed = {k: state[k] for k in keep_top if k in state}

    # Trim each entry — keep strikes, credits, stops, status, trend; strip IDs
    keep_entry = [
        "entry_number", "entry_time",
        "short_call_strike", "long_call_strike", "short_put_strike", "long_put_strike",
        "call_spread_credit", "put_spread_credit",
        "call_side_stop", "put_side_stop",
        "call_side_stopped", "put_side_stopped",
        "call_side_expired", "put_side_expired",
        "call_side_skipped", "put_side_skipped",
        "call_long_sold", "put_long_sold",
        "call_long_sold_revenue", "put_long_sold_revenue",
        "trend_signal", "override_reason", "skip_reason", "early_closed",
        "is_complete", "open_commission", "close_commission",
    ]
    trimmed["entries"] = []
    for e in state.get("entries", []):
        trimmed["entries"].append({k: e[k] for k in keep_entry if k in e})

    return trimmed


def _extract_summary(report: str) -> Optional[str]:
    """Extract text between <summary> tags."""
    start = report.find("<summary>")
    end = report.find("</summary>")
    if start >= 0 and end > start:
        return report[start + len("<summary>"):end].strip()
    return None
