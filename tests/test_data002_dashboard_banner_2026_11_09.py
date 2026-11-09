"""The dashboard must SAY WHY entries stopped, not just stop showing them.

During the 2026-11-02 delayed-data incident the dashboard showed nothing at
all. The only visible symptom was a live seat that had quietly stopped
completing entries — which looks identical to a quiet market. A halt the
operator cannot see is a halt that lasts a week, and this one did.

These pin the WIRING: the state file carries the fields, the type declares
them, the banner reads them, and it is mounted above the FOMC strip (a
degraded feed outranks a calendar policy, because it makes every price on the
page suspect).
"""

from __future__ import annotations

import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "dashboard" / "frontend" / "src"
BANNER = SRC / "components" / "dashboard" / "DataFeedBanner.tsx"
HOST = SRC / "components" / "dashboard" / "IronCondorDashboard.tsx"
STORE = SRC / "store" / "hydraStore.ts"
BASE = ROOT / "bots" / "hydra" / "base_strategy.py"

FIELDS = ("market_data_degraded", "market_data_reason", "market_data_degraded_since")


def test_the_bot_writes_all_three_fields_to_the_state_file():
    """The fields must be inside `_save_state_to_disk`, not merely in a file.

    ⚠️ THIS TEST WAS VACUOUS AND SHIPPED A BROKEN FEATURE. It originally read
    `assert '"market_data_degraded"' in BASE.read_text()` — which passed
    because the strings existed SOMEWHERE in base_strategy.py. They were in
    `get_dashboard_metrics()`, a different dict that never reaches
    hydra_state.json, so the banner could never have rendered. Caught by
    post-deploy verification of the real state file, not by this test.

    Parsing the actual function is the difference between "the string exists"
    and "the writer writes it".
    """
    import ast
    strat = (ROOT / "bots" / "hydra" / "strategy.py").read_text()
    tree = ast.parse(strat)
    writer = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef)
                   and n.name == "_save_state_to_disk"), None)
    assert writer is not None, "the state-file writer has been renamed"
    seg = ast.get_source_segment(strat, writer) or ""
    for f in FIELDS:
        assert f'"{f}"' in seg, (
            f"{f} is not written by _save_state_to_disk — the dashboard "
            f"reads hydra_state.json and would never see it")


def test_the_frontend_type_declares_them():
    src = STORE.read_text()
    for f in FIELDS:
        assert f in src, f"{f} missing from HydraState"


def test_the_banner_exists_and_reads_the_flag():
    src = BANNER.read_text()
    assert "market_data_degraded" in src
    assert "hydraState" in src


def test_it_renders_nothing_when_data_is_healthy():
    """The common case must not show a scary empty strip."""
    src = BANNER.read_text()
    assert "if (!hydraState?.market_data_degraded) return null;" in src


def test_it_states_that_exits_still_work():
    """The single most important fact for an operator reading this strip."""
    src = BANNER.read_text()
    assert "Exits" in src and "WORKING" in src
    assert "BLOCKED" in src


def test_it_names_the_likely_cause_and_where_to_look():
    src = BANNER.read_text()
    assert "Market Data Subscriptions" in src
    assert "cash balance" in src or "account cash" in src


def test_it_shows_when_the_degradation_started():
    src = BANNER.read_text()
    assert "market_data_degraded_since" in src


def test_it_is_mounted_ABOVE_the_fomc_strip():
    """A degraded feed outranks a calendar policy."""
    src = HOST.read_text()
    assert "<DataFeedBanner />" in src, "banner never mounted"
    assert src.index("<DataFeedBanner />") < src.index("<FOMCBanner />")


def test_it_is_announced_to_screen_readers():
    src = BANNER.read_text()
    assert 'role="alert"' in src and 'aria-live="assertive"' in src


def test_it_uses_the_brand_palette_not_an_invented_colour():
    """tradingColors is the single source; a hardcoded hex drifts from index.css."""
    src = BANNER.read_text()
    assert "from \"../../lib/tradingColors\"" in src
    assert "#" not in src.split("export function")[1], "hardcoded colour in the markup"
