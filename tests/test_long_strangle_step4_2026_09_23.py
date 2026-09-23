"""Strategy H, Playbook Step 4 — entry selection, sizing, and dry-run simulation.

Step 4's exit criterion is "a simulated entry books correctly (strikes,
debit/credit, commission, state save) under dry-run; happy-path + skip-path tests
pass; **no real order is ever placed**."

The tests are weighted towards refusals, for the same reason the Step 3 tests were:
every dangerous failure in this path is silent-but-plausible rather than loud.

Three in particular are worth naming, because they are the ones that would produce
a believable number that is wrong:

1. **A silent fallback between the two expected moves.** They disagree by roughly
   3x (straddle ~22pt vs VIX-implied ~71pt on 2026-09-22), and the expected move
   IS the strike choice. A fallback would place a 71pt strangle while the config,
   the logs and the recorded ``em_source`` all said "straddle" — and the resulting
   series could never be separated back into two strategies.
2. **A zero premium becoming a free position.** An unpriced leg gives a zero
   debit, which makes sizing-for-zero divide into infinity and books a strangle
   that appears to have cost nothing.
3. **An IV filter that passes everything.** The repo has no option-IV history, so
   the filter is off; a version that silently substituted VIX, or that treated
   "unknown" as "passes", would look like a working filter and be none.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bots.hydra.base_strategy import ConfigError  # noqa: E402
from bots.hydra.long_strangle_entry import LongStrangleEntry  # noqa: E402
from bots.hydra.long_strangle_strategy import LongStrangleStrategy  # noqa: E402

SPOT = 7765.0
#: A plausible SPXW 0DTE grid around spot: 5pt near the money, 25pt far out.
CHAIN = ([SPOT - 300 + 25 * i for i in range(12)]
         + [SPOT - 100 + 5 * i for i in range(41)]
         + [SPOT + 125 + 25 * i for i in range(12)])


def _strat(*, em_source="straddle", contracts=1, ls_cfg=None, quotes=None,
           chain=None, uic=lambda k, r, e: int(k * 10 + (1 if r == "Call" else 2))):
    """A LongStrangleStrategy with only what Step 4 touches.

    ``__new__`` bypasses the real ``__init__`` (which needs a broker and a live
    session) — the same shortcut ``test_strangle_strategy.py`` uses. Everything
    Step 4 reads is set explicitly, so a test that passes here is a test about
    Step 4's logic and not about the base class's construction.
    """
    s = LongStrangleStrategy.__new__(LongStrangleStrategy)
    s.current_price = SPOT
    s.current_vix = 14.5
    s.dry_run = True
    s.contracts_per_entry = contracts
    s.max_contracts_per_order = 10
    s.commission_per_leg = 1.15
    s.underlying_symbol, s.trading_class, s.exchange = "SPX", "SPXW", "CBOE"
    s.target_dte = 0
    cfg = {"expected_move_source": em_source, "sizing_for_zero_max_loss": 500.0}
    cfg.update(ls_cfg or {})
    s.strategy_config = {"long_strangle": cfg}
    s.ls_recorder = MagicMock()

    grid = list(CHAIN if chain is None else chain)
    s.broker = MagicMock()
    s.broker.get_option_chain.return_value = grid

    # Step 9 batched the entry path from 13 broker calls to 7, so the fakes sit
    # on the seams it actually uses now: ONE `_read_option_chain` resolves both
    # rights, and ONE `_read_option_quotes_batch` prices both legs. Stubbing the
    # per-leg helpers would test a path the strategy no longer takes.
    def _chain(expiry, candidates):
        call_map, put_map = {}, {}
        for cand in candidates:
            if not grid:
                continue
            nearest = min(grid, key=lambda k: abs(k - cand))
            if abs(nearest - cand) > 25:
                continue
            c, pu = uic(nearest, "Call", expiry), uic(nearest, "Put", expiry)
            if c:
                call_map[float(nearest)] = c
            if pu:
                put_map[float(nearest)] = pu
        return call_map, put_map
    s._read_option_chain = MagicMock(side_effect=_chain)

    # Default book: ~$11 ATM legs (a 22pt straddle) and ~$1.20 OTM wings.
    prices = {} if quotes is None else dict(quotes)

    def _quotes_batch(ids):
        out = {}
        for i in ids:
            px = prices.get(i, prices.get("*", 1.20))
            if px is None:
                continue          # absent from the batch = not quoted
            out[i] = {"bid": px - 0.05, "ask": px + 0.05,
                      "mid": px, "last": px, "mark": px}
        return out
    s._read_option_quotes_batch = MagicMock(side_effect=_quotes_batch)
    return s


def _atm_quotes(call=11.0, put=11.0, otm=1.20, atm=None):
    """Quote map keyed the way the fake ``_get_option_uic`` mints conids."""
    atm = round(SPOT / 5) * 5 if atm is None else atm
    return {int(atm * 10 + 1): call, int(atm * 10 + 2): put, "*": otm}


def _entry(n=1, contracts=1):
    e = LongStrangleEntry(entry_number=n)
    e.contracts = contracts
    return e


# ======================================================================
# The expected move — which IS the strike choice
# ======================================================================

class TestTheExpectedMove:
    def test_straddle_source_reads_the_atm_straddle_off_the_chain(self):
        s = _strat(quotes=_atm_quotes(call=11.0, put=11.0))
        em, src = s._expected_move(SPOT, "2026-09-23", CHAIN)
        assert src == "straddle"
        assert em == pytest.approx(22.0)

    def test_vix_source_matches_variant_F_exactly(self):
        """H and F must never disagree about the arithmetic — only about config."""
        from bots.hydra.long_strangle_chain import expected_move_from_vix
        s = _strat(em_source="vix")
        em, src = s._expected_move(SPOT, "2026-09-23", CHAIN)
        assert src == "vix"
        assert em == pytest.approx(expected_move_from_vix(SPOT, 14.5))

    def test_the_two_sources_disagree_by_roughly_3x(self):
        """Not a tuning knob — it selects which strategy runs. Pinned so nobody
        later treats the two as interchangeable defaults."""
        straddle = _strat(quotes=_atm_quotes())._expected_move(SPOT, "d", CHAIN)[0]
        vix = _strat(em_source="vix")._expected_move(SPOT, "d", CHAIN)[0]
        assert vix > straddle * 2.5

    def test_an_unpriceable_straddle_does_NOT_fall_back_to_vix(self):
        """The single most important refusal in Step 4.

        A fallback would place a ~71pt strangle while the config, the logs and
        the recorded em_source all said "straddle", blending two strategies into
        one series that can never be separated afterwards."""
        s = _strat(quotes={"*": None})          # nothing quotes
        em, src = s._expected_move(SPOT, "2026-09-23", CHAIN)
        assert em == 0.0
        assert src == "straddle"                # it did not silently become "vix"

    def test_an_unknown_source_refuses_rather_than_guessing(self):
        s = _strat(em_source="atr")
        em, src = s._expected_move(SPOT, "2026-09-23", CHAIN)
        assert em == 0.0 and src == "atr"

    def test_no_atm_strike_within_tolerance_refuses(self):
        """A sparse or partially-loaded chain must not resolve the ATM straddle
        to something 100pt away and call the result an expected move."""
        s = _strat(chain=[SPOT - 500, SPOT + 500], quotes=_atm_quotes())
        assert s._expected_move(SPOT, "2026-09-23", [SPOT - 500, SPOT + 500])[0] == 0.0


# ======================================================================
# Strike selection
# ======================================================================

class TestStrikeSelection:
    def _ok(self, **kw):
        s = _strat(quotes=_atm_quotes(), **kw)
        s._get_todays_expiry = MagicMock(return_value="2026-09-23")
        return s

    def test_happy_path_sets_both_LONG_strikes_at_spot_plus_minus_the_move(self):
        s = self._ok()
        e = _entry()
        assert s._calculate_strikes(e) is True
        assert e.long_call_strike == pytest.approx(7785.0)   # 7765 + 22 → snapped
        assert e.long_put_strike == pytest.approx(7745.0)    # 7765 − 22 → snapped
        assert e.ls_em_source == "straddle"
        assert e.ls_expected_move == pytest.approx(22.0)

    def test_the_short_legs_stay_at_zero(self):
        """H is the mirror of G: it populates long_* and leaves short_* at 0.0.
        A non-zero short strike here would mean the strategy had sold something."""
        s = self._ok()
        e = _entry()
        s._calculate_strikes(e)
        assert e.short_call_strike == 0.0 and e.short_put_strike == 0.0
        assert e.total_credit == 0.0

    def test_both_conids_are_resolved_and_stored(self):
        s = self._ok()
        e = _entry()
        s._calculate_strikes(e)
        assert e.long_call_uic and e.long_put_uic

    def test_a_missing_conid_refuses(self):
        # Only the OTM put fails to resolve — the ATM pair still prices, so this
        # reaches the conid check rather than tripping the expected move first.
        s = self._ok(uic=lambda k, r, e: (None if (r == "Put" and k < SPOT - 10)
                                          else int(k * 10 + (1 if r == "Call" else 2))))
        e = _entry()
        assert s._calculate_strikes(e) is False
        assert "conid" in e.ls_skip_reason

    def test_an_unpriceable_leg_refuses_rather_than_booking_a_free_position(self):
        """A zero premium becomes a zero debit, an unbounded sizing-for-zero
        count, and a strangle that appears to have cost nothing."""
        atm = _atm_quotes()
        atm["*"] = None                          # the OTM wings do not quote
        s = _strat(quotes=atm)
        s._get_todays_expiry = MagicMock(return_value="2026-09-23")
        e = _entry()
        assert s._calculate_strikes(e) is False
        assert "priceable" in e.ls_skip_reason

    def test_an_empty_chain_refuses(self):
        s = self._ok(chain=[])
        e = _entry()
        assert s._calculate_strikes(e) is False
        assert "chain" in e.ls_skip_reason

    def test_a_chain_that_supplies_only_one_side_refuses(self):
        """One leg is a directional bet, which is not this strategy.

        The put target (7743) has a listed strike 2pt away; the call target
        (7787) has nothing nearer than 7760, 27pt off. A strategy that placed the
        put and dropped the call would be long a put, not long a strangle."""
        gapped = [7700.0 + 5 * i for i in range(13)] + [7900.0]   # 7700-7760, 7900
        s = _strat(chain=gapped, quotes=_atm_quotes(atm=7760.0))
        s._get_todays_expiry = MagicMock(return_value="2026-09-23")
        e = _entry()
        assert s._calculate_strikes(e) is False
        assert "both strikes" in e.ls_skip_reason

    def test_no_spx_price_refuses(self):
        s = self._ok()
        s.current_price = 0.0
        e = _entry()
        assert s._calculate_strikes(e) is False


class TestTheSkewCheck:
    def _run(self, call_px, put_px, tol=35.0):
        atm = _atm_quotes()
        call_k = round((SPOT + 22) / 5) * 5
        put_k = round((SPOT - 22) / 5) * 5
        atm[int(call_k * 10 + 1)] = call_px
        atm[int(put_k * 10 + 2)] = put_px
        s = _strat(quotes=atm, ls_cfg={"skew_tolerance_pct": tol})
        s._get_todays_expiry = MagicMock(return_value="2026-09-23")
        e = _entry()
        return s._calculate_strikes(e), e

    def test_ordinary_index_put_skew_passes(self):
        """Equidistant strikes are not equally priced; a put a fifth dearer is
        normal and must not be vetoed."""
        ok, e = self._run(1.00, 1.20)
        assert ok is True
        assert e.ls_skew_gap_pct == pytest.approx(16.67, abs=0.01)

    def test_a_lopsided_pair_is_vetoed_and_says_why(self):
        """A large imbalance means the 'strangle' is a directional position
        wearing two legs, which defeats the point of being non-directional."""
        ok, e = self._run(0.40, 2.00)
        assert ok is False
        assert "skew" in e.ls_skip_reason and "80.0%" in e.ls_skip_reason

    def test_the_tolerance_is_config_driven(self):
        """35% is explicitly a guess — the source says only 'reasonably similar'."""
        assert self._run(1.00, 1.50, tol=35.0)[0] is True
        assert self._run(1.00, 1.50, tol=20.0)[0] is False


# ======================================================================
# Sizing for zero
# ======================================================================

class TestSizingForZero:
    def _e(self, call_px, put_px, contracts_cap=10):
        s = _strat(contracts=contracts_cap)
        e = _entry()
        e.long_call_price, e.long_put_price = call_px, put_px
        return s, e

    def test_the_loss_limit_divided_by_the_cost_per_contract(self):
        s, e = self._e(1.00, 1.45)              # $245/contract, $500 limit
        assert s._size_for_zero(e) == 2

    def test_a_position_that_costs_more_than_the_limit_places_nothing(self):
        """Floors at 0, never 1. Rounding up would breach the very limit the
        rule exists to enforce."""
        s, e = self._e(3.00, 3.50)              # $650/contract > $500
        assert s._size_for_zero(e) == 0

    def test_the_fleet_contract_cap_can_only_reduce_the_count(self):
        s, e = self._e(0.20, 0.20, contracts_cap=1)   # affordable = 12
        assert s._size_for_zero(e) == 1

    def test_the_fleet_cap_cannot_raise_the_count_past_the_loss_limit(self):
        s, e = self._e(3.00, 3.50, contracts_cap=10)
        assert s._size_for_zero(e) == 0

    def test_an_unpriced_entry_sizes_to_zero_rather_than_dividing_by_it(self):
        s, e = self._e(0.0, 0.0)
        assert s._size_for_zero(e) == 0

    def test_an_order_cap_of_zero_is_honoured_not_treated_as_absent(self):
        """A present cap of 0 means "place nothing". Coercing it to "no cap"
        would be a safety limit that disappears when set to its strictest
        value — the wrong direction for every such knob."""
        s, e = self._e(1.00, 1.45)
        s.max_contracts_per_order = 0
        assert s._size_for_zero(e) == 0

    def test_a_missing_order_cap_simply_does_not_apply(self):
        s, e = self._e(1.00, 1.45)
        del s.max_contracts_per_order
        assert s._size_for_zero(e) == 2


# ======================================================================
# The IV-percentile filter — built, wired, and deliberately OFF
# ======================================================================

class TestTheIVPercentileGate:
    def test_it_is_off_by_default_and_passes_everything(self):
        """Off because the repo has no option-IV history to feed it — see the
        spec's assumption 2. Not off by oversight."""
        assert _strat()._iv_percentile_gate(_entry()) is None

    def test_enabling_it_without_a_series_FAILS_CLOSED(self):
        """A premature flip must be immediately visible, not silently inert.
        Passing everything is what a broken filter looks like from outside."""
        s = _strat(ls_cfg={"iv_percentile_filter_enabled": True})
        reason = s._iv_percentile_gate(_entry())
        assert reason and "no iv_percentile_source" in reason

    def test_an_unimplemented_source_is_refused_not_ignored(self):
        s = _strat(ls_cfg={"iv_percentile_filter_enabled": True,
                           "iv_percentile_source": "polygon_iv"})
        assert "not implemented" in s._iv_percentile_gate(_entry())

    def test_an_empty_history_is_unknown_and_therefore_a_SKIP(self):
        """`iv_percentile` returns None for "unknown". Treating that as "passes
        the <35% filter" is exactly the silent failure the chain module warns
        about."""
        s = _strat(ls_cfg={"iv_percentile_filter_enabled": True,
                           "iv_percentile_source": "vix"})
        assert "unavailable" in s._iv_percentile_gate(_entry())

    def test_the_vix_proxy_is_labelled_as_a_proxy_when_it_vetoes(self):
        """VIX is a 30-day INDEX vol, not the IV of the 0DTE options being
        bought. If it is ever used, the recorded reason must say so — otherwise a
        later analysis reads a VIX percentile as an option-IV percentile."""
        s = _strat(ls_cfg={"iv_percentile_filter_enabled": True,
                           "iv_percentile_source": "vix", "iv_percentile_max": 35})
        s._vix_history_for_percentile = lambda: [10.0, 11.0, 12.0, 13.0, 20.0]
        e = _entry()
        reason = s._iv_percentile_gate(e)       # current_vix 14.5 → 80th pct
        assert "NOT an option-IV percentile" in reason
        assert e.ls_iv_percentile == pytest.approx(80.0)

    def test_a_low_proxy_passes(self):
        s = _strat(ls_cfg={"iv_percentile_filter_enabled": True,
                           "iv_percentile_source": "vix", "iv_percentile_max": 35})
        s.current_vix = 10.0
        s._vix_history_for_percentile = lambda: [12.0, 13.0, 14.0, 20.0, 25.0]
        assert s._iv_percentile_gate(_entry()) is None


