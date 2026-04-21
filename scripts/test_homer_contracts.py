"""Regression test: Phase 2 A-1 HOMER + A-2/J-1 journal Contracts row.

Verifies:
  1. SECTION2_ROWS contains the Contracts row between VIX Low and Entries Completed
  2. journal_updater's formatter renders integer contract counts correctly
  3. HOMER SYSTEM_PROMPT includes the per-contract normalization rule
  4. HYDRA_TRADING_JOURNAL.md Section 2 has the Contracts row with 1-per-cell
     for all historical columns (pre-2c era)

Run: python scripts/test_homer_contracts.py
"""
from __future__ import annotations
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def test_section2_rows_includes_contracts():
    """Contracts row is in SECTION2_ROWS, positioned between VIX Low and Entries Completed."""
    print("\n[HJ-1] SECTION2_ROWS contains 'Contracts' row after VIX Low")
    from services.homer.journal_updater import SECTION2_ROWS

    labels = [r[0] for r in SECTION2_ROWS]
    assert "Contracts" in labels, f"'Contracts' missing from SECTION2_ROWS: {labels}"
    idx_contracts = labels.index("Contracts")
    idx_vix_low = labels.index("VIX Low")
    idx_entries = labels.index("Entries Completed")
    assert idx_vix_low < idx_contracts < idx_entries, (
        f"Contracts row should be between VIX Low ({idx_vix_low}) and "
        f"Entries Completed ({idx_entries}), got {idx_contracts}"
    )
    print(f"  \u2713 Contracts at index {idx_contracts} "
          f"(VIX Low={idx_vix_low}, Entries Completed={idx_entries})")


def test_contracts_formatter_renders_int():
    """The formatter for Contracts produces **N** markdown bold."""
    print("\n[HJ-2] Contracts formatter handles integer + null-safe default")
    from services.homer.journal_updater import SECTION2_ROWS

    contracts_row = next(r for r in SECTION2_ROWS if r[0] == "Contracts")
    label, sheet_key, formatter = contracts_row
    assert sheet_key == "Contracts"
    # Test valid values
    assert formatter(2) == "**2**", f"got {formatter(2)}"
    assert formatter("2") == "**2**", f"got {formatter('2')}"
    assert formatter(1) == "**1**", f"got {formatter(1)}"
    # Test null-safe fallback: empty / None / zero-ish → 1
    assert formatter("") == "**1**", f"empty string should render '**1**', got {formatter('')}"
    assert formatter(None) == "**1**", f"None should render '**1**', got {formatter(None)}"
    assert formatter(0) == "**1**", f"0 should render '**1**' (invalid on live entry), got {formatter(0)}"
    print("  \u2713 formatter renders **N** and null-safe defaults to **1**")


def test_homer_system_prompt_mentions_contracts():
    """HOMER SYSTEM_PROMPT has a CRITICAL RULE about contracts per day."""
    print("\n[HJ-3] HOMER SYSTEM_PROMPT has per-contract normalization rule")
    from services.homer.narrative_generator import SYSTEM_PROMPT

    required_phrases = [
        "Contracts",
        "per-contract",
        "normalize",
    ]
    for phrase in required_phrases:
        assert phrase.lower() in SYSTEM_PROMPT.lower(), (
            f"SYSTEM_PROMPT missing required phrase: '{phrase}'"
        )
    print(f"  \u2713 SYSTEM_PROMPT contains contracts / per-contract / normalize guidance")


def test_journal_section_2_contracts_row_present():
    """docs/HYDRA_TRADING_JOURNAL.md Section 2 table has a 'Contracts' row with all 1s."""
    print("\n[HJ-4] Journal Section 2 has Contracts row with historical all-1s")
    journal_path = REPO / "docs" / "HYDRA_TRADING_JOURNAL.md"
    lines = journal_path.read_text().splitlines()

    # Find the Contracts row
    contracts_lines = [i for i, line in enumerate(lines) if line.startswith("| Contracts |")]
    assert len(contracts_lines) >= 1, "No '| Contracts |' row found in journal"

    # Primary row is the first one (Section 2 table). Count cells.
    contracts_line = lines[contracts_lines[0]]
    cells = contracts_line.split("|")
    # cells[0] = empty (before first pipe); cells[1] = "Contracts "; cells[2:-1] = data
    data_cells = [c.strip() for c in cells[2:-1]]
    assert len(data_cells) >= 40, (
        f"expected at least 40 data cells, got {len(data_cells)}"
    )

    # Every historical data cell should be either "1" or "**1**" — all 1s.
    invalid = [(i, c) for i, c in enumerate(data_cells) if c not in ("1", "**1**")]
    assert not invalid, f"Non-1 contract cells in historical row: {invalid[:5]}"

    # Verify cell count matches neighboring rows (VIX Low, Entries Completed)
    for neighbor in ("| VIX Low |", "| Entries Completed |"):
        n_lines = [i for i, line in enumerate(lines) if line.startswith(neighbor)]
        if n_lines:
            n_cells = [c.strip() for c in lines[n_lines[0]].split("|")[2:-1]]
            assert len(n_cells) == len(data_cells), (
                f"Column count mismatch: Contracts={len(data_cells)} vs "
                f"{neighbor.strip('|').strip()}={len(n_cells)}"
            )
    print(f"  \u2713 Contracts row present with {len(data_cells)} historical cells, all = 1")
    print(f"  \u2713 Column count matches VIX Low and Entries Completed rows")


def main():
    tests = [
        test_section2_rows_includes_contracts,
        test_contracts_formatter_renders_int,
        test_homer_system_prompt_mentions_contracts,
        test_journal_section_2_contracts_row_present,
    ]
    fails = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"  \u2717 FAIL: {e}")
            fails += 1
        except Exception as e:
            import traceback
            traceback.print_exc()
            fails += 1
    print("\n" + "=" * 60)
    if fails == 0:
        print(f"ALL {len(tests)} A-1 + A-2/J-1 HOMER TESTS PASSED")
        return 0
    print(f"{fails} / {len(tests)} tests FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
