"""Refuse to run a post-close agent before the trading day has actually settled.

WHY THIS EXISTS
---------------
HERMES (daily execution analysis) and HOMER (trading journal, which COMMITS TO
GIT) are timer-driven. Until 2026-09-10 they fired at 19:00 and 19:30 ET while
settlement was completing between 21:45 and 22:37 ET — so both ran roughly three
hours early, every trading day, and analysed an unsettled day. Their unit files
had said "post-settlement" the whole time; the schedules simply never matched.

Moving the timers to 23:00 / 23:30 fixed the common case, but a fixed clock time
is still a guess against a variable event. 0DTE SPX is PM-settled and IBKR's
position feed clears hours after the 16:00 close, so the completion time drifts
with the broker. If it ever slips past the timer we are silently back to the
original bug — and a wrong journal entry gets committed to git, where it is
persistent and easy to miss.

THE SIGNAL
----------
A ``daily_summaries`` row for the date, in the SAME database the agent reads.
That row is written by the bot only after ``check_after_hours_settlement()``
returns True, so its presence is the bot's own statement that the day is done.
Using the agent's own data source (via ``resolve_agent_source``) rather than a
separately-configured path is deliberate: a gate that checks a different
database than the agent reads can pass while the agent still gets nothing.

FAIL CLOSED, AND WHY THAT IS SAFE HERE
--------------------------------------
When settlement cannot be confirmed the agent SKIPS. That is normally a
dangerous default — a silent skip can mean the job never runs again — but it is
recoverable in this specific case: HOMER detects missing journal days on later
runs and back-fills them, so a skipped day is picked up tomorrow. Running early
is NOT recoverable in the same way, because the wrong numbers get written and
committed.

A date with no ``daily_summaries`` row at all (a day the bot never traded and
never summarised) is not treated as a permanent block either: it simply is not a
day HOMER's gap detection considers missing, because gap detection compares
against the same table.

``CALYPSO_SKIP_SETTLEMENT_GATE=1`` overrides, for an operator running an agent
by hand outside the normal cycle.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

#: Operator escape hatch for manual/ad-hoc runs.
OVERRIDE_ENV = "CALYPSO_SKIP_SETTLEMENT_GATE"


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def settlement_is_complete(
    config: Dict[str, Any],
    *,
    agent: str,
    date_str: str,
    db_path: Optional[str] = None,
) -> Tuple[bool, str]:
    """Has ``date_str`` settled, according to the data this agent reads?

    Returns ``(ok, reason)``. ``reason`` is always populated and is meant to be
    logged verbatim — when this gate blocks a run, the reason is the only thing
    an operator has to go on.

    Legacy Google-Sheets mode returns ``(True, ...)``: there is no cheap
    settlement signal there, and blocking a path that is not in use would be a
    regression rather than a safeguard.
    """
    if os.environ.get(OVERRIDE_ENV, "").strip() not in ("", "0", "false", "False"):
        return True, f"{OVERRIDE_ENV} set — settlement gate bypassed by operator"

    if db_path is None:
        try:
            from shared.sheets_db_shim import resolve_agent_source

            data_source, db_path = resolve_agent_source(config, agent)
        except Exception as e:  # pragma: no cover - defensive
            return True, (
                f"could not resolve the agent's data source "
                f"({type(e).__name__}: {e}) — gate not applied"
            )
        if data_source != "db":
            return True, f"data_source={data_source!r} (not db) — gate not applied"

    path = db_path if os.path.isabs(db_path) else os.path.join(_project_root(), db_path)
    if not os.path.exists(path):
        return False, f"database not found at {path} — cannot confirm settlement"

    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = con.execute(
                "SELECT 1 FROM daily_summaries WHERE date = ? LIMIT 1", (date_str,)
            ).fetchone()
        finally:
            con.close()
    except sqlite3.Error as e:
        return False, f"could not read {path} ({type(e).__name__}: {e})"

    if row:
        return True, f"daily_summaries row present for {date_str}"
    return False, (
        f"no daily_summaries row for {date_str} in {os.path.basename(path)} — "
        f"the bot has not finished settling the day. 0DTE SPX is PM-settled and "
        f"IBKR's feed clears hours after the 16:00 close (observed 21:45-22:37 "
        f"ET), so this most likely means settlement is running late. Skipping "
        f"rather than analysing an incomplete day; HOMER back-fills missed days "
        f"on a later run. Override with {OVERRIDE_ENV}=1."
    )


def require_settled(config: Dict[str, Any], *, agent: str, date_str: str) -> bool:
    """Convenience wrapper that logs the outcome. True = safe to proceed."""
    ok, reason = settlement_is_complete(config, agent=agent, date_str=date_str)
    if ok:
        logger.info("SETTLEMENT-GATE (%s): proceeding — %s", agent, reason)
    else:
        logger.error("SETTLEMENT-GATE (%s): SKIPPING RUN — %s", agent, reason)
    return ok
