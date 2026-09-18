"""Nothing stopped a strategy trading the WRONG ACCOUNT.

Design: ``docs/migration/LIVE_MONEY_ARCHITECTURE.md`` §3.1 + §3.2. Found during the
pre-design audit for running real money alongside paper — by reading the health payload
rather than by an incident, which is the only acceptable way to find this one.

THE HOLE. A strategy is bound to an account by exactly one line — ``CALYPSO_BROKER_URL``
in its systemd unit — and until now nothing verified the far end. ``/health`` answered
``{status, connected, authenticated, competing}``: whether the broker was WELL, never
WHO it was. With one broker that was harmless. With a paper broker on :8788 and a
real-money broker on :8789, a single mistyped port routes a paper-intended variant onto
the funded account, where it places REAL orders. No error. No alert. Nothing unusual in
any log. It would look like an ordinary day until the statement arrived.

``IBClient._assert_account_matches_env`` is the right pattern and was already there, but
one layer too low to help: it validates the broker's own session against the broker's own
declaration. It cannot see that a *strategy* dialled the wrong port.

THE SECOND FINDING, and a correction worth keeping. The first draft of the design claimed
adding a real-money variant breaks ``live_seat_id()`` outright. **That was wrong** — a new
id is not in the hardcoded ``LIVE_SEAT_IDS = ("b", "c")``, so it would not have been seen
at all. The real defect is a fork where BOTH branches are wrong:

  * leave the new id out of the tuple  -> the real-money bot is structurally invisible to
    "which is the bot": never primary, never followed by reader_for()/the WS/the agents;
  * add it (as anyone reasonably would) -> two ``dry_run=false`` seats, ``len(live) == 1``
    fails, and the function falls back to ``"c"`` — a DRY-RUN variant — silently declared
    to be "the bot" at the exact moment real money is at stake.

Root cause is one field carrying two facts, the same shape as the ``pnl_shape`` bug that
forced the dashboard rebuild: ``dry_run`` says whether orders are PLACED; nothing said
what they were placed AGAINST. Hence the new ``account_kind`` axis and the split into
``live_seat_id()`` (paper) and ``live_money_seat_id()`` (real money).

THE MOST IMPORTANT TESTS HERE ARE THE NO-OP CONTROLS. This lands while B holds the live
paper seat mid-Gate-4, so "changes nothing today" is a claim that must be *proved*, not
asserted: every strategy still declares paper, and ``live_seat_id()`` still resolves to
``"b"`` — now via the taxonomy instead of a hardcoded tuple, and it must still be "b".
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared import strategy_taxonomy as tax  # noqa: E402
from shared.broker_service import BrokerDispatcher  # noqa: E402

MAIN_PY = ROOT / "bots" / "hydra" / "main.py"


# ══════════════════════════════════════════════════════════════════════════════
# NO-OP CONTROLS — prove this changed nothing about today
# ══════════════════════════════════════════════════════════════════════════════

def test_exactly_one_variant_declares_real_money():
    """NARROWED 2026-09-18 — this asserted that EVERY variant declared paper, and it
    failed the moment `bm` was added. That is the test working, not breaking: its whole
    purpose is to fail when a variant stops declaring paper.

    So it is narrowed rather than weakened. The live-money set must be EXACTLY {"bm"} —
    a drift on any of A/B/C/D/E/F/G still fails, and adding a SECOND real-money variant
    also fails, which is correct: that should require a deliberate edit here.
    """
    live = {sid for sid, m in tax.STRATEGIES.items() if m.account_kind == tax.LIVE_MONEY}
    assert live == {"bm"}, (
        f"the set of real-money variants is {live or '{}'}, expected exactly "
        f"{{'bm'}} — a default has drifted, or a promotion happened without updating "
        f"this test"
    )


def test_the_real_money_variant_ships_not_trading():
    """account_kind says WHICH account; dry_run says whether it trades at all. `bm`
    declares real money and must still ship simulating — going live is a deliberate
    operator flip, never a checked-in default."""
    cfg = json.loads((ROOT / "bots" / "hydra" / "config"
                      / "config_variant_bm.json").read_text())
    assert cfg["dry_run"] is True, (
        "config_variant_bm.json ships with dry_run=false — a fresh checkout would place "
        "REAL orders the moment the unit starts"
    )


def test_account_kind_values_are_valid():
    bad = {sid: m.account_kind for sid, m in tax.STRATEGIES.items()
           if m.account_kind not in tax.VALID_ACCOUNT_KINDS}
    assert not bad, f"invalid account_kind: {bad}"


def test_there_is_no_real_money_seat_today():
    """``live_money_seat_id()`` must answer None, and callers must handle None. A
    stub that returned some default would be worse than useless."""
    from dashboard.backend.services.variant_readers import live_money_seat_id
    assert live_money_seat_id() is None


# ══════════════════════════════════════════════════════════════════════════════
# The seat resolver
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def seats(tmp_path, monkeypatch):
    """Point EVERY variant's config at a file under tmp_path.

    Hermetic on purpose. ``live_seat_id()`` now scans every paper variant rather than a
    hardcoded ``("b", "c")``, so any id left pointing at the real ``/opt/calypso`` path
    would make the result depend on whether the test host happens to be the VM.

    Swaps the whole ``settings`` OBJECT rather than setting attributes on it. The real
    one is a pydantic model with ``extra`` disallowed, so ``setattr`` for an id it has
    no field for raises ``ValueError: "Settings" object has no field ...``. That is not
    a test artefact — it is a genuine integration requirement this fixture surfaced:
    **adding `bm` for real needs an explicit `variant_bm_*` field in
    dashboard/backend/config.py**, it cannot be introduced by the taxonomy alone.
    """
    from dashboard.backend.services import variant_readers as vr

    def _set(**dry_run_by_id):
        ns = SimpleNamespace()
        for sid in set(tax.available_ids()) | set(dry_run_by_id):
            p = tmp_path / f"config_{sid}.json"
            p.write_text(json.dumps({"dry_run": dry_run_by_id.get(sid, True)}))
            setattr(ns, f"variant_{sid}_config_file", p)
        monkeypatch.setattr(vr, "settings", ns)
        return ns
    return _set


def test_live_seat_is_still_b(seats):
    """THE CONTROL THAT MATTERS. B holds the live paper seat mid-Gate-4; this refactor
    must not move it."""
    from dashboard.backend.services.variant_readers import live_seat_id
    seats(b=False)
    assert live_seat_id() == "b"


def test_a_swap_back_to_c_still_resolves(seats):
    """The swap this resolver exists to follow, in the other direction (RB-9 rollback)."""
    from dashboard.backend.services.variant_readers import live_seat_id
    seats(c=False)
    assert live_seat_id() == "c"


def test_two_live_paper_seats_do_not_resolve_to_a_dry_run_variant(seats):
    """THE §3.2 DEFECT, in the branch that produced a wrong answer.

    Old behaviour: two live seats -> ``len(live) == 1`` fails -> hardcoded ``"c"``, which
    is DRY-RUN. Answering "which bot is trading" with one that places no orders is worse
    than answering "I don't know".
    """
    from dashboard.backend.services.variant_readers import live_seat_id
    seats(a=False, b=False)
    resolved = live_seat_id()
    assert resolved == "b", (
        f"ambiguous seat resolved to {resolved!r}; it must fall back to the taxonomy's "
        f"declared live seat (b), never to a dry-run variant"
    )


def test_no_live_seat_falls_back_to_the_declared_one(seats):
    """All dry-run (e.g. an emergency stop). Must still name the declared seat rather
    than a stale constant."""
    from dashboard.backend.services.variant_readers import live_seat_id
    seats()
    assert live_seat_id() == "b"


def test_unreadable_config_never_raises(seats, tmp_path):
    """This runs on every dashboard request. A config missing for a moment during a
    restart must not 500 the page.

    Mutate the namespace the FIXTURE installed, not the real ``settings`` — the first
    version patched the real object, which the fixture had already replaced, so it
    passed while exercising nothing. A test that cannot fail is worse than no test.
    """
    from dashboard.backend.services.variant_readers import live_seat_id
    ns = seats(b=False)
    ns.variant_b_config_file = tmp_path / "vanished.json"
    # B was the only live seat and its config is now gone, so no variant reads as
    # dry_run=false: the declared-seat fallback answers, and nothing raises.
    assert live_seat_id() == "b"


def test_malformed_config_never_raises(seats, tmp_path):
    """Truncated JSON — the realistic corruption, mid-write rather than missing."""
    from dashboard.backend.services.variant_readers import live_seat_id
    ns = seats(c=False)
    bad = tmp_path / "truncated.json"
    bad.write_text('{"dry_run": fal')
    ns.variant_c_config_file = bad
    assert live_seat_id() == "b"


def test_seat_candidates_come_from_the_taxonomy_not_a_tuple(seats):
    """The hardcoded tuple is the reason a new variant would have been invisible.
    Pinning this stops a future edit quietly reintroducing it."""
    src = (ROOT / "dashboard" / "backend" / "services" / "variant_readers.py").read_text()
    body = src[src.index("def live_seat_id"):src.index("def live_money_seat_id")]
    assert "ids_for_account_kind" in body, "live_seat_id no longer derives its candidates"
    assert "LIVE_SEAT_IDS" not in body, "live_seat_id reads the hardcoded tuple again"


def test_a_real_money_variant_does_not_disturb_the_paper_seat(seats, monkeypatch):
    """THE WHOLE POINT OF THE SPLIT, simulated before `bm` exists.

    Registers a synthetic real-money variant that is ALSO dry_run=false — the exact
    condition that broke the old resolver — and asserts the two questions now get two
    independent, correct answers.
    """
    from dashboard.backend.services import variant_readers as vr

    synthetic = dict(tax.STRATEGIES)
    b = tax.STRATEGIES["b"]
    synthetic["bm"] = tax.StrategyMeta(
        id="bm", display_name="B money", short_name="BM",
        strategy_class=b.strategy_class, group_id=b.group_id,
        structure_family=b.structure_family, pnl_shape=b.pnl_shape,
        dte_class=b.dte_class, status="live", bot_name_base=b.bot_name_base,
        account_kind=tax.LIVE_MONEY,
    )
    monkeypatch.setattr(tax, "STRATEGIES", synthetic)

    seats(b=False, bm=False)

    assert vr.live_seat_id() == "b", "the real-money variant hijacked the paper seat"
    assert vr.live_money_seat_id() == "bm"


# ══════════════════════════════════════════════════════════════════════════════
# The guard itself
# ══════════════════════════════════════════════════════════════════════════════

def test_paper_variant_reaching_a_live_broker_refuses_to_start():
    """THE MONEY-LOSING DIRECTION. A paper-intended variant on the funded account."""
    with pytest.raises(ValueError, match="ACCOUNT MISMATCH"):
        tax.assert_account_matches("b", tax.LIVE_MONEY)


def test_real_money_variant_reaching_the_paper_broker_refuses_to_start(monkeypatch):
    """The reverse loses no money but corrupts the paper record with a second set of
    orders — the very record being used to decide whether to scale up."""
    m = tax.STRATEGIES["b"]
    monkeypatch.setitem(tax.STRATEGIES, "b",
                        tax.StrategyMeta(**{**m.__dict__, "account_kind": tax.LIVE_MONEY}))
    with pytest.raises(ValueError, match="ACCOUNT MISMATCH"):
        tax.assert_account_matches("b", tax.PAPER)


def test_real_money_variant_refuses_an_unverifiable_broker(monkeypatch):
    """FAIL CLOSED. An old broker that cannot report its identity must never be trusted
    with real orders."""
    m = tax.STRATEGIES["b"]
    monkeypatch.setitem(tax.STRATEGIES, "b",
                        tax.StrategyMeta(**{**m.__dict__, "account_kind": tax.LIVE_MONEY}))
    with pytest.raises(ValueError, match="did not report its identity"):
        tax.assert_account_matches("b", None)


def test_paper_variant_tolerates_an_unverifiable_broker():
    """FAIL OPEN, deliberately and asymmetrically.

    Failing closed here would take all seven strategies down the first time someone
    restarts them before the broker — the 2026-06-08 deploy-order bug — to protect
    against paper orders on a paper account, i.e. nothing.
    """
    tax.assert_account_matches("b", None)


def test_matching_account_passes():
    """INSTRUMENT CONTROL. A guard that rejects everything is not a guard."""
    tax.assert_account_matches("b", tax.PAPER)


def test_unrecognised_broker_answer_refuses_rather_than_guesses():
    with pytest.raises(ValueError, match="unrecognised account kind"):
        tax.assert_account_matches("b", "demo")


def test_invalid_declaration_refuses(monkeypatch):
    m = tax.STRATEGIES["b"]
    monkeypatch.setitem(tax.STRATEGIES, "b",
                        tax.StrategyMeta(**{**m.__dict__, "account_kind": "sandbox"}))
    with pytest.raises(ValueError, match="not one of"):
        tax.assert_account_matches("b", tax.PAPER)


# ══════════════════════════════════════════════════════════════════════════════
# broker vocabulary -> taxonomy vocabulary
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("env,expected", [
    ("paper", tax.PAPER),
    ("live", tax.LIVE_MONEY),
    ("PAPER", tax.PAPER),
    ("  Live  ", tax.LIVE_MONEY),
    (None, None),
    ("", None),
    ("live_money", None),   # the taxonomy's own word is NOT the broker's
    ("prod", None),
    (123, None),
])
def test_environment_maps_to_account_kind(env, expected):
    assert tax.account_kind_for_environment(env) is expected or \
           tax.account_kind_for_environment(env) == expected


# ══════════════════════════════════════════════════════════════════════════════
# /health publishes identity
# ══════════════════════════════════════════════════════════════════════════════

class _Creds:
    def __init__(self, environment):
        self.environment = environment


class _Cfg:
    def __init__(self, environment):
        self.credentials = _Creds(environment)


_HEALTHY = {"authenticated": True, "connected": True, "competing": False}
_UNSET = object()  # so a test can pass status=None and mean it, not "use the default"


class _FakeIB:
    def __init__(self, environment="paper", account="DUR123456", status=_UNSET, boom=False):
        self.cfg = _Cfg(environment)
        self._account = account
        self._status = _HEALTHY if status is _UNSET else status
        self._boom = boom

    @property
    def account_id(self):
        if self._account is None:
            raise RuntimeError("account_id not yet resolved — call connect() first")
        return self._account

    def check_auth_status(self):
        if self._boom:
            raise RuntimeError("IBKR exploded")
        return self._status


def test_health_reports_environment_and_account():
    h = BrokerDispatcher(_FakeIB())._probe_health()
    assert h["environment"] == "paper"
    assert h["account"] == "DUR123456"
    assert h["connected"] is True


def test_health_reports_identity_on_a_LIVE_broker():
    h = BrokerDispatcher(_FakeIB(environment="live", account="U7654321"))._probe_health()
    assert h["environment"] == "live"
    assert tax.account_kind_for_environment(h["environment"]) == tax.LIVE_MONEY


def test_identity_is_present_even_when_the_session_is_degraded():
    """"Which broker is sick" is exactly what an operator needs when two are running,
    and it is knowable even when the session is unusable."""
    ib = _FakeIB(status={"authenticated": False, "connected": False, "competing": True})
    h = BrokerDispatcher(ib)._probe_health()
    assert h["connected"] is False
    assert h["environment"] == "paper"


def test_identity_survives_an_exploding_auth_check():
    """Health must never raise — the pre-existing contract. Identity must not weaken it."""
    h = BrokerDispatcher(_FakeIB(boom=True))._probe_health()
    assert h["connected"] is False
    assert h["status"] == "degraded"
    assert h["environment"] == "paper"


@pytest.mark.parametrize("bad_status", [None, "nonsense", 42, ["authenticated"]])
def test_identity_present_when_auth_status_is_not_a_dict(bad_status):
    """THE THIRD DEGRADED PATH, and it was uncovered until mutation testing said so.

    ``_probe_health`` has TWO identical ``return {**base, "status": "degraded"}`` lines —
    one for a non-dict auth status, one in the exception handler. Deleting identity from
    the FIRST left all 44 tests green, because every degraded test I had written went
    through either the exception handler or the normal branch. A mutation that survives
    is a test suite admitting it does not cover a line.
    """
    h = BrokerDispatcher(_FakeIB(status=bad_status))._probe_health()
    assert h["connected"] is False
    assert h["environment"] == "paper", "identity is missing on the non-dict-status path"


def test_identity_present_when_the_ib_has_no_auth_check():
    """``getattr(self._ib, "check_auth_status", None)`` tolerates its absence, so an
    IBClient stub without it reaches the same fail-closed return."""
    class NoAuthCheck:
        cfg = _Cfg("paper")
        account_id = "DUR999999"

    h = BrokerDispatcher(NoAuthCheck())._probe_health()
    assert h["connected"] is False
    assert h["environment"] == "paper"
    assert h["account"] == "DUR999999"


def test_unresolved_account_degrades_to_none_rather_than_raising():
    """``account_id`` raises BY CONTRACT before connect() resolves it. Reading it in
    health must not turn that into a 500."""
    h = BrokerDispatcher(_FakeIB(account=None))._probe_health()
    assert h["account"] is None
    assert h["environment"] == "paper"


def test_health_never_raises_on_a_hostile_ib():
    """The paranoid case: an object where everything blows up."""
    class Hostile:
        @property
        def cfg(self):
            raise RuntimeError("no")

        @property
        def account_id(self):
            raise RuntimeError("no")

        def check_auth_status(self):
            raise RuntimeError("no")

    h = BrokerDispatcher(Hostile())._probe_health()
    assert h["connected"] is False
    assert h["environment"] is None and h["account"] is None


# ══════════════════════════════════════════════════════════════════════════════
# The strategy side: reading the broker's answer
# ══════════════════════════════════════════════════════════════════════════════

def test_broker_account_kind_reads_health_in_broker_mode(monkeypatch):
    from bots.hydra import main as hydra_main
    from shared.broker_client import BrokerClient

    bc = BrokerClient("http://127.0.0.1:9999")
    monkeypatch.setattr(bc, "health", lambda: {"environment": "live", "connected": True})
    assert hydra_main._broker_account_kind(bc) == tax.LIVE_MONEY


def test_broker_account_kind_is_none_when_the_broker_cannot_say(monkeypatch):
    """An OLD broker — one deployed before the identity fields existed — answers health
    without them. That is the deploy-order case, and it must read as "cannot say"."""
    from bots.hydra import main as hydra_main
    from shared.broker_client import BrokerClient

    bc = BrokerClient("http://127.0.0.1:9999")
    monkeypatch.setattr(bc, "health", lambda: {"status": "ok", "connected": True})
    assert hydra_main._broker_account_kind(bc) is None


def test_broker_account_kind_is_none_when_health_raises(monkeypatch):
    from bots.hydra import main as hydra_main
    from shared.broker_client import BrokerClient, BrokerError

    bc = BrokerClient("http://127.0.0.1:9999")

    def _boom():
        raise BrokerError("down")

    monkeypatch.setattr(bc, "health", _boom)
    assert hydra_main._broker_account_kind(bc) is None


def test_broker_account_kind_reads_credentials_on_the_legacy_path():
    """Legacy direct-IBClient mode. By this point IBClient._assert_account_matches_env
    has already refused a session whose account code disagreed with the declaration, so
    the credential environment is a verified fact rather than a claim."""
    from bots.hydra import main as hydra_main
    assert hydra_main._broker_account_kind(_FakeIB(environment="live")) == tax.LIVE_MONEY
    assert hydra_main._broker_account_kind(_FakeIB(environment="paper")) == tax.PAPER


def test_broker_account_kind_does_not_guess_from_a_bool():
    """``is_paper`` is ``environment == "paper"``, so False conflates "live" with
    "a value we do not recognise". Reading the raw string keeps unknown as None."""
    from bots.hydra import main as hydra_main
    assert hydra_main._broker_account_kind(_FakeIB(environment="staging")) is None


# ══════════════════════════════════════════════════════════════════════════════
# WIRING — the guard has to actually run
# ══════════════════════════════════════════════════════════════════════════════

def _calls_named(tree, name: str):
    """Every ``ast.Call`` in ``tree`` invoking ``name`` (bare or as ``x.name``).

    AST, NOT a substring search, and that is the whole point. The first version of
    these three tests searched the raw source for ``"assert_account_matches"`` — and
    MUTATION TESTING CAUGHT IT: deleting the real call from main.py left all 44 tests
    green, because ``_broker_account_kind``'s own docstring names the function it feeds.
    The test was reading prose about the guard instead of the guard.

    That trap has now fired THIRTEEN times in this repo in a single day, which is enough
    evidence that ``strip_comments`` is not the fix for source checks about CODE
    STRUCTURE — it removes whole-line comments and docstrings, but a trailing ``#``
    comment would slip straight through it. Parsing removes the whole class of error.
    """
    import ast
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if (isinstance(f, ast.Name) and f.id == name) or \
           (isinstance(f, ast.Attribute) and f.attr == name):
            out.append(node)
    return out


@pytest.fixture(scope="module")
def main_tree():
    import ast
    return ast.parse(MAIN_PY.read_text())


def test_startup_calls_the_guard(main_tree):
    """A guard that is defined but never called is decoration."""
    assert _calls_named(main_tree, "assert_account_matches"), (
        "main.py never CALLS the account guard (a docstring mentioning it is not a call)"
    )
    assert _calls_named(main_tree, "_broker_account_kind"), (
        "main.py never asks the broker which account it is holding"
    )


def test_the_guard_runs_before_the_strategy_is_built(main_tree):
    """Order matters: refusing AFTER constructing the strategy would already have run
    its __init__ side effects against the wrong account's data paths."""
    guard = min(c.lineno for c in _calls_named(main_tree, "assert_account_matches"))
    built = min(c.lineno for c in _calls_named(main_tree, "build_strategy"))
    assert guard < built, (
        f"the account guard (line {guard}) runs after build_strategy (line {built})"
    )


