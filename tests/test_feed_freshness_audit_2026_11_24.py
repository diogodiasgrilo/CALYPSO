"""The nightly audit must notice a lapsed market-data subscription.

WHY THIS EXISTS. The IBKR feeds behind this fleet have lapsed twice --
2026-07-06 and 2026-11-02 -- and neither lapse raised anything. IBKR does not
error when a subscription drops; it silently serves 15-20 minute delayed data.
The second lapse ran 20 days and was found only because somebody went looking.
Detection was entirely "a human remembers to grep", which is the same gap this
script already closes for the database instruments.

THE HARD PART IS NOT COUNTING THE WARNINGS. It is that **a zero has two
causes**: the feed is healthy, or nothing was looked at. A silent detector and
a restored subscription produce the identical 0 -- which is exactly why the
2026-11-23 verification had to prove the detector was live (E priced SPY at
723.65, and the same code emitted the warning at 15:58 the Friday before)
before the 0 was believed. The test that matters most here is therefore
`test_no_rth_lines_reports_UNCHECKED_not_ok`, not the one that finds a lapse.

Every group carries a CONTROL that reproduces the original defect, because a
source-grep assertion would pass against a gutted implementation (see memory
`feedback_source_grep_tests_are_blind`, 2026-09-23).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.audit_telemetry_health import (
    _FEED_SUBSCRIPTION,
    _variant_log_dir,
    audit_data_feed_freshness,
)

# A real line, copied from variant E's log after the 2026-11-20 symbol fix.
DELAYED = ("2026-11-20 {t} | WARNING  | bots.hydra.base_strategy | "
           "DATA: {sym} quote is DELAYED (6509='DP') — proceeding but data "
           "may lag real-time (further occurrences suppressed for 5 min)")
NORMAL = ("2026-11-23 {t} | INFO     | shared.logger_service | "
          "Heartbeat: SPX 7163.65 | VIX 18.2")


def _mk(root: Path, vid: str, day: str, lines: list[str], log_dir: str = "") -> Path:
    """Build a variant's config + one dated log file."""
    cfgdir = root / "bots" / "hydra" / "config"
    cfgdir.mkdir(parents=True, exist_ok=True)
    name = f"config_variant_{vid}.json" if vid else "config.json"
    (cfgdir / name).write_text(json.dumps(
        {"logging": {"log_dir": log_dir}} if log_dir else {}))
    d = (root / log_dir) if log_dir else (
        root / "logs" / (f"hydra_variant_{vid}" if vid else "hydra"))
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"bot.log.{day}"
    f.write_text("\n".join(lines) + "\n")
    return f


# --------------------------------------------------------------------------
# 1. A lapse is found, and the finding is ACTIONABLE
# --------------------------------------------------------------------------

def test_a_lapsed_feed_is_reported(tmp_path):
    _mk(tmp_path, "e", "2026-11-20",
        [NORMAL.format(t="09:35:00")]
        + [DELAYED.format(t=f"09:4{i}:00", sym="SPY") for i in range(5)])
    found = audit_data_feed_freshness(tmp_path, "e")
    assert len(found) == 1
    kind, where, detail = found[0]
    assert kind == "FEED_DELAYED"
    assert where == "feed/SPY"
    assert "5 delayed-quote warning(s)" in detail


def test_the_finding_names_the_subscription_to_check(tmp_path):
    """"Market data is delayed" is not actionable. Three feeds, three
    separate subscriptions, restored independently (OPRA 11-11, Network B
    11-23) -- the finding has to say WHICH to go look at."""
    _mk(tmp_path, "e", "2026-11-20", [DELAYED.format(t="10:00:00", sym="SPY")])
    detail = audit_data_feed_freshness(tmp_path, "e")[0][2]
    assert "Network B" in detail
    assert _FEED_SUBSCRIPTION["SPY"] in detail


def test_the_finding_carries_the_6509_flag_and_the_date(tmp_path):
    _mk(tmp_path, "e", "2026-11-20", [DELAYED.format(t="10:00:00", sym="SPY")])
    detail = audit_data_feed_freshness(tmp_path, "e")[0][2]
    assert "6509='DP'" in detail
    assert "2026-11-20" in detail


