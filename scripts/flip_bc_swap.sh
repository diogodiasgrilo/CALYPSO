#!/usr/bin/env bash
# One-shot manual SWAP of live-paper trading from variant C -> variant B.
#
#   C (hydra_variant_c): dry_run false->true, alerts true->false
#                        (stops placing REAL orders + stops alerting)
#   B (hydra_variant_b): dry_run true->false, alerts false->true
#                        (starts placing REAL paper orders + starts alerting)
#   A (hydra)          : NOT touched.
#
# WHY alerts flip too: a live variant that can't alert trades SILENTLY. Alerts
# must follow the live seat, so B gains alerts.enabled and C loses it.
#
# ORDER MATTERS (swap-audit B5): C is set dry FIRST + restarted, THEN B is set
# live. Run AFTER the close + after-hours settlement, when the day's 0DTE
# positions have expired and BOTH variants are flat — so B going live can never
# collide with a still-open C position at the same strike (IBKR merges positions
# at the same (conid, side) on the one shared paper account).
#
# NOTE: this script does NOT change contract size or the position cap. Set B's
# `contracts_per_entry` and `max_contracts_per_underlying` to the desired live
# values in config_variant_b.json BEFORE running this (see RB-8 / BC_SWAP_PLAN).
#
# OPERATOR-RUN (manual), as root, AFTER the close + after-hours settlement.
# There is intentionally NO auto-flip timer — a date-pinned auto-flip is a
# go-live footgun. Idempotent. Runbook: docs/migration/RUNBOOKS.md RB-8.
set -uo pipefail

VENV=/opt/calypso/.venv/bin/python
log() { echo "flip_bc_swap: $*"; }

# ── Guard 1 — broker session must be live RIGHT NOW ────────────────────────────
h=$(curl -s --max-time 6 http://127.0.0.1:8788/health 2>/dev/null || true)
if ! echo "$h" | grep -qE '"connected": ?true'; then
    log "ABORT: broker /health not connected ($h) — leaving B/C unchanged"
    exit 0
fi

# ── Guard 2 — require a FRESH (today ET) paper-smoke PASS sentinel ─────────────
SENT=/opt/calypso/data/smoke/last_pass.txt
today=$(TZ=America/New_York date +%F)
if ! grep -q "^${today} PASS" "$SENT" 2>/dev/null; then
    log "ABORT: no fresh ($today ET) paper-smoke PASS at $SENT — not swapping."
    log "       Run a paper-smoke TODAY first: sudo systemctl start broker-paper-smoke"
    exit 0
fi

# ── Guard 3 — the overlay over-placement FIX must be deployed ─────────────────
# B places defensive overlays LIVE. The pre-fix code over-placed each overlay
# leg contracts_per_entry-fold and truncated into a naked short. NEVER flip B
# live onto that code.
BSTRAT=/opt/calypso/bots/hydra/brandon/strategy.py
if ! grep -q "_brandon_unwind_overlay_legs" "$BSTRAT"; then
    log "ABORT: overlay over-placement fix NOT deployed (no _brandon_unwind_overlay_legs"
    log "       in $BSTRAT). Deploy the fix + clear __pycache__ + restart hydra_variant_b first."
    exit 0
fi

# ── Guard 4 — the shared paper account should be FLAT before swapping ─────────
# Query the broker for open option positions (authoritative). After the close +
# settlement it must be empty; a non-empty book means something is still open
# and swapping risks a same-strike merge.
openpos=$(runuser -u calypso -- env CALYPSO_BROKER_URL=http://127.0.0.1:8788 "$VENV" - <<'PY' 2>/dev/null || echo ERR
import sys
sys.path.insert(0, "/opt/calypso")
try:
    from shared.broker_client import BrokerClient
    bc = BrokerClient("http://127.0.0.1:8788")
    pos = bc.get_positions() or []
    n = sum(1 for p in pos if abs(float(p.get("position", p.get("quantity", 0)) or 0)) > 0)
    print(n)
except Exception as e:
    print("ERR:%s" % e)
PY
)
if [ "$openpos" = "ERR" ] || echo "$openpos" | grep -q "ERR:"; then
    log "WARN: could not read broker positions ($openpos) — proceeding on operator timing."
elif [ "$openpos" != "0" ]; then
    log "ABORT: broker shows $openpos open position(s) — the account is not flat."
    log "       Run this AFTER the close + settlement, or flatten first (flatten_variant.py)."
    exit 0
else
    log "account flat (0 open positions) — safe to swap."
fi

# ── Config edit helpers ───────────────────────────────────────────────────────
# set_key <label> <cfg> <dotted.key> <json-literal>
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
json.loads(out)  # validate before write
tmp = fn + ".tmp"
open(tmp, "w").write(out)
os.replace(tmp, fn)
print("%s <- %s" % (key, val))
PY
    local rc=$?
    [ "$rc" -ne 0 ] && { log "ABORT: $label edit of $key failed"; exit 1; }
}

