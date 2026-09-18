"""A "primary" that was three frozen copies of a two-month-old answer.

Found 2026-09-18 while deploying the dashboard — by noticing an unfamiliar systemd
drop-in in `systemctl status`, not by looking for a bug.

WHAT WAS THERE. The dashboard's "primary" data source was expressed as four file paths,
set in THREE places that disagreed:

    dashboard/backend/config.py defaults ......... variant_c
    the installed dashboard.service unit ......... variant_a
    dashboard.service.d/primary-c.conf ........... variant_c   (VM-ONLY, untracked)

The drop-in was written 2026-06-02 to override the unit's A-paths back to C. C stopped
being the live seat on 2026-07-24. Nothing updated any of the three.

WHY IT MOSTLY DID NOT MATTER, and the one place it did. The real resolution is dynamic —
``live_seat_id() -> _seat_field() -> variant_<seat>_*`` — so these statics are reached
only when the live seat has no field of its own, which for a registered variant never
happens. Traced before alarming: ``canonical_reader`` had ZERO importers, and
``live_*()`` all resolved through ``variant_b_*``.

The single reachable path was ``routers/market.py``'s module-level
``db_reader = BacktestingDBReader(settings.backtesting_db)``, the last-resort tick source
for /ohlc and /ticks. Bound at IMPORT, it could never follow a swap.

AND THAT IS THE PART WORTH REMEMBERING: ``variant_readers.py`` exists *specifically* to
kill this drift class — its own module docstring describes the 2026-07-13 bug where
hard-wired ``settings.backtesting_db`` readers disagreed with variant-aware ones. That
sweep fixed ``replay_pnl`` **in this very file** and missed ``/ohlc`` and ``/ticks`` two
functions above it. A fix applied by hand to the endpoints someone happened to look at.

A CORRECTION I MADE TO MYSELF, recorded because the wrong version was reported first:
"the SPX chart reads variant C's database" is FALSE. The chart reads ``market_data_db``
(variant A's) and is *deliberately* pinned there — A samples ~4-8 ticks/min against C's
~1/min, and a sparse source renders every candle as a flat doji. That pin is chosen for
SAMPLING DENSITY, not for account ownership, and it is the one variant-pinned reader in
the dashboard that is correct as a pin. Only the *fallback beneath it* was stale.

THE SHAPE OF THE FIX. Remove the reachable staleness (market.py resolves per request),
delete the dead trap (``canonical_reader``), make the remaining fallbacks name the
current live seat so nothing in the block contradicts itself — and then make the
alignment a TEST rather than a habit, so the next swap fails loudly here instead of
rotting quietly for two months the way this did.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared import strategy_taxonomy as tax  # noqa: E402

MARKET_PY = ROOT / "dashboard" / "backend" / "routers" / "market.py"
READERS_PY = ROOT / "dashboard" / "backend" / "services" / "variant_readers.py"
ROUTERS_DIR = ROOT / "dashboard" / "backend" / "routers"


# ── The reachable defect ───────────────────────────────────────────────────

@pytest.mark.parametrize("endpoint", ["get_ohlc", "get_ticks"])
def test_market_endpoints_resolve_the_reader_per_request(endpoint):
    """/ohlc and /ticks must EACH fall back through the LIVE SEAT, not a frozen import.

    Checked per function, in the AST. The first version asserted only that
    ``"canonical_db_reader()"`` appeared SOMEWHERE in the file — and mutation testing
    killed it: breaking /ohlc alone left the string present via /ticks and the test
    passed. A file-level substring cannot tell you which endpoint is correct.
    """
    tree = ast.parse(MARKET_PY.read_text())
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == endpoint), None)
    assert fn is not None, f"{endpoint} is gone from market.py"
    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call)
             and (getattr(n.func, "id", None) == "canonical_db_reader"
                  or getattr(n.func, "attr", None) == "canonical_db_reader")]
    assert calls, (
        f"{endpoint} does not resolve its fallback reader via canonical_db_reader(), so "
        f"it cannot follow a live-seat swap"
    )


def test_no_router_binds_a_db_reader_at_import_time():
    """THE RULE, generalised beyond the one endpoint that was broken.

    A module-level ``BacktestingDBReader(...)`` in a router is bound once at import and
    can never follow a live-seat swap. ``market_reader`` is the sole allowed exception:
    it is pinned to the DENSEST recorder for the price chart, which is a sampling
    decision, not an account decision.
    """
    offenders = []
    for path in sorted(ROUTERS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in tree.body:                     # module level ONLY
            if not isinstance(node, ast.Assign):
                continue
            call = node.value
            if not isinstance(call, ast.Call):
                continue
            name = getattr(call.func, "id", None) or getattr(call.func, "attr", None)
            if name != "BacktestingDBReader":
                continue
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if targets == ["market_reader"]:
                continue                            # the deliberate density pin
            offenders.append(f"{path.name}:{node.lineno} {targets}")
    assert not offenders, (
        "router(s) build a DB reader at import time, so they cannot follow a live-seat "
        "swap — resolve per request via canonical_db_reader()/reader_for():\n  "
        + "\n  ".join(offenders)
    )


def test_the_density_pin_survives():
    """CONTROL. The fix must not have "helpfully" routed the price chart through the
    live seat — that would swap a dense recorder for a sparse one and turn every candle
    into a flat doji. Being pinned is CORRECT here."""
    src = MARKET_PY.read_text()
    assert "BacktestingDBReader(settings.market_data_db)" in src, (
        "the price chart's density pin is gone; the candle chart will degrade to the "
        "live seat's ~1-tick/min sampling"
    )


# ── The dead trap ──────────────────────────────────────────────────────────

def test_the_dead_canonical_reader_is_gone():
    """Named `canonical_reader` while being the one reader that could NOT follow a
    swap, built bare so it silently carried the defined-risk capital formula (defect
    D2), and imported by nothing. A trap named like the truth."""
    tree = ast.parse(READERS_PY.read_text())
    assigned = {t.id for node in tree.body if isinstance(node, ast.Assign)
                for t in node.targets if isinstance(t, ast.Name)}
    assert "canonical_reader" not in assigned, (
        "the dead module-level canonical_reader is back"
    )


def test_the_live_resolver_it_was_confused_with_still_exists():
    """CONTROL — deleting the trap must not have taken the real one with it."""
    from dashboard.backend.services import variant_readers as vr
    assert callable(vr.canonical_db_reader)


def test_nothing_references_the_deleted_name():
    """AST, so the tombstone comment explaining the deletion does not match itself.

    The first draft of this tried to do it with string juggling
    (``"canonical_reader" in src and ... src.replace(..., 1)``) and was incoherent — it
    could not have failed for the right reason. Written out properly: a reference is an
    ``import canonical_reader``, a bare ``canonical_reader`` name, or an attribute access
    ``something.canonical_reader``. ``canonical_db_reader`` is a DIFFERENT name and the
    AST treats it as such, which is the whole reason string matching was the wrong tool.
    """
    offenders = []
    for path in sorted((ROOT / "dashboard").rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            hit = (
                (isinstance(node, ast.ImportFrom)
                 and any(a.name == "canonical_reader" for a in node.names))
                or (isinstance(node, ast.Name) and node.id == "canonical_reader")
                or (isinstance(node, ast.Attribute) and node.attr == "canonical_reader")
            )
            if hit:
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not offenders, f"something references the deleted canonical_reader: {offenders}"


# ── Make the staleness impossible to sustain quietly ───────────────────────

def _live_seat_from_taxonomy() -> str:
    live = [sid for sid in tax.ids_for_account_kind(tax.PAPER)
            if tax.meta(sid).status == "live"]
    assert len(live) == 1, f"taxonomy declares {len(live)} live paper seats: {live}"
    return live[0]


@pytest.mark.parametrize("field", [
    "bot_config_file", "hydra_state_file", "hydra_metrics_file",
    "backtesting_db", "hydra_log_file",
])
def test_dashboard_fallback_paths_track_the_live_seat(field):
    """THE TEST THAT STOPS THIS RECURRING.

    These five are last-resort fallbacks and are unreachable in normal operation — which
    is exactly why they rotted for two months without anyone noticing. Pinning them to
    the taxonomy's declared live seat converts the next swap from "something nobody
    updates" into a failing test that names the file and the field.

    If this fails after a deliberate swap, the fix is to update config.py — not to
    weaken the assertion.
    """
    from dashboard.backend.config import Settings

    seat = _live_seat_from_taxonomy()
    value = str(getattr(Settings.model_fields[field], "default"))
    assert f"variant_{seat}" in value, (
        f"config.py's fallback {field} = {value!r} does not name the live seat "
        f"{seat!r}. The live seat moved and this fallback was left behind — the exact "
        f"two-month drift this test exists to prevent."
    )


def test_the_fallback_label_names_the_live_seat():
    """Same rule for the header label, which a reader SEES."""
    from dashboard.backend.config import Settings

    seat = _live_seat_from_taxonomy()
    label = str(Settings.model_fields["primary_label"].default)
    assert label.strip().upper().startswith(seat.upper()), (
        f"primary_label {label!r} does not name the live seat {seat!r}"
    )


def test_the_density_pin_is_exempt_from_that_rule():
    """CONTROL, and the distinction the whole cleanup rests on: market_data_db must NOT
    track the live seat. If a future tidy-up "fixes" it to match, the price chart
    silently degrades."""
    from dashboard.backend.config import Settings

    seat = _live_seat_from_taxonomy()
    value = str(Settings.model_fields["market_data_db"].default)
    assert f"variant_{seat}" not in value, (
        "market_data_db now tracks the live seat — it must stay pinned to the DENSEST "
        "recorder, which is a sampling decision, not an account one"
    )


@pytest.mark.parametrize("var", [
    "DASHBOARD_HYDRA_STATE_FILE", "DASHBOARD_HYDRA_METRICS_FILE",
    "DASHBOARD_BACKTESTING_DB", "DASHBOARD_HYDRA_LOG_FILE",
])
def test_the_unit_does_not_re_pin_the_fallback_paths(var):
    """ONE source of truth, and it must be the one the tests can see.

    These four lived in THREE places — this unit (variant A), a VM-only drop-in
    (variant C), and config.py (something else again) — with systemd's drop-in
    precedence, not intent, picking the winner. An env var set in a unit file is
    invisible to every test in this repo, so any value here silently outranks the
    test-guarded defaults in config.py.
    """
    live = [l for l in (ROOT / "dashboard" / "deploy" / "dashboard.service").read_text()
            .splitlines() if l.strip() and not l.strip().startswith("#")]
    offenders = [l for l in live if var in l]
    assert not offenders, (
        f"{var} is set in the unit again, overriding config.py's test-guarded default "
        f"with a value no test can see: {offenders}"
    )


def test_config_block_does_not_contradict_itself():
    """The comment said C was live directly above a line saying the seat swapped to B.
    Whatever this block claims, it must not claim a seat the values disagree with."""
    src = (ROOT / "dashboard" / "backend" / "config.py").read_text()
    block = src[src.index("LAST-RESORT FALLBACKS"):src.index("position_registry_file")]
    assert "variant_c" not in block, (
        "the fallback block names variant_c again — C has been a dry-run shadow since "
        "2026-07-24"
    )
