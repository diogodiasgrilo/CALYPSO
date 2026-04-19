"""
HERMES v1.1.0 — Daily Execution Quality Analyst for CALYPSO

Runs at 7:00 PM ET on weekdays via systemd timer. Collects the day's trading data,
pre-computes a cheat sheet of all arithmetic (counts, P&L, streaks), sends it to
Claude for narrative analysis, saves a report, and sends a summary alert.

v1.1.0 Changes:
    - Pre-computed cheat sheet prevents Claude arithmetic errors
    - Narrative-focused analysis (story of the day, not just numbers)
    - Updated strategy params for HYDRA v1.23.0 (2 effective base entries E#2+E#3 — E#1 at 10:15
      dropped at ALL VIX levels since 2026-04-17; VIX-scaled spreads 25-110pt; Downday-035 E6
      conditional call-only on down days; FOMC T+1 blackout supersedes MKT-038 call-only)
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
    deploy/hermes.timer                 systemd timer (7 PM ET weekdays)

Last Updated: 2026-04-19
"""