CFG_B=/opt/calypso/bots/hydra/config/config_variant_b.json
CFG_C=/opt/calypso/bots/hydra/config/config_variant_c.json

# ── STEP 1 — C off the live seat FIRST (stop real orders + alerts), restart ───
log "STEP 1: C -> dry_run:true, alerts:false"
set_key "C" "$CFG_C" "dry_run" "true"
set_key "C" "$CFG_C" "alerts.enabled" "false"
systemctl restart hydra_variant_c
sleep 10

# ── STEP 2 — B onto the live seat (real orders + alerts), restart ─────────────
log "STEP 2: B -> dry_run:false, alerts:true"
set_key "B" "$CFG_B" "dry_run" "false"
set_key "B" "$CFG_B" "alerts.enabled" "true"
systemctl restart hydra_variant_b
sleep 15

# ── Verify ────────────────────────────────────────────────────────────────────
report=""
for pair in "B:hydra_variant_b:false" "C:hydra_variant_c:true"; do
    label="${pair%%:*}"; rest="${pair#*:}"; unit="${rest%%:*}"; want="${rest##*:}"
    act=$(systemctl is-active "$unit" 2>/dev/null)
    cfg="/opt/calypso/bots/hydra/config/config_variant_${label,,}.json"
    dr=$(runuser -u calypso -- "$VENV" -c \
        "import json;print(str(json.load(open('$cfg')).get('dry_run')).lower())" 2>/dev/null || echo ERR)
    ok="OK"; [ "$dr" != "$want" ] && ok="MISMATCH(want=$want got=$dr)"
    log "$label: $unit active=$act dry_run=$dr $ok"
    report="${report}${label}(active=${act},dry=${dr}) "
done

# ── Telegram (best-effort) ────────────────────────────────────────────────────
runuser -u calypso -- env CALYPSO_BROKER_URL=http://127.0.0.1:8788 "$VENV" - "$report" <<'PY' || true
import sys
sys.path.insert(0, "/opt/calypso")
report = sys.argv[1] if len(sys.argv) > 1 else ""
try:
    from shared.alert_service import AlertService, AlertType, AlertPriority
    AlertService({"alerts": {"enabled": True}}, "HYDRA").send_alert(
        alert_type=AlertType.API_ERROR,
        title="HYDRA live seat swapped C -> B ✅",
        message=(f"Variant B is now LIVE paper trading; C is now dry-run. "
                 f"State: {report.strip()}. B will place REAL paper orders + "
                 f"alert at its entry windows. A unchanged. Rollback: "
                 f"scripts/flip_bc_rollback.sh (flatten B first)."),
        priority=AlertPriority.HIGH,
    )
except Exception as e:
    print("swap alert failed:", e)
PY

log "done — B is LIVE paper trading, C is dry-run, A unchanged."
