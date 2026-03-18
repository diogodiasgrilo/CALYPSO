"""
HERMES data collector — gathers today's trading data from all sources.
"""

import json
import logging
import os
import subprocess
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def collect_daily_data(config: Dict[str, Any], today_str: str) -> Dict[str, Any]:
    """
    Collect all data HERMES needs for daily analysis.

    Args:
        config: Agent config dict.
        today_str: Date string "YYYY-MM-DD".

    Returns:
        Dict with all collected data (keys: apollo_report, daily_summary,
        positions, state, metrics, journal_logs).
    """
    data = {}

    # 1. Apollo's morning report
    data["apollo_report"] = _read_apollo_report(config, today_str)

    # 2. Google Sheets data
    data["daily_summary"] = _read_sheets_daily_summary(config)
    data["positions"] = _read_sheets_positions(config)

    # 3. State file
    data["state"] = _read_json_file(
        config.get("hermes", {}).get("state_file", "data/hydra_state.json")
    )

    # 4. Metrics file
    data["metrics"] = _read_json_file(
        config.get("hermes", {}).get("metrics_file", "data/hydra_metrics.json")
    )

    # 5. Journal logs
    journal_lines = config.get("hermes", {}).get("journal_lines", 200)
    data["journal_logs"] = _read_journal_logs(journal_lines)

    # Summarize what we collected
    collected = [k for k, v in data.items() if v]
    missing = [k for k, v in data.items() if not v]
    logger.info(f"Collected: {', '.join(collected)}")
    if missing:
        logger.warning(f"Missing: {', '.join(missing)}")

    return data


def _read_apollo_report(config: Dict[str, Any], today_str: str) -> Optional[str]:
    """Read today's Apollo morning report if it exists."""
    apollo_dir = config.get("apollo", {}).get("report_dir", "intel/apollo")
    report_path = os.path.join(apollo_dir, f"{today_str}.md")

    if os.path.exists(report_path):
        try:
            with open(report_path) as f:
                content = f.read()
            logger.info(f"Read Apollo report: {report_path} ({len(content)} chars)")
            return content
        except IOError as e:
            logger.warning(f"Failed to read Apollo report: {e}")

    logger.info("No Apollo report for today (Apollo may not have run yet)")
    return None


