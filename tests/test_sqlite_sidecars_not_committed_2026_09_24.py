"""The auth database's write-ahead log must never reach the repository.

Found by the second full sweep, from an ordinary line in a deploy check: after
restarting the dashboard, ``git status`` on the VM showed

    ?? dashboard/dashboard_auth.db-shm
    ?? dashboard/dashboard_auth.db-wal

``.gitignore`` had ``*.db``, which does **not** match those — a different
extension. And **HOMER runs ``git add -A && commit && push`` on the VM every
night at 23:30 ET**. So the auth database's write-ahead log — holding rows
recently written to the table that stores sessions — was one agent run away from
being committed and pushed.

It never was; all history was checked. That is luck about timing, not a control:
nothing had created those files at the wrong moment before, because they only
appear while SQLite has the database open in WAL mode and a restart interrupts
the checkpoint.

The two facts that make this dangerous together are already each documented —
``homer_vm_autocommit_gotcha`` ("never leave untracked files in /opt/calypso")
and the WAL-mode database layout — and neither one alone would have caught it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SIDECARS = ("db-wal", "db-shm", "db-journal")


def _ignored(name: str) -> bool:
    r = subprocess.run(["git", "check-ignore", "-q", name],
                       cwd=ROOT, capture_output=True)
    return r.returncode == 0


class TestTheSidecarsAreIgnored:

    @pytest.mark.parametrize("ext", SIDECARS)
    def test_a_sidecar_anywhere_is_ignored(self, ext):
        assert _ignored(f"probe.{ext}"), f"*.{ext} is not ignored"

    @pytest.mark.parametrize("ext", SIDECARS)
    def test_including_the_real_auth_database_path(self, ext):
        """The specific file that appeared on the VM."""
        assert _ignored(f"dashboard/dashboard_auth.{ext}")

    def test_the_plain_db_rule_does_NOT_cover_them(self):
        """The reason this gap existed: `*.db` looks like it covers a `.db-wal`
        and does not. Pinned so nobody 'simplifies' the three rules away."""
        gi = (ROOT / ".gitignore").read_text()
        assert "*.db-wal" in gi and "*.db-shm" in gi

    def test_the_reason_is_recorded_beside_the_rule(self):
        gi = (ROOT / ".gitignore").read_text()
        assert "HOMER" in gi and "add -A" in gi


class TestNoneHasEverBeenCommitted:

    def test_no_sidecar_is_tracked_now(self):
        out = subprocess.run(["git", "ls-files"], cwd=ROOT,
                             capture_output=True, text=True).stdout
        bad = [ln for ln in out.splitlines()
               if any(ln.endswith(f".{e}") for e in SIDECARS)]
        assert not bad, f"tracked SQLite sidecars: {bad}"

    def test_none_was_ever_added_in_history(self):
        """If one ever lands, it stays in history even after deletion — so the
        useful assertion is that it never entered at all."""
        out = subprocess.run(
            ["git", "log", "--all", "--diff-filter=A", "--name-only", "--format="],
            cwd=ROOT, capture_output=True, text=True).stdout
        bad = sorted({ln for ln in out.splitlines()
                      if any(ln.endswith(f".{e}") for e in SIDECARS)})
        assert not bad, (
            f"a SQLite sidecar entered git history: {bad}. It persists in the "
            f"object store even if deleted later; treat as a disclosure.")


class TestTheDatabaseItselfStaysIgnoredToo:

    def test_the_auth_db_is_ignored(self):
        assert _ignored("dashboard/dashboard_auth.db")

    def test_a_variant_backtesting_db_is_ignored(self):
        assert _ignored("data/variant_b/backtesting.db")
