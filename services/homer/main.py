#!/usr/bin/env python3
"""
HOMER — Automated HYDRA Trading Journal Writer

Runs at 5:30 PM ET on weekdays. Detects missing trading days in the journal,
gathers data, and updates all sections automatically.

Usage:
    python -m services.homer.main
    python -m services.homer.main --dry-run
    sudo systemctl start homer.service
"""

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime

# Ensure project root is on path
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("homer")

CONFIG_PATH = os.path.join(_project_root, "services", "agents_config.json")
FALLBACK_CONFIG_PATH = os.path.join(_project_root, "bots", "hydra", "config", "config.json")


def load_config() -> dict:
    """Load agent config, falling back to HYDRA config."""
    for path in [CONFIG_PATH, FALLBACK_CONFIG_PATH]:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to load {path}: {e}")
    logger.error("No config file found")
    return {}


def is_trading_day() -> bool:
    """Check if today is a trading day (weekday + not a market holiday)."""
    from shared.market_hours import is_market_holiday, get_us_market_time

    now_et = get_us_market_time()
    if now_et.weekday() >= 5:
        logger.info(f"Weekend ({now_et.strftime('%A')}) — skipping")
        return False
    if is_market_holiday(now_et):
        logger.info("Market holiday — skipping")
        return False
    return True


def detect_missing_days(journal_dates: list, sheets_dates: list) -> list:
    """
    Find trading days in Sheets that are not yet in the journal.

    Args:
        journal_dates: List of "Mon DD" date labels from Section 2 header.
        sheets_dates: List of "YYYY-MM-DD" dates from Sheets Daily Summary.

    Returns:
        List of "YYYY-MM-DD" dates that need to be added.
    """
    from services.homer.journal_updater import format_date_label

    # Convert journal dates to a set for quick lookup
    journal_set = set(journal_dates)

    missing = []
    for date_str in sheets_dates:
        label = format_date_label(date_str)
        if label not in journal_set:
            missing.append(date_str)

    return sorted(missing)


def backup_journal(journal_path: str, backup_dir: str) -> str:
    """Create a backup of the journal before editing."""
    os.makedirs(backup_dir, exist_ok=True)

    from shared.market_hours import get_us_market_time

    now_et = get_us_market_time()
    backup_name = f"journal_backup_{now_et.strftime('%Y-%m-%d_%H%M')}.md"
    backup_path = os.path.join(backup_dir, backup_name)

    shutil.copy2(journal_path, backup_path)
    logger.info(f"Journal backed up to {backup_path}")
    return backup_path


def validate_journal(content: str) -> bool:
    """
    Basic validation that journal structure is intact after edits.

    Returns:
        True if structure looks valid.
    """
    checks = [
        ("## 1.", "Section 1 header missing"),
        ("## 2.", "Section 2 header missing"),
        ("## 3.", "Section 3 header missing"),
        ("## 4.", "Section 4 header missing"),
        ("## 5.", "Section 5 header missing"),
        ("## 8.", "Section 8 header missing"),
        ("## 9.", "Section 9 header missing"),
        ("| Column |", "Section 2 table header missing"),
        ("### P&L Verification", "P&L verification section missing"),
    ]

    for pattern, msg in checks:
        if pattern not in content:
            logger.error(f"Validation failed: {msg}")
            return False

    # Check Section 2 table has consistent column counts
    in_table = False
    expected_cols = None
    for line in content.split("\n"):
        if line.strip().startswith("| Column |"):
            in_table = True
            expected_cols = line.count("|")
            continue
        if in_table and line.strip().startswith("|"):
            cols = line.count("|")
            if cols != expected_cols:
                logger.error(
                    f"Validation failed: Section 2 table column mismatch "
                    f"(expected {expected_cols}, got {cols})"
                )
                return False
        elif in_table and not line.strip().startswith("|"):
            in_table = False

    logger.info("Journal validation passed")
    return True


