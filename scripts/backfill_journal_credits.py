#!/usr/bin/env python3
"""
Backfill per-side credits and override_reason from HYDRA Trading Journal into backtesting DB.

The journal has explicit per-side credit data for all 82 entries (Feb 10 - Mar 9),
but the DB is missing call_credit/put_credit for 64 entries (all before Mar 4)
because Google Sheets didn't log per-side credits until v1.7.2.

This script parses the journal's Section 3 entry tables and updates the DB.

Usage:
    python scripts/backfill_journal_credits.py           # dry run (default)
    python scripts/backfill_journal_credits.py --apply    # actually update DB
"""

import argparse
import os
import re
import sqlite3
import sys

# Journal path
JOURNAL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "docs",
    "HYDRA_TRADING_JOURNAL.md",
)
DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "backtesting.db",
)

# Date header pattern: "### Feb 10 (Tuesday) - NET P&L: +$350"
DATE_HEADER_RE = re.compile(
    r"^### ((?:Feb|Mar)\s+\d+)\s+\((\w+)\)\s+-\s+NET P&L:\s+[+-]?\$[\d,.]+",
)

# Table row pattern for entry detail tables
# Matches: | #1 | 10:05 | NEUTRAL | Put-only (MKT-011) | P:6935 | $210 (P) | EXPIRED | +$210 |
# Also matches: | #1 | 10:05 | NEUTRAL | Full IC | C:7000 P:6910 | $435 ($125C+$310P) | ... |
# And Mar 4+ format: | #1 | 11:06 AM ET | NEUTRAL | Iron Condor | C:6915 P:6825 | $250 ($75C+$175P) | ... |
ENTRY_ROW_RE = re.compile(
    r"\|\s*#(\d+)\s*\|"  # Entry number
    r"\s*([^|]+?)\s*\|"  # Time
    r"\s*([^|]+?)\s*\|"  # Signal
    r"\s*([^|]+?)\s*\|"  # Type
    r"\s*([^|]+?)\s*\|"  # Short Strikes (or Spread Width on some formats)
    r"(?:\s*[^|]*?\s*\|)?"  # Optional Spread Width column
    r"\s*\$?([\d,]+(?:\.\d+)?)\s*"  # Credit amount
    r"(?:\(([^)]*)\))?"  # Optional credit breakdown
    r"\s*\|",  # End of credit cell
)

# More targeted credit parsing from the Credit column content
CREDIT_FULL_IC_RE = re.compile(
    r"\$?([\d,]+(?:\.\d+)?)\s*\(\s*\$?([\d,.]+)\s*C\s*\+\s*\$?([\d,.]+)\s*P\s*\)"
)
CREDIT_PUT_ONLY_RE = re.compile(r"\$?([\d,]+(?:\.\d+)?)\s*\(\s*P\s*\)")
CREDIT_CALL_ONLY_RE = re.compile(r"\$?([\d,]+(?:\.\d+)?)\s*\(\s*C\s*\)")

# Month mapping for 2026
MONTH_MAP = {"Feb": "02", "Mar": "03"}


def parse_date(date_str: str) -> str:
    """Convert 'Feb 10' to '2026-02-10'."""
    parts = date_str.strip().split()
    month = MONTH_MAP.get(parts[0], "??")
    day = int(parts[1])
    return f"2026-{month}-{day:02d}"


def parse_credit(credit_text: str):
    """Parse credit text to extract total, call_credit, put_credit.

    Returns (total, call_credit, put_credit, override_reason).
    """
    credit_text = credit_text.strip()

    # Full IC: "$435 ($125C+$310P)"
    m = CREDIT_FULL_IC_RE.search(credit_text)
    if m:
        total = float(m.group(1).replace(",", ""))
        call_c = float(m.group(2).replace(",", ""))
        put_c = float(m.group(3).replace(",", ""))
        return total, call_c, put_c

    # Put-only: "$210 (P)"
    m = CREDIT_PUT_ONLY_RE.search(credit_text)
    if m:
        total = float(m.group(1).replace(",", ""))
        return total, 0.0, total

    # Call-only: "$140 (C)"
    m = CREDIT_CALL_ONLY_RE.search(credit_text)
    if m:
        total = float(m.group(1).replace(",", ""))
        return total, total, 0.0

    # Plain number only (no breakdown) - can't determine per-side
    try:
        total = float(credit_text.replace("$", "").replace(",", "").strip())
        return total, None, None
    except ValueError:
        return None, None, None


