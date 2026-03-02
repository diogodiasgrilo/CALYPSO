# CLIO — Weekly Strategy Analyst & Optimizer

**Last Updated:** 2026-03-01

Runs Saturday 9:00 AM ET via systemd timer. Performs deep weekly strategy analysis using Claude and maintains the cumulative strategy memory.

---

## Data Sources

| Source | What | How |
|--------|------|-----|
| HERMES reports | All daily reports from past week | `intel/hermes/YYYY-MM-DD.md` |
| APOLLO reports | All morning briefings from past week | `intel/apollo/YYYY-MM-DD.md` |
| Google Sheets | Full Daily Summary history | `SheetsReader` → "Daily Summary" tab |
| Metrics | Cumulative trading metrics | `data/hydra_metrics.json` |
| Previous CLIO | Last weekly report for continuity | `intel/clio/week_YYYY_WNN.md` |
| Strategy memory | Cumulative learnings | `intel/strategy_memory.md` |

## Analysis Framework

1. **Weekly Synthesis** — P&L attribution, VIX regime, entry slot analysis, equity curve
2. **Apollo Accuracy** — Were morning risk assessments predictive?
3. **Strategy Recommendations** — Parameter changes with confidence + evidence
4. **New Learnings** — 3-5 durable insights appended to strategy memory

## Output

| Path | Description | Committed? |
|------|-------------|------------|
| `intel/clio/week_YYYY_WNN.md` | Full weekly report | Yes (git commit + push) |
| `intel/strategy_memory.md` | Appended with new learnings | Yes (git commit + push) |
| Telegram/Email alert | Weekend digest summary | N/A |

## Data Flow

```
APOLLO (daily) → intel/apollo/     ─┐
HERMES (daily) → intel/hermes/     ─┤
Google Sheets  → Daily Summary     ─┼→ CLIO (Saturday) → weekly report
Metrics        → hydra_metrics.json─┤                  → strategy_memory.md
Previous CLIO  → intel/clio/       ─┤                  → git commit + push
Strategy memory → strategy_memory  ─┘                  → cleanup old reports
```

---

## Files

| File | Purpose |
|------|---------|
| `services/clio/main.py` | Entry point (orchestrate + git commit + cleanup) |
| `services/clio/data_aggregator.py` | Aggregate week's data from all sources |
| `services/clio/analyst.py` | Build prompt, call Claude (max_tokens=12288) |
| `services/cleanup_intel.py` | Shared retention cleanup (Hermes: 90d, Apollo: 30d, Argus: 90d) |
| `deploy/clio.service` | systemd oneshot service (300s timeout) |
| `deploy/clio.timer` | systemd timer (Saturday 9 AM ET) |

## Commands

```bash
# Deploy timer
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo cp /opt/calypso/deploy/clio.service /opt/calypso/deploy/clio.timer /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable clio.timer && sudo systemctl start clio.timer"

# Run manually
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start clio.service && sudo journalctl -u clio -n 100 --no-pager"

# View latest report
gcloud compute ssh calypso-bot --zone=us-east1-b --command="ls -la /opt/calypso/intel/clio/"

# View strategy memory
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /opt/calypso/intel/strategy_memory.md"
```