# ======================================================================
# The dry-run simulation
# ======================================================================

class TestSimulateEntry:
    def _ready(self, contracts=2, call_px=1.00, put_px=1.45):
        s = _strat(contracts=contracts)
        e = _entry(contracts=contracts)
        e.long_call_strike, e.long_put_strike = 7785.0, 7745.0
        e.long_call_uic, e.long_put_uic = 111, 222
        e.long_call_price, e.long_put_price = call_px, put_px
        return s, e

    def test_it_books_the_debit_per_leg_times_contracts(self):
        s, e = self._ready()
        assert s._simulate_entry(e) is True
        assert e.call_debit == pytest.approx(200.0)    # 1.00 × 100 × 2
        assert e.put_debit == pytest.approx(290.0)     # 1.45 × 100 × 2
        assert e.total_debit == pytest.approx(490.0)

    def test_the_debit_IS_the_max_loss(self):
        s, e = self._ready()
        s._simulate_entry(e)
        assert e.max_loss == e.total_debit

    def test_nothing_was_collected(self):
        """total_credit stays a truthful 0.0 — not −debit, which would let the
        inherited `credit − value` formula produce a plausible wrong answer."""
        s, e = self._ready()
        s._simulate_entry(e)
        assert e.total_credit == 0.0

    def test_synthetic_DRY_ids_mark_both_LONG_legs(self):
        s, e = self._ready()
        s._simulate_entry(e)
        assert e.long_call_position_id.startswith("DRY_")
        assert e.long_put_position_id.endswith("_LP")
        assert e.is_complete is True

    def test_a_missing_conid_refuses(self):
        s, e = self._ready()
        e.long_put_uic = 0
        assert s._simulate_entry(e) is False

    def test_an_unpriced_leg_refuses(self):
        s, e = self._ready(call_px=0.0)
        assert s._simulate_entry(e) is False


