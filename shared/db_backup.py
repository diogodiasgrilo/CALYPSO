"""One correct way to back up a SQLite database before a destructive write.

WHY THIS EXISTS. Three scripts independently hand-rolled the same backup and all
three got the same two things wrong:

  1. **`shutil.copy2` is not safe on a WAL database.** `DataRecorder` and
     `dc_recorder` both set `PRAGMA journal_mode=WAL`, which is PERSISTENT on the
     file — every later connection inherits it. Committed transactions can be
     sitting in the `-wal` sidecar, so copying the `.db` alone yields a backup
     that is silently missing recent rows. It looks like a backup and restores
     like a rollback. `sqlite3`'s online backup API checkpoints into a single
     consistent file, and works while another connection holds the DB open.

  2. **A second-granularity timestamp collides.** Two runs inside the same
     second produce the same filename, so the second one overwrites the good
     backup with a copy of the ALREADY-MODIFIED database — destroying the
     original at exactly the moment it is wanted. Milliseconds plus a
     never-overwrite guard closes that.

This module is NOT imported by HYDRA, the strategies, or `calypso-broker` — it is
an operator-tooling helper, so adding it needs no broker restart and cannot
affect the trading path.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

__all__ = ["safe_db_backup"]


def safe_db_backup(db_path: str, tag: str) -> str:
    """Back up `db_path` to a timestamped sibling file. Returns the backup path.

    Args:
        db_path: the database to copy. Must exist.
        tag: short label for what is about to happen, e.g. "pre_skip_backfill".

    Raises:
        FileNotFoundError: if `db_path` does not exist — refusing here is the
            point. Silently "succeeding" would let the caller proceed to write
            believing it has a rollback.
        sqlite3.Error: if the copy fails, for the same reason.
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"cannot back up a database that isn't there: {db_path}")

    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%S") + f"{now.microsecond // 1000:03d}Z"
    backup = f"{db_path}.{tag}_{stamp}"
    seq = 0
    while os.path.exists(backup):
        seq += 1
        backup = f"{db_path}.{tag}_{stamp}.{seq}"

    # Read-only source: the backup must never be the thing that mutates the DB,
    # and this works even while the caller holds its own open connection.
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(backup)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return backup
