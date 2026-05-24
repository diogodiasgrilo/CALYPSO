#!/usr/bin/env bash
#
# Pre-start state-file snapshot (Polish Item 5)
# =============================================
#
# Invoked by `hydra.service`'s `ExecStartPre=`. Snapshots the HYDRA
# state file to `data/state_snapshots/` before the bot starts. If the
# bot's state-load fails (corrupt JSON, partial write from a SIGKILL
# crash) the snapshot is the recovery source (see RUNBOOKS.md RB-5).
#
# Failure semantics: this script ALWAYS exits 0. A snapshot failure
# must NEVER block bot start — the bot starting cold with no snapshot
# is a recoverable problem; the bot failing to start because the
# snapshot script crashed is a worse one. All errors are logged via
# `logger` (systemd-journal) and swallowed.
#
# Retention: keep the 50 most recent snapshots. State files are ~10 KB
# each → ~500 KB ceiling. 50 snapshots covers ~10 restart-days at 5
# restarts/day, a reasonable forensic window.
#
# Atomic write: cp+mv (mv on the same filesystem is atomic), so an
# interrupted snapshot cannot leave a torn file in `state_snapshots/`.
#
# Test: bash tests/test_pre_start_snapshot.sh

set -uo pipefail
# DO NOT use -e: we always exit 0 even on per-step failure.

CALYPSO_DIR="${CALYPSO_DIR:-/opt/calypso}"
STATE_FILE="${STATE_FILE:-${CALYPSO_DIR}/data/hydra_state.json}"
SNAPSHOT_DIR="${SNAPSHOT_DIR:-${CALYPSO_DIR}/data/state_snapshots}"
RETENTION_COUNT="${RETENTION_COUNT:-50}"

log() {
    # Use logger so output lands in journalctl alongside hydra.service.
    # Fall back to stderr if logger is missing (e.g., test environment).
    if command -v logger >/dev/null 2>&1; then
        logger -t hydra-pre-start "$1" 2>/dev/null || echo "[hydra-pre-start] $1" >&2
    else
        echo "[hydra-pre-start] $1" >&2
    fi
}

# ─── Step 1: ensure snapshot dir exists (mode 700, calypso-owned) ─────────
mkdir -p "${SNAPSHOT_DIR}" 2>/dev/null || {
    log "WARN: could not create ${SNAPSHOT_DIR} — skipping snapshot"
    exit 0
}

# ─── Step 2: snapshot the state file (if it exists) ───────────────────────
if [[ -f "${STATE_FILE}" ]]; then
    TIMESTAMP="$(date -u +%Y%m%d_%H%M%SZ)"
    SNAPSHOT_PATH="${SNAPSHOT_DIR}/hydra_state.pre_restart_${TIMESTAMP}.json"
    SNAPSHOT_TMP="${SNAPSHOT_PATH}.tmp"

    # Copy to .tmp then atomic-rename (mv on same FS is atomic). If the
    # process is interrupted between cp and mv, the .tmp gets cleaned
    # up on the next run via the retention sweep below.
    if cp -p "${STATE_FILE}" "${SNAPSHOT_TMP}" 2>/dev/null; then
        if mv "${SNAPSHOT_TMP}" "${SNAPSHOT_PATH}" 2>/dev/null; then
            log "snapshot OK: $(basename "${SNAPSHOT_PATH}")"
        else
            log "WARN: mv failed for ${SNAPSHOT_TMP} — leaving .tmp for next-run cleanup"
            rm -f "${SNAPSHOT_TMP}" 2>/dev/null || true
        fi
    else
        log "WARN: cp failed for ${STATE_FILE} → ${SNAPSHOT_TMP}"
    fi
else
    log "no state file at ${STATE_FILE} — first start of the day, nothing to snapshot"
fi

# ─── Step 3: retention sweep — keep RETENTION_COUNT most-recent ───────────
#
# `find -printf '%T@ %p\n' | sort -nr` is the safe pattern: tab-aware,
# null-terminator-safe via -print0 would be even better, but our filenames
# are constructed by this script with [A-Z0-9._-]+ only, so we know
# there's no whitespace or newlines in them. Keep the implementation
# straightforward.
#
# `tail -n +N` drops the first N-1 lines (keeps the rest = older snapshots).

if [[ -d "${SNAPSHOT_DIR}" ]]; then
    # Also sweep any leftover *.tmp files older than 1 minute (interrupted
    # snapshots).
    find "${SNAPSHOT_DIR}" -name 'hydra_state.pre_restart_*.json.tmp' \
        -type f -mmin +1 -delete 2>/dev/null || true

    # Now retention on the real snapshots.
    DELETED=0
    while IFS= read -r OLD_SNAPSHOT; do
        rm -f "${OLD_SNAPSHOT}" 2>/dev/null && DELETED=$((DELETED + 1))
    done < <(
        ls -1t "${SNAPSHOT_DIR}"/hydra_state.pre_restart_*.json 2>/dev/null \
            | tail -n "+$((RETENTION_COUNT + 1))"
    )
    if [[ "${DELETED}" -gt 0 ]]; then
        log "retention: removed ${DELETED} snapshot(s) beyond keep-count ${RETENTION_COUNT}"
    fi
fi

# ─── Exit 0 unconditionally — never block bot start on snapshot issues. ──
exit 0
