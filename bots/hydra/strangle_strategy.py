"""StrangleStrategy — a 0DTE SPX short strangle (modularity-audit driver).

This is the concrete second strategy that proves the modularity work: it reuses
the base machinery (scheduling, monitoring loop, state, instrument params, the
market-data kernels) and only supplies what is genuinely strategy-specific via
the item-4b extension hooks.

What makes it different from the iron-condor family:
  - **2 naked legs, no protective wings** — a short call + a short put, both ~Nδ
    OTM. ``requires_protective_wings = False`` (item-6a) so the base never treats
    its intended shorts as a naked-short emergency.
  - **Undefined risk** — sized by broker margin, not the defined-risk floor
    (wired in a later step, S2).

Build status: registered and dry-run-RUNNABLE, NOT hardened for live. It IS
registered in ``bots/hydra/registry.py`` ("strangle") and selectable via
``strategy.name="strangle"``, so it is **gated DRY-RUN ONLY in __init__** — a
non-dry-run construction raises ``ConfigError``. Two pre-live gates remain open:
  - audit **S-CRIT-1**: the naked-short stop is not yet wired through
    ``_validate_pnl_sanity`` (the full-IC P&L-sanity guard rejects every tick at
    long=0), so the stop would NOT fire — undefined risk with no working stop.
  - **S2**: margin sizing still uses the defined-risk IC floor, not broker margin.
Going live requires both fixed + paper verification + an explicit operator flip.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from bots.hydra.base_strategy import ConfigError, MEICState
from bots.hydra.order_types import BuySell
from bots.hydra.strategy import HydraIronCondorEntry, HydraStrategy
from shared.event_calendar import is_fomc_t_plus_one
from shared.market_hours import get_us_market_time

logger = logging.getLogger(__name__)


class StrangleStrategy(HydraStrategy):
    """0DTE SPX short strangle: two naked short legs at a symmetric OTM distance.

    Inherits HYDRA's scheduling / monitoring / state machinery and overrides only
    the strategy-defining hooks. ``requires_protective_wings = False`` is the
    crux — it tells the shared safety path (item-6a) that unhedged shorts are the
    position here, not an emergency to be auto-closed.
    """

    BOT_NAME = "STRANGLE"
    requires_protective_wings = False  # undefined-risk: naked shorts by design

    def __init__(self, *args, **kwargs):
        """Construct, then enforce the dry-run-only gate (audit S-HIGH-3).

        The strangle is registered + selectable, but two pre-live gates are still
        open: its naked-short stop is not yet wired (S-CRIT-1 — it would NOT stop)
        and margin sizing is the defined-risk IC floor, not broker margin (S2).
        Until those land + paper verification, refuse any non-dry-run construction
        so a stray ``strategy.name="strangle"`` on a ``dry_run=false`` variant
        cannot silently arm real, un-stopped, undefined-risk naked-short trading.
        """
        super().__init__(*args, **kwargs)
        if not getattr(self, "dry_run", False):
            raise ConfigError(
                "StrangleStrategy is dry-run-only until hardened: the naked-short "
                "stop is not yet wired (audit S-CRIT-1) and sizing uses the "
                "defined-risk IC margin floor (S2). Set dry_run=true, or do not "
                "select strategy.name='strangle'."
            )

    def _calculate_strikes(self, entry: HydraIronCondorEntry) -> bool:
        """Select the two short strikes — a short call and a short put at a
        symmetric ~target_delta OTM distance. No long wings (strikes left 0).

        Reuses the base 8δ-anchored OTM math and the instrument strike grid
        (``_snap_to_grid`` → ``self.strike_increment``), so it is instrument-
        and grid-agnostic for free. Returns True if both strikes were set.
        """
        spx = self.current_price
        if spx <= 0:
            logger.error("Strangle: cannot calculate strikes — no SPX price")
            return False

        vix = self.current_vix if self.current_vix > 0 else 15.0
        rounded_spx = self._snap_to_grid(spx)

        # Same 8-delta-anchored OTM distance as the iron-condor base, snapped to
        # the configured strike grid. Symmetric: a classic strangle places the
        # call and put the same distance OTM.
        base_distance_at_vix15 = 40  # ~8δ at VIX 15
        delta_adjustment = 8.0 / self.target_delta
        vix_factor = max(0.7, min(2.5, vix / 15.0))
        otm_distance = self._snap_to_grid(base_distance_at_vix15 * vix_factor * delta_adjustment)
        otm_distance = max(25, min(180, otm_distance))

        # Two naked shorts; no protective long wings.
        entry.short_call_strike = rounded_spx + otm_distance
        entry.short_put_strike = rounded_spx - otm_distance
        entry.long_call_strike = 0.0
        entry.long_put_strike = 0.0

        logger.info(
            f"STRANGLE strikes: SC {entry.short_call_strike:.0f} / "
            f"SP {entry.short_put_strike:.0f} (±{otm_distance:.0f}pt OTM, VIX {vix:.1f})"
        )
        return True

    def _estimate_short_premium(self, uic) -> float:
        """Per-contract premium (option points) of a short leg — mid, then last,
        then mark. 0.0 if the leg has no uic or no usable quote. Reuses the base
        normalized-quote reader (`_read_option_quote`)."""
        if not uic:
            return 0.0
        quote = self._read_option_quote(uic) or {}
        for key in ("mid", "last", "mark"):
            value = quote.get(key)
            if value is not None:
                return float(value)
        return 0.0

    def _simulate_entry(self, entry: HydraIronCondorEntry) -> bool:
        """Dry-run entry: resolve the two short conids, book the premium collected,
        and assign synthetic DRY ids — the strangle analogue of the base IC
        simulation (which requires long wings the strangle doesn't have).

        Credit is the sum of the two short premiums (no long debit — naked). The
        entry's spread-value / unrealized-pnl math collapses correctly because the
        unused long legs price at 0 (long_*_price stays 0.0).
        """
        expiry = self._get_todays_expiry()
        if not expiry:
            logger.error(f"[DRY RUN] Strangle: no expiry for entry #{entry.entry_number}")
            return False

        # Resolve the two short conids so heartbeat monitoring fetches real quotes.
        if entry.short_call_strike:
            entry.short_call_uic = self._get_option_uic(entry.short_call_strike, "Call", expiry) or 0
        if entry.short_put_strike:
            entry.short_put_uic = self._get_option_uic(entry.short_put_strike, "Put", expiry) or 0

        # Credit = premium collected on each naked short (per-contract × 100 × contracts).
        call_premium = self._estimate_short_premium(entry.short_call_uic)
        put_premium = self._estimate_short_premium(entry.short_put_uic)
        entry.call_spread_credit = call_premium * 100 * self.contracts_per_entry
        entry.put_spread_credit = put_premium * 100 * self.contracts_per_entry
        entry.short_call_fill_price = call_premium
        entry.short_put_fill_price = put_premium

        # Synthetic DRY ids for the two shorts (longs are unused).
        base_id = int(datetime.now().timestamp() * 1000)
        entry.short_call_position_id = f"DRY_{base_id}_SC"
        entry.short_put_position_id = f"DRY_{base_id}_SP"
        entry.is_complete = True

        logger.info(
            f"[DRY RUN] Simulated STRANGLE entry #{entry.entry_number}: "
            f"SC {entry.short_call_strike:.0f} (${entry.call_spread_credit:.2f}) / "
            f"SP {entry.short_put_strike:.0f} (${entry.put_spread_credit:.2f}), "
            f"total ${entry.total_credit:.2f}"
        )
        return True

    def _calculate_stop_levels_hydra(self, entry: HydraIronCondorEntry) -> None:
        """Strangle stop policy: each naked short stops on ITS OWN credit + buffer.

        This is the key exit difference from the iron-condor base, which stops
        both sides on the *total* credit (the IC is managed as one unit). A
        strangle's two legs are independent naked positions, so the call stops
        when buying it back costs more than the call premium + buffer, and the
        put likewise on its own premium. Reuses the base buffer convention
        (`call_stop_buffer`/`put_stop_buffer` × contracts, regime-adjusted
        upstream) and the per-side MIN_STOP_LEVEL floor — only the credit basis
        differs (per-side, not total).
        """
        n = self.contracts_per_entry
        min_stop_level = 50.0 * n
        call_buf = self.call_stop_buffer * n
        put_buf = self.put_stop_buffer * n

        call_credit = max(entry.call_spread_credit, min_stop_level)
        put_credit = max(entry.put_spread_credit, min_stop_level)
        entry.call_side_stop = call_credit + call_buf
        entry.put_side_stop = put_credit + put_buf

        logger.info(
            f"STRANGLE stops (per-side): call ${entry.call_side_stop:.2f} "
            f"(credit ${call_credit:.2f} + buf ${call_buf:.2f}), put "
            f"${entry.put_side_stop:.2f} (credit ${put_credit:.2f} + buf ${put_buf:.2f})"
        )

    def _execute_entry(self, entry: HydraIronCondorEntry) -> bool:
        """Real-order entry: SELL the two naked shorts. No protective longs and
        no hedge-pairing — the strangle is undefined-risk by design (S1 keeps an
        unhedged short off the emergency path). On a partial fill the base
        unwinder closes whatever filled, aborting the entry cleanly.

        Credit on each side is the full short premium (no long debit). Mirrors
        the base IC placement primitives (`_place_option_order`,
        `_register_position`, `_unwind_partial_entry`) — only the leg set differs.
        """
        expiry = self._get_todays_expiry()
        if not expiry:
            logger.error("Strangle: could not determine expiry")
            return False
        if not (entry.short_call_strike and entry.short_put_strike):
            logger.error(
                f"Strangle entry #{entry.entry_number}: both short strikes "
                f"required (SC {entry.short_call_strike} / SP {entry.short_put_strike})"
            )
            return False

        filled_legs = []
        try:
            logger.info(f"STRANGLE: selling Short Call at {entry.short_call_strike}")
            sc = self._place_option_order(
                strike=entry.short_call_strike, put_call="Call",
                buy_sell=BuySell.SELL, expiry=expiry,
                external_ref=f"{entry.strategy_id}_SC",
            )
            if not sc:
                raise Exception("Short Call order failed")
            entry.short_call_position_id = sc.get("position_id")
            entry.short_call_uic = sc.get("uic")
            entry.short_call_fill_price = sc.get("fill_price", 0)
            entry.short_call_mid_at_fill = sc.get("mid_at_fill", 0)
            entry.call_spread_credit = sc.get("credit", 0)  # naked: full premium, no long debit
            filled_legs.append(("short_call", entry.short_call_position_id, entry.short_call_uic))
            self._register_position(entry, "short_call")

            logger.info(f"STRANGLE: selling Short Put at {entry.short_put_strike}")
            sp = self._place_option_order(
                strike=entry.short_put_strike, put_call="Put",
                buy_sell=BuySell.SELL, expiry=expiry,
                external_ref=f"{entry.strategy_id}_SP",
            )
            if not sp:
                raise Exception("Short Put order failed")
            entry.short_put_position_id = sp.get("position_id")
            entry.short_put_uic = sp.get("uic")
            entry.short_put_fill_price = sp.get("fill_price", 0)
            entry.short_put_mid_at_fill = sp.get("mid_at_fill", 0)
            entry.put_spread_credit = sp.get("credit", 0)
            filled_legs.append(("short_put", entry.short_put_position_id, entry.short_put_uic))
            self._register_position(entry, "short_put")

            # Seed monitoring prices with the fills (overwritten each heartbeat).
            entry.short_call_price = entry.short_call_fill_price
            entry.short_put_price = entry.short_put_fill_price
            entry.is_complete = True
            logger.info(
                f"STRANGLE entry #{entry.entry_number} complete: "
                f"call ${entry.call_spread_credit:.2f}, put ${entry.put_spread_credit:.2f}"
            )
            return True

        except Exception as e:
            logger.error(f"Strangle entry failed at leg {len(filled_legs) + 1}: {e}")
            # No naked-short emergency: requires_protective_wings=False means an
            # unhedged short is expected here; just unwind whatever filled.
            self._unwind_partial_entry(filled_legs, entry)
            return False

    def _strangle_pre_entry_gates(self, entry_num: int) -> Optional[str]:
        """Run the shared pre-entry gates (reusing the base check helpers) and
        return a skip/delay string if any blocks, else None. Mirrors HYDRA's gate
        sequence + side effects, minus the IC-specific conditional/credit logic.

        (The base `_initiate_entry` keeps these gates inline; deduplicating them
        into a shared helper on MEICStrategy is a later, paper-verified refactor.
        Reusing the *helpers* here keeps the gate LOGIC single-sourced.)
        """
        if self._has_orphaned_orders():
            logger.error(f"Strangle entry #{entry_num} blocked by orphaned orders")
            self._next_entry_index += 1
            return f"Entry #{entry_num} skipped - orphaned orders blocking"

        is_halted, halt_reason = self._check_market_halt()
        if is_halted:
            # A halt is a DELAY (retry later), not a skip — do NOT advance the index.
            return f"Entry #{entry_num} delayed - {halt_reason}"

        has_bp, bp_message = self._check_buying_power()
        if not has_bp:
            self.daily_state.entries_skipped += 1
            self._next_entry_index += 1
            self._record_skipped_entry(entry_num, f"Insufficient margin: {bp_message}", send_alert=False)
            return f"Entry #{entry_num} skipped - {bp_message}"

        whipsaw_reason = self._check_whipsaw_filter()
        if whipsaw_reason:
            self.daily_state.entries_skipped += 1
            self._next_entry_index += 1
            self._record_skipped_entry(entry_num, whipsaw_reason, send_alert=True)
            return f"Entry #{entry_num} skipped - {whipsaw_reason}"

        if (self.fomc_t1_skip_enabled and is_fomc_t_plus_one() and not self._force_normal_day()):
            self.daily_state.entries_skipped += 1
            self._next_entry_index += 1
            self._record_skipped_entry(entry_num, "FOMC T+1 blackout", send_alert=True)
            return f"Entry #{entry_num} skipped - FOMC T+1 blackout"

        return None

    def _initiate_entry(self) -> str:
        """Strangle entry orchestration: shared gates → strikes → place → book.

        Deliberately simpler than HYDRA's IC `_initiate_entry`: a strangle always
        places both naked legs (no one-sided conversion, no MKT-011 spread-credit
        gate, no conditional E6). v1 is single-attempt (no retry loop). Reuses the
        base bookkeeping verbatim (entry tracking, 2-leg commission, stop levels,
        state-save, Sheets/DB logging).
        """
        entry_num = self._next_entry_index + 1
        logger.info(f"STRANGLE: initiating entry #{entry_num}")

        gate = self._strangle_pre_entry_gates(entry_num)
        if gate is not None:
            return gate

        self._entry_in_progress = True
        self.state = MEICState.ENTRY_IN_PROGRESS
        try:
            entry = HydraIronCondorEntry(entry_number=entry_num)
            # Audit S-HIGH-1: stamp contracts (the IC path does this) — credits,
            # commission, stops, spread_value and reconciliation all scale by it.
            entry.contracts = self.contracts_per_entry
            entry.strategy_id = f"strangle_{get_us_market_time().strftime('%Y%m%d')}_{entry_num:03d}"

            if not self._calculate_strikes(entry):
                self.daily_state.entries_skipped += 1
                self._next_entry_index += 1
                self._record_skipped_entry(entry_num, "strangle: strike calculation failed", send_alert=False)
                return f"Entry #{entry_num} skipped - strike calculation failed"

            success = self._simulate_entry(entry) if self.dry_run else self._execute_entry(entry)
            if not success:
                self.daily_state.entries_failed += 1
                self._next_entry_index += 1
                return f"Entry #{entry_num} failed - placement unsuccessful"

            entry.entry_time = get_us_market_time()
            entry.is_complete = True
            self.daily_state.entries.append(entry)
            self.daily_state.entries_completed += 1
            self.daily_state.total_credit_received += entry.total_credit
            # Two naked legs (no long wings) → 2-leg open commission.
            entry.open_commission = 2 * self.commission_per_leg * self.contracts_per_entry
            self.daily_state.total_commission += entry.open_commission

            self._calculate_stop_levels_hydra(entry)
            self._save_state_to_disk()      # persist before logging (crash-window guard)
            self._log_entry(entry)
            entry._spx_at_entry = self.current_price
            self._record_entry_to_db(entry)
            self._next_entry_index += 1

            return (
                f"Entry #{entry_num} placed: STRANGLE "
                f"SC {entry.short_call_strike:.0f} / SP {entry.short_put_strike:.0f}, "
                f"credit ${entry.total_credit:.2f}"
            )
        finally:
            self._entry_in_progress = False
            self.state = MEICState.MONITORING
