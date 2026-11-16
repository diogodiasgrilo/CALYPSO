"""Conditional GET (ETag / 304) on the two 2s dashboard polls — 2026-11-16.

WHAT THIS PINS AND WHY IT MATTERS
---------------------------------
``/api/strategies/{id}/snapshot`` (main view) and
``/api/strategies/groups/{id}/comparison`` (comparison page) are polled every 2s
and were re-sent in FULL every time, with no validator anywhere in the backend:

    bl snapshot        64,521 B raw / 11,481 B gzip  ->  20.7 MB/hour
    ic_0dte comparison 93,511 B raw / 11,720 B gzip  ->  21.1 MB/hour

Measured after the close on 2026-11-16, when NOTHING on either payload changes
at all — a dashboard left open overnight re-downloaded ~20 MB/hour of
byte-identical data, worst on exactly the slow mobile link that prompted this.

Diffing two polls 2.5s apart found exactly ONE differing field in the snapshot
and five in the group payload, every one of them the same key,
``state_file_age_seconds`` — a clock reading taken at response-build time. That
single key is why a naive whole-body ETag would never have matched, so it is
excluded from the validator and echoed in a header instead.

THE INVARIANT THAT ACTUALLY MATTERS: a 304 must never mask a real change. The
validator is a hash of the content, so its only failure mode is an unnecessary
200. ``test_a_real_content_change_is_never_masked_by_a_304`` is the test that
pins it, and it is the one to read first if any of this is ever revisited.

Every group below carries a CONTROL that reproduces the original behaviour, so
none of these can pass vacuously:
  * no pruning          -> the ETag differs every poll (no 304 ever)
  * naive == comparison -> nginx's weak validator never matches
  * a changed payload   -> 200, not 304
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

import pyotp  # noqa: E402

from dashboard.backend.config import settings  # noqa: E402
from dashboard.backend.services import auth_crypto, auth_db  # noqa: E402
from dashboard.backend.services.http_cache import (  # noqa: E402
    SNAPSHOT_VOLATILE_KEYS,
    compute_etag,
    conditional_json,
    etag_matches,
)

VOLATILE = "state_file_age_seconds"


# ---------------------------------------------------------------------------
# compute_etag — the validator
# ---------------------------------------------------------------------------

def test_identical_payloads_share_one_etag():
    a = {"x": 1, "list": [1, 2, {"y": "z"}]}
    b = {"x": 1, "list": [1, 2, {"y": "z"}]}
    assert compute_etag(a) == compute_etag(b)


def test_any_content_change_changes_the_etag():
    base = {"body": {"entries": [{"credit": 1.25}]}}
    changed = {"body": {"entries": [{"credit": 1.30}]}}
    assert compute_etag(base) != compute_etag(changed)


def test_etag_ignores_key_order():
    """A refactor that reorders how a payload is assembled must not invalidate
    every client's cache for no reason."""
    assert compute_etag({"a": 1, "b": 2}) == compute_etag({"b": 2, "a": 1})


def test_volatile_key_is_ignored_at_the_top_level():
    a = {"id": "bl", VOLATILE: 1.0}
    b = {"id": "bl", VOLATILE: 999.0}
    assert compute_etag(a, SNAPSHOT_VOLATILE_KEYS) == compute_etag(b, SNAPSHOT_VOLATILE_KEYS)


def test_volatile_key_is_ignored_at_any_depth_including_per_member():
    """The real group payload carries one age PER MEMBER (variants.A, variants.BL,
    ...). Pruning by key NAME rather than by a fixed dotted path is what keeps
    this working when a strategy joins the group."""
    a = {"variants": {"A": {VOLATILE: 10.0, "pnl": 5}, "BL": {VOLATILE: 11.0, "pnl": 7}}}
    b = {"variants": {"A": {VOLATILE: 99.0, "pnl": 5}, "BL": {VOLATILE: 98.0, "pnl": 7}}}
    assert compute_etag(a, SNAPSHOT_VOLATILE_KEYS) == compute_etag(b, SNAPSHOT_VOLATILE_KEYS)


def test_volatile_key_inside_a_list_is_ignored():
    a = {"rows": [{VOLATILE: 1.0, "v": 2}]}
    b = {"rows": [{VOLATILE: 5.0, "v": 2}]}
    assert compute_etag(a, SNAPSHOT_VOLATILE_KEYS) == compute_etag(b, SNAPSHOT_VOLATILE_KEYS)


