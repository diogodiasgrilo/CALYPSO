#!/usr/bin/env python3
"""Entry-window watchdog.

Runs just after each HYDRA entry window and flags if the entry path misbehaved
on A/B/C or the shared broker. Checks:
  • calypso-broker /health says connected (the one IBKR session is alive),
  • calypso-broker + hydra + hydra_variant_b + hydra_variant_c are `active`,
  • none logged entry-path errors in the window (ERROR/Traceback/BrokerError/
    broker-unreachable/auth-eviction/persistent zero-price stop skips).

LOG SOURCE (important): the bots/broker do NOT log to journald — journalctl is
empty for these units. They write to FILES under /opt/calypso/logs, and the
exact path is messy (A → hydra/bot.log; B and C share bot_log.txt; logrotate
can rename a file out from under an open fd). So instead of guessing paths we
resolve each unit's *actually-open* log file(s) via /proc/<MainPID>/fd — that
auto-follows wherever each process truly writes, immune to the rotation mess.
Bot log lines are ET-stamped ("YYYY-MM-DD HH:MM:SS | LEVEL | ..."); we window
by parsing that prefix against now-in-ET. (Earlier versions grepped journalctl
and were silently blind — always "OK" because journald had nothing.)

On any problem it raises ONE Telegram alert via AlertService (→ the same
calypso-alerts → Cloud Function → Telegram path) and exits non-zero; otherwise
it logs OK and exits 0. Installed as a systemd timer — see
deploy/entry-window-watch.{service,timer}. Self-contained (no Claude session).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta

sys.path.insert(0, "/opt/calypso")

BROKER_HEALTH = "http://127.0.0.1:8788/health"
STRATEGIES = ["hydra", "hydra_variant_b", "hydra_variant_c"]
SERVICES = ["calypso-broker", *STRATEGIES]
WINDOW_MIN = 12
LOG_ROOT = "/opt/calypso/logs"
# The broker writes to a known RotatingFileHandler path (broker.log). Its
# /proc/<pid>/fd is resolvable from the watchdog (the broker runs under the same
# User=calypso with the same sandbox posture as the bots — no ProtectProc/
# ProcSubset hides /proc fd), so /proc-resolution normally finds it too. We add
# BROKER_LOG as a redundant supplement in case the broker's fd is momentarily
# unresolvable (restart/rotation). Bots rely on /proc-resolution because their
# paths are messy: shared file + logrotate renaming a file out from under an
# open fd.
BROKER_LOG = "/opt/calypso/logs/broker/broker.log"
TAIL_BYTES = 512 * 1024  # plenty to cover a 12-min window on any of our logs

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - zoneinfo always present on 3.9+
    _ET = None

# Entry-path trouble. Match the LOG-LEVEL field ("| ERROR |" / "| CRITICAL |",
# incl. the bots' padded "| ERROR    |") rather than the bare word — so normal
# INFO lines that happen to mention an auth endpoint (the broker logs
# "POST .../iserver/auth/ssodh/init" at INFO on every re-auth) do NOT false-
# trigger. Plus multiline-traceback bodies and explicit broker-comms phrases.
# DATA-004 (zero option prices, logged at WARNING in base_strategy) only matters
# if it PERSISTS — a one-off mid-warmup is benign — so it's counted separately
# and only alerts at >=5 occurrences. DATA-003 (impossible P&L) is a different
# condition logged at ERROR, so it matches ERR_RE first and alerts immediately
# as an error (it is NOT subject to the persistence threshold).
ERR_RE = re.compile(
    r"\|\s*(?:ERROR|CRITICAL)\s*\||Traceback|BrokerError|broker unreachable",
    re.IGNORECASE,
)
ZEROPRICE_RE = re.compile(r"DATA-004|call side prices are zero|zero prices", re.I)
TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def _active(unit: str) -> str:
    return subprocess.run(
        ["systemctl", "is-active", unit], capture_output=True, text=True
    ).stdout.strip()


def _main_pid(unit: str) -> int:
    out = subprocess.run(
        ["systemctl", "show", unit, "-p", "MainPID", "--value"],
        capture_output=True, text=True,
    ).stdout.strip()
    try:
        return int(out)
    except ValueError:
        return 0


def _open_log_files(pid: int) -> set:
    """Resolve a process's currently-open log files via /proc/<pid>/fd.

    Returns absolute paths under LOG_ROOT ending in .log/.txt (incl. rotated
    names like bot_log.txt.2026-05-30 that a process may still hold open).
    """
    found = set()
    if pid <= 0:
        return found
    fd_dir = f"/proc/{pid}/fd"
    try:
        entries = os.listdir(fd_dir)
    except OSError:
        return found
    for fd in entries:
        try:
            target = os.readlink(os.path.join(fd_dir, fd))
        except OSError:
            continue
        if not target.startswith(LOG_ROOT):
            continue
        base = os.path.basename(target)
        if ".log" in base or ".txt" in base:
            found.add(target)
    return found


def _cutoff() -> datetime:
    # Log lines are ET-stamped and parsed as naive ET in _scan_file, so the
    # cutoff MUST be ET too. If zoneinfo is unavailable we CANNOT fall back to
    # the host clock (the VM runs in UTC, ~4-5h ahead of ET) — a UTC cutoff
    # would push the whole real window into the "future" and silently exclude
    # every genuine ET log line, making the watchdog report a false OK. A safety
    # watchdog must fail CLOSED, so raise instead of comparing against a
    # mismatched clock; main() turns this into an alert + non-zero exit.
    if _ET is None:
        raise RuntimeError(
            "zoneinfo America/New_York unavailable — cannot align the scan "
            "window to ET log timestamps (refusing to scan against host UTC)"
        )
    now = datetime.now(_ET)
    return (now - timedelta(minutes=WINDOW_MIN)).replace(tzinfo=None)


def _scan_file(path: str, cutoff: datetime):
    """Return (error_lines, zeroprice_lines) within the time window for one file."""
    errs = []
    zeros = []
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > TAIL_BYTES:
                fh.seek(size - TAIL_BYTES)
                fh.readline()  # drop the partial first line
            data = fh.read().decode("utf-8", "replace")
    except OSError:
        return errs, zeros

    last_ts = None
    for line in data.splitlines():
        m = TS_RE.match(line)
        if m:
            try:
                last_ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                last_ts = None
        # In-window if this line (or the header of its multi-line block) is recent.
        if last_ts is None or last_ts < cutoff:
            continue
        if ERR_RE.search(line):
            errs.append(line)
        elif ZEROPRICE_RE.search(line):
            zeros.append(line)
    return errs, zeros


def main() -> int:
    problems = []

    # 1. broker session alive
    try:
        with urllib.request.urlopen(BROKER_HEALTH, timeout=8) as r:
            h = json.load(r)
        if not h.get("connected"):
            problems.append(f"calypso-broker /health not connected: {h}")
    except Exception as e:
        problems.append(f"calypso-broker /health unreachable: {type(e).__name__}: {e}")

    # 2. services active. Track each bot's resolved log files per-unit so we can
    #    catch a bot that is `active` but whose /proc/fd resolved nothing — that
    #    state means we'd scan ZERO of its lines and must NOT be masked by the
    #    broker fallback below (see the coverage guard in step 3).
    log_files = set()
    per_unit_files: dict[str, set] = {}
    for unit in SERVICES:
        st = _active(unit)
        if st != "active":
            problems.append(f"{unit} is {st!r} (expected active)")
            continue
        resolved = _open_log_files(_main_pid(unit))
        per_unit_files[unit] = resolved
        log_files |= resolved

    # The broker's /proc/fd is normally resolvable too (same User=calypso, same
    # sandbox posture as the bots), but add its known RotatingFileHandler path as
    # a redundant supplement in case its fd is momentarily unresolvable. This is
    # a SUPPLEMENT, not a substitute — it must not mask a bot whose own logs were
    # never opened (the coverage guard below checks bots explicitly).
    if os.path.exists(BROKER_LOG):
        log_files.add(BROKER_LOG)

    # Coverage guard: every bot that systemctl reports `active` must contribute at
    # least one resolved log file, or we'd silently scan none of its lines and
    # report a false "OK" — the exact blindness this watchdog exists to kill.
    for unit in STRATEGIES:
        if unit in per_unit_files and not per_unit_files[unit]:
            problems.append(
                f"{unit} active but no open log file resolved "
                f"(cannot scan its entry-path logs)"
            )

    # 3. entry-path errors in the window, scanning the union of all open log
    #    files (A/B/C + broker). B/C share a file and all carry bot_name=HYDRA,
    #    so errors are reported across the set rather than per-unit — for a
    #    watchdog "something in the entry path errored" is the signal that matters.
    if not log_files:
        problems.append("no open log files resolved for A/B/C/broker (all PIDs dead?)")
    else:
        cutoff = None
        try:
            cutoff = _cutoff()
        except RuntimeError as e:
            # Fail closed: a mismatched clock would make the scan silently blind,
            # so flag it as a problem rather than scanning against the wrong clock.
            problems.append(str(e))
        if cutoff is not None:
            all_errs = []
            all_zeros = []
            for path in sorted(log_files):
                e, z = _scan_file(path, cutoff)
                all_errs += e
                all_zeros += z
            if all_errs:
                problems.append(
                    f"{len(all_errs)} entry-path error line(s) in last {WINDOW_MIN}m; "
                    f"last: {all_errs[-1][-160:].strip()}"
                )
            elif len(all_zeros) >= 5:  # persistent zero-price = real (chain/quote) problem
                problems.append(
                    f"{len(all_zeros)} zero-price stop-skip lines in last {WINDOW_MIN}m "
                    f"(chain/quote problem?)"
                )

    if problems:
        summary = "Entry-window watchdog found issue(s):\n- " + "\n- ".join(problems[:8])
        try:
            from shared.alert_service import AlertService, AlertType, AlertPriority
            AlertService({"alerts": {"enabled": True}}, "HYDRA").send_alert(
                alert_type=AlertType.API_ERROR,
                title="Entry-window watchdog ⚠️",
                message=summary,
                priority=AlertPriority.HIGH,
            )
        except Exception as e:
            print(f"watchdog: failed to send alert: {e}", file=sys.stderr)
        print(summary)
        return 1

    scanned = len(log_files)
    print(f"entry-window watchdog OK — broker connected, A/B/C active, "
          f"no entry-path errors in last {WINDOW_MIN}m ({scanned} log file(s) scanned)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
