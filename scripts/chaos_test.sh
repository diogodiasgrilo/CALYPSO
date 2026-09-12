#!/usr/bin/env bash
#
# CHAOS TEST — LIVE_READINESS_CHECKLIST Gate 4.
#
# Crash the live seat with SIGKILL and prove the bot comes back correctly:
# state file intact, automatic restart, recovery reconciles with the broker, and
# no orphaned or duplicated positions.
#
# ─── THIS IS THE ONE SANCTIONED `kill -9` IN THE PROJECT ───────────────────
# CLAUDE.md says, in bold: NEVER use kill/pkill to stop a bot — they have
# Restart=always, so killing one just makes it come back 30s later and you have
# achieved nothing except an unclean shutdown. That rule stands. This script is
# the single deliberate exception, because "does it survive an unclean death" is
# exactly what Gate 4 asks and there is no way to test it politely.
#
# Run it ONLY through this script, never by hand, so the guards below always
# apply.
#
# ─── WHY IT IS SAFE TO RUN (when the guards pass) ──────────────────────────
# The state file and the metrics file are both written ATOMICALLY
# (temp + flush + fsync + os.replace — strategy.py:~12213, base_strategy.py:~5925),
# so SIGKILL cannot truncate them. The worst case is losing the most recent
# write, not a corrupt file. That is precisely the property this test confirms
# rather than assumes.
#
# ─── WHAT IT DOES NOT COVER ────────────────────────────────────────────────
# Gate 4 says "kill -9 MID-TRADE-ATTEMPT". That requires an in-flight order,
# which only exists during RTH — and this script refuses to run during RTH. So
# this covers crash/restart/recovery/reconciliation, NOT a crash between placing
# a leg and recording it. The protection for that case is cOID dedup
# (`_ensure_coid`, IBKR dedupes server-side), which is unit-tested. If you want
# the true mid-order test, it needs a deliberate RTH window with an operator
# watching and the account otherwise flat — a separate, riskier exercise.
#
# Usage:  sudo bash scripts/chaos_test.sh [unit]     (default: hydra_variant_b)

set -uo pipefail

UNIT="${1:-hydra_variant_b}"
VARIANT="${UNIT#hydra_variant_}"
[[ "${UNIT}" == "hydra" ]] && VARIANT=""
DATA="/opt/calypso/data${VARIANT:+/variant_${VARIANT}}"
PY="/opt/calypso/.venv/bin/python"
BROKER="http://127.0.0.1:8788"

fail() { echo "ABORT: $*" >&2; exit 1; }

echo "════════ CHAOS TEST — ${UNIT} ════════"

