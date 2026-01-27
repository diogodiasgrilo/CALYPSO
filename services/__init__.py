"""
CALYPSO Services

Standalone services that run independently of trading bots.
These services run as systemd services on the GCP VM.

Services:
---------
- token_keeper: Keeps Saxo OAuth tokens fresh 24/7
  - Checks token expiry every 60 seconds
  - Refreshes when < 5 minutes until expiry
  - Uses shared TokenCoordinator for file-based locking
  - Runs with Restart=always to ensure continuous operation
  - See: services/token_keeper/README.md for full documentation

Deployment:
-----------
All services run on the GCP VM (calypso-bot, us-east1-b).
Service files are in deploy/*.service and should be copied to /etc/systemd/system/.

Commands:
---------
    # Check token keeper status
    gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl status token_keeper"

    # View logs
    gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u token_keeper -f"

Last Updated: 2026-01-27
"""