def _read_sheets_daily_summary(config: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Read today's daily summary row from Google Sheets."""
    try:
        from shared.sheets_reader import SheetsReader

        spreadsheet = config.get("google_sheets", {}).get(
            "spreadsheet_name", "Calypso_HYDRA_Live_Data"
        )
        reader = SheetsReader(config)
        return reader.get_last_row_as_dict(spreadsheet, "Daily Summary")
    except Exception as e:
        logger.warning(f"Failed to read Daily Summary from Sheets: {e}")
        return None


def _read_sheets_positions(config: Dict[str, Any]) -> Optional[List[Dict[str, str]]]:
    """Read today's position entries from Google Sheets."""
    try:
        from shared.sheets_reader import SheetsReader

        spreadsheet = config.get("google_sheets", {}).get(
            "spreadsheet_name", "Calypso_HYDRA_Live_Data"
        )
        reader = SheetsReader(config)
        # Read last 20 rows (max 6 entries × ~3 rows each for a typical day)
        return reader.read_tab_as_dicts(spreadsheet, "Positions", limit_rows=20)
    except Exception as e:
        logger.warning(f"Failed to read Positions from Sheets: {e}")
        return None


def _read_json_file(path: str) -> Optional[Dict]:
    """Read a JSON file, returning None on error."""
    if not os.path.exists(path):
        logger.info(f"File not found: {path}")
        return None

    try:
        with open(path) as f:
            data = json.load(f)
        logger.info(f"Read {path}")
        return data
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Failed to read {path}: {e}")
        return None


def _read_journal_logs(lines: int = 200) -> Optional[str]:
    """Read recent HYDRA journal logs via journalctl."""
    try:
        result = subprocess.run(
            ["journalctl", "-u", "hydra", "--since", "today", "-n", str(lines),
             "--no-pager"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            logger.info(f"Read {len(result.stdout.splitlines())} journal log lines")
            return result.stdout
        logger.info("No HYDRA journal logs for today")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("journalctl timed out")
        return None
    except FileNotFoundError:
        logger.info("journalctl not available (running locally?)")
        return None


# ---------------------------------------------------------------------------
# Cheat Sheet — pre-computed metrics for Claude (HERMES v1.1.0)
# ---------------------------------------------------------------------------


def compute_cheat_sheet(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pre-compute all counting and arithmetic for HERMES analysis.

    Claude MUST use these numbers directly instead of attempting its own
    arithmetic. Prevents hallucination of counts and P&L figures.

    Args:
        data: Collected data dict from collect_daily_data().

    Returns:
        Dict with all pre-computed metrics, or {"error": "..."} on failure.
    """
    state = data.get("state")
    metrics = data.get("metrics")
    apollo_report = data.get("apollo_report")

    if not state:
        return {"error": "state file unavailable"}

    entries = state.get("entries", [])

    # --- Per-entry outcomes ---
    entry_outcomes = []
    for e in entries:
        outcome = _classify_outcome(e)
        call_credit = e.get("call_spread_credit", 0) or 0
        put_credit = e.get("put_spread_credit", 0) or 0
        total_credit = call_credit + put_credit

        if e.get("call_side_skipped"):
            entry_type = "put_only"
        elif e.get("put_side_skipped"):
            entry_type = "call_only"
        else:
            entry_type = "full_ic"

        # MKT-033: Per-entry salvage revenue
        entry_salvage = (e.get("call_long_sold_revenue", 0) or 0) + (e.get("put_long_sold_revenue", 0) or 0)

        entry_outcomes.append({
            "entry_number": e.get("entry_number"),
            "trend_signal": e.get("trend_signal", "neutral"),
            "entry_type": entry_type,
            "call_credit": call_credit,
            "put_credit": put_credit,
            "total_credit": total_credit,
            "stop_level": (e.get("call_side_stop", 0) or 0)
            if not e.get("call_side_skipped")
            else (e.get("put_side_stop", 0) or 0),
            "outcome": outcome,
            "commission": (e.get("open_commission", 0) or 0)
            + (e.get("close_commission", 0) or 0),
            "salvage_revenue": entry_salvage,
        })

    # --- Aggregate counts (from state file counters) ---
    entries_placed = state.get("entries_completed", 0)
    entries_skipped = state.get("entries_skipped", 0)
    entries_failed = state.get("entries_failed", 0)
    total_attempted = entries_placed + entries_skipped + entries_failed

    call_stops = state.get("call_stops_triggered", 0)
    put_stops = state.get("put_stops_triggered", 0)
    double_stops_count = state.get("double_stops", 0)

    clean_entries = sum(1 for eo in entry_outcomes if eo["outcome"] == "clean")
    entries_with_stops = sum(1 for eo in entry_outcomes if eo["outcome"] != "clean")

    # --- Best / worst entry by outcome category ---
    # clean > one-side-stop > double-stop; within same category, higher credit = better
    outcome_rank = {"clean": 0, "call_stopped": 1, "put_stopped": 1, "double_stopped": 2}
    best_entry = None
    worst_entry = None
    if entry_outcomes:
        sorted_best = sorted(
            entry_outcomes,
            key=lambda x: (outcome_rank.get(x["outcome"], 2), -x["total_credit"]),
        )
        sorted_worst = sorted(
            entry_outcomes,
            key=lambda x: (-outcome_rank.get(x["outcome"], 0), x["total_credit"]),
        )
        b = sorted_best[0]
        w = sorted_worst[0]
        best_entry = {
            "num": b["entry_number"],
            "outcome": b["outcome"],
            "total_credit": b["total_credit"],
        }
        worst_entry = {
            "num": w["entry_number"],
            "outcome": w["outcome"],
            "total_credit": w["total_credit"],
        }

    # --- Stop side pattern ---
    stop_side_pattern = _detect_stop_pattern(entry_outcomes)

    # --- P&L (authoritative from state file) ---
    total_realized_pnl = state.get("total_realized_pnl", 0)
    total_commission = state.get("total_commission", 0)
    net_pnl = total_realized_pnl - total_commission
    total_credit_received = state.get("total_credit_received", 0)

    # Expired credits: sum credits for sides that expired worthless (profit)
    expired_credits = 0.0
    for e in entries:
        if e.get("call_side_expired"):
            expired_credits += e.get("call_spread_credit", 0) or 0
        if e.get("put_side_expired"):
            expired_credits += e.get("put_spread_credit", 0) or 0

    # Stop loss debits derived from P&L identity (Fix #78)
    stop_loss_debits = expired_credits - total_realized_pnl

    # --- MKT-033: Long leg salvage ---
    long_salvage_revenue = 0.0
    call_salvage_count = 0
    put_salvage_count = 0
    for e in entries:
        call_rev = e.get("call_long_sold_revenue", 0) or 0
        put_rev = e.get("put_long_sold_revenue", 0) or 0
        long_salvage_revenue += call_rev + put_rev
        if e.get("call_long_sold"):
            call_salvage_count += 1
        if e.get("put_long_sold"):
            put_salvage_count += 1
    total_salvage_count = call_salvage_count + put_salvage_count

    # --- Market data from state OHLC ---
    ohlc = state.get("market_data_ohlc", {})
    spx = {
        "open": ohlc.get("spx_open", 0),
        "high": ohlc.get("spx_high", 0),
        "low": ohlc.get("spx_low", 0),
        "range_pts": round(
            (ohlc.get("spx_high", 0) or 0) - (ohlc.get("spx_low", 0) or 0), 2
        ),
    }
    vix = {
        "open": ohlc.get("vix_open", 0),
        "high": ohlc.get("vix_high", 0),
        "low": ohlc.get("vix_low", 0),
    }

    # --- Cumulative context from metrics file ---
    daily_returns = []
    if metrics and metrics.get("daily_returns"):
        daily_returns = metrics["daily_returns"]

    win_streak, lose_streak = _compute_streak(daily_returns)
    avg_win, avg_loss = _compute_averages(daily_returns)

    cumulative = {
        "day_number": len(daily_returns) if daily_returns else 1,
        "cumulative_pnl": metrics.get("cumulative_pnl", 0) if metrics else net_pnl,
        "winning_days": metrics.get("winning_days", 0) if metrics else (1 if net_pnl >= 0 else 0),
        "losing_days": metrics.get("losing_days", 0) if metrics else (1 if net_pnl < 0 else 0),
        "win_streak": win_streak,
        "lose_streak": lose_streak,
        "avg_win_pnl": avg_win,
        "avg_loss_pnl": avg_loss,
    }

    # --- Apollo accuracy ---
    apollo = _extract_apollo_assessment(apollo_report, net_pnl)

    # --- Early close ---
    early_close_triggered = state.get("early_close_triggered", False)

    # --- FOMC config (user-configurable override) ---
    hydra_config = _read_json_file("bots/hydra/config/config.json")
    fomc_skip = True  # default
    if hydra_config:
        fomc_skip = hydra_config.get("strategy", {}).get("fomc_announcement_skip", True)

    return {
        "entry_outcomes": entry_outcomes,
        "entries_placed": entries_placed,
        "entries_skipped": entries_skipped,
        "entries_failed": entries_failed,
        "total_attempted": total_attempted,
        "clean_entries": clean_entries,
        "entries_with_stops": entries_with_stops,
        "call_stops_total": call_stops,
        "put_stops_total": put_stops,
        "total_stopped_sides": call_stops + put_stops,
        "double_stops": double_stops_count,
        "early_close_triggered": early_close_triggered,
        "best_entry": best_entry,
        "worst_entry": worst_entry,
        "stop_side_pattern": stop_side_pattern,
        "net_pnl": net_pnl,
        "total_realized_pnl": total_realized_pnl,
        "total_commission": total_commission,
        "total_credit": total_credit_received,
        "expired_credits": expired_credits,
        "stop_loss_debits": stop_loss_debits,
        "long_salvage_revenue": long_salvage_revenue,
        "long_salvage_count": total_salvage_count,
        "spx": spx,
        "vix": vix,
        "cumulative": cumulative,
        "apollo": apollo,
        "fomc_announcement_skip": fomc_skip,
    }


def _classify_outcome(entry: Dict) -> str:
    """Classify entry outcome: clean, call_stopped, put_stopped, or double_stopped."""
    call_stopped = entry.get("call_side_stopped", False)
    put_stopped = entry.get("put_side_stopped", False)

    if call_stopped and put_stopped:
        return "double_stopped"
    if call_stopped:
        return "call_stopped"
    if put_stopped:
        return "put_stopped"
    return "clean"


def _detect_stop_pattern(entry_outcomes: List[Dict]) -> str:
    """Detect the predominant stop side pattern across all entries."""
    call_stops = 0
    put_stops = 0
    for eo in entry_outcomes:
        if eo["outcome"] in ("call_stopped", "double_stopped"):
            call_stops += 1
        if eo["outcome"] in ("put_stopped", "double_stopped"):
            put_stops += 1

    total = call_stops + put_stops
    if total == 0:
        return "none"
    if call_stops == total:
        return "all call-side"
    if put_stops == total:
        return "all put-side"
    if call_stops / total > 0.6:
        return "mostly call-side"
    if put_stops / total > 0.6:
        return "mostly put-side"
    return "mixed"


def _compute_streak(daily_returns: List[Dict]) -> Tuple[int, int]:
    """Compute current win/lose streak from daily returns (iterates from end)."""
    if not daily_returns:
        return 0, 0

    win_streak = 0
    lose_streak = 0

    for dr in reversed(daily_returns):
        pnl = dr.get("net_pnl", 0)
        if pnl >= 0:
            if lose_streak > 0:
                break
            win_streak += 1
        else:
            if win_streak > 0:
                break
            lose_streak += 1

    return win_streak, lose_streak


def _compute_averages(daily_returns: List[Dict]) -> Tuple[float, float]:
    """Compute average win P&L and average loss P&L from daily returns."""
    wins = [dr["net_pnl"] for dr in daily_returns if dr.get("net_pnl", 0) > 0]
    losses = [dr["net_pnl"] for dr in daily_returns if dr.get("net_pnl", 0) < 0]

    avg_win = round(sum(wins) / len(wins), 1) if wins else 0.0
    avg_loss = round(sum(losses) / len(losses), 1) if losses else 0.0

    return avg_win, avg_loss


def _extract_apollo_assessment(
    apollo_report: Optional[str], net_pnl: float
) -> Dict[str, Any]:
    """Extract Apollo's risk level and assess accuracy against actual P&L."""
    if not apollo_report:
        return {"risk_level": None, "accurate": None, "note": "Apollo report unavailable"}

    # Apollo ALWAYS starts with RISK: GREEN/YELLOW/RED on first line
    risk_level = None
    for line in apollo_report.strip().split("\n")[:5]:
        line_upper = line.strip().upper()
        for level in ["GREEN", "YELLOW", "RED"]:
            if f"RISK: {level}" in line_upper or f"RISK:{level}" in line_upper:
                risk_level = level
                break
        if risk_level:
            break

    if not risk_level:
        return {"risk_level": None, "accurate": None, "note": "Could not parse risk level"}

    # Accuracy heuristic
    if risk_level == "YELLOW":
        accurate = True
        note = "YELLOW is always considered accurate (hedge assessment)"
    elif risk_level == "GREEN" and net_pnl >= 0:
        accurate = True
        note = "GREEN + positive day = accurate"
    elif risk_level == "GREEN" and net_pnl < 0:
        accurate = False
        note = "GREEN but negative day"
    elif risk_level == "RED" and net_pnl < 0:
        accurate = True
        note = "RED + negative day = accurate"
    elif risk_level == "RED" and net_pnl >= 0:
        accurate = False
        note = "RED but positive day"
    else:
        accurate = None
        note = ""

    return {"risk_level": risk_level, "accurate": accurate, "note": note}
