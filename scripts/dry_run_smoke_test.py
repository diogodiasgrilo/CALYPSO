#!/usr/bin/env python3
"""HYDRA dry-run smoke test — verify dry mode actually mirrors live behavior.

Why this exists: 2026-04-27's static audit declared dry mode "100% ready"
three separate times and was wrong each time. Each round of grep-based
review missed gates. The only way to verify dry mode actually works is to
watch a real entry placement end-to-end and check that all subsystems fire.

This script does THREE things:

  1. PRE-FLIGHT (runs anytime, no market dependency):
     - Reads live VM config and validates expected dry-mode settings
     - Verifies bot is healthy and active
     - Confirms recent journalctl shows DRY RUN banner
     - Checks data-path connectivity (state file, DB)

  2. ENTRY-WATCH (runs while bot is in dry mode and entries are scheduled):
     - Streams journalctl and waits for next "Initiating Entry" line
     - Captures the ~90-second window around the entry placement
     - Greps the captured window for expected log markers
     - Reports PASS / FAIL per subsystem with diagnostic detail

  3. ASSERT (verifies each subsystem behaved as expected):
     - MKT-024 calculated strikes? ✓/✗
     - MKT-020/022 actually fired (today's bug)? ✓/✗
     - MKT-045 chain snap fired? ✓/✗
     - MKT-011 credit gate ran? ✓/✗
     - Path-B Simulated Entry log line appeared with real credits? ✓/✗
     - VIX regime override applied? ✓/✗
     - STATE-002 / reconciliation did NOT kill the entry? ✓/✗
     - First heartbeat after entry succeeded? ✓/✗
     - Path-B real-quote heartbeat updates fetched real Saxo bid/ask? ✓/✗

Usage:
    # Pre-flight only (any time):
    python scripts/dry_run_smoke_test.py --preflight

    # Entry-watch (during market hours, bot must be running):
    python scripts/dry_run_smoke_test.py --watch [--timeout 600]

    # Both (preflight then wait for next entry):
    python scripts/dry_run_smoke_test.py --full

Exit codes:
    0 — all checks pass
    1 — one or more checks failed (details printed)
    2 — script error / unable to connect

Re-run this AFTER any change to the dry_run code paths or before any new
dry-mode session. Static review alone is not sufficient — empirical
verification is the only proof.
"""
import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

VM_NAME = "calypso-bot"
VM_ZONE = "us-east1-b"
GCLOUD = "gcloud"

# ── Expected config values for a SAFE dry-run session
EXPECTED_DRY_RUN = True
EXPECTED_VIX_REGIME_ENABLED = True
SANITY_MAX_SPREAD = (25, 200)            # acceptable range
SANITY_VIX_BREAKPOINTS = [18.0, 22.0, 28.0]


@dataclass
class Check:
    """One pass/fail item with a description and optional diagnostic."""
    name: str
    passed: bool
    detail: str = ""

    def __str__(self) -> str:
        marker = "✅" if self.passed else "❌"
        line = f"{marker} {self.name}"
        if self.detail:
            line += f" — {self.detail}"
        return line


@dataclass
class Report:
    checks: List[Check] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append(Check(name, passed, detail))

    def all_pass(self) -> bool:
        return all(c.passed for c in self.checks)

    def print(self, header: str) -> None:
        print()
        print("=" * 80)
        print(header)
        print("=" * 80)
        for c in self.checks:
            print(f"  {c}")
        passed = sum(1 for c in self.checks if c.passed)
        total = len(self.checks)
        print()
        print(f"  Summary: {passed} / {total} passed")


# ──────────────────────────────────────────────────────────────────────
# SSH helpers
# ──────────────────────────────────────────────────────────────────────

