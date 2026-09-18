"""A circuit breaker opening on the REAL-MONEY broker would have been seen by nobody.

ARGUS finds breaker trips by scanning a log file, and until 2026-09-18 that path was a
single hardcoded literal:

    BROKER_LOG="${CALYPSO_DIR}/logs/broker/broker.log"

Circuit breakers live in the broker PROCESS (the strategies proxy to it and expose
``circuit_breakers = {}``), so that file is the only place a trip is recorded. With
``calypso-broker-live`` writing to its own log, the funded account's session had zero
monitoring — the one session where an unnoticed outage costs real money.

TWO CHANGES, and the second is a judgement call worth stating:

1. Both logs are scanned, and every matched line is LABELLED with its broker. "A breaker
   opened" without naming the account is close to useless once two are running, because
   the consequences differ completely.

2. On the real-money broker, EVERY family is a FAIL — not just ``orders``. The paper
   split is right for paper: a ``market`` breaker there means degraded quotes, the bot
   skips entries, nothing is harmed. The same breaker on a funded account means real
   positions are being managed blind, and ``session``/``portfolio`` OPEN means the bot
   cannot read its own positions on an account holding real money. "Warning" is the wrong
   word for that.

Inert today: the live broker is not running and a missing log is skipped silently.

HOW THIS IS TESTED. ARGUS is a 590-line bash script that shells out to systemctl, gsutil
and a venv python — running it whole in a unit test would prove nothing and fail on a
laptop. So the two decision-making blocks are EXTRACTED FROM THE SCRIPT ITSELF at test
time and executed against fixtures. That is deliberately not a reimplementation: if
someone edits the scan or the classification, these tests follow the edit rather than
quietly testing a stale copy — the same drift that made nine MKT-048 tests vacuous
earlier today.
"""

from __future__ import annotations

import re
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SCRIPT = ROOT / "services" / "argus" / "health_check.sh"
ET = ZoneInfo("America/New_York")


def _src() -> str:
    return SCRIPT.read_text()


def _extract_python_scan() -> str:
    """The embedded breaker-scan, verbatim from the script."""
    s = _src()
    start = s.index('breaker_log=$("${VENV_PYTHON}" -c "')
    body_start = s.index("\n", start) + 1
    end = s.index('" 2>/dev/null)', body_start)
    return s[body_start:end]


def _extract_bash_classification() -> str:
    """The FAIL/WARN decision block, verbatim from the script."""
    s = _src()
    start = s.index('if [[ -n "${breaker_log}" ]]; then')
    end = s.index("\n# =====", start)
    return s[start:end]


def _line(ts: datetime, family: str) -> str:
    """One real breaker-OPEN line.

    NOTE THE ARROW: shared/ib_retry.py logs "CircuitBreaker[ib.<family>] closed → OPEN",
    with U+2192 — and the scan regex matches `<state> . OPEN`, exactly ONE character. A
    fixture written with ASCII "->" is two characters and silently never matches, which
    is how the first run of this file failed for a reason unrelated to the code. If the
    production format ever changes, these fixtures must change with it.
    """
    return (f"{ts.strftime('%Y-%m-%d %H:%M:%S')},123 | WARNING | calypso-broker | "
            f"CircuitBreaker[ib.{family}] closed \u2192 OPEN \u2014 too many failures")


@pytest.fixture
def logs(tmp_path):
    """A fixture CALYPSO_DIR with both brokers' log directories."""
    (tmp_path / "logs" / "broker").mkdir(parents=True)
    (tmp_path / "logs" / "broker-live").mkdir(parents=True)
    return tmp_path


def run_scan(root: Path) -> list[str]:
    """Execute the script's OWN python scan against the fixture tree."""
    code = _extract_python_scan()
    code = code.replace("${BROKER_LOG}", str(root / "logs" / "broker" / "broker.log"))
    code = code.replace("${BROKER_LIVE_LOG}",
                        str(root / "logs" / "broker-live" / "broker.log"))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, f"scan failed: {r.stderr}"
    return [l for l in r.stdout.splitlines() if l.strip()]


def _config_line(name: str) -> str:
    """Lift a config assignment out of the script, so the harness cannot drift from it.

    Mutation testing caught this: the first version HARDCODED
    ``BREAKER_LIVE_MONEY_ALL_FAMILIES_FAIL=true`` here, so flipping it to false in the
    script changed nothing and all 16 tests still passed. The harness was testing its own
    constant. Exactly the drift this file's docstring warns about, committed inside the
    file that warns about it.
    """
    m = re.search(rf"^{name}=.*$", _src(), re.M)
    assert m, f"{name} is gone from health_check.sh"
    return m.group(0)


