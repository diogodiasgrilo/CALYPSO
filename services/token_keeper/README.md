# Token Keeper Service

A dedicated service that keeps Saxo OAuth tokens fresh 24/7, independent of trading bot status.

## Problem Solved

Saxo Bank OAuth tokens expire every **20 minutes**. Previously, tokens were refreshed by whichever trading bot needed them. This created a problem:

- If all bots are stopped (e.g., for safety during volatile markets), no one refreshes the token
- The token expires after ~20 minutes
- Expired tokens require **manual OAuth browser flow** to re-authenticate
- Manual re-auth requires SSH to VM, running a script, and browser interaction

## Solution

Token Keeper is a lightweight service that:
1. Runs independently of all trading bots
2. Checks token expiry every 60 seconds
3. Refreshes token when it's within 5 minutes of expiry
4. Uses the same `TokenCoordinator` as all bots (file-based locking)
5. Saves refreshed tokens to both local cache and Secret Manager

## How It Works

```
┌─────────────────────────────────────────────────────────────────┐
│                       Token Keeper Service                       │
│                                                                  │
│   Every 60s:                                                     │
│   1. Check token expiry from cache file                         │
│   2. If < 5 min until expiry → refresh                          │
│   3. Acquire file lock (prevents race with bots)                │
│   4. Call Saxo /token endpoint with refresh_token               │
│   5. Save new tokens to cache + Secret Manager                  │
│   6. Release lock                                                │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│              /opt/calypso/data/saxo_token_cache.json            │
│                                                                  │
│   {                                                              │
│     "access_token": "eyJhbGci...",                              │
│     "refresh_token": "62055fcc-fa4d-...",                       │
│     "token_expiry": "2026-01-27T14:35:00",                      │
│     "app_key": "...",                                           │
│     "app_secret": "..."                                         │
│   }                                                              │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Trading Bots                              │
│                                                                  │
│   On startup / before API calls:                                │
│   1. Read token from cache file                                 │
│   2. If valid → use it                                          │
│   3. If expired → refresh (fallback if Token Keeper is down)    │
│                                                                  │
│   ┌──────────┐  ┌──────────────┐  ┌──────────┐  ┌──────────┐   │
│   │ Iron Fly │  │ Delta Neutral│  │   MEIC   │  │ Rolling  │   │
│   └──────────┘  └──────────────┘  └──────────┘  │ Put Diag │   │
│                                                  └──────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

## Configuration

| Setting | Value | Description |
|---------|-------|-------------|
| `CHECK_INTERVAL_SECONDS` | 60 | How often to check token status |
| `REFRESH_THRESHOLD_SECONDS` | 300 | Refresh when < 5 min until expiry |
| `MAX_REFRESH_FAILURES` | 5 | Alert after this many consecutive failures |

## Files

| File | Purpose |
|------|---------|
| `services/token_keeper/main.py` | Main service code |
| `deploy/token_keeper.service` | systemd service file |
| `/opt/calypso/data/saxo_token_cache.json` | Shared token cache |
| `/opt/calypso/data/saxo_token.lock` | File lock for coordination |

## First-Time Deployment

```bash
# 1. Push code to VM
git add -A && git commit -m "Add token keeper service" && git push

# 2. Pull on VM
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && git pull && find services shared -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; echo Cache cleared'"

# 3. Copy service file to systemd
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo cp /opt/calypso/deploy/token_keeper.service /etc/systemd/system/"

# 4. Reload systemd
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl daemon-reload"

# 5. Enable service (auto-start on boot)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl enable token_keeper"

# 6. Start service
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start token_keeper"

# 7. Verify it's running
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl status token_keeper"
```

## Commands

```bash
# Start
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start token_keeper"

# Stop (WARNING: token will expire in ~20 min!)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop token_keeper"

# Restart
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart token_keeper"

# Status
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl status token_keeper"

# View logs
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u token_keeper -n 50 --no-pager"

# Follow logs (live)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u token_keeper -f"

# Check current token expiry
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /opt/calypso/data/saxo_token_cache.json | python3 -c \"import json,sys; d=json.load(sys.stdin); print(f'Expires: {d.get(\\\"token_expiry\\\", \\\"unknown\\\")}')\""
```

## Expected Log Output

Normal operation:
```
TOKEN KEEPER SERVICE STARTING
Check interval: 60s
Refresh threshold: 300s before expiry
Token status: valid, 15 minutes until expiry
Token status: valid, 14 minutes until expiry
...
Token needs refresh: threshold reached
Token refreshed successfully (expires in 20 min)
New token valid for 19 minutes
```

If refresh fails:
```
Token refresh API failed: 401 - {"error": "invalid_grant"}
Token refresh failed (attempt 1/5)
...
ALERT: Token refresh failed 5 consecutive times! Manual intervention may be required.
```

## Troubleshooting

### Token refresh keeps failing

1. Check if refresh token is still valid:
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /opt/calypso/data/saxo_token_cache.json"
   ```

2. If `refresh_token` is empty or `null`, manual OAuth flow is required:
   ```bash
   # Run any bot interactively to trigger OAuth
   gcloud compute ssh calypso-bot --zone=us-east1-b
   cd /opt/calypso
   sudo -u calypso .venv/bin/python -m bots.iron_fly_0dte.main --status
   # Follow browser prompts to re-authenticate
   ```

### Service won't start

1. Check for Python errors:
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u token_keeper --since '5 minutes ago' --no-pager"
   ```

2. Test manually:
   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python -m services.token_keeper.main'"
   ```

### Lock contention issues

If you see "Could not acquire token lock" errors:
```bash
# Check who holds the lock
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo lsof /opt/calypso/data/saxo_token.lock"

# Remove stale lock (only if process is definitely dead!)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo rm -f /opt/calypso/data/saxo_token.lock"
```

## Integration with Trading Bots

Token Keeper is designed to run alongside trading bots:

1. **Token Keeper runs first** (via `Before=` in systemd)
2. **Bots check cache** before making API calls
3. **If cache is fresh** → bots use cached token
4. **If cache is stale** → bots can refresh (fallback)

This means:
- Token Keeper handles routine refresh (every ~15-18 min)
- Bots don't need to refresh unless Token Keeper is down
- File locking prevents race conditions

## Resource Usage

Token Keeper is very lightweight:
- CPU: ~0.1% (wakes every 60s, checks a file, sleeps)
- Memory: ~30-50 MB
- Network: One HTTPS request every ~15 minutes
- Disk: Writes ~1KB JSON file every ~15 minutes
