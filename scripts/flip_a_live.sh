#!/usr/bin/env bash
# Conditional auto-flip: variant A (hydra) → dry_run:false → LIVE paper trading.
#
# Wired as `ExecStartPost=+` on broker-paper-smoke.service. systemd runs an
# ExecStartPost line ONLY if the ExecStart command (the paper smoke, run with
# --place) exited 0 — i.e. a full paper ROUND-TRIP PASS: a real 1-contract order
# placed, FILLED at a live price, and closed flat. That PASS path also hard-
# gates on the account being paper (DU…) before it writes, so reaching here
# means: broker healthy + real fills working + paper account confirmed.
#
# The `+` prefix runs this as ROOT (the service is User=calypso, which can't
# restart hydra). It flips ONLY variant A; B/C stay dry-run. Idempotent +
# guarded; logs to the journal and alerts via Telegram.
set -uo pipefail

CFG=/opt/calypso/bots/hydra/config/config.json
VENV=/opt/calypso/.venv/bin/python
log() { echo "flip_a_live: $*"; }

# Guard 1 — broker session must be live RIGHT NOW (never flip A onto a dead session).
h=$(curl -s --max-time 6 http://127.0.0.1:8788/health 2>/dev/null || true)
if ! echo "$h" | grep -qE '"connected": ?true'; then
    log "ABORT: broker /health not connected ($h) — leaving A in dry-run"
    exit 0
fi

# Guard 2 — idempotent: only flip if A is currently dry_run:true.
cur=$(runuser -u calypso -- "$VENV" -c "import json;print(json.load(open('$CFG')).get('dry_run'))" 2>/dev/null || echo ERR)
if [ "$cur" != "True" ]; then
    log "A dry_run is '$cur' (not True) — no change"
    exit 0
fi

# Flip dry_run -> false (as calypso, JSON-validated, temp+rename atomic).
runuser -u calypso -- "$VENV" - "$CFG" <<'PY'
import json, os, sys
fn = sys.argv[1]
d = json.load(open(fn))
d["dry_run"] = False
out = json.dumps(d, indent=2) + "\n"
json.loads(out)  # validate
tmp = fn + ".tmp"
open(tmp, "w").write(out)
os.replace(tmp, fn)
print("set A dry_run=false")
PY
if [ $? -ne 0 ]; then
    log "ABORT: config edit failed — NOT restarting hydra"
    exit 1
fi

log "A flipped to dry_run:false; restarting hydra.service"
systemctl restart hydra.service

# Alert (best-effort) — loop the operator in AFTER the flip.
runuser -u calypso -- env CALYPSO_BROKER_URL=http://127.0.0.1:8788 "$VENV" - <<'PY' || true
import sys
sys.path.insert(0, "/opt/calypso")
try:
    from shared.alert_service import AlertService, AlertType, AlertPriority
    AlertService({"alerts": {"enabled": True}}, "HYDRA").send_alert(
        alert_type=AlertType.API_ERROR,
        title="HYDRA A → LIVE paper trading ✅",
        message=("Paper smoke PASSED — variant A auto-flipped to dry_run:false "
                 "and restarted. A will place REAL paper orders at today's entry "
                 "windows (10:15/10:45/11:15 ET). B and C remain dry-run. To "
                 "revert: set dry_run:true in config.json + restart hydra."),
        priority=AlertPriority.HIGH,
    )
except Exception as e:
    print("flip alert failed:", e)
PY

log "done — variant A is now LIVE paper trading"
