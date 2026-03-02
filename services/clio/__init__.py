"""
CLIO — Weekly Strategy Analyst & Optimizer for CALYPSO

Runs Saturday 9:00 AM ET via systemd timer. Aggregates the week's data from
all agents, calls Claude for deep strategy analysis, saves a weekly report,
appends new learnings to strategy_memory.md, commits both to git, and runs
retention cleanup on old reports.

Data Sources:
    - All HERMES reports from the past week (intel/hermes/)
    - All APOLLO reports from the past week (intel/apollo/)
    - Cumulative metrics (data/hydra_metrics.json)
    - Full Daily Summary history from Google Sheets
    - Previous CLIO report (intel/clio/)
    - Strategy memory (intel/strategy_memory.md)

Output:
    - intel/clio/week_YYYY_WNN.md — weekly analysis report (committed to git)
    - intel/strategy_memory.md — appended with new learnings (committed to git)
    - git commit + push of both files
    - Telegram/Email alert with weekend digest

Files:
    services/clio/main.py              Entry point (includes git commit logic)
    services/clio/data_aggregator.py   Aggregates week's data from all sources
    services/clio/analyst.py           Builds prompt, calls Claude
    services/cleanup_intel.py          Shared retention cleanup
    deploy/clio.service                systemd oneshot service
    deploy/clio.timer                  systemd timer (Saturday 9 AM ET)

Last Updated: 2026-03-01
"""
