"""Strike + sizing selection for Strategy H (0DTE long strangle) — Playbook Step 3.

Pure, broker-free helpers (stdlib only). The playbook requires selection logic to
live in a module like this so it is unit-testable without a broker or a live
clock; the caller passes every input explicitly.

**Nothing here has been confirmed against live data yet.** Step 3's other half is
a read-only VM probe during market hours, and the playbook is blunt about why:
D's offline tests were green while the live probe caught SPXW expiry gaps and a
missing IV field. Treat every assumption below as unverified until that probe
runs. The two most likely to break are marked ⚠️.

THE EXPECTED MOVE — TWO DEFINITIONS, AND THEY DISAGREE
-------------------------------------------------------
This is the open question from the spec, and it is not cosmetic: the expected
move IS the strike choice, so picking the wrong definition builds a different
strategy.

* ``expected_move_from_straddle`` — the ATM straddle's price. This is what the
  source means by "the market's expected move": Tompkins reads it off the option
  chain and adds/subtracts it from spot. **Source-faithful.**
* ``expected_move_from_vix`` — ``spot × (vix/100) / sqrt(252)``, the one-day move
  implied by a 30-day annualised vol. This is what variant F already computes
  (``ghauri_strategy.py``), so it is the one with live precedent here.

They are not interchangeable. The straddle prices *this* expiry's actual demand,
including event premium and the 0DTE smile; the VIX formula prices a 30-day
annualised vol scaled down by a constant. On a quiet day they can land close; on
an event day the straddle is typically much wider. Both are implemented, the
choice is config-driven (``long_strangle.expected_move_source``), and the
intended default is ``straddle`` because it is what the strategy actually
describes.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
No stop-distance helper, because the strategy has no stop: max loss is the debit.
No margin/buying-power helper, because risk is known before entry.
"""

from __future__ import annotations

from math import sqrt
from typing import Iterable, Optional, Sequence

# Re-exported so H's callers and tests keep importing these from here, while
# the definition lives in ONE place shared with variant E — both sources ask
# the same question ('IV percentile', 'the lower end of the spectrum') and two
# implementations would be free to drift on arithmetic rather than on config.
from bots.hydra.iv_percentile import (  # noqa: F401
    iv_percentile,
    iv_percentile_with_n,
)

#: Trading days per year — the annualisation constant in the VIX-implied move.
#: Matches ``ghauri_strategy.py`` exactly so F and H cannot silently disagree.
TRADING_DAYS_PER_YEAR = 252


def expected_move_from_straddle(atm_call_price: float, atm_put_price: float) -> float:
    """The market's own one-day expected move: the ATM straddle's price.

    Source-faithful. Tompkins reads the expected move off the chain rather than
    deriving it, so this is the definition the strategy is actually written
    around.

    ⚠️ **Unverified assumption:** that the straddle price is a usable proxy for
    the expected move at 0DTE. It is the standard retail approximation (and is
    what the source does), but it systematically *overstates* the one-standard-
    deviation move — the common correction is ~0.85×. No correction is applied
    here: the source does not apply one, and inventing a fudge factor before the
    probe would be fitting to nothing.

    Returns 0.0 on non-positive inputs rather than propagating a bad quote into
    strike selection — a missing leg price must not silently become a tiny
    expected move and therefore near-the-money strikes.
    """
    if atm_call_price <= 0 or atm_put_price <= 0:
        return 0.0
    return atm_call_price + atm_put_price


def expected_move_from_vix(spot: float, vix: float,
                           multiplier: float = 1.0) -> float:
    """The one-day move implied by VIX: ``spot × (vix/100) / sqrt(252)``.

    Identical to variant F's calculation, deliberately — if H and F ever disagree
    about "the expected move" on the same session that should be a config
    difference, never an arithmetic one.

    ``multiplier`` mirrors F's ``ghauri_em_multiplier`` so a caller can widen or
    tighten the boundary without editing the formula.
    """
    if spot <= 0 or vix <= 0:
        return 0.0
    return spot * (vix / 100.0) / sqrt(TRADING_DAYS_PER_YEAR) * multiplier