def test_an_unknown_symbol_still_reports_rather_than_crashing(tmp_path):
    """A symbol absent from the subscription map must still be reported --
    silence on an unrecognised instrument is the failure mode being fixed."""
    _mk(tmp_path, "e", "2026-11-20", [DELAYED.format(t="10:00:00", sym="QQQ")])
    found = audit_data_feed_freshness(tmp_path, "e")
    assert len(found) == 1
    assert found[0][1] == "feed/QQQ"
    assert "subscription" in found[0][2]


# --------------------------------------------------------------------------
# 2. THE CENTRAL INVARIANT: a zero is not automatically healthy
# --------------------------------------------------------------------------

def test_a_healthy_session_reports_nothing(tmp_path):
    _mk(tmp_path, "e", "2026-11-23",
        [NORMAL.format(t=f"1{h}:00:00") for h in range(5)])
    assert audit_data_feed_freshness(tmp_path, "e") == []


def test_no_rth_lines_reports_UNCHECKED_not_ok(tmp_path):
    """THE test. A log with no regular-hours activity means nobody looked --
    a holiday, or the variant was not running. Reporting that as healthy is
    how a lapse survives 20 days."""
    _mk(tmp_path, "e", "2026-11-22",  # a Saturday: pre-market lines only
        [NORMAL.format(t="04:00:00"), NORMAL.format(t="07:03:00")])
    found = audit_data_feed_freshness(tmp_path, "e")
    assert len(found) == 1
    assert found[0][0] == "UNCHECKED"
    assert "NOT checked" in found[0][2]


def test_an_unreadable_log_reports_UNCHECKED(tmp_path, monkeypatch):
    _mk(tmp_path, "e", "2026-11-20", [NORMAL.format(t="10:00:00")])

    def boom(self, *a, **k):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_text", boom)
    found = audit_data_feed_freshness(tmp_path, "e")
    assert found and found[0][0] == "UNCHECKED"


def test_a_variant_that_has_never_logged_is_silent_not_a_finding(tmp_path):
    """No log directory at all is a variant that was never started -- not
    evidence about any feed. It must not manufacture noise every night."""
    assert audit_data_feed_freshness(tmp_path, "zz") == []


# --------------------------------------------------------------------------
# 3. Scope: RTH only, and the latest REAL session
# --------------------------------------------------------------------------

def test_delayed_outside_rth_is_not_a_finding(tmp_path):
    """Outside regular hours a delayed/frozen flag is normal. The one VIX='D'
    line on 2026-11-02 was a 07:03 pre-market artefact, not a lapse -- the
    scope check is what keeps this from firing every night."""
    _mk(tmp_path, "e", "2026-11-20",
        [NORMAL.format(t="10:00:00"),
         DELAYED.format(t="07:03:00", sym="VIX"),
         DELAYED.format(t="17:30:00", sym="SPY")])
    assert audit_data_feed_freshness(tmp_path, "e") == []


def test_it_skips_past_a_weekend_to_the_last_real_session(tmp_path):
    """Sat/Sun carry no RTH lines. The check must fall through to Friday and
    report on it, not stop at the newest file and declare UNCHECKED."""
    _mk(tmp_path, "e", "2026-11-20", [DELAYED.format(t="10:00:00", sym="SPY")])
    _mk(tmp_path, "e", "2026-11-21", [NORMAL.format(t="04:00:00")])
    _mk(tmp_path, "e", "2026-11-22", [NORMAL.format(t="04:00:00")])
    found = audit_data_feed_freshness(tmp_path, "e")
    assert len(found) == 1 and found[0][1] == "feed/SPY"


def test_the_newest_real_session_wins_over_an_older_lapse(tmp_path):
    """A restored feed must clear. Friday lapsed, Monday clean -> no finding;
    this is the 11-20 -> 11-23 transition the fleet actually went through."""
    _mk(tmp_path, "e", "2026-11-20", [DELAYED.format(t="10:00:00", sym="SPY")])
    _mk(tmp_path, "e", "2026-11-23", [NORMAL.format(t="10:00:00")])
    assert audit_data_feed_freshness(tmp_path, "e") == []


def test_several_symbols_are_reported_separately(tmp_path):
    """One account event dropped OPRA and Network B together. Collapsing them
    into one finding would hide that two subscriptions need checking."""
    _mk(tmp_path, "e", "2026-11-20",
        [DELAYED.format(t="10:00:00", sym="SPY"),
         DELAYED.format(t="10:05:00", sym="SPX")])
    found = audit_data_feed_freshness(tmp_path, "e")
    assert {w for _, w, _ in found} == {"feed/SPY", "feed/SPX"}