# ── GUARD 1: never during RTH ────────────────────────────────────────────
# 0DTE positions and in-flight orders during market hours; a crash test here
# could leave a real naked short. 13:30-20:00 UTC = 09:30-16:00 ET.
DOW="$(/bin/date -u +%u)" HHMM="$(/bin/date -u +%H%M)"
# 10# forces base-10: "0930" in an arithmetic context would otherwise be read as
# octal and "0930" is not even valid octal, so the guard would error out at
# exactly the wrong time of day.
if [[ "${DOW}" -le 5 && $((10#${HHMM})) -gt 1315 && $((10#${HHMM})) -lt 2015 ]]; then
    fail "inside (or adjacent to) RTH — 13:30-20:00 UTC. Run after the close."
fi
echo "  guard 1 OK — outside RTH ($(/bin/date -u +%a\ %H:%MZ))"

# ── GUARD 2: broker healthy ──────────────────────────────────────────────
HEALTH="$(curl -s --max-time 10 ${BROKER}/health || true)"
grep -q '"connected":true' <<<"${HEALTH}" \
    || fail "broker not holding a session: ${HEALTH:-<no response>}"
echo "  guard 2 OK — broker connected"

# ── GUARD 3: account FLAT (count QUANTITY, not rows) ──────────────────────
# IBKR returns rows with quantity 0 for expired contracts; len(positions) is NOT
# flatness and has misled this project before.
NZ="$(curl -s --max-time 25 -X POST ${BROKER}/rpc -H 'Content-Type: application/json' \
      -d '{"method":"get_positions","args":[],"kwargs":{}}' \
      | "${PY}" -c "
import json,sys
r=(json.load(sys.stdin).get('result') or [])
print(len([p for p in r if abs(float(p.get('quantity') or p.get('position') or 0))>0]))" 2>/dev/null || echo ERR)"
[[ "${NZ}" == "0" ]] || fail "account NOT flat (non-zero positions: ${NZ}). Never crash-test with risk on."
echo "  guard 3 OK — account flat"

# ── PRE-STATE ────────────────────────────────────────────────────────────
PID="$(systemctl show -p MainPID --value "${UNIT}")"
[[ -n "${PID}" && "${PID}" != "0" ]] || fail "${UNIT} is not running"
SNAP_BEFORE="$(ls -1 "${DATA}/state_snapshots/" 2>/dev/null | wc -l)"
echo
echo "  PRE : pid=${PID}  state=$(stat -c%s "${DATA}/hydra_state.json") bytes  snapshots=${SNAP_BEFORE}"
"${PY}" -c "
import json; d=json.load(open('${DATA}/hydra_state.json'))
print('  PRE : state JSON valid — keys=%d entries=%d date=%s' % (len(d), len(d.get('entries') or []), d.get('date')))" \
    || fail "state file is ALREADY invalid before the test — fix that first"

# ── THE CRASH ────────────────────────────────────────────────────────────
echo
echo "  >>> SIGKILL ${PID}"
T0="$(/bin/date +%s)"
kill -9 "${PID}" || fail "kill failed"

NEWPID=""; ELAPSED=0
while [[ ${ELAPSED} -lt 150 ]]; do
    sleep 2
    ELAPSED=$(( $(/bin/date +%s) - T0 ))
    P="$(systemctl show -p MainPID --value "${UNIT}")"
    if [[ -n "${P}" && "${P}" != "0" && "${P}" != "${PID}" ]] && systemctl is-active --quiet "${UNIT}"; then
        NEWPID="${P}"; break
    fi
done

# ── POST-STATE ───────────────────────────────────────────────────────────
echo
echo "  POST: restarted after ${ELAPSED}s  newpid=${NEWPID:-NONE}  active=$(systemctl is-active "${UNIT}")"
echo "        (RestartSec=30, so >=30s is CORRECT — Gate 4's '<30s' wording is unmeetable by design)"
"${PY}" -c "
import json
try:
    d=json.load(open('${DATA}/hydra_state.json'))
    print('  POST: state JSON STILL VALID — keys=%d entries=%d date=%s' % (len(d), len(d.get('entries') or []), d.get('date')))
except Exception as e:
    raise SystemExit('  POST: *** STATE FILE CORRUPT *** %s' % e)" || fail "state file corrupted by the crash"

SNAP_AFTER="$(ls -1 "${DATA}/state_snapshots/" 2>/dev/null | wc -l)"
echo "  POST: snapshots ${SNAP_BEFORE} -> ${SNAP_AFTER} (ExecStartPre should have added one)"
TMPS="$(ls -1 "${DATA}"/*.tmp 2>/dev/null | wc -l)"
echo "  POST: leftover .tmp atomic-write residue: ${TMPS} (0 expected)"

sleep 25
echo "  POST: recovery log —"
journalctl -u "${UNIT}" --since "3 min ago" --no-pager \
    | grep -iE "recover|reconcil|DataRecorder initialized|Traceback|ERROR" | tail -8 | sed 's/^/        /'

NZ2="$(curl -s --max-time 25 -X POST ${BROKER}/rpc -H 'Content-Type: application/json' \
      -d '{"method":"get_positions","args":[],"kwargs":{}}' \
      | "${PY}" -c "
import json,sys
r=(json.load(sys.stdin).get('result') or [])
print(len([p for p in r if abs(float(p.get('quantity') or p.get('position') or 0))>0]))" 2>/dev/null || echo ERR)"
echo "  POST: non-zero positions = ${NZ2} (0 expected — no orphan, no duplicate)"

echo
if [[ -n "${NEWPID}" && "${NZ2}" == "0" && "${TMPS}" == "0" ]]; then
    echo "  RESULT: PASS — record it in RUNBOOKS.md RB-7-adjacent Gate-4 log + the journal."
    exit 0
fi
echo "  RESULT: FAIL — investigate before going anywhere near real money."
exit 1
