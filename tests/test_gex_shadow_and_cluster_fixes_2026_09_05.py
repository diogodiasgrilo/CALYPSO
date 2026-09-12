"""2026-09-05: GEX cluster-detection corrections + the shadow gate.

Follows the 2026-09-04 gate audit, which found the accel-zone gate had five
defects, three of which change which entries get placed and so must be
shadowed rather than flipped on a live seat:

  BUG 1 (width)         - a ONE-strike run qualified as a "gamma wall".
  BUG 2 (normalization) - strength scored against the WHOLE chain's |GEX|,
                          which on a 0DTE book made a genuine 345pt wall
                          score 9.25% (fail) while one ATM strike scored
                          16.94% (pass).
  BUG 3 (sign)          - this codebase's dealer assumption is the inverse
                          of published SpotGamma, putting ~all accel zones
                          above spot and leaving the put branch near-blind
                          (0 confirmations in 843 in-25pt watch ticks).
  BUG 4 (predicates)    - the adjuster asks "cluster CONTAINS the short",
                          the overlay asks "cluster entirely BEYOND spot".

These tests pin the new primitives and the shadow scoring.

UPDATED 2026-09-06 — the three corrections did NOT all stay shadowed:
  * BUG 1 (width floor) SHIPPED LIVE as gex_provider.MIN_CLUSTER_STRIKES = 2.
    It went live rather than into the shadow because it was MEASURED inert for
    entry selection: across all 44 real BRANDON-GEX-ADJ SKIPs in the log
    window, the narrowest cited zone was 30pt = 7 strikes (rest 75-405pt), so
    no recorded veto changes. Shadowing a provably-inert change is theatre.
  * BUG 2 (normalization) stays SHADOWED — it genuinely moves which entries
    get vetoed and there is no evidence yet that it moves them for the better.
  * BUG 3 (sign) stays SHADOWED, and the 2026-09-06 research verdict is DO NOT
    FLIP — the put-side-blindness argument for it was refuted (ungated
    variants A and G show the same put-lean, so that lean is a property of
    selling 0DTE index premium, not of this gate).
The shadow's `width_floor` arm was therefore INVERTED into `legacy_no_floor`,
which reproduces the old single-strike-permitting behavior so we keep
measuring whether the now-live floor ever actually bites.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from bots.hydra.brandon.gex_provider import GEXProfile, StrikeGEX
from bots.hydra.brandon import gex_shadow
from bots.hydra.brandon.gex_shadow import ShadowVerdict, evaluate_shadow, disagreement_summary


def _profile(pairs, spot=7718.0):
    """pairs: [(strike, gex), ...]"""
    return GEXProfile(
        spot=spot,
        expiry=date(2026, 9, 4),
        fetched_at=datetime(2026, 9, 4, 12, 52),
        strikes=tuple(StrikeGEX(strike=float(k), gex=float(g)) for k, g in pairs),
    )


def _real_0904_shape():
    """Reproduces the SHAPE of the live 2026-09-04 profile that the audit
    decomposed, calibrated so the two percentages that mattered come out
    right against a WHOLE-CHAIN denominator:

        NEG[7715-7715]   1 strike,  ~17%  -> PASSES the 0.10 gate  (artifact)
        NEG[7755-8100]  70 strikes, ~9%   -> FAILS  the 0.10 gate  (real wall)

    That inversion -- one ATM strike outscoring a 345pt wall -- is BUG 2.
    Magnitudes are scaled; the structure and the pass/fail split are what
    the tests depend on."""
    pairs = []
    for k in range(7335, 7715, 5):        # 76 strikes, positive, below spot
        pairs.append((k, 1000.0))
    pairs.append((7715, -24000.0))        # ONE strike, strongly negative (the artifact)
    for k in range(7720, 7755, 5):        # 7 strikes, positive
        pairs.append((k, 4000.0))
    for k in range(7755, 8105, 5):        # 70 strikes, negative but diffuse (real wall)
        pairs.append((k, -180.0))
    return _profile(pairs, spot=7718.34)


# ───────────── what the LIVE default does (width floor shipped 2026-09-06) ─────────────
class TestLiveDefaults:
    """The width floor SHIPPED as the live default on 2026-09-06, one day after
    landing as an opt-in parameter. It went live rather than into the shadow
    because it was measured INERT for entry selection: across all 44 real
    BRANDON-GEX-ADJ SKIPs in the log-retention window, the narrowest cited zone
    was 30pt = 7 strikes (the rest 75-405pt), so no recorded veto changes.
    The windowed normalization did NOT ship — it genuinely moves selection and
    stays shadowed."""

    def test_single_strike_cluster_is_rejected_by_default(self):
        """The 2026-09-04 live profile's NEG[7715-7715] artifact (16.94%, the
        single strongest negative cluster on the board) must no longer qualify."""
        p = _real_0904_shape()
        zones = p.negative_clusters(min_strength_pct=0.10)
        assert not any(c.n_strikes == 1 for c in zones)

    def test_the_live_default_constant_is_two(self):
        from bots.hydra.brandon.gex_provider import MIN_CLUSTER_STRIKES
        assert MIN_CLUSTER_STRIKES == 2

    def test_whole_chain_normalization_is_STILL_the_default(self):
        """The normalization fix deliberately did NOT ship — it changes which
        entries get vetoed and has no supporting evidence yet."""
        p = _real_0904_shape()
        zones = p.negative_clusters(min_strength_pct=0.10)
        # The diffuse 70-strike tail still fails against a whole-chain basis.
        assert not any(c.n_strikes > 10 for c in zones)

    def test_new_fields_are_populated(self):
        p = _real_0904_shape()
        for c in p.negative_clusters(min_strength_pct=0.05):
            assert c.n_strikes >= 2          # the floor now guarantees this
            assert 0.0 <= c.strength_pct <= 1.0
            assert c.width_pts == c.strike_high - c.strike_low


# ───────────────────────── BUG 1: width floor ─────────────────────────
class TestWidthFloor:
    def test_explicit_floor_of_one_restores_the_legacy_artifact(self):
        """min_cluster_strikes=1 is the pre-2026-09-06 behavior, and is what
        the shadow's `legacy_no_floor` arm uses to keep measuring whether the
        now-live floor ever actually bites."""
        p = _real_0904_shape()
        legacy = p.negative_clusters(min_strength_pct=0.10, min_cluster_strikes=1)
        live = p.negative_clusters(min_strength_pct=0.10)
        assert any(c.n_strikes == 1 for c in legacy)
        assert not any(c.n_strikes == 1 for c in live)
        assert legacy != live   # the arm is not a no-op on this profile

    def test_width_floor_does_not_touch_genuinely_wide_clusters(self):
        """The inertness property that justified shipping it live: a real
        multi-strike wall is unaffected."""
        p = _profile([(7700, -100.0), (7705, -100.0), (7710, -100.0), (7800, 50.0)])
        z1 = p.negative_clusters(min_strength_pct=0.05, min_cluster_strikes=1)
        z2 = p.negative_clusters(min_strength_pct=0.05)
        assert z1 == z2

    def test_inert_on_the_narrowest_zone_any_real_veto_ever_cited(self):
        """Regression pin for the measurement that justified going live: the
        narrowest real SKIP zone was 30pt = 7 contiguous strikes. The floor
        must leave a cluster that size completely untouched."""
        pairs = [(k, -100.0) for k in range(7700, 7735, 5)]  # 7 strikes, 30pt
        pairs.append((7800, 20.0))
        p = _profile(pairs, spot=7690.0)
        z = p.negative_clusters(min_strength_pct=0.05)
        assert len(z) == 1 and z[0].n_strikes == 7
        assert z[0].strike_high - z[0].strike_low == 30


# ───────────────────────── BUG 2: windowed normalization ─────────────────────────
class TestWindowedNormalization:
    def test_windowing_raises_local_cluster_scores(self):
        """The whole point: scoring against near-money gamma instead of the
        entire chain lets a real near-spot wall clear a threshold it failed
        against the full-chain denominator."""
        p = _real_0904_shape()
        wide = {(c.strike_low, c.strike_high) for c in p.negative_clusters(min_strength_pct=0.10)}
        narrow = {(c.strike_low, c.strike_high)
                  for c in p.negative_clusters(min_strength_pct=0.10,
                                               normalization_window_pts=100.0)}
        # Windowing must change the answer on this profile shape, otherwise
        # the correction would be inert and the shadow pointless.
        assert wide != narrow

    def test_degenerate_window_falls_back_to_whole_chain_not_divide_by_noise(self):
        """A window containing <3 strikes must NOT become a near-zero
        denominator that passes everything -- that would be a worse bug than
        the one being fixed."""
        p = _profile([(7000, -100.0), (7005, -100.0), (8000, 50.0)], spot=7500.0)
        tiny = p.negative_clusters(min_strength_pct=0.10, normalization_window_pts=1.0)
        whole = p.negative_clusters(min_strength_pct=0.10)
        assert tiny == whole

    def test_none_window_is_the_legacy_path(self):
        p = _real_0904_shape()
        assert (p.negative_clusters(min_strength_pct=0.10)
                == p.negative_clusters(min_strength_pct=0.10, normalization_window_pts=None))


# ───────────────────────── BUG 3: sign convention flip ─────────────────────────
class TestFlippedSignConvention:
    def test_flip_negates_every_strike(self):
        p = _profile([(7700, -100.0), (7750, 250.0)])
        f = p.with_flipped_sign_convention()
        assert [sg.gex for sg in f.strikes] == [100.0, -250.0]

    def test_flip_is_an_involution(self):
        p = _real_0904_shape()
        assert p.with_flipped_sign_convention().with_flipped_sign_convention().strikes == p.strikes

    def test_flip_preserves_non_strike_fields(self):
        p = _real_0904_shape()
        f = p.with_flipped_sign_convention()
        assert (f.spot, f.expiry, f.fetched_at) == (p.spot, p.expiry, p.fetched_at)

    def test_flip_moves_accel_zones_to_the_other_hemisphere(self):
        """The audit's core structural finding, made executable: under the
        live convention accel zones sit ABOVE spot (blinding the put side);
        flipped, they sit BELOW it."""
        p = _real_0904_shape()
        live_neg = p.negative_clusters(min_strength_pct=0.05)
        flip_neg = p.with_flipped_sign_convention().negative_clusters(min_strength_pct=0.05)
        live_above = sum(1 for c in live_neg if c.strike_low > p.spot)
        flip_below = sum(1 for c in flip_neg if c.strike_high < p.spot)
        assert live_above >= 1
        assert flip_below >= 1


# ───────────────────────── shadow scoring ─────────────────────────
class TestEvaluateShadow:
    def _args(self, **over):
        base = dict(
            spot=7718.34, threatened_side="call", reference_strike=7740.0,
            min_strength_pct=0.10, peak_locality_pts=25.0,
        )
        base.update(over)
        return base

    def test_returns_a_verdict_per_variant(self):
        v = evaluate_shadow(_real_0904_shape(), **self._args())
        assert tuple(x.variant for x in v) == gex_shadow.SHADOW_VARIANTS

    def test_live_variant_matches_an_unshadowed_read(self):
        """The `live` shadow row must reproduce the real gate, else every
        disagreement it reports is against a strawman."""
        p = _real_0904_shape()
        v = {x.variant: x for x in evaluate_shadow(p, **self._args())}["live"]
        zones = p.negative_clusters(min_strength_pct=0.10)
        expected_adj = any(
            c.strike_low <= 7740.0 <= c.strike_high and abs(7740.0 - c.peak_strike) <= 25.0
            for c in zones
        )
        assert v.adjuster_predicate is expected_adj
        assert v.n_zones == len(zones)

    def test_reports_both_predicates_separately(self):
        v = evaluate_shadow(_real_0904_shape(), **self._args())
        for x in v:
            assert isinstance(x.adjuster_predicate, bool)
            assert isinstance(x.overlay_predicate, bool)

    def test_put_side_uses_below_spot_for_the_overlay_predicate(self):
        p = _profile([(7600, -500.0), (7605, -500.0), (7900, 100.0)], spot=7700.0)
        v = {x.variant: x for x in evaluate_shadow(
            p, **self._args(threatened_side="put", reference_strike=7620.0))}
        assert v["live"].overlay_predicate is True  # cluster entirely below spot

    def test_never_raises_on_a_degenerate_profile(self):
        empty = GEXProfile(spot=7700.0, expiry=date(2026, 9, 4),
                           fetched_at=datetime(2026, 9, 4, 12, 0))
        assert evaluate_shadow(empty, **self._args()) == ()
        assert evaluate_shadow(None, **self._args()) == ()

    def test_shadow_is_read_only_leaves_the_profile_untouched(self):
        p = _real_0904_shape()
        before = tuple(p.strikes)
        evaluate_shadow(p, **self._args())
        assert p.strikes == before


class TestDisagreementSummary:
    def test_empty_when_every_variant_agrees(self):
        agree = (
            ShadowVerdict("live", True, True, None, 1),
            ShadowVerdict("legacy_no_floor", True, True, None, 1),
        )
        assert disagreement_summary(agree) == ""

    def test_renders_only_the_disagreeing_variants(self):
        v = (
            ShadowVerdict("live", True, True, None, 1),
            ShadowVerdict("legacy_no_floor", False, True, None, 0),   # differs
            ShadowVerdict("windowed", True, True, None, 1),       # agrees
        )
        s = disagreement_summary(v)
        assert "live:" in s and "legacy_no_floor:" in s
        assert "windowed:" not in s

    def test_empty_on_no_verdicts(self):
        assert disagreement_summary(()) == ""

    def test_real_0904_shape_produces_a_disagreement(self):
        """End-to-end sanity on the actual incident shape: the corrections
        must visibly disagree with live here, or the shadow would log
        nothing and teach us nothing."""
        v = evaluate_shadow(
            _real_0904_shape(), spot=7718.34, threatened_side="call",
            reference_strike=7715.0, min_strength_pct=0.10, peak_locality_pts=25.0,
        )
        assert disagreement_summary(v) != ""


# ───────────────────────── v16 recorder + strategy wiring ─────────────────────────
class TestGexTelemetryTables:
    def test_schema_creates_both_tables_and_stamps_v16(self, tmp_path):
        from shared.data_recorder import DataRecorder, SCHEMA_VERSION
        import sqlite3
        rec = DataRecorder(str(tmp_path / "bt.db"))
        assert rec.ensure_schema() is True
        assert SCHEMA_VERSION >= 16
        con = sqlite3.connect(rec.db_path)
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
        assert {"gex_profile_snapshots", "gex_decisions"} <= names

    def test_tables_are_created_on_a_preexisting_v15_db(self, tmp_path):
        """Upgrade path: these are unconditional CREATE IF NOT EXISTS, not
        version-gated ALTERs, so an existing DB must gain them."""
        import sqlite3
        db = tmp_path / "old.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE schema_info (key TEXT PRIMARY KEY, value TEXT)")
        con.execute("INSERT INTO schema_info VALUES ('version', '15')")
        con.commit(); con.close()
        from shared.data_recorder import DataRecorder
        assert DataRecorder(str(db)).ensure_schema() is True
        con = sqlite3.connect(db)
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
        assert {"gex_profile_snapshots", "gex_decisions"} <= names

    def test_decisions_are_NOT_deduplicated(self, tmp_path):
        """A variant legitimately re-evaluates the same entry+side many times
        a session, and the repeated-evaluation instability is exactly what the
        razor-edge finding is about — dedup would discard the signal."""
        from shared.data_recorder import DataRecorder
        import sqlite3
        rec = DataRecorder(str(tmp_path / "bt.db")); rec.ensure_schema()
        row = {"timestamp": "2026-09-05 10:00:00", "date": "2026-09-05",
               "variant": "b", "consumer": "overlay", "side": "call"}
        rec.record_gex_decision(dict(row))
        rec.record_gex_decision(dict(row))
        con = sqlite3.connect(rec.db_path)
        n = con.execute("SELECT COUNT(*) FROM gex_decisions").fetchone()[0]
        con.close()
        assert n == 2

    def test_snapshots_ARE_deduplicated_per_variant(self, tmp_path):
        """B and C share a fetch under the cross-process lock and both record
        it (distinct variants, both kept); the same variant re-recording the
        same refresh is a true duplicate and must be ignored."""
        from shared.data_recorder import DataRecorder
        import sqlite3
        rec = DataRecorder(str(tmp_path / "bt.db")); rec.ensure_schema()
        base = {"timestamp": "2026-09-05 10:00:00", "date": "2026-09-05", "spot": 7700.0}
        rec.record_gex_snapshot({**base, "variant": "b"})
        rec.record_gex_snapshot({**base, "variant": "b"})   # dup -> ignored
        rec.record_gex_snapshot({**base, "variant": "c"})   # sibling -> kept
        con = sqlite3.connect(rec.db_path)
        n = con.execute("SELECT COUNT(*) FROM gex_profile_snapshots").fetchone()[0]
        con.close()
        assert n == 2


class TestTelemetryIsNonFatal:
    """The single most important property of all this instrumentation: it is
    pure observation bolted onto a live trading path, so ANY failure in it
    must be swallowed. A telemetry bug must never be able to abort an entry
    or a hedge decision."""

    def _strat(self):
        from bots.hydra.brandon.strategy import BrandonHydraStrategy
        s = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
        s.brandon_accel_min_pct = 0.10
        s.brandon_decel_min_pct = 0.05
        s.brandon_accel_peak_locality_pts = 25.0
        s.brandon_gex_max_contracts_to_hydrate = 250
        return s

    def test_decision_record_survives_a_raising_recorder(self):
        from unittest.mock import MagicMock
        s = self._strat()
        s._data_recorder = MagicMock()
        s._data_recorder.record_gex_decision.side_effect = RuntimeError("db exploded")
        s._brandon_record_gex_decision(
            consumer="adjuster", entry_number=1, side="call", spot=7700.0,
            reference_strike=7740.0, live_action="KEEP",
            live_adjuster_predicate=False, live_overlay_predicate=False,
            profile=_real_0904_shape(),
        )  # must not raise

    def test_snapshot_record_survives_a_raising_recorder(self):
        from unittest.mock import MagicMock
        s = self._strat()
        s._data_recorder = MagicMock()
        s._data_recorder.record_gex_snapshot.side_effect = RuntimeError("db exploded")
        s._brandon_record_gex_snapshot(_real_0904_shape(), 200)  # must not raise

    def test_both_are_noops_without_a_recorder(self):
        s = self._strat()
        s._data_recorder = None
        s._brandon_record_gex_decision(
            consumer="overlay", entry_number=1, side="put", spot=7700.0,
            reference_strike=7620.0, live_action="False",
            live_adjuster_predicate=False, live_overlay_predicate=False,
            profile=_real_0904_shape(),
        )
        s._brandon_record_gex_snapshot(_real_0904_shape(), 200)

    def test_decision_record_writes_both_predicates_and_shadow_json(self):
        import json
        from unittest.mock import MagicMock
        s = self._strat()
        s._data_recorder = MagicMock()
        s._brandon_record_gex_decision(
            consumer="adjuster", entry_number=3, side="call", spot=7718.34,
            reference_strike=7715.0, live_action="SKIP",
            live_adjuster_predicate=True, live_overlay_predicate=False,
            profile=_real_0904_shape(),
        )
        payload = s._data_recorder.record_gex_decision.call_args[0][0]
        assert payload["consumer"] == "adjuster"
        assert payload["live_adjuster_predicate"] == 1
        assert payload["live_overlay_predicate"] == 0
        variants = [v["variant"] for v in json.loads(payload["shadow_json"])]
        assert variants == list(gex_shadow.SHADOW_VARIANTS)


class TestCallSitesAreWired:
    """Source-level wiring pins, matching this codebase's established pattern
    for regressions that are invisible at runtime until a real trading day
    (see TestMainPyUsesTheHook, test_calendar_carry_and_activity_gate)."""

    def _src(self):
        import inspect
        import bots.hydra.brandon.strategy as m
        return inspect.getsource(m)

    def test_adjuster_records_both_sides(self):
        src = self._src()
        assert src.count('consumer="adjuster"') >= 2  # call side + put side

    def test_overlay_watch_records(self):
        assert 'consumer="overlay"' in self._src()

    def test_profile_refresh_records_a_snapshot(self):
        assert "_brandon_record_gex_snapshot(profile, candidates_found)" in self._src()
