"""Gamma Exposure (GEX) provider sourced from Polygon.io.

SIGN CONVENTION — READ THIS BEFORE CHANGING ANYTHING HERE.

This module uses an INVERTED dealer-positioning assumption relative to the
published SpotGamma / SqueezeMetrics convention. That is deliberate, but the
docstring here previously called it "the standard SpotGamma convention",
which is wrong and actively misleading — the published convention is the
OPPOSITE of what this code computes, and that mislabel cost real
investigation time on 2026-09-04. Stated precisely:

    THIS MODULE assumes dealers SHORT calls (retail buys calls → dealer
    fills the sell) and LONG puts (retail sells puts for premium → dealer
    fills the buy). Calls are negated; puts are positive.

    PUBLISHED SpotGamma / SqueezeMetrics assumes the reverse — dealers LONG
    calls, SHORT puts (customers buy protective puts and sell covered
    calls). Under that convention calls are positive and puts negative.

CONSEQUENCE, measured on live data (2026-09-04 audit): because SPX 0DTE call
open interest sits above spot and put OI below it, negating calls puts
essentially ALL negative ("accelerator") clusters ABOVE spot — live profile
showed 76 positive / 1 negative below spot vs 57 negative / 7 positive
above. The put branch of every accel-zone consumer is therefore near-blind
by construction: across the full log retention the put gate confirmed 0
times in 843 watch ticks inside 25pt of a short, while all three real
put-side stop-losses in the same window went undefended. The strike adjuster
compensates with `accel_peak_locality_pts` (see gex_strike_adjuster's
AdjusterConfig docstring, which has documented this hemisphere collapse
since 2026-05-13) — that is a mitigation for this convention's side effect,
NOT an independent feature.

WHICH CONVENTION IS CORRECT IS AN OPEN QUESTION, deliberately not resolved
here. The "retail buys calls" half is well supported for SPX 0DTE; the
"retail sells puts" half is questionable for the same product, where 0DTE
put buying is also heavy. Rather than flip a live trading assumption on
argument alone, GEXProfile.with_flipped_sign_convention() + brandon/
gex_shadow.py score the alternate read alongside the live one on every
decision, so the choice can eventually be made on recorded evidence. Nothing
acts on the shadow.

This gives signed GEX where:

    POSITIVE GEX = dealer net LONG gamma at that strike → DECELERATOR
                   (dealer hedging fights moves into the strike — Brandon's
                    "green node, MM long gamma, deceleration")
    NEGATIVE GEX = dealer net SHORT gamma at that strike → ACCELERATOR
                   (dealer hedging amplifies moves — Brandon's "red node,
                    MM short gamma, acceleration")

Math:

    GEX_strike = (put_OI − call_OI) × γ × S² × 100

i.e., **calls negated**: a call adds NEGATIVE contribution, a put adds
POSITIVE contribution. γ is option gamma (Polygon `greeks.gamma` if
present, BS-from-IV fallback otherwise). The factor of 100 converts
per-contract gamma to per-share notional dollars.

The fetcher is injectable so tests do not hit the network. Pagination is
handled by following Polygon's `next_url` field. Greeks are optional in the
Polygon response — the Options Starter tier's bulk chain-snapshot endpoint
omits BOTH γ and IV entirely (2026-09-01 correction: this docstring
previously claimed IV survives on the bulk endpoint; it does not — see
`fetch_per_contract_snapshot`'s own docstring and
`fetch_polygon_chain_with_greeks`, which is the only path that actually
populates either field, via individual per-contract calls for a capped
subset of strikes). An un-hydrated contract has neither γ nor IV and
contributes ZERO to the GEX calculation (`build_profile` drops it) — there
is no BS-gamma fallback for contracts the two-pass fetch never hydrated.
"""

from __future__ import annotations

import concurrent.futures
import logging
import math
import time as _time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timezone
from typing import Callable, Iterable, Optional

logger = logging.getLogger(__name__)

