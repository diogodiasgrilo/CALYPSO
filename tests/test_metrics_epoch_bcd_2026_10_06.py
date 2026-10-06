"""B, C and D must report the same lifetime era as the dashboard shows.

The metrics file and the dashboard each rebase the lifetime number, to
DIFFERENT settings: the dashboard to `variant_X_baseline_date`, the bot to
`strategy.metrics_epoch_date`. Variant E's config states the invariant in prose
— "the two must move together or the dashboard and the bot will disagree about
the same lifetime number" — and nothing enforced it.

They disagreed on three variants, measured 2026-10-06:

    B   file +17,404.62   dashboard  -2,024.85    <- the headline flipped SIGN
    C   file  -6,879.03   dashboard  -9,778.33
    D   file  -9,746.70   dashboard  -5,919.70    <- and the other way

B's gap is 91% simulated history: it is why +$21,439 was quoted for a strategy
whose traded record is a small loss.
"""
import json
import pathlib

import pytest

CONFIG = "bots/hydra/config/config_variant_%s.json"

# (variant, the dashboard baseline it must mirror)
PAIRS = [("b", "2026-07-24"), ("c", "2026-06-11"), ("d", "2026-07-11")]


def _strategy(vid):
    return json.load(open(CONFIG % vid))["strategy"]


@pytest.mark.parametrize("vid,expected", PAIRS)
def test_the_epoch_is_set_and_matches_the_dashboard_baseline(vid, expected):
    assert _strategy(vid).get("metrics_epoch_date") == expected, (
        f"variant {vid.upper()}'s metrics epoch must mirror its dashboard "
        f"baseline {expected}, or the two surfaces report different eras")


def _committed_baseline(vid: str) -> str:
    """The baseline as COMMITTED in dashboard/backend/config.py.

    Deliberately NOT the live `settings` singleton: other tests in the suite
    mutate it, so reading it made these assertions pass alone and fail in the
    full run (all three reported ''). A flaky guard is worse than none. The
    invariant being pinned is about the committed configuration anyway, not
    about whatever a sibling test left in a shared object.
    """
    from dashboard.backend.config import Settings
    f = Settings.model_fields.get(f"variant_{vid}_baseline_date")
    return str((f.default if f else "") or "").strip()


@pytest.mark.parametrize("vid,expected", PAIRS)
def test_the_dashboard_still_agrees(vid, expected):
    """If someone moves the dashboard baseline and not the epoch, this fails —
    which is the whole invariant."""
    dash = _committed_baseline(vid)
    assert dash == expected, (
        f"dashboard baseline for {vid.upper()} is {dash!r}, config epoch is "
        f"{expected!r} — they have drifted apart again")


@pytest.mark.parametrize("vid,_", PAIRS)
def test_the_change_carries_its_reason(vid, _):
    """A bare date would leave the next reader guessing why a lifetime record
    restarts mid-history."""
    c = _strategy(vid).get("_comment_metrics_epoch_date", "")
    assert "ARCHIVED, NOT DELETED" in c, f"{vid}: must state that history is kept"
    assert "pre_epoch" in c, f"{vid}: must name where the old totals go"


def test_variant_E_is_untouched():
    """E was the first user of this mechanism; this change must not disturb it."""
    assert _strategy("e").get("metrics_epoch_date") == "2026-09-24"


@pytest.mark.parametrize("vid", ["a", "f", "g", "h"])
def test_variants_with_no_dashboard_baseline_get_no_epoch(vid):
    """Setting an epoch where the dashboard has no baseline would CREATE the
    disagreement this change exists to remove."""
    dash = _committed_baseline(vid)
    if dash:
        pytest.skip(f"{vid} now has a dashboard baseline — re-evaluate")
    path = pathlib.Path("bots/hydra/config/config.json" if vid == "a" else CONFIG % vid)
    if not path.exists():
        pytest.skip(f"{vid} config not in the repo (A's is gitignored)")
    assert not json.load(open(path))["strategy"].get("metrics_epoch_date"), (
        f"{vid.upper()} has an epoch but no dashboard baseline — that creates "
        f"the very mismatch this change removes")
