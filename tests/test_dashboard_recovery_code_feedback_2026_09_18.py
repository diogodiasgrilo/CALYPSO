"""Signing in with a recovery code must SAY SO, and say how many are left.

Found 2026-09-18 while auditing the one surface the whole visual audit had
bypassed: the login gate. Every screen in that audit was reached through a mock
server with authentication switched off, so the real entry path had never been
exercised at all.

THE DEFECT. ``POST /api/auth/verify-totp`` already returns
``recovery_code_used: true`` when the submitted code was a recovery code rather
than a TOTP digit — and ``tests/test_dashboard_auth.py`` pins that contract. The
frontend declares the field in ``VerifyTotpResponse``...

    frontend/src/auth.ts:58        recovery_code_used?: boolean;

...and then never reads it. ``handleVerifyTotp`` branches only on
``res.recovery_codes`` (the first-enrollment list), so a recovery-code login
falls through the ``else`` straight to ``authed``.

The consequence is not cosmetic. There are exactly TEN recovery codes
(``auth_crypto.generate_recovery_codes(count=10)``), each is destroyed on use,
and there is no self-service way to mint more — regenerating them needs
``scripts/manage_dashboard_users.py reset-2fa`` over SSH. So a person who has
lost their authenticator burns a strictly finite, unreplenishable resource on
every sign-in and is told nothing: not that a code was consumed, not how many
remain, and not that reaching zero locks them out of their own dashboard until
an operator intervenes. They discover the count only by hitting zero.

The backend never reported a remaining count at all, so surfacing one needs both
halves: a new ``recovery_codes_remaining`` field, and a gate step that shows it.

CONTROLS. An ordinary TOTP login and a first-time enrollment must be completely
unchanged — the failure mode of a fix like this is a warning that fires on every
login, which trains people to click through it.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GATE = ROOT / "dashboard" / "frontend" / "src" / "components" / "auth" / "LoginGate.tsx"
AUTH_TS = ROOT / "dashboard" / "frontend" / "src" / "auth.ts"


def _strip_comments(src: str) -> str:
    """Search code, not the prose that explains it.

    A bare substring search also matches the comment documenting the very thing
    being asserted. That trap has now bitten four tests in this repo, so it is
    handled up front rather than by rewording each comment around it.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^\s*//.*$", "", src, flags=re.M)


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    """TestClient on a fresh temp auth DB.

    Deliberately a copy of the fixture in ``test_dashboard_auth.py`` rather than
    an import: importing a fixture across test modules drags the entire other
    module in as a side effect, and these two files must be runnable
    independently of each other.
    """
    from dashboard.backend import main as main_module
    from dashboard.backend.config import settings
    from dashboard.backend.routers import auth as auth_router_module

    monkeypatch.setattr(settings, "dashboard_auth_db", tmp_path / "auth.db")
    monkeypatch.setattr(settings, "session_cookie_secure", False)
    auth_router_module._pending.clear()
    auth_router_module._rate_buckets.clear()

    from starlette.testclient import TestClient

    with TestClient(main_module.app) as client:
        yield client, settings


# ── Backend: the remaining count must exist and be right ───────────────────

def _enrolled_user(settings, codes: list[str], username: str = "diogo"):
    """A fully-enrolled account with a known set of recovery codes."""
    from dashboard.backend.services import auth_db as adb, auth_crypto as ac

    uid = adb.create_user(settings.dashboard_auth_db, username, ac.hash_password("x" * 20))
    adb.update_password(settings.dashboard_auth_db, uid, ac.hash_password("x" * 20))
    adb.set_totp_secret(settings.dashboard_auth_db, uid, "AAAAAAAAAAAAAAAA")
    adb.enable_totp(settings.dashboard_auth_db, uid, ac.hash_recovery_codes(codes))
    return uid


def _sign_in_with(client, code: str, username: str = "diogo"):
    r = client.post("/api/auth/login", json={"username": username, "password": "x" * 20})
    pending = r.json()["pending_token"]
    return client.post("/api/auth/verify-totp", json={"pending_token": pending, "code": code})


def test_remaining_count_is_reported(app_client):
    """THE FIX. Three codes, one consumed, two left — and the response says so."""
    client, settings = app_client
    codes = ["aaaaa-11111", "bbbbb-22222", "ccccc-33333"]
    _enrolled_user(settings, codes)

    r = _sign_in_with(client, codes[0])
    assert r.status_code == 200
    body = r.json()
    assert body.get("recovery_code_used") is True
    assert body.get("recovery_codes_remaining") == 2, (
        "the login succeeded but the person was not told how much of a finite, "
        "unreplenishable resource they have left"
    )