def classify(breaker_log: str) -> dict:
    """Execute the script's OWN bash classification against a given scan result."""
    harness = textwrap.dedent("""
        set -uo pipefail
    """) + _config_line("BREAKER_OPEN_FAIL_FAMILIES") + "\n" \
        + _config_line("BREAKER_LIVE_MONEY_ALL_FAMILIES_FAIL") + "\n" + textwrap.dedent("""
        FAILURES=()
        WARNINGS=()
        breaker_status="ok"
        breaker_opens_today="0"
        breaker_log=$(cat <<'ARGUS_FIXTURE_EOF'
""") + breaker_log + textwrap.dedent("""
ARGUS_FIXTURE_EOF
)
    """) + _extract_bash_classification() + textwrap.dedent("""
        echo "STATUS=${breaker_status}"
        for f in ${FAILURES+"${FAILURES[@]}"}; do echo "FAIL=$f"; done
        for w in ${WARNINGS+"${WARNINGS[@]}"}; do echo "WARN=$w"; done
    """)
    r = subprocess.run(["bash", "-c", harness], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"classification failed: {r.stderr}"
    out = r.stdout
    return {
        "status": re.search(r"STATUS=(\S+)", out).group(1),
        "failures": re.findall(r"FAIL=(.*)", out),
        "warnings": re.findall(r"WARN=(.*)", out),
    }


# ══════════════════════════════════════════════════════════════════════════════
# The scan — both logs, correctly labelled
# ══════════════════════════════════════════════════════════════════════════════

def test_a_real_money_breaker_is_seen_at_all():
    """THE DEFECT. Before this, only the paper log was scanned."""
    assert "BROKER_LIVE_LOG" in _src(), "ARGUS no longer knows the real-money broker log"


def test_the_scan_finds_and_labels_a_live_money_trip(logs):
    now = datetime.now(ET)
    (logs / "logs" / "broker-live" / "broker.log").write_text(_line(now, "orders") + "\n")
    hits = run_scan(logs)
    assert len(hits) == 1
    assert hits[0].startswith("BROKER=live-money "), (
        f"a real-money trip is not labelled as such: {hits[0]!r}"
    )


def test_the_scan_still_finds_paper_trips(logs):
    """CONTROL — the pre-existing capability must survive."""
    now = datetime.now(ET)
    (logs / "logs" / "broker" / "broker.log").write_text(_line(now, "orders") + "\n")
    hits = run_scan(logs)
    assert len(hits) == 1 and hits[0].startswith("BROKER=paper ")


def test_both_brokers_are_reported_separately(logs):
    now = datetime.now(ET)
    (logs / "logs" / "broker" / "broker.log").write_text(_line(now, "market") + "\n")
    (logs / "logs" / "broker-live" / "broker.log").write_text(_line(now, "session") + "\n")
    hits = run_scan(logs)
    labels = sorted(h.split(" ", 1)[0] for h in hits)
    assert labels == ["BROKER=live-money", "BROKER=paper"]


def test_rotated_live_logs_are_scanned(logs):
    """The broker rotates at 10MB. A trip just before a rotation must not vanish."""
    now = datetime.now(ET)
    (logs / "logs" / "broker-live" / "broker.log.1").write_text(_line(now, "orders") + "\n")
    hits = run_scan(logs)
    assert len(hits) == 1 and hits[0].startswith("BROKER=live-money ")


def test_a_missing_live_log_is_silent(logs):
    """Inert until the live broker runs — no noise, no error."""
    now = datetime.now(ET)
    (logs / "logs" / "broker" / "broker.log").write_text(_line(now, "orders") + "\n")
    assert len(run_scan(logs)) == 1


def test_old_trips_are_windowed_out(logs):
    """15-minute window. A trip from an hour ago is history, not an incident."""
    old = datetime.now(ET) - timedelta(hours=1)
    (logs / "logs" / "broker-live" / "broker.log").write_text(_line(old, "orders") + "\n")
    assert run_scan(logs) == []


def test_an_unparseable_timestamp_fails_closed(logs):
    """A line whose timestamp cannot be read must be KEPT, not dropped — the existing
    fail-closed contract, which must survive on the real-money path too."""
    (logs / "logs" / "broker-live" / "broker.log").write_text(
        "garbled | CircuitBreaker[ib.orders] closed \u2192 OPEN \u2014 boom\n")
    hits = run_scan(logs)
    assert len(hits) == 1 and hits[0].startswith("BROKER=live-money ")


# ══════════════════════════════════════════════════════════════════════════════
# The classification — real money escalates
# ══════════════════════════════════════════════════════════════════════════════

def test_any_live_money_family_is_a_failure():
    """THE ESCALATION. `market` on paper is a warning; on a funded account it means real
    positions are being managed blind."""
    r = classify("BROKER=live-money 2026-09-18 12:00:00,1 | W | b | CircuitBreaker[ib.market] closed \u2192 OPEN \u2014 x")
    assert r["status"] == "fail_live_money_open"
    assert r["failures"] and "REAL-MONEY" in r["failures"][0]
    assert "ib.market" in r["failures"][0], "the failure does not name the family"
    assert not r["warnings"]


def test_the_paper_split_is_unchanged_for_non_fail_families():
    """CONTROL. The escalation must not have turned every paper warning into a failure —
    that would page on degraded quotes the bot already handles by skipping."""
    r = classify("BROKER=paper 2026-09-18 12:00:00,1 | W | b | CircuitBreaker[ib.market] closed \u2192 OPEN \u2014 x")
    assert r["status"] == "non_fail_family_open"
    assert not r["failures"] and r["warnings"]
    assert "PAPER" in r["warnings"][0], "the warning does not say which broker"


def test_paper_orders_still_fails():
    """CONTROL. The pre-existing safety-critical path."""
    r = classify("BROKER=paper 2026-09-18 12:00:00,1 | W | b | CircuitBreaker[ib.orders] closed \u2192 OPEN \u2014 x")
    assert r["status"] == "fail_family_open"
    assert r["failures"] and "PAPER" in r["failures"][0]


def test_real_money_is_reported_even_alongside_paper_noise():
    """Ordering matters: with trips on both, the message must name the account that can
    actually lose money, not the first line that happened to match."""
    r = classify(
        "BROKER=paper 2026-09-18 12:00:00,1 | W | b | CircuitBreaker[ib.market] closed \u2192 OPEN \u2014 x\n"
        "BROKER=live-money 2026-09-18 12:00:01,1 | W | b | CircuitBreaker[ib.session] closed \u2192 OPEN \u2014 y")
    assert r["status"] == "fail_live_money_open"
    assert "REAL-MONEY" in r["failures"][0] and "ib.session" in r["failures"][0]


def test_a_quiet_scan_raises_nothing():
    """INSTRUMENT CONTROL. A monitor that always fails is not a monitor."""
    r = classify("")
    assert r["status"] == "ok" and not r["failures"] and not r["warnings"]


# ══════════════════════════════════════════════════════════════════════════════
# Guards on the script itself
# ══════════════════════════════════════════════════════════════════════════════

def test_the_script_is_valid_bash():
    r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, f"health_check.sh has a syntax error: {r.stderr}"


def test_calypso_dir_still_defaults_to_the_production_path():
    """Made overridable only for these tests. argus.service sets no CALYPSO_DIR, so
    production must resolve to the same literal it always did."""
    assert 'CALYPSO_DIR="${CALYPSO_DIR:-/opt/calypso}"' in _src()
    unit = (ROOT / "deploy" / "argus.service").read_text()
    assert "CALYPSO_DIR" not in unit, (
        "argus.service now sets CALYPSO_DIR — the default is no longer what runs"
    )


def test_real_money_escalation_ships_enabled():
    """The escalation is a named switch, so it can be turned off in one character. Pin
    the SHIPPED value: turning it off is a deliberate decision that should fail a test
    with this name, not a quiet edit."""
    assert "BREAKER_LIVE_MONEY_ALL_FAMILIES_FAIL=true" in _src(), (
        "real-money breaker escalation is disabled — any family OPEN on the funded "
        "account would be downgraded to a warning"
    )


def test_the_live_log_path_matches_what_the_broker_writes():
    """Two files, one path. If the unit's CALYPSO_BROKER_LOG and ARGUS's BROKER_LIVE_LOG
    drift apart, ARGUS silently watches a file nothing writes — which looks exactly like
    a healthy broker."""
    m = re.search(r'BROKER_LIVE_LOG="\$\{CALYPSO_DIR\}(/[^"]+)"', _src())
    assert m, "BROKER_LIVE_LOG is gone or reshaped"
    unit = (ROOT / "deploy" / "calypso-broker-live.service").read_text()
    assert f"/opt/calypso{m.group(1)}" in unit, (
        f"ARGUS watches {m.group(1)} but the live broker unit does not write there"
    )
