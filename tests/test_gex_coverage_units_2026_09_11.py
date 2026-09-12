"""
The GEX coverage log mixed contracts with strikes (2026-09-11).

    dropped = chain_total - contributed
              ^contracts     ^STRIKES

On a real refresh — 618 contracts in the chain, 221 hydrated, 145 strikes
contributing — it reported `dropped=473`. That is neither the contracts that
failed to hydrate (618 − 221 = 397) nor the strikes that did not contribute. It
was simply a subtraction of two different units that happened to produce a
plausible-looking number, overstating the loss by about 19%.

WHY IT MATTERS MORE THAN A COSMETIC LOG ERROR. This line exists SPECIFICALLY to
make the hydration coverage gap visible, added after the 2026-09-01 incident
where the hydration cap silently excluded 59% of near-the-money open interest and
nobody could see it. A number that is wrong in a plausible direction defeats the
entire purpose of the instrument — the same failure as the fill-quality tool
measuring spread capture and calling it execution quality.

A chain carries roughly two contracts per strike (a call and a put), so the two
counts are never directly comparable and must be reported separately.
"""

from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.brandon.strategy import BrandonHydraStrategy  # noqa: E402

SRC = inspect.getsource(BrandonHydraStrategy)


class TestTheUnitsBugIsGone:
    def test_the_mixed_unit_subtraction_is_removed(self):
        """The exact defect: a STRIKE count subtracted from a CONTRACT count."""
        assert "dropped = chain_total - contributed" not in SRC

    def test_not_hydrated_is_contracts_minus_contracts(self):
        assert "not_hydrated = chain_total - with_greeks_or_iv" in SRC

    def test_the_log_labels_the_units(self):
        """An unlabelled count next to a differently-scaled one is how the bug
        survived: both looked like 'number of things'."""
        assert "chain=%d contracts" in SRC
        assert "not_hydrated=%d contracts" in SRC
        assert "%d strikes contributed" in SRC


class TestTheArithmeticOnRealNumbers:
    """The observed refresh: chain=618, hydrated=221, strikes contributing=145."""

    CHAIN, HYDRATED, STRIKES = 618, 221, 145

    def test_the_old_formula_produced_the_wrong_number(self):
        assert self.CHAIN - self.STRIKES == 473          # what was logged
        assert self.CHAIN - self.HYDRATED == 397         # what it should be
        assert 473 != 397

    def test_it_overstated_the_loss(self):
        """Wrong in the direction that makes coverage look WORSE than it is —
        which would have prompted a cap increase that was not needed."""
        old, new = self.CHAIN - self.STRIKES, self.CHAIN - self.HYDRATED
        assert old > new
        assert (old - new) / new > 0.15

    def test_hydrated_plus_not_hydrated_equals_the_chain(self):
        """The invariant the corrected figure satisfies and the old one did not:
        every contract is either hydrated or it is not."""
        not_hydrated = self.CHAIN - self.HYDRATED
        assert self.HYDRATED + not_hydrated == self.CHAIN
        assert self.HYDRATED + (self.CHAIN - self.STRIKES) != self.CHAIN

    def test_strikes_are_roughly_half_the_contracts(self):
        """A call and a put per strike — which is why the two counts can never
        be subtracted from one another."""
        assert self.STRIKES < self.CHAIN / 2


class TestTheCapReportingStillWorks:
    """The cap-binding alarm from the 2026-09-01 fix must survive this change —
    it is the other half of the same instrument."""

    def test_candidates_found_is_still_reported(self):
        assert "candidates_found=%d" in SRC

    def test_the_cap_is_still_reported(self):
        assert "hydrate_cap=%d" in SRC

    def test_the_cap_binding_alarm_still_exists(self):
        assert "brandon_gex_max_contracts_to_hydrate" in SRC

    def test_candidates_and_hydrated_are_comparable_units(self):
        """Both are CONTRACT counts, so `candidates_found > hydrate_cap` — the
        binding test — is a valid comparison. This is the check that must not
        become a units mix in future."""
        assert re.search(r"candidates_found\s*>\s*self\.brandon_gex_max_contracts_to_hydrate", SRC)
