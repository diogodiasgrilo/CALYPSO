#!/usr/bin/env bash
# ROLLBACK of the C->B live-seat swap: put C back on the live seat, B back to
# dry-run.
#
#   B (hydra_variant_b): dry_run false->true, alerts true->false
#   C (hydra_variant_c): dry_run true->false, alerts false->true
#   A (hydra)          : NOT touched.
#
# CRITICAL (swap-audit B2): flipping B to dry_run:true does NOT close, manage, or
# even monitor B's open positions — a live position would ride to expiry
# UNMANAGED (no stop-loss). So this script HARD-ABORTS unless the account is
# already FLAT. Flatten B FIRST while it is still live:
#
#   sudo -u calypso CALYPSO_BROKER_URL=http://127.0.0.1:8788 \
#       /opt/calypso/.venv/bin/python /opt/calypso/scripts/flatten_paper_account.py --execute
#
# then confirm flat and run this. Easiest path: run it AFTER the close +
# settlement, when the day's 0DTE positions have expired on their own.
#
# OPERATOR-RUN (manual), as root. Idempotent. Runbook: docs/migration/RUNBOOKS.md RB-8.
set -uo pipefail

VENV=/opt/calypso/.venv/bin/python
log() { echo "flip_bc_rollback: $*"; }

# ── Guard 1 — broker session live now ─────────────────────────────────────────
h=$(curl -s --max-time 6 http://127.0.0.1:8788/health 2>/dev/null || true)
if ! echo "$h" | grep -qE '"connected": ?true'; then
    log "ABORT: broker /health not connected ($h) — leaving B/C unchanged"
    exit 0
fi

# ── Guard 2 — the account MUST be FLAT (hard) ─────────────────────────────────
openpos=$(runuser -u calypso -- env CALYPSO_BROKER_URL=http://127.0.0.1:8788 "$VENV" - <<'PY' 2>/dev/null || echo ERR
import sys
sys.path.insert(0, "/opt/calypso")
try:
    from shared.broker_client import BrokerClient
    bc = BrokerClient("http://127.0.0.1:8788")
    pos = bc.get_positions() or []
    def q(p):
        for k in ("position","quantity","size"):
            if p.get(k) is not None:
                try: return float(p[k])
                except Exception: pass
        return 0.0
    def opt(p):
        ac = str(p.get("assetClass") or p.get("asset_class") or p.get("secType") or "").upper()
        return ac in ("OPT","FOP")
    print(sum(1 for p in pos if opt(p) and abs(q(p))>0))
except Exception as e:
    print("ERR:%s" % e)
PY
)
if [ "$openpos" = "ERR" ] || echo "$openpos" | grep -q "ERR:"; then
    log "ABORT: could not read broker positions ($openpos) — refusing to flip B blind."
    log "       Verify flat manually, then re-run."
    exit 0
elif [ "$openpos" != "0" ]; then
    log "ABORT: broker shows $openpos open option position(s) — B is NOT flat."
    log "       Flipping B to dry now would strand them unmanaged. Flatten FIRST:"
    log "         sudo -u calypso CALYPSO_BROKER_URL=http://127.0.0.1:8788 \\"
    log "           $VENV /opt/calypso/scripts/flatten_paper_account.py --execute"
    log "       (or run after the close when 0DTE has settled), then re-run."
    exit 1
fi
log "account flat (0 open positions) — safe to roll back."

# ── Config edit helper ────────────────────────────────────────────────────────
set_key() {
    local label="$1" cfg="$2" key="$3" val="$4"
    runuser -u calypso -- "$VENV" - "$cfg" "$key" "$val" <<'PY'
import json, os, sys
fn, key, val = sys.argv[1], sys.argv[2], sys.argv[3]
d = json.load(open(fn))
node = d
parts = key.split(".")
for p in parts[:-1]:
    node = node.setdefault(p, {})
node[parts[-1]] = json.loads(val)
out = json.dumps(d, indent=2) + "\n"
json.loads(out)
tmp = fn + ".tmp"; open(tmp, "w").write(out); os.replace(tmp, fn)
print("%s <- %s" % (key, val))
PY
    [ "$?" -ne 0 ] && { log "ABORT: $label edit of $key failed"; exit 1; }
}

CFG_B=/opt/calypso/bots/hydra/config/config_variant_b.json
CFG_C=/opt/calypso/bots/hydra/config/config_variant_c.json

# ── STEP 1 — B off the live seat FIRST, restart ───────────────────────────────
log "STEP 1: B -> dry_run:true, alerts:false"
set_key "B" "$CFG_B" "dry_run" "true"
set_key "B" "$CFG_B" "alerts.enabled" "false"
systemctl restart hydra_variant_b
sleep 10

# ── STEP 2 — C back onto the live seat, restart ───────────────────────────────
log "STEP 2: C -> dry_run:false, alerts:true"
set_key "C" "$CFG_C" "dry_run" "false"
set_key "C" "$CFG_C" "alerts.enabled" "true"
systemctl restart hydra_variant_c
sleep 15

# ── STEP 3 — restart the dashboard so its WS/widget canonical view re-resolves ─
# back onto C (the live seat again). Read-only service; safe anytime.
log "STEP 3: restart dashboard so the WS/widget canonical view follows C"
systemctl restart dashboard 2>/dev/null || log "  (dashboard restart skipped — unit not present)"

# ── Verify + alert ────────────────────────────────────────────────────────────
for pair in "B:hydra_variant_b:true" "C:hydra_variant_c:false"; do
    label="${pair%%:*}"; rest="${pair#*:}"; unit="${rest%%:*}"; want="${rest##*:}"
    act=$(systemctl is-active "$unit" 2>/dev/null)
    cfg="/opt/calypso/bots/hydra/config/config_variant_${label,,}.json"
    dr=$(runuser -u calypso -- "$VENV" -c \
        "import json;print(str(json.load(open('$cfg')).get('dry_run')).lower())" 2>/dev/null || echo ERR)
    ok="OK"; [ "$dr" != "$want" ] && ok="MISMATCH(want=$want got=$dr)"
    log "$label: $unit active=$act dry_run=$dr $ok"
done

runuser -u calypso -- env CALYPSO_BROKER_URL=http://127.0.0.1:8788 "$VENV" - <<'PY' || true
import sys
sys.path.insert(0, "/opt/calypso")
try:
    from shared.alert_service import AlertService, AlertType, AlertPriority
    AlertService({"alerts": {"enabled": True}}, "HYDRA").send_alert(
        alert_type=AlertType.API_ERROR,
        title="HYDRA live seat rolled back B -> C ✅",
        message=("Variant C is LIVE paper trading again; B is back to dry-run. "
                 "A unchanged."),
        priority=AlertPriority.HIGH,
    )
except Exception as e:
    print("rollback alert failed:", e)
PY

log "done — C is LIVE paper trading, B is dry-run, A unchanged."
