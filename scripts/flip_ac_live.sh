#!/usr/bin/env bash
# One-shot manual go-live: flip variant A (hydra) AND variant C (hydra_variant_c)
# from dry_run:true → dry_run:false (REAL paper trading), run AFTER the
# 2026-06-02 close so both are live for the 2026-06-03 session from the open.
# B (hydra_variant_b) intentionally stays dry-run.
#
# OPERATOR-RUN (manual), as root, AFTER the close + after-hours settlement.
# There is intentionally NO auto-flip systemd timer for A+C: a date-pinned
# auto-flip is a go-live footgun (a clock change or a stale unit could re-fire
# it). Run a fresh paper-smoke the SAME ET day first — it writes today's PASS
# sentinel that Guard 2 below requires. Runbook: docs/migration/RUNBOOKS.md RB-8.
# Restarts services as root; config edits run as the calypso user. Idempotent.
set -uo pipefail

VENV=/opt/calypso/.venv/bin/python
log() { echo "flip_ac_live: $*"; }

# Guard 1 — broker session must be live RIGHT NOW (never flip onto a dead session).
h=$(curl -s --max-time 6 http://127.0.0.1:8788/health 2>/dev/null || true)
if ! echo "$h" | grep -qE '"connected": ?true'; then
    log "ABORT: broker /health not connected ($h) — leaving A/C in dry-run"
    exit 0
fi

# Guard 2 — require a FRESH (today) paper-smoke PASS sentinel: defense in depth so
# we only go live if the real order path was validated today.
SENT=/opt/calypso/data/smoke/last_pass.txt
# AUD5 GL-1: pin to the Eastern (market) day so the "fresh today" check matches
# the ET-dated sentinel the smoke writes, regardless of the VM's system TZ.
today=$(TZ=America/New_York date +%F)
if ! grep -q "^${today} PASS" "$SENT" 2>/dev/null; then
    log "ABORT: no fresh ($today ET) paper-smoke PASS sentinel at $SENT — not flipping."
    log "       Run a paper-smoke TODAY first: sudo systemctl start broker-paper-smoke"
    exit 0
fi

# Flip one strategy: $1=label  $2=config path  $3=systemd unit
flip_one() {
    local label="$1" cfg="$2" unit="$3" cur rc
    cur=$(runuser -u calypso -- "$VENV" -c \
        "import json;print(json.load(open('$cfg')).get('dry_run'))" 2>/dev/null || echo ERR)
    if [ "$cur" != "True" ]; then
        log "$label dry_run is '$cur' (not True) — no change"
        return 0
    fi
    runuser -u calypso -- "$VENV" - "$cfg" <<'PY'
import json, os, sys
fn = sys.argv[1]
d = json.load(open(fn))
d["dry_run"] = False
out = json.dumps(d, indent=2) + "\n"
json.loads(out)  # validate before write
tmp = fn + ".tmp"
open(tmp, "w").write(out)
os.replace(tmp, fn)
print("set dry_run=false")
PY
    rc=$?
    if [ "$rc" -ne 0 ]; then
        log "ABORT: $label config edit failed — NOT restarting $unit"
        return 1
    fi
    log "$label flipped to dry_run:false; restarting $unit"
    systemctl restart "$unit"
}

flip_one "A" /opt/calypso/bots/hydra/config/config.json            hydra
flip_one "C" /opt/calypso/bots/hydra/config/config_variant_c.json  hydra_variant_c

# Verify both came up and are no longer in dry-run.
sleep 15
report=""
for pair in "A:hydra" "C:hydra_variant_c"; do
    label="${pair%%:*}"; unit="${pair##*:}"
    act=$(systemctl is-active "$unit" 2>/dev/null)
    drline=$(journalctl -u "$unit" --since "60 seconds ago" --no-pager 2>/dev/null \
             | grep -c "DRY RUN")
    log "$label: $unit active=$act  DRY-RUN-loglines-last60s=$drline"
    report="${report}${label}=${act}(dry_lines=${drline}) "
done

# Telegram alert (best-effort) so the operator knows the morning state.
runuser -u calypso -- env CALYPSO_BROKER_URL=http://127.0.0.1:8788 "$VENV" - "$report" <<'PY' || true
import sys
sys.path.insert(0, "/opt/calypso")
report = sys.argv[1] if len(sys.argv) > 1 else ""
try:
    from shared.alert_service import AlertService, AlertType, AlertPriority
    AlertService({"alerts": {"enabled": True}}, "HYDRA").send_alert(
        alert_type=AlertType.API_ERROR,
        title="HYDRA A + C → LIVE paper trading ✅",
        message=(f"A (hydra) and C (hydra_variant_c) flipped to dry_run:false after "
                 f"the 2026-06-02 close and restarted ({report.strip()}). They will "
                 f"place REAL paper orders at tomorrow's entry windows "
                 f"(10:15/10:45/11:15 ET). B stays dry-run. Revert: set "
                 f"dry_run:true in the config + restart the unit."),
        priority=AlertPriority.HIGH,
    )
except Exception as e:
    print("flip alert failed:", e)
PY

log "done — A + C are now LIVE paper trading (B remains dry-run)"
