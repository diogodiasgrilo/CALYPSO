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

# The two account kinds a strategy's orders can land on. See StrategyMeta.account_kind.
PAPER = "paper"
LIVE_MONEY = "live_money"
VALID_ACCOUNT_KINDS = (PAPER, LIVE_MONEY)


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

    # ── Added 2026-09-17 (dashboard rebuild Phase 2 — docs/DASHBOARD_REBUILD_PLAN.md) ──
    #
    # capital_basis answers "what does CAPITAL mean for this strategy", which is
    # NOT the same question as pnl_shape ("how is P&L earned"). Conflating them is
    # what made G render as an iron condor: G sells premium, so pnl_shape="credit"
    # like A/B/C/F — but its capital has no spread width to be a percentage OF,
    # so return-on-capital came out UNDEFINED rather than merely missing.
    #
    #   "defined_risk"  loss capped by spread width -> width x 100 x contracts
    #   "net_debit"     capital IS the debit paid   -> sum(open net_debit)
    #   "broker_margin" UNDEFINED risk, no structural cap -> broker requirement
    #
    # Deliberately a NEW field rather than a new pnl_shape value: pnl_shape drives
    # the comparison AXIS, and G genuinely belongs on the credit axis. Overloading
    # it would silently make G non-comparable to A/B/C on P&L, which is a
    # different wrong answer.
    # ── Added 2026-09-18 (navigation revamp) ──────────────────────────────
    #
    # A one-line SPEC, rendered under display_name in the strategy switcher.
    # The names alone carried four different conventions across seven items and
    # two of them (B and C) differed only by a parenthetical, so a reader could
    # not tell what any of them actually traded. This is the "14-inch, M3" line
    # under the product name.
    #
    # Deliberately a NEW field rather than a rewrite of display_name:
    # display_name is the ALERT IDENTITY on the live seat (see B's note below),
    # and renaming it would change what arrives in Telegram and email for the
    # only variant that places real orders. Not worth it for a label.
    # NOTE: no "/" in a subtitle. /api/strategies/meta is guarded by
    # test_no_filesystem_paths_leaked, which asserts no path separator appears
    # ANYWHERE in the payload — a blunt but effective guard that "7 entries/day"
    # tripped on its first run. Use "slots", not "per day".
    # The name as the UI shows it. Falls back to display_name when empty.
    #
    # THREE name fields is a smell and worth saying so: display_name is the
    # ALERT IDENTITY (Telegram/email on the live seat), short_name is the
    # compact tag for widgets and logs, and this is the dashboard label. They
    # exist because the three contexts genuinely differ — but the real reason
    # this one was ADDED rather than display_name being edited is that
    # renaming display_name changes what arrives in Telegram for the only
    # variant placing real orders. Not worth it for a label. Worth consolidating
    # once the live seat is not mid-Gate-4.
    #
    # The rule these values follow: the NAME says WHICH one, the SUBTITLE says
    # WHAT it is, and never both. Every deletion below removes something the
    # subtitle already states — "(7-slot)" against "7 slots", "SPY" against
    # "Multi-day SPY double calendar", "HYDRA" against a wordmark two inches to
    # its left.
    ui_name: str = ""

    subtitle: str = ""

    capital_basis: str = "defined_risk"

    # sides answers "does this strategy ever place BOTH a call and a put side".
    # F places a true one-sided subset of a 4-leg IC, so a renderer that always
    # draws both sides shows a side F never trades — the phantom short_call=0.0
    # seen in F's first recorded entry.
    sides: str = "two_sided"

    # ── Added 2026-09-18 (live-money architecture — docs/migration/LIVE_MONEY_ARCHITECTURE.md §3.2) ──
    #
    # account_kind answers "are this strategy's orders against PAPER or REAL
    # MONEY", which is NOT the same question as ``dry_run`` ("does it place real
    # orders at all") and NOT the same as ``status`` ("live" here has always meant
    # *the live PAPER seat*). Nothing in this codebase could express real money
    # before this field existed.
    #
    #   "paper"       orders go to the IBKR paper account (every variant today)
    #   "live_money"  orders go to a funded account and can lose real money
    #
    # WHY A NEW FIELD, not a new ``status`` value — this is the same mistake
    # ``capital_basis`` was added to undo. ``status`` is display metadata and is
    # also read as "is this the canonical seat"; overloading it would make
    # "which variant is the live paper seat" and "which variant trades real
    # money" the same query, and they must never be. Concretely, ``live_seat_id()``
    # resolves the seat by scanning for dry_run=false: with a real-money variant
    # ALSO at dry_run=false it saw two, failed its len(...)==1 test, and fell back
    # to naming a DRY-RUN variant as "the bot".
    #
    # DELIBERATELY NOT CONFIGURABLE PER-VM. This is keyed on the variant letter in
    # committed source, so moving a strategy onto real money is a reviewed code
    # change, never a config flip on the box. ``config_variant_*.json`` is
    # gitignored/skip-worktree and has been silently reverted by a ``git pull``
    # before (see CLAUDE.md) — that is not a file the paper/real-money boundary
    # should depend on.
    account_kind: str = "paper"


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
    "undefined_risk_0dte": GroupMeta(
        id="undefined_risk_0dte",
        label="Undefined-Risk 0DTE",
        pnl_shape="credit",  # sells premium upfront, same P&L direction as ic_0dte —
        # NOT pooled with it because the risk profile (undefined, naked shorts vs
        # ic_0dte's defined-risk wings) makes them structurally incomparable, not
        # because the pnl_shape differs.
        # comparable=False (not True like the other 2 groups): with exactly one
        # member (Strangle) today there is nothing to compare it against yet, and
        # no group-comparison renderer has been built for this group (deliberate —
        # building a head-to-head UI with nothing to compare is premature). Flip
        # this to True and build a renderer together, if/when a second
        # undefined-risk strategy is ever added — see docs/NEW_STRATEGY_PLAYBOOK.md.
        comparable=False,
    ),
    "long_gamma_0dte": GroupMeta(
        id="long_gamma_0dte",
        label="Long-Gamma 0DTE",
        # The ONLY group that BUYS premium. Every other group sells it: ic_0dte and
        # undefined_risk_0dte are credit, and calendar_multiday is net debit but
        # theta-POSITIVE. This one is net debit AND theta-negative — it pays to be
        # long gamma. That is the whole point of the group existing: its P&L is
        # expected to be anti-correlated with the rest of the fleet, profiting on
        # exactly the large-move days that hurt the sellers.
        pnl_shape="debit",
        # comparable=False, same reasoning as undefined_risk_0dte: one member, and
        # no group-comparison renderer has been built. Flip it to True and build a
        # renderer together if a second long-gamma strategy is ever added.
        comparable=False,
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
        status="dry_run_shadow",  # A is dry-run (config dry_run=true; operator-confirmed 2026-06-17). Only C is live-paper.
        bot_name_base="HYDRA",
        capital_basis="defined_risk",
        sides="two_sided",
        subtitle="0DTE SPX iron condor · 2 slots · 1 contract",
        ui_name="Baseline",
    ),
    "b": StrategyMeta(
        id="b",
        # 2026-09-02 dropped 11:15 (7 -> 6). RESTORED 2026-09-09, so B is back to
        # 7 slots and this label was stale for a day — it is the alert identity on
        # the ONLY variant that places real orders, so it must track the live
        # config. A 2026-09-10 permutation test on B's live era put the whole
        # per-slot effect at p=0.569 (11:15's entire -$875 was ONE stop on 08-28;
        # excluding it, 8 entries at +$109), so no slot moves again until it has
        # >=70 hedge-free live entries. Verified against the VM config, not assumed.
        display_name="Brandon Narrow (7-slot)",
        short_name="BRANDON-B",
        strategy_class="brandon",
        group_id="ic_0dte",
        structure_family="iron_condor",
        pnl_shape="credit",
        dte_class="0DTE",
        status="live",  # live paper seat since the 2026-07-24 B<->C swap (was dry_run_shadow before)
        bot_name_base="HYDRA",
        capital_basis="defined_risk",
        sides="two_sided",
        subtitle="0DTE SPX iron condor · 7 slots · 7 contracts",
        ui_name="Brandon Narrow",
    ),
    "bm": StrategyMeta(
        id="bm",
        # "B, money". The SAME strategy B runs, against the FUNDED account.
        #
        # The id deliberately avoids the word "live": in this codebase "the live seat"
        # has always meant the live PAPER seat, and reusing it would give "the live seat"
        # and "B-live" two different meanings in one sentence — the conflation
        # LIVE_MONEY_ARCHITECTURE.md §3.2 exists to undo. "bm" also fits the dashboard's
        # letter badge, which renders id.toUpperCase() in a one-to-two character space.
        #
        # strategy_class is "brandon", the SAME registry entry as B and C — one
        # implementation, three configurations. No registry row is needed or wanted: a
        # forked class would let the real-money path drift away from the one exercised
        # daily on paper.
        #
        # Same group as B on purpose. It is structurally identical, so it belongs on the
        # same P&L axis — and paper-B vs bm on the same signals IS the execution-drag
        # measurement. account_kind is what tells a renderer the money is real; the group
        # is about STRUCTURE, which is the distinction Phase 2 added capital_basis to make.
        display_name="Brandon Narrow (real money)",
        short_name="BRANDON-BM",
        strategy_class="brandon",
        group_id="ic_0dte",
        structure_family="iron_condor",
        pnl_shape="credit",
        dte_class="0DTE",
        # NOT "live". `status` has always meant "the live PAPER seat" and is read that
        # way by live_seat_id()'s tie-breaker; claiming it here would make this variant
        # compete for the paper seat it has nothing to do with. Real money is
        # account_kind's job, and dry_run says whether it is trading at all.
        status="dry_run_locked",
        bot_name_base="HYDRA",
        capital_basis="defined_risk",
        sides="two_sided",
        subtitle="0DTE SPX iron condor · REAL MONEY · 1 contract",
        ui_name="Brandon Narrow",
        account_kind=LIVE_MONEY,
    ),
    "c": StrategyMeta(
        id="c",
        display_name="Brandon Narrow (3-slot)",
        short_name="BRANDON-C",
        strategy_class="brandon",
        group_id="ic_0dte",
        structure_family="iron_condor",
        pnl_shape="credit",
        dte_class="0DTE",
        status="dry_run_shadow",  # dry-run sim since the 2026-07-24 B<->C swap (was live before)
        bot_name_base="HYDRA",
        capital_basis="defined_risk",
        sides="two_sided",
        subtitle="0DTE SPX iron condor · 3 slots · 7 contracts",
        ui_name="Brandon Narrow",
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
        capital_basis="net_debit",
        sides="two_sided",
        subtitle="Multi-day SPX double calendar · net debit",
        ui_name="Time Machine",
    ),
    "e": StrategyMeta(
        id="e",
        display_name="SPY Double Calendar",
        short_name="SPYDC-E",
        strategy_class="spy_double_calendar",
        group_id="calendar_multiday",
        structure_family="double_calendar",
        pnl_shape="debit",
        dte_class="multi_day",
        status="dry_run_locked",
        bot_name_base="SPYDC",
        capital_basis="net_debit",
        sides="two_sided",
        subtitle="Multi-day SPY double calendar · net debit",
        ui_name="Double Calendar",
    ),
    "f": StrategyMeta(
        id="f",
        display_name="Ghauri Mean Reversion",
        short_name="GHAURI-F",
        strategy_class="ghauri",
        # structure_family="iron_condor" (not a new taxonomy value) matches B/C's
        # own one-sided put_only/call_only fallback convention — this variant's
        # entries are a strict 2-of-4-leg subset of the same shape, so it inherits
        # ic_state routing + History/Analytics capability with no new branches.
        # NOTE (deliberate judgment call, not a default): joining ic_0dte pools
        # this strategy's aggregate P&L/win-rate with A/B/C's, even though its
        # one-sided entries are the PRIMARY structure here (not a degraded
        # fallback of a failed full-IC attempt like B/C's occasional one-sided
        # entries are) — see the memory onesided_entry_negative_expectancy. Any
        # UI/copy comparing F to A/B/C should carry that caveat.
        group_id="ic_0dte",
        structure_family="iron_condor",
        pnl_shape="credit",
        dte_class="0DTE",
        status="dry_run_locked",
        bot_name_base="HYDRA",
        capital_basis="defined_risk",
        sides="one_sided",  # a true one-sided subset of a 4-leg IC
        subtitle="0DTE SPX credit vertical · fades the expected-move boundary",
        ui_name="Ghauri",
    ),
    "g": StrategyMeta(
        id="g",
        display_name="Strangle (0DTE Naked)",
        short_name="STRANGLE-G",
        strategy_class="strangle",
        group_id="undefined_risk_0dte",
        # Genuinely NEW structure_family value (not a reuse of "iron_condor" like F) —
        # a strangle's two legs are ALWAYS naked (long_call_strike/long_put_strike
        # permanently 0.0 in strangle_strategy.py, never a real wing), unlike F's
        # entries which are a true one-sided subset of a real 4-leg IC. Reusing
        # "iron_condor" here would set is_ic=True in the dashboard backend
        # (routers/strategies.py _capabilities), turning on History/Analytics —
        # pages hard-bound to IC-shaped assumptions (spread width, wing distance)
        # this variant's entries don't have. Any structure_family other than
        # "double_calendar" still routes through the SAME already-working
        # ic_state reader (data_kind), since a strangle entry uses the identical
        # HydraIronCondorEntry fields as A/B/C/F — so it still renders correctly
        # as a standalone snapshot via the strategy picker; it just doesn't claim
        # IC-specific analytics capabilities it hasn't earned.
        structure_family="strangle",
        pnl_shape="credit",
        dte_class="0DTE",
        status="dry_run_locked",
        bot_name_base="STRANGLE",
        capital_basis="broker_margin",  # naked shorts: margin, not width
        sides="two_sided",  # two naked shorts — two-sided, just wingless  # matches StrangleStrategy.BOT_NAME (not "HYDRA")
        subtitle="0DTE SPX naked strangle · undefined risk",
        ui_name="Strangle",
    ),
    "h": StrategyMeta(
        id="h",
        display_name="Long Strangle (0DTE)",
        short_name="LONGSTRANGLE-H",
        strategy_class="long_strangle",
        group_id="long_gamma_0dte",
        # Deliberately NOT structure_family="strangle" (G's value), even though the
        # leg geometry is identical. structure_family drives dashboard capabilities,
        # and every IC/strangle-shaped consumer assumes premium was COLLECTED —
        # "expired worthless" means profit there and maximum loss here. Sharing G's
        # family would invite exactly that mis-render. New family, own renderer when
        # one is built.
        structure_family="long_strangle",
        pnl_shape="debit",
        dte_class="0DTE",
        status="dry_run_locked",
        bot_name_base="LONGSTRANGLE",  # matches LongStrangleStrategy.BOT_NAME
        # Risk is the premium paid — known before entry, needs no margin lookup and
        # no width floor. "defined_risk" is the closest existing value and is true in
        # the literal sense: the maximum loss is defined at entry.
        capital_basis="defined_risk",
        sides="two_sided",  # a long call + a long put — two legs, both bought
        subtitle="0DTE SPX long strangle · long gamma, pays theta",
        ui_name="Long Strangle",
    ),
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


