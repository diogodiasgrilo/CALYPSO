"""
HOMER journal updater — generates formatted content and applies updates
to the HYDRA Trading Journal section by section.

All data-driven sections use Python for accuracy. Claude API is only used
for narrative sections (observations, market labels, assessments) via
the narrative_generator module.
"""

import logging
import re
from datetime import datetime
from typing import Any, Dict, List

from services.homer.journal_parser import JournalParser

logger = logging.getLogger(__name__)

# Month abbreviations for date formatting
MONTH_ABBREV = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
    7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
}

DAY_NAMES = {
    0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday",
    4: "Friday", 5: "Saturday", 6: "Sunday",
}

DAY_ABBREV = {
    0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun",
}


def format_date_label(date_str: str) -> str:
    """Convert "2026-03-02" to "Mar 2"."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{MONTH_ABBREV[dt.month]} {dt.day}"


def format_day_of_week(date_str: str) -> str:
    """Convert "2026-03-02" to "Monday"."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return DAY_NAMES[dt.weekday()]


def format_day_abbrev(date_str: str) -> str:
    """Convert "2026-03-02" to "Mon"."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return DAY_ABBREV[dt.weekday()]


def safe_float(value, default=0.0) -> float:
    """Safely convert a value to float, handling commas and empty strings."""
    if value is None or value == "":
        return default
    try:
        return float(str(value).replace(",", "").replace("$", "").replace("%", ""))
    except (ValueError, TypeError):
        return default


def safe_int(value, default=0) -> int:
    """Safely convert a value to int."""
    return int(safe_float(value, default))


def format_money(value: float) -> str:
    """Format money: show cents when fractional, integer when whole.

    Examples: 305 → '305', 47.5 → '47.50', 1592.5 → '1592.50'
    """
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}"


def format_currency(value: float) -> str:
    """Format a float as currency: $305 or $47.50."""
    return f"${format_money(abs(value))}"


def format_signed_currency(value: float) -> str:
    """Format as +$1,234 or -$1,234 (with cents when fractional)."""
    sign = "+" if value >= 0 else "-"
    return f"{sign}${format_money(abs(value))}"


def _format_credit_with_breakdown(entry: dict) -> str:
    """
    Format credit column with per-side breakdown.

    Examples:
        Full IC:   "$310 ($108C+$202P)"
        Put only:  "$205 (P)"
        Call only: "$108 (C)"
        No data:   "$310"
    """
    call_credit = safe_float(entry.get("Call Credit", 0))
    put_credit = safe_float(entry.get("Put Credit", 0))
    total = safe_float(entry.get("Total Credit", 0))

    # Use per-side sum if no total but per-side data exists
    if not total and (call_credit or put_credit):
        total = call_credit + put_credit

    if not total:
        return "--"

    # Both sides have credits → full breakdown
    if call_credit > 0 and put_credit > 0:
        return f"${format_money(total)} (${format_money(call_credit)}C+${format_money(put_credit)}P)"

    # One-sided entries
    if call_credit > 0 and not put_credit:
        return f"${format_money(call_credit)} (C)"
    if put_credit > 0 and not call_credit:
        return f"${format_money(put_credit)} (P)"

    # No per-side data available — show total only
    return format_currency(total)


# =============================================================================
# SECTION 2: DAILY SUMMARY TABLE
# =============================================================================

# Row labels in the Section 2 table, in order.
# Each tuple is (label_in_journal, key_in_sheets_row, formatter).
SECTION2_ROWS = [
    ("Date", "Date", lambda v: f"**{v}**"),
    ("SPX Open", "SPX Open", lambda v: f"**{safe_float(v):,.2f}**"),
    ("SPX Close", "SPX Close", lambda v: f"**{safe_float(v):,.2f}**"),
    ("SPX High", "SPX High", lambda v: f"**{safe_float(v):,.2f}**"),
    ("SPX Low", "SPX Low", lambda v: f"**{safe_float(v):,.2f}**"),
    ("VIX Open", "VIX Open", lambda v: f"**{safe_float(v):.2f}**"),
    ("VIX Close", "VIX Close", lambda v: f"**{safe_float(v):.2f}**"),
    ("VIX High", "VIX High", lambda v: f"**{safe_float(v):.2f}**"),
    ("VIX Low", "VIX Low", lambda v: f"**{safe_float(v):.2f}**"),
    ("Entries Completed", "Entries Completed", lambda v: f"**{safe_int(v)}**"),
    ("Entries Skipped", "Entries Skipped", lambda v: f"**{safe_int(v)}**"),
    ("Full ICs", "Full ICs", lambda v: f"**{safe_int(v)}**"),
    ("One-Sided Entries", "One-Sided Entries", lambda v: f"**{safe_int(v)}**"),
    ("Bullish Signals", "Bullish Signals", lambda v: f"**{safe_int(v)}**"),
    ("Bearish Signals", "Bearish Signals", lambda v: f"**{safe_int(v)}**"),
    ("Neutral Signals", "Neutral Signals", lambda v: f"**{safe_int(v)}**"),
    ("Total Credit ($)", "Total Credit ($)", lambda v: f"**{format_money(safe_float(v))}**"),
    ("Call Stops", "Call Stops", lambda v: f"**{safe_int(v)}**"),
    ("Put Stops", "Put Stops", lambda v: f"**{safe_int(v)}**"),
    ("Double Stops", "Double Stops", lambda v: f"**{safe_int(v)}**"),
    ("Stop Loss Debits ($)", "Stop Loss Debits ($)", lambda v: f"**{format_money(safe_float(v))}**"),
    ("Commission ($)", "Commission ($)", lambda v: f"**{format_money(safe_float(v))}**"),
    ("Expired Credits ($)", "Expired Credits ($)", lambda v: f"**{format_money(safe_float(v))}**"),
    ("Daily P&L ($)", "Daily P&L ($)", lambda v: f"**{format_money(safe_float(v))}**"),
    ("Daily P&L (EUR)", "Daily P&L (EUR)", lambda v: f"**~{format_money(safe_float(v))}**"),
    ("Cumulative P&L ($)", "Cumulative P&L ($)", lambda v: f"**{format_money(safe_float(v))}**"),
    ("Cumulative P&L (EUR)", "Cumulative P&L (EUR)", lambda v: f"**~{format_money(safe_float(v))}**"),
    ("Win Rate (%)", "Win Rate (%)", lambda v: f"**{safe_float(v):.1f}**"),
    ("Capital Deployed ($)", "Capital Deployed ($)", lambda v: f"**{format_money(safe_float(v))}**"),
    ("Return on Capital (%)", "Return on Capital (%)", lambda v: f"**{safe_float(v):.2f}**"),
    ("Sortino Ratio", "Sortino Ratio", lambda v: f"**~{safe_float(v):.1f}**"),
    ("Max Loss Stops ($)", "Max Loss Stops ($)", lambda v: f"**{format_money(safe_float(v))}**"),
    ("Max Loss Catastrophic ($)", "Max Loss Catastrophic ($)", lambda v: f"**{format_money(safe_float(v))}**"),
    ("Early Close", "Early Close", lambda v: f"**{v if v else 'No'}**"),
    ("Notes", "Notes", lambda v: f"**{v}**" if v else "**Post-settlement**"),
]


def add_section2_column(parser: JournalParser, day_data: Dict[str, Any]):
    """
    Add a new date column to the Section 2 daily summary table.

    This is the most delicate operation — modifies ~35 table rows.
    """
    table_range = parser.get_section2_table_range()
    if table_range is None:
        logger.error("Cannot find Section 2 table")
        return

    summary = day_data["summary"]
    date_str = str(summary.get("Date", "")).strip()
    date_label = format_date_label(date_str)

    start, end = table_range

    for i in range(start, end + 1):
        line = parser.lines[i]
        if not line.strip().startswith("|"):
            continue

        parts = line.rstrip().rstrip("|").split("|")
        # parts[0] is empty (before first |), parts[1] is label

        if i == start:
            # Header row — add date column
            new_col = f" **{date_label}** "
            parser.lines[i] = line.rstrip() + f"{new_col}|"
            continue

        if i == start + 1:
            # Separator row (|--------|)
            parser.lines[i] = line.rstrip() + "--------|"
            continue

        # Data rows — find the matching row label
        label = parts[1].strip() if len(parts) > 1 else ""

        # Find matching row definition
        value = None
        for row_label, sheet_key, formatter in SECTION2_ROWS:
            if label == row_label:
                raw = summary.get(sheet_key, "")
                value = formatter(raw)
                break

        if value is None:
            # Unknown row — add empty cell
            value = " "

        # Match column width from previous columns
        if len(parts) >= 3:
            prev_width = len(parts[-1])
            col_width = max(prev_width, len(value) + 2)
        else:
            col_width = len(value) + 2

        padded = f" {value}".ljust(col_width) + "|"
        parser.lines[i] = line.rstrip() + padded

    logger.info(f"Added Section 2 column for {date_label}")


# =============================================================================
# SECTION 2b: P&L VERIFICATION
# =============================================================================

def add_pnl_verification(parser: JournalParser, day_data: Dict[str, Any]):
    """Add a P&L verification formula line for the new day."""
    pnl_range = parser.get_pnl_verification_range()
    if pnl_range is None:
        logger.warning("Cannot find P&L verification section")
        return

    summary = day_data["summary"]
    date_str = str(summary.get("Date", "")).strip()
    date_label = format_date_label(date_str)

    expired = safe_float(summary.get("Expired Credits ($)", 0))
    stops = safe_float(summary.get("Stop Loss Debits ($)", 0))
    commission = safe_float(summary.get("Commission ($)", 0))
    pnl = safe_float(summary.get("Daily P&L ($)", 0))

    computed = expired - stops - commission
    # Allow small floating point tolerance
    check = "✓" if abs(computed - pnl) < 0.01 else "MISMATCH"

    # Build note about the day
    notes = summary.get("Notes", "")
    note_parts = []
    if notes:
        note_parts.append(notes)
    note_str = f" ({', '.join(note_parts)})" if note_parts else ""

    formula_line = (
        f"- {date_label}: {format_money(expired)} - {format_money(stops)} "
        f"- {format_money(commission)} = {format_money(pnl)} {check}{note_str}"
    )

    # Insert after last formula line
    _, last_line = pnl_range
    parser.insert_lines(last_line + 1, [formula_line])
    logger.info(f"Added P&L verification for {date_label}: {formula_line}")


# =============================================================================
# SECTION 2c: CUMULATIVE METRICS JSON
# =============================================================================

def update_cumulative_metrics(parser: JournalParser, metrics: Dict, date_label: str):
    """Replace the cumulative metrics JSON block."""
    metrics_range = parser.get_cumulative_metrics_range()
    if metrics_range is None:
        logger.warning("Cannot find cumulative metrics block")
        return

    start, end = metrics_range

    new_lines = [
        f"### Cumulative Metrics (hydra_metrics.json as of {date_label} EOD)",
        "```json",
        "{",
    ]

    # Output metrics in a readable order
    key_order = [
        "cumulative_pnl", "total_entries", "winning_days", "losing_days",
        "total_credit_collected", "total_stops", "double_stops", "last_updated",
    ]
    items = []
    for key in key_order:
        if key in metrics:
            val = metrics[key]
            if isinstance(val, str):
                items.append(f'  "{key}": "{val}"')
            elif isinstance(val, float):
                items.append(f'  "{key}": {val}')
            elif isinstance(val, int):
                items.append(f'  "{key}": {val}')
            else:
                items.append(f'  "{key}": {val}')

    # Add any remaining keys not in key_order
    for key, val in metrics.items():
        if key not in key_order:
            if isinstance(val, str):
                items.append(f'  "{key}": "{val}"')
            else:
                items.append(f'  "{key}": {val}')

    for i, item in enumerate(items):
        comma = "," if i < len(items) - 1 else ""
        new_lines.append(f"{item}{comma}")

    new_lines.append("}")
    new_lines.append("```")

    parser.replace_range(start, end, new_lines)
    logger.info(f"Updated cumulative metrics JSON block")


# =============================================================================
# SECTION 3: ENTRY-LEVEL DETAIL
# =============================================================================

def build_section3_day_block(
    day_data: Dict[str, Any],
    narratives: Dict[str, str],
) -> List[str]:
    """
    Build a complete Section 3 day block with entry table and observations.

    Args:
        day_data: Day-specific data from collect_day_data().
        narratives: Dict with "observations" key from narrative generator.

    Returns:
        List of lines for the day block.
    """
    summary = day_data["summary"]
    entries = day_data.get("entries", [])
    date_str = str(summary.get("Date", "")).strip()
    date_label = format_date_label(date_str)
    day_name = format_day_of_week(date_str)
    pnl = safe_float(summary.get("Daily P&L ($)", 0))

    spx_open = safe_float(summary.get("SPX Open", 0))
    spx_close = safe_float(summary.get("SPX Close", 0))
    spx_high = safe_float(summary.get("SPX High", 0))
    spx_low = safe_float(summary.get("SPX Low", 0))
    vix_open = safe_float(summary.get("VIX Open", 0))
    vix_close = safe_float(summary.get("VIX Close", 0))
    spx_range = spx_high - spx_low
    spx_range_pct = (spx_range / spx_open * 100) if spx_open else 0

    lines = []
    lines.append(f"### {date_label} ({day_name}) - NET P&L: {format_signed_currency(pnl)}")
    lines.append("")

    # Market summary
    lines.append(
        f"**Market**: SPX range {spx_range:.0f} pts ({spx_range_pct:.1f}%). "
        f"VIX {vix_open:.1f}→{vix_close:.1f}."
    )
    lines.append("")

    # Entry table
    lines.append("| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact |")
    lines.append("|-------|------|--------|------|---------------|--------|---------|------------|")

    if entries:
        for entry in entries:
            entry_num = entry.get("Entry #", entry.get("Entry", "?"))
            time_val = entry.get("Entry Time", entry.get("Time", "--"))
            signal = entry.get("Trend Signal", entry.get("Signal", "NEUTRAL"))
            entry_type = entry.get("Entry Type", entry.get("Type", "Full IC"))
            short_call = entry.get("Short Call Strike", entry.get("Short Call", ""))
            short_put = entry.get("Short Put Strike", entry.get("Short Put", ""))
            outcome = entry.get("Outcome", "")
            pnl_impact = entry.get("P&L Impact", entry.get("PnL Impact", ""))

            # Build strikes column
            strikes = []
            if short_call:
                strikes.append(f"C:{short_call}")
            if short_put:
                strikes.append(f"P:{short_put}")
            strikes_str = " ".join(strikes) if strikes else "--"

            # Format credit with per-side breakdown
            credit_str = _format_credit_with_breakdown(entry)

            lines.append(
                f"| #{entry_num} | {time_val} | {signal} | {entry_type} | "
                f"{strikes_str} | {credit_str} | {outcome} | {pnl_impact} |"
            )
    else:
        lines.append("| -- | -- | -- | -- | -- | -- | No entry data available | -- |")

    lines.append("")

    # Key observations (from Claude)
    observations = narratives.get("observations", "")
    if observations:
        lines.append("**Key observations**:")
        for obs in observations.strip().split("\n"):
            obs = obs.strip()
            if obs and not obs.startswith("-"):
                lines.append(f"- {obs}")
            elif obs:
                lines.append(obs)
        lines.append("")

    # Stop timing log if there were stops
    call_stops = safe_int(summary.get("Call Stops", 0))
    put_stops = safe_int(summary.get("Put Stops", 0))
    total_stops = call_stops + put_stops

    if total_stops > 0 and entries:
        stop_entries = [e for e in entries if "STOP" in str(e.get("Outcome", "")).upper()]
        if stop_entries:
            lines.append("### Stop Timing Log")
            lines.append("")
            lines.append("```")
            for se in stop_entries:
                entry_num = se.get("Entry #", se.get("Entry", "?"))
                stop_time = se.get("Stop Time", se.get("Close Time", "??:?? ET"))
                outcome = se.get("Outcome", "STOPPED")
                pnl_val = safe_float(se.get("P&L Impact", 0))
                pnl_str = f"${format_money(abs(pnl_val))} loss" if pnl_val else ""
                lines.append(f"{stop_time} - Entry #{entry_num} {outcome}" + (f" ({pnl_str})" if pnl_str else ""))
            lines.append("```")
            lines.append("")

    # P&L reconciliation
    expired = safe_float(summary.get("Expired Credits ($)", 0))
    stop_debits = safe_float(summary.get("Stop Loss Debits ($)", 0))
    commission = safe_float(summary.get("Commission ($)", 0))

    lines.append("### P&L Reconciliation")
    lines.append("")
    lines.append(f"- Expired Credits: ${format_money(expired)}")
    lines.append(f"- Stop Loss Debits: ${format_money(stop_debits)}")
    lines.append(f"- Commission: ${format_money(commission)}")
    lines.append(f"- **Net P&L: {format_signed_currency(pnl)}** ({format_money(expired)} - {format_money(stop_debits)} - {format_money(commission)} = {format_money(pnl)})")
    lines.append("")

    return lines


def insert_section3_block(parser: JournalParser, block_lines: List[str]):
    """Insert a day block at the end of Section 3."""
    insertion_point = parser.get_section3_insertion_point()
    if insertion_point is None:
        logger.error("Cannot find Section 3 insertion point")
        return

    parser.insert_lines(insertion_point, block_lines)
    logger.info(f"Inserted Section 3 block ({len(block_lines)} lines)")


# =============================================================================
# SECTION 4: MARKET CONDITIONS
# =============================================================================

def add_section4_market_character_row(
    parser: JournalParser,
    day_data: Dict[str, Any],
    market_label: str,
):
    """Add a row to the Daily Market Character table in Section 4."""
    last_row = parser.find_table_last_row(4, r"Daily Market Character")
    if last_row is None:
        logger.warning("Cannot find Daily Market Character table")
        return

    summary = day_data["summary"]
    date_str = str(summary.get("Date", "")).strip()
    date_label = format_date_label(date_str)
    day_abbrev = format_day_abbrev(date_str)

    spx_open = safe_float(summary.get("SPX Open", 0))
    spx_close = safe_float(summary.get("SPX Close", 0))
    spx_high = safe_float(summary.get("SPX High", 0))
    spx_low = safe_float(summary.get("SPX Low", 0))
    vix_open = safe_float(summary.get("VIX Open", 0))
    vix_close = safe_float(summary.get("VIX Close", 0))

    spx_change_pct = ((spx_close - spx_open) / spx_open * 100) if spx_open else 0
    spx_range = spx_high - spx_low
    spx_range_pct = (spx_range / spx_open * 100) if spx_open else 0

    spx_change_str = f"{spx_change_pct:+.1f}%"
    range_str = f"{spx_range:.0f} pts ({spx_range_pct:.1f}%)"
    vix_str = f"{vix_open:.0f}→{vix_close:.0f}" if abs(vix_open - vix_close) > 1 else f"{vix_close:.0f}"

    # Build key event from notes or stop count
    call_stops = safe_int(summary.get("Call Stops", 0))
    put_stops = safe_int(summary.get("Put Stops", 0))
    total_stops = call_stops + put_stops
    entries = safe_int(summary.get("Entries Completed", 0))
    notes = summary.get("Notes", "")

    key_event = notes if notes else f"{entries} entries, {total_stops} stops"

    row = f"| {date_label} | {day_abbrev} | {market_label} | {spx_change_str} | {range_str} | {vix_str} | {key_event} |"

    parser.insert_lines(last_row + 1, [row])
    logger.info(f"Added Section 4 Market Character row for {date_label}")


def add_section4_expected_move_row(parser: JournalParser, day_data: Dict[str, Any]):
    """Add a row to the Expected Move vs Actual Range table in Section 4."""
    last_row = parser.find_table_last_row(4, r"Expected Move vs Actual Range")
    if last_row is None:
        logger.warning("Cannot find Expected Move vs Actual Range table")
        return

    summary = day_data["summary"]
    date_str = str(summary.get("Date", "")).strip()
    date_label = format_date_label(date_str)

    vix_open = safe_float(summary.get("VIX Open", 0))
    vix_close = safe_float(summary.get("VIX Close", 0))
    vix_avg = (vix_open + vix_close) / 2

    spx_open = safe_float(summary.get("SPX Open", 0))
    spx_high = safe_float(summary.get("SPX High", 0))
    spx_low = safe_float(summary.get("SPX Low", 0))
    actual_range = spx_high - spx_low

    # Expected move = SPX × VIX% / sqrt(252)
    expected_move = spx_open * (vix_avg / 100) / (252 ** 0.5) if spx_open else 0
    ratio = actual_range / expected_move if expected_move else 0

    # Assessment
    if ratio < 0.5:
        assessment = "Far below expected (compressed)"
    elif ratio < 0.75:
        assessment = "Below expected (calm)"
    elif ratio <= 1.05:
        assessment = "At expected (normal)"
    elif ratio <= 1.5:
        assessment = "Above expected"
    else:
        assessment = "FAR above expected (extreme)"

    row = (
        f"| {date_label} | {vix_avg:.1f} | ~{expected_move:.0f} pts | "
        f"{actual_range:.0f} pts | {ratio:.2f}x | {assessment} |"
    )

    parser.insert_lines(last_row + 1, [row])
    logger.info(f"Added Section 4 Expected Move row for {date_label}")


# =============================================================================
# SECTION 5: PERFORMANCE METRICS (FULL RECOMPUTATION)
# =============================================================================

def recompute_section5(
    parser: JournalParser,
    all_summary_rows: List[Dict[str, Any]],
    first_date: str,
    last_date: str,
    total_days: int,
):
    """
    Recompute all Section 5 aggregate metrics from the full dataset
    and replace the entire section content.
    """
    sec5_range = parser.get_section5_range()
    if sec5_range is None:
        logger.error("Cannot find Section 5")
        return

    start, end = sec5_range

    first_label = format_date_label(first_date)
    last_label = format_date_label(last_date)

    # Compute aggregates
    total_credit = sum(safe_float(r.get("Total Credit ($)", 0)) for r in all_summary_rows)
    total_expired = sum(safe_float(r.get("Expired Credits ($)", 0)) for r in all_summary_rows)
    total_stops_debits = sum(safe_float(r.get("Stop Loss Debits ($)", 0)) for r in all_summary_rows)
    total_commission = sum(safe_float(r.get("Commission ($)", 0)) for r in all_summary_rows)
    net_pnl = total_expired - total_stops_debits - total_commission

    total_entries = sum(safe_int(r.get("Entries Completed", 0)) for r in all_summary_rows)
    total_call_stops = sum(safe_int(r.get("Call Stops", 0)) for r in all_summary_rows)
    total_put_stops = sum(safe_int(r.get("Put Stops", 0)) for r in all_summary_rows)
    total_double_stops = sum(safe_int(r.get("Double Stops", 0)) for r in all_summary_rows)
    total_full_ics = sum(safe_int(r.get("Full ICs", 0)) for r in all_summary_rows)
    total_one_sided = sum(safe_int(r.get("One-Sided Entries", 0)) for r in all_summary_rows)
    daily_pnls = [safe_float(r.get("Daily P&L ($)", 0)) for r in all_summary_rows]
    winning_days = sum(1 for p in daily_pnls if p > 0)
    losing_days = sum(1 for p in daily_pnls if p < 0)
    best_day_pnl = max(daily_pnls) if daily_pnls else 0
    worst_day_pnl = min(daily_pnls) if daily_pnls else 0
    avg_daily_pnl = net_pnl / total_days if total_days else 0
    avg_daily_credit = total_credit / total_days if total_days else 0

    # Find best and worst day dates
    best_day_date = ""
    worst_day_date = ""
    for r in all_summary_rows:
        p = safe_float(r.get("Daily P&L ($)", 0))
        d = str(r.get("Date", "")).strip()
        if p == best_day_pnl and not best_day_date:
            best_day_date = format_date_label(d) if d else ""
        if p == worst_day_pnl and not worst_day_date:
            worst_day_date = format_date_label(d) if d else ""

    # Win rate calculations
    winning_total = sum(p for p in daily_pnls if p > 0)
    losing_total = sum(abs(p) for p in daily_pnls if p < 0)

    # Stop rates
    total_stop_sides = total_call_stops + total_put_stops
    stop_rate = (total_stop_sides / total_entries * 100) if total_entries else 0

    # Win rate by entries (entries with 0 stops)
    # For clean wins: compute from win rates in daily summary
    # Using individual day win rates weighted by entries
    clean_wins = 0
    partial_wins = 0
    full_losses = 0
    for r in all_summary_rows:
        entries_completed = safe_int(r.get("Entries Completed", 0))
        win_rate = safe_float(r.get("Win Rate (%)", 0)) / 100
        day_clean = round(entries_completed * win_rate)
        call_stops_day = safe_int(r.get("Call Stops", 0))
        put_stops_day = safe_int(r.get("Put Stops", 0))
        double_stops_day = safe_int(r.get("Double Stops", 0))
        one_sided_day = safe_int(r.get("One-Sided Entries", 0))

        clean_wins += day_clean

        # Full losses = double stops + one-sided entries that were stopped
        day_full_losses = double_stops_day

        # Estimate one-sided stops: total stop SIDES (not counting double stop sides
        # which are already accounted for) minus full IC partial wins (1 side stopped).
        # The number of entries with at least 1 stop = entries - clean.
        # Of those, full IC entries with 1 stop = partial wins.
        # One-sided entries that were stopped = entries_with_stops - partial_ic - double_stops.
        entries_with_stops = entries_completed - day_clean
        # Total stop sides from full ICs: each partial has 1 side, each double has 2
        # One-sided stops: each stopped one-sided has 1 stop side
        # day_stop_sides = partial_ic * 1 + double * 2 + one_sided_stops * 1
        day_stop_sides = call_stops_day + put_stops_day
        # One-sided can have at most one_sided_day stops, and at most
        # entries_with_stops - double_stops_day entries
        remaining_stopped_entries = entries_with_stops - double_stops_day
        # Stop sides from double stops = 2 * double_stops_day
        remaining_stop_sides = day_stop_sides - (2 * double_stops_day)
        if remaining_stop_sides < 0:
            remaining_stop_sides = 0

        # Full ICs with 1 stop contribute 1 side each.
        # One-sided entries with 1 stop contribute 1 side each.
        # remaining_stopped_entries = partial_ic + one_sided_stops
        # remaining_stop_sides = partial_ic * 1 + one_sided_stops * 1
        # So remaining_stop_sides = remaining_stopped_entries (each has exactly 1 stop side)
        # Therefore one_sided_stops = min(one_sided_day, remaining_stopped_entries)
        # But we can also bound it: one-sided stops <= one-sided entries
        day_one_sided_stops = min(one_sided_day, remaining_stopped_entries)
        if day_one_sided_stops < 0:
            day_one_sided_stops = 0
        day_full_losses += day_one_sided_stops

        full_losses += day_full_losses
        day_partial = entries_completed - day_clean - day_full_losses
        if day_partial < 0:
            day_partial = 0
        partial_wins += day_partial

    # Net capture rate
    net_capture_rate = (net_pnl / total_credit * 100) if total_credit else 0
    expired_pct = (total_expired / total_credit * 100) if total_credit else 0
    stops_pct = (total_stops_debits / total_credit * 100) if total_credit else 0
    commission_pct = (total_commission / total_credit * 100) if total_credit else 0

    # Win/loss dollar ratio
    dollar_ratio = (winning_total / losing_total) if losing_total else 99.99

    # Build new Section 5
    new_lines = [
        f"## 5. Key Performance Metrics",
        "",
        f"### Financial Metrics ({total_days} days: {first_label} - {last_label})",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total Credit Collected | ${format_money(total_credit)} |",
        f"| Total Expired Credits | ${format_money(total_expired)} ({expired_pct:.1f}% of credit) |",
        f"| Total Stop Loss Debits | ${format_money(total_stops_debits)} ({stops_pct:.1f}% of credit) |",
        f"| Total Commission | ${format_money(total_commission)} ({commission_pct:.1f}% of credit) |",
        f"| Net P&L | {format_signed_currency(net_pnl)} ({net_capture_rate:.1f}% net capture rate) |",
        f"| Average Daily Credit | ${format_money(round(avg_daily_credit))} |",
        f"| Average Daily P&L | {format_signed_currency(round(avg_daily_pnl))} |",
        f"| Best Day | {format_signed_currency(best_day_pnl)} ({best_day_date}) |",
        f"| Worst Day | {format_signed_currency(worst_day_pnl)} ({worst_day_date}) |",
        f"| Win/Loss Day Ratio | {winning_days}:{losing_days} |",
        f"| Win/Loss Dollar Ratio | {dollar_ratio:.2f}:1 (${format_money(winning_total)} / ${format_money(losing_total)}) |",
        "",
        "### Entry Performance",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total Entries | {total_entries} |",
        f"| Clean Wins (0 stops) | {clean_wins} ({clean_wins/total_entries*100:.1f}%) |" if total_entries else "| Clean Wins (0 stops) | 0 |",
        f"| Partial Wins (1 side stopped, IC) | {partial_wins} ({partial_wins/total_entries*100:.1f}%) |" if total_entries else "| Partial Wins | 0 |",
        f"| Full Losses (stopped, 1-sided or double stop) | {full_losses} ({full_losses/total_entries*100:.1f}%) |" if total_entries else "| Full Losses | 0 |",
        f"| Entries with Call Stop | {total_call_stops} ({total_call_stops/total_entries*100:.1f}%) |" if total_entries else "| Call Stops | 0 |",
        f"| Entries with Put Stop | {total_put_stops} ({total_put_stops/total_entries*100:.1f}%) |" if total_entries else "| Put Stops | 0 |",
        f"| Double Stops | {total_double_stops} ({total_double_stops/total_entries*100:.1f}%) |" if total_entries else "| Double Stops | 0 |",
        "",
        "### Entry Type Distribution",
        "",
        "| Entry Type | Count | Stops | Stop Rate | Avg Credit |",
        "|------------|-------|-------|-----------|------------|",
    ]

    # Entry type distribution
    if total_full_ics > 0:
        # Total stop sides = call stops + put stops (double stops already counted in both)
        full_ic_stop_sides = total_call_stops + total_put_stops
        # Approximate per-side rate
        full_ic_total_sides = total_full_ics * 2
        full_ic_stop_rate = (full_ic_stop_sides / full_ic_total_sides * 100) if full_ic_total_sides else 0
        full_ic_credit = sum(
            safe_float(r.get("Total Credit ($)", 0)) for r in all_summary_rows
        ) / total_full_ics if total_full_ics else 0
        new_lines.append(
            f"| Full IC | {total_full_ics} | {full_ic_stop_sides} sides stopped* | "
            f"~{full_ic_stop_rate:.0f}% per side | ${format_money(full_ic_credit)} |"
        )

    if total_one_sided > 0:
        new_lines.append(
            f"| One-Sided (various) | {total_one_sided} | -- | -- | -- |"
        )

    new_lines.append("")
    new_lines.append(
        "*Full ICs can have 0, 1, or 2 sides stopped. "
        "v1.4.0+ (Feb 27 onward) disabled one-sided entries — all new entries are Full ICs."
    )
    new_lines.append("")

    # Stop Clustering Data table header
    new_lines.extend([
        "### Stop Clustering Data",
        "",
        "| Date | Stops | Fastest Cluster | Entries After Cluster | Loss After Cluster |",
        "|------|-------|----------------|-----------------------|-------------------|",
    ])

    # Add rows from daily data
    for r in all_summary_rows:
        date_str = str(r.get("Date", "")).strip()
        dlabel = format_date_label(date_str) if date_str else "?"
        cs = safe_int(r.get("Call Stops", 0))
        ps = safe_int(r.get("Put Stops", 0))
        ds = safe_int(r.get("Double Stops", 0))
        total = cs + ps - ds  # Stopped entries (double stops counted once in cs and once in ps)

        if total == 0:
            new_lines.append(f"| {dlabel} | 0 | N/A | N/A | N/A |")
        elif total == 1:
            new_lines.append(f"| {dlabel} | 1 | N/A (single) | N/A | N/A |")
        else:
            # Bold for high stop counts
            prefix = "**" if total >= 4 else ""
            new_lines.append(
                f"| {prefix}{dlabel}{prefix} | {prefix}{total}{prefix} | "
                f"See entry detail | See entry detail | See entry detail |"
            )

    new_lines.append("")

    # Trend Filter Accuracy table header
    new_lines.extend([
        "### Trend Filter Accuracy",
        "",
        "| Date | Trend Signals | Were They Correct? | Trend Filter Impact |",
        "|------|--------------|--------------------|--------------------|",
    ])

    for r in all_summary_rows:
        date_str = str(r.get("Date", "")).strip()
        dlabel = format_date_label(date_str) if date_str else "?"
        bullish = safe_int(r.get("Bullish Signals", 0))
        bearish = safe_int(r.get("Bearish Signals", 0))
        neutral = safe_int(r.get("Neutral Signals", 0))

        parts = []
        if neutral:
            parts.append(f"{neutral} NEUTRAL")
        if bullish:
            parts.append(f"{bullish} BULLISH")
        if bearish:
            parts.append(f"{bearish} BEARISH")
        signals = ", ".join(parts) if parts else "None"

        # Basic assessment (narrative generator can enhance this)
        if bullish == 0 and bearish == 0:
            correct = "Yes (all neutral)"
            impact = "Neutral"
        else:
            correct = "See entry detail"
            impact = "See entry detail"

        new_lines.append(f"| {dlabel} | {signals} | {correct} | {impact} |")

    new_lines.append("")
    new_lines.append("---")
    new_lines.append("")

    parser.replace_range(start, end - 1, new_lines)
    logger.info(f"Recomputed Section 5 ({total_days} days, {total_entries} entries)")


# =============================================================================
# SECTION 8: IMPLEMENTATION LOG
# =============================================================================

def add_section8_version_rows(
    parser: JournalParser,
    new_versions: List[Dict[str, str]],
):
    """Add new version rows to Section 8 implementation table."""
    last_row = parser.get_section8_table_last_row()
    if last_row is None:
        logger.warning("Cannot find Section 8 table")
        return

    for version in new_versions:
        date = version.get("date", "")
        ver = version.get("version", "")
        desc = version.get("description", "")

        row = (
            f"| {date} | -- | v{ver}: {desc} | "
            f"v{ver} commits | {date} | Auto-detected by HOMER |"
        )
        parser.insert_lines(last_row + 1, [row])
        last_row += 1

    logger.info(f"Added {len(new_versions)} version rows to Section 8")


# =============================================================================
# SECTION 9: POST-IMPROVEMENT TRACKING
# =============================================================================

def build_section9_day_block(
    day_data: Dict[str, Any],
    day_number: int,
    narratives: Dict[str, str],
) -> List[str]:
    """
    Build a Section 9 post-improvement assessment block.

    Args:
        day_data: Day-specific data.
        day_number: Post-improvement day number (incrementing).
        narratives: Dict with "assessment" key from narrative generator.

    Returns:
        List of lines for the block.
    """
    summary = day_data["summary"]
    date_str = str(summary.get("Date", "")).strip()
    date_label = format_date_label(date_str)
    notes = summary.get("Notes", "")

    # Extract version from notes if available
    version_match = re.search(r"v(\d+\.\d+\.\d+)", str(notes))
    version_str = version_match.group(0) if version_match else "current"

    spx_open = safe_float(summary.get("SPX Open", 0))
    spx_close = safe_float(summary.get("SPX Close", 0))
    spx_high = safe_float(summary.get("SPX High", 0))
    spx_low = safe_float(summary.get("SPX Low", 0))
    spx_range = spx_high - spx_low
    spx_range_pct = (spx_range / spx_open * 100) if spx_open else 0
    vix_open = safe_float(summary.get("VIX Open", 0))
    vix_close = safe_float(summary.get("VIX Close", 0))

    entries = safe_int(summary.get("Entries Completed", 0))
    skipped = safe_int(summary.get("Entries Skipped", 0))
    full_ics = safe_int(summary.get("Full ICs", 0))
    one_sided = safe_int(summary.get("One-Sided Entries", 0))
    total_credit = safe_float(summary.get("Total Credit ($)", 0))
    call_stops = safe_int(summary.get("Call Stops", 0))
    put_stops = safe_int(summary.get("Put Stops", 0))
    stop_debits = safe_float(summary.get("Stop Loss Debits ($)", 0))
    commission = safe_float(summary.get("Commission ($)", 0))
    expired = safe_float(summary.get("Expired Credits ($)", 0))
    pnl = safe_float(summary.get("Daily P&L ($)", 0))
    cum_pnl = safe_float(summary.get("Cumulative P&L ($)", 0))
    early_close = summary.get("Early Close", "No")

    lines = [
        f"#### Post-Improvement Day {day_number}: {date_label} ({version_str})",
        "",
        f"| Column | {date_label} |",
        f"|--------|{''.ljust(len(date_label) + 2, '-')}|",
        f"| Date | {date_str} |",
        f"| SPX Open | {spx_open:,.2f} |",
        f"| SPX Close | {spx_close:,.2f} |",
        f"| SPX Range | {spx_range:.0f} pts ({spx_range_pct:.1f}%) |",
        f"| VIX Open | {vix_open:.2f} |",
        f"| VIX Close | {vix_close:.2f} |",
        f"| Entries | {entries} (+{skipped} skipped) |",
        f"| Full ICs | {full_ics} |",
        f"| One-Sided | {one_sided} |",
        f"| Total Credit | ${format_money(total_credit)} |",
        f"| Call Stops | {call_stops} |",
        f"| Put Stops | {put_stops} |",
        f"| Stop Debits | ${format_money(stop_debits)} |",
        f"| Commission | ${format_money(commission)} |",
        f"| Expired Credits | ${format_money(expired)} |",
        f"| Daily P&L | {format_signed_currency(pnl)} |",
        f"| Cumulative P&L | ${format_money(cum_pnl)} |",
        f"| Early Close | {early_close if early_close else 'No'} |",
        "",
    ]

    # Assessment narrative from Claude
    assessment = narratives.get("assessment", "")
    if assessment:
        lines.append(f"**{date_label} Assessment**: {assessment.strip()}")
        lines.append("")

    return lines


def insert_section9_block(parser: JournalParser, block_lines: List[str]):
    """Insert a day block at the end of Section 9."""
    insertion_point = parser.get_section9_insertion_point()
    if insertion_point is None:
        logger.error("Cannot find Section 9 insertion point")
        return

    parser.insert_lines(insertion_point, block_lines)
    logger.info(f"Inserted Section 9 block ({len(block_lines)} lines)")


# =============================================================================
# SECTION 1: EXECUTIVE SUMMARY
# =============================================================================

def update_section1(
    parser: JournalParser,
    all_summary_rows: List[Dict[str, Any]],
    first_date: str,
    last_date: str,
    total_days: int,
):
    """
    Update Section 1 executive summary with latest aggregate numbers.
    Uses regex replacement on specific lines.
    """
    sec1_range = parser.get_section1_range()
    if sec1_range is None:
        logger.error("Cannot find Section 1")
        return

    start, end = sec1_range

    # Calculate aggregates
    net_pnl = sum(
        safe_float(r.get("Expired Credits ($)", 0))
        - safe_float(r.get("Stop Loss Debits ($)", 0))
        - safe_float(r.get("Commission ($)", 0))
        for r in all_summary_rows
    )
    total_entries = sum(safe_int(r.get("Entries Completed", 0)) for r in all_summary_rows)
    # Total stop sides = call_stops + put_stops (double stops are already counted in both)
    total_stops = sum(
        safe_int(r.get("Call Stops", 0)) + safe_int(r.get("Put Stops", 0))
        for r in all_summary_rows
    )
    total_double_stops = sum(safe_int(r.get("Double Stops", 0)) for r in all_summary_rows)
    daily_pnls = [safe_float(r.get("Daily P&L ($)", 0)) for r in all_summary_rows]
    winning_days = sum(1 for p in daily_pnls if p > 0)
    losing_days = sum(1 for p in daily_pnls if p < 0)
    win_pct = (winning_days / total_days * 100) if total_days else 0
    loss_pct = (losing_days / total_days * 100) if total_days else 0

    # Clean wins = entries with 0 stops (using win rate from each day)
    clean_wins = 0
    for r in all_summary_rows:
        ec = safe_int(r.get("Entries Completed", 0))
        wr = safe_float(r.get("Win Rate (%)", 0)) / 100
        clean_wins += round(ec * wr)

    stop_rate = (total_stops / total_entries * 100) if total_entries else 0
    entry_win_rate = (clean_wins / total_entries * 100) if total_entries else 0

    first_label = format_date_label(first_date)
    last_label = format_date_label(last_date)

    # Update specific lines within Section 1
    for i in range(start, end):
        line = parser.lines[i]

        # Update "### Period Result" subsection
        if line.startswith("- **Net P&L**"):
            parser.lines[i] = f"- **Net P&L**: {format_signed_currency(net_pnl)}"
        elif line.startswith("- **Winning Days**"):
            parser.lines[i] = f"- **Winning Days**: {winning_days} ({win_pct:.1f}%)"
        elif line.startswith("- **Losing Days**"):
            parser.lines[i] = f"- **Losing Days**: {losing_days} ({loss_pct:.1f}%)"
        elif line.startswith("- **Total Entries**"):
            parser.lines[i] = f"- **Total Entries**: {total_entries}"
        elif line.startswith("- **Total Stops**"):
            parser.lines[i] = f"- **Total Stops**: {total_stops} ({stop_rate:.1f}% stop rate)"
        elif line.startswith("- **Double Stops**"):
            parser.lines[i] = f"- **Double Stops**: {total_double_stops}"
        elif line.startswith("- **Win Rate"):
            parser.lines[i] = f"- **Win Rate (entries with 0 stops)**: {entry_win_rate:.1f}% ({clean_wins}/{total_entries})"

        # Update trading days count
        if line.startswith("**Trading Days**"):
            parser.lines[i] = re.sub(
                r"\*\*Trading Days\*\*:\s*\d+",
                f"**Trading Days**: {total_days}",
                line,
            )

    # Update Last Updated date at top of file
    for i in range(min(10, len(parser.lines))):
        if parser.lines[i].startswith("**Last Updated**"):
            dt = datetime.strptime(last_date, "%Y-%m-%d")
            parser.lines[i] = f"**Last Updated**: {MONTH_ABBREV[dt.month]} {dt.day}, {dt.year}"
            break

    # Update ToC date range
    for i in range(min(20, len(parser.lines))):
        if "Trading Period:" in parser.lines[i] and "[" in parser.lines[i]:
            parser.lines[i] = re.sub(
                r"\[Trading Period:.*?\]",
                f"[Trading Period: {first_label} - {last_label}, {datetime.strptime(last_date, '%Y-%m-%d').year}]",
                parser.lines[i],
            )
            break

    # Update Section 1 heading date range
    year = datetime.strptime(last_date, "%Y-%m-%d").year
    for i in range(start, min(start + 5, end)):
        if parser.lines[i].startswith("## 1. Trading Period:"):
            parser.lines[i] = f"## 1. Trading Period: {first_label} - {last_label}, {year}"
            break

    # Update trading days list to include all dates
    last_dt = datetime.strptime(last_date, "%Y-%m-%d")
    last_day_str = f"{MONTH_ABBREV[last_dt.month]} {last_dt.day}"
    for i in range(start, end):
        line = parser.lines[i]
        if line.startswith("**Trading Days**") and "(" in line:
            # Check if the last date is already in the list
            if last_day_str not in line:
                # Insert the new date before the closing parenthesis
                parser.lines[i] = re.sub(
                    r"\)\s*$",
                    f", {last_day_str})",
                    line,
                )
            break

    logger.info(f"Updated Section 1 executive summary ({total_days} days)")