def test_count_reaches_zero_on_the_last_code(app_client):
    """The case that matters most: the next lost-phone login locks them out."""
    client, settings = app_client
    _enrolled_user(settings, ["only-one-left"])
    r = _sign_in_with(client, "only-one-left")
    assert r.status_code == 200
    assert r.json().get("recovery_codes_remaining") == 0


def test_count_decrements_across_consecutive_logins(app_client):
    """Not a constant. Two sign-ins must report 2 then 1, not 2 then 2."""
    client, settings = app_client
    codes = ["aaaaa-11111", "bbbbb-22222", "ccccc-33333"]
    _enrolled_user(settings, codes)

    seen = []
    for c in codes[:2]:
        r = _sign_in_with(client, c)
        seen.append(r.json().get("recovery_codes_remaining"))
        client.post("/api/auth/logout")
    assert seen == [2, 1]


# ── Controls: the ordinary paths must not change at all ────────────────────

def test_normal_totp_login_reports_neither_field(app_client):
    """NO-OP CONTROL. A warning that fires on every sign-in is worse than none —
    people learn to dismiss it, and the real one gets dismissed too."""
    import pyotp

    client, settings = app_client
    _enrolled_user(settings, ["aaaaa-11111", "bbbbb-22222"])

    r = client.post("/api/auth/login", json={"username": "diogo", "password": "x" * 20})
    pending = r.json()["pending_token"]
    r = client.post("/api/auth/verify-totp", json={
        "pending_token": pending,
        "code": pyotp.TOTP("AAAAAAAAAAAAAAAA").now(),
    })
    assert r.status_code == 200
    body = r.json()
    assert "recovery_code_used" not in body
    assert "recovery_codes_remaining" not in body, (
        "the remaining count leaked into the ordinary login path"
    )


def test_first_enrollment_is_unchanged(app_client):
    """CONTROL. Enrollment hands over all ten codes; it has not 'used' one."""
    import pyotp

    from dashboard.backend.services import auth_db as adb, auth_crypto as ac

    client, settings = app_client
    uid = adb.create_user(settings.dashboard_auth_db, "diogo", ac.hash_password("x" * 20))
    adb.update_password(settings.dashboard_auth_db, uid, ac.hash_password("x" * 20))

    r = client.post("/api/auth/login", json={"username": "diogo", "password": "x" * 20})
    pending = r.json()["pending_token"]
    secret = client.post("/api/auth/setup-totp", json={"pending_token": pending}).json()["secret"]
    r = client.post("/api/auth/verify-totp", json={
        "pending_token": pending, "code": pyotp.TOTP(secret).now(),
    })
    assert r.status_code == 200
    body = r.json()
    assert len(body["recovery_codes"]) == 10
    assert "recovery_codes_remaining" not in body
    assert "recovery_code_used" not in body


def test_a_rejected_code_reports_nothing(app_client):
    """CONTROL. A failed attempt must not leak the remaining count — that would
    tell an attacker how close the account is to being permanently locked."""
    client, settings = app_client
    _enrolled_user(settings, ["aaaaa-11111", "bbbbb-22222"])
    r = _sign_in_with(client, "not-a-real-code")
    assert r.status_code == 401
    assert "recovery_codes_remaining" not in r.json()


# ── Frontend: the gate must actually act on it ─────────────────────────────

def test_login_gate_reads_recovery_code_used():
    """The defect itself: the field was typed and never read."""
    src = _strip_comments(GATE.read_text())
    assert "recovery_code_used" in src, (
        "LoginGate still ignores recovery_code_used — a recovery-code sign-in "
        "falls through to the dashboard silently"
    )


def test_login_gate_renders_the_remaining_count():
    """A warning with no number tells the person nothing actionable."""
    src = _strip_comments(GATE.read_text())
    assert "recovery_codes_remaining" in src or "recoveryRemaining" in src