def assert_account_matches(vid: str, actual_kind: Optional[str]) -> None:
    """Fail-stop guardrail: the account a variant REACHED must match the one it DECLARES.

    Sibling of :func:`assert_class_matches`, for the same class of defect — a mis-pointed
    systemd unit — but with real money on the other side of it. A strategy is bound to an
    account by exactly one line, ``CALYPSO_BROKER_URL``, and until this existed nothing
    verified the far end. A paper-intended variant pointed at the live broker would have
    placed REAL orders with no error, no alert and nothing unusual in the log.

    ``actual_kind`` is what the broker reports about itself (``None`` when it cannot say —
    an older broker that predates the identity fields, or a health read that failed).

    ASYMMETRIC ON UNKNOWN, matching ``IBClient._assert_account_matches_env``'s own
    reasoning, because the two unknowns are not equally dangerous:

    * declared ``live_money`` + broker cannot say  -> **RAISE**. Never place real orders
      against a broker whose identity is unverifiable.
    * declared ``paper`` + broker cannot say       -> **PASS** (caller warns). The worst
      case is paper orders on a paper account. Failing closed here would instead take all
      seven strategies down the first time someone restarts them before the broker — the
      2026-06-08 deploy-order bug — to protect against nothing.

    A KNOWN mismatch always raises, in BOTH directions. The reverse (a real-money variant
    reaching the paper broker) loses no money, but it would place that variant's orders
    into the paper account alongside the paper seat's own positions, corrupting the very
    record being used to decide whether to scale up.
    """
    declared = meta(vid).account_kind
    if declared not in VALID_ACCOUNT_KINDS:
        raise ValueError(
            f"variant {vid!r} declares account_kind={declared!r}, which is not one of "
            f"{VALID_ACCOUNT_KINDS}; refusing to start."
        )
    if actual_kind is None:
        if declared == LIVE_MONEY:
            raise ValueError(
                f"variant {vid!r} declares account_kind={LIVE_MONEY!r} but the broker did "
                f"not report its identity, so the account cannot be verified. Refusing to "
                f"place real-money orders against an unverifiable broker — check that "
                f"calypso-broker is running code new enough to report `environment`."
            )
        return
    if actual_kind not in VALID_ACCOUNT_KINDS:
        raise ValueError(
            f"broker reported an unrecognised account kind {actual_kind!r} for variant "
            f"{vid!r}; refusing to start rather than guess."
        )
    if actual_kind != declared:
        raise ValueError(
            f"ACCOUNT MISMATCH for variant {vid!r}: it declares account_kind={declared!r} "
            f"but the broker it reached is {actual_kind!r}. A systemd unit is almost "
            f"certainly pointed at the wrong CALYPSO_BROKER_URL; refusing to start."
        )


