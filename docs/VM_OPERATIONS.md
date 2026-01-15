# Google Cloud VM Operations Guide

## VM Details
- **VM Name:** `calypso-bot`
- **Zone:** `us-east1-b`
- **Bot Location:** `/opt/calypso`
- **Log File:** `/opt/calypso/logs/bot_output.log`
- **Virtual Env:** `/opt/calypso/.venv`

## Quick Commands

### Check if bot is running
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="ps aux | grep -E 'python.*main.py' | grep -v grep" 2>/dev/null
```

### View recent logs (last 50 lines)
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="tail -50 /opt/calypso/logs/bot_output.log" 2>/dev/null
```

### View more logs (last 200 lines)
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="tail -200 /opt/calypso/logs/bot_output.log" 2>/dev/null
```

### Search logs for errors/warnings
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="grep -E 'ERROR|WARNING|CRITICAL' /opt/calypso/logs/bot_output.log | tail -30" 2>/dev/null
```

### List log files
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="ls -la /opt/calypso/logs/" 2>/dev/null
```

## Deployment Commands

### Pull latest code from git
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cd /opt/calypso && git pull" 2>/dev/null
```

### Stop the bot
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="pkill -f 'python3 src/main.py'" 2>/dev/null
```

### Start the bot (dry-run mode)
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cd /opt/calypso && nohup .venv/bin/python3 src/main.py --live --dry-run > logs/bot_output.log 2>&1 &" 2>/dev/null
```

### Start the bot (LIVE mode - real trades)
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cd /opt/calypso && nohup .venv/bin/python3 src/main.py --live > logs/bot_output.log 2>&1 &" 2>/dev/null
```

### Full deploy sequence (pull + restart)
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cd /opt/calypso && git pull && pkill -f 'python3 src/main.py'" 2>/dev/null
sleep 2
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cd /opt/calypso && nohup .venv/bin/python3 src/main.py --live --dry-run > logs/bot_output.log 2>&1 &" 2>/dev/null
sleep 5
gcloud compute ssh calypso-bot --zone=us-east1-b --command="ps aux | grep -E 'python.*main.py' | grep -v grep" 2>/dev/null
```

## Troubleshooting

### Check VM status
```bash
gcloud compute instances describe calypso-bot --zone=us-east1-b --format="value(status)"
```

### Start VM if stopped
```bash
gcloud compute instances start calypso-bot --zone=us-east1-b
```

### Interactive SSH session
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b
```

### Check disk space
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="df -h /opt/calypso" 2>/dev/null
```

### Check memory usage
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="free -h" 2>/dev/null
```

## Notes
- Always add `2>/dev/null` to suppress SSH connection warnings
- Bot runs with `nohup` so it persists after SSH disconnects
- The `--dry-run` flag prevents real trades (simulation mode)
- Log file is at `/opt/calypso/logs/bot_output.log`
