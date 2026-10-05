"""The check that looks at whether we are still collecting what we rely on.

Two design decisions are load-bearing and both are pinned here, because the
first version of this script got both wrong:

1. CURATED, NOT A SCANNER. Scanning every column for all-NULL/single-valued
   produced 118 findings, essentially all false (`contracts` is legitimately
   always 7 on B; `long_call_strike` is legitimately 0.0 on G, a naked strangle
   with no long leg). A report nobody reads is the exact failure this exists to
   catch.

2. JUDGED ON THE LATEST DAY, NOT A WINDOW PERCENTAGE. A percentage conflates
   "broken now" with "was broken, since fixed". Measured: `expected_move` was
   NULL on every row before 2026-09-30 and populated on every row after, so a
   14-day percentage reported 62% NULL for a bug that no longer existed, while
   variant G's `delta_call` — genuinely NULL that day — looked milder at 23%.
"""
import json
import sqlite3

import pytest

import scripts.audit_telemetry_health as th


def _mkvariant(root, vid, rows, dry_run=True, epoch=None):
    """rows = [(date, expected_move, delta_call)]"""
    sub = f"variant_{vid}" if vid else ""
    (root / "data" / sub).mkdir(parents=True, exist_ok=True)
    (root / "bots" / "hydra" / "config").mkdir(parents=True, exist_ok=True)
    cfg = {"dry_run": dry_run, "strategy": {}}
    if epoch:
        cfg["strategy"]["metrics_epoch_date"] = epoch
    name = "config.json" if not vid else f"config_variant_{vid}.json"
    (root / "bots" / "hydra" / "config" / name).write_text(json.dumps(cfg))

    db = root / "data" / sub / "backtesting.db"
    c = sqlite3.connect(str(db))
    c.execute("CREATE TABLE trade_entries (date TEXT, expected_move REAL, "
              "delta_call REAL, otm_distance_call REAL, short_call_fill_price REAL)")
    c.execute("CREATE TABLE trade_stops (date TEXT, quoted_mid_at_stop REAL, "
              "slippage_on_close REAL)")
    c.execute("CREATE TABLE daily_summaries (date TEXT, day_type TEXT, "
              "realized_volatility REAL, net_pnl REAL)")
    for d, em, dc in rows:
        c.execute("INSERT INTO trade_entries VALUES (?,?,?,?,?)", (d, em, dc, 1.0, None))
    c.commit(); c.close()
    return db


def _today_minus(n):
    import datetime
    return (datetime.date.today() - datetime.timedelta(days=n)).isoformat()


def test_a_dead_instrument_on_the_latest_day_is_flagged(tmp_path):
    """G's real 2026-10-05 shape: delta_call NULL on every row of the latest day."""
    _mkvariant(tmp_path, "g", [
        (_today_minus(5), 80.0, 0.08),
        (_today_minus(4), 80.0, 0.08),
        (_today_minus(3), 80.0, 0.08),
        (_today_minus(2), 80.0, 0.08),
        (_today_minus(1), 80.0, None),
        (_today_minus(1), 80.0, None),
    ])
    found = th.audit_variant(tmp_path, "g", 14)
    kinds = {k: w for k, w, _ in found}
    assert "DEAD_NOW" in kinds, found
    assert kinds["DEAD_NOW"] == "trade_entries.delta_call"


def test_a_FIXED_instrument_is_NOT_flagged_as_current(tmp_path):
    """THE false-positive this design exists to avoid.

    `expected_move`: NULL before the fix, populated after. A window percentage
    would report ~60% NULL; the latest day is clean, so it must read HEALED.
    """
    _mkvariant(tmp_path, "b", [
        (_today_minus(6), None, 0.08),
        (_today_minus(5), None, 0.08),
        (_today_minus(4), None, 0.08),
        (_today_minus(3), None, 0.08),
        (_today_minus(2), 79.8, 0.08),
        (_today_minus(1), 79.8, 0.08),
    ])
    found = th.audit_variant(tmp_path, "b", 14)
    em = [(k, w) for k, w, _ in found if w == "trade_entries.expected_move"]
    assert em, "expected_move was not examined at all"
    assert em[0][0] == "HEALED", f"a fixed instrument reported as broken: {em}"
    assert not any(k in ("DEAD_NOW", "PARTIAL_NOW") and w == "trade_entries.expected_move"
                   for k, w, _ in found)


