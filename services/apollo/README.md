# APOLLO — Morning Scout

**Last Updated:** 2026-03-29

Runs at 8:30 AM ET on weekdays via systemd timer. Provides a pre-market briefing with risk assessment for the trading day.

---

## Data Sources

| Source | What | How |
|--------|------|-----|
| VIX level | Current VIX | `ExternalPriceFeed` (Yahoo Finance) |
| SPY price | Pre-market SPY | `ExternalPriceFeed` (Yahoo Finance) |
| ES futures | S&P 500 futures | `ExternalPriceFeed` (Yahoo Finance) |
| HERMES report | Yesterday's execution analysis | `intel/hermes/YYYY-MM-DD.md` |
| Strategy memory | Cumulative learnings | `intel/strategy_memory.md` |
| Economic calendar | FOMC, CPI, Jobs, earnings | `shared/event_calendar.py` |

## Risk Levels

| Level | VIX Range | Events | Expected Impact |
|-------|-----------|--------|-----------------|
| GREEN | 12-20 | None major | 2 base entries (E#2+E#3; E#1 dropped at all VIX since 2026-04-17), normal fills |
| YELLOW | 20-25 | Minor data | Possible MKT-011 skip on E#3, wider spreads |
| RED | > 25 | CPI, Jobs | Multiple stops likely, entry skips, VIX regime may cap to E#3-only at VIX ≥ 28 |

## Output

| Path | Description |
|------|-------------|
| `intel/apollo/YYYY-MM-DD.md` | Full morning briefing (markdown) |
| Telegram/Email alert | Full briefing with risk level in title |

---

## Files

| File | Purpose |
|------|---------|
| `services/apollo/main.py` | Entry point, orchestrates data → briefing → alert |
| `services/apollo/market_data.py` | Fetches VIX, SPY, ES=F from Yahoo Finance |
| `services/apollo/scout.py` | Builds prompt, calls Claude for risk assessment |
| `deploy/apollo.service` | systemd oneshot service |
| `deploy/apollo.timer` | systemd timer (8:30 AM ET weekdays) |

## Commands

```bash
# Deploy timer
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo cp /opt/calypso/deploy/apollo.service /opt/calypso/deploy/apollo.timer /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable apollo.timer && sudo systemctl start apollo.timer"

# Run manually
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start apollo.service && sudo journalctl -u apollo -n 50 --no-pager"

# View today's briefing
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /opt/calypso/intel/apollo/$(date +%Y-%m-%d).md"
```
