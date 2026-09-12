"""
The combo what-if probe must be incapable of placing an order (2026-09-11).

This is the only script in the repo that deliberately touches the order
subsystem on the live-paper account, so the tests that matter most are the ones
asserting what it CANNOT do.

WHY whatif IS SAFE, verified rather than assumed: ibind's `whatif_order` posts to
`iserver/account/{id}/orders/whatif` — a DISTINCT endpoint from
`iserver/account/{id}/orders` — with no `answers` parameter and no reply loop.
There is no code path from it to a placement.

WHY IT GOES THROUGH THE BROKER: a probe building its own IBClient would open a
SECOND IBKR session and evict calypso-broker's, since IBKR allows one brokerage
session per username. That is the exact failure the broker service exists to
prevent, so `what_if_order` was allowlisted for RPC instead.

AN EMPTY BLOCK IS INCONCLUSIVE, NEVER A PASS. IBKR returns an empty whatif when
the `/iserver/accounts` preflight has not run or the legs were not snapshot
first. Reading that as "fine" is how an untested assumption becomes a stated
fact — which is what happened with the executions endpoint.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import probe_combo_whatif as P  # noqa: E402
from shared.broker_service import ALLOWED_METHODS  # noqa: E402

SRC = (Path(__file__).resolve().parents[1] / "scripts" / "probe_combo_whatif.py").read_text()


class TestItCannotPlaceAnOrder:
    """The assertions that actually matter."""

    @pytest.mark.parametrize("method", [
        "place_order", "place_and_wait_for_fill", "place_iron_condor",
        "place_vertical_spread", "modify_order", "cancel_order",
    ])
    def test_the_rpc_allowlist_refuses_every_write_method(self, method):
        with pytest.raises(RuntimeError, match="read-only"):
            P.rpc(method)

    @pytest.mark.parametrize("method", [
        "place_order", "place_and_wait_for_fill", "place_iron_condor",
        "place_vertical_spread", "modify_order",
    ])
    def test_no_write_method_is_even_named_in_the_source(self, method):
        """Belt and braces: the allowlist is enforced at runtime, and the name
        does not appear at all, so a careless edit cannot reintroduce one by
        uncommenting something."""
        assert method not in SRC

    def test_the_permitted_set_is_read_only(self):
        assert P._PERMITTED == frozenset({
            "qualify_contract", "qualify_option_strikes", "get_option_chain",
            "get_quote", "get_quotes_batch", "what_if_order",
        })

    def test_every_permitted_method_is_actually_allowlisted_on_the_broker(self):
        """A probe calling a method the broker rejects fails confusingly at
        runtime instead of at review time."""
        assert P._PERMITTED <= set(ALLOWED_METHODS)

    def test_whatif_is_allowlisted(self):
        assert "what_if_order" in ALLOWED_METHODS


class TestItCallsTheRealSignatures:
    """The first live run failed because the probe passed `sec_type=` to
    get_quote, which does not accept it — and get_quote takes a CONID, not a
    symbol. Reading the signature would have caught it; so will this."""

    def test_get_quote_is_called_with_a_conid_only(self):
        import inspect
        from shared.ib_client import IBClient
        params = set(inspect.signature(IBClient.get_quote).parameters)
        assert "sec_type" not in params, "get_quote has no sec_type"
        assert "conid" in params
        assert "sec_type=\"IND\"" not in SRC.split("get_quote")[1][:80]

    def test_qualify_contract_accepts_what_the_probe_passes(self):
        import inspect
        from shared.ib_client import IBClient
        params = set(inspect.signature(IBClient.qualify_contract).parameters)
        for kw in ("sec_type", "exchange", "strike", "right", "trading_class"):
            assert kw in params, f"qualify_contract has no {kw}"

    def test_the_index_is_qualified_before_it_is_quoted(self):
        assert SRC.index("qualify_contract") < SRC.index("get_quote")

    def test_the_preview_order_mirrors_place_iron_condor(self):
        """The probe's whole purpose is previewing the EXACT ticket the real
        path builds. The first live run omitted sec_type='BAG' and IBKR answered
        400 'Unknown order type' — a combo is not identified by its conidex
        alone."""
        import inspect
        from shared.ib_client import IBClient
        real = inspect.getsource(IBClient.place_iron_condor)
        for field in ('sec_type="BAG"', 'order_type="LMT"', 'side="SELL"'):
            assert field in real, f"place_iron_condor no longer sets {field}"
        # snake_case again: broker_service rebuilds the OrderRequest at the RPC
        # boundary (2026-09-11), so the probe no longer has to hand-map to
        # camelCase to avoid IBKR's 400 "Unknown order type".
        assert '"sec_type": "BAG"' in SRC
        assert '"order_type": "LMT"' in SRC

    def test_conid_is_OMITTED_not_null(self):
        """Over JSON, conid=None becomes `null` and ibind reads the key's
        presence as "provided" -> "Both 'conidex' and 'conid' are provided".
        The direct Python path gets a genuine absence from the dataclass
        default; the RPC path must omit the key."""
        assert '"conid"' not in SRC

    def test_the_preview_sends_no_coid(self):
        """A preview needs no server-side dedup key, and reusing one could
        collide with a real order's id."""
        assert '"coid"' not in SRC


