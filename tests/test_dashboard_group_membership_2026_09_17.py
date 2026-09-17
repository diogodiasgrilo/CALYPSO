"""A group comparison must render EVERY member the taxonomy gives it.

Defect D9, found by the 2026-09-17 visual audit
(docs/DASHBOARD_VISUAL_AUDIT_2026_09_17.md).

``routers/variants.py`` hardcoded ``_VARIANT_IDS = ["a", "b", "c"]`` and built
its reader pools from it. The taxonomy-driven group endpoint resolved
``ic_0dte``'s members as ``[a, b, c, f]`` and handed them to
``build_comparison``, which filtered them through those readers — silently
dropping F. ``undefined_risk_0dte`` (member ``[g]``) came back completely empty.

Measured on the live VM before the fix:

    ic_0dte              members=[a, b, c, f]  rendered=[A, B, C]
    undefined_risk_0dte  members=[g]           rendered=[]
    calendar_multiday    members=[d, e]        rendered=2 calendars   (fine)

The frontend renders column headers from ``member_ids``, so F got a header with
no data behind it: its column collapsed to zero width and its em-dashes merged
into C's numbers, printing literal garbage like ``75—`` and ``7—``.

The fix separates two things the old code conflated:

  ``_VARIANT_IDS``   the DEFAULT member set of the legacy /api/variants endpoint
  ``_READABLE_IDS``  every variant that CAN be read (taxonomy-derived)

D and E stay out of both: they are multi-day net-DEBIT calendars and the
IC-shaped math here would mis-render them (debit shown as credit). That
exclusion is deliberate and long-standing, so it gets its own test.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import shared.strategy_taxonomy as tax  # noqa: E402
from dashboard.backend.routers import variants as V  # noqa: E402


def test_every_non_calendar_strategy_is_readable():
    """F and G were invisible purely because no reader existed for them."""
    expected = {
        vid for vid in tax.available_ids()
        if tax.group(vid).pnl_shape != "debit"
    }
    assert expected <= set(V._READABLE_IDS), (
        f"Strategies {sorted(expected - set(V._READABLE_IDS))} have no state reader, "
        f"so any group containing them silently drops them from the comparison."
    )


def test_f_and_g_specifically_are_readable():
    """The two the audit caught. Named explicitly so a regression is unambiguous."""
    for vid in ("f", "g"):
        assert vid in V._state_readers, f"variant {vid} has no state reader"
        assert vid in V._db_readers, f"variant {vid} has no DB reader"
        assert vid in V._metrics_readers, f"variant {vid} has no metrics reader"


def test_calendars_stay_out_of_the_ic_reader_pool():
    """D/E must NOT be swept in by widening the pool. They are net-debit
    calendars; the IC comparison math would render their debit as credit."""
    for vid in ("d", "e"):
        assert vid not in V._READABLE_IDS, (
            f"variant {vid} is a net-debit calendar and must not enter the "
            f"IC-shaped comparison path — it has its own renderer."
        )


def test_build_comparison_honours_every_member_it_is_given():
    """The actual failure: members went in, fewer came out."""
    members = tax.members("ic_0dte")
    payload = V.build_comparison(list(members), baseline_id="a")
    rendered = set((payload.get("variants") or {}).keys())
    expected = {m.upper() for m in members}
    assert expected == rendered, (
        f"build_comparison dropped {sorted(expected - rendered)}. The frontend "
        f"still draws a header for them from member_ids, so they appear as a "
        f"zero-width column of em-dashes merged into the neighbouring numbers."
    )


def test_single_member_group_is_not_empty():
    """undefined_risk_0dte has exactly one member and returned {}."""
    members = tax.members("undefined_risk_0dte")
    assert members, "taxonomy says undefined_risk_0dte has no members"
    payload = V.build_comparison(list(members), baseline_id=members[0])
    rendered = set((payload.get("variants") or {}).keys())
    assert rendered == {m.upper() for m in members}, (
        "G's own group page rendered nothing at all — 18 config rows of em-dash."
    )


def test_legacy_endpoint_default_is_unchanged():
    """Widening the READER pool must not widen what the legacy /comparison
    endpoint compares by default. Conflating those two is the original bug, and
    adding G there would put a naked strangle on an iron-condor leaderboard,
    which the taxonomy explicitly marks non-comparable."""
    assert V._VARIANT_IDS == ["a", "b", "c"]
    # `comparable` is a GROUP property, not a strategy one — G's group is the
    # solo undefined-risk cohort and is explicitly non-comparable.
    assert tax.group("g").comparable is False, (
        "G's group is marked comparable — re-check whether it belongs on a "
        "shared leaderboard before relying on this test's reasoning."
    )


def test_readable_ids_is_derived_not_hardcoded():
    """Adding a strategy must not require editing this router — that promise is
    what the taxonomy exists for, and breaking it is how F went missing."""
    import inspect

    src = inspect.getsource(V._readable_ids)
    assert "tax." in src, "_readable_ids no longer consults the taxonomy"
    assert '"a"' not in src and "'a'" not in src, (
        "_readable_ids hardcodes variant letters again."
    )
