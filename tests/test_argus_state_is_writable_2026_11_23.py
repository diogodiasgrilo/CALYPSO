"""ARGUS could never persist its alert-dedup state, so dedup was a no-op.

`services/argus/notify.py` wrote `argus_alert_state.json` into
`/opt/calypso/data/`. `deploy/argus.service` runs with `ProtectSystem=strict`,
`ReadOnlyPaths=/opt/calypso/data` and `ReadWritePaths=/opt/calypso/intel/argus`
— so every write failed with `[Errno 30] Read-only file system` and was
swallowed by the deliberately fail-open handler. **The file had never existed on
the VM.**

What that cost: `ARGUS_DEDUP_WINDOW_S = 3600` promises "re-ping a persistent
failure at most once/hour". With nothing persisted, `_recently_alerted()` always
returns False, so a SUSTAINED health failure alerts on every 15-minute timer
tick — **96 alerts/day where 24 was intended.** Latent until something actually
failed for more than one cycle.

Found 2026-11-23 when a 9-way fleet restart made ARGUS fail for a single cycle
(it caught the fleet mid-restart; it passed again immediately after). That was
the first time in the journal's retention window it had tried to write at all,
which is why a years-old path bug surfaced as a one-line warning inside an
unrelated deploy.

The fix moves the file to the directory the unit ALREADY grants, rather than
adding `/opt/calypso/data` to `ReadWritePaths`: a health checker has no business
with write access to the trading data directory, and that `ReadOnlyPaths` entry
is deliberate.

THE TEST BELOW DERIVES THE ANSWER FROM THE UNIT FILE. Hard-coding
"intel/argus" would re-break the moment either side moved, which is exactly the
failure being fixed — the code and the sandbox disagreed and nothing compared
them. So it parses `argus.service` and asserts the state path falls under a
granted `ReadWritePaths=` and NOT under a `ReadOnlyPaths=`.
"""

from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

UNIT = os.path.join(ROOT, "deploy/argus.service")


def _unit_paths(directive: str) -> list[str]:
    """Every path listed on a given systemd directive in argus.service."""
    out: list[str] = []
    with open(UNIT, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            if key.strip() != directive:
                continue
            out.extend(v for v in val.split() if v.startswith("/"))
    return out


def _state_path() -> str:
    from services.argus.notify import ARGUS_STATE_PATH
    return os.path.abspath(ARGUS_STATE_PATH)


def _as_deployed(local_abs: str) -> str:
    """Map the repo-relative path onto the VM layout the unit describes."""
    return os.path.join("/opt/calypso", os.path.relpath(local_abs, ROOT))


class TestTheStateFileLandsSomewhereTheUnitCanWrite:
    def test_the_unit_declares_a_strict_sandbox(self):
        """The premise. If this ever stops being true the rest is moot."""
        with open(UNIT, encoding="utf-8") as fh:
            body = fh.read()
        assert "ProtectSystem=strict" in body

    def test_the_state_path_is_under_a_ReadWritePaths_grant(self):
        """THE REGRESSION. Derived from the unit, not hard-coded."""
        granted = _unit_paths("ReadWritePaths")
        assert granted, "argus.service grants no writable paths at all"
        deployed = _as_deployed(_state_path())
        assert any(deployed.startswith(g.rstrip("/") + "/") for g in granted), (
            f"{deployed} is not under any ReadWritePaths= {granted} — every "
            f"write will fail with EROFS and be swallowed by the fail-open handler"
        )

    def test_the_state_path_is_NOT_under_a_ReadOnlyPaths_entry(self):
        """The other half: `data/` is explicitly read-only and must stay so."""
        ro = _unit_paths("ReadOnlyPaths")
        deployed = _as_deployed(_state_path())
        offending = [r for r in ro
                     if deployed.startswith(r.rstrip("/") + "/")]
        assert not offending, f"{deployed} sits under ReadOnlyPaths {offending}"

    def test_CONTROL_the_old_location_would_fail_both_checks(self):
        """Prove the test can see the original bug: the previous path, under
        `data/`, must be rejected by the same logic."""
        old = "/opt/calypso/data/argus_alert_state.json"
        granted = _unit_paths("ReadWritePaths")
        ro = _unit_paths("ReadOnlyPaths")
        assert not any(old.startswith(g.rstrip("/") + "/") for g in granted), (
            "control broken: the old path appears writable"
        )
        assert any(old.startswith(r.rstrip("/") + "/") for r in ro), (
            "control broken: the old path is not under ReadOnlyPaths"
        )

    def test_the_data_dir_is_still_read_only_for_argus(self):
        """Guards against the lazy alternative fix — widening the sandbox so a
        health checker can write to the trading data directory."""
        ro = _unit_paths("ReadOnlyPaths")
        rw = _unit_paths("ReadWritePaths")
        assert any(r.rstrip("/").endswith("/opt/calypso/data") for r in ro), ro
        assert not any(r.rstrip("/") == "/opt/calypso/data" for r in rw), (
            "argus.service now grants write access to the trading data dir"
        )


class TestTheWriteItselfIsRobust:
    def test_it_creates_its_directory_and_writes_atomically(self, tmp_path, monkeypatch):
        """A fresh host has no intel/argus yet; and the write must be atomic so a
        crash cannot leave a half-written dedup record."""
        import services.argus.notify as n

        target = tmp_path / "intel" / "argus" / "argus_alert_state.json"
        monkeypatch.setattr(n, "ARGUS_STATE_PATH", str(target))
        n._record_alert("some-fingerprint")
        assert target.exists(), "state was not persisted"
        assert not target.with_suffix(".json.tmp").exists(), "temp file left behind"

    def test_a_write_failure_still_fails_open(self, tmp_path, monkeypatch):
        """Dedup must never silence a fault. An unwritable location is logged,
        not raised — that is why this bug stayed invisible, and it is still the
        right behaviour."""
        import services.argus.notify as n

        monkeypatch.setattr(n, "ARGUS_STATE_PATH", "/proc/nope/state.json")
        n._record_alert("fp")                     # must not raise
        assert n._recently_alerted("fp") is False, "a failed write must not dedup"