def test_pruning_a_volatile_key_does_not_hide_a_real_change_next_to_it():
    a = {"variants": {"A": {VOLATILE: 10.0, "pnl": 5}}}
    b = {"variants": {"A": {VOLATILE: 99.0, "pnl": 6}}}
    assert compute_etag(a, SNAPSHOT_VOLATILE_KEYS) != compute_etag(b, SNAPSHOT_VOLATILE_KEYS)


def test_control_without_pruning_the_etag_would_differ_on_every_poll():
    """CONTROL — reproduces the pre-fix situation. Two polls that differ ONLY in
    the clock reading hash differently when nothing is pruned, which is why a
    whole-body ETag would have produced a 200 every 2s and saved nothing."""
    a = {"id": "bl", VOLATILE: 1.0}
    b = {"id": "bl", VOLATILE: 999.0}
    assert compute_etag(a) != compute_etag(b)


def test_compute_etag_does_not_mutate_the_callers_payload():
    """The payload is still going out over the wire — mutating it to build the
    validator would DELETE the field from the response."""
    payload = {"body": {VOLATILE: 3.5, "x": 1}}
    compute_etag(payload, SNAPSHOT_VOLATILE_KEYS)
    assert payload["body"][VOLATILE] == 3.5


def test_etag_is_a_quoted_strong_validator():
    tag = compute_etag({"a": 1})
    assert tag.startswith('"') and tag.endswith('"')
    assert not tag.startswith("W/")


# ---------------------------------------------------------------------------
# etag_matches — If-None-Match evaluation
# ---------------------------------------------------------------------------

def test_exact_match():
    assert etag_matches('"abc"', '"abc"')


def test_weak_validator_matches_because_nginx_gzip_produces_one():
    """NOT defensive boilerplate. nginx gzips application/json on this host, and
    on-the-fly gzip downgrades a strong ETag to weak — so the validator the
    browser echoes really does arrive as W/"...". Without this, every
    revalidation through the real proxy would miss and the whole mechanism would
    be dead in production while passing locally."""
    assert etag_matches('W/"abc"', '"abc"')


def test_control_naive_equality_fails_the_nginx_case():
    """CONTROL — the comparison the obvious implementation would have used."""
    assert 'W/"abc"' != '"abc"'


def test_list_form_matches_any_member():
    assert etag_matches('"other", W/"abc"', '"abc"')


def test_wildcard_matches():
    assert etag_matches("*", '"abc"')


def test_mismatch_and_absent_do_not_match():
    assert not etag_matches('"different"', '"abc"')
    assert not etag_matches(None, '"abc"')
    assert not etag_matches("", '"abc"')


# ---------------------------------------------------------------------------
# conditional_json — the response
# ---------------------------------------------------------------------------

class _Req:
    """Minimal stand-in for a Starlette Request (only .headers is read)."""

    def __init__(self, if_none_match: str | None = None):
        from starlette.datastructures import Headers
        self.headers = Headers({"if-none-match": if_none_match} if if_none_match else {})


def test_200_carries_the_validator_and_a_revalidating_cache_control():
    r = conditional_json(_Req(), {"a": 1})
    assert r.status_code == 200
    assert r.headers["etag"]
    # `no-cache` means "store, but revalidate before reuse" — that is what makes
    # the NEXT poll conditional. `no-store` would disable the mechanism and
    # `max-age` would let a stale body be served WITHOUT asking, which on a live
    # trading view would be wrong.
    assert r.headers["cache-control"] == "private, no-cache"
    assert "private" in r.headers["cache-control"]


def test_matching_validator_yields_an_empty_304_with_the_same_etag():
    first = conditional_json(_Req(), {"a": 1})
    tag = first.headers["etag"]
    second = conditional_json(_Req(tag), {"a": 1})
    assert second.status_code == 304
    assert second.body == b""
    assert second.headers["etag"] == tag


def test_304_declares_no_entity():
    """A 304 must not claim to carry a body. Pins Starlette's behaviour too —
    if a version ever started emitting content-length: 0 here, some proxies and
    clients mis-frame the response."""
    first = conditional_json(_Req(), {"a": 1})
    r = conditional_json(_Req(first.headers["etag"]), {"a": 1})
    assert "content-length" not in r.headers
    assert "content-type" not in r.headers


