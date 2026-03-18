# HERMES v1.1.0 — Daily Execution Quality Analyst

**Last Updated:** 2026-03-18

Runs at 5:00 PM ET on weekdays via systemd timer. Pre-computes a cheat sheet of all arithmetic (counts, P&L, streaks), sends it to Claude for narrative analysis, saves a report, and sends a summary alert.

---

## v1.1.0 Changes

- **Pre-computed cheat sheet** prevents Claude arithmetic errors (e.g., "5 stopped legs: 4C/2P" when 4+2=6)
- **Narrative-focused analysis** — explains WHY, not just WHAT (story of the day, market context)
- **Updated strategy params** for HYDRA v1.6.0 (5 entries, asymmetric spreads, 3.5×/4.0× OTM)
- **Cumulative context** — win/lose streak, avg win/loss, day number
- **Apollo accuracy assessment** — did pre-market risk level match actual outcome?
- **Trimmed state file** — strips UICs, position IDs to save tokens
- **No redundant header** — summary body only (AlertService adds title automatically)

## Data Sources

| Source | What | How |
|--------|------|-----|
| Apollo report | Morning market context + risk level | `intel/apollo/YYYY-MM-DD.md` |
| Google Sheets | Daily Summary (today's P&L row) | `SheetsReader` → "Daily Summary" tab |
| Google Sheets | Positions (today's entries) | `SheetsReader` → "Positions" tab |
| State file | HYDRA's current state | `data/hydra_state.json` |
| Metrics file | Cumulative metrics | `data/hydra_metrics.json` |
| Journal logs | Last 200 lines from HYDRA | `journalctl -u hydra --since today` |

## Analysis Framework (v1.1.0)

1. **Story of the Day** — Connect SPX movement to stop outcomes, explain the market narrative
2. **Apollo Accuracy** — Did pre-market risk assessment match actual outcome?
3. **Entry Quality** — MKT-020/022 tightening, credit levels, MKT-011 skips
4. **Stop Analysis** — Stop side pattern, best/worst entry (from cheat sheet)
5. **Cumulative Context** — How today compares to avg win/loss, current streak

## Output

| Path | Description |
|------|-------------|
| `intel/hermes/YYYY-MM-DD.md` | Full analysis report (markdown) |
| Telegram/Email alert | 5-line summary (narrative insight on line 5) |

---

## Files

| File | Purpose |
|------|---------|
| `services/hermes/main.py` | Entry point, orchestrates collect → cheat sheet → analyze → save → alert |
| `services/hermes/data_collector.py` | Reads data sources + `compute_cheat_sheet()` (pre-computes all arithmetic) |
| `services/hermes/analyzer.py` | Builds prompt (cheat sheet first), calls Claude, extracts summary |
| `deploy/hermes.service` | systemd oneshot service |
| `deploy/hermes.timer` | systemd timer (5 PM ET weekdays) |

## Commands

```bash
# Deploy timer
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo cp /opt/calypso/deploy/hermes.service /opt/calypso/deploy/hermes.timer /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable hermes.timer && sudo systemctl start hermes.timer"

# Run manually
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start hermes.service && sudo journalctl -u hermes -n 50 --no-pager"

# View today's report
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /opt/calypso/intel/hermes/$(date +%Y-%m-%d).md"

# Check timer
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl list-timers | grep hermes"
```