class TestNoRealOrderCanEverReachTheBroker:
    """Step 4's hard exit criterion. Two independent locks, because "the other
    guard will catch it" is how a strategy ends up selling naked options."""

    def test_simulate_entry_touches_no_placement_method(self):
        s, e = TestSimulateEntry()._ready()
        s.broker = MagicMock()
        s._place_option_order = MagicMock()
        s._simulate_entry(e)
        s._place_option_order.assert_not_called()
        for m in ("place_order", "place_and_wait_for_fill", "place_iron_condor"):
            getattr(s.broker, m).assert_not_called()

    def test_execute_entry_refuses_instead_of_inheriting_IC_placement(self):
        """The inherited four-leg path would SELL two short legs H does not have
        and has never sized for."""
        s = _strat()
        with pytest.raises(ConfigError, match="SELL two short legs"):
            s._execute_entry(_entry())


# ======================================================================
# Capital, stops, and recording
# ======================================================================

class TestTheCapitalFloor:
    def test_it_is_the_loss_limit_not_a_derived_IC_width(self):
        """The base derives ``max(call_width, put_width) × $100`` — $6,000-7,500
        per contract for a position that costs a few hundred. That would not fail
        loudly; it would skip every entry and look like "no signal"."""
        assert _strat()._min_buying_power_per_unit() == pytest.approx(500.0)

    def test_an_operator_can_raise_it(self):
        s = _strat(ls_cfg={"min_buying_power_per_long_strangle": 2500.0})
        assert s._min_buying_power_per_unit() == pytest.approx(2500.0)

    def test_it_tracks_the_loss_limit_when_not_set_explicitly(self):
        s = _strat(ls_cfg={"sizing_for_zero_max_loss": 1200.0})
        assert s._min_buying_power_per_unit() == pytest.approx(1200.0)


