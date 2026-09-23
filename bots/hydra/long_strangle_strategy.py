"""LongStrangleStrategy — a 0DTE SPX **LONG** strangle (variant H). Step 1 scaffold.

The structural mirror of ``StrangleStrategy`` (variant G): the same two legs, the
opposite sign. G **sells** an OTM call and an OTM put; this **buys** them. That one
difference inverts almost everything that matters:

  - **NET DEBIT, not credit.** Premium is paid, not collected.
  - **LONG gamma, SHORT theta.** It profits from a large move in either direction
    and bleeds when the market sits still — the exact opposite of A/B/C/F/G, all
    of which are premium sellers, and of D/E, which are net debit but theta-POSITIVE.
  - **Max loss is the debit, by construction.** There is no stop-loss, because
    there is nothing to stop out of: the worst case is both legs expiring
    worthless. No GUARD-FLOOR, no A2 %-of-width, no MKT-046 anti-spike, no buffer
    decay. This is the only strategy in the fleet whose risk is bounded without
    any machinery at all.
  - **Naked-short exposure is structurally impossible.** A partial fill leaves a
    LONG option, never an unhedged short. The entry-execution failure that cost
    variant B $159.50 on 2026-09-22 cannot occur in this structure.

WHY IT EXISTS. Not because its source's numbers are believed — they are not; see
``docs/LONG_STRANGLE_STRATEGY_SPECIFICATION.md`` §1, where 80% winners at
+50–100% against losers at −100% works out to roughly +40% expected per trade,
which would be the best documented edge in retail options. It exists to MEASURE
something the fleet currently cannot answer: **does long gamma hedge the
short-gamma book's bad days?** 2026-09-21 (SPX +1.1%, B's worst live session at
−$441/contract) and 2026-09-22 (a 20-point range, B positive) are the two sides
of that question.

BUILD STATUS — **Step 1 of ``docs/NEW_STRATEGY_PLAYBOOK.md`` ONLY.** This class is
a registered, dry-run-LOCKED, **INERT** scaffold: it starts, it is schedulable, and
it deliberately implements **no entry logic yet**. Steps 2–5 (debit entry model,
expected-move strike selection, percent-of-debit exits, sizing-for-zero) are not
written. Do not read the presence of this file as a working strategy.

Spec + build-weight decision: ``docs/LONG_STRANGLE_STRATEGY_SPECIFICATION.md``
(MEDIUM build — skip Step 6, include a small Step 2 model and a Step 7 isolated DB,
because ``IronCondorEntry``'s P&L is credit-shaped in every branch and
``trade_entries`` has no debit columns).
"""

from __future__ import annotations

import logging

from bots.hydra.base_strategy import ConfigError
from bots.hydra.strategy import HydraStrategy

logger = logging.getLogger(__name__)


class LongStrangleStrategy(HydraStrategy):
    """0DTE SPX long strangle: buy an OTM call + an OTM put at the expected move.

    Inherits HYDRA's scheduling / monitoring / state machinery. Like variant G it
    sets ``requires_protective_wings = False`` — but for the opposite reason. G
    sets it because its unhedged SHORTS are the intended position and must not be
    auto-closed as a naked-short emergency. Here there are no shorts at all: both
    legs are long, so the base's naked-short guard has nothing to act on either
    way. The flag is set so the shared safety path does not mistake a two-leg
    structure for half of a broken iron condor.
    """

    BOT_NAME = "LONGSTRANGLE"

    #: No shorts anywhere in this structure — see the class docstring for why this
    #: is the same flag as G's but not the same reasoning.
    requires_protective_wings = False

    #: The base heartbeat renders an "E1-EN: full IC" schedule line written for a
    #: 4-leg iron condor, which misdescribes a 2-leg structure. Cosmetic/log-only,
    #: mirroring StrangleStrategy and GhauriMeanReversion.
    _show_ic_schedule_in_heartbeat = False

    def __init__(self, *args, **kwargs):
        """Construct, then enforce the dry-run-only gate.

        Locked for a different reason from G's. G is locked because it carries
        UNDEFINED risk and arming it is a deliberate operator decision. This is
        locked because it is an **unfinished Step 1 scaffold** with no entry
        logic — its risk is bounded by construction, but a half-built strategy
        must not be armed regardless.

        The kwarg is checked BEFORE ``super().__init__`` so an illegal live
        construction never reaches the base init's broker I/O. ``build_strategy``
        always passes ``dry_run`` as a kwarg.
        """
        if not kwargs.get("dry_run", False):
            raise ConfigError(
                "LongStrangleStrategy is dry-run-LOCKED: it is a Step 1 scaffold "
                "with NO entry logic implemented (see "
                "docs/LONG_STRANGLE_STRATEGY_SPECIFICATION.md). Set dry_run=true, "
                "or do not select strategy.name='long_strangle'."
            )
        super().__init__(*args, **kwargs)
        # Defense-in-depth: re-check after super in case dry_run is derived
        # differently downstream (it should equal the kwarg).
        if not getattr(self, "dry_run", False):
            raise ConfigError(
                "LongStrangleStrategy resolved to dry_run=false after init — "
                "refusing to arm an unfinished strategy scaffold."
            )
        logger.info(
            "LongStrangleStrategy (variant H) constructed — Step 1 scaffold, "
            "INERT: no entry logic implemented yet."
        )