def test_login_gate_has_a_distinct_exhausted_branch():
    """Zero left is a different situation from three left: the next lost-phone
    login cannot succeed at all and needs an operator before it happens.

    This assertion is deliberately about the COPY, not merely about a ``=== 0``
    comparison existing. The first version of this test checked only for the
    comparison, and mutation testing killed it immediately: deleting the
    "that was your last code" headline — the single most important sentence in
    the whole flow — left the test green, because the branch's own colour
    ternaries still contained ``recoveryRemaining === 0``. Matching a loose
    keyword inside the conditional's string literal keeps it robust to rewording
    while still requiring that the exhausted case actually SAYS something the
    ordinary one does not.
    """
    src = _strip_comments(GATE.read_text())
    assert re.search(r"(recoveryRemaining|recovery_codes_remaining)\s*===?\s*0", src), (
        "no branch distinguishes 'none left' from 'some left'"
    )
    headline = re.search(
        r'recoveryRemaining\s*===?\s*0\s*\?\s*"[^"]*\blast\b[^"]*"', src, re.I
    )
    assert headline, (
        "the exhausted case has no headline of its own — someone signing in on "
        "their final recovery code is told the same thing as someone with nine "
        "left, which is the whole point of reporting the count"
    )
    body = re.search(
        r'recoveryRemaining\s*===?\s*0\s*\?\s*"[^"]*(none|not be able|locked)[^"]*"',
        src, re.I,
    )
    assert body, "the exhausted case does not spell out the consequence"


def test_auth_ts_declares_the_new_field():
    """The response shape is the contract between the two halves of this fix."""
    src = _strip_comments(AUTH_TS.read_text())
    assert "recovery_codes_remaining" in src


def test_the_step_is_not_skippable_by_falling_through():
    """The original bug in one line: branching only on `recovery_codes` sends a
    recovery-code login to `authed`. Pin that a used code routes elsewhere."""
    src = _strip_comments(GATE.read_text())
    fn = src[src.index("const handleVerifyTotp"):]
    fn = fn[: fn.index("\n  };")]
    assert "recovery_code_used" in fn, (
        "handleVerifyTotp decides the next step without consulting whether a "
        "recovery code was consumed"
    )


# ── Tap targets: the gate is also the app's only phone-first screen ────────

def _button_tags(src: str) -> list[str]:
    """The attribute text of every ``<button …>`` opening tag.

    Deliberately not a regex ending at the first ``>``. JSX handlers contain
    arrows — ``onClick={() => setStep("authed")}`` — and a ``.*?>`` match stops
    dead on the ``>`` of the arrow, truncating the tag before its className and
    reporting a perfectly compliant button as a violation. The first version of
    this test did precisely that and failed on correct code, which is how the
    bug was caught: the mutation run's BASELINE was red.
    """
    tags = []
    for m in re.finditer(r"<button\b", src):
        i = m.end()
        while i < len(src) and not (src[i] == ">" and src[i - 1] != "="):
            i += 1
        tags.append(src[m.end():i])
    return tags

def test_every_control_on_the_gate_clears_44px():
    """Measured, not guessed: uiaudit/diag-login.mjs at 390px reported inputs at
    42px, primary buttons at 40px and the copy button at 34px — every single
    interactive element on the sign-in path was under the 44px HIG minimum.

    It had never surfaced because the visual audit reached the app through a
    mock server with authentication disabled, so this screen was never rendered
    once across 42 audited surfaces.

    Pinned as a source rule rather than a pixel measurement so it fails in the
    unit suite (which runs in CI on every push) instead of only when someone
    remembers to re-run the browser harness.
    """
    src = GATE.read_text()

    m = re.search(r"const inputClass\s*=\s*\n?\s*\"([^\"]+)\"", src)
    assert m, "inputClass literal not found — has the gate been restructured?"
    assert "min-h-11" in m.group(1), (
        f"text inputs have no 44px floor: {m.group(1)!r}"
    )

    buttons = _button_tags(src)
    assert len(buttons) >= 3, f"expected several buttons, found {len(buttons)}"

    undersized = []
    for attrs in buttons:
        m_cls = re.search(r'className="([^"]*)"', attrs)
        classes = m_cls.group(1) if m_cls else ""
        if "min-h-11" not in classes:
            undersized.append(attrs.strip()[:120])

    assert not undersized, (
        f"{len(undersized)} button(s) on the login gate have no 44px floor — "
        f"first: {undersized[0]}"
    )


def test_gate_step_union_includes_the_new_step():
    """TypeScript's Step union is the list of screens that exist; a step that
    is set but not in the union would not compile."""
    src = GATE.read_text()
    union = src[src.index("type Step ="):]
    union = union[: union.index(";")]
    steps = set(re.findall(r'"(\w+)"', union))
    assert "recoveryUsed" in steps, f"Step union is {sorted(steps)}"