def determine_override_reason(entry_type: str, signal: str) -> str:
    """Determine override_reason from entry type and signal."""
    entry_type = entry_type.strip()

    if "MKT-011" in entry_type:
        return "mkt-011"
    if "MKT-010" in entry_type:
        return "mkt-010"

    # One-sided entries from trend signal (not MKT-011)
    if "Put-only" in entry_type or "Call-only" in entry_type:
        sig = signal.strip().upper()
        if sig in ("BULLISH", "BEARISH"):
            return "trend"
        # If NEUTRAL with one-sided and no MKT code, assume mkt-011
        return "mkt-011"

    return None


def parse_journal_entries(journal_path: str) -> list:
    """Parse all entry records from the journal's Section 3."""
    with open(journal_path, "r") as f:
        content = f.read()

    entries = []
    current_date = None
    in_section_3 = False

    for line in content.split("\n"):
        # Detect Section 3 start
        if "## 3. Entry-Level Detail by Day" in line:
            in_section_3 = True
            continue

        # Detect Section 4 start (end of Section 3)
        if line.startswith("## 4.") or line.startswith("## 5."):
            break

        if not in_section_3:
            continue

        # Date headers
        m = DATE_HEADER_RE.match(line)
        if m:
            current_date = parse_date(m.group(1))
            continue

        if not current_date:
            continue

        # Skip non-table lines
        if not line.strip().startswith("|"):
            continue

        # Skip header/separator rows
        if "Entry" in line and "Time" in line and "Signal" in line:
            continue
        if "---" in line and "|" in line:
            continue

        # Try to parse entry row
        # Find entry number
        entry_num_match = re.match(r"\|\s*#(\d+)\s*\|", line)
        if not entry_num_match:
            continue

        entry_num = int(entry_num_match.group(1))

        # Split the row by |
        cells = [c.strip() for c in line.split("|")]
        cells = [c for c in cells if c]  # Remove empty strings

        if len(cells) < 6:
            continue

        # cells[0] = #N, cells[1] = time, cells[2] = signal, cells[3] = type
        signal = cells[2].strip()
        entry_type = cells[3].strip()

        # Skip SKIPPED entries
        if "SKIPPED" in entry_type or "--" == entry_type.strip():
            continue

        # Find credit cell - must start with $ and match credit patterns
        # Skip cells that are entry type descriptions (contain MKT-011: put $90<$100)
        credit_text = None
        for i, cell in enumerate(cells):
            stripped = cell.strip()
            # Credit cell STARTS with $ and contains a credit breakdown or is a plain dollar amount
            if not stripped.startswith("$"):
                continue
            # Skip P&L Impact cells (start with +$ or -$)
            if stripped.startswith("+$") or stripped.startswith("-$"):
                continue
            # This is a credit cell
            credit_text = stripped
            break

        if not credit_text:
            print(f"  WARNING: Could not find credit for {current_date} Entry #{entry_num}")
            continue

        total, call_c, put_c = parse_credit(credit_text)
        override = determine_override_reason(entry_type, signal)

        if total is not None:
            entries.append({
                "date": current_date,
                "entry_number": entry_num,
                "total_credit": total,
                "call_credit": call_c,
                "put_credit": put_c,
                "override_reason": override,
                "entry_type": entry_type,
                "signal": signal,
            })

    return entries