class TestStopsAreDisarmedNotInherited:
    def test_both_side_stops_are_unreachable(self):
        """The base's ``credit + buffer`` with a truthful 0.0 credit collapses to
        the MIN_STOP_LEVEL floor plus a buffer — a small arbitrary figure with no
        relationship to anything, which the monitoring loop would treat as a real
        trigger. "No stop" and "a stop nobody chose" are different things."""
        s, e = TestSimulateEntry()._ready()
        s._simulate_entry(e)
        s._calculate_stop_levels_hydra(e)
        assert e.call_side_stop == float("inf")
        assert e.put_side_stop == float("inf")

    def test_the_worst_loss_is_still_bounded_by_the_debit(self):
        s, e = TestSimulateEntry()._ready()
        s._simulate_entry(e)
        e.long_call_price = e.long_put_price = 0.0     # both expire worthless
        assert e.unrealized_pnl == pytest.approx(-e.total_debit)


class TestRecording:
    def test_entries_go_to_the_isolated_db_not_trade_entries(self):
        """`trade_entries` has no column that can hold a debit, so a row there
        would record a strangle that cost nothing."""
        s, e = TestSimulateEntry()._ready()
        s._simulate_entry(e)
        e.ls_em_source, e.ls_expected_move, e.ls_skew_gap_pct = "straddle", 22.0, 16.7
        s._record_entry_to_db(e)
        s.ls_recorder.record_entry.assert_called_once()
        kwargs = s.ls_recorder.record_entry.call_args.kwargs
        assert kwargs["em_source"] == "straddle"
        assert kwargs["expected_move"] == 22.0

    def test_a_missing_recorder_is_survivable(self):
        s, e = TestSimulateEntry()._ready()
        s.ls_recorder = None
        s._record_entry_to_db(e)               # must not raise

    def test_a_skip_records_the_full_counterfactual(self):
        """The GEX work on variant B had to be retro-fitted for exactly this and
        could never recover its first 95 vetoes."""
        s = _strat()
        s.daily_state = SimpleNamespace(entries_skipped=0)
        s._next_entry_index = 0
        s._record_skipped_entry = MagicMock()
        e = _entry()
        e.long_call_strike, e.long_put_strike = 7785.0, 7745.0
        e.long_call_price, e.long_put_price = 1.00, 1.45
        e.ls_em_source, e.ls_expected_move, e.ls_skew_gap_pct = "straddle", 22.0, 31.0
        s._skip(e, 1, "skew 31.0% > 20.0% tolerance")

        kwargs = s.ls_recorder.record_skip.call_args.kwargs
        assert kwargs["proposed_call_strike"] == 7785.0
        assert kwargs["proposed_debit"] == pytest.approx(245.0)
        assert kwargs["em_source"] == "straddle"
        assert kwargs["expected_move"] == 22.0
        assert s.daily_state.entries_skipped == 1