def test_the_guard_is_not_inside_the_connect_retry_loop(main_tree):
    """A mismatch is PERMANENT. The connect loop treats BrokerError as transient and
    waits 15s x 48 — right for "the broker has no session yet", wrong here: twelve
    minutes of retries would bury the reason. The guard must sit AFTER the loop."""
    import ast

    loops = [n for n in ast.walk(main_tree)
             if isinstance(n, ast.While) and _calls_named(n, "connect")]
    assert loops, "could not find the connect-retry loop — re-check this test's premise"
    guard = min(c.lineno for c in _calls_named(main_tree, "_broker_account_kind")
                if c.lineno > 1)
    for loop in loops:
        assert not (loop.lineno <= guard <= (loop.end_lineno or loop.lineno)), (
            "the account guard sits inside the connect-retry loop, so a permanent "
            "mismatch would be retried for ~12 minutes before failing"
        )


def test_the_meta_api_exposes_account_kind():
    """The UI must never give a real-money variant the same affordances as a paper one,
    and it cannot distinguish them if the API does not say."""
    from dashboard.backend.routers.strategies import _strategy_meta_dict
    d = _strategy_meta_dict(tax.STRATEGIES["b"])
    assert d["account_kind"] == tax.PAPER


# ══════════════════════════════════════════════════════════════════════════════
# The fail-safe worth keeping (§3.3)
# ══════════════════════════════════════════════════════════════════════════════

def test_broker_client_defaults_to_the_paper_port():
    """A unit that loses CALYPSO_BROKER_URL falls back to PAPER, never to real money.
    That is an accident of the original single-broker design and it happens to be the
    safe direction — pin it so a refactor cannot quietly make the default dynamic."""
    import inspect
    from shared.broker_client import BrokerClient

    default = inspect.signature(BrokerClient.__init__).parameters["base_url"].default
    assert default == "http://127.0.0.1:8788", (
        f"BrokerClient's default base_url is now {default!r} — it must stay the PAPER "
        f"broker so a missing CALYPSO_BROKER_URL cannot reach real money"
    )


def test_the_broker_banner_does_not_hardcode_an_environment():
    """It logged "paper account" three lines above resolving the environment
    dynamically. Two brokers make that actively misleading in the journal."""
    src = (ROOT / "services" / "broker" / "main.py").read_text()
    banner = re.search(r'logger\.info\(\s*"calypso-broker starting[^)]*\)', src, re.S)
    assert banner, "the startup banner is gone — re-check what the broker announces"
    assert "paper" not in banner.group(0), (
        "the broker's startup banner hardcodes an environment again"
    )
