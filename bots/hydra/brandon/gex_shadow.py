"""SHADOW evaluation of the Brandon GEX accel-zone gate (2026-09-05).

NOTHING IN THIS MODULE AFFECTS TRADING. It recomputes what the accel-zone
gate WOULD have decided under corrected readings, alongside the live
decision, so the corrections can eventually be adopted on recorded evidence
instead of on argument. The live path keeps using the live gate.

WHY THIS EXISTS — the 2026-09-04 gate audit found five defects, of which
three change which entries get placed and therefore must not be flipped
blind on a live seat:

  1. WIDTH — a run of ONE strike qualified as a "gamma wall" (its
     strike_low == strike_high == peak_strike, so the peak-locality test is
     satisfied trivially). On the live 2026-09-04 profile the only negative
     cluster clearing the 0.10 threshold was a single strike at 16.94%, and
     it was the sole source of every put-side overlay confirmation on
     record. Shadowed as `width_floor` (min_cluster_strikes=2).

  2. NORMALIZATION — cluster strength is scored against the |GEX| of the
     WHOLE chain. On a 0DTE book that inverts the threshold's meaning: the
     genuine 345pt call wing scored 9.25% and failed the 0.10 gate while one
     ATM strike scored 16.94% and passed. Shadowed as `windowed`
     (normalization base restricted to spot +/- SHADOW_WINDOW_PTS).

  3. SIGN CONVENTION — this codebase assumes dealers short calls / long
     puts, the inverse of published SpotGamma. That places essentially all
     "accel" clusters ABOVE spot (live: 76 pos / 1 neg below spot vs 57 neg
     / 7 pos above), leaving the put branch near-blind — 0 confirmations in
     843 watch ticks inside 25pt, while all three real put-side stop-losses
     in the same window went undefended. Shadowed as `flipped_sign`.

  `all_fixes` applies all three together, which is the candidate
  replacement gate.

ALSO SHADOWED: the PREDICATE DIVERGENCE (audit BUG 4). The strike adjuster
asks "does a cluster CONTAIN the proposed short?" while the defensive
overlay asks "is there a cluster entirely BEYOND spot on this side?" Those
are different questions, and the divergence is invisible in production
today. Every verdict below reports BOTH predicates so the disagreement rate
is measurable.

DELIBERATELY NOT SHADOWED: the peak-persistence gate. It needs a prior
independent profile and its own force_unconfirmed semantics; folding that in
would make a disagreement uninterpretable (was it the fix, or the prior
read?). Shadow verdicts therefore answer "would a cluster have qualified
here", not "would a SKIP have fired end-to-end". Persistence only ever
SUPPRESSES a skip, so a shadow verdict of False is conclusive (no skip
possible) while True is an upper bound.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from bots.hydra.brandon.gex_provider import GEXCluster, GEXProfile

# Normalization half-width for the `windowed` correction. 100pt on SPX is
# ~1.3% of spot at 7700 — wide enough to contain both condor wings at the
# 5-10pt widths B/C trade plus the near-money gamma that actually drives
# 0DTE hedging flow, narrow enough to exclude the deep-tail strikes whose
# aggregate mass is what currently swamps the denominator.
SHADOW_WINDOW_PTS = 100.0

# Minimum contiguous strikes for a run to count as a wall under the width
# correction. 2 is the least aggressive value that still excludes the
# single-strike artifact; the audit's own recommendation was "2-3".
SHADOW_MIN_CLUSTER_STRIKES = 2

SHADOW_VARIANTS: Tuple[str, ...] = (
    "live",
    "width_floor",
    "windowed",
    "flipped_sign",
    "all_fixes",
)


@dataclass(frozen=True)
class ShadowVerdict:
    """One variant's read of the same market state. Pure data, no side effects."""

    variant: str
    # "does a qualifying accel cluster CONTAIN the proposed short?" — the
    # strike adjuster's predicate (gex_strike_adjuster.py).
    adjuster_predicate: bool
    # "is there a qualifying accel cluster entirely beyond spot on this
    # side?" — the defensive overlay's predicate (defensive_overlay.py).
    overlay_predicate: bool
    # The cluster that satisfied the adjuster predicate (post-locality), if any.
    cluster: Optional[GEXCluster]
    # How many accel zones the variant found at all, before predicate/locality.
    n_zones: int

    @property
    def summary(self) -> str:
        c = self.cluster
        detail = (
            f" cluster=[{c.strike_low:.0f},{c.strike_high:.0f}] n={c.n_strikes} "
            f"peak={c.peak_strike:.0f} strength={c.strength_pct:.1%}"
            if c is not None else ""
        )
        return (
            f"{self.variant}: adj={self.adjuster_predicate} "
            f"ovl={self.overlay_predicate} zones={self.n_zones}{detail}"
        )