# ======================================================================
# Orchestration
# ======================================================================

def _orchestrated(**kw):
    kw.setdefault("quotes", _atm_quotes())
    s = _strat(**kw)
    s._get_todays_expiry = MagicMock(return_value="2026-09-23")
    s.daily_state = SimpleNamespace(entries=[], entries_skipped=0, entries_failed=0,
                                    entries_completed=0, total_commission=0.0,
                                    total_credit_received=0.0)
    s._next_entry_index = 0
    s._entry_in_progress = False
    s.state = None
    s.fomc_t1_skip_enabled = False
    s._has_orphaned_orders = MagicMock(return_value=False)
    s._check_market_halt = MagicMock(return_value=(False, ""))
    s._check_buying_power = MagicMock(return_value=(True, "ok"))
    s._check_whipsaw_filter = MagicMock(return_value=None)
    s._force_normal_day = MagicMock(return_value=False)
    s._record_skipped_entry = MagicMock()
    s._record_failed_entry = MagicMock()
    s._save_state_to_disk = MagicMock()
    s._log_entry = MagicMock()
    return s


class TestInitiateEntry:
    def test_the_happy_path_books_one_complete_entry(self):
        s = _orchestrated()
        msg = s._initiate_entry()
        assert "LONG STRANGLE" in msg and "debit $" in msg
        assert len(s.daily_state.entries) == 1
        e = s.daily_state.entries[0]
        assert e.is_complete and e.total_debit > 0
        assert e.structure == "long_strangle"
        assert e.entry_time is not None
        s._save_state_to_disk.assert_called_once()

    def test_commission_is_charged_for_TWO_legs(self):
        """Two legs, not four — there are no wings to pay for."""
        s = _orchestrated()
        s._initiate_entry()
        e = s.daily_state.entries[0]
        assert e.open_commission == pytest.approx(2 * 1.15 * e.contracts)
        assert s.daily_state.total_commission == pytest.approx(e.open_commission)

    def test_the_daily_credit_accumulator_stays_at_zero(self):
        """Nothing was collected. The money paid lives in ls_entries.total_debit,
        not in a credit counter that would inflate the fleet's totals."""
        s = _orchestrated()
        s._initiate_entry()
        assert s.daily_state.total_credit_received == 0.0

    def test_a_blocking_gate_skips_without_opening_anything(self):
        s = _orchestrated()
        s._check_whipsaw_filter = MagicMock(return_value="whipsaw: range 2.1x EM")
        msg = s._initiate_entry()
        assert "skipped" in msg and not s.daily_state.entries
        assert s.daily_state.entries_skipped == 1

    def test_a_market_halt_DELAYS_and_does_not_burn_the_slot(self):
        s = _orchestrated()
        s._check_market_halt = MagicMock(return_value=(True, "market halted"))
        msg = s._initiate_entry()
        assert "delayed" in msg
        assert s._next_entry_index == 0        # the slot is retried, not consumed

    def test_a_skew_veto_skips_and_advances_the_slot(self):
        atm = _atm_quotes()
        atm[int((round((SPOT + 22) / 5) * 5) * 10 + 1)] = 0.30
        atm[int((round((SPOT - 22) / 5) * 5) * 10 + 2)] = 2.00
        s = _orchestrated(quotes=atm)
        msg = s._initiate_entry()
        assert "skew" in msg and not s.daily_state.entries
        assert s._next_entry_index == 1

    def test_an_unaffordable_debit_skips_rather_than_buying_one_anyway(self):
        s = _orchestrated(quotes=_atm_quotes(otm=4.00),      # $800/contract
                          ls_cfg={"sizing_for_zero_max_loss": 500.0})
        msg = s._initiate_entry()
        assert "sizing-for-zero" in msg and not s.daily_state.entries

    def test_contracts_are_set_from_sizing_for_zero_not_the_config_default(self):
        s = _orchestrated(contracts=5, quotes=_atm_quotes(otm=1.20))
        s._initiate_entry()                                  # $240/contract
        assert s.daily_state.entries[0].contracts == 2       # $500 // $240

    def test_the_entry_state_flag_is_released_even_on_a_skip(self):
        s = _orchestrated()
        s._check_whipsaw_filter = MagicMock(return_value="whipsaw")
        s._initiate_entry()
        assert s._entry_in_progress is False


