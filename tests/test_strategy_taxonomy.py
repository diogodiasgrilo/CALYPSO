"""Tests for shared.strategy_taxonomy — the single source of truth for strategy identity + groups.

Pins the invariants the whole grouping redesign rests on: every strategy's group exists; a group
carries one P&L shape so credit and debit never co-group; comparison stays within a comparable
group; unknown letters degrade gracefully (never KeyError); the env parse + poller-owner gate; and
the IMPORT PURITY guarantee (this module must stay stdlib-only so the read-only dashboard can import
it without dragging in trading/broker code).
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from shared import strategy_taxonomy as tax


# ---------------------------------------------------------------------------
# Structural integrity of the taxonomy data
# ---------------------------------------------------------------------------

def test_every_strategy_group_exists():
    for sid, m in tax.STRATEGIES.items():
        assert m.group_id in tax.GROUPS, f"{sid} -> unknown group {m.group_id!r}"


def test_strategy_id_matches_its_key():
    for sid, m in tax.STRATEGIES.items():
        assert m.id == sid


def test_strategy_pnl_shape_matches_its_group():
    for sid, m in tax.STRATEGIES.items():
        assert m.pnl_shape == tax.GROUPS[m.group_id].pnl_shape, (
            f"{sid} pnl_shape {m.pnl_shape!r} != group {m.group_id} shape"
        )


def test_a_group_has_exactly_one_pnl_shape():
    # The apples-to-apples invariant: no group mixes credit and debit members.
    for gid, g in tax.GROUPS.items():
        shapes = {tax.STRATEGIES[sid].pnl_shape for sid in tax.members(gid)}
        assert len(shapes) <= 1, f"group {gid} mixes pnl_shapes {shapes}"
        if shapes:
            assert shapes == {g.pnl_shape}


def test_known_membership_today():
    assert tax.members("ic_0dte") == ["a", "b", "c"]
    assert tax.members("calendar_multiday") == ["d", "e"]  # E (SPY double calendar) joined D


# ---------------------------------------------------------------------------
# Comparability (the credit-vs-debit guard, by data not by scattered ifs)
# ---------------------------------------------------------------------------

def test_comparable_with_is_same_group_only():
    assert sorted(tax.comparable_with("a")) == ["b", "c"]
    assert sorted(tax.comparable_with("b")) == ["a", "c"]
    assert tax.comparable_with("d") == ["e"]  # D and E share the calendar group
    assert tax.comparable_with("e") == ["d"]


def test_comparable_never_crosses_pnl_shape():
    for sid in tax.STRATEGIES:
        my_shape = tax.meta(sid).pnl_shape
        for other in tax.comparable_with(sid):
            assert tax.meta(other).pnl_shape == my_shape


def test_groups_of_clusters_by_group():
    assert tax.groups_of(["a", "d", "c"]) == {"ic_0dte": ["a", "c"], "calendar_multiday": ["d"]}


# ---------------------------------------------------------------------------
# Unknown-letter graceful degradation (never KeyError)
# ---------------------------------------------------------------------------

def test_unknown_letter_returns_safe_sentinel():
    m = tax.meta("z")
    assert m.id == "z"
    assert m.strategy_class == ""
    assert m.group_id == "unknown"
    assert m.pnl_shape == "unknown"


def test_unknown_letter_is_not_comparable():
    assert tax.comparable_with("z") == []
    assert tax.group("z").comparable is False


def test_meta_never_raises_for_arbitrary_input():
    for vid in ("", "zz", "Q", "9", "d "):
        tax.meta(vid)  # must not raise


# ---------------------------------------------------------------------------
# Env parse + poller-owner gate
# ---------------------------------------------------------------------------

def test_variant_id_defaults_to_a_when_unset(monkeypatch):
    monkeypatch.delenv("HYDRA_VARIANT_ID", raising=False)
    assert tax.variant_id() == "a"
    assert tax.is_default_variant() is True


def test_variant_id_reads_and_normalizes_env(monkeypatch):
    monkeypatch.setenv("HYDRA_VARIANT_ID", " C ")
    assert tax.variant_id() == "c"
    assert tax.is_default_variant() is False


def test_blank_env_is_default(monkeypatch):
    monkeypatch.setenv("HYDRA_VARIANT_ID", "   ")
    assert tax.variant_id() == "a"
    assert tax.is_default_variant() is True


# ---------------------------------------------------------------------------
# bot_name unified scheme (display only — not the frozen alert-wire name)
# ---------------------------------------------------------------------------

def test_bot_name_default_variant_has_no_suffix():
    assert tax.bot_name("a") == "HYDRA"


def test_bot_name_non_default_is_base_underscore_id():
    assert tax.bot_name("b") == "HYDRA_B"
    assert tax.bot_name("c") == "HYDRA_C"
    assert tax.bot_name("d") == "DCTM_D"


# ---------------------------------------------------------------------------
# Fail-stop class validation (the table can lie vs the runtime resolver)
# ---------------------------------------------------------------------------

def test_assert_class_matches_passes_on_agreement():
    tax.assert_class_matches("c", "brandon")  # no raise


def test_assert_class_matches_raises_on_mismatch():
    with pytest.raises(ValueError):
        tax.assert_class_matches("d", "hydra")  # taxonomy says double_calendar


def test_assert_class_matches_skips_unknown_letter():
    tax.assert_class_matches("z", "anything")  # unknown -> no committed binding -> skip


# ---------------------------------------------------------------------------
# IMPORT PURITY — the dashboard must be able to import this without trading/broker deps
# ---------------------------------------------------------------------------

_ALLOWED_IMPORT_ROOTS = {"os", "dataclasses", "typing", "__future__"}


def test_module_imports_are_stdlib_only():
    src = Path(tax.__file__).read_text()
    tree = ast.parse(src)
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                pytest.fail(f"relative import not allowed in taxonomy: level={node.level}")
            if node.module:
                roots.add(node.module.split(".")[0])
    forbidden = roots - _ALLOWED_IMPORT_ROOTS
    assert not forbidden, (
        f"strategy_taxonomy must be stdlib-only; found disallowed import roots: {sorted(forbidden)}"
    )