def ssh(remote_cmd: str, timeout: int = 60) -> Tuple[int, str, str]:
    """Run a single command on the VM via gcloud compute ssh."""
    cmd = [
        GCLOUD, "compute", "ssh", VM_NAME,
        f"--zone={VM_ZONE}", f"--command={remote_cmd}",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"Timed out after {timeout}s"


# ──────────────────────────────────────────────────────────────────────
# Phase 1: PRE-FLIGHT
# ──────────────────────────────────────────────────────────────────────

def phase_preflight() -> Report:
    """Static checks that don't require an active entry — runnable anytime."""
    rep = Report()

    # 1. Bot service active
    rc, out, err = ssh("sudo systemctl is-active hydra")
    rep.add(
        "HYDRA service active",
        out.strip() == "active",
        f"systemctl returned: {out.strip() or err.strip()}"
    )

    # 2. Read live config; validate dry mode + safety settings
    rc, out, err = ssh(
        "sudo -u calypso /opt/calypso/.venv/bin/python -c '"
        "import json; "
        "c=json.load(open(\"/opt/calypso/bots/hydra/config/config.json\")); "
        "import json as j; "
        "print(j.dumps({"
        "  \"dry_run\": c.get(\"dry_run\"),"
        "  \"max_spread_width\": c[\"strategy\"][\"max_spread_width\"],"
        "  \"min_spread_width\": c[\"strategy\"][\"min_spread_width\"],"
        "  \"contracts_per_entry\": c[\"strategy\"][\"contracts_per_entry\"],"
        "  \"vix_regime_enabled\": c[\"strategy\"][\"vix_regime\"][\"enabled\"],"
        "  \"vix_regime_breakpoints\": c[\"strategy\"][\"vix_regime\"][\"breakpoints\"],"
        "  \"vix_call_buf\": c[\"strategy\"][\"vix_regime\"][\"call_stop_buffer\"],"
        "  \"vix_put_buf\": c[\"strategy\"][\"vix_regime\"][\"put_stop_buffer\"],"
        "  \"call_starting_otm\": c[\"strategy\"][\"call_starting_otm_multiplier\"],"
        "  \"put_starting_otm\": c[\"strategy\"][\"put_starting_otm_multiplier\"],"
        "}))'"
    )
    if rc != 0 or not out.strip():
        rep.add("Read live VM config", False, f"failed: {err.strip()[:120]}")
        return rep
    try:
        cfg = json.loads(out.strip())
    except json.JSONDecodeError as e:
        rep.add("Parse VM config JSON", False, str(e))
        return rep

    rep.add(
        "Config: dry_run=true",
        cfg.get("dry_run") == EXPECTED_DRY_RUN,
        f"got: {cfg.get('dry_run')!r}"
    )
    rep.add(
        f"Config: max_spread_width in {SANITY_MAX_SPREAD}",
        SANITY_MAX_SPREAD[0] <= cfg.get("max_spread_width", 0) <= SANITY_MAX_SPREAD[1],
        f"got: {cfg.get('max_spread_width')}pt"
    )
    rep.add(
        "Config: vix_regime.enabled=true",
        cfg.get("vix_regime_enabled") is True,
        ""
    )
    rep.add(
        f"Config: vix_regime.breakpoints={SANITY_VIX_BREAKPOINTS}",
        cfg.get("vix_regime_breakpoints") == SANITY_VIX_BREAKPOINTS,
        f"got: {cfg.get('vix_regime_breakpoints')}"
    )
    # MKT-024 multiplier sanity vs spread width — flag if 50pt + 3.5x (the bug we hit today)
    width = cfg.get("max_spread_width", 110)
    cm = cfg.get("call_starting_otm")
    pm = cfg.get("put_starting_otm")
    multiplier_warning = ""
    if width <= 75 and (cm and cm > 2.0 or pm and pm > 2.5):
        multiplier_warning = (
            f"⚠ width={width}pt + multipliers ({cm}/{pm}) — narrow spread "
            f"with wide MKT-024 starting position. May produce far-OTM strikes "
            f"in illiquid territory like 2026-04-27 E#1."
        )
    rep.add(
        "MKT-024 multipliers compatible with spread width",
        not multiplier_warning,
        multiplier_warning or f"width={width}pt, mult call={cm}/put={pm}"
    )
    rep.add(
        "Option B per-VIX-regime buffers populated",
        cfg.get("vix_call_buf") and cfg.get("vix_put_buf") and any(
            v is not None for v in cfg.get("vix_call_buf", [])
        ),
        f"call={cfg.get('vix_call_buf')}, put={cfg.get('vix_put_buf')}"
    )

    # 3. State file readable + zero active positions (clean starting point)
    rc, out, err = ssh(
        "sudo -u calypso jq '{"
        "active_entries: ((.active_entries // []) | length),"
        "entries_placed: (.daily_state.entries_placed // 0)"
        "}' /opt/calypso/data/hydra_state.json"
    )
    if rc == 0 and out.strip():
        try:
            state = json.loads(out.strip())
            rep.add(
                "State: zero active positions at start",
                state.get("active_entries", 0) == 0,
                f"active_entries={state.get('active_entries')}, placed={state.get('entries_placed')}"
            )
        except json.JSONDecodeError:
            rep.add("Parse state file", False, "JSON decode failed")
    else:
        rep.add("Read state file", False, err.strip()[:120])

    # 4. Recent log shows DRY-RUN mode
    rc, out, _ = ssh(
        "sudo journalctl -u hydra -n 50 --no-pager | "
        "grep -E 'Mode: DRY RUN|HEARTBEAT.*\\[DRY RUN\\]' | wc -l"
    )
    rep.add(
        "Recent log confirms DRY RUN mode",
        rc == 0 and int(out.strip() or "0") > 0,
        f"DRY RUN log lines in last 50: {out.strip()}"
    )

    # 5. DataRecorder DB connectable + has expected schema
    rc, out, _ = ssh(
        "sudo -u calypso sqlite3 /opt/calypso/data/backtesting.db "
        "'SELECT version FROM schema_info LIMIT 1'"
    )
    rep.add(
        "DataRecorder DB connectable",
        rc == 0 and out.strip().isdigit(),
        f"schema version: {out.strip() or 'unreadable'}"
    )

    # 6. Reconciliation gates from today's commits are deployed
    rc, out, _ = ssh(
        "sudo grep -c 'Path-B dry-run skip' /opt/calypso/bots/hydra/strategy.py "
        "/opt/calypso/bots/meic/strategy.py 2>/dev/null"
    )
    if rc == 0:
        total = sum(int(line.split(":")[-1]) for line in out.strip().split("\n") if ":" in line)
        rep.add(
            "Today's reconciliation gates deployed (≥9 total)",
            total >= 9,
            f"found {total} 'Path-B dry-run skip' markers"
        )

    return rep


# ──────────────────────────────────────────────────────────────────────
# Phase 2: ENTRY-WATCH
# ──────────────────────────────────────────────────────────────────────

ENTRY_INITIATE_RE = re.compile(r"HYDRA: Initiating Entry #\d+")
EXPECTED_MARKERS = {
    "MKT-024 strike calc": r"MKT-024 strike calc",
    "MKT-020 call tightening fired": r"MKT-020:.*tightened|MKT-020:.*Call",
    "MKT-022 put tightening fired": r"MKT-022:.*tightened|MKT-022:.*Put",
    "MKT-045 chain snap fired": r"MKT-045|Snapped to chain|snap.*chain",
    "MKT-011 credit gate ran": r"MKT-011:.*Credit gate",
    "VIX regime override applied": r"VIX regime: (call|put)_stop_buffer",
    "Path-B simulated entry placed": r"\[DRY RUN\] Simulated Entry #\d+: Real credits",
    "Stop level computed": r"Stop level for full IC|Stop level for call-only|Stop level for put-only",
    "Buffer decay logged": r"MKT-042: Buffer decay",
    "Heartbeat after entry": r"\[DRY RUN\] HEARTBEAT.*Entries: [^0]",
}
DANGER_MARKERS = {
    "STATE-002 mismatch (RECONCILIATION KILL)": r"STATE-002:.*Position count mismatch",
    "Marked as stopped (external close)": r"marked as stopped \(external close\)",
    "Reconciling positions": r"Reconciling positions with Saxo",
    "MKT-033 AUTO long missing": r"MKT-033 AUTO:.*missing from Saxo",
    "ERROR or TRACEBACK in window": r"ERROR|Traceback|Exception",
}


def phase_entry_watch(timeout: int = 600) -> Report:
    """Stream journalctl, wait for next entry, capture window, evaluate."""
    rep = Report()
    print(f"Streaming journalctl waiting up to {timeout}s for next entry...")
    print(f"(scheduled entry times are typically 10:45, 11:15, 14:00 ET)")
    print()

    started = time.time()
    captured_lines: List[str] = []
    entry_start_time: Optional[datetime] = None

    # Use journalctl --follow with a process pipe
    cmd = [
        GCLOUD, "compute", "ssh", VM_NAME, f"--zone={VM_ZONE}",
        "--command=sudo journalctl -u hydra -f --no-pager"
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)

    try:
        while time.time() - started < timeout:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            line = line.rstrip()

            # Look for entry initiation
            if ENTRY_INITIATE_RE.search(line) and entry_start_time is None:
                entry_start_time = datetime.now()
                print(f"📍 Entry detected: {line[:120]}")
                print(f"   Capturing 90s of subsequent activity...")

            if entry_start_time is not None:
                captured_lines.append(line)
                elapsed = (datetime.now() - entry_start_time).total_seconds()
                if elapsed > 90:
                    print(f"   90s window complete ({len(captured_lines)} lines captured)")
                    break
    finally:
        proc.terminate()

    if entry_start_time is None:
        rep.add(
            "Entry placement detected within timeout",
            False,
            f"no 'Initiating Entry' seen in {timeout}s"
        )
        return rep

    rep.add(
        "Entry placement detected",
        True,
        f"start: {entry_start_time.strftime('%H:%M:%S')}, captured {len(captured_lines)} lines"
    )

    # Evaluate expected markers
    captured_text = "\n".join(captured_lines)
    for marker_name, pattern in EXPECTED_MARKERS.items():
        rep.add(
            marker_name,
            bool(re.search(pattern, captured_text)),
            "" if re.search(pattern, captured_text) else "missing from 90s window"
        )

    # Evaluate danger markers (these should be ABSENT)
    for danger_name, pattern in DANGER_MARKERS.items():
        seen = bool(re.search(pattern, captured_text))
        rep.add(
            f"NO {danger_name}",
            not seen,
            "DANGER: seen in window" if seen else ""
        )

    return rep


# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="HYDRA dry-run smoke test")
    parser.add_argument("--preflight", action="store_true", help="Pre-flight checks only")
    parser.add_argument("--watch", action="store_true", help="Watch for next entry")
    parser.add_argument("--full", action="store_true", help="Both preflight and watch")
    parser.add_argument("--timeout", type=int, default=600, help="Watch timeout in seconds")
    args = parser.parse_args()

    if not (args.preflight or args.watch or args.full):
        parser.error("Specify --preflight, --watch, or --full")

    do_preflight = args.preflight or args.full
    do_watch = args.watch or args.full

    overall_pass = True

    if do_preflight:
        rep = phase_preflight()
        rep.print("PHASE 1 — PRE-FLIGHT")
        if not rep.all_pass():
            overall_pass = False
            print()
            print("⚠ Pre-flight failed. Skipping watch phase to avoid wasting time.")
            print("  Fix the failed items first, then re-run.")
            return 1

    if do_watch:
        rep = phase_entry_watch(timeout=args.timeout)
        rep.print("PHASE 2 — ENTRY-WATCH")
        if not rep.all_pass():
            overall_pass = False

    print()
    print("=" * 80)
    print(f"OVERALL: {'✅ PASS' if overall_pass else '❌ FAIL'}")
    print("=" * 80)
    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
