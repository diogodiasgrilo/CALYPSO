# HERMES — Daily Execution Quality Analyst

**Last Updated:** 2026-03-01

Runs at 5:00 PM ET on weekdays via systemd timer. Analyzes today's HYDRA trading execution using Claude and sends a summary alert.

---

## Data Sources

| Source | What | How |
|--------|------|-----|
| Apollo report | Morning market context + risk level | `intel/apollo/YYYY-MM-DD.md` |
| Google Sheets | Daily Summary (today's P&L row) | `SheetsReader` → "Daily Summary" tab |
| Google Sheets | Positions (today's entries) | `SheetsReader` → "Positions" tab |
| State file | HYDRA's current state | `data/hydra_state.json` |
| Metrics file | Cumulative metrics | `data/hydra_metrics.json` |
| Journal logs | Last 200 lines from HYDRA | `journalctl -u hydra --since today` |

## Analysis Framework

1. Market context vs outcome correlation (Apollo accuracy)
2. Entry quality (fill slippage, credit gate activity, timing)
3. Stop loss analysis (slippage, side distribution)
4. P&L reconciliation (verify identity: Expired Credits - Stop Debits - Commission = Net)
5. Key insights (3-5 actionable bullet points)

## Output

| Path | Description |
|------|-------------|
| `intel/hermes/YYYY-MM-DD.md` | Full analysis report (markdown) |
| Telegram/Email alert | 5-line summary |

---

## Files

| File | Purpose |
|------|---------|
| `services/hermes/main.py` | Entry point, orchestrates collect → analyze → save → alert |
| `services/hermes/data_collector.py` | Reads Sheets, state file, metrics, journal logs |
| `services/hermes/analyzer.py` | Builds prompt, calls Claude, extracts summary |
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