def git_commit_and_push(journal_path: str, date_labels: list) -> bool:
    """Commit the journal update and push to remote. Returns True on success."""
    try:
        # Stage the journal file
        subprocess.run(
            ["git", "add", journal_path],
            cwd=_project_root,
            check=True,
            timeout=30,
        )

        # Build commit message
        dates_str = ", ".join(date_labels)
        commit_msg = f"journal: HOMER auto-update ({dates_str})"

        result = subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=_project_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            logger.info(f"Committed: {commit_msg}")
        else:
            if "nothing to commit" in result.stdout or "nothing to commit" in result.stderr:
                logger.info("Nothing to commit — journal unchanged")
                return True  # Not an error
            else:
                logger.error(f"git commit failed: {result.stderr}")
                return False

        # Push
        result = subprocess.run(
            ["git", "push"],
            cwd=_project_root,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            logger.info("Pushed to remote")
            return True
        else:
            logger.warning(f"git push failed: {result.stderr}")
            return False

    except subprocess.CalledProcessError as e:
        logger.error(f"Git operation failed: {e}")
        return False
    except subprocess.TimeoutExpired:
        logger.error("Git operation timed out")
        return False


def send_telegram_alert(config: dict, message: str):
    """Send a completion/failure message to the HYDRA Telegram chat."""
    import requests

    try:
        from shared.secret_manager import get_secret

        secret_value = get_secret("calypso-telegram-credentials")
        if not secret_value:
            logger.warning("Cannot send Telegram alert: no credentials")
            return

        creds = json.loads(secret_value)
        bot_token = creds.get("bot_token", "")
        chat_id = str(creds.get("chat_id", ""))

        if not bot_token or not chat_id:
            logger.warning("Cannot send Telegram alert: missing bot_token or chat_id")
            return

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

        # Try with Markdown first
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
        resp = requests.post(url, json=payload, timeout=10)

        if resp.status_code != 200:
            # Retry without Markdown
            payload.pop("parse_mode")
            requests.post(url, json=payload, timeout=10)

        logger.info("Telegram alert sent")
    except Exception as e:
        logger.error(f"Failed to send Telegram alert: {e}")


def _format_money_msg(value: float) -> str:
    """Format money for Telegram: show cents when fractional, integer when whole."""
    if value == int(value):
        return f"{int(value)}"
    return f"{value:.2f}"


def build_success_message(date_labels: list, days_added: int, net_pnl: float, cum_pnl: float, total_days: int, git_ok: bool = True) -> str:
    """Build the Telegram success message."""
    dates_str = ", ".join(date_labels)
    sections = "1, 2, 3, 4, 5, 8, 9"

    pnl_str = f"{'+' if net_pnl >= 0 else '-'}{_format_money_msg(abs(net_pnl))}"
    cum_str = _format_money_msg(cum_pnl)
    git_line = "_Committed and pushed to main_" if git_ok else "⚠️ _Git commit/push failed — manual push required_"

    return (
        f"📝 *HOMER* | Journal Updated\n"
        f"\n"
        f"Updated HYDRA Trading Journal for: {dates_str}\n"
        f"Days added: {days_added}\n"
        f"Sections updated: {sections}\n"
        f"\n"
        f"Net P&L today: {pnl_str}\n"
        f"Cumulative P&L: ${cum_str} ({total_days} days)\n"
        f"\n"
        f"{git_line}"
    )


def build_failure_message(date_str: str, error: str) -> str:
    """Build the Telegram failure message."""
    return (
        f"⚠️ *HOMER* | Journal Update Failed\n"
        f"\n"
        f"Failed to update journal for: {date_str}\n"
        f"Error: {error}\n"
        f"\n"
        f"_Manual update required_"
    )


def main():
    """Entry point for HOMER journal writer."""
    parser = argparse.ArgumentParser(description="HOMER — HYDRA Trading Journal Writer")
    parser.add_argument("--dry-run", action="store_true", help="Parse and collect data but don't write")
    args = parser.parse_args()

    logger.info("HOMER starting journal update")

    if not is_trading_day():
        return

    config = load_config()
    if not config:
        logger.error("No config loaded — aborting")
        sys.exit(1)

    homer_config = config.get("homer", {})
    journal_path = homer_config.get("journal_path", "docs/HYDRA_TRADING_JOURNAL.md")
    backup_dir = homer_config.get("backup_dir", "intel/homer")
    max_catch_up = homer_config.get("max_catch_up_days", 10)

    # Resolve paths relative to project root
    journal_path = os.path.join(_project_root, journal_path)
    backup_dir = os.path.join(_project_root, backup_dir)

    if not os.path.exists(journal_path):
        logger.error(f"Journal file not found: {journal_path}")
        sys.exit(1)

    # 1. Read current journal
    with open(journal_path) as f:
        journal_content = f.read()

    from services.homer.journal_parser import JournalParser

    jp = JournalParser(journal_content)
    journal_dates = jp.get_existing_dates_from_section2()
    logger.info(f"Journal has {len(journal_dates)} date columns: {journal_dates[-3:]}...")

    # 2. Collect data from Sheets and files
    from services.homer.data_collector import collect_all_data, collect_day_data, get_all_trading_dates

    all_data = collect_all_data(config)
    sheets_dates = get_all_trading_dates(all_data)
    logger.info(f"Sheets has {len(sheets_dates)} trading days: {sheets_dates[-3:]}...")

    # 3. Detect missing days
    missing_days = detect_missing_days(journal_dates, sheets_dates)

    if not missing_days:
        logger.info("Journal is up to date — no missing days")
        return

    if len(missing_days) > max_catch_up:
        logger.warning(
            f"Too many missing days ({len(missing_days)} > {max_catch_up}). "
            f"Processing only the last {max_catch_up}."
        )
        missing_days = missing_days[-max_catch_up:]

    from services.homer.journal_updater import format_date_label

    date_labels = [format_date_label(d) for d in missing_days]
    logger.info(f"Missing days to add: {date_labels}")

    if args.dry_run:
        logger.info("DRY RUN — would add these days but not writing")
        for d in missing_days:
            day = collect_day_data(all_data, d, config)
            if day:
                logger.info(f"  {d}: {day['summary'].get('Daily P&L ($)', '?')} P&L, {len(day['entries'])} entries")
        return

    # 4. Create Claude client for narratives
    from shared.claude_client import get_anthropic_client

    claude_client = get_anthropic_client(config)
    if not claude_client:
        logger.warning("No Claude client — narratives will be empty (data-only mode)")

    # 5. Backup journal
    backup_journal(journal_path, backup_dir)

    # 6. Process each missing day
    from services.homer.journal_updater import (
        add_section2_column,
        add_pnl_verification,
        build_section3_day_block,
        insert_section3_block,
        add_section4_market_character_row,
        add_section4_expected_move_row,
        build_section9_day_block,
        insert_section9_block,
        add_section8_version_rows,
    )
    from services.homer.narrative_generator import generate_day_narratives

    post_improvement_day_num = jp.get_last_post_improvement_day_number()
    existing_versions = jp.get_existing_versions_in_section8()
    last_pnl = 0
    last_cum_pnl = 0

    try:
        for date_str in missing_days:
            day_data = collect_day_data(all_data, date_str, config)
            if not day_data:
                logger.warning(f"Skipping {date_str} — no data available")
                continue

            label = format_date_label(date_str)
            logger.info(f"Processing {label}...")

            # Generate narratives (Claude API)
            narratives = {"observations": "", "market_label": "See data", "assessment": ""}
            if claude_client:
                try:
                    narratives = generate_day_narratives(claude_client, day_data, config)
                except Exception as e:
                    logger.warning(f"Narrative generation failed for {label}: {e}")

            # Section 2: Add column
            add_section2_column(jp, day_data)

            # Section 2b: P&L verification
            add_pnl_verification(jp, day_data)

            # Section 3: Entry detail block
            block = build_section3_day_block(day_data, narratives)
            insert_section3_block(jp, block)

            # Section 4: Market conditions
            add_section4_market_character_row(jp, day_data, narratives.get("market_label", "See data"))
            add_section4_expected_move_row(jp, day_data)

            # Section 8: New versions
            version_history = day_data.get("version_history", [])
            new_versions = [
                v for v in version_history
                if v.get("date") == date_str and f"v{v.get('version')}" not in existing_versions
            ]
            if new_versions:
                add_section8_version_rows(jp, new_versions)
                for v in new_versions:
                    existing_versions.append(f"v{v['version']}")

            # Section 9: Post-improvement block
            post_improvement_day_num += 1
            sec9_block = build_section9_day_block(day_data, post_improvement_day_num, narratives)
            insert_section9_block(jp, sec9_block)

            last_pnl = float(day_data["summary"].get("Daily P&L ($)", 0) or 0)
            last_cum_pnl = float(day_data["summary"].get("Cumulative P&L ($)", 0) or 0)

            logger.info(f"Completed {label}")

        # 7. Section 2c: Update cumulative metrics (once, after all days)
        from services.homer.journal_updater import update_cumulative_metrics

        metrics = all_data.get("metrics", {})
        if metrics:
            update_cumulative_metrics(jp, metrics, date_labels[-1])

        # 8. Section 5: Recompute aggregates (once, after all days)
        from services.homer.journal_updater import recompute_section5

        recompute_section5(
            jp,
            all_data["daily_summary_rows"],
            sheets_dates[0],
            sheets_dates[-1],
            len(sheets_dates),
        )

        # 9. Section 1: Update executive summary
        from services.homer.journal_updater import update_section1

        update_section1(
            jp,
            all_data["daily_summary_rows"],
            sheets_dates[0],
            sheets_dates[-1],
            len(sheets_dates),
        )

        # 10. Validate and write
        new_content = jp.rebuild()

        if not validate_journal(new_content):
            error_msg = "Journal validation failed after edits"
            logger.error(error_msg)
            if homer_config.get("telegram_alert", True):
                send_telegram_alert(config, build_failure_message(", ".join(date_labels), error_msg))
            sys.exit(1)

        # Atomic write: temp file → validate → rename
        temp_path = journal_path + ".tmp"
        with open(temp_path, "w") as f:
            f.write(new_content)

        os.replace(temp_path, journal_path)
        logger.info(f"Journal updated: {journal_path} ({len(new_content)} chars)")

        # 11. Git commit + push
        git_ok = git_commit_and_push(journal_path, date_labels)

        # 12. Telegram alert (reflects git status)
        if homer_config.get("telegram_alert", True):
            msg = build_success_message(
                date_labels, len(missing_days), last_pnl, last_cum_pnl, len(sheets_dates), git_ok
            )
            send_telegram_alert(config, msg)

    except Exception as e:
        logger.error(f"HOMER failed: {e}", exc_info=True)
        if homer_config.get("telegram_alert", True):
            send_telegram_alert(config, build_failure_message(", ".join(date_labels), str(e)))
        sys.exit(1)

    logger.info(f"HOMER journal update complete ({len(missing_days)} days added)")


if __name__ == "__main__":
    main()
