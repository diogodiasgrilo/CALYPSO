# Dashboard UI audit harness

Renders **every surface for every strategy** against real production payloads, screenshots each, and
collects console errors. Findings: [`docs/DASHBOARD_VISUAL_AUDIT_2026_09_17.md`](../../../docs/DASHBOARD_VISUAL_AUDIT_2026_09_17.md).

## Why it exists

The dashboard sits behind bcrypt + TOTP, so a browser cannot log in to it. Every prior dashboard
audit was therefore done by *reading code and diffing API payloads* — which cannot see a layout
collapse, a meaningless chart, or a number that is wrong rather than missing. This harness closes
that gap.

## Running

```bash
cd dashboard/frontend && npm run build
cd uiaudit && node mock-server.mjs &
node audit.mjs        # → shots/*.png, findings.json
node structural.mjs   # → structural.json
```

`fixtures/`, `shots/` and the JSON reports are gitignored — regenerate them, don't commit them.

## Re-capturing fixtures

The capture scripts call the FastAPI router handlers directly on the VM (read-only, no auth, no
mutation) and write one JSON per endpoint × strategy. See the audit doc §1.

## Two traps, both hit during the first run

- **The picker's localStorage key is `calypso-selected-strategy`** (`store/hydraStore.ts`). Guess it
  wrong and all 7 strategies render the default while the run still reports success — the variant
  dimension is silently untested.
- **The WS replay is mandatory.** The PRIMARY strategy renders from the live WebSocket, not from
  `/snapshot` (`StrategyDashboard.tsx:57`). With no WS server the live seat renders an empty shell,
  so the seat that matters most is the one you cannot see without it.

Both are the same failure mode this harness exists to catch: a check that appears to pass because it
never ran.
