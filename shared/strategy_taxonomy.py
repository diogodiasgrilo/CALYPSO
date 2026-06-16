"""Central strategy taxonomy — the single source of truth for "what is this strategy
and what may it be compared against".

Today every surface (dashboard, Telegram, email, DB, logging) identifies strategies by
the bare variant LETTER (``HYDRA_VARIANT_ID`` = a/b/c/d/...), and "is this strategy
comparable to that one?" is hardcoded in ~5 scattered places (``if vid == "d": continue``
guards, ``_VARIANT_IDS = ["a","b","c"]``, the ``main.py`` banner ``if variant == "d"``).
This module replaces those scattered guards with ONE data structure:

  * the LETTER stays the stable internal key (filesystem ``data/variant_<letter>/``, env
    ``HYDRA_VARIANT_ID``, ``BOT_NAME``, alert-wire ``bot_name``, systemd units — NONE change);
  * human ``display_name`` + comparability ``group`` are a presentation/semantics overlay
    layered on top of that unchanged key.

Comparison refuses credit-vs-debit *by construction* (a group carries a single ``pnl_shape``),
not by scattered ``if`` guards.

IMPORTANT — this module is deliberately STDLIB-ONLY and dependency-free. It is imported by
the trading process (``bots/hydra``), by ``shared/``, AND by the read-only dashboard backend
(which must NOT pull in trading/broker code — see ``dashboard``'s "does NOT import IBClient"
guarantee). ``tests/test_strategy_taxonomy.py`` asserts the import purity. Do NOT add any
intra-``shared`` import (e.g. ``alert_service`` drags in ``google.cloud``) or any ``bots`` import.

Two guardrails the consumers MUST honor (audit AUD-1 / AUD-4):
  1. The ``strategy_class`` field here can LIE — the real letter->class binding is computed at
     runtime by ``bots.hydra.registry.resolve_strategy_name`` from each variant's own (VM-local,
     skip-worktree'd) config, via two different mechanisms (``brandon.enabled`` for B/C vs
     ``strategy.name`` for D). Callers in the trading process should assert the table against the
     resolver at startup and FAIL-STOP on mismatch (``assert_class_matches`` below).
  2. An unknown letter must degrade gracefully (``comparable=False``, no group), NEVER raise —
     ``meta()`` returns a safe sentinel so adding a future variant F can't crash the banner /
     discovery / dashboard before its row is added here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional

# The variant whose HYDRA_VARIANT_ID env var is unset (the original single-bot / poller-owner).
DEFAULT_ID = "a"


@dataclass(frozen=True)
class GroupMeta:
    """A comparability cohort. Members share one ``pnl_shape`` so a credit iron condor and a
    net-debit calendar are never put on the same P&L axis."""

    id: str
    label: str
    pnl_shape: str  # "credit" | "debit" | "unknown"
    comparable: bool  # may members share a leaderboard / P&L axis?


@dataclass(frozen=True)
class StrategyMeta:
    """One running strategy instance, keyed by its (unchanged) variant letter."""

    id: str  # the variant letter — STABLE internal key, never changes
    display_name: str  # human label for dashboard/Telegram/email headers
    short_name: str  # compact tag for widgets/logs (NOT the alert-wire bot_name)
    strategy_class: str  # registry key: "hydra"/"brandon"/"double_calendar"/... ("" if unknown)
    group_id: str  # FK -> GROUPS
    structure_family: str  # "iron_condor" | "double_calendar" | "unknown"
    pnl_shape: str  # "credit" | "debit" | "unknown" (mirrors the group)
    dte_class: str  # "0DTE" | "multi_day" | "unknown"
    status: str  # operational metadata: "live" | "dry_run_shadow" | "dry_run_locked" (display only)
    bot_name_base: str  # the BOT_NAME class const it runs under ("HYDRA"/"DCTM"/...)


# ---------------------------------------------------------------------------
# The taxonomy data. Add a row here (and a GroupMeta if a new cohort) to register
# a strategy — this is step 1 of "adding a new variant".
# ---------------------------------------------------------------------------

GROUPS: Dict[str, GroupMeta] = {
    "ic_0dte": GroupMeta(
        id="ic_0dte",
        label="0DTE Iron Condor",
        pnl_shape="credit",
        comparable=True,
    ),
    "calendar_multiday": GroupMeta(
        id="calendar_multiday",
        label="Multi-day Calendar",
        pnl_shape="debit",
        comparable=True,
    ),
}

# Sentinel group for an unregistered letter (never comparable, no axis).
_UNKNOWN_GROUP = GroupMeta(id="unknown", label="Unknown", pnl_shape="unknown", comparable=False)

STRATEGIES: Dict[str, StrategyMeta] = {
    "a": StrategyMeta(
        id="a",
        display_name="HYDRA Baseline",
        short_name="HYDRA-A",
        strategy_class="hydra",
        group_id="ic_0dte",
        structure_family="iron_condor",
        pnl_shape="credit",
        dte_class="0DTE",
        status="live",  # operational metadata only; confirm on VM
        bot_name_base="HYDRA",
    ),
    "b": StrategyMeta(
        id="b",
        display_name="Brandon Narrow (4-slot)",
        short_name="BRANDON-B",
        strategy_class="brandon",
        group_id="ic_0dte",
        structure_family="iron_condor",
        pnl_shape="credit",
        dte_class="0DTE",
        status="dry_run_shadow",
        bot_name_base="HYDRA",
    ),
    "c": StrategyMeta(
        id="c",
        display_name="Brandon Narrow (live)",
        short_name="BRANDON-C",
        strategy_class="brandon",
        group_id="ic_0dte",
        structure_family="iron_condor",
        pnl_shape="credit",
        dte_class="0DTE",
        status="live",
        bot_name_base="HYDRA",
    ),
    "d": StrategyMeta(
        id="d",
        display_name="DC Time Machine",
        short_name="DCTM-D",
        strategy_class="double_calendar",
        group_id="calendar_multiday",
        structure_family="double_calendar",
        pnl_shape="debit",
        dte_class="multi_day",
        status="dry_run_locked",
        bot_name_base="DCTM",
    ),
    # Variant "e" (the SPY double-calendar sibling of D) is added in its build phase,
    # once its registry class + name are created. Its row will be:
    #   group_id="calendar_multiday", structure_family="double_calendar",
    #   pnl_shape="debit", dte_class="multi_day", status="dry_run_locked".
}


def _unknown_meta(vid: str) -> StrategyMeta:
    """Safe sentinel for a letter with no registered row (graceful degradation, never KeyError)."""
    up = vid.upper()
    return StrategyMeta(
        id=vid,
        display_name=f"Variant {up}",
        short_name=f"VARIANT-{up}",
        strategy_class="",
        group_id=_UNKNOWN_GROUP.id,
        structure_family="unknown",
        pnl_shape="unknown",
        dte_class="unknown",
        status="unknown",
        bot_name_base="HYDRA",
    )


def variant_id() -> str:
    """The current process's variant letter — the ONE canonical env parse (replaces ~8 copies).

    Unset/blank ``HYDRA_VARIANT_ID`` resolves to :data:`DEFAULT_ID` ("a"), matching the legacy
    single-bot behavior. Always lowercased/stripped.
    """
    return (os.environ.get("HYDRA_VARIANT_ID", "") or "").strip().lower() or DEFAULT_ID


def is_default_variant() -> bool:
    """True when ``HYDRA_VARIANT_ID`` is unset/blank — i.e. variant A, the Telegram-poller owner.

    Callers that gate poller ownership MUST use this (the raw env check), NOT a taxonomy field
    like ``status``/``is_primary`` (audit AUD-1-M2), so exactly one process ever starts the poller.
    """
    return not (os.environ.get("HYDRA_VARIANT_ID", "") or "").strip()


def meta(vid: Optional[str] = None) -> StrategyMeta:
    """Look up a strategy by letter (defaults to the current process). Unknown letter -> safe sentinel."""
    key = (vid if vid is not None else variant_id()).strip().lower()
    return STRATEGIES.get(key) or _unknown_meta(key)


def group(vid: Optional[str] = None) -> GroupMeta:
    """The comparability group for a strategy (defaults to the current process)."""
    return GROUPS.get(meta(vid).group_id, _UNKNOWN_GROUP)


def members(group_id: str) -> List[str]:
    """Registered strategy letters in a group, in definition order (empty for an unknown group)."""
    return [sid for sid, m in STRATEGIES.items() if m.group_id == group_id]


def comparable_with(vid: str) -> List[str]:
    """Other registered strategies a strategy may be compared against (same comparable group only).

    Returns ``[]`` for a non-comparable / unknown group — the apples-to-apples guarantee, by data.
    """
    g = group(vid)
    if not g.comparable:
        return []
    key = vid.strip().lower()
    return [sid for sid in members(g.id) if sid != key]


def groups_of(ids: List[str]) -> Dict[str, List[str]]:
    """Cluster a set of letters into ``{group_id: [letters]}`` (preserves input order per group)."""
    out: Dict[str, List[str]] = {}
    for vid in ids:
        gid = meta(vid).group_id
        out.setdefault(gid, []).append(vid.strip().lower())
    return out


def bot_name(vid: Optional[str] = None) -> str:
    """The unified ``{bot_name_base}_{ID}`` name (A = bare base, no suffix — the legacy form).

    NOTE: this is the proposed UNIFIED naming for display/banner. It is NOT a drop-in for the
    alert-wire ``bot_name`` (the anti-spam dedup partition key) nor the monitor-log column — those
    are frozen (audit AUD-1-H1). Use this for banners/startup lines/widgets only.
    """
    m = meta(vid)
    key = m.id
    if key == DEFAULT_ID:
        return m.bot_name_base
    return f"{m.bot_name_base}_{key.upper()}"


def display_name(vid: Optional[str] = None) -> str:
    """Human label for headers/alerts (e.g. ``"Brandon Narrow (live)"``)."""
    return meta(vid).display_name


def assert_class_matches(vid: str, resolved_class: str) -> None:
    """Fail-stop guardrail: the static ``strategy_class`` must match the runtime-resolved class.

    Call this at startup with the result of ``bots.hydra.registry.resolve_strategy_name(config)``.
    A mismatch means a variant's systemd unit points at the wrong ``--config`` (the letter<->class
    binding is convention, enforced nowhere) — fail loudly rather than render a debit calendar as a
    0DTE iron condor across every surface (audit AUD-4-F1). Unknown letters (empty expected class)
    are skipped — they have no committed binding to validate.
    """
    expected = meta(vid).strategy_class
    if not expected:
        return
    if resolved_class != expected:
        raise ValueError(
            f"strategy taxonomy mismatch for variant {vid!r}: taxonomy expects "
            f"strategy_class={expected!r} but registry resolved {resolved_class!r}. "
            "A systemd unit is likely pointed at the wrong --config; refusing to start."
        )


def available_ids() -> List[str]:
    """All registered strategy letters, in definition order."""
    return list(STRATEGIES.keys())
