#!/usr/bin/env python3
"""Check E#1 call spread price AFTER the 10:49 stop — query Saxo option chain
history to see if we'd have been better off waiting."""
import sqlite3
import sys

DB_PATH = "data/backtesting.db"

def main():
    conn = sqlite3.connect(DB_PATH)

    # Get E#1 strikes (UICs not in DB schema — derive from state file)
    e = conn.execute(
        "SELECT short_call_strike, long_call_strike "
        "FROM trade_entries WHERE date='2026-04-17' AND entry_number=1"
    ).fetchone()
    print("E#1 strikes: SC=%s LC=%s" % (e[0], e[1]))

    # Get UICs from state file
    import json
    with open("data/hydra_state.json") as f:
        state = json.load(f)
    sc_uic = lc_uic = None
    for ent in state.get("entries", []):
        if ent.get("entry_number") == 1:
            sc_uic = ent.get("short_call_uic")
            lc_uic = ent.get("long_call_uic")
            break
    print("E#1 UICs (from state): SC=%s LC=%s" % (sc_uic, lc_uic))

    # 1. Check DB snapshots for E#1 after stop
    print("\n=== E#1 snapshots 10:49-11:00 (post-stop) ===")
    rows = conn.execute(
        "SELECT timestamp, call_spread_value, put_spread_value, "
        "short_call_bid, short_call_ask, long_call_bid, long_call_ask "
        "FROM spread_snapshots WHERE entry_number=1 "
        "AND timestamp >= '2026-04-17 10:49' AND timestamp <= '2026-04-17 11:00' "
        "ORDER BY timestamp"
    ).fetchall()

    print("Count: %d" % len(rows))
    for r in rows[:30]:
        ts = r[0][11:19]
        csv = r[1] if r[1] is not None else "None"
        sc_bid = r[3] if r[3] is not None else 0
        sc_ask = r[4] if r[4] is not None else 0
        lc_bid = r[5] if r[5] is not None else 0
        lc_ask = r[6] if r[6] is not None else 0
        print("  %s  csv=%s  SC bid/ask=%.2f/%.2f  LC bid/ask=%.2f/%.2f" % (
            ts, csv, sc_bid, sc_ask, lc_bid, lc_ask))

    # 2. Now the REAL check: query Saxo directly for the E#1 option chain history
    # The call was closed at 10:49:48 with fill @ $4.70.
    # Let's see the short call (7160) price over the next 30 min to see if it dropped.
    print("\n=== QUERYING SAXO FOR E#1 SHORT CALL (UIC %s) CURRENT PRICE ===" % sc_uic)
    sys.path.insert(0, "/opt/calypso")
    from shared.saxo_client import SaxoClient
    from shared.config_loader import get_config_loader

    cfg = get_config_loader("bots/hydra/config").load_config()
    client = SaxoClient(cfg)
    client.authenticate()

    # Note: 0DTE options expired at 4 PM today, so we can't query them live anymore.
    # But we can check closed positions for the fill data we already have.
    q = client.get_quote(int(sc_uic), asset_type="StockIndexOption") if sc_uic else None
    if q:
        quote = q.get("Quote", {})
        print("  Current quote: bid=%s ask=%s mid=%s last=%s" % (
            quote.get("Bid"), quote.get("Ask"), quote.get("Mid"),
            quote.get("LastTraded")))
    else:
        print("  No quote available (likely expired 0DTE)")

    # 3. Best alternative: use closed positions API to see fills
    print("\n=== CHECKING SAXO CLOSED POSITIONS FOR E#1 ===")
    closed = client.get_closed_positions(limit=100)
    if closed:
        for p in closed:
            pb = p.get("ClosedPosition", {})
            uic = pb.get("Uic")
            if sc_uic and lc_uic and uic in (int(sc_uic), int(lc_uic)):
                print("  UIC %s: Open=%.2f  Close=%.2f  CloseTime=%s  BuyOrSell=%s  Amount=%s" % (
                    uic, pb.get("OpenPrice", 0), pb.get("ClosingPrice", 0),
                    pb.get("ExecutionTimeClose", "")[:19], pb.get("BuyOrSell"),
                    pb.get("Amount")))

if __name__ == "__main__":
    main()
