"""2026-06-10 GUARD-INVERT: _validate_realized_credit must reject + unwind an
entry whose realized per-side credit filled at a net DEBIT (the canonical
incident: variant C Entry #2 put side sold short @$8.80 after buying long
@$9.60 → −$0.80/ct = −$560), and must NOT touch healthy entries.

Rule 1 (always on): a credit vertical must collect a credit (>= floor 0).
Rule 2 (opt-in via config fraction>0): realized < fraction × MKT-011 estimate.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.strategy import HydraStrategy  # noqa: E402


def _strat(dry_run=False, guard_cfg=None):
    s = HydraStrategy.__new__(HydraStrategy)
    s.dry_run = dry_run
    s.strategy_config = {"realized_credit_guard": guard_cfg} if guard_cfg is not None else {}
    s.alert_service = MagicMock()
    s._unwind_partial_entry = MagicMock()
    s._log_safety_event = MagicMock()
    return s


def _entry(call_credit, put_credit, contracts=7, call_active=True, put_active=True,
           est_call=None, est_put=None):
    return SimpleNamespace(
        entry_number=2, contracts=contracts,
        call_spread_credit=call_credit, put_spread_credit=put_credit,
        short_call_uic=1 if call_active else None,
        short_put_uic=3 if put_active else None,
        call_side_skipped=not call_active, put_side_skipped=not put_active,
        _mkt011_est_call=est_call, _mkt011_est_put=est_put,
    )


# Full-IC filled_legs in PLACEMENT order (longs first).
FULL_LEGS = [("long_call", None, 2), ("long_put", None, 4),
             ("short_call", None, 1), ("short_put", None, 3)]
PUT_LEGS = [("long_put", None, 4), ("short_put", None, 3)]
CALL_LEGS = [("long_call", None, 2), ("short_call", None, 1)]


class TestGuard:
    def test_unit1_put_inversion_rejected_and_unwound(self):
        # The 2026-06-10 incident: call +$130 healthy, put -$560 inverted.
        s = _strat()
        e = _entry(call_credit=130.0, put_credit=-560.0, est_call=45.0, est_put=90.0)
        assert s._validate_realized_credit(e, FULL_LEGS,
                                           est_call_pc=45.0, est_put_pc=90.0) is False
        # unwound with all 4 legs, SHORTS FIRST
        s._unwind_partial_entry.assert_called_once()
        passed_legs = s._unwind_partial_entry.call_args[0][0]
        assert [l[0] for l in passed_legs[:2]] == ["short_call", "short_put"]
        assert {l[0] for l in passed_legs} == {"long_call", "long_put", "short_call", "short_put"}
        # CRITICAL alert with contracts
        assert s.alert_service.send_alert.called
        assert s.alert_service.send_alert.call_args.kwargs.get("contracts") == 7

    def test_unit2_healthy_full_ic_passes(self):
        s = _strat()
        e = _entry(call_credit=190.0, put_credit=1190.0)
        assert s._validate_realized_credit(e, FULL_LEGS) is True
        s._unwind_partial_entry.assert_not_called()
        s.alert_service.send_alert.assert_not_called()

    def test_unit3_rule2_estimate_collapse_optin(self):
        # Rule 2 only when fraction>0. realized put +$0.30/ct vs est $0.90 → <40% → reject.
        s = _strat(guard_cfg={"credit_realized_fraction": 0.40})
        e = _entry(call_credit=190.0, put_credit=210.0, est_call=45.0, est_put=90.0)  # 210/700=$0.30
        assert s._validate_realized_credit(e, FULL_LEGS, est_call_pc=45.0, est_put_pc=90.0) is False
        # Bump put to $0.40/ct (280) → 0.40 >= 0.40*0.90=0.36 → pass.
        s2 = _strat(guard_cfg={"credit_realized_fraction": 0.40})
        e2 = _entry(call_credit=190.0, put_credit=280.0, est_call=45.0, est_put=90.0)
        assert s2._validate_realized_credit(e2, FULL_LEGS, est_call_pc=45.0, est_put_pc=90.0) is True

    def test_unit3b_rule2_off_by_default(self):
        # Same collapsed-but-positive put, but default config (fraction 0.0) → Rule 2 dormant → pass.
        s = _strat()
        e = _entry(call_credit=190.0, put_credit=210.0, est_call=45.0, est_put=90.0)
        assert s._validate_realized_credit(e, FULL_LEGS, est_call_pc=45.0, est_put_pc=90.0) is True

    def test_unit4_put_only_inversion(self):
        s = _strat()
        e = _entry(call_credit=0.0, put_credit=-560.0, call_active=False)
        assert s._validate_realized_credit(e, PUT_LEGS, est_put_pc=90.0) is False
        passed = s._unwind_partial_entry.call_args[0][0]
        assert {l[0] for l in passed} == {"long_put", "short_put"}

    def test_unit5_call_only_healthy(self):
        s = _strat()
        e = _entry(call_credit=200.0, put_credit=0.0, put_active=False)
        assert s._validate_realized_credit(e, CALL_LEGS, est_call_pc=60.0) is True
        s._unwind_partial_entry.assert_not_called()

    def test_unit6_missing_estimate_rule1_still_protects(self):
        # No estimate, slightly positive both sides → pass.
        s = _strat()
        e = _entry(call_credit=70.0, put_credit=70.0)  # +$0.10/ct each
        assert s._validate_realized_credit(e, FULL_LEGS) is True
        # Flip put to -$0.05/ct (-35) → Rule 1 fires even with no estimate.
        s2 = _strat()
        e2 = _entry(call_credit=70.0, put_credit=-35.0)
        assert s2._validate_realized_credit(e2, FULL_LEGS) is False

    def test_unit7_dry_run_noop(self):
        s = _strat(dry_run=True)
        e = _entry(call_credit=-999.0, put_credit=-999.0)
        assert s._validate_realized_credit(e, FULL_LEGS) is True
        s._unwind_partial_entry.assert_not_called()
        s.alert_service.send_alert.assert_not_called()

    def test_unit8_zero_contracts_noop(self):
        s = _strat()
        e = _entry(call_credit=-560.0, put_credit=-560.0, contracts=0)
        assert s._validate_realized_credit(e, FULL_LEGS) is True

    def test_unit10_action_alert_only_books_but_warns(self):
        s = _strat(guard_cfg={"action": "alert_only"})
        e = _entry(call_credit=130.0, put_credit=-560.0)
        assert s._validate_realized_credit(e, FULL_LEGS) is True  # booked
        s.alert_service.send_alert.assert_called_once()           # but flagged
        s._unwind_partial_entry.assert_not_called()               # NOT unwound

    def test_unit10b_action_off_restores_prior_behavior(self):
        s = _strat(guard_cfg={"action": "off"})
        e = _entry(call_credit=130.0, put_credit=-560.0)
        assert s._validate_realized_credit(e, FULL_LEGS) is True
        s.alert_service.send_alert.assert_not_called()
        s._unwind_partial_entry.assert_not_called()

    def test_unit_exact_incident_numbers(self):
        # short put 7250 @8.80, long put 7245 @9.60 → (8.80-9.60)*100*7 = -560
        s = _strat()
        e = _entry(call_credit=130.0, put_credit=(8.80 - 9.60) * 100 * 7)
        assert round(e.put_spread_credit, 2) == -560.0
        assert s._validate_realized_credit(e, FULL_LEGS) is False