def chain_snap_cap(strikes: Sequence[float], spot: float,
                   multiple: float = 2.5,
                   fallback: float = 25.0) -> float:
    """A snap tolerance derived from the chain's OWN near-the-money spacing.

    **This exists because a hardcoded tolerance is an instrument assumption in
    disguise.** H shipped ``MAX_SNAP_DISTANCE = 25.0``, which is half the widest
    far-OTM *SPXW* gap and entirely sensible on SPX, where strikes near the money
    sit 5pt apart. Point the same constant at SPY — whose near-the-money strikes
    are **$1** apart — and it authorises snapping **twenty-five strikes**. The
    entry would still be placed, still be logged, and still look ordinary; it
    would simply be a different position from the one the strategy chose.

    So the tolerance is measured instead: take the median gap between strikes
    bracketing the spot and allow a small multiple of it. SPX (5pt) yields 12.5,
    SPY ($1) yields 2.5, and an instrument nobody has tried yet yields whatever
    its own chain says. ``fallback`` covers a chain too sparse to measure.
    """
    near = sorted(s for s in (float(k) for k in strikes if k)
                  if abs(s - spot) <= max(abs(spot) * 0.02, 1e-9))
    gaps = sorted(round(b - a, 6) for a, b in zip(near, near[1:]) if b > a)
    if not gaps:
        return fallback
    median = gaps[len(gaps) // 2]
    return median * multiple if median > 0 else fallback


def snap_to_chain(target: float, strikes: Iterable[float],
                  max_distance: Optional[float] = None) -> Optional[float]:
    """Nearest listed strike to ``target``, or None if none is close enough.

    ``max_distance`` guards against a sparse or partially-loaded chain silently
    returning something far away: with a 25pt cap, a target of 7825 will not
    quietly resolve to 7700 because the nearer strikes were missing from the
    snapshot. Returning None lets the caller skip the entry, which is the correct
    response to a chain it cannot trust.
    """
    candidates = [float(s) for s in strikes if s]
    if not candidates:
        return None
    best = min(candidates, key=lambda s: abs(s - target))
    if max_distance is not None and abs(best - target) > max_distance:
        return None
    return best


def select_strangle_strikes(spot: float, expected_move: float,
                            strikes: Sequence[float],
                            max_snap_distance: Optional[float] = 25.0):
    """The strategy's strike rule: ``spot ± expected_move``, snapped to the chain.

    Returns ``(call_strike, put_strike)``, either of which may be None when the
    chain cannot supply a strike within ``max_snap_distance``. The caller must
    treat a None on EITHER side as "no entry" — a long strangle with one leg is
    a directional bet, which is not this strategy.

    Refuses a non-positive expected move outright: ``spot ± 0`` is an ATM
    straddle, a materially different (and far more expensive) position than the
    OTM strangle described here.
    """
    if spot <= 0 or expected_move <= 0:
        return None, None
    call = snap_to_chain(spot + expected_move, strikes, max_snap_distance)
    put = snap_to_chain(spot - expected_move, strikes, max_snap_distance)
    return call, put


def premiums_are_balanced(call_premium: float, put_premium: float,
                          tolerance_pct: float = 35.0) -> bool:
    """The source's skew check: are both sides' premiums "reasonably similar"?

    Because of put skew, equidistant strikes are not equally priced — the put is
    usually dearer. A large imbalance means the "strangle" is really a
    directional position wearing two legs, which defeats the point of being
    non-directional.

    ⚠️ **``tolerance_pct`` is a guess.** The source says "reasonably similar" and
    never quantifies it. 35% is a starting value chosen to be loose enough not to
    veto ordinary index skew and tight enough to catch a genuinely lopsided pair;
    it is config-exposed so the probe and dry-run data can correct it. Do not
    treat this number as derived from anything.

    Measured against the LARGER premium, so the result is symmetric — swapping
    the arguments cannot change the answer.
    """
    if call_premium <= 0 or put_premium <= 0:
        return False
    larger = max(call_premium, put_premium)
    return abs(call_premium - put_premium) / larger * 100.0 <= tolerance_pct


def size_for_zero(max_acceptable_loss: float, debit_per_contract: float) -> int:
    """"Sizing for zero": how many contracts, assuming the whole debit is lost.

    The source's sizing rule, and the only one that makes sense for a position
    whose maximum loss is its cost: choose what you can afford to lose outright,
    then buy that many.

    Floors at 0 rather than 1. A caller that cannot afford a single contract must
    place nothing — silently rounding up to one contract would breach the very
    limit this function exists to enforce.
    """
    if max_acceptable_loss <= 0 or debit_per_contract <= 0:
        return 0
    return int(max_acceptable_loss // debit_per_contract)