# --------------------------------------------------------------------------
# 4. The symbol is CAPTURED, never assumed
# --------------------------------------------------------------------------

def test_the_symbol_comes_from_the_line_not_from_an_assumption(tmp_path):
    """Until 2026-11-20 the warning hardcoded "SPX" while E and H trade SPY,
    so 9,114 lines named the wrong instrument and a SPY grep found nothing.
    A checker that re-assumed the symbol would inherit that blindness."""
    _mk(tmp_path, "e", "2026-11-20", [DELAYED.format(t="10:00:00", sym="SPY")])
    found = audit_data_feed_freshness(tmp_path, "e")
    assert found[0][1] == "feed/SPY", "symbol must be read from the log line"
    assert "SPX" not in found[0][2]


# --------------------------------------------------------------------------
# 5. The log directory comes from CONFIG, not from the variant id
# --------------------------------------------------------------------------

def test_log_dir_is_read_from_config_not_derived_from_the_variant_id(tmp_path):
    """`logging.log_dir` BEATS HYDRA_VARIANT_ID -- which is how `bl` spent its
    first days writing into `b`'s log file (fixed 2026-10-09). A checker that
    guessed the path by convention would read the wrong strategy's log and
    report on the wrong variant."""
    _mk(tmp_path, "bl", "2026-11-20",
        [DELAYED.format(t="10:00:00", sym="SPY")], log_dir="logs/custom_bl")
    assert _variant_log_dir(tmp_path, "bl") == tmp_path / "logs" / "custom_bl"
    found = audit_data_feed_freshness(tmp_path, "bl")
    assert len(found) == 1, "must follow log_dir out of the conventional path"


def test_an_absolute_log_dir_is_honoured(tmp_path):
    target = tmp_path / "elsewhere"
    cfgdir = tmp_path / "bots" / "hydra" / "config"
    cfgdir.mkdir(parents=True)
    (cfgdir / "config_variant_e.json").write_text(
        json.dumps({"logging": {"log_dir": str(target)}}))
    assert _variant_log_dir(tmp_path, "e") == target


def test_a_missing_config_falls_back_to_the_conventional_path(tmp_path):
    assert _variant_log_dir(tmp_path, "g") == tmp_path / "logs" / "hydra_variant_g"
    assert _variant_log_dir(tmp_path, "") == tmp_path / "logs" / "hydra"


def test_an_unparsable_config_does_not_crash_the_audit(tmp_path):
    cfgdir = tmp_path / "bots" / "hydra" / "config"
    cfgdir.mkdir(parents=True)
    (cfgdir / "config_variant_e.json").write_text("{not json")
    assert _variant_log_dir(tmp_path, "e") == tmp_path / "logs" / "hydra_variant_e"


# --------------------------------------------------------------------------
# 6. Containment: the feed check cannot suppress the DB findings
# --------------------------------------------------------------------------

