"""Simulated position book + round-trip P&L, from the signal's own prices.

WHY THIS EXISTS. The first cut of this service recorded INTENTS only — it knew
a BUY had arrived, but not whether we were already long, and never marked what
the price did afterwards. So it could answer "how often do signals fire?" and
**not** "would they have made money?", which is the more important question. It
was half an instrument, and that was a real gap rather than a deferred nicety.

NO EXTERNAL PRICE SOURCE IS NEEDED. TradingView sends `{{close}}` in the alert
body, so a round trip can be priced from the two signals that opened and closed
it. That is deliberately first-order: it ignores slippage, spread and
commission, so **a positive result here is an UPPER BOUND, not a projection**.
This project has already measured what that gap costs in reality — entry rung
pricing was ~38% of one variant's net, and stop-close slippage about a third of
another's. Treat these numbers as "is there anything here at all", never as
expected return.

⚠️ A SELL WITH NO POSITION DOES NOT OPEN A SHORT. Shorting is a different risk
shape — borrow, unlimited loss, different margin — and inferring one from a
bare "sell" would silently invent exposure the signal never asked for. It is
recorded as flat, with a reason.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

_DDL = """
CREATE TABLE IF NOT EXISTS sim_positions (
    symbol      TEXT PRIMARY KEY,
    quantity    REAL NOT NULL,
    avg_price   REAL NOT NULL,
    opened_at   TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sim_roundtrips (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol       TEXT NOT NULL,
    quantity     REAL NOT NULL,
    entry_price  REAL NOT NULL,
    exit_price   REAL NOT NULL,
    opened_at    TEXT NOT NULL,
    closed_at    TEXT NOT NULL,
    gross_pnl    REAL NOT NULL,
    held_seconds INTEGER
);
"""

# Outcomes of applying a signal to the simulated book.
OPENED = "opened"
ADDED = "added"
CLOSED = "closed"
REDUCED = "reduced"
FLAT_NOOP = "flat_noop"      # sell with nothing to sell


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(_DDL)


def apply_signal(con: sqlite3.Connection, *, symbol: str, action: str,
                 quantity: float, price: Optional[float],
                 now: Optional[datetime] = None) -> Tuple[str, Optional[float], Optional[str]]:
    """Apply one admitted signal to the simulated book.

    Returns (effect, realized_pnl, note). Realized P&L is populated only when a
    position is closed or reduced.

    Priceless signals are still applied to the BOOK (so position state stays
    right) but cannot contribute P&L — recorded in the note rather than guessed.
    """
    now = now or datetime.now(timezone.utc)
    ts = now.astimezone(timezone.utc).isoformat()
    symbol = symbol.upper()
    action = action.lower()

    row = con.execute(
        "SELECT quantity, avg_price, opened_at FROM sim_positions WHERE symbol=?",
        (symbol,)).fetchone()
    held_qty = float(row[0]) if row else 0.0
    avg_price = float(row[1]) if row else 0.0
    opened_at = row[2] if row else ts

    if action == "buy":
        if held_qty <= 0:
            con.execute(
                "INSERT OR REPLACE INTO sim_positions VALUES (?,?,?,?,?)",
                (symbol, quantity, price or 0.0, ts, ts))
            return OPENED, None, (None if price else "no price in signal — P&L unmeasurable")
        # Already long: ADD, and re-average. A second BUY is a real second
        # signal, not a duplicate — dedup already removed byte-identical resends.
        new_qty = held_qty + quantity
        new_avg = ((avg_price * held_qty) + ((price or avg_price) * quantity)) / new_qty
        con.execute("UPDATE sim_positions SET quantity=?, avg_price=?, updated_at=? "
                    "WHERE symbol=?", (new_qty, new_avg, ts, symbol))
        return ADDED, None, (None if price else "no price in signal — average unchanged")

    # action == "sell"
    if held_qty <= 0:
        return FLAT_NOOP, None, ("sell with no position — recorded flat, NOT shorted "
                                 "(a short is a different risk shape and is never inferred)")

    closing = min(quantity, held_qty)
    realized = None
    note = None
    if price is not None and avg_price:
        realized = (price - avg_price) * closing
    else:
        note = "no price in signal — round trip not priced"

    remaining = held_qty - closing
    if remaining <= 1e-12:
        con.execute("DELETE FROM sim_positions WHERE symbol=?", (symbol,))
        effect = CLOSED
    else:
        con.execute("UPDATE sim_positions SET quantity=?, updated_at=? WHERE symbol=?",
                    (remaining, ts, symbol))
        effect = REDUCED

    if realized is not None:
        held_s = None
        try:
            held_s = int((now - datetime.fromisoformat(opened_at)).total_seconds())
        except Exception:
            pass
        con.execute(
            "INSERT INTO sim_roundtrips (symbol, quantity, entry_price, exit_price,"
            " opened_at, closed_at, gross_pnl, held_seconds) VALUES (?,?,?,?,?,?,?,?)",
            (symbol, closing, avg_price, price, opened_at, ts, realized, held_s))
    return effect, realized, note


def summary(con: sqlite3.Connection) -> Dict[str, Any]:
    """What the dry run has actually learned so far."""
    rt = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(gross_pnl),0), "
        "COALESCE(SUM(CASE WHEN gross_pnl>0 THEN 1 ELSE 0 END),0) "
        "FROM sim_roundtrips").fetchone()
    open_n = con.execute("SELECT COUNT(*) FROM sim_positions").fetchone()[0]
    n, total, wins = int(rt[0]), float(rt[1]), int(rt[2])
    return {
        "round_trips": n,
        "gross_pnl_upper_bound": round(total, 2),
        "win_rate_pct": round(100.0 * wins / n, 1) if n else None,
        "open_positions": open_n,
        "caveat": ("gross of slippage/spread/commission — an UPPER BOUND. "
                   "Measured elsewhere in this project: entry pricing ~38% of one "
                   "variant's net, stop-close slippage ~1/3 of another's."),
    }