class TestEmptyIsInconclusiveNeverAPass:
    def test_an_empty_block_is_not_ok(self, monkeypatch):
        monkeypatch.setattr(P, "rpc", lambda *a, **k: {})
        r = P.preview("28812380;;;1/-1,2/1", "SELL", 1.25, 1, "t")
        assert r["ok"] is False
        assert r["reason"] == "empty"

    def test_a_none_result_is_not_ok(self, monkeypatch):
        monkeypatch.setattr(P, "rpc", lambda *a, **k: None)
        assert P.preview("x", "SELL", 1.0, 1, "t")["ok"] is False

    def test_a_rejection_is_not_ok(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("combo not supported")
        monkeypatch.setattr(P, "rpc", boom)
        r = P.preview("x", "SELL", 1.0, 1, "t")
        assert r["ok"] is False and "not supported" in r["reason"]

    def test_a_populated_block_IS_ok(self, monkeypatch):
        monkeypatch.setattr(P, "rpc", lambda *a, **k: {
            "initial": {"change": "500.00"}, "amount": {"amount": "1.25 USD"}})
        r = P.preview("x", "SELL", 1.25, 1, "t")
        assert r["ok"] is True
        assert r["initial_change"] == pytest.approx(500.0)


class TestMoneyParsing:
    """IBKR returns margin as display strings, not numbers."""

    @pytest.mark.parametrize("raw,want", [
        ("500", 500.0), ("+4,500.00", 4500.0), ("1.25 USD", 1.25),
        ("-250.50", -250.5), ("107,150.00", 107150.0),
    ])
    def test_it_parses_ibkrs_display_strings(self, raw, want):
        assert P._money(raw) == pytest.approx(want)

    @pytest.mark.parametrize("raw", [None, "", "n/a", "--"])
    def test_unparseable_returns_None_not_zero(self, raw):
        """Zero would read as 'no margin required' — the most dangerous possible
        misreading of a margin preview."""
        assert P._money(raw) is None


class TestTheConidexItPreviews:
    def test_it_probes_BOTH_the_bare_and_CBOE_routed_forms(self):
        """The routing question is the whole point of probing two variants —
        see COMBO_ENTRY_LIVE_CUTOVER_PLAN.md §0-bis."""
        assert "@CBOE" in SRC
        assert "SPREAD_TEMPLATE_CONID};;;" in SRC

    def test_the_leg_ratios_encode_a_SHORT_iron_condor(self):
        """shorts sold (-1), longs bought (+1). Inverting these previews the
        opposite trade and would make a credit look like a debit."""
        assert "/-1,{conids['lc']}/1," in SRC.replace('f"', '"')
        assert "sc']}/-1" in SRC and "lp']}/1" in SRC

    def test_it_refuses_to_run_without_a_live_quote(self):
        """A margin preview off a stale overnight quote is not evidence."""
        assert "Run this during market hours" in SRC


class TestTheVerdictIsHonest:
    def test_it_says_a_preview_is_not_a_fill(self):
        """The single most important caveat: acceptance != atomicity, and
        atomicity is venue-level and only observable live."""
        assert "an accepted PREVIEW is not an atomic FILL" in SRC

    def test_it_compares_against_the_defined_risk_figure(self):
        assert "defined_risk = a.width * 100 * a.quantity" in SRC