def get_db_entries(db_path: str) -> dict:
    """Get existing entries from the DB, keyed by (date, entry_number)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        "SELECT date, entry_number, call_credit, put_credit, total_credit, override_reason "
        "FROM trade_entries"
    )
    entries = {}
    for row in cursor:
        key = (row["date"], row["entry_number"])
        entries[key] = dict(row)
    conn.close()
    return entries


def apply_updates(db_path: str, updates: list):
    """Apply updates to the DB."""
    conn = sqlite3.connect(db_path)
    for upd in updates:
        sets = []
        vals = []
        for field in ("call_credit", "put_credit", "override_reason"):
            if field in upd and upd[field] is not None:
                sets.append(f"{field} = ?")
                vals.append(upd[field])

        if not sets:
            continue

        vals.extend([upd["date"], upd["entry_number"]])
        sql = (
            f"UPDATE trade_entries SET {', '.join(sets)} "
            f"WHERE date = ? AND entry_number = ?"
        )
        conn.execute(sql, vals)

    conn.commit()
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Backfill per-side credits from journal")
    parser.add_argument("--apply", action="store_true", help="Actually update the DB")
    parser.add_argument(
        "--journal", default=JOURNAL_PATH, help="Path to HYDRA Trading Journal"
    )
    parser.add_argument("--db", default=DB_PATH, help="Path to backtesting.db")
    args = parser.parse_args()

    if not os.path.exists(args.journal):
        print(f"ERROR: Journal not found at {args.journal}")
        sys.exit(1)

    if not os.path.exists(args.db):
        print(f"ERROR: Database not found at {args.db}")
        sys.exit(1)

    print(f"Journal: {args.journal}")
    print(f"Database: {args.db}")
    print(f"Mode: {'APPLY' if args.apply else 'DRY RUN'}")
    print()

    # Parse journal
    journal_entries = parse_journal_entries(args.journal)
    print(f"Parsed {len(journal_entries)} entries from journal")

    # Get DB entries
    db_entries = get_db_entries(args.db)
    print(f"Found {len(db_entries)} entries in DB")
    print()

    # Compare and find updates needed
    updates = []
    missing_in_db = 0
    already_correct = 0
    needs_update = 0

    for je in journal_entries:
        key = (je["date"], je["entry_number"])
        if key not in db_entries:
            missing_in_db += 1
            print(f"  MISSING IN DB: {je['date']} Entry #{je['entry_number']}")
            continue

        db_entry = db_entries[key]
        upd = {"date": je["date"], "entry_number": je["entry_number"]}
        changed = False

        # Check call_credit
        if je["call_credit"] is not None:
            db_val = db_entry["call_credit"]
            if db_val is None or db_val == 0:
                upd["call_credit"] = je["call_credit"]
                changed = True

        # Check put_credit
        if je["put_credit"] is not None:
            db_val = db_entry["put_credit"]
            if db_val is None or db_val == 0:
                upd["put_credit"] = je["put_credit"]
                changed = True

        # Check override_reason
        if je["override_reason"] is not None:
            db_val = db_entry["override_reason"]
            if db_val is None:
                upd["override_reason"] = je["override_reason"]
                changed = True

        if changed:
            needs_update += 1
            cc = upd.get("call_credit", "—")
            pc = upd.get("put_credit", "—")
            ov = upd.get("override_reason", "—")
            print(
                f"  UPDATE: {je['date']} E#{je['entry_number']} "
                f"call={cc} put={pc} override={ov}"
            )
            updates.append(upd)
        else:
            already_correct += 1

    print()
    print(f"Summary:")
    print(f"  Journal entries parsed: {len(journal_entries)}")
    print(f"  Already correct in DB: {already_correct}")
    print(f"  Need update:           {needs_update}")
    print(f"  Missing from DB:       {missing_in_db}")

    if not updates:
        print("\nNo updates needed.")
        return

    if args.apply:
        print(f"\nApplying {len(updates)} updates...")
        apply_updates(args.db, updates)
        print("Done!")

        # Verify
        db_entries_after = get_db_entries(args.db)
        null_call = sum(
            1 for e in db_entries_after.values() if e["call_credit"] is None
        )
        null_put = sum(
            1 for e in db_entries_after.values() if e["put_credit"] is None
        )
        null_override = sum(
            1 for e in db_entries_after.values() if e["override_reason"] is None
        )
        print(f"\nPost-update verification:")
        print(f"  Entries with NULL call_credit:    {null_call}/{len(db_entries_after)}")
        print(f"  Entries with NULL put_credit:     {null_put}/{len(db_entries_after)}")
        print(f"  Entries with NULL override_reason: {null_override}/{len(db_entries_after)}")
    else:
        print(f"\nDry run — use --apply to update the DB")


if __name__ == "__main__":
    main()
