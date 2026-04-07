"""
CALYPSO Services

Standalone services that run independently of trading bots.
These services run as systemd services/timers on the GCP VM.

Services:
---------
- token_keeper: Keeps Saxo OAuth tokens fresh 24/7
  - Checks token expiry every 60 seconds
  - Refreshes when < 5 minutes until expiry
  - Uses shared TokenCoordinator for file-based locking
  - Runs with Restart=always to ensure continuous operation

Agents (Claude-powered):
------------------------
- apollo: Morning Scout — pre-market briefing with risk level (8:30 AM ET weekdays)
- hermes: Daily Execution Quality Analyst — post-market report (7:00 PM ET weekdays)
- homer: Automated HYDRA Trading Journal Writer (7:30 PM ET weekdays)
- clio: Weekly Strategy Analyst (Saturday 9:00 AM ET)
- argus: Health Monitor — bot process, API, token status (every 15 min)

Deployment:
-----------
All services run on the GCP VM (calypso-bot, us-east1-b).
Service files are in deploy/*.service and should be copied to /etc/systemd/system/.

Last Updated: 2026-03-04
"""
