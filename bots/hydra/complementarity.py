"""Conditional-performance analysis across HYDRA strategies.

Implements `docs/STRATEGY_COMPLEMENTARITY_ANALYSIS.md`, which was PRE-REGISTERED
before this module existed. Hypotheses, bucket boundaries and pass/fail criteria
are fixed there; this module only executes them.

WHY THE PERMUTATION TEST IS NOT OPTIONAL HERE. On 2026-09-02 the 11:15 entry slot
was cut for being the worst of seven, and a later permutation test put the ENTIRE
per-slot effect at p=0.569 — the cut was reversed. The method was "rank N things,
keep the best" with no test against chance. Partitioning ~26 days into three
buckets and reading off the means is that same method. So every effect this
module reports carries a permutation p-value, and the report prints the failures
as prominently as the successes.

THE PURE FUNCTIONS ARE THE POINT. `bucket_by_terciles`, `bootstrap_ci` and
`permutation_p` take plain numbers and are unit-tested against known answers, so
the statistics can be trusted independently of whatever the database happens to
contain on a given day.
"""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass, field
from statistics import mean, median
from typing import Callable, Dict, List, Optional, Sequence, Tuple

#: Fixed by the pre-registration. Do not tune to results.
N_BOOTSTRAP = 10_000
N_PERMUTATION = 10_000
BUCKET_LABELS = ("quiet", "moderate", "trending")


@dataclass
class DayRow:
    """One traded session for one variant, normalised per contract."""

    date: str
    variant: str
    pnl_per_contract: float
    move_pct: float
    range_pct: float
    vix_open: Optional[float]
    contracts: int


@dataclass
class BucketStat:
    label: str
    n: int
    mean: float
    median: float
    ci_low: float
    ci_high: float

    @property
    def spans_zero(self) -> bool:
        """A CI crossing zero is 'no signal' — never report it as a direction."""
        return self.ci_low <= 0.0 <= self.ci_high


@dataclass
class VariantResult:
    variant: str
    n_days: int
    buckets: List[BucketStat] = field(default_factory=list)
    permutation_p: Optional[float] = None
    note: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Pure statistics — unit-tested independently of any database
# ─────────────────────────────────────────────────────────────────────────────