def test_a_feed_check_failure_does_not_hide_the_db_findings(tmp_path, capsys,
                                                            monkeypatch):
    """The two checks read different things and fail for unrelated reasons.
    An exception in one must not abort the other -- the same containment bug
    that silently skipped whole variants on 8 of 13 runs (2026-11-16)."""
    import scripts.audit_telemetry_health as m

    monkeypatch.setattr(m, "VARIANTS", ["e"])
    monkeypatch.setattr(m, "audit_variant",
                        lambda *a, **k: [("DEAD_NOW", "t.c", "db finding")])
    monkeypatch.setattr(m, "audit_data_feed_freshness",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert m.main(["--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "db finding" in out, "a feed-check crash must not hide a DB finding"
    assert "NOT checked" in out, "the crash must be reported, not swallowed"


def test_the_check_is_wired_into_main(tmp_path, capsys, monkeypatch):
    """Pins the WIRING, not the function. The feed check existing but never
    being called is the failure this cannot be allowed to miss."""
    import scripts.audit_telemetry_health as m

    monkeypatch.setattr(m, "VARIANTS", ["e"])
    monkeypatch.setattr(m, "audit_variant", lambda *a, **k: [])
    _mk(tmp_path, "e", "2026-11-20", [DELAYED.format(t="10:00:00", sym="SPY")])
    assert m.main(["--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "FEED_DELAYED" in out and "feed/SPY" in out


def test_the_check_is_non_gating(tmp_path, monkeypatch):
    """A delayed feed on two dry-run-locked strategies must not fail the unit
    that gates real money. Report, do not gate."""
    import scripts.audit_telemetry_health as m

    monkeypatch.setattr(m, "VARIANTS", ["e"])
    monkeypatch.setattr(m, "audit_variant", lambda *a, **k: [])
    _mk(tmp_path, "e", "2026-11-20", [DELAYED.format(t="10:00:00", sym="SPY")])
    assert m.main(["--root", str(tmp_path)]) == 0, "must stay exit 0"


# --------------------------------------------------------------------------
# 7. CONTROLS -- each reproduces the real defect and must fail the group above
# --------------------------------------------------------------------------

def test_CONTROL_counting_the_whole_file_would_fire_every_night(tmp_path):
    """Drop the RTH scope and the pre-market artefact becomes a finding."""
    f = _mk(tmp_path, "e", "2026-11-20",
            [NORMAL.format(t="10:00:00"), DELAYED.format(t="07:03:00", sym="VIX")])
    assert audit_data_feed_freshness(tmp_path, "e") == []
    assert "quote is DELAYED" in f.read_text(), "the line IS in the file"


def test_CONTROL_a_zero_and_an_unchecked_day_are_distinguishable(tmp_path):
    """If UNCHECKED collapsed into "no finding", these two would be equal --
    and a lapse would hide behind every holiday and restart."""
    healthy = tmp_path / "healthy"
    quiet = tmp_path / "quiet"
    _mk(healthy, "e", "2026-11-23", [NORMAL.format(t="10:00:00")])
    _mk(quiet, "e", "2026-11-22", [NORMAL.format(t="04:00:00")])
    assert audit_data_feed_freshness(healthy, "e") == []
    assert audit_data_feed_freshness(quiet, "e") != []


def test_CONTROL_the_regex_matches_the_real_production_line():
    """Guards against the regex drifting from the emitted text. Built from the
    real logger call, not from the sample string above."""
    from scripts.audit_telemetry_health import _DELAYED_RE
    real = ("2026-11-20 15:53:31 | WARNING  | bots.hydra.base_strategy | DATA: "
            "SPY quote is DELAYED (6509='DP') — proceeding but data may lag "
            "real-time (further occurrences suppressed for 5 min)")
    m = _DELAYED_RE.search(real)
    assert m and m.group(1) == "SPY" and m.group(2) == "DP"


def test_CONTROL_every_emitted_line_still_matches_the_regex():
    """Reads the ACTUAL format strings out of the source that logs them and
    RENDERS them the way logging would, so a reworded warning fails here
    instead of silently never matching again.

    This control paid for itself on the first run: it caught that the source
    uses `(6509=%r)`, not `(6509='%s')`. `%r` of a str renders WITH quotes, so
    the regex was right -- but only by luck, and the test's own rendering was
    wrong. There are also TWO call sites (underlying at ~1021 and volatility
    at ~1142) with different trailing text; both must match.
    """
    import re
    from scripts.audit_telemetry_health import _DELAYED_RE

    src = Path("bots/hydra/base_strategy.py").read_text()
    fmts = re.findall(r'"(DATA: %s quote is DELAYED[^"]*)"', src)
    assert fmts, "the DELAYED warning is no longer emitted by base_strategy"
    assert len(fmts) >= 2, (
        f"expected both the underlying and volatility call sites, found "
        f"{len(fmts)}")

    for fmt in fmts:
        # Render as logging would: %s -> the symbol, %r -> repr of the flag.
        rendered = fmt.replace("%s", "SPY", 1).replace("%r", repr("DP"), 1)
        m = _DELAYED_RE.search(rendered)
        assert m, (f"the audit regex no longer matches what the bot logs: "
                   f"{rendered!r}")
        assert m.group(1) == "SPY" and m.group(2) == "DP"


def test_CONTROL_the_delayed_branch_can_never_log_a_quoteless_flag():
    """`%r` of None would render `6509=None` -- no quotes -- and the regex
    would miss it. That is unreachable only because the branch is gated on
    `avail[:1] == "D"`, so `avail` is always a non-empty str. If that guard
    ever goes, this check goes blind silently, so pin the guard."""
    src = Path("bots/hydra/base_strategy.py").read_text()
    assert 'first = avail[:1] if avail else None' in src, (
        "the guard that makes `avail` a non-empty str in the DELAYED branch "
        "has changed — re-check whether 6509=%r can render without quotes, "
        "which would make the audit regex silently stop matching")