# Per-contract greek hydration is fanned out across a small thread pool — urllib
# releases the GIL during socket I/O, so ~80 calls / 8 workers ≈ 1s vs the ~6-8s
# serial loop that tripped the 5s read-timeout / 20s fetch_lock (2026-06-10).
# 2026-09-01: raised 8 -> 12 alongside GEX_HYDRATE_MAX_CONTRACTS_DEFAULT's
# 80 -> 250 bump (see that constant's comment) so the pathological-case
# ceiling (Polygon read-timeouts on every call) doesn't triple right along
# with the cap — see GEX_HYDRATE_DEADLINE_S for the actual wall-clock bound
# this relies on instead of trusting worker count alone.
GEX_HYDRATE_WORKERS = 12
# Hard wall-clock budget on the whole per-contract hydration pass (2026-09-01).
# This fetch runs SYNCHRONOUSLY in the entry-time decision path
# (force_refresh=True), so an unbounded pool.map() means a genuinely bad
# Polygon day (every per-contract call timing out at its own 5s limit) could
# stall an entry decision for ceil(candidates/workers) x 5s -- 160s at
# 250 candidates / 12 workers if every single call timed out. Whatever
# hasn't completed by this deadline is abandoned (same disposition as an
# ordinary per-call failure: stays un-hydrated, contributes zero GEX) rather
# than blocking further. Decouples "how bad can a degraded-Polygon day get"
# from "how big is the cap" -- see fetch_polygon_chain_with_greeks.
GEX_HYDRATE_DEADLINE_S = 15.0
# The chain pull is the single point whose failure aborts the WHOLE fetch (and
# makes the caller fall back to a STALE profile). Retry it with backoff so a
# transient Polygon read-timeout doesn't degrade strike selection.
GEX_CHAIN_FETCH_ATTEMPTS = 3
GEX_CHAIN_RETRY_BACKOFF_S = 0.5


@dataclass(frozen=True)
class StrikeGEX:
    strike: float
    gex: float


@dataclass(frozen=True)
class StrikeDelta:
    """Per-strike, per-side option delta from the chain snapshot.

    Used for delta-target strike selection (Brandon's "8 delta short" rule).
    Polygon Starter exposes delta in the per-contract snapshot; calls have
    delta in (0, 1] and puts in [-1, 0). When the API doesn't return greeks
    we leave delta=None and the lookup falls back to the OTM-multiplier
    method downstream.

    `iv` is captured from the same chain snapshot so callers can BS-recompute
    delta at a *live* spot when the cached `delta` is stale. 0DTE delta moves
    very fast as spot drifts (high gamma near expiry); a 15-min snapshot can
    drift 5-10 delta points by the time the next entry fires.
    """
    strike: float
    contract_type: str  # "call" or "put"
    delta: Optional[float]
    iv: Optional[float] = None


@dataclass(frozen=True)
class GEXCluster:
    strike_low: float
    strike_high: float
    total_gex: float
    # Peak strike: the single strike inside the cluster's contiguous run with
    # the largest |GEX|. The strike adjuster uses this for peak-locality SKIP
    # tests so a 300pt-wide same-sign band doesn't blanket-ban every short on
    # that side — only shorts within a small radius of the peak are forbidden.
    # See gex_strike_adjuster.AdjusterConfig.accel_peak_locality_pts.
    peak_strike: float = 0.0
    # 2026-09-05 instrumentation: how many contiguous strikes this cluster
    # spans, and its |total_gex| as a fraction of the normalization base.
    # Both were previously uncomputable by any consumer, which is why the
    # 2026-09-04 gate audit could not tell a genuine 345pt gamma wall from a
    # single-strike OI artifact without re-deriving clusters by hand. Logged
    # on every adjuster/overlay decision now. n_strikes defaults to 0 for
    # profiles built before this field existed (treat 0 as "unknown", not
    # "empty") — see _cluster_width_ok.
    n_strikes: int = 0
    strength_pct: float = 0.0

    @property
    def sign(self) -> str:
        return "positive" if self.total_gex > 0 else "negative"

    @property
    def width_pts(self) -> float:
        return self.strike_high - self.strike_low


