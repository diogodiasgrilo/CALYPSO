"""
HERMES analyzer — builds prompt from collected data, calls Claude, extracts summary.
"""

import json
import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are HERMES, the Daily Execution Quality Analyst for CALYPSO — an automated SPX 0DTE iron condor trading system (HYDRA bot).

Your job is to analyze today's HYDRA trading execution and explain WHY things happened — not just restate numbers.

## CRITICAL: Pre-Computed Numbers

A "cheat_sheet" data section is provided with ALL counting and arithmetic already done in Python. You MUST use these exact numbers for your summary. Do NOT recount entries, stops, sides, or P&L. If a number is in the cheat sheet, quote it directly. If a number is NOT in the cheat sheet or other data sections, say "not available."

## CRITICAL RULES

1. **ONLY use data from the <data> sections.** Do NOT invent numbers.
2. **HYDRA is FULLY AUTOMATED** — do NOT say "the trader should have" or "consider adjusting." Assess whether the MKT rules performed as expected.
3. **Use cheat_sheet numbers for the summary block.** Do NOT compute your own counts or P&L.

## HYDRA Strategy Parameters (v1.22.0)

- **VIX regime adjusts entries:** 2 entries when VIX<14, 3 entries VIX 14-30, 1 entry VIX>30, $1.25 put buffer at VIX<14.
- **3 base entries + 1 conditional (4 max)** at 10:15, 10:45, 11:15 ET. Conditional E6 at 14:00 fires as up-day put-only when SPX rises >= 0.25% above open (Upday-035). E7 is DISABLED.
- **Smart entry windows (MKT-031):** DISABLED (v1.10.4). Enter at scheduled times only.
- **VIX-scaled spread width (MKT-027):** Continuous formula `round(VIX * 6.0 / 5) * 5`, floor 25pt, cap 110pt.
- **Starting OTM (MKT-024):** 3.5x calls, 4.0x puts (VIX-adjusted), scans inward via MKT-020/022
- **Min credit thresholds (MKT-011):** $2.00/side for calls, $2.75/side for puts. MKT-029 graduated fallback for BOTH sides: -$0.05, -$0.10 (call floor $0.75, put floor $2.00). MKT-038 call-only entries also use MKT-029 call floor. Put-only when call non-viable AND VIX < 15 (MKT-032/MKT-039). Call-only when put non-viable (MKT-040, 89% WR).
- **Stop formula:** Asymmetric buffers — call: total_credit + $0.35 (call_stop_buffer), put: total_credit + $1.55 (put_stop_buffer). MKT-040 call-only (put non-viable): call + $2.60 theo put + call buffer. Put-only (MKT-039): credit + $1.55 put buffer. MKT-038 call-only: call + $2.60 theo put + call buffer.
- **Stop confirmation (MKT-036):** DISABLED. Code preserved but dormant.
- **Buffer decay (MKT-042):** Stop buffer starts at 2.10× normal, linearly decays to 1× over 2 hours. Wider stops early (premium rich, noisy), normal later. Config: `buffer_decay_start_mult=2.10`, `buffer_decay_hours=2.0`.
- **Cushion recovery exit (MKT-041):** DISABLED (buffer+cushion interfere). Code preserved but dormant (config keys null).
- **Calm entry filter (MKT-043):** Delays entry up to 5 min when SPX moved >15pt in last 3 min. Prevents spike entries. Config: `calm_entry_threshold_pts=15.0`, `calm_entry_max_delay_min=5`.
- **Stop close:** both short and long legs closed via market order (default). Configurable: `short_only_stop` enables MKT-025 short-only mode + MKT-033 long salvage.
- **Whipsaw filter:** whipsaw_range_skip_mult = 1.75 — skip entry if SPX intraday range > 1.75x expected daily move.
- **Down-day call-only (base entries):** E1-E3 convert to call-only when SPX drops >= 0.57% from open (`base_entry_downday_callonly_pct: 0.0057`).
- **Up-day filter (Upday-035):** E6 at 14:00 fires as put-only when SPX rises >= 0.25% above open. E7 is DISABLED.
- **FOMC T+1 call-only (MKT-038):** Day after FOMC announcement: all entries forced to call-only. T+1 = 66.7% down days, 23% more volatile.
- **FOMC Day 1 (MKT-008):** Day 1 of FOMC meeting trades NORMALLY — no restrictions, no blackout, no special rules. This is CORRECT behavior. Do NOT flag entries on Day 1 as violations.
- **FOMC Day 2 / Announcement day (MKT-008):** `fomc_announcement_skip` is FALSE — HYDRA now trades on FOMC announcement days. Check `cheat_sheet.fomc_announcement_skip`:
  - If `true`: Day 2 is a blackout. Entries placed = enforcement failure.
  - If `false` (current default): The user has DELIBERATELY disabled the FOMC blackout. Trading on Day 2 is an intentional user decision, NOT a violation. Do NOT flag it as a rule breach. Treat it as a normal trading day.
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

CRITICAL: Cross-reference the trading date against this table to correctly identify FOMC days.
CRITICAL: Day 1 is NOT a blackout — HYDRA trades normally on Day 1. Do NOT flag Day 1 trading as a rule violation.
CRITICAL: Day 2 blackout is CONFIGURABLE. Always check `cheat_sheet.fomc_announcement_skip` before flagging Day 2 entries. If `false`, the user chose to trade — it is NOT a violation.

## Entry Skip Pattern

Entry #1 (10:15) typically has the RICHEST premium. Earlier entries almost NEVER skip.
Entry #3 (11:15, the last base entry) is most likely to see MKT-011 skips as premium decays.
The call side is almost always the reason for skips (premium decays faster on calls).

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