def _zones_for(variant: str, profile: GEXProfile, min_strength_pct: float):
    """The accel ('negative') clusters this variant would see."""
    if variant == "flipped_sign" or variant == "all_fixes":
        prof = profile.with_flipped_sign_convention()
    else:
        prof = profile
    kwargs = {}
    if variant in ("width_floor", "all_fixes"):
        kwargs["min_cluster_strikes"] = SHADOW_MIN_CLUSTER_STRIKES
    if variant in ("windowed", "all_fixes"):
        kwargs["normalization_window_pts"] = SHADOW_WINDOW_PTS
    return prof.negative_clusters(min_strength_pct=min_strength_pct, **kwargs)


def evaluate_shadow(
    profile: GEXProfile,
    *,
    spot: float,
    threatened_side: str,
    reference_strike: float,
    min_strength_pct: float,
    peak_locality_pts: Optional[float],
    variants: Tuple[str, ...] = SHADOW_VARIANTS,
) -> Tuple[ShadowVerdict, ...]:
    """Score every shadow variant against one real decision moment.

    Never raises: a shadow failure must never be able to disturb a live
    trading decision, so the caller can treat this as best-effort. Returns
    an empty tuple if the profile is unusable.
    """
    if profile is None or not getattr(profile, "strikes", ()):  # pragma: no cover - defensive
        return ()

    out: list[ShadowVerdict] = []
    for variant in variants:
        try:
            zones = _zones_for(variant, profile, min_strength_pct)

            # Adjuster predicate: a cluster containing the proposed short,
            # subject to the same peak-locality radius the live gate uses.
            adj_cluster: Optional[GEXCluster] = None
            for c in zones:
                if c.strike_low <= reference_strike <= c.strike_high:
                    if peak_locality_pts is None or abs(reference_strike - c.peak_strike) <= peak_locality_pts:
                        adj_cluster = c
                        break

            # Overlay predicate: any qualifying cluster entirely beyond spot
            # on the threatened side (same locality radius when supplied).
            if threatened_side == "call":
                beyond = [c for c in zones if c.strike_low > spot]
            else:
                beyond = [c for c in zones if c.strike_high < spot]
            if peak_locality_pts is not None:
                beyond = [c for c in beyond if abs(reference_strike - c.peak_strike) <= peak_locality_pts]

            out.append(
                ShadowVerdict(
                    variant=variant,
                    adjuster_predicate=adj_cluster is not None,
                    overlay_predicate=bool(beyond),
                    cluster=adj_cluster,
                    n_zones=len(zones),
                )
            )
        except Exception:  # pragma: no cover - shadow must never break live
            continue
    return tuple(out)


def disagreement_summary(verdicts: Tuple[ShadowVerdict, ...]) -> str:
    """One-line log rendering, emitted only when a variant disagrees with live.

    Returns "" when every variant agrees with `live` on both predicates —
    the caller uses that to stay silent on the (expected) common case rather
    than doubling the log volume of every GEX decision.
    """
    if not verdicts:
        return ""
    live = next((v for v in verdicts if v.variant == "live"), None)
    if live is None:
        return ""
    diffs = [
        v for v in verdicts
        if v.variant != "live"
        and (v.adjuster_predicate != live.adjuster_predicate
             or v.overlay_predicate != live.overlay_predicate)
    ]
    if not diffs:
        return ""
    return " | ".join([live.summary] + [v.summary for v in diffs])
