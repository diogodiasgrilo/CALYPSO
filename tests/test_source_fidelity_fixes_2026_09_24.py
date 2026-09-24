"""The fidelity fixes the operator approved on 2026-09-24, pinned behaviourally.

Four decisions, each traced to a quoted source rule:

* **E's low-IV gate** — OptionsKit states a RELATIVE condition, *"enter when the
  implied volatility is at the lower end of the spectrum"*. E gated on an
  ABSOLUTE ``VIX <= 22``, which means something different every season: VIX 18 is
  roughly the 90th percentile of a calm year and the 20th of a volatile one.
* **D stays ungated** — Burnich's video specifies no IV condition at all, so
  adding one would MAKE D unfaithful. "Exactly like their videos" means the two
  calendars legitimately differ here.
* **F's delta band** — Ghauri specifies a 10–25Δ short strike; ``[0.05, 0.35]``
  admitted strikes he would not take.
* **H's IV window** — the config requested 252 days and the DB held ~94.

Every test calls the code. See the memory ``feedback_source_grep_tests_are_blind``
for why that distinction is not cosmetic.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bots.hydra.iv_percentile import (  # noqa: E402
    iv_percentile_with_n,
    vix_history_from_db,
)

CFG = ROOT / "bots" / "hydra" / "config"
E = json.loads((CFG / "config_variant_e.json").read_text())["strategy"]
D = json.loads((CFG / "config_variant_d.json").read_text())["strategy"]
F = json.loads((CFG / "config_variant_f.json").read_text())["strategy"]
H = json.loads((CFG / "config_variant_h.json").read_text())["strategy"]


class TestOneDefinitionSharedByEveryVariant:
    """H and E ask the same question in the same words. Two implementations
    would be free to drift on arithmetic rather than on config — the rule
    already applied to the expected move shared between H and F."""

    def test_H_still_imports_it_from_its_own_module(self):
        """H's public API is unchanged by the extraction."""
        from bots.hydra.long_strangle_chain import iv_percentile, iv_percentile_with_n as w
        from bots.hydra import iv_percentile as shared
        assert iv_percentile is shared.iv_percentile
        assert w is shared.iv_percentile_with_n

    def test_E_uses_the_same_object(self):
        import bots.hydra.spy_double_calendar_strategy as e
        from bots.hydra import iv_percentile as shared
        assert e.iv_percentile_with_n is shared.iv_percentile_with_n


class TestEGatesOnAPercentileNotAnAbsoluteLevel:

    def _strategy(self, history, vix, **over):
        from bots.hydra.spy_double_calendar_strategy import SpyDoubleCalendarStrategy
        s = SpyDoubleCalendarStrategy.__new__(SpyDoubleCalendarStrategy)
        s.spy_dc_iv_gate_mode = over.get("mode", "percentile")
        s.spy_dc_iv_pct_max = over.get("pct_max", 35.0)
        s.spy_dc_iv_lookback = over.get("lookback", 252)
        s.spy_dc_iv_min_history = over.get("floor", 60)
        s.spy_dc_max_vix_entry = over.get("abs_max", 22.0)
        s.current_vix = vix
        s._spy_dc_vix_history = lambda: history
        return s

    #: A year whose median sits near 20 — so VIX 18 is CHEAP here.
    VOLATILE_YEAR = [14.0 + (i % 23) for i in range(252)]
    #: A year whose whole range is 12–16 — so VIX 18 is EXPENSIVE here.
    CALM_YEAR = [12.0 + (i % 5) * 1.0 for i in range(252)]

    def test_the_same_VIX_is_cheap_in_one_regime_and_expensive_in_another(self):
        """The defect, stated as the thing an absolute cutoff cannot express.
        VIX 18 passes in a volatile year and is vetoed in a calm one — and the
        OLD absolute gate would have passed it in BOTH."""
        assert self._strategy(self.VOLATILE_YEAR, 18.0)._spy_dc_low_iv_gate() is None
        assert self._strategy(self.CALM_YEAR, 18.0)._spy_dc_low_iv_gate() is not None
        # The negative control: the absolute gate cannot tell them apart.
        for hist in (self.VOLATILE_YEAR, self.CALM_YEAR):
            assert self._strategy(hist, 18.0, mode="absolute")._spy_dc_low_iv_gate() is None

    def test_genuinely_cheap_vol_passes(self):
        assert self._strategy(self.CALM_YEAR, 12.0)._spy_dc_low_iv_gate() is None

    def test_expensive_vol_is_vetoed_and_the_reason_names_the_source_rule(self):
        r = self._strategy(self.CALM_YEAR, 30.0)._spy_dc_low_iv_gate()
        assert "LOWER END" in r and "NOT an option-IV percentile" in r

    def test_it_FAILS_CLOSED_on_an_underpowered_sample(self):
        """Same rule as H: an unknown percentile is a skip, never a pass. A gate
        that admits everything when it cannot measure is indistinguishable from
        a working one."""
        r = self._strategy([14.0] * 5, 12.0)._spy_dc_low_iv_gate()
        assert r is not None and "below the 60-day minimum" in r

    def test_an_unknown_VIX_is_not_a_veto(self):
        assert self._strategy(self.CALM_YEAR, 0.0)._spy_dc_low_iv_gate() is None

    def test_the_absolute_gate_remains_reachable_for_an_A_B(self):
        s = self._strategy(self.CALM_YEAR, 25.0, mode="absolute")
        assert "low-IV gate" in s._spy_dc_low_iv_gate()

    def test_the_shipped_config_selects_the_percentile(self):
        e = E["spy_double_calendar"]
        assert e["iv_gate_mode"] == "percentile"
        assert e["iv_percentile_min_history_days"] >= 30

    def test_the_provenance_of_our_invented_knobs_is_recorded(self):
        raw = (CFG / "config_variant_e.json").read_text()
        assert "_comment_em_fraction_provenance" in raw
        assert "OURS, NOT THE VIDEO'S" in raw

    def test_the_marketing_claim_is_no_longer_stated_as_a_property(self):
        """E's docstring quoted the coaching program's '>80% win rate' as if it
        were measured. It may now appear ONLY as a quote being debunked."""
        src = (ROOT / "bots" / "hydra" / "spy_double_calendar_strategy.py").read_text()
        head = src[:src.index('"""', 3)]
        assert "coaching program" in head, "the withholding must be stated"
        assert "sales claim" in head or "never verified" in head


