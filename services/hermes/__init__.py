"""
HERMES v1.1.0 — Daily Execution Quality Analyst for CALYPSO

Runs at 5:00 PM ET on weekdays via systemd timer. Collects the day's trading data,
pre-computes a cheat sheet of all arithmetic (counts, P&L, streaks), sends it to
Claude for narrative analysis, saves a report, and sends a summary alert.

v1.1.0 Changes:
    - Pre-computed cheat sheet prevents Claude arithmetic errors
    - Narrative-focused analysis (story of the day, not just numbers)
    - Updated strategy params for HYDRA v1.19.0 (3 base entries, VIX-scaled spreads 25-83pt)
    - Cumulative context (win/lose streak, averages, day number)
    - Apollo accuracy assessment
    - Trimmed state file to save tokens (strip UICs, position IDs)
    - Removed redundant header from summary (AlertService adds it)

Data Sources:
    - Apollo's morning report (intel/apollo/YYYY-MM-DD.md)
    - Google Sheets: Daily Summary tab (today's row)
    - Google Sheets: Positions tab (today's entries)
    - State file: data/hydra_state.json
    - Metrics file: data/hydra_metrics.json
    - Journal logs: last 200 lines from HYDRA service

Output:
    - intel/hermes/YYYY-MM-DD.md — full analysis report
    - Telegram/Email alert with 5-line summary

Files:
    services/hermes/main.py             Entry point
    services/hermes/data_collector.py   Gathers data + compute_cheat_sheet()
    services/hermes/analyzer.py         Builds prompt, calls Claude
    deploy/hermes.service               systemd oneshot service
    deploy/hermes.timer                 systemd timer (5 PM ET weekdays)

Last Updated: 2026-03-29
"""