@dataclass(frozen=True)
class GEXProfile:
    spot: float
    expiry: date
    fetched_at: datetime
    strikes: tuple[StrikeGEX, ...] = field(default_factory=tuple)
    # Per-strike per-side delta data captured from the same chain fetch
    # that built the GEX clusters. Reused by delta-target strike selection
    # so we don't issue a second chain fetch per entry. Empty tuple if the
    # chain didn't carry greeks (Brandon falls back to OTM-multiplier).
    deltas: tuple[StrikeDelta, ...] = field(default_factory=tuple)
    # Chain-hydration telemetry (2026-08-03) — how many contracts were in the
    # raw chain vs. how many actually carried usable greeks/IV from this
    # fetch. Embedded on the profile itself (not tracked as a separate
    # per-instance counter) specifically so it travels correctly through
    # every reuse path — in-process TTL cache, cross-process shared-cache
    # hit, sibling-variant reuse under the fetch lock — instead of going
    # stale/mismatched whenever a variant reuses a PROFILE it didn't fetch
    # itself (found in the 2026-08-03 telemetry review: B and C share entry
    # slots, so this is a routine occurrence, not an edge case). 0 = unknown
    # (e.g. a profile built by test code or before this field existed).
    chain_total: int = 0
    hydrated_count: int = 0

    def gex_at(self, strike: float, tolerance: float = 0.01) -> float:
        for sg in self.strikes:
            if abs(sg.strike - strike) <= tolerance:
                return sg.gex
        return 0.0

    def sum_gex_between(self, low: float, high: float) -> float:
        if low > high:
            low, high = high, low
        return sum(sg.gex for sg in self.strikes if low <= sg.strike <= high)

    def total_abs_gex(self) -> float:
        return sum(abs(sg.gex) for sg in self.strikes)

    def positive_clusters(
        self,
        min_strength_pct: float = 0.05,
        *,
        min_cluster_strikes: int = 1,
        normalization_window_pts: Optional[float] = None,
    ) -> tuple[GEXCluster, ...]:
        return _detect_clusters(
            self.strikes, sign=+1, min_strength_pct=min_strength_pct,
            min_cluster_strikes=min_cluster_strikes,
            normalization_window_pts=normalization_window_pts, spot=self.spot,
        )

    def negative_clusters(
        self,
        min_strength_pct: float = 0.05,
        *,
        min_cluster_strikes: int = 1,
        normalization_window_pts: Optional[float] = None,
    ) -> tuple[GEXCluster, ...]:
        return _detect_clusters(
            self.strikes, sign=-1, min_strength_pct=min_strength_pct,
            min_cluster_strikes=min_cluster_strikes,
            normalization_window_pts=normalization_window_pts, spot=self.spot,
        )

    def with_flipped_sign_convention(self) -> "GEXProfile":
        """Return this profile with every strike's signed GEX negated.

        Flipping the dealer-positioning assumption (see the module docstring's
        SIGN CONVENTION section) is EXACTLY a negation of every per-strike
        contribution — ``sign * oi * gamma * ...`` with ``sign`` inverted for
        both contract types — so the alternate convention needs no re-fetch
        and no re-hydration. Under the flip, what was a negative ("accel")
        cluster becomes positive and vice versa, which is precisely the
        hemisphere swap the 2026-09-04 audit identified as the reason the put
        branch never confirms (0 of 843 watch ticks inside 25pt).

        Used ONLY by the shadow gate (brandon/gex_shadow.py) to log what a
        standard-convention read WOULD have decided. Nothing acts on it.
        """
        return replace(
            self,
            strikes=tuple(replace(sg, gex=-sg.gex) for sg in self.strikes),
        )


def black_scholes_gamma(spot: float, strike: float, iv: float, t_years: float, r: float = 0.0) -> float:
    """Standard Black-Scholes gamma. Returns 0.0 on degenerate inputs."""
    if t_years <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t_years) / (iv * sqrt_t)
    pdf = math.exp(-d1 * d1 / 2.0) / math.sqrt(2.0 * math.pi)
    return pdf / (spot * iv * sqrt_t)


def _norm_cdf(x: float) -> float:
    """Standard-normal CDF via math.erf — no scipy dependency."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def black_scholes_delta(
    spot: float,
    strike: float,
    iv: float,
    t_years: float,
    contract_type: str,
    r: float = 0.0,
) -> Optional[float]:
    """Standard Black-Scholes delta. Call ∈ (0, 1], put ∈ [-1, 0).

    Returns None on degenerate inputs (so callers can fall back to the
    cached snapshot delta cleanly).

    Used by find_strike_at_delta(recompute_t_years=...) to refresh stale
    Polygon snapshot deltas from a live spot. 0DTE gamma is enormous —
    spot moving 5 points in the 12 minutes since the last chain fetch
    can flip a 7δ put into a 14δ put without anything else changing.
    """
    if t_years <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return None
    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t_years) / (iv * sqrt_t)
    if contract_type == "call":
        return _norm_cdf(d1)
    if contract_type == "put":
        return _norm_cdf(d1) - 1.0
    return None


def time_to_expiry_years(now_et: datetime, expiry_close_et: datetime) -> float:
    """Calendar-time T in years for BS. expiry_close_et is the option's settlement instant."""
    delta = (expiry_close_et - now_et).total_seconds()
    if delta <= 0:
        return 0.0
    return delta / (365.0 * 24.0 * 3600.0)


