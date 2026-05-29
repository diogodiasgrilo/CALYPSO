#!/usr/bin/env python3
"""Entry-window watchdog.

Runs just after each HYDRA entry window and flags if the entry path misbehaved
on A/B/C or the shared broker. Checks:
  • calypso-broker /health says connected (the one IBKR session is alive),
  • calypso-broker + hydra + hydra_variant_b + hydra_variant_c are `active`,
  • none logged entry-path errors in the window (ERROR/Traceback/BrokerError/
    broker-unreachable/auth-eviction/persistent zero-price stop skips).

On any problem it raises ONE Telegram alert via AlertService (→ the same
calypso-alerts → Cloud Function → Telegram path) and exits non-zero; otherwise
it logs OK and exits 0. Installed as a systemd timer — see
deploy/entry-window-watch.{service,timer}. Self-contained (no Claude session).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.request

sys.path.insert(0, "/opt/calypso")

BROKER_HEALTH = "http://127.0.0.1:8788/health"
STRATEGIES = ["hydra", "hydra_variant_b", "hydra_variant_c"]
SERVICES = ["calypso-broker", *STRATEGIES]
WINDOW = "12 min ago"

# Entry-path trouble. DATA-003/004 (zero option prices) only matters if it
# PERSISTS — a one-off mid-warmup is benign — so it's counted separately.
ERR_RE = re.compile(
    r"\bERROR\b|\bCRITICAL\b|Traceback|BrokerError|broker unreachable|"
    r"Failed to connect|410 Gone|Invalid_username|ssodh/init",
    re.IGNORECASE,
)
ZEROPRICE_RE = re.compile(r"DATA-00[34]|call side prices are zero|zero prices", re.I)


def _active(unit: str) -> str:
    return subprocess.run(
        ["systemctl", "is-active", unit], capture_output=True, text=True
    ).stdout.strip()


def _journal(unit: str) -> str:
    return subprocess.run(
        ["journalctl", "-u", unit, "--since", WINDOW, "--no-pager"],
        capture_output=True, text=True,
    ).stdout


def main() -> int:
    problems: list[str] = []

    # 1. broker session alive
    try:
        with urllib.request.urlopen(BROKER_HEALTH, timeout=8) as r:
            h = json.load(r)
        if not h.get("connected"):
            problems.append(f"calypso-broker /health not connected: {h}")
    except Exception as e:
        problems.append(f"calypso-broker /health unreachable: {type(e).__name__}: {e}")

    # 2. services active + entry-path errors in the window
    for unit in SERVICES:
        st = _active(unit)
        if st != "active":
            problems.append(f"{unit} is {st!r} (expected active)")
            continue
        if unit == "calypso-broker":
            continue
        log = _journal(unit)
        errs = [ln for ln in log.splitlines() if ERR_RE.search(ln)]
        zeros = [ln for ln in log.splitlines() if ZEROPRICE_RE.search(ln)]
        if errs:
            problems.append(f"{unit}: {len(errs)} entry-path error line(s); last: {errs[-1][-160:].strip()}")
        elif len(zeros) >= 5:  # persistent zero-price = real (chain/quote) problem
            problems.append(f"{unit}: {len(zeros)} zero-price stop-skip lines (chain/quote problem?)")

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

    print("entry-window watchdog OK — broker connected, A/B/C active, no entry-path errors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
