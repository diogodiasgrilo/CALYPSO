"""
APOLLO — Morning Scout for CALYPSO

Runs at 8:30 AM ET on weekdays via systemd timer. Fetches pre-market data,
checks the economic calendar, reads yesterday's HERMES report and strategy
memory, then calls Claude for a morning briefing with risk level assessment.

Data Sources:
    - VIX level (Yahoo Finance via ExternalPriceFeed)
    - SPY pre-market price (Yahoo Finance)
    - S&P 500 futures ES=F (Yahoo Finance)
    - Yesterday's HERMES report (intel/hermes/)
    - Strategy memory (intel/strategy_memory.md)
    - Economic calendar (shared/event_calendar.py)

Output:
    - intel/apollo/YYYY-MM-DD.md — morning briefing with risk level
    - Telegram/Email alert with full briefing

Files:
    services/apollo/main.py            Entry point
    services/apollo/market_data.py     Fetches pre-market data
    services/apollo/scout.py           Builds prompt, calls Claude
    deploy/apollo.service              systemd oneshot service
    deploy/apollo.timer                systemd timer (8:30 AM ET weekdays)

Last Updated: 2026-03-01
"""
