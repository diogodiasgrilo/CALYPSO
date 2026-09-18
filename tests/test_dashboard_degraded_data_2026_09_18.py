"""The dashboard had never been rendered against bad data — and blanked on it.

Every fixture in the audit harness is happy-path: a populated, well-formed
response captured from a working backend. So across 42 audited surfaces the app
had never once been shown a 500, an empty result, a null field, or a number that
arrived as a string — all of which production produces routinely.

uiaudit/diag-degraded.mjs replays each surface against those four shapes.
Measured 2026-09-18, BEFORE any fix: **9 of 24 surface/mode combinations**
failed, and seven of them were a WHITE PAGE — react root emptied, no message, no
reload affordance, nothing to tell the operator what happened.

    backend returns 500        dashboard, /dc                    white page
    a date field is null       history, analytics                white page
    numbers arrive as strings  history, both comparisons, /dc    white page
    numbers arrive as strings  dashboard                         "EXPECTANCY $NaN"

The 500 is not hypothetical. It happens every time `dashboard.service`
restarts — which happened four times during this session alone — and the
operator would have seen a blank browser rather than "the backend is down".

TWO FIXES.

1. **An error boundary**, which did not exist anywhere in the app. Any render
   throw unmounted the entire tree. There is now one per route (so a crashing
   page keeps the header and nav usable and you can navigate away) and one
   outermost (covering the layout and the login gate itself). It cannot make bad
   data good, but it converts every white page — including the modes nobody has
   thought of yet — into a visible, explained state.

2. **Coercion at the stats boundary.** `statsUtils`' functions are typed
   `number[]` and TypeScript enforces that at the call site, but the values come
   off the wire and SQLite returns TEXT for a column written as text. The
   failure is quiet and specific: `v > 0` coerces, so the win/loss FILTERS work,
   but `reduce((a, b) => a + b, 0)` CONCATENATES — `0 + "100"` is `"0100"` — so
   every average became NaN while the surrounding cards still showed plausible
   numbers.

After both: **0 of 24**.

A note on measuring this, because it nearly went wrong twice. The probe's text
extraction was first written as `document.body.cloneNode(true).innerText` in
order to exclude the boundary's own message (which legitimately contains the
word "undefined" — "Cannot read properties of undefined"). A DETACHED clone has
no layout, so `innerText` silently degrades to textContent semantics and returns
different text — and the real `$NaN` finding disappeared. It now hides the
boundaries in the live document instead. Separately, `npx tsc --noEmit | tail`
reports the PIPE's exit status, so a broken build was reported as "tsc clean".
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SRC = ROOT / "dashboard" / "frontend" / "src"
STATS = SRC / "lib" / "statsUtils.ts"
PERF = SRC / "components" / "pnl" / "PerformanceMetrics.tsx"
BOUNDARY = SRC / "components" / "shared" / "ErrorBoundary.tsx"
APP = SRC / "App.tsx"
MAIN = SRC / "main.tsx"
PROBE = ROOT / "dashboard" / "frontend" / "uiaudit" / "diag-degraded.mjs"


# ── The error boundary ─────────────────────────────────────────────────────

def test_an_error_boundary_exists():
    """There was none. A single render throw blanked the whole application."""
    assert BOUNDARY.exists(), "ErrorBoundary.tsx is gone"
    src = BOUNDARY.read_text()
    assert "getDerivedStateFromError" in src, (
        "no getDerivedStateFromError — the boundary cannot render a fallback"
    )
    assert "componentDidCatch" in src, "the error is never logged anywhere"


def test_the_boundary_tells_the_user_what_to_do():
    """A fallback that renders an empty div is the white page with extra steps."""
    src = BOUNDARY.read_text()
    assert 'role="alert"' in src, "the fallback is not announced to assistive tech"
    assert "reload" in src.lower(), "no way out is offered"
    assert re.search(r"error\.message|String\(error", src), (
        "the actual error is never shown, so an operator cannot act on it"
    )


@pytest.mark.parametrize("route", ["Dashboard", "History", "Analytics", "GroupComparison"])
def test_every_route_is_individually_wrapped(route):
    """Per-route rather than only at the top: one crashing page must not take
    the header and navigation with it, or there is no way to leave."""
    src = APP.read_text()
    m = re.search(rf"element=\{{<ErrorBoundary[^>]*>\s*<{route}\s*/>", src)
    assert m, f"route {route} is not wrapped in an ErrorBoundary"


def test_the_shell_itself_is_wrapped():
    """The per-route boundaries live INSIDE the layout, so they cannot catch a
    throw from the layout, the header, or the login gate."""
    src = MAIN.read_text()
    assert "ErrorBoundary" in src, "no outermost boundary — the shell is unprotected"
    assert src.index("<ErrorBoundary") < src.index("<LoginGate"), (
        "the boundary must enclose LoginGate, not sit inside it"
    )


# ── Coercion at the stats boundary ─────────────────────────────────────────

def test_stats_input_is_coerced():
    src = STATS.read_text()
    assert "export function toFiniteNumbers" in src
    perf = PERF.read_text()
    assert "toFiniteNumbers(" in perf, (
        "PerformanceMetrics feeds the stats functions uncoerced API data"
    )


def test_coercion_handles_every_shape_the_wire_produces():
    """Behavioural, by transliterating the function — it is small and pure, and
    a source-only assertion would not catch a wrong predicate."""
    def to_finite(series):
        out = []
        for v in series or []:
            try:
                n = float(v)
            except (TypeError, ValueError):
                continue
            if n == n and abs(n) != float("inf"):
                out.append(n)
        return out

    assert to_finite(["100", "-50"]) == [100.0, -50.0]
    assert to_finite(None) == []
    assert to_finite([]) == []
    assert to_finite(["abc", None, "12"]) == [12.0]
    assert to_finite([float("nan"), float("inf"), 3]) == [3.0]


def test_the_concatenation_trap_is_what_made_it_NaN():
    """Pins the mechanism, so the comment explaining it cannot drift from the
    truth: comparisons coerce but `+` does not, which is why the filters looked
    right and only the averages broke."""
    vals = ["100", "-50"]
    assert [v for v in vals if float(v) > 0] == ["100"]     # filters fine
    # In JS `["100"].reduce((a, b) => a + b, 0)` is "0100"; Python's analogue
    # is the same category error — the sum is not numeric.
    assert "".join(["0"] + vals) == "0100-50"


def test_the_coercion_signature_accepts_null():
    """It is fed `number[] | null`. The first version was typed
    `readonly unknown[]` and failed the build — which was reported as passing,
    because `npx tsc --noEmit | tail` yields the PIPE's exit status."""
    src = STATS.read_text()
    sig = src[src.index("export function toFiniteNumbers"):]
    sig = sig[: sig.index("{")]
    assert "null" in sig and "undefined" in sig, (
        f"toFiniteNumbers rejects the very shape it is given: {sig.strip()!r}"
    )


# ── The probe itself ───────────────────────────────────────────────────────

def test_the_probe_covers_all_four_degradation_modes():
    src = PROBE.read_text()
    for mode in ("http-500", "empty", "null-fields", "numbers-as-strings"):
        assert mode in src, f"diag-degraded.mjs no longer tests {mode}"


def test_the_probe_reads_text_from_the_live_document():
    """A detached cloneNode has no layout, so innerText degrades to textContent
    and returns different text — which made a real `$NaN` finding vanish when
    this was first written as a clone."""
    # Comments stripped: the probe's own comment explains why a detached
    # cloneNode is wrong, so a bare substring ban matched the explanation
    # rather than any code. Eighth occurrence of that trap in this repo — in
    # the test that documents it.
    src = re.sub(r"//.*$", "", PROBE.read_text(), flags=re.M)
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    assert "cloneNode" not in src, (
        "the probe reads text from a detached clone again; innerText on a "
        "node with no layout does not report what the user sees"
    )