# ======================================================================
# Step 9 — hardening
# ======================================================================

class TestItIsBoundedAgainstTheSharedBroker:
    """Step 9 item 3: "performance pass = correctness, because the broker
    session is shared."

    Every strategy proxies through the ONE `calypso-broker` session, so a
    read-heavy entry burst on a DRY-RUN variant adds latency to **live B**. The
    playbook records that variant D's first entry took ~4 minutes and had to be
    bounded before it could safely run beside a live variant — "bound it BEFORE
    soak, not after".

    The entry path originally cost **13 broker round-trips**: one chain fetch,
    four `_get_option_uic` calls (each internally a chain fetch PLUS a qualify),
    and four single-leg quotes. Resolving both rights from the one chain read
    and batching the quotes brings it to seven.
    """

    def _counted(self, **kw):
        s = _strat(quotes=_atm_quotes(), **kw)
        s._get_todays_expiry = MagicMock(return_value="2026-09-23")
        s.broker.get_option_chain.reset_mock()
        return s

    def test_a_full_entry_costs_seven_round_trips_not_thirteen(self):
        s = self._counted()
        assert s._calculate_strikes(_entry()) is True
        # 1 strike-grid fetch + 2 chain reads (ATM pair, OTM pair) + 2 batch
        # quotes. Each _read_option_chain is itself a chain fetch + a qualify.
        assert s.broker.get_option_chain.call_count == 1
        assert s._read_option_chain.call_count == 2
        assert s._read_option_quotes_batch.call_count == 2
        total = 1 + s._read_option_chain.call_count * 2 + \
            s._read_option_quotes_batch.call_count
        assert total == 7

    def test_one_chain_read_resolves_BOTH_rights(self):
        """`_get_option_uic` resolves a single right and costs a full chain read
        each time. `_read_option_chain` already returns both maps."""
        s = self._counted()
        call_uic, put_uic = s._resolve_pair("2026-09-23", 7785.0, 7745.0)
        assert call_uic and put_uic
        assert s._read_option_chain.call_count == 1

    def test_one_batch_quote_prices_BOTH_legs(self):
        s = self._counted()
        c, p = s._price_pair(77851, 77452)
        assert c > 0 and p > 0
        assert s._read_option_quotes_batch.call_count == 1

    def test_the_vix_source_is_cheaper_still(self):
        """It needs no chain to compute the expected move, so it skips the ATM
        round-trip entirely."""
        s = self._counted(em_source="vix")
        assert s._calculate_strikes(_entry()) is True
        assert s._read_option_chain.call_count == 1      # the OTM pair only
        assert s._read_option_quotes_batch.call_count == 1

    def test_an_unquoted_leg_is_absent_from_the_batch_not_zero(self):
        """A batch omits instruments it has nothing for. Reading a missing key
        as 0.0 is correct here ONLY because the caller then refuses; the test
        pins that it does."""
        atm = _atm_quotes()
        atm["*"] = None
        s = _strat(quotes=atm)
        s._get_todays_expiry = MagicMock(return_value="2026-09-23")
        e = _entry()
        assert s._calculate_strikes(e) is False
        assert "priceable" in e.ls_skip_reason


