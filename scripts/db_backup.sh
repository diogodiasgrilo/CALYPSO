#!/usr/bin/env bash
#
# CALYPSO daily backup to GCS. Invoked by db_backup.service (23:00 UTC timer).
#
# Why a script file (not inline ExecStart bash): the inline unit computed the
# date with `$(date +%%Y%%m%%d)`, but in the systemd exec context that expanded
# to an EMPTY string, so every backup landed on a DATELESS object
# (gs://.../hydra_state_.json) — each day silently overwrote the last and NO
# dated history existed (a real data-loss-on-disk-failure risk; also the source
# of ARGUS Check 8's perpetual "GCS backup missing: hydra_state_<date>.json"
# warning). systemd does NOT specifier-expand the CONTENTS of a script file, so
# computing the date here in plain bash (with an absolute `date` path) is robust.
#
# backtesting.db is the GATING backup (its failure fails the unit, exit 1);
# the metrics + state + per-variant state files are best-effort (logged, never
# fatal — the canonical DB above is the gating step).

set -uo pipefail

BUCKET="gs://calypso-backups"
DATA="/opt/calypso/data"
DATE="$(/bin/date -u +%Y%m%d)"
GSUTIL="/usr/bin/gsutil"

if [[ -z "${DATE}" ]]; then
    echo "db_backup: FATAL — could not compute backup date" >&2
    exit 1
fi

# ── Gating: backtesting.db ───────────────────────────────────────────────
# I-M6: the DB runs in WAL mode, so a plain `gsutil cp` of the main file
# captures a consistent-but-STALE snapshot missing rows still in the -wal
# sidecar (the recent day's data). Use sqlite3's online .backup (via the venv
# python — always present) to a temp file that folds in the WAL, then upload
# THAT. Falls back to a direct cp so a backup still lands if .backup fails.
VENV_PY="/opt/calypso/.venv/bin/python"
TMP_DB="/tmp/backtesting_backup_${DATE}.db"
if [[ -x "${VENV_PY}" ]] && "${VENV_PY}" - "${DATA}/backtesting.db" "${TMP_DB}" <<'PY' 2>/dev/null
import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
dst.close(); src.close()
PY
then
    if "${GSUTIL}" cp "${TMP_DB}" "${BUCKET}/backtesting_${DATE}.db"; then
        rm -f "${TMP_DB}"
        echo "Backup complete: backtesting_${DATE}.db (WAL-consistent .backup)"
    else
        rm -f "${TMP_DB}"
        echo "Backup FAILED: gsutil cp of .backup snapshot" >&2
        exit 1
    fi
else
    rm -f "${TMP_DB}" 2>/dev/null || true
    echo "db_backup: .backup unavailable/failed — falling back to direct cp" >&2
    if "${GSUTIL}" cp "${DATA}/backtesting.db" "${BUCKET}/backtesting_${DATE}.db"; then
        echo "Backup complete: backtesting_${DATE}.db (direct cp fallback)"
    else
        echo "Backup FAILED: backtesting.db" >&2
        exit 1
    fi
fi

# ── Best-effort: metrics + canonical state + per-variant state ───────────
"${GSUTIL}" cp "${DATA}/hydra_metrics.json" \
    "${BUCKET}/hydra_metrics_${DATE}.json" \
    || echo "Backup FAILED: hydra_metrics.json" >&2

"${GSUTIL}" cp "${DATA}/hydra_state.json" \
    "${BUCKET}/hydra_state_${DATE}.json" \
    || echo "Backup FAILED: hydra_state.json" >&2

for f in "${DATA}"/variant_*/hydra_state.json; do
    [[ -e "${f}" ]] || continue
    v="$(basename "$(dirname "${f}")")"
    "${GSUTIL}" cp "${f}" "${BUCKET}/${v}_hydra_state_${DATE}.json" \
        || echo "Backup FAILED: ${v}/hydra_state.json" >&2
done

echo "db_backup: complete for ${DATE}"
exit 0
