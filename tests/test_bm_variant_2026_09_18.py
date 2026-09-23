"""Variant BM — "B, money": the same strategy B runs, against the FUNDED account.

Added 2026-09-18 per docs/migration/LIVE_MONEY_ARCHITECTURE.md. Nothing here can trade
yet: the config ships ``dry_run=true`` and ``calypso-broker-live`` has no credentials to
start with. These tests pin the properties that must hold BEFORE either of those changes,
because after they change the cost of being wrong is real money.

THE DESIGN IN ONE LINE: one implementation, three configurations. BM reuses
``strategy_class="brandon"`` — the same registry entry as B and C — so the real-money path
cannot drift away from the one exercised on paper every day. A forked class would be the
obvious mistake here and is explicitly tested against.

WHY THE ID IS `bm` AND NOT `b_live`: in this codebase "the live seat" has always meant the
live PAPER seat, and ``live_seat_id()`` still resolves it. Naming the real-money variant
"live" anything would give one sentence two meanings — the conflation §3.2 exists to undo.
"bm" also fits the dashboard's letter badge, which renders ``id.toUpperCase()`` into a
one-to-two character space.

THE NON-OBVIOUS REQUIREMENT, found by a test fixture rather than by reading: the
dashboard's ``Settings`` is a pydantic model with ``extra`` disallowed, so a variant with
no ``variant_bm_*`` fields cannot be introduced by the taxonomy alone — and the failure is
SILENT in the dangerous direction. The seat resolver would simply skip the id and the
real-money variant would be invisible to ``live_money_seat_id()``, ``reader_for()``, the
WS broadcaster and the agent suite: exactly the §3.2 branch where the real-money bot is
never followed by anything.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared import strategy_taxonomy as tax  # noqa: E402

CFG = ROOT / "bots" / "hydra" / "config" / "config_variant_bm.json"
UNIT = ROOT / "deploy" / "hydra_variant_bm.service"
B_UNIT = ROOT / "deploy" / "hydra_variant_b.service"


def directives(path: Path) -> list[str]:
    return [l.strip() for l in path.read_text().splitlines()
            if l.strip() and not l.strip().startswith("#")]


def cfg() -> dict:
    return json.loads(CFG.read_text())


# ══════════════════════════════════════════════════════════════════════════════
# Taxonomy
# ══════════════════════════════════════════════════════════════════════════════

def test_bm_declares_real_money():
    assert tax.STRATEGIES["bm"].account_kind == tax.LIVE_MONEY


def test_bm_reuses_brandons_class():
    """One implementation, three configurations. A forked class would let the real-money
    path drift away from the one B exercises daily — the single most likely way for this
    variant to develop a bug nobody sees until it costs money."""
    assert tax.STRATEGIES["bm"].strategy_class == tax.STRATEGIES["b"].strategy_class == "brandon"


def test_bm_is_structurally_identical_to_b():
    """Same shape ⇒ same comparability group. The whole point is that paper-B and BM run
    identical signals, so the difference between them IS the execution drag."""
    b, bm = tax.STRATEGIES["b"], tax.STRATEGIES["bm"]
    for field in ("group_id", "structure_family", "pnl_shape", "dte_class",
                  "capital_basis", "sides"):
        assert getattr(bm, field) == getattr(b, field), f"{field} differs from B"


def test_bm_does_not_claim_the_live_paper_seat():
    """``status="live"`` means the live PAPER seat and is read as a tie-breaker by
    live_seat_id(). Claiming it here would make the real-money variant compete for a seat
    it has nothing to do with."""
    assert tax.STRATEGIES["bm"].status != "live"


def test_the_id_does_not_reuse_the_word_live():
    """Naming it b_live would give "the live seat" and "B-live" two different meanings in
    the same sentence. That is the conflation the account_kind axis exists to undo."""
    assert "live" not in "bm"
    assert tax.STRATEGIES["bm"].id == "bm"


def test_the_badge_still_fits():
    """The dashboard switcher renders id.toUpperCase() in a one-to-two character badge."""
    assert len(tax.STRATEGIES["bm"].id) <= 2


# ══════════════════════════════════════════════════════════════════════════════
# The config — what ships in git
# ══════════════════════════════════════════════════════════════════════════════

def test_it_ships_not_trading():
    """THE ONE THAT MATTERS MOST. A fresh checkout must not place real orders."""
    assert cfg()["dry_run"] is True


def test_it_ships_at_one_contract():
    """Gate 8: week one is 1 contract, so the purpose of the first deployment is
    learning, not earning."""
    assert cfg()["strategy"]["contracts_per_entry"] == 1


def test_real_money_may_never_trade_unalerted():
    """THE INVARIANT THAT OUTLIVES THIS COMMIT.

    Vacuously true today (dry_run=true), and deliberately written anyway: going live is a
    TWO-field change — dry_run false AND alerts.enabled true — and the second is exactly
    the kind of thing that gets forgotten. Flip one without the other and this fails by
    name.
    """
    c = cfg()
    if c["dry_run"] is False:
        assert c["alerts"]["enabled"] is True, (
            "config_variant_bm.json trades REAL MONEY with alerts disabled — every fill, "
            "stop and failure would be silent"
        )


def test_it_runs_the_same_strategy_stack_as_b():
    """Brandon must be enabled, or this is not "the same strategy B runs" at all."""
    assert cfg()["strategy"]["brandon"]["enabled"] is True


def test_sheets_stay_off():
    assert cfg()["google_sheets"]["enabled"] is False


# ══════════════════════════════════════════════════════════════════════════════
# The unit
# ══════════════════════════════════════════════════════════════════════════════

def test_it_points_at_the_live_broker():
    d = directives(UNIT)
    assert any("CALYPSO_BROKER_URL=http://127.0.0.1:8789" in l for l in d), \
        "BM does not point at the live broker"
    assert not any("8788" in l for l in d), \
        "BM can still reach the PAPER broker — that is a different account"


def test_it_depends_on_the_live_broker_not_the_paper_one():
    d = directives(UNIT)
    assert any(re.match(r"^(Wants|After)=calypso-broker-live\.service$", l) for l in d)
    assert not any(re.match(r"^(Wants|After)=calypso-broker\.service$", l) for l in d), (
        "BM is ordered behind the PAPER broker, which holds a different account"
    )


def test_it_carries_no_oauth_credentials():
    """Deliberate, and stronger than tidiness. PAPER credentials here would be actively
    wrong (the direct branch would trade paper while declaring live_money, which
    _assert_account_matches_env only WARNS about). LIVE credentials would put decrypted
    real-money keys in an eighth process with no use for them.

    With neither, a missing CALYPSO_BROKER_URL fails CLOSED.
    """
    assert not [l for l in directives(UNIT) if l.startswith("LoadCredentialEncrypted=")]


def test_its_data_paths_do_not_collide_with_bs():
    """Isolation is by FILE PATH. A shared state file or DB would mean the real-money
    variant writing into the paper seat's record."""
    bm = [l for l in directives(UNIT) if "variant_b" in l]
    wrong = [l for l in bm if "variant_bm" not in l]
    assert not wrong, f"BM writes into variant_b's paths: {wrong}"