class TestTheStrikesCannotCollapseIntoAStraddle:
    """Step 9 finding: `select_strangle_strikes` refuses a non-positive expected
    move because "spot ± 0" is an ATM straddle — materially different and far
    more expensive. It does NOT catch the same thing happening through the SNAP.

    With 5pt strikes near the money, any expected move under ~2.5pt rounds BOTH
    legs onto the same strike and a long strangle silently becomes a long
    straddle. That is reachable: the ATM straddle collapses late in a quiet
    session, which is exactly when this strategy is least likely to be watched.
    """

    def _tiny_em(self, straddle_px):
        atm = round(SPOT / 5) * 5
        s = _strat(quotes={int(atm * 10 + 1): straddle_px,
                           int(atm * 10 + 2): straddle_px, "*": 1.20})
        s._get_todays_expiry = MagicMock(return_value="2026-09-23")
        return s

    def test_a_sub_grid_expected_move_is_refused(self):
        s = self._tiny_em(1.00)          # EM 2.0pt → both snap to 7765
        e = _entry()
        assert s._calculate_strikes(e) is False
        assert "straddle, not a strangle" in e.ls_skip_reason

    def test_the_reason_names_the_collapsed_strike_and_the_move(self):
        s = self._tiny_em(1.00)
        e = _entry()
        s._calculate_strikes(e)
        assert "7765" in e.ls_skip_reason and "2.0pt" in e.ls_skip_reason

    def test_it_refuses_rather_than_widening_to_the_next_strike(self):
        """Widening would invent a position the expected move did not ask for.
        Skipping records the reason and leaves the decision visible."""
        s = self._tiny_em(1.00)
        e = _entry()
        s._calculate_strikes(e)
        assert e.long_call_strike == 0.0 and e.long_put_strike == 0.0

    def test_an_expected_move_just_over_the_grid_still_passes(self):
        s = self._tiny_em(1.60)          # EM 3.2pt → 7770 / 7760, distinct
        e = _entry()
        assert s._calculate_strikes(e) is True
        assert e.long_call_strike > e.long_put_strike

    def test_the_skip_is_recorded_with_its_counterfactual(self):
        s = self._tiny_em(1.00)
        s.daily_state = SimpleNamespace(entries_skipped=0)
        s._next_entry_index = 0
        s._record_skipped_entry = MagicMock()
        e = _entry()
        s._calculate_strikes(e)
        s._skip(e, 1, e.ls_skip_reason)
        call = s.ls_recorder.record_skip.call_args
        assert call.kwargs["expected_move"] == pytest.approx(2.0)
        # skip_reason is positional (date, entry_number, skip_time, reason).
        assert "straddle" in call.args[3]


# ======================================================================
# The IV-percentile filter — the source's rule, now actually running
# ======================================================================

class TestTheVixHistoryIsRealNow:
    """`_vix_history_for_percentile` returned `[]` until 2026-09-23 on the
    reasoning that this repo stores no option-IV history. **That was too
    absolutist.** The source is a retail trader reading "IV percentile" off a
    broker platform, and for SPX that number comes from index-option implied vol
    — which is what VIX is. `market_ticks` has carried a VIX level on every
    heartbeat since 2026-05-05, so the filter the source SPECIFIES is
    computable, and running without it was the larger infidelity.

    What it is NOT: VIX is 30-day implied vol, not the IV of the specific 0DTE
    contracts. Every skip it produces says so.
    """

    def _db(self, tmp_path, rows):
        import sqlite3
        d = tmp_path / "variant_h"
        d.mkdir(exist_ok=True)
        con = sqlite3.connect(str(d / "backtesting.db"))
        con.execute("CREATE TABLE market_ticks (timestamp TEXT, vix_level REAL)")
        con.executemany("INSERT INTO market_ticks VALUES (?,?)", rows)
        con.commit()
        con.close()
        return d

    def _strat_with(self, tmp_path, monkeypatch, rows, **cfg):
        d = self._db(tmp_path, rows)
        import bots.hydra.long_strangle_strategy as mod
        monkeypatch.setattr(mod, "DATA_DIR", str(d))
        return _strat(ls_cfg=cfg)

    def test_it_reads_one_close_per_day_oldest_first(self, tmp_path, monkeypatch):
        s = self._strat_with(tmp_path, monkeypatch, [
            ("2026-09-20 15:59:00", 15.0), ("2026-09-20 10:00:00", 99.0),
            ("2026-09-21 15:59:00", 16.0), ("2026-09-21 10:00:00", 98.0),
        ])
        # The LAST tick of each day is that day's close; earlier ones are noise.
        assert s._vix_history_for_percentile() == [15.0, 16.0]

    def test_today_is_excluded(self, tmp_path, monkeypatch):
        """Ranking the current reading against itself drags the percentile
        toward the middle on every tick."""
        from shared.market_hours import get_us_market_time
        today = get_us_market_time().strftime("%Y-%m-%d")
        s = self._strat_with(tmp_path, monkeypatch, [
            ("2026-09-20 15:59:00", 15.0), (f"{today} 10:00:00", 44.0),
        ])
        assert s._vix_history_for_percentile() == [15.0]

    def test_zero_and_missing_readings_are_dropped(self, tmp_path, monkeypatch):
        s = self._strat_with(tmp_path, monkeypatch, [
            ("2026-09-20 15:59:00", 15.0), ("2026-09-19 15:59:00", 0.0),
        ])
        assert s._vix_history_for_percentile() == [15.0]

    def test_the_lookback_is_bounded_and_configurable(self, tmp_path, monkeypatch):
        rows = [(f"2026-0{m}-{d:02d} 15:59:00", 10.0 + d)
                for m in (6, 7) for d in range(1, 21)]
        s = self._strat_with(tmp_path, monkeypatch, rows,
                             iv_percentile_lookback_days=5)
        assert len(s._vix_history_for_percentile()) == 5

    def test_a_missing_database_is_empty_not_an_exception(self, tmp_path, monkeypatch):
        import bots.hydra.long_strangle_strategy as mod
        monkeypatch.setattr(mod, "DATA_DIR", str(tmp_path / "nope"))
        assert _strat()._vix_history_for_percentile() == []

    def test_a_corrupt_database_degrades_to_empty(self, tmp_path, monkeypatch):
        d = tmp_path / "variant_h"
        d.mkdir()
        (d / "backtesting.db").write_bytes(b"not a database")
        import bots.hydra.long_strangle_strategy as mod
        monkeypatch.setattr(mod, "DATA_DIR", str(d))
        assert _strat()._vix_history_for_percentile() == []

    def test_the_gate_now_PASSES_on_genuinely_cheap_vol(self, tmp_path, monkeypatch):
        """The whole point: the source's rule runs instead of being skipped."""
        s = self._strat_with(tmp_path, monkeypatch,
                             [(f"2026-06-{d:02d} 15:59:00", 20.0 + d) for d in range(1, 21)],
                             iv_percentile_filter_enabled=True,
                             iv_percentile_source="vix", iv_percentile_max=35.0)
        s.current_vix = 14.0            # far below every stored close
        assert s._iv_percentile_gate(_entry()) is None

    def test_the_gate_VETOES_expensive_vol_and_names_the_proxy(self, tmp_path, monkeypatch):
        s = self._strat_with(tmp_path, monkeypatch,
                             [(f"2026-06-{d:02d} 15:59:00", 10.0 + d * 0.1) for d in range(1, 21)],
                             iv_percentile_filter_enabled=True,
                             iv_percentile_source="vix", iv_percentile_max=35.0)
        s.current_vix = 30.0            # above every stored close
        reason = s._iv_percentile_gate(_entry())
        assert reason is not None
        # It must never be mistaken for a per-option IV percentile.
        assert "NOT an option-IV percentile" in reason