def tercile_edges(values: Sequence[float]) -> Tuple[float, float]:
    """The two cut points splitting `values` into thirds.

    Computed on the POOLED distribution across all traded days, so the buckets
    do not shift depending on which variant is being examined — comparing
    variants against differently-drawn boundaries would be meaningless.
    """
    if len(values) < 3:
        raise ValueError("need at least 3 values to form terciles")
    s = sorted(values)
    return s[len(s) // 3], s[(2 * len(s)) // 3]


def bucket_by_terciles(value: float, edges: Tuple[float, float]) -> str:
    lo, hi = edges
    if value < lo:
        return BUCKET_LABELS[0]
    if value < hi:
        return BUCKET_LABELS[1]
    return BUCKET_LABELS[2]


def bootstrap_ci(
    sample: Sequence[float],
    n_resamples: int = N_BOOTSTRAP,
    confidence: float = 0.95,
    rng: Optional[random.Random] = None,
) -> Tuple[float, float]:
    """Percentile bootstrap CI for the mean. Returns (low, high).

    A single-observation sample has no spread to resample, so it returns a
    degenerate interval at that value rather than a fake one — the caller must
    still see `n=1` and disregard it.
    """
    if not sample:
        raise ValueError("empty sample")
    if len(sample) == 1:
        return float(sample[0]), float(sample[0])
    r = rng or random.Random(0xC0FFEE)  # deterministic: a report must reproduce
    n = len(sample)
    means = []
    for _ in range(n_resamples):
        means.append(mean(r.choices(sample, k=n)))
    means.sort()
    tail = (1.0 - confidence) / 2.0
    return means[int(tail * n_resamples)], means[int((1.0 - tail) * n_resamples) - 1]


def permutation_p(
    labelled: Sequence[Tuple[str, float]],
    statistic: Callable[[Dict[str, List[float]]], float],
    n_permutations: int = N_PERMUTATION,
    rng: Optional[random.Random] = None,
) -> float:
    """Two-sided p: how often does shuffling the labels do as well as reality?

    `labelled` is [(bucket, value), …]. The labels are shuffled while the values
    stay put, so the null is "the bucket assignment carries no information" —
    exactly the claim being made when someone says one regime is different.

    Returns the fraction of shuffles whose |statistic| >= the observed |statistic|.
    A high value means the observed spread is what chance produces anyway.
    """
    if len(labelled) < 3:
        return 1.0
    r = rng or random.Random(0xBEEF)
    labels = [lab for lab, _ in labelled]
    values = [val for _, val in labelled]

    def grouped(labs: Sequence[str]) -> Dict[str, List[float]]:
        out: Dict[str, List[float]] = {}
        for lab, val in zip(labs, values):
            out.setdefault(lab, []).append(val)
        return out

    observed = abs(statistic(grouped(labels)))
    shuffled = list(labels)
    hits = 0
    for _ in range(n_permutations):
        r.shuffle(shuffled)
        if abs(statistic(grouped(shuffled))) >= observed:
            hits += 1
    return hits / n_permutations


def spread_statistic(groups: Dict[str, List[float]]) -> float:
    """Trending-bucket mean minus quiet-bucket mean.

    The pre-registered effect: a strategy whose losses are a trend-day
    phenomenon shows a large NEGATIVE value. Buckets absent from a shuffle
    contribute 0 rather than raising, so the permutation loop cannot crash on a
    degenerate draw.
    """
    q = groups.get(BUCKET_LABELS[0]) or []
    t = groups.get(BUCKET_LABELS[2]) or []
    if not q or not t:
        return 0.0
    return mean(t) - mean(q)


# ─────────────────────────────────────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────────────────────────────────────

def load_days(db_path: str, variant: str, since: str) -> List[DayRow]:
    """Traded sessions for one variant, normalised per contract.

    Skips rows that cannot yield a regime feature (missing SPX open/close) rather
    than defaulting them to zero — a fabricated 0% move would land in the quiet
    bucket and quietly bias every comparison.
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    out: List[DayRow] = []
    try:
        rows = con.execute(
            "SELECT date, spx_open, spx_close, spx_high, spx_low, vix_open, "
            "       net_pnl, entries_placed, contracts_per_entry "
            "FROM daily_summaries WHERE date >= ? AND entries_placed > 0 "
            "ORDER BY date",
            (since,),
        ).fetchall()
    finally:
        con.close()

    for r in rows:
        o, c = r["spx_open"], r["spx_close"]
        if not o or not c or o <= 0:
            continue
        hi, lo = r["spx_high"] or c, r["spx_low"] or c
        contracts = int(r["contracts_per_entry"] or 1) or 1
        out.append(
            DayRow(
                date=r["date"],
                variant=variant,
                pnl_per_contract=(r["net_pnl"] or 0.0) / contracts,
                move_pct=abs(c - o) / o * 100.0,
                range_pct=(hi - lo) / o * 100.0,
                vix_open=r["vix_open"],
                contracts=contracts,
            )
        )
    return out


def analyse_variant(
    days: Sequence[DayRow],
    edges: Tuple[float, float],
    feature: str,
    min_per_bucket: int = 3,
) -> VariantResult:
    """Bucket one variant's days and test the spread against chance."""
    if not days:
        return VariantResult(variant="?", n_days=0, note="no traded days")
    res = VariantResult(variant=days[0].variant, n_days=len(days))

    labelled: List[Tuple[str, float]] = [
        (bucket_by_terciles(getattr(d, feature), edges), d.pnl_per_contract)
        for d in days
    ]
    groups: Dict[str, List[float]] = {}
    for lab, val in labelled:
        groups.setdefault(lab, []).append(val)

    for label in BUCKET_LABELS:
        vals = groups.get(label) or []
        if not vals:
            continue
        lo, hi = bootstrap_ci(vals)
        res.buckets.append(
            BucketStat(label, len(vals), mean(vals), median(vals), lo, hi)
        )

    thin = [b for b in res.buckets if b.n < min_per_bucket]
    if len(res.buckets) < 2 or thin:
        res.note = (
            f"too thin to test ({', '.join(f'{b.label} n={b.n}' for b in res.buckets)})"
        )
        return res
    res.permutation_p = permutation_p(labelled, spread_statistic)
    return res
