"""The deferred-fill settle window must stay above the OBSERVED lag maximum.

45s was derived as "2.25x the ~20s measured lag, so the threshold is not sitting
on its own sample maximum" (2026-09-27). On 2026-10-01 the lag was still present
at 48s — B stopped E#5 and E#6 on one shared conid and the re-check read -7 when
the truth was 0 — which put 45s BELOW its own sample maximum.

The last test here is the one that matters: it encodes the derivation rule as an
assertion, so lowering the window back under the evidence fails with the reason
attached rather than silently reopening the race.
"""
import pytest

from bots.hydra.base_strategy import MEICStrategy


# The largest broker position-read lag actually OBSERVED, in seconds.
# 2026-09-25: ~20s. 2026-10-01: still stale at 48s (the shared-conid orphan).
OBSERVED_LAG_MAX_S = 48.0


class _Concrete(MEICStrategy):
    """Minimal concrete subclass — MEICStrategy is abstract.

    Two earlier attempts failed here and both were the same mistake in
    different clothes: a hand-built double lacked the class attribute
    `_DEFERRED_FILL_SETTLE_DEFAULT_S` that the method reads, and
    `MEICStrategy.__new__` refuses outright because the class is an ABC
    (`_calculate_strikes` / `_check_stop_losses` / `_initiate_entry`).
    Subclassing inherits the REAL default from the real class, so the
    assertion below is not checking the fixture against itself.
    """
    def _calculate_strikes(self, *a, **k):  # pragma: no cover - never called
        raise NotImplementedError

    def _check_stop_losses(self, *a, **k):  # pragma: no cover - never called
        raise NotImplementedError

    def _initiate_entry(self, *a, **k):  # pragma: no cover - never called
        raise NotImplementedError


def _stub(cfg=None):
    """A real instance with __init__ bypassed (no broker setup)."""
    s = _Concrete.__new__(_Concrete)
    if cfg is not None:
        s.strategy_config = cfg
    return s


def test_default_is_120():
    assert MEICStrategy._deferred_fill_settle_s(_stub({})) == 120.0


def test_config_can_override():
    assert MEICStrategy._deferred_fill_settle_s(_stub({"fill_verify_settle_s": 200})) == 200.0


@pytest.mark.parametrize("junk", ["abc", None, {}, [], object()])
def test_junk_config_falls_back_to_the_default_not_a_stale_literal(junk):
    """A bad value must land on the CURRENT default, not a forgotten 45."""
    got = MEICStrategy._deferred_fill_settle_s(_stub({"fill_verify_settle_s": junk}))
    assert got == MEICStrategy._DEFERRED_FILL_SETTLE_DEFAULT_S == 120.0


def test_missing_strategy_config_entirely():
    """A partially-constructed strategy must still answer, not raise."""
    assert MEICStrategy._deferred_fill_settle_s(_stub(None)) == 120.0


def test_window_clears_the_observed_lag_maximum_with_margin():
    """THE DERIVATION, as an assertion.

    The rule that produced both 45s and 120s is "do not sit on your own sample
    maximum". 45s broke it the moment a 48s lag was observed. Keep at least a
    2x margin over whatever the largest observed lag is, so that raising
    OBSERVED_LAG_MAX_S after a new observation forces the window up with it.
    """
    window = MEICStrategy._DEFERRED_FILL_SETTLE_DEFAULT_S
    assert window > OBSERVED_LAG_MAX_S, (
        f"settle window {window}s is at or under the observed lag maximum "
        f"{OBSERVED_LAG_MAX_S}s — this is the 2026-09-27 derivation error "
        f"repeated: the threshold is sitting on its own sample maximum"
    )
    assert window >= 2.0 * OBSERVED_LAG_MAX_S, (
        f"settle window {window}s gives under 2x margin on the observed "
        f"{OBSERVED_LAG_MAX_S}s lag"
    )


def test_window_stays_well_inside_the_absolute_drop():
    """A window at or above MAX_AGE would mean nothing is ever re-checked."""
    window = MEICStrategy._DEFERRED_FILL_SETTLE_DEFAULT_S
    cap = MEICStrategy._DEFERRED_FILL_MAX_AGE_S
    assert window < cap / 2.0, (
        f"settle window {window}s is not comfortably inside the {cap}s drop — "
        f"a parked check would risk being dropped before it is ever due"
    )
