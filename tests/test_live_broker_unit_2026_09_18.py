"""The real-money broker unit — and the one mistake that would have cost a live session.

``deploy/calypso-broker-live.service`` is the second IBKR session owner, for the funded
account. It coexists with the paper broker ONLY because IBKR's one-session limit is per
USERNAME and paper/live are separate usernames with separate OAuth registrations
(LIVE_MONEY_ARCHITECTURE.md §2, fact 2).

THE MISTAKE THIS FILE EXISTS TO PREVENT, which was in the design's own first draft:
"start it on paper credentials first, to prove two brokers coexist with zero live-money
exposure." That is not a safe rehearsal — it is two sessions on ONE username, which is
precisely the eviction crash-loop calypso-broker was built to end, and it would have
taken the LIVE PAPER SEAT offline. Caught in the audit-before for this unit rather than
in production. The design is safe *because* the usernames differ; pointing both brokers
at paper destroys the premise it rests on.

So the load-bearing assertion here is not about ports or logs. It is
``test_the_two_brokers_share_no_credential_file``: overlap in ANY of the six credential
paths means two sessions on one username.

WHAT CAN AND CANNOT BE VERIFIED YET. Everything in this file is STATIC — unit-file
correctness, no collisions with the paper broker, and the guard wiring. Two brokers
actually running concurrently is only testable once live credentials exist, and at that
point it is no longer a rehearsal: it gets the full pre-start verification in
deploy/IBKR_CREDENTIALS_SETUP.md and a first start with NO strategy pointed at it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PAPER = ROOT / "deploy" / "calypso-broker.service"
LIVE = ROOT / "deploy" / "calypso-broker-live.service"


def directives(path: Path) -> list[str]:
    """Live directive lines only.

    A systemd unit's comments routinely quote the very directive under discussion — and
    this unit's comments quote several deliberately, to explain what must NOT be set. A
    raw substring search would match the explanation instead of the setting.
    """
    return [l.strip() for l in path.read_text().splitlines()
            if l.strip() and not l.strip().startswith("#")]


def env_of(path: Path) -> dict[str, str]:
    out = {}
    for line in directives(path):
        m = re.match(r'^Environment="?([A-Z_0-9]+)=([^"]*)"?$', line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def creds_of(path: Path) -> dict[str, str]:
    out = {}
    for line in directives(path):
        m = re.match(r"^LoadCredentialEncrypted=([^:]+):(.+)$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


# ══════════════════════════════════════════════════════════════════════════════
# THE ONE THAT MATTERS
# ══════════════════════════════════════════════════════════════════════════════

def test_the_two_brokers_share_no_credential_file():
    """A shared credential file = two sessions on ONE IBKR username = they evict each
    other, and the live paper seat goes down with them.

    This is the assertion the whole file exists for. It fails loudly for the exact
    mistake the design's first draft proposed as a *safety measure*.
    """
    paper, live = creds_of(PAPER), creds_of(LIVE)
    assert paper and live, "could not parse credentials from one of the units"
    overlap = set(paper.values()) & set(live.values())
    assert not overlap, (
        f"the live and paper brokers load the SAME credential file(s): {sorted(overlap)}. "
        f"That is two sessions on one IBKR username — they will evict each other and "
        f"take the live paper seat down."
    )


def test_the_live_unit_uses_the_live_key_directory():
    live = creds_of(LIVE)
    assert len(live) == 6, f"expected 6 credentials, found {len(live)}"
    wrong = {k: v for k, v in live.items() if "/ibkr/live/" not in v}
    assert not wrong, f"live credential(s) not under /etc/calypso/ibkr/live/: {wrong}"


def test_the_paper_unit_was_not_touched():
    """CONTROL. Adding a live broker must not have moved the paper one's credentials —
    that would break the session the live seat is trading on right now."""
    paper = creds_of(PAPER)
    assert len(paper) == 6
    moved = {k: v for k, v in paper.items() if not v.startswith("/etc/calypso/ibkr/")}
    assert not moved, f"paper credential paths changed: {moved}"
    assert all("/ibkr/live/" not in v for v in paper.values()), (
        "the PAPER broker now points at live credentials"
    )


# ══════════════════════════════════════════════════════════════════════════════
# No other collision between the two services
# ══════════════════════════════════════════════════════════════════════════════

def test_the_two_brokers_bind_different_ports():
    assert env_of(PAPER)["CALYPSO_BROKER_PORT"] == "8788"
    assert env_of(LIVE)["CALYPSO_BROKER_PORT"] == "8789"


def test_the_live_broker_declares_the_live_environment():
    """resolve_environment() reads this, and IBClient cross-checks it against the
    account code IBKR actually returns."""
    assert env_of(LIVE)["CALYPSO_IBKR_ENV"] == "live"


def test_the_paper_broker_does_not_declare_live():
    """It may declare paper or say nothing — resolve_environment() defaults to paper on
    purpose, so that an unset value can never silently select live."""
    assert env_of(PAPER).get("CALYPSO_IBKR_ENV", "paper") == "paper"


def test_the_two_brokers_log_to_different_files():
    """ARGUS finds circuit-breaker events by scanning a broker log path. One shared file
    would interleave two processes' rotation AND make "which broker degraded"
    unanswerable — on the session where it matters most."""
    live_log = env_of(LIVE).get("CALYPSO_BROKER_LOG", "")
    assert live_log, "the live broker does not set CALYPSO_BROKER_LOG"
    # The paper broker leaves it unset and takes services/broker/main.py's default.
    default = "/opt/calypso/logs/broker/broker.log"
    paper_log = env_of(PAPER).get("CALYPSO_BROKER_LOG", default)
    assert live_log != paper_log, f"both brokers log to {live_log}"


def test_the_two_units_have_different_syslog_identifiers():
    def ident(p):
        return next((l.split("=", 1)[1] for l in directives(p)
                     if l.startswith("SyslogIdentifier=")), None)
    assert ident(PAPER) and ident(LIVE) and ident(PAPER) != ident(LIVE)


def test_the_live_broker_does_not_order_itself_behind_the_paper_one():
    """Two independent sessions. An After=/Requires= on the paper broker would let a
    paper-side fault delay or block the real-money session for no reason."""
    bad = [l for l in directives(LIVE)
           if re.match(r"^(After|Requires|BindsTo|PartOf)=.*calypso-broker\.service", l)]
    assert not bad, f"the live broker is coupled to the paper broker: {bad}"


# ══════════════════════════════════════════════════════════════════════════════
# Safety posture must match the paper broker's — it holds real-money keys
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("directive", [
    "NoNewPrivileges=yes", "PrivateTmp=yes", "ProtectSystem=strict",
    "ProtectControlGroups=yes", "ProtectKernelTunables=yes", "PrivateDevices=yes",
    "User=calypso", "Restart=always",
])
def test_live_broker_keeps_the_hardening(directive):
    """The process holding the decrypted REAL-MONEY OAuth keys must be at least as
    sandboxed as the paper one."""
    assert directive in directives(LIVE), f"{directive} missing from the live unit"


def test_live_broker_does_not_use_the_silently_ignored_directive():
    """ProtectDevices= is silently ignored on this host's systemd 252 (verified on the
    paper unit). Using it would LOOK like /dev isolation while providing none."""
    assert not any(l.startswith("ProtectDevices=") for l in directives(LIVE))


def test_live_broker_runs_the_same_entrypoint():
    """One implementation, two configurations. A forked entrypoint would let the
    real-money path drift away from the one that gets exercised daily."""
    def execstart(p):
        return next(l for l in directives(p) if l.startswith("ExecStart="))
    assert execstart(LIVE) == execstart(PAPER)


def test_live_broker_is_not_wired_to_start_automatically_anywhere():
    """Going live is a decision. Same rule the Gate-3 smoke landmine taught: nothing may
    pull the real-money broker up as a side effect."""
    offenders = []
    for unit in list((ROOT / "deploy").glob("*.service")) + list((ROOT / "deploy").glob("*.timer")):
        if unit.name == "calypso-broker-live.service":
            continue
        for line in directives(unit):
            if re.match(r"^(Wants|Requires|After|BindsTo)=.*calypso-broker-live", line):
                offenders.append(f"{unit.name}: {line}")
    assert not offenders, (
        f"unit(s) pull the real-money broker up automatically: {offenders}"
    )


def test_no_strategy_unit_points_at_the_live_broker_yet():
    """No variant declares live_money, so none may be aimed at :8789. When `bm` is added
    this test is updated deliberately — which is the point: aiming a unit at the
    real-money broker should require touching a file called 'test'."""
    offenders = []
    for unit in (ROOT / "deploy").glob("hydra*.service"):
        for line in directives(unit):
            if "CALYPSO_BROKER_URL" in line and "8789" in line:
                offenders.append(f"{unit.name}: {line}")
    assert not offenders, f"strategy unit(s) already aimed at the live broker: {offenders}"
