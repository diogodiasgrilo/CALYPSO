"""
HOMER — Automated HYDRA Trading Journal Writer for CALYPSO

Named after Homer, the writer of the Odyssey.

Runs at 5:30 PM ET on weekdays via systemd timer. Detects missing trading days
in the HYDRA Trading Journal, gathers data from all sources, and updates all
journal sections automatically. Commits and pushes changes to git.

Data Sources:
    - Google Sheets: Daily Summary tab (all rows)
    - Google Sheets: Positions tab (entry-level detail)
    - Google Sheets: Trades tab (per-entry + per-stop records)
    - HYDRA log file (logs/hydra/bot.log) — fallback for missing Trades data
    - P&L identity derivation — fallback for missing individual stop P&L
    - Cumulative metrics (data/hydra_metrics.json)
    - HYDRA version history (bots/hydra/__init__.py)
    - HERMES daily report (intel/hermes/) — context for Claude narratives

Output:
    - docs/HYDRA_TRADING_JOURNAL.md — updated with new trading day(s)
    - intel/homer/journal_backup_YYYY-MM-DD.md — pre-edit backup
    - git commit + push of journal changes
    - Telegram alert with completion summary

Sections Updated:
    1. Executive Summary (trading period, aggregate metrics)
    2. Daily Summary Table (column-per-day format)
    3. Entry-Level Detail (per-day blocks with tables + narratives)
    4. Market Conditions (two tables: character + expected move)
    5. Key Performance Metrics (aggregate recomputation)
    8. Implementation Log (new versions since last entry)
    9. Post-Improvement Performance Tracking (day blocks)

Files:
    services/homer/main.py               Entry point (orchestration, git, alerts)
    services/homer/data_collector.py     Gathers data from Sheets + files
    services/homer/journal_parser.py     Parses journal structure (sections, tables)
    services/homer/journal_updater.py    Applies updates section-by-section
    services/homer/narrative_generator.py Claude API for observations/assessments
    deploy/homer.service                 systemd oneshot service
    deploy/homer.timer                   systemd timer (5:30 PM ET weekdays)

Last Updated: 2026-03-04
"""