def test_a_fully_healthy_variant_reports_nothing(tmp_path):
    _mkvariant(tmp_path, "d", [(_today_minus(i), 80.0, 0.08) for i in range(1, 7)])
    assert th.audit_variant(tmp_path, "d", 14) == []


def test_live_only_instruments_are_skipped_on_a_dry_run_variant(tmp_path):
    """Fill prices are legitimately NULL without real orders — flagging them is
    how the scanner version produced 118 false findings."""
    _mkvariant(tmp_path, "c", [(_today_minus(i), 80.0, 0.08) for i in range(1, 7)],
               dry_run=True)
    assert not any("fill_price" in w for _, w, _ in th.audit_variant(tmp_path, "c", 14))


def test_live_only_instruments_ARE_checked_on_the_live_seat(tmp_path):
    _mkvariant(tmp_path, "b", [(_today_minus(i), 80.0, 0.08) for i in range(1, 7)],
               dry_run=False)
    found = th.audit_variant(tmp_path, "b", 14)
    assert any("short_call_fill_price" in w for _, w, _ in found), found


class TestTheHeadlineCheck:
    """The check that would have caught +$21,439 being quoted for a +$2,010 record."""

    def _with_metrics(self, root, vid, cum, summaries, epoch=None):
        _mkvariant(root, vid, [(_today_minus(i), 80.0, 0.08) for i in range(1, 7)],
                   epoch=epoch)
        sub = f"variant_{vid}" if vid else ""
        db = root / "data" / sub / "backtesting.db"
        c = sqlite3.connect(str(db))
        for d, p in summaries:
            c.execute("INSERT INTO daily_summaries VALUES (?,?,?,?)", (d, "chop", 8.0, p))
        c.commit(); c.close()
        (root / "data" / sub / "hydra_metrics.json").write_text(
            json.dumps({"cumulative_pnl": cum}))

    def test_a_blended_headline_is_flagged(self, tmp_path, monkeypatch):
        """B's real shape: the file counts ALL history, the dashboard rebases.

        NOT a drift check — the file and the full-history DB sum agree exactly.
        The defect is that the two surfaces measure a different ERA.
        """
        monkeypatch.setattr(th, "_dashboard_baseline", lambda vid: "2026-08-01")
        self._with_metrics(tmp_path, "b", 21439.52,
                           [("2026-07-01", 19429.47), ("2026-08-01", 2010.05)])
        found = th.audit_variant(tmp_path, "b", 14)
        assert any(k == "ERA_MISMATCH" for k, _, _ in found), found

    def test_an_agreeing_headline_is_not_flagged(self, tmp_path, monkeypatch):
        monkeypatch.setattr(th, "_dashboard_baseline", lambda vid: "2026-08-01")
        self._with_metrics(tmp_path, "b", 2010.05, [("2026-08-01", 2010.05)])
        assert not any(k.startswith("ERA") or k.startswith("HEADLINE")
                       for k, _, _ in th.audit_variant(tmp_path, "b", 14))

    def test_the_epoch_is_read_from_the_CONFIG_not_the_metrics_file(self, tmp_path, monkeypatch):
        """Reading it from the metrics file made the first version report a
        false mismatch on variant E, whose epoch lives in its config."""
        self._with_metrics(tmp_path, "e", 500.0,
                           [("2026-07-01", -685.90), ("2026-09-25", 500.0)],
                           epoch="2026-09-24")
        monkeypatch.setattr(th, "_dashboard_baseline", lambda vid: "2026-09-24")
        found = th.audit_variant(tmp_path, "e", 14)
        assert not any(k == "HEADLINE" for k, _, _ in found), (
            "the epoch in the config was ignored, so pre-epoch history was "
            "counted and a false mismatch reported: %s" % found)


def test_an_unreadable_dashboard_baseline_is_REPORTED_not_swallowed(tmp_path, monkeypatch):
    """Swallowing it skips the most valuable check and still prints 'ok'.

    That is this script's own disease committed inside itself, and it bit once:
    run from /tmp without the repo on sys.path, every ERA_MISMATCH silently
    vanished and the report looked clean.
    """
    monkeypatch.setattr(th, "_BASELINE_UNAVAILABLE", "", raising=False)
    import sys
    monkeypatch.setitem(sys.modules, "dashboard.backend.config", None)
    th._dashboard_baseline("b")
    assert th._BASELINE_UNAVAILABLE, (
        "the baseline lookup failed silently — a skipped check must not read as a pass")