class TestTheSourcesCostCap:
    """He will not pay more than ~$1.15/share for a SPY strangle. A QUALITY
    filter, distinct from sizing-for-zero: that decides HOW MANY to buy, this
    refuses to OVERPAY in the first place.

    The PORT is ours, not his — he trades SPY/QQQ and we chose SPX. $1.15 on a
    ~$775 SPY is 0.1483% of spot, so the same fraction on SPX is ~$11.50/share.
    Expressed as a percentage so it tracks the index rather than going stale.
    """

    def _s(self, otm_px, cap=0.1483):
        s = _strat(quotes=_atm_quotes(otm=otm_px),
                   ls_cfg={"max_debit_pct_of_spot": cap})
        s._get_todays_expiry = MagicMock(return_value="2026-09-23")
        return s

    def test_an_ordinary_debit_passes(self):
        # 2 x $1.20 = $2.40/share on spot 7765 -> 0.031%, far under the cap.
        assert self._s(1.20)._calculate_strikes(_entry()) is True

    def test_an_over_priced_strangle_is_refused(self):
        # 2 x $8.00 = $16.00/share -> 0.206% of spot, over the 0.1483% cap.
        e = _entry()
        assert self._s(8.00)._calculate_strikes(e) is False
        assert "cost cap" in e.ls_skip_reason

    def test_the_cap_tracks_the_index_rather_than_a_fixed_dollar(self):
        """0.1483% of 7765 is ~$11.52/share. A fixed dollar cap would drift as
        the index moves; a percentage does not."""
        s = self._s(5.70)          # 2 x 5.70 = 11.40/share, just UNDER
        assert s._calculate_strikes(_entry()) is True
        s2 = self._s(5.80)         # 2 x 5.80 = 11.60/share, just OVER
        assert s2._calculate_strikes(_entry()) is False

    def test_zero_disables_it(self):
        """An operator must be able to turn HIS rule off without deleting it."""
        assert self._s(8.00, cap=0.0)._calculate_strikes(_entry()) is True

    def test_it_is_independent_of_sizing_for_zero(self):
        """The two rules answer different questions. At 1 contract with a $500
        loss limit sizing binds first, so this must be its OWN gate or raising
        that limit later would silently drop the source's rule."""
        s = self._s(8.00)
        e = _entry()
        s._calculate_strikes(e)
        # Refused on COST, before sizing was ever consulted.
        assert "cost cap" in e.ls_skip_reason
        assert "sizing" not in e.ls_skip_reason

    def test_the_skip_records_the_counterfactual(self):
        s = self._s(8.00)
        s.daily_state = SimpleNamespace(entries_skipped=0)
        s._next_entry_index = 0
        s._record_skipped_entry = MagicMock()
        e = _entry()
        s._calculate_strikes(e)
        s._skip(e, 1, e.ls_skip_reason)
        assert "cost cap" in s.ls_recorder.record_skip.call_args.args[3]