class TestDStaysUngatedBecauseItsSourceSaysNothing:
    """The counter-intuitive half of "make both exactly like their videos"."""

    def test_D_has_no_low_IV_gate(self):
        dc = D["double_calendar"]
        assert "iv_gate_mode" not in dc
        assert "iv_percentile_max" not in dc

    def test_D_does_not_import_the_percentile_machinery(self):
        src = (ROOT / "bots" / "hydra" / "double_calendar_strategy.py").read_text()
        assert "iv_percentile" not in src

    def test_the_asymmetry_is_documented_rather_than_accidental(self):
        audit = (ROOT / "docs" / "SOURCE_FIDELITY_AUDIT_2026_09_24.md").read_text()
        assert "no low-IV gate" in audit or "ungated" in audit


class TestFStaysInsideTheStatedDeltaRange:

    def test_the_band_matches_the_sources_10_to_25_delta(self):
        assert F["ghauri"]["delta_band"] == [0.10, 0.25]

    def test_the_target_sits_inside_its_own_band(self):
        lo, hi = F["ghauri"]["delta_band"]
        assert lo <= F["ghauri"]["target_delta_pct"] <= hi

    def test_the_tightening_records_what_it_costs(self):
        raw = (CFG / "config_variant_f.json").read_text()
        assert "_comment_delta_band" in raw and "fewer entries" in raw