def test_it_runs_its_own_config():
    assert any("config_variant_bm.json" in l for l in directives(UNIT) if l.startswith("ExecStart="))


def test_it_has_its_own_variant_id_and_identifier():
    d = directives(UNIT)
    assert any(l == 'Environment="HYDRA_VARIANT_ID=bm"' for l in d)
    assert any(l == "SyslogIdentifier=hydra_variant_bm" for l in d)


@pytest.mark.parametrize("directive", [
    "NoNewPrivileges=yes", "PrivateTmp=yes", "ProtectSystem=strict",
    "User=calypso", "Restart=always",
])
def test_it_keeps_the_fleet_hardening(directive):
    assert directive in directives(UNIT)


def test_the_paper_variant_was_not_touched():
    """CONTROL. Adding BM must not have re-pointed B — B is the live paper seat and is
    trading right now."""
    d = directives(B_UNIT)
    assert any("CALYPSO_BROKER_URL=http://127.0.0.1:8788" in l for l in d)
    assert any(l == 'Environment="HYDRA_VARIANT_ID=b"' for l in d)


# ══════════════════════════════════════════════════════════════════════════════
# Dashboard wiring — the silent-failure one
# ══════════════════════════════════════════════════════════════════════════════

def test_the_dashboard_has_every_settings_field_bm_needs():
    """DERIVED, not hand-listed — and that distinction is why this test exists.

    The first version enumerated six field names by hand. It passed, and CI broke
    minutes later on the SEVENTH: `variant_bm_baseline_date`, which
    scripts/make_synthetic_fixtures.py assigns for every taxonomy variant. A hand-written
    list can only ever check what its author already thought of, which is precisely the
    failure mode being guarded against.

    The authoritative requirement is "whatever an established variant has", so the set is
    read off variant B. Add a field to B and this fails until bm gets it too.

    Why it matters: pydantic's `extra` ban means a missing field is not a soft default —
    `setattr` RAISES. Downstream, the seat resolver silently SKIPS the id instead, so the
    real-money variant becomes invisible to live_money_seat_id(), reader_for(), the WS
    broadcaster and the agent suite.
    """
    from dashboard.backend.config import Settings

    def suffixes(vid):
        pre = f"variant_{vid}_"
        return {n[len(pre):] for n in Settings.model_fields if n.startswith(pre)}

    missing = suffixes("b") - suffixes("bm")
    assert not missing, (
        f"variant_bm_* is missing field(s) that variant_b_* has: {sorted(missing)}. "
        f"pydantic's extra-ban makes each of these a hard ValueError at setattr, not a "
        f"default."
    )