def build_profile(
    contracts: Iterable[dict],
    *,
    spot: float,
    expiry: date,
    time_to_expiry: float,
    fetched_at: Optional[datetime] = None,
) -> GEXProfile:
    """Build a GEXProfile from a list of Polygon-shaped contract dicts.

    Each contract dict accepts the shape Polygon's `/v3/snapshot/options/{u}`
    returns, with the fields we need:

        {
            "details": {"strike_price": float, "contract_type": "call"|"put"},
            "open_interest": int,
            "greeks": {"gamma": float}   # optional — falls back to BS from IV
            "implied_volatility": float, # required if greeks.gamma absent
        }

    Strikes are aggregated across calls/puts. Contracts with no OI are
    dropped to keep the strike list clean.
    """
    if fetched_at is None:
        fetched_at = datetime.now(timezone.utc)
    by_strike: dict[float, float] = {}
    delta_records: list[StrikeDelta] = []

    for c in contracts:
        details = c.get("details") or {}
        strike = details.get("strike_price")
        ctype = (details.get("contract_type") or "").lower()
        if strike is None or ctype not in ("call", "put"):
            continue

        # Capture delta even for zero-OI contracts — delta-target strike
        # selection wants the full chain shape, not just the OI-weighted
        # subset. GEX clustering still drops zero-OI below. IV travels
        # alongside so callers can BS-recompute delta at a live spot if
        # the cached delta is too stale (see find_strike_at_delta).
        greeks = c.get("greeks") or {}
        delta_raw = greeks.get("delta")
        iv_raw = c.get("implied_volatility")
        delta_records.append(StrikeDelta(
            strike=float(strike),
            contract_type=ctype,
            delta=float(delta_raw) if delta_raw is not None else None,
            iv=float(iv_raw) if iv_raw is not None else None,
        ))

        oi = int(c.get("open_interest") or 0)
        if oi <= 0:
            continue

        gamma = greeks.get("gamma")
        if gamma is None:
            iv = c.get("implied_volatility")
            if iv is None or iv <= 0:
                continue
            gamma = black_scholes_gamma(spot, float(strike), float(iv), time_to_expiry)
        if gamma <= 0:
            continue

        # THIS MODULE'S convention (dealers short calls, long puts) — which is
        # the INVERSE of published SpotGamma/SqueezeMetrics. See the module
        # docstring's SIGN CONVENTION section before changing this line: it is
        # the single line that decides which hemisphere "accel" zones live in,
        # and therefore which side of the condor the adjuster defends.
        sign = -1.0 if ctype == "call" else +1.0
        contribution = sign * oi * gamma * spot * spot * 100.0
        by_strike[float(strike)] = by_strike.get(float(strike), 0.0) + contribution

    strikes_sorted = tuple(
        StrikeGEX(strike=k, gex=v) for k, v in sorted(by_strike.items())
    )
    deltas_sorted = tuple(sorted(delta_records, key=lambda d: (d.strike, d.contract_type)))
    return GEXProfile(
        spot=spot,
        expiry=expiry,
        fetched_at=fetched_at,
        strikes=strikes_sorted,
        deltas=deltas_sorted,
    )


def _detect_clusters(
    strikes: tuple[StrikeGEX, ...],
    *,
    sign: int,
    min_strength_pct: float,
    min_cluster_strikes: int = 1,
    normalization_window_pts: Optional[float] = None,
    spot: float = 0.0,
) -> tuple[GEXCluster, ...]:
    """Detect contiguous runs of strikes whose GEX has the requested sign.

    A cluster is a maximal run where every strike has gex matching `sign`
    (one zero-GEX or wrong-sign strike breaks the run). After detection,
    clusters whose |total_gex| is below min_strength_pct × the normalization
    base are filtered out so noise around zero doesn't get reported as walls.

    min_cluster_strikes (2026-09-05, BUG 1 of the gate audit): a run of ONE
    strike used to qualify as a "wall". Its strike_low == strike_high ==
    peak_strike, so the downstream peak-locality test is satisfied trivially
    and a single high-OI strike could veto an entry or arm a hedge. On the
    live 2026-09-04 profile the ONLY negative cluster clearing the 0.10
    threshold was NEG[7715-7715] — one strike, 16.94% — and it was the sole
    source of every put-side overlay confirmation on record. Requiring >= N
    contiguous strikes restores the intended "a wall is a localized BAND, not
    a single point" meaning. Default 1 preserves legacy behavior for any
    caller that doesn't opt in.

    normalization_window_pts (2026-09-05, BUG 2): min_strength_pct is
    normalized over the |GEX| of the ENTIRE chain by default. On a 0DTE book
    whose gamma is concentrated at the money, that inverts the intended
    meaning of the threshold — measured on the live 2026-09-04 profile, the
    genuine 345pt call wing NEG[7755-8100] scored 9.25% and FAILED the 0.10
    gate while the single ATM strike scored 16.94% and PASSED. No value of
    min_strength_pct can repair that, because the units are wrong, not the
    number. Passing a window restricts the normalization base to strikes
    within +/- window_pts of spot, so a cluster is scored against the gamma
    that is actually near the money. None = legacy whole-chain behavior.

    KNOWN SCOPE-MIXING PROPERTY of the windowed mode, stated so a future
    reader does not mistake it for a bug: a cluster's FULL total_gex is
    scored against a WINDOWED basis, so a cluster extending past the window
    scores higher than if both were windowed. On the live 2026-09-04 shape
    that is precisely the intended correction (the 345pt wall goes 8.96% ->
    17.18% and begins to qualify), but the windowed score is therefore not a
    strict "fraction of local gamma". Windowing both sides would need a rule
    for clusters straddling the window edge; that choice is deferred until
    the shadow shows whether windowing helps at all.

    Both new parameters default to the legacy behavior. Nothing in the live
    decision path passes non-default values yet — they exist so the shadow
    gate can score the corrected reads alongside the live one. See
    brandon/gex_shadow.py.
    """
    if not strikes:
        return ()
    if normalization_window_pts is not None and normalization_window_pts > 0 and spot > 0:
        lo, hi = spot - normalization_window_pts, spot + normalization_window_pts
        basis = [sg for sg in strikes if lo <= sg.strike <= hi]
        # An empty/degenerate window must not silently divide by ~0 and pass
        # everything — fall back to the whole chain rather than inventing a
        # threshold from two stray strikes.
        if len(basis) < 3:
            basis = list(strikes)
    else:
        basis = list(strikes)
    total_abs = sum(abs(sg.gex) for sg in basis)
    if total_abs <= 0:
        return ()
    threshold = min_strength_pct * total_abs

    out: list[GEXCluster] = []
    run: list[StrikeGEX] = []
    for sg in strikes:
        in_sign = (sign > 0 and sg.gex > 0) or (sign < 0 and sg.gex < 0)
        if in_sign:
            run.append(sg)
        else:
            if run:
                _flush_cluster(out, run, threshold, total_abs, min_cluster_strikes)
                run = []
    if run:
        _flush_cluster(out, run, threshold, total_abs, min_cluster_strikes)
    return tuple(out)