class TestTheVixBackfillMakesTheWindowReal:

    def _db(self, tmp_path, ticks=(), daily=()):
        p = tmp_path / "bt.db"
        con = sqlite3.connect(p)
        with con:
            con.execute("CREATE TABLE market_ticks (timestamp TEXT, vix_level REAL)")
            con.executemany("INSERT INTO market_ticks VALUES (?,?)", ticks)
            if daily:
                con.execute("CREATE TABLE vix_daily (date TEXT PRIMARY KEY, "
                            "close REAL, source TEXT)")
                con.executemany("INSERT INTO vix_daily VALUES (?,?,'yahoo')", daily)
        con.close()
        return str(p)

    def test_without_the_backfill_only_the_bots_own_days_exist(self):
        """The situation being fixed: a 252-day request served ~94 days."""
        import datetime as dt
        ticks = [((dt.date(2026, 5, 1) + dt.timedelta(days=i)).isoformat()
                  + " 15:59:00", 15.0) for i in range(94)]
        db = self._db(Path(self._tmp), ticks=ticks)
        assert len(vix_history_from_db(db, "2026-09-24", 252)) == 94

    def test_the_backfill_supplies_the_full_window(self):
        import datetime as dt
        daily = [((dt.date(2025, 1, 2) + dt.timedelta(days=i)).isoformat(), 16.0)
                 for i in range(300)]
        db = self._db(Path(self._tmp), daily=daily)
        assert len(vix_history_from_db(db, "2026-09-24", 252)) == 252

    def test_the_two_sources_union_rather_than_replace(self):
        """A session the backfill has not caught up to must not be dropped."""
        db = self._db(Path(self._tmp),
                      ticks=[("2026-09-23 15:59:00", 14.5)],
                      daily=[("2026-09-20", 16.0)])
        assert sorted(vix_history_from_db(db, "2026-09-24", 252)) == [14.5, 16.0]

    def test_the_official_close_wins_on_a_shared_date(self):
        """vix_daily is the official close; market_ticks is whatever tick landed
        last. On a collision the authoritative one is used."""
        db = self._db(Path(self._tmp),
                      ticks=[("2026-09-20 15:59:00", 99.0)],
                      daily=[("2026-09-20", 16.0)])
        assert vix_history_from_db(db, "2026-09-24", 252) == [16.0]

    def test_a_database_with_no_vix_daily_still_works(self):
        """The table does not exist until the backfill runs. That must degrade,
        not raise — this is an entry path."""
        db = self._db(Path(self._tmp), ticks=[("2026-09-20 15:59:00", 14.0)])
        assert vix_history_from_db(db, "2026-09-24", 252) == [14.0]

    def test_today_is_excluded_so_the_reading_is_not_ranked_against_itself(self):
        db = self._db(Path(self._tmp),
                      daily=[("2026-09-23", 15.0), ("2026-09-24", 99.0)])
        assert vix_history_from_db(db, "2026-09-24", 252) == [15.0]

    def test_a_full_year_and_a_short_window_can_disagree_about_the_same_VIX(self):
        """Why the backfill is worth doing at all."""
        calm_94 = [14.0 + (i % 6) * 0.3 for i in range(94)]
        full_252 = calm_94 + [20.0 + (i % 11) for i in range(158)]
        short_pct, _ = iv_percentile_with_n(16.0, calm_94, min_history=60)
        full_pct, _ = iv_percentile_with_n(16.0, full_252, min_history=60)
        assert short_pct > full_pct, (
            "a quiet 94-day window makes ordinary vol look expensive")

    def test_H_requests_a_full_year(self):
        assert H["long_strangle"]["iv_percentile_lookback_days"] == 252

    @pytest.fixture(autouse=True)
    def _tmpdir(self, tmp_path):
        self._tmp = str(tmp_path)


class TestEActuallyREADSTheGateModeFromConfig:
    """The hole this file shipped with, and the reason it is worth its own class.

    The percentile tests above build E with ``__new__`` and set
    ``spy_dc_iv_gate_mode`` by hand — so they verify the GATE LOGIC and nothing
    about whether the strategy ever consults the config. Reverting the
    constructor to a hardcoded ``"absolute"`` left all 25 of them green.

    A test that sets the input it claims to test is not testing the wiring.
    These construct through the real ``__init__`` config-reading path.
    """

    def _build(self, monkeypatch, cfg):
        from bots.hydra.spy_double_calendar_strategy import SpyDoubleCalendarStrategy
        from bots.hydra.strategy import HydraStrategy

        def fake_init(self, *a, **k):
            self.dry_run = k.get("dry_run", True)
            # The real base populates strategy_config from the passed config;
            # a stub that leaves it EMPTY makes every config-wiring assertion
            # vacuous, which is the exact hole this class exists to close.
            self.strategy_config = (a[1] if len(a) > 1 else {}).get("strategy", {})
            self.state_file = "/tmp/spydc_fidelity/hydra_state.json"
        monkeypatch.setattr(HydraStrategy, "__init__", fake_init)
        return SpyDoubleCalendarStrategy(None, cfg, None, dry_run=True)

    def test_the_mode_comes_FROM_the_config(self, monkeypatch):
        s = self._build(monkeypatch, {"strategy": {"spy_double_calendar": {
            "iv_gate_mode": "absolute"}}})
        assert s.spy_dc_iv_gate_mode == "absolute", (
            "the constructor is ignoring the config and hardcoding a mode")

    def test_the_DEFAULT_is_the_percentile(self, monkeypatch):
        """An operator who writes no gate key must get the source-faithful
        behaviour, not the old absolute cutoff."""
        s = self._build(monkeypatch, {"strategy": {"spy_double_calendar": {}}})
        assert s.spy_dc_iv_gate_mode == "percentile"

    def test_the_thresholds_come_from_config_too(self, monkeypatch):
        s = self._build(monkeypatch, {"strategy": {"spy_double_calendar": {
            "iv_percentile_max": 20.0,
            "iv_percentile_min_history_days": 90,
            "iv_percentile_lookback_days": 180}}})
        assert (s.spy_dc_iv_pct_max, s.spy_dc_iv_min_history,
                s.spy_dc_iv_lookback) == (20.0, 90, 180)

    def test_the_SHIPPED_config_builds_a_percentile_gate(self, monkeypatch):
        """End to end on the real file, not a synthetic dict."""
        cfg = json.loads((CFG / "config_variant_e.json").read_text())
        s = self._build(monkeypatch, cfg)
        assert s.spy_dc_iv_gate_mode == "percentile"
        assert s.spy_dc_iv_pct_max == 35.0
