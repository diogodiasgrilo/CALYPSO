"""Two timestamp formats reach the UI, and they must be parsed differently.

Found while replaying a real past session (B, 2026-07-16) through the dashboard.
The entry cards rendered 04:46 / 05:15 / 05:46 for entries the database records
at 09:46 / 10:15 / 10:46 — a 5-hour shift.

It turned out to be the replay fixture, NOT a product bug, but the underlying
hazard is real and undocumented:

    /api/hydra/entries   (state file)  '2026-09-17T09:45:30.414039-04:00'  ISO + offset
    /api/metrics/entries (database)    '2026-05-05 10:46:26'               NAIVE wall-clock ET

``formatTime`` does ``new Date(iso)`` then converts to America/New_York. Given
an offset-bearing string that is correct. Given a NAIVE one the engine assumes a
zone, and the entry renders hours away from when it happened — silently, with no
error and no NaN.

Today nothing routes a database timestamp through ``formatTime``: the two
database consumers (``SessionReplay.toTime24h`` and
``Analytics.parseEntryTimeToSlot``) extract the clock time by REGEX, which
treats the naive string as the wall-clock ET it is. That is the correct handling
and this file pins it, because the failure mode is a plausible-looking wrong
time rather than a crash — the kind of defect that survives a long time.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SRC = ROOT / "dashboard" / "frontend" / "src"
FORMATTERS = SRC / "lib" / "formatters.ts"
SESSION_REPLAY = SRC / "components" / "history" / "SessionReplay.tsx"
ANALYTICS = SRC / "pages" / "Analytics.tsx"
BACKEND = ROOT / "dashboard" / "backend"


def test_format_time_converts_via_a_zone():
    """It is only SAFE for offset-bearing strings, which is why the consumers
    below must not hand it a naive one."""
    src = FORMATTERS.read_text()
    fn = src[src.index("export function formatTime"):]
    fn = fn[: fn.index("\n}")]
    assert "new Date(" in fn
    assert 'timeZone: "America/New_York"' in fn


def test_database_consumers_extract_the_clock_time_by_regex():
    """A naive DB timestamp is already wall-clock ET. Extracting the digits is
    correct; re-interpreting it through a timezone is not."""
    replay = SESSION_REPLAY.read_text()
    fn = replay[replay.index("function toTime24h"):]
    fn = fn[: fn.index("\n}")]
    assert "new Date(" not in fn, (
        "toTime24h now constructs a Date from a DB timestamp — naive strings "
        "will render hours off, silently."
    )
    assert re.search(r"\\d\{4\}-\\d\{2\}-\\d\{2\}", fn), "no literal date-time extraction"

    an = ANALYTICS.read_text()
    fn2 = an[an.index("function parseEntryTimeToSlot"):]
    fn2 = fn2[: fn2.index("\n}")]
    assert "new Date(" not in fn2, (
        "parseEntryTimeToSlot now constructs a Date — entry-slot bucketing "
        "would shift by the browser's timezone offset."
    )


def test_the_two_sources_really_do_differ():
    """Pin the premise. If the DB ever starts writing offsets, or the state file
    stops, the handling above needs revisiting rather than silently coping."""
    rec = ROOT / "shared" / "data_recorder.py"
    assert rec.exists()
    src = rec.read_text()
    # The recorder writes entry_time from a datetime formatted without tz.
    assert "entry_time" in src


@pytest.mark.parametrize("consumer", ["SessionReplay.tsx", "Analytics.tsx"])
def test_db_consumers_do_not_import_format_time(consumer):
    """The single rule that keeps this safe: never format a database timestamp
    with the state-file formatter."""
    path = SESSION_REPLAY if consumer == "SessionReplay.tsx" else ANALYTICS
    src = path.read_text()
    # It may legitimately import other formatters; only formatTime is unsafe here.
    m = re.search(r"import \{([^}]*)\} from \"[^\"]*lib/formatters\"", src)
    imported = {x.strip() for x in (m.group(1).split(",") if m else [])}
    assert "formatTime" not in imported, (
        f"{consumer} imports formatTime, and its data comes from the database — "
        f"naive timestamps would render hours away from when they happened."
    )