def _flush_cluster(
    out: list[GEXCluster],
    run: list[StrikeGEX],
    threshold: float,
    total_abs: float,
    min_cluster_strikes: int = 1,
) -> None:
    if len(run) < max(1, min_cluster_strikes):
        return
    total = sum(sg.gex for sg in run)
    if abs(total) < threshold:
        return
    peak = max(run, key=lambda sg: abs(sg.gex))
    out.append(
        GEXCluster(
            strike_low=run[0].strike,
            strike_high=run[-1].strike,
            total_gex=total,
            peak_strike=peak.strike,
            n_strikes=len(run),
            strength_pct=(abs(total) / total_abs) if total_abs > 0 else 0.0,
        )
    )


# ---------------------------------------------------------------------------
# Polygon HTTP client
# ---------------------------------------------------------------------------

POLYGON_SNAPSHOT_URL = "https://api.polygon.io/v3/snapshot/options/{underlying}"
POLYGON_PER_CONTRACT_URL = "https://api.polygon.io/v3/snapshot/options/I:{underlying}/{ticker}"


SPX_STRIKE_GRID_PT = 5.0


def find_strike_at_delta(
    profile: GEXProfile,
    *,
    side: str,
    target_delta_abs: float,
    spot_fallback: Optional[float] = None,
    recompute_t_years: Optional[float] = None,
    increment: float = SPX_STRIKE_GRID_PT,
    max_delta_abs: Optional[float] = None,
    return_delta: bool = False,
) -> "Optional[float] | tuple[Optional[float], Optional[float]]":
    """Find the strike whose `side` option delta is closest to ±target_delta_abs.

    When ``return_delta`` is True, returns ``(snapped_strike, achieved_delta)``
    (or ``(None, None)`` on failure) so the caller can apply a MIN-delta floor —
    a too-FAR pick off an under-hydrated chain (2026-07-17: 8δ target selected
    ~0.5-1δ garbage) is NOT catchable here (we only clamp the too-CLOSE ceiling
    via ``max_delta_abs``), so the floor policy lives in the caller. Default
    (False) preserves the original ``Optional[float]`` contract.

    Brandon's "8 delta short" rule: target_delta_abs ≈ 0.08, side="put" or "call".
    For puts we match against |delta| since Polygon returns put deltas as
    negative values; calls are positive in (0, 1].

    Constraints:
    - Strike must be on the SPX 5pt grid (snapped to nearest).
    - Calls must be ABOVE spot, puts must be BELOW spot — protects against a
      degenerate delta crossover (e.g., a put with positive delta from stale
      data) flipping the strike to the wrong side.
    - Returns None if the chain has no contracts on the requested side with
      delta data (caller should fall back to OTM-multiplier).

    Args:
        profile: GEXProfile with `deltas` populated by build_profile.
        side: "call" or "put"
        target_delta_abs: target absolute delta, e.g. 0.08 for 8 delta
        spot_fallback: spot price to gate strikes against (defaults to
            profile.spot if not provided)
        recompute_t_years: when provided alongside `spot_fallback`, apply a
            spot-DRIFT adjustment to each candidate's cached market delta —
            Black-Scholes delta at the live spot minus BS delta at the
            profile's own spot (same iv/t). This corrects for spot moving since
            the fetch WITHOUT replacing the market-correct level (BS's 0DTE
            level-error cancels in the difference). NOTE: it must NOT be used to
            re-level the delta outright — calendar-time BS systematically
            under-deltas 0DTE options ~2x (2026-06-11 incident: a 33δ put picked
            as "8δ"). Candidates without a cached `delta` are skipped.

    Returns:
        float strike price (snapped to 5pt grid) or None.
    """
    side = side.lower()
    if side not in ("call", "put"):
        raise ValueError(f"side must be 'call' or 'put', got {side!r}")
    if target_delta_abs <= 0 or target_delta_abs >= 1:
        raise ValueError(f"target_delta_abs must be in (0, 1), got {target_delta_abs}")

    spot = float(spot_fallback if spot_fallback is not None else profile.spot)
    _none = (None, None) if return_delta else None
    if spot <= 0:
        return _none

    recompute_enabled = (
        recompute_t_years is not None
        and recompute_t_years > 0
        and spot_fallback is not None
    )

    # Filter to the right side, on the right side of spot, with a usable
    # cached (market) delta.
    candidates: list[tuple[StrikeDelta, float]] = []
    for d in profile.deltas:
        if d.contract_type != side:
            continue
        if side == "call" and d.strike <= spot:
            continue
        if side == "put" and d.strike >= spot:
            continue

        # 2026-06-11 ROOT-CAUSE FIX: the cached delta IS the market delta — it
        # matches the live option prices (verified against fills). The previous
        # path REPLACED it with a calendar-time Black-Scholes delta, which
        # systematically UNDER-deltas 0DTE options (~2x): on 2026-06-11 a 33δ put
        # was selected as the "8δ" short because its recomputed delta read ~0.08.
        # We now use the cached delta as the LEVEL and apply BS only as the
        # spot-DRIFT adjustment — BS at the live spot minus BS at the profile's
        # own spot, same iv/t. BS's absolute-level error cancels in that
        # difference, so we keep the correct market level AND still correct for
        # any spot move since the fetch. Strikes with no cached delta are skipped
        # (we do not trust the bare recompute to invent one); a too-sparse chain
        # then trips the max-delta clamp below and routes to the OTM-multiplier.
        if d.delta is None:
            continue
        effective_delta = d.delta
        if recompute_enabled and d.iv is not None and d.iv > 0:
            bs_live = black_scholes_delta(
                spot=spot, strike=d.strike, iv=d.iv,
                t_years=float(recompute_t_years), contract_type=side,
            )
            bs_ref = black_scholes_delta(
                spot=float(profile.spot), strike=d.strike, iv=d.iv,
                t_years=float(recompute_t_years), contract_type=side,
            )
            if bs_live is not None and bs_ref is not None:
                effective_delta = d.delta + (bs_live - bs_ref)
        candidates.append((d, effective_delta))

    if not candidates:
        return _none

    # Closest by absolute delta distance to target.
    best_d, best_delta = min(candidates, key=lambda item: abs(abs(item[1]) - target_delta_abs))
    # S-HIGH-1 (2026-06-10): a sparse / ATM-biased chain (Starter tier strips
    # greeks; only near-money strikes get hydrated) can make the strike "closest
    # to target" actually a 20-35delta short — far from the 8delta intent — even
    # off a FRESH profile (the stale-greeks guard only catches STALE ones, not a
    # thin chain). With no clamp this silently places a much-too-close short.
    # Reject when the best match is well past target so the caller falls back to
    # the conservative OTM-multiplier instead.
    if max_delta_abs is not None and abs(best_delta) > max_delta_abs:
        logger.warning(
            "find_strike_at_delta(%s): closest match %.0f is %.3fdelta > max %.3f "
            "(target %.3f) — chain too sparse near target; returning None for OTM fallback",
            side, best_d.strike, abs(best_delta), max_delta_abs, target_delta_abs,
        )
        return _none
    snapped = round(best_d.strike / increment) * increment
    return (snapped, best_delta) if return_delta else snapped


