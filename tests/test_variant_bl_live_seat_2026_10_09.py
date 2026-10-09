"""Variant `bl` — the Brandon stack on a 12:15-15:15 grid, made the live paper
seat on 2026-10-09, with the former live seat `b` demoted to dry-run shadow.

⚠️ NOT VALIDATED. The later slots looked best in-sample (#6 +$2,438, #7 +$3,223
over B's live era) but a 20,000-shuffle permutation on the per-slot spread gives
**p = 0.597** — indistinguishable from random labelling — and 13:15 onward has no
live record at all. `bl` exists to generate OUT-OF-SAMPLE data. These tests pin
the mechanics, not the merit.

The important one is `test_bl_is_otherwise_an_EXACT_copy_of_b`: the whole premise
is "same strategy, different clock", so anything else differing is a bug.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared import strategy_taxonomy as tax  # noqa: E402

CFG = ROOT / "bots/hydra/config"
EXPECTED_GRID = ["12:15", "12:45", "13:15", "13:45", "14:15", "14:45", "15:15"]
B_GRID = ["09:45", "10:15", "10:45", "11:15", "11:45", "12:15", "12:45"]


def _cfg(v):
    return json.load(open(CFG / f"config_variant_{v}.json"))


class TestExactlyOneLiveSeat:

    def test_only_one_variant_has_status_live(self):
        """`live_seat_id()` asserts len == 1; two live rows fail it closed and
        the dashboard then names a dry-run variant as 'the bot'."""
        live = [k for k, m in tax.STRATEGIES.items() if getattr(m, "status", "") == "live"]
        assert live == ["bl"], live

    def test_only_one_config_has_dry_run_false(self):
        seats = [v for v in ("a", "b", "bl", "c", "d", "e", "f", "g", "h")
                 if (CFG / f"config_variant_{v}.json").exists()
                 and _cfg(v).get("dry_run") is False]
        assert seats == ["bl"], seats

    def test_only_the_live_seat_alerts(self):
        on = [v for v in ("b", "bl", "c", "d", "e", "f", "g", "h")
              if (CFG / f"config_variant_{v}.json").exists()
              and (_cfg(v).get("alerts") or {}).get("enabled") is True]
        assert on == ["bl"], on


class TestTheGrid:

    def test_bl_runs_the_shifted_grid(self):
        assert _cfg("bl")["strategy"]["entry_times"] == EXPECTED_GRID

    def test_b_keeps_its_original_grid(self):
        """Demoting b must not also move its clock — it is the control."""
        assert _cfg("b")["strategy"]["entry_times"] == B_GRID

    def test_slot_count_matches_the_vix_cap(self):
        """The config's own warning: 'ALWAYS change max_entries in the SAME edit
        as entry_times — a stale cap silently drops the extra slot via the VIX
        regime.' Zone 0/1 must admit every slot."""
        s = _cfg("bl")["strategy"]
        regime = s.get("vix_regime") or s.get("vix_regime_adaptive") or {}
        caps = regime.get("max_entries")
        assert caps is not None, "no max_entries — the cap would default and drop slots"
        assert caps[0] >= len(s["entry_times"]), (caps, s["entry_times"])

    def test_the_grid_is_strictly_increasing_and_half_hourly(self):
        mins = [int(h) * 60 + int(m) for h, m in (t.split(":") for t in EXPECTED_GRID)]
        assert mins == sorted(mins)
        assert all(b - a == 30 for a, b in zip(mins, mins[1:]))

    def test_no_slot_lands_after_the_EOD_flatten(self):
        """There is NO late-entry cutoff in the code — an entry opened after the
        flatten time would be opened into a window that immediately closes it."""
        eod = _cfg("bl")["strategy"].get("eod_flatten", {}).get("time_et", "15:50")
        eh, em = (int(x) for x in eod.split(":"))
        for t in EXPECTED_GRID:
            h, m = (int(x) for x in t.split(":"))
            assert (h, m) < (eh, em), f"slot {t} is at/after the {eod} flatten"


class TestBlIsOtherwiseAnExactCopyOfB:

    ALLOWED = {
        "dry_run",                 # bl is the live seat, b is not
        "alerts",                  # only the live seat alerts
        "strategy.entry_times",    # the whole point
        "strategy._comment_entry_times",
        "_comment_demoted",
    }

    def _flat(self, d, prefix=""):
        out = {}
        for k, v in d.items():
            key = f"{prefix}{k}"
            if isinstance(v, dict) and key != "alerts":
                out.update(self._flat(v, key + "."))
            else:
                out[key] = json.dumps(v, sort_keys=True)
        return out

    def test_bl_is_otherwise_an_EXACT_copy_of_b(self):
        """'It's literally a copy-paste with different times.' Prove it — any
        other divergence is an accident, not a decision."""
        fb, fl = self._flat(_cfg("b")), self._flat(_cfg("bl"))
        diff = {k for k in set(fb) | set(fl) if fb.get(k) != fl.get(k)}
        unexpected = diff - self.ALLOWED
        assert not unexpected, f"unexpected divergence from b: {sorted(unexpected)}"

    def test_the_things_that_must_match_do(self):
        b, l = _cfg("b")["strategy"], _cfg("bl")["strategy"]
        for k in ("contracts_per_entry", "narrow_spread_stop", "brandon",
                  "one_sided_entries_enabled", "underlying_symbol", "trading_class"):
            if k in b or k in l:
                assert b.get(k) == l.get(k), k


class TestTheRecordIsNotOrphaned:

    def test_b_keeps_its_id_so_its_history_survives(self):
        """Renaming the id would orphan data/variant_b, its DB, metrics file,
        journal history and the agents' read_db pointer."""
        assert "b" in tax.STRATEGIES
        assert tax.STRATEGIES["b"].id == "b"

    def test_b_is_displayed_as_BA_and_bl_as_B(self):
        assert tax.STRATEGIES["b"].display_name == "B-A"
        assert tax.STRATEGIES["bl"].display_name == "B"

    def test_both_are_in_the_same_comparison_group(self):
        """They must stay comparable — same structure, same P&L shape — or the
        head-to-head that justifies the swap cannot be rendered."""
        b, l = tax.STRATEGIES["b"], tax.STRATEGIES["bl"]
        assert b.group_id == l.group_id == "ic_0dte"
        assert b.pnl_shape == l.pnl_shape
        assert b.structure_family == l.structure_family
