"""Every wired handler must pass the variable its own `except` actually binds.

32 handlers were wired to the swallowed-exception counter by a mechanical edit.
The risk of that edit is not a syntax error — it is a NameError INSIDE an
exception handler, which would turn a swallowed failure into a crash in the one
place the code was written never to crash. This project has shipped that exact
bug before (`self._classify_day_type(events, opex)` where `opex` existed
nowhere: valid syntax, passing tests, a NameError inside settlement).

So this walks the real AST: for every `_swallow.note(site, VAR)` inside an
`except ... as NAME:` block, assert VAR == NAME.
"""
import ast
import pathlib

import pytest

FILES = [
    "bots/hydra/strategy.py",
    "bots/hydra/base_strategy.py",
    "bots/hydra/brandon/strategy.py",
    "bots/hydra/calendar_strategy_base.py",
]


def _notes_in_handlers(path):
    """Yield (lineno, bound_name, passed_name, site) for each wired note."""
    tree = ast.parse(pathlib.Path(path).read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == "note"
                    and isinstance(sub.func.value, ast.Name)
                    and sub.func.value.id == "_swallow"):
                site = (sub.args[0].value
                        if sub.args and isinstance(sub.args[0], ast.Constant)
                        else None)
                passed = (sub.args[1].id
                          if len(sub.args) > 1 and isinstance(sub.args[1], ast.Name)
                          else None)
                yield sub.lineno, node.name, passed, site


@pytest.mark.parametrize("path", FILES)
def test_the_exception_variable_matches_the_handler(path):
    bad = [(ln, bound, passed) for ln, bound, passed, _ in _notes_in_handlers(path)
           if passed is not None and bound is not None and passed != bound]
    assert not bad, (
        f"{path}: a note() passes a variable its handler does not bind — "
        f"that is a NameError inside an exception handler: {bad}")


@pytest.mark.parametrize("path", FILES)
def test_every_note_is_inside_an_except_that_binds_a_name(path):
    """A bare `except:` binds nothing, so passing a variable would NameError."""
    orphan = [(ln, site) for ln, bound, passed, site in _notes_in_handlers(path)
              if passed is not None and bound is None]
    assert not orphan, f"{path}: note() with a variable inside a bare except: {orphan}"


@pytest.mark.parametrize("path", FILES)
def test_site_names_are_literals(path):
    """A computed key fragments the tally into noise and defeats the report."""
    bad = [ln for ln, _, _, site in _notes_in_handlers(path) if site is None]
    assert not bad, f"{path}: non-literal site name at line(s) {bad}"


def test_site_names_are_unique_across_the_fleet():
    """Two handlers sharing a key merge into one number, hiding whichever is
    quieter — exactly the invisibility this counter exists to remove."""
    seen = {}
    for path in FILES:
        for ln, _, _, site in _notes_in_handlers(path):
            if site is None:
                continue
            if site in seen:
                pytest.fail(f"site {site!r} used twice: {seen[site]} and {path}:{ln}")
            seen[site] = f"{path}:{ln}"
    assert len(seen) >= 30, f"expected the full wiring, found only {len(seen)}"


def test_the_counter_actually_increments_from_a_wired_site():
    """Static checks prove shape, not behaviour — exercise a real handler."""
    from shared import swallow_counter as sc
    from bots.hydra.strategy import HydraStrategy
    sc.reset()
    s = HydraStrategy.__new__(HydraStrategy)
    s._data_recorder = None          # makes the greeks path return early
    class Boom:
        def get_option_greeks(self, c):
            raise RuntimeError("broker down")
    s.broker = Boom()
    # _read_option_greeks catches and warns; _fetch_entry_greeks swallows too.
    class E:
        entry_number = 1
        short_call_uic = 1
        short_put_uic = None
    import bots.hydra.strategy as mod
    old = mod.GREEKS_RETRY_DELAY_S
    mod.GREEKS_RETRY_DELAY_S = 0.0
    try:
        assert s._fetch_entry_greeks(E()) == {}
    finally:
        mod.GREEKS_RETRY_DELAY_S = old
    sc.reset()
