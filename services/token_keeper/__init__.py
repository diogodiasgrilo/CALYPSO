"""
Token Keeper Service

A dedicated service that keeps Saxo OAuth tokens fresh 24/7,
independent of trading bot status.

Why It's Needed:
----------------
- Saxo tokens expire every 20 minutes
- If all bots are stopped (e.g., for safety), no one refreshes the token
- Expired tokens require manual OAuth browser flow to re-authenticate
- This service ensures tokens stay fresh even when all trading bots are stopped

How It Works:
-------------
1. Runs as a lightweight systemd service with Restart=always
2. Checks token expiry every 60 seconds (CHECK_INTERVAL_SECONDS)
3. Refreshes token when it's within 5 minutes of expiry (REFRESH_THRESHOLD_SECONDS)
4. Uses the same TokenCoordinator as all bots (file-based locking)
5. Saves refreshed tokens to both local cache and Secret Manager

Configuration:
--------------
| Setting                    | Value | Description                              |
|----------------------------|-------|------------------------------------------|
| CHECK_INTERVAL_SECONDS     | 60    | How often to check token status          |
| REFRESH_THRESHOLD_SECONDS  | 300   | Refresh when < 5 min until expiry        |
| MAX_REFRESH_FAILURES       | 5     | Alert after this many consecutive fails  |

Files:
------
| File                                   | Purpose                    |
|----------------------------------------|----------------------------|
| services/token_keeper/main.py          | Main service code          |
| deploy/token_keeper.service            | systemd service file       |
| /opt/calypso/data/saxo_token_cache.json| Shared token cache         |
| /opt/calypso/data/saxo_token.lock      | File lock for coordination |

Usage:
------
    # As systemd service (production)
    sudo systemctl start token_keeper
    sudo systemctl status token_keeper
    sudo journalctl -u token_keeper -f

    # Directly (development)
    python -m services.token_keeper.main

See: services/token_keeper/README.md for full documentation.

Last Updated: 2026-01-27
"""

from services.token_keeper.main import (
    run_token_keeper,
    get_token_age_info,
    perform_token_refresh,
    CHECK_INTERVAL_SECONDS,
    REFRESH_THRESHOLD_SECONDS,
)

__all__ = [
    'run_token_keeper',
    'get_token_age_info',
    'perform_token_refresh',
    'CHECK_INTERVAL_SECONDS',
    'REFRESH_THRESHOLD_SECONDS',
]
