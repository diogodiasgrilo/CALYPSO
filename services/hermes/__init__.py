"""
HERMES — Daily Execution Quality Analyst for CALYPSO

Runs at 5:00 PM ET on weekdays via systemd timer. Collects the day's trading data,
sends it to Claude for analysis, saves a markdown report, and sends a summary alert.

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
    services/hermes/data_collector.py   Gathers data from all sources
    services/hermes/analyzer.py         Builds prompt, calls Claude
    deploy/hermes.service               systemd oneshot service
    deploy/hermes.timer                 systemd timer (5 PM ET weekdays)

Last Updated: 2026-03-01
"""
