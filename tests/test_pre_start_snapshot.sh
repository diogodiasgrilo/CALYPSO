#!/usr/bin/env bash
#
# Tests for scripts/pre_start_snapshot.sh (Polish Item 5)
# ========================================================
#
# Run with: bash tests/test_pre_start_snapshot.sh
# Exits 0 on success, non-zero on any test failure.
#
# Test cases:
#   T1: no state file → script exits 0, no snapshot created (first-run case)
#   T2: state file present → snapshot created with correct name pattern
#   T3: snapshot is byte-identical to source (preserves JSON shape)
#   T4: snapshot has the atomic .tmp → final-name pattern (no .tmp leftover)
#   T5: retention sweep — beyond N snapshots, oldest are deleted
#   T6: leftover .tmp file older than 1 minute is cleaned up
#   T7: script exits 0 even if snapshot dir can't be created (defensive)

set -u  # NOT -e — we want to count failures explicitly

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="${REPO_ROOT}/scripts/pre_start_snapshot.sh"

PASS=0
FAIL=0

assert() {
    local cond="$1"
    local msg="$2"
    if eval "${cond}"; then
        echo "  ✓ ${msg}"
        PASS=$((PASS + 1))
    else
        echo "  ✗ ${msg}"
        FAIL=$((FAIL + 1))
    fi
}

echo "Test: pre_start_snapshot.sh"
echo "============================"

# Use a per-test temp dir so tests are isolated and don't touch /opt/calypso.
TMPDIR=$(mktemp -d)
trap 'rm -rf "${TMPDIR}"' EXIT

run_script() {
    # All env vars set; script runs with our tmpdir not /opt/calypso.
    CALYPSO_DIR="${TMPDIR}" \
        STATE_FILE="${TMPDIR}/data/hydra_state.json" \
        SNAPSHOT_DIR="${TMPDIR}/data/state_snapshots" \
        RETENTION_COUNT="${1:-50}" \
        bash "${SCRIPT}"
}

# ─── T1: no state file ────────────────────────────────────────────────────
echo ""
echo "T1: no state file"
rm -rf "${TMPDIR}/data"
mkdir -p "${TMPDIR}/data"
run_script
assert "[[ $? -eq 0 ]]" "exit code 0"
assert "[[ ! -d '${TMPDIR}/data/state_snapshots' ]] || [[ -z \"\$(ls -1 '${TMPDIR}/data/state_snapshots' 2>/dev/null)\" ]]" \
    "no snapshot created"

# ─── T2: state file present → snapshot created ────────────────────────────
echo ""
echo "T2: state file present → snapshot created"
rm -rf "${TMPDIR}/data"
mkdir -p "${TMPDIR}/data"
echo '{"foo": "bar"}' > "${TMPDIR}/data/hydra_state.json"
run_script
assert "[[ -d '${TMPDIR}/data/state_snapshots' ]]" "snapshot dir created"
SNAPSHOTS=( "${TMPDIR}/data/state_snapshots"/hydra_state.pre_restart_*.json )
assert "[[ -f '${SNAPSHOTS[0]}' ]]" "snapshot file exists matching name pattern"

# ─── T3: snapshot byte-identical to source ────────────────────────────────
echo ""
echo "T3: snapshot is byte-identical"
SOURCE_HASH=$(shasum -a 256 "${TMPDIR}/data/hydra_state.json" | awk '{print $1}')
SNAPSHOT_HASH=$(shasum -a 256 "${SNAPSHOTS[0]}" | awk '{print $1}')
assert "[[ '${SOURCE_HASH}' == '${SNAPSHOT_HASH}' ]]" "shasum matches (preserves JSON)"

# ─── T4: no .tmp leftover ─────────────────────────────────────────────────
echo ""
echo "T4: no .tmp leftover after successful snapshot"
TMP_COUNT=$(ls -1 "${TMPDIR}/data/state_snapshots"/*.tmp 2>/dev/null | wc -l | tr -d ' ')
assert "[[ '${TMP_COUNT}' -eq 0 ]]" "no .tmp leftover"

# ─── T5: retention — beyond N snapshots, oldest deleted ───────────────────
echo ""
echo "T5: retention sweep keeps N most recent"
rm -rf "${TMPDIR}/data"
mkdir -p "${TMPDIR}/data/state_snapshots"
echo '{"x": 1}' > "${TMPDIR}/data/hydra_state.json"

# Pre-populate 5 fake old snapshots with backdated mtimes (-A flag on touch)
for i in 1 2 3 4 5; do
    P="${TMPDIR}/data/state_snapshots/hydra_state.pre_restart_2000010${i}_000000Z.json"
    echo "{\"old\": ${i}}" > "${P}"
    # Backdate mtime so the new snapshot sorts as newest.
    touch -t "20240101000${i}" "${P}" 2>/dev/null || true
done

# Run with RETENTION_COUNT=3 → after run there are 3 snapshots total
# (the new one + the 2 most-recent old ones; 3 oldest deleted).
run_script 3
COUNT=$(ls -1 "${TMPDIR}/data/state_snapshots"/hydra_state.pre_restart_*.json 2>/dev/null | wc -l | tr -d ' ')
assert "[[ '${COUNT}' -eq 3 ]]" "exactly 3 snapshots remain (got ${COUNT})"

# Newest survives
NEWEST=$(ls -1t "${TMPDIR}/data/state_snapshots"/hydra_state.pre_restart_*.json 2>/dev/null | head -1)
assert "[[ '${NEWEST}' != *2000010* ]]" "newest is the fresh one, not pre-populated"

# ─── T6: leftover .tmp older than 1 minute is cleaned up ──────────────────
echo ""
echo "T6: leftover .tmp older than 1 min is cleaned up"
rm -rf "${TMPDIR}/data"
mkdir -p "${TMPDIR}/data/state_snapshots"
echo '{"x": 1}' > "${TMPDIR}/data/hydra_state.json"
# Plant a fake .tmp file older than 1 minute
STALE_TMP="${TMPDIR}/data/state_snapshots/hydra_state.pre_restart_20240101_000000Z.json.tmp"
echo "stale" > "${STALE_TMP}"
touch -t "200001010000" "${STALE_TMP}" 2>/dev/null || true  # very old mtime

run_script
assert "[[ ! -f '${STALE_TMP}' ]]" "stale .tmp file was removed"

# ─── T7: script exits 0 even when snapshot dir creation fails ─────────────
echo ""
echo "T7: defensive — exit 0 even on dir-creation failure"
# Point SNAPSHOT_DIR at a path under a non-existent + non-creatable parent
# (simulate a permission issue by using a path under /sys which is RO).
CALYPSO_DIR="${TMPDIR}" \
    STATE_FILE="${TMPDIR}/data/hydra_state.json" \
    SNAPSHOT_DIR="/sys/nonexistent/state_snapshots" \
    RETENTION_COUNT=50 \
    bash "${SCRIPT}"
RC=$?
assert "[[ '${RC}' -eq 0 ]]" "exit code 0 (got ${RC}) even when SNAPSHOT_DIR can't be created"

# ─── Summary ──────────────────────────────────────────────────────────────
echo ""
echo "============================"
echo "PASS: ${PASS}"
echo "FAIL: ${FAIL}"
if [[ "${FAIL}" -gt 0 ]]; then
    exit 1
fi
exit 0