HttpFetcher = Callable[[str], dict]


def _default_http_fetch(url: str, *, timeout: float = 5.0) -> dict:
    """Default HTTP fetcher used when no injected fetcher is supplied.

    5s timeout keeps the bot's heartbeat tight — the strategy harness is
    looping every ~10s, so this caps any single request at half the cycle.
    Failures bubble up as urllib.error.URLError (caught by the caller).
    """
    import json

    req = urllib.request.Request(url, headers={"User-Agent": "calypso-hydra/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_per_contract_snapshot(
    *,
    underlying: str,
    ticker: str,
    api_key: str,
    http_fetch: Optional[HttpFetcher] = None,
) -> Optional[dict]:
    """Fetch a single contract's snapshot — Greeks + IV + OI.

    Polygon Starter strips greeks/IV from the chain snapshot but DOES expose
    them via this per-contract endpoint. Returns the inner `results` dict
    (which has details/greeks/implied_volatility/open_interest at the top
    level) or None on failure.
    """
    fetch = http_fetch or _default_http_fetch
    url = (
        POLYGON_PER_CONTRACT_URL.format(underlying=underlying, ticker=ticker)
        + "?" + urllib.parse.urlencode({"apiKey": api_key})
    )
    try:
        body = fetch(url)
    except Exception:
        return None
    if body.get("status") not in (None, "OK", "DELAYED"):
        return None
    return body.get("results")


def fetch_polygon_chain_with_greeks(
    *,
    underlying: str,
    expiry: date,
    api_key: str,
    http_fetch: Optional[HttpFetcher] = None,
    max_pages: int = 4,
    oi_threshold: int = 50,
    spot: Optional[float] = None,
    spot_window_pct: float = 0.05,
    max_contracts_to_hydrate: int = 250,
) -> tuple[list[dict], int]:
    """Two-pass fetch: chain for OI, per-contract for Greeks/IV.

    Polygon Starter ($29/mo) returns OI in the chain snapshot but omits
    Greeks and IV entirely (see the module docstring — a contract this
    function doesn't hydrate contributes ZERO GEX, not a discounted amount).
    This wrapper fetches the chain, then hydrates the most liquid strikes
    via per-contract calls. Strikes that don't meet the OI threshold OR
    fall outside `spot ± spot_window_pct` keep their chain payload (no
    greeks) — they contribute ~0 to GEX anyway because their gamma at
    far-OTM is microscopic.

    2026-09-01: `max_contracts_to_hydrate` raised 80 -> 250 after a live
    check found 80 was silently excluding 59% of real, liquid,
    near-the-money candidates (195 qualified, only 80 got hydrated) on an
    ordinary trading day — not an edge case. See bots/hydra/__init__.py
    version history for the full incident writeup. `candidates_found` (the
    pre-cap count, now returned) makes a recurrence of this gap visible to
    the caller instead of requiring an ad-hoc investigation to discover.

    Args:
        underlying: e.g. "SPX"
        expiry: option expiry date
        api_key: Polygon API key
        http_fetch: injectable for testing
        max_pages: pagination cap on the chain endpoint
        oi_threshold: skip per-contract hydration for strikes with OI below this
        spot: current underlying spot — used to bound the hydration window
        spot_window_pct: only hydrate strikes within ±this fraction of spot
        max_contracts_to_hydrate: hard cap on per-contract calls per refresh

    Returns:
        (contracts, candidates_found) — contracts is the full list; strikes
        selected for hydration carry merged greeks/implied_volatility, the
        rest carry only the chain payload (which build_profile will drop if
        greeks AND iv are absent). candidates_found is how many contracts
        PASSED the OI + spot-window filter BEFORE the max_contracts_to_hydrate
        cap was applied — compare it to max_contracts_to_hydrate to tell
        whether the cap is actually binding today.
    """
    # Pass 1: the chain pull. This is the single point whose failure aborts the
    # WHOLE fetch (the caller then returns a STALE profile → too-close strikes,
    # the 2026-06-10 incident). Retry it with backoff; on final failure let the
    # exception propagate so the caller's stale-fallback (now age-gated by the
    # strike-selection guard) still applies.
    contracts: list[dict] = []
    for _attempt in range(GEX_CHAIN_FETCH_ATTEMPTS):
        try:
            contracts = fetch_polygon_chain(
                underlying=underlying, expiry=expiry, api_key=api_key,
                http_fetch=http_fetch, max_pages=max_pages,
            )
            if contracts:
                break
        except Exception as exc:
            if _attempt == GEX_CHAIN_FETCH_ATTEMPTS - 1:
                raise
            logger.warning(
                "Brandon GEX chain pull attempt %d/%d failed (%s) — retrying",
                _attempt + 1, GEX_CHAIN_FETCH_ATTEMPTS, exc,
            )
        if _attempt < GEX_CHAIN_FETCH_ATTEMPTS - 1:
            _time.sleep(GEX_CHAIN_RETRY_BACKOFF_S * (_attempt + 1))
    if not contracts:
        return contracts, 0

    # Filter to strikes worth hydrating
    candidates: list[dict] = []
    for c in contracts:
        oi = int(c.get("open_interest") or 0)
        if oi < oi_threshold:
            continue
        if spot is not None and spot > 0:
            strike = (c.get("details") or {}).get("strike_price")
            if strike is None or abs(float(strike) - spot) > spot * spot_window_pct:
                continue
        candidates.append(c)

    candidates_found = len(candidates)

    # Hydrate top-N by OI to bound API load
    candidates.sort(key=lambda c: int(c.get("open_interest") or 0), reverse=True)
    candidates = candidates[:max_contracts_to_hydrate]

    # Pass 2: hydrate greeks/IV per-contract, PARALLELIZED. Serially this was
    # ~80 round-trips ≈ 6-8s — enough to trip the 5s read-timeout and the 20s
    # cross-variant fetch_lock, returning a stale profile (2026-06-10). Each
    # fetch swallows its own errors (returns None) and the merge is per-contract
    # / order-independent, so fanning out across a small thread pool is safe.
    def _hydrate(c: dict) -> None:
        ticker = (c.get("details") or {}).get("ticker")
        if not ticker:
            return
        details = fetch_per_contract_snapshot(
            underlying=underlying, ticker=ticker,
            api_key=api_key, http_fetch=http_fetch,
        )
        if not details:
            return
        # Merge: greeks and implied_volatility live at the per-contract root
        if details.get("greeks"):
            c["greeks"] = details["greeks"]
        if details.get("implied_volatility") is not None:
            c["implied_volatility"] = details["implied_volatility"]

    if candidates:
        workers = min(GEX_HYDRATE_WORKERS, len(candidates))
        # 2026-09-01: NOT a `with ThreadPoolExecutor(...) as pool:` block —
        # that form's __exit__ calls shutdown(wait=True), which blocks until
        # EVERY submitted call finishes regardless of how long we're willing
        # to wait, silently defeating GEX_HYDRATE_DEADLINE_S. Submit futures
        # explicitly, wait only up to the deadline, and shut down with
        # wait=False so a caller on the synchronous entry-time path is never
        # blocked past the deadline by a genuinely slow Polygon. Any calls
        # still in flight past the deadline keep running in background
        # threads and harmlessly mutate their own (already-abandoned-by-then)
        # contract dict when they eventually finish — each `_hydrate` call
        # only touches the one dict it was given, so a late finish can't
        # corrupt a profile that's already been built from the pre-deadline
        # snapshot of `contracts`.
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
        try:
            futures = [pool.submit(_hydrate, c) for c in candidates]
            _done, not_done = concurrent.futures.wait(
                futures, timeout=GEX_HYDRATE_DEADLINE_S
            )
            if not_done:
                logger.warning(
                    "Brandon GEX hydration hit its %.0fs wall-clock deadline "
                    "with %d/%d per-contract calls still in flight — "
                    "proceeding with partial hydration (un-hydrated "
                    "contracts contribute zero GEX, same as an ordinary "
                    "per-call failure)",
                    GEX_HYDRATE_DEADLINE_S, len(not_done), len(futures),
                )
        finally:
            pool.shutdown(wait=False)
    return contracts, candidates_found


def fetch_polygon_chain(
    *,
    underlying: str,
    expiry: date,
    api_key: str,
    http_fetch: Optional[HttpFetcher] = None,
    max_pages: int = 20,
) -> list[dict]:
    """Pull the full options chain for one expiry from Polygon.

    Follows `next_url` pagination up to `max_pages` to avoid runaway loops.
    Returns the raw `results` array; pass it to build_profile to compute GEX.

    Raises:
        ValueError: if Polygon returns an error envelope.
        urllib.error.URLError: on transport failure.
    """
    fetch = http_fetch or _default_http_fetch
    base = POLYGON_SNAPSHOT_URL.format(underlying=underlying)
    qs = urllib.parse.urlencode(
        {"expiration_date": expiry.isoformat(), "limit": 250, "apiKey": api_key}
    )
    url = f"{base}?{qs}"

    out: list[dict] = []
    pages = 0
    while url and pages < max_pages:
        body = fetch(url)
        if body.get("status") not in (None, "OK", "DELAYED"):
            raise ValueError(f"polygon error: {body.get('error') or body}")
        out.extend(body.get("results") or [])
        next_url = body.get("next_url")
        if not next_url:
            break
        # Polygon's next_url omits the apiKey; append it.
        sep = "&" if "?" in next_url else "?"
        url = f"{next_url}{sep}apiKey={api_key}"
        pages += 1
    return out
