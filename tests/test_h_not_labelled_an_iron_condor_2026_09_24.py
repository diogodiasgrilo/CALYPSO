"""A long strangle must not be STORED as an iron condor.

Seen live on H's first real entry (2026-09-24 09:45 ET):

    Entry #1 [NEUTRAL] recorded: SPX=764.69, Credit=$0.00,
    Type=Iron Condor, Strikes: C:0.0/767.0 P:0.0/762.0

Three things wrong in one line, and it is a PERSISTED row rather than screen
chrome: the type, the four-strike layout whose short legs read ``0.0`` because
H has none, and ``[NEUTRAL]`` for a position that is the opposite of neutral —
it is long gamma and wants a big move.

The shared recording path in ``strategy.py`` falls through to its IC defaults
for any entry that does not declare a ``structure``. G set one ("strangle") for
exactly this reason. H never did, so it inherited the IC labels — and
``structure`` also feeds the DB's ``entry_type`` column, which is how HERMES,
HOMER and CLIO bucket a trade.

A/B/C set no ``structure`` at all, so their labels are untouched.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bots.hydra.long_strangle_entry import LongStrangleEntry  # noqa: E402
from bots.hydra.strategy import HydraIronCondorEntry  # noqa: E402

STRATEGY_SRC = (ROOT / "bots" / "hydra" / "strategy.py").read_text()


class TestHDeclaresWhatItIs:

    def test_the_entry_carries_a_structure(self):
        assert LongStrangleEntry(entry_number=1).structure == "long_strangle"

    def test_an_ordinary_IC_entry_still_declares_nothing(self):
        """The fall-through must stay the default for A/B/C, or their labels
        change too."""
        assert getattr(HydraIronCondorEntry(entry_number=1), "structure", None) is None

    def test_it_reaches_the_DB_entry_type_column(self):
        """`structure` feeds the recorded entry_type, which is how HERMES,
        HOMER and CLIO bucket a trade — a long strangle filed as full_ic would
        be averaged into the IC population."""
        i = STRATEGY_SRC.index('"entry_type": (')
        assert 'getattr(entry, "structure", None)' in STRATEGY_SRC[i:i + 220]


class TestTheLabelsFollowTheStructure:

    def test_the_recording_path_has_a_long_strangle_branch(self):
        assert 'entry_type = "Long Strangle"' in STRATEGY_SRC

    def test_the_branch_runs_BEFORE_the_IC_fallthrough(self):
        i_ls = STRATEGY_SRC.index('trend_tag = "[LONG-GAMMA]"')
        i_ic = STRATEGY_SRC.index('entry_type = "Iron Condor"')
        assert i_ls < i_ic, "the IC branch would swallow it"

    def test_the_strike_string_shows_TWO_legs_not_four(self):
        """`C:0.0/767.0` renders an absent short strike as a real 0.0 strike."""
        i = STRATEGY_SRC.index('trend_tag = "[LONG-GAMMA]"')
        block = STRATEGY_SRC[i - 400:i]
        assert "f\"C:{entry.long_call_strike} P:{entry.long_put_strike}\"" in block
        assert "short_call_strike" not in block

    def test_it_is_not_called_NEUTRAL(self):
        """Long gamma is the opposite of neutral — it needs a big move.

        Anchored on the RECORDING branch specifically: the first
        ``entry_type = "Long Strangle"`` in the file is the Telegram label,
        which carries no trend tag, so a naive index() checks the wrong site.
        """
        i = STRATEGY_SRC.index('trend_tag = "[LONG-GAMMA]"')
        assert 'entry_type = "Long Strangle"' in STRATEGY_SRC[i - 200:i]

    def test_the_telegram_label_is_also_fixed(self):
        assert 'entry_type = "Long Strangle"   # bought premium' in STRATEGY_SRC

    def test_G_s_strangle_label_is_untouched(self):
        """A second structure must not shadow the first."""
        assert 'entry_type = "Strangle"' in STRATEGY_SRC
        i_h = STRATEGY_SRC.index('if getattr(entry, \'structure\', None) == "long_strangle"')
        i_g = STRATEGY_SRC.index('elif getattr(entry, \'structure\', None) == "strangle"')
        assert i_h < i_g

    @pytest.mark.parametrize("bad", ["Iron Condor", "full_ic"])
    def test_a_long_strangle_never_resolves_to_an_IC_type(self, bad):
        """The end-to-end property, replicating the recorder's own expression."""
        e = LongStrangleEntry(entry_number=1)
        resolved = (getattr(e, "structure", None)
                    or ("call_only" if e.call_only else
                        ("put_only" if e.put_only else "full_ic")))
        assert resolved != bad
