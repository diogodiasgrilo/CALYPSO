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
# ─── 2026-09-12: THE LIVE SEAT WAS NOT BEING BACKED UP AT ALL ──────────────
# Found by the first RB-7 restore rehearsal (Gate 7). This script used to copy
# `data/backtesting.db` + `data/hydra_metrics.json` — which are **variant A's**,
# a dry-run shadow — plus every variant's *state* file. It never copied any
# variant's `backtesting.db` or `hydra_metrics.json`. So variant B, the LIVE
# PAPER SEAT, had its entire trading record (trade_entries, trade_stops,
# spread_snapshots, daily_summaries) and its lifetime P&L sitting on a single
# VM disk with no off-box copy, while the gating backup protected the shadow.
#
# Now: every DB and every metrics file is copied, seat-agnostic, so a future
# live-seat swap cannot silently move the important data outside the backup set.
# Cost of the fix: ~89 MB/day on top of the existing ~120 MB/day.
#
# GATING (failure => exit 1, unit fails, ARGUS notices):
#   every *backtesting.db* — the canonical trading records.
# BEST-EFFORT (logged, never fatal):
#   metrics, state files, and D/E's dc_calendar.db sidecars.
#
# WAL SAFETY: these DBs run in WAL mode, so a plain `gsutil cp` of the main file
# captures a consistent-but-STALE snapshot missing rows still in the -wal
# sidecar (i.e. the most recent day — exactly what you want back). Every DB goes
# through sqlite3's online `.backup`, which folds the WAL in, with a direct cp
# fallback so a degraded backup still lands rather than none at all.

set -uo pipefail

BUCKET="gs://calypso-backups"
DATA="/opt/calypso/data"
DATE="$(/bin/date -u +%Y%m%d)"
GSUTIL="/usr/bin/gsutil"
VENV_PY="/opt/calypso/.venv/bin/python"

if [[ -z "${DATE}" ]]; then
    echo "db_backup: FATAL — could not compute backup date" >&2
    exit 1
fi

FAILED_GATING=0

# backup_db <source.db> <object-basename> <gating|besteffort>
#
# WAL-consistent copy via sqlite3 .backup, uploaded under the dated object name.
# Falls back to a direct cp if .backup is unavailable or fails.
backup_db() {
    local src="$1" name="$2" mode="$3"
    local tmp="/tmp/cal_bk_${name}_${DATE}.db"

    if [[ ! -f "${src}" ]]; then
        echo "db_backup: skip ${name} — ${src} not present"
        return 0
    fi

    if [[ -x "${VENV_PY}" ]] && "${VENV_PY}" - "${src}" "${tmp}" <<'PY' 2>/dev/null
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
dst.close(); src.close()
PY
    then
        if "${GSUTIL}" cp "${tmp}" "${BUCKET}/${name}_${DATE}.db"; then
            rm -f "${tmp}"
            echo "Backup complete: ${name}_${DATE}.db (WAL-consistent .backup)"
            return 0
        fi
        rm -f "${tmp}"
        echo "Backup FAILED: gsutil cp of ${name} .backup snapshot" >&2
    else
        rm -f "${tmp}" 2>/dev/null || true
        echo "db_backup: .backup unavailable/failed for ${name} — falling back to direct cp" >&2
        if "${GSUTIL}" cp "${src}" "${BUCKET}/${name}_${DATE}.db"; then
            echo "Backup complete: ${name}_${DATE}.db (direct cp fallback)"
            return 0
        fi
        echo "Backup FAILED: ${name} (direct cp fallback)" >&2
    fi

    [[ "${mode}" == "gating" ]] && FAILED_GATING=1
    return 1
}

# backup_file <source> <object-name>   — best-effort plain copy
backup_file() {
    local src="$1" name="$2"
    [[ -f "${src}" ]] || return 0
    "${GSUTIL}" cp "${src}" "${BUCKET}/${name}" \
        || echo "Backup FAILED: ${name}" >&2
}

# ── Canonical (variant A) ────────────────────────────────────────────────
backup_db  "${DATA}/backtesting.db"    "backtesting"           gating
backup_file "${DATA}/hydra_metrics.json" "hydra_metrics_${DATE}.json"
backup_file "${DATA}/hydra_state.json"   "hydra_state_${DATE}.json"

# ── Every variant: DB (gating) + metrics + state + calendar sidecar ──────
# `variant_*` also matches archived directories like
# `variant_c.pre-v1.27.bak.20260505_110408` — those are themselves old backups,
# so skip anything with a dot in the directory name.
for d in "${DATA}"/variant_*/; do
    [[ -d "${d}" ]] || continue
    v="$(basename "${d}")"
    case "${v}" in
        *.*) echo "db_backup: skip archived dir ${v}"; continue ;;
    esac

    backup_db  "${d}backtesting.db"   "${v}_backtesting"  gating
    backup_db  "${d}dc_calendar.db"   "${v}_dc_calendar"  besteffort
    backup_file "${d}hydra_metrics.json" "${v}_hydra_metrics_${DATE}.json"
    backup_file "${d}hydra_state.json"   "${v}_hydra_state_${DATE}.json"
done

if [[ "${FAILED_GATING}" -ne 0 ]]; then
    echo "db_backup: FATAL — one or more gating database backups failed" >&2
    exit 1
fi

echo "db_backup: complete for ${DATE}"
exit 0
