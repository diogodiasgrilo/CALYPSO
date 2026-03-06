# HYDRA Dashboard — Frontend

Real-time monitoring dashboard for the HYDRA trading bot. Built with React 19 + TypeScript + Vite.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | React 19 + TypeScript |
| Build | Vite 6 |
| Styling | Tailwind CSS v4 (HYDRA brand color system) |
| State | Zustand 5 |
| Charts | Recharts (bar/scatter/area) + TradingView Lightweight Charts (candlestick) |
| Icons | lucide-react |
| PWA | vite-plugin-pwa |

## Pages

| Page | Route | Purpose |
|------|-------|---------|
| Dashboard | `/` | Live entries, P&L, SPX chart, cushion bars, agent status, log feed |
| History | `/history` | Calendar heat map of daily P&L, day drill-down |
| Analytics | `/analytics` | Entry time performance, VIX correlation, stop analysis, day-of-week P&L |

## Color System

All colors derived from the HYDRA logo (dark teal background):

- Background: `#2a3a42` / Deep: `#1e2c33` / Elevated: `#344a52`
- Profit: `#7ee8c7` (mint) / Loss: `#f85149` (coral) / Warning: `#d29922` (amber)
- Text: Primary `#e8edf3` / Secondary `#8b9bb0` / Dim `#5e6e82`

Defined in both `src/lib/tradingColors.ts` (JS) and `src/index.css` (CSS custom properties).

## Development

```bash
npm install
npm run dev          # Dev server at http://localhost:5173
npm run build        # Production build → dist/
```

Dev server proxies `/api/*` and `/ws/*` to the backend (see `vite.config.ts`).

## Deployment

Build locally, then copy `dist/` to the VM:

```bash
npm run build
gcloud compute scp --recurse dist/ calypso-bot:/tmp/dashboard-dist --zone=us-east1-b
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo cp -r /tmp/dashboard-dist/* /opt/calypso/dashboard/frontend/dist/ && sudo chown -R calypso:calypso /opt/calypso/dashboard/frontend/dist/"
```

nginx serves static files from `/opt/calypso/dashboard/frontend/dist/` on port 8080.

## Safety

The dashboard is 100% read-only. It reads HYDRA's existing data files — zero changes to the bot. The bot has no idea the dashboard exists. `systemctl stop dashboard` has zero effect on HYDRA.