def test_volatile_key_is_excluded_from_the_validator_but_still_in_the_body():
    """The optimisation must not be a tradeoff: the field is pruned from the
    HASH, never from the response."""
    payload = {"body": {VOLATILE: 42.5, "x": 1}}
    r = conditional_json(_Req(), payload, volatile_keys=SNAPSHOT_VOLATILE_KEYS)
    assert json.loads(r.body)["body"][VOLATILE] == 42.5


def test_extra_headers_ride_on_both_the_200_and_the_304():
    """This is the mechanism that makes a 304 lossless — the live value of a
    pruned key travels in a header, which a 304 still carries."""
    p = {"body": {VOLATILE: 7.0}}
    first = conditional_json(_Req(), p, volatile_keys=SNAPSHOT_VOLATILE_KEYS,
                             extra_headers={"X-State-Age-Seconds": 7.0})
    assert first.headers["x-state-age-seconds"] == "7.0"
    second = conditional_json(_Req(first.headers["etag"]), p,
                              volatile_keys=SNAPSHOT_VOLATILE_KEYS,
                              extra_headers={"X-State-Age-Seconds": 9.0})
    assert second.status_code == 304
    assert second.headers["x-state-age-seconds"] == "9.0"


def test_changed_content_returns_200_even_with_a_validator_present():
    first = conditional_json(_Req(), {"a": 1})
    r = conditional_json(_Req(first.headers["etag"]), {"a": 2})
    assert r.status_code == 200
    assert json.loads(r.body) == {"a": 2}


def test_body_is_byte_identical_to_what_jsonresponse_would_have_sent():
    """We hash the bytes we send, so the serialisation must match FastAPI's
    exactly — otherwise the validator describes something other than the
    response."""
    from fastapi.responses import JSONResponse
    payload = {"z": 1, "a": [1, 2, {"k": "v"}], "u": "é"}
    assert conditional_json(_Req(), payload).body == JSONResponse(payload).body


# ---------------------------------------------------------------------------
# Route level — the real app, through the real auth guard
# ---------------------------------------------------------------------------

