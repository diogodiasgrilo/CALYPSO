"""
Make paper-vs-live a real switch, and make `is_paper` a verified fact
(2026-09-11). Combo-track step 4 — a prerequisite for any real-money cutover.

THE DEFECT. The environment was a HARDCODED `"paper"` literal at both call
sites (`bots/hydra/main.py` and `services/broker/main.py`), and
`IBClient.is_paper` was derived from that literal. So:

  * there was no way to run live without editing source, and
  * `is_paper` was a SELF-DECLARATION, not a fact — and it gated NOTHING.
    Re-encrypting live credentials under the same systemd credential IDs would
    have connected to a live account while `is_paper` still reported True.

TWO HALVES, and the second is the one that matters:
  1. `resolve_environment()` reads $CALYPSO_IBKR_ENV, defaulting to paper.
     That is still only a DECLARATION.
  2. `_assert_account_matches_env()` cross-checks the DISCOVERED account code
     against it. That is what turns the declaration into a verified fact.

IBKR's convention: PAPER codes start with "D" (DU, DUR, DF…); LIVE codes do not
(U######## individuals, F/I advisor structures). Verified against this account,
which is DUR + 6 digits.

THE ASYMMETRY IS DELIBERATE. Declared-paper-but-actually-live RAISES: that is
the direction that loses real money under a simulation assumption, and nothing
downstream can recover from it. Declared-live-but-actually-paper only WARNS:
annoying, harmless, and refusing would brick a go-live over a mis-set variable.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.ib_client import IBAuthError, IBClient  # noqa: E402
from shared.ib_oauth import (  # noqa: E402
    ENV_VAR,
    VALID_ENVIRONMENTS,
    resolve_environment,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)


class TestResolveEnvironment:
    def test_unset_defaults_to_paper(self):
        assert resolve_environment() == "paper"

    @pytest.mark.parametrize("blank", ["", "   ", "\t"])
    def test_blank_defaults_to_paper(self, monkeypatch, blank):
        """An empty variable must never be read as a selection."""
        monkeypatch.setenv(ENV_VAR, blank)
        assert resolve_environment() == "paper"

    @pytest.mark.parametrize("raw,expect", [
        ("paper", "paper"), ("live", "live"),
        ("PAPER", "paper"), ("Live", "live"), ("  live  ", "live"),
    ])
    def test_case_and_whitespace_tolerant(self, monkeypatch, raw, expect):
        monkeypatch.setenv(ENV_VAR, raw)
        assert resolve_environment() == expect

    @pytest.mark.parametrize("bad", ["prod", "production", "prod ", "real", "1", "true"])
    def test_an_unrecognised_value_RAISES_rather_than_guessing(self, monkeypatch, bad):
        """Falling back would be wrong in BOTH directions: silently choosing
        paper would make an operator think they were live and not be, and
        silently choosing live is obviously worse."""
        monkeypatch.setenv(ENV_VAR, bad)
        with pytest.raises(ValueError, match=ENV_VAR):
            resolve_environment()

    def test_live_is_never_the_default(self):
        assert resolve_environment(default="paper") == "paper"
        assert "live" in VALID_ENVIRONMENTS and "paper" in VALID_ENVIRONMENTS


def _client(declared):
    c = IBClient.__new__(IBClient)
    c.cfg = SimpleNamespace(credentials=SimpleNamespace(environment=declared))
    return c


class TestTheAccountCrossCheck:
    """The half that makes the declaration a fact."""

    @pytest.mark.parametrize("acct", ["DU1234567", "DUR123456", "DF7654321", "du1234567"])
    def test_paper_declared_and_paper_account_is_fine(self, acct):
        IBClient._assert_account_matches_env(_client("paper"), acct)

    @pytest.mark.parametrize("acct", ["U1234567", "U12345678", "F1234567", "I7654321"])
    def test_paper_declared_but_LIVE_account_RAISES(self, acct):
        """THE case this exists for — real money under a simulation assumption."""
        with pytest.raises(IBAuthError, match="ENV-ASSERT FAILED"):
            IBClient._assert_account_matches_env(_client("paper"), acct)

    def test_the_refusal_names_the_fix(self):
        """An operator reading this at 3am needs to know what to change."""
        with pytest.raises(IBAuthError, match=ENV_VAR):
            IBClient._assert_account_matches_env(_client("paper"), "U1234567")

    def test_live_declared_and_live_account_is_fine(self):
        IBClient._assert_account_matches_env(_client("live"), "U1234567")

    def test_live_declared_but_PAPER_account_only_WARNS(self, caplog):
        """Harmless direction: refusing would brick a go-live over a mis-set
        variable. Must warn, must NOT raise."""
        with caplog.at_level(logging.WARNING):
            IBClient._assert_account_matches_env(_client("live"), "DUR123456")
        # r.getMessage() applies the lazy-% args properly; r.message does not.
        assert any("looks like a PAPER account" in r.getMessage()
                   for r in caplog.records)

    def test_this_accounts_real_prefix_passes(self):
        """Regression guard against bricking startup: the live paper account is
        DUR + 6 digits. If the rule ever stops accepting it, every restart
        fails."""
        IBClient._assert_account_matches_env(_client("paper"), "DUR123456")


class TestItNeverTakesTheBotDownUnnecessarily:
    @pytest.mark.parametrize("acct", ["", None, "   "])
    def test_a_missing_account_warns_and_proceeds(self, acct, caplog):
        with caplog.at_level(logging.WARNING):
            IBClient._assert_account_matches_env(_client("paper"), acct)
        assert any("cannot verify" in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize("declared", [None, "", "sandbox"])
    def test_an_unknown_declaration_warns_and_proceeds(self, declared, caplog):
        with caplog.at_level(logging.WARNING):
            IBClient._assert_account_matches_env(_client(declared), "DUR123456")
        assert any("cannot verify" in r.getMessage() for r in caplog.records)

    def test_it_does_not_log_the_full_account_number(self, caplog):
        """The account id is not secret, but it is not something to scatter
        through logs either — only the prefix is needed to diagnose."""
        with caplog.at_level(logging.INFO):
            IBClient._assert_account_matches_env(_client("paper"), "DUR987654")
        assert not any("DUR987654" in r.getMessage() for r in caplog.records)


class TestTheCallSitesAreWired:
    """A resolver nothing calls is decoration."""

    @pytest.mark.parametrize("path", ["bots/hydra/main.py", "services/broker/main.py"])
    def test_no_hardcoded_paper_literal_remains(self, path):
        src = (Path(__file__).resolve().parents[1] / path).read_text()
        assert 'load_credentials("paper")' not in src
        assert "load_credentials(resolve_environment())" in src

    def test_discover_account_id_runs_the_assertion(self):
        import inspect
        assert "_assert_account_matches_env" in inspect.getsource(
            IBClient._discover_account_id)