def account_kind_for_environment(environment: Optional[str]) -> Optional[str]:
    """Translate the BROKER's vocabulary into the taxonomy's. ``None`` when unknown.

    The broker reports ``environment`` as ``paper``/``live`` — the IBKR credential
    environment, the same token ``$CALYPSO_IBKR_ENV`` and ``_keys_dir()`` use. The
    taxonomy says ``paper``/``live_money``, because "live" in this codebase already
    means *the live PAPER seat* and reusing it here is precisely the conflation
    LIVE_MONEY_ARCHITECTURE.md §3.2 exists to undo.

    One translation site on purpose: the broker stays free of strategy vocabulary and
    keeps publishing raw facts, and no consumer has to re-derive this mapping.
    Anything unrecognised returns ``None`` (= "cannot say") rather than guessing, which
    routes into ``assert_account_matches``'s asymmetric unknown handling.
    """
    if not isinstance(environment, str):
        return None
    env = environment.strip().lower()
    if env == "paper":
        return PAPER
    if env == "live":
        return LIVE_MONEY
    return None


def ids_for_account_kind(kind: str) -> List[str]:
    """Variant letters whose orders land on ``kind``, in definition order.

    Replaces hardcoded seat tuples (``LIVE_SEAT_IDS = ("b", "c")``) so a new variant is
    picked up by existing code instead of needing an edit nobody remembers to make.
    """
    return [sid for sid, m in STRATEGIES.items() if m.account_kind == kind]


def available_ids() -> List[str]:
    """All registered strategy letters, in definition order."""
    return list(STRATEGIES.keys())
