"""Gamma Exposure (GEX) provider sourced from Polygon.io.

Computes per-strike GEX for a given underlying + expiry using the standard
SpotGamma / Vol Signals dealer-positioning convention:

    Dealers assumed SHORT calls (retail buys calls → dealer fills the sell),
    LONG puts (retail sells puts for premium → dealer fills the buy).

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
Polygon response — the Options Starter tier exposes IV but not γ, so the
provider falls back to BS-gamma when γ is missing.
"""

from __future__ import annotations

import math
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from typing import Callable, Iterable, Optional


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

    @property
    def sign(self) -> str:
        return "positive" if self.total_gex > 0 else "negative"


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

    def positive_clusters(self, min_strength_pct: float = 0.05) -> tuple[GEXCluster, ...]:
        return _detect_clusters(self.strikes, sign=+1, min_strength_pct=min_strength_pct)

    def negative_clusters(self, min_strength_pct: float = 0.05) -> tuple[GEXCluster, ...]:
        return _detect_clusters(self.strikes, sign=-1, min_strength_pct=min_strength_pct)


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

        # SpotGamma / Vol Signals convention: dealers short calls, long puts.
        # Calls are negated so dealer-perspective signed GEX comes out right.
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
) -> tuple[GEXCluster, ...]:
    """Detect contiguous runs of strikes whose GEX has the requested sign.

    A cluster is a maximal run where every strike has gex matching `sign`
    (one zero-GEX or wrong-sign strike breaks the run). After detection,
    clusters whose |total_gex| is below min_strength_pct × total_abs_gex
    are filtered out so noise around zero doesn't get reported as walls.
    """
    if not strikes:
        return ()
    total_abs = sum(abs(sg.gex) for sg in strikes)
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
                _flush_cluster(out, run, threshold)
                run = []
    if run:
        _flush_cluster(out, run, threshold)
    return tuple(out)


def _flush_cluster(out: list[GEXCluster], run: list[StrikeGEX], threshold: float) -> None:
    total = sum(sg.gex for sg in run)
    if abs(total) < threshold:
        return
    out.append(
        GEXCluster(
            strike_low=run[0].strike,
            strike_high=run[-1].strike,
            total_gex=total,
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
) -> Optional[float]:
    """Find the strike whose `side` option delta is closest to ±target_delta_abs.

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
        recompute_t_years: when provided alongside `spot_fallback`, refresh
            each candidate's delta via Black-Scholes using the cached IV +
            live spot + current time-to-expiry. Fixes the stale-snapshot
            problem on 0DTE chains where delta drifts fast between the
            15-min refresh windows. Strikes whose cached `iv` is missing
            keep their cached `delta` (mixed-mode fallback).

    Returns:
        float strike price (snapped to 5pt grid) or None.
    """
    side = side.lower()
    if side not in ("call", "put"):
        raise ValueError(f"side must be 'call' or 'put', got {side!r}")
    if target_delta_abs <= 0 or target_delta_abs >= 1:
        raise ValueError(f"target_delta_abs must be in (0, 1), got {target_delta_abs}")

    spot = float(spot_fallback if spot_fallback is not None else profile.spot)
    if spot <= 0:
        return None

    recompute_enabled = (
        recompute_t_years is not None
        and recompute_t_years > 0
        and spot_fallback is not None
    )

    # Filter to the right side, on the right side of spot, with a usable
    # delta source (cached snapshot OR cached IV for BS-recompute).
    candidates: list[tuple[StrikeDelta, float]] = []
    for d in profile.deltas:
        if d.contract_type != side:
            continue
        if side == "call" and d.strike <= spot:
            continue
        if side == "put" and d.strike >= spot:
            continue

        effective_delta: Optional[float] = None
        if recompute_enabled and d.iv is not None and d.iv > 0:
            effective_delta = black_scholes_delta(
                spot=spot,
                strike=d.strike,
                iv=d.iv,
                t_years=float(recompute_t_years),
                contract_type=side,
            )
        if effective_delta is None:
            effective_delta = d.delta
        if effective_delta is None:
            continue
        candidates.append((d, effective_delta))

    if not candidates:
        return None

    # Closest by absolute delta distance to target.
    best_d, _ = min(candidates, key=lambda item: abs(abs(item[1]) - target_delta_abs))
    snapped = round(best_d.strike / SPX_STRIKE_GRID_PT) * SPX_STRIKE_GRID_PT
    return snapped


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
    max_contracts_to_hydrate: int = 80,
) -> list[dict]:
    """Two-pass fetch: chain for OI, per-contract for Greeks/IV.

    Polygon Starter ($29/mo) returns OI in the chain snapshot but omits
    Greeks and IV. This wrapper fetches the chain, then hydrates the most
    liquid strikes via per-contract calls. Strikes that don't meet the OI
    threshold OR fall outside `spot ± spot_window_pct` keep their chain
    payload (no greeks) — they contribute ~0 to GEX anyway because their
    gamma at far-OTM is microscopic.

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
        List of contract dicts. Strikes selected for hydration carry
        merged greeks/implied_volatility; the rest carry only the chain
        payload (which build_profile will drop if greeks AND iv are absent).
    """
    contracts = fetch_polygon_chain(
        underlying=underlying, expiry=expiry, api_key=api_key,
        http_fetch=http_fetch, max_pages=max_pages,
    )
    if not contracts:
        return contracts

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

    # Hydrate top-N by OI to bound API load
    candidates.sort(key=lambda c: int(c.get("open_interest") or 0), reverse=True)
    candidates = candidates[:max_contracts_to_hydrate]

    for c in candidates:
        ticker = (c.get("details") or {}).get("ticker")
        if not ticker:
            continue
        details = fetch_per_contract_snapshot(
            underlying=underlying, ticker=ticker,
            api_key=api_key, http_fetch=http_fetch,
        )
        if not details:
            continue
        # Merge: greeks and implied_volatility live at the per-contract root
        if details.get("greeks"):
            c["greeks"] = details["greeks"]
        if details.get("implied_volatility") is not None:
            c["implied_volatility"] = details["implied_volatility"]
    return contracts


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