def _enroll_and_login(client, username="etag-tester", password="initial-temp-password-1"):
    from dashboard.backend.routers import auth as auth_router_module
    auth_router_module._rate_buckets.clear()
    auth_router_module._pending.clear()
    auth_db.init_db(settings.dashboard_auth_db)
    auth_db.create_user(settings.dashboard_auth_db, username, auth_crypto.hash_password(password))
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    pending = r.json()["pending_token"]
    r = client.post("/api/auth/change-password",
                    json={"pending_token": pending, "new_password": "a-brand-new-strong-password-2"})
    pending = r.json()["pending_token"]
    r = client.post("/api/auth/setup-totp", json={"pending_token": pending})
    secret = r.json()["secret"]
    r = client.post("/api/auth/verify-totp",
                    json={"pending_token": pending, "code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200, r.text


@pytest.fixture
def state_file(tmp_path):
    """A minimal IC state file for variant G, so the snapshot has real content."""
    vd = tmp_path / "variant_g"
    vd.mkdir()
    f = vd / "hydra_state.json"
    f.write_text(json.dumps({"date": "2026-11-16", "state": "idle", "entries": [],
                             "pnl_history": [], "market_data_ohlc": {"spx_open": 7499.0}}))
    return f


@pytest.fixture
def client(tmp_path, monkeypatch, state_file):
    monkeypatch.setattr(settings, "dashboard_auth_db", tmp_path / "dashboard_auth.db", raising=False)
    monkeypatch.setattr(settings, "session_cookie_secure", False, raising=False)
    monkeypatch.setattr(settings, "variant_g_state_file", state_file, raising=False)
    from dashboard.backend.main import app
    with TestClient(app) as c:
        _enroll_and_login(c)
        yield c


SNAP = "/api/strategies/g/snapshot"
GROUP = "/api/strategies/groups/ic_0dte/comparison"


def test_snapshot_200_carries_an_etag_and_the_age_header(client):
    r = client.get(SNAP)
    assert r.status_code == 200
    assert r.headers["etag"]
    assert r.headers["cache-control"] == "private, no-cache"
    assert "x-state-age-seconds" in r.headers
    assert r.json()["id"] == "g"


def test_snapshot_replay_with_the_validator_is_a_304(client):
    first = client.get(SNAP)
    r = client.get(SNAP, headers={"If-None-Match": first.headers["etag"]})
    assert r.status_code == 304
    assert r.content == b""


def test_snapshot_304_still_carries_the_live_age_header(client):
    """Nothing is withheld by the 304: the one field excluded from the validator
    comes back in a header, recomputed."""
    first = client.get(SNAP)
    r = client.get(SNAP, headers={"If-None-Match": first.headers["etag"]})
    assert r.status_code == 304
    assert float(r.headers["x-state-age-seconds"]) >= float(first.headers["x-state-age-seconds"])


def test_snapshot_weak_validator_still_matches(client):
    """What nginx actually sends back after gzipping our response."""
    first = client.get(SNAP)
    weak = "W/" + first.headers["etag"]
    assert client.get(SNAP, headers={"If-None-Match": weak}).status_code == 304


def test_a_real_content_change_is_never_masked_by_a_304(client, state_file):
    """THE invariant. A validator in hand plus changed data must yield a fresh
    200 with a different ETag — the whole safety case for putting a cache
    validator on a live trading view rests on this."""
    first = client.get(SNAP)
    assert first.status_code == 200
    state_file.write_text(json.dumps({"date": "2026-11-16", "state": "monitoring",
                                      "entries": [{"entry_number": 1, "total_credit": 1.25}],
                                      "pnl_history": [],
                                      "market_data_ohlc": {"spx_open": 7499.0}}))
    second = client.get(SNAP, headers={"If-None-Match": first.headers["etag"]})
    assert second.status_code == 200
    assert second.headers["etag"] != first.headers["etag"]
    assert second.json()["body"]["summary"]["total_entries"] >= 1


def test_another_strategys_validator_does_not_produce_a_304(client):
    """Cross-strategy safety: a validator is only ever valid for the content it
    was computed from, and two strategies never share content."""
    g = client.get(SNAP)
    r = client.get("/api/strategies/bl/snapshot", headers={"If-None-Match": g.headers["etag"]})
    assert r.status_code == 200


def test_group_comparison_is_conditional_too(client):
    first = client.get(GROUP)
    assert first.status_code == 200
    assert first.headers["etag"]
    r = client.get(GROUP, headers={"If-None-Match": first.headers["etag"]})
    assert r.status_code == 304
    assert r.content == b""


def test_control_an_unconditional_request_always_gets_the_full_body(client):
    """CONTROL — the pre-fix behaviour, which is still exactly what a client
    without a validator gets. Proves the 304s above come from the conditional
    header and not from some blanket change to the endpoint."""
    a = client.get(SNAP)
    b = client.get(SNAP)
    assert a.status_code == b.status_code == 200
    assert len(b.content) > 100


# ---------------------------------------------------------------------------
# The builder seam — the fixture generator's entry point
# ---------------------------------------------------------------------------

def test_the_route_serves_exactly_what_the_builder_builds(client):
    """The routes now return a conditional ``Response``, so anything that wants
    the PAYLOAD (``scripts/make_synthetic_fixtures.py``, which serialises these
    into the Playwright fixture set) calls the builder instead. That is two code
    paths to the same bytes, and this pins them together — otherwise the CI
    fixtures can drift away from what the dashboard is actually served, and the
    only symptom is a Playwright page rendering blank.

    The volatile clock field is excluded from the comparison for the same reason
    it is excluded from the validator: it differs between any two reads."""
    from dashboard.backend.routers import strategies as S

    served = client.get(SNAP).json()
    built = S.build_snapshot_payload("g")
    served["body"].pop(VOLATILE, None)
    built["body"].pop(VOLATILE, None)
    assert served == built


def test_the_group_route_serves_exactly_what_its_builder_builds(client):
    import asyncio
    from dashboard.backend.routers import strategies as S

    served = client.get(GROUP).json()
    built = asyncio.run(S.build_group_comparison_payload("ic_0dte"))

    def strip(node):
        if isinstance(node, dict):
            node.pop(VOLATILE, None)
            for v in node.values():
                strip(v)
        elif isinstance(node, list):
            for v in node:
                strip(v)

    strip(served)
    strip(built)
    assert served == built


def test_the_builders_are_importable_under_their_public_names():
    """They are public (no leading underscore) precisely because a script
    outside this module calls them. A rename is a breaking change for the CI
    fixture set, so make it fail here rather than there."""
    from dashboard.backend.routers import strategies as S

    assert callable(S.build_snapshot_payload)
    assert callable(S.build_group_comparison_payload)