def test_EVERY_taxonomy_variant_has_the_settings_fields_it_needs():
    """The same lesson, one level up — and it had to be learned twice.

    The test above is DERIVED over field names but HARD-CODED to `bm`. So when
    variant **h** was added on 2026-09-23 it was never checked, and
    `variant_h_baseline_date` was missing: CI's fixture generator broke on
    EVERY push from 10:31 UTC onward, 14 runs, while the local suite stayed
    green because nothing local iterates the taxonomy this way.

    A guard that names the variant it guards can only ever protect that one
    variant, which is exactly the failure it was written to prevent. This one
    iterates the taxonomy, so the NEXT variant is covered before it is written.
    """
    from dashboard.backend.config import Settings
    import shared.strategy_taxonomy as tax

    def suffixes(vid):
        pre = f"variant_{vid}_"
        return {n[len(pre):] for n in Settings.model_fields if n.startswith(pre)}

    reference = suffixes("b")
    assert reference, "variant_b_* has no fields — the reference is wrong"

    gaps = {vid: sorted(reference - suffixes(vid))
            for vid in tax.available_ids()
            if vid != "b" and (reference - suffixes(vid))}
    assert not gaps, (
        f"dashboard Settings is missing variant fields: {gaps}. Every taxonomy "
        f"variant needs what variant_b_* has — scripts/make_synthetic_fixtures.py "
        f"assigns them for ALL variants and pydantic's extra-ban makes each "
        f"omission a hard ValueError at setattr, not a default."
    )


def test_the_dashboard_paths_are_bm_specific():
    from dashboard.backend.config import Settings
    for field in ("variant_bm_state_file", "variant_bm_backtesting_db",
                  "variant_bm_log_file", "variant_bm_config_file"):
        v = str(Settings.model_fields[field].default)
        assert "_bm" in v, f"{field} = {v} does not point at bm's own path"


def test_bm_is_not_the_paper_seat():
    """THE §3.2 REGRESSION TEST, now against the REAL taxonomy rather than a synthetic
    row. Adding a real-money variant must not move the paper seat."""
    from dashboard.backend.services.variant_readers import live_seat_id
    assert live_seat_id() != "bm"


def test_there_is_still_no_real_money_seat_trading():
    """bm ships dry_run=true, so live_money_seat_id() must answer None. When this starts
    returning "bm", real money is being traded — which should never happen as a side
    effect of a code change."""
    from dashboard.backend.services.variant_readers import live_money_seat_id
    assert live_money_seat_id() is None


def test_the_meta_api_marks_it_as_real_money():
    """The UI must be able to tell. Without this the switcher would present a
    real-money variant with the same affordances as a paper one."""
    from dashboard.backend.routers.strategies import _strategy_meta_dict
    d = _strategy_meta_dict(tax.STRATEGIES["bm"])
    assert d["account_kind"] == tax.LIVE_MONEY
    assert d["subtitle"], "bm has no spec line in the switcher"
