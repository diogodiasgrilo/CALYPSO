#!/usr/bin/env python3
"""
CLIO — Weekly Strategy Analyst & Optimizer

Runs Saturday 9:00 AM ET. Aggregates the week's data, calls Claude for
deep analysis, saves report, appends learnings to strategy_memory.md,
commits to git, and runs retention cleanup.

Usage:
    python -m services.clio.main
    sudo systemctl start clio.service
"""

import json
import logging
import os
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
logger = logging.getLogger("clio")

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


def get_week_label(dt: datetime) -> str:
    """Get ISO week label like '2026-W09'."""
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def append_learnings(learnings: str, config: dict):
    """Append new learnings to strategy_memory.md."""
    memory_file = config.get("clio", {}).get(
        "strategy_memory_file", "intel/strategy_memory.md"
    )

    if not learnings.strip():
        logger.info("No new learnings to append")
        return

    from shared.market_hours import get_us_market_time

    now_et = get_us_market_time()
    week_label = get_week_label(now_et)

    entry = f"\n## {week_label} ({now_et.strftime('%Y-%m-%d')})\n\n{learnings}\n"

    try:
        with open(memory_file, "a") as f:
            f.write(entry)
        logger.info(f"Appended learnings to {memory_file}")
    except IOError as e:
        logger.error(f"Failed to append to {memory_file}: {e}")


def git_commit_and_push(report_path: str, memory_file: str, week_label: str):
    """Commit report and strategy memory to git and push."""
    try:
        # Stage files
        subprocess.run(
            ["git", "add", report_path, memory_file],
            cwd=_project_root,
            check=True,
            timeout=30,
        )

        # Commit
        commit_msg = f"intel: Clio weekly report {week_label}"
        subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=_project_root,
            check=True,
            timeout=30,
        )
        logger.info(f"Committed: {commit_msg}")

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
        else:
            logger.warning(f"git push failed: {result.stderr}")

    except subprocess.CalledProcessError as e:
        logger.error(f"Git operation failed: {e}")
    except subprocess.TimeoutExpired:
        logger.error("Git operation timed out")


def main():
    """Entry point for CLIO weekly analysis."""
    logger.info("CLIO starting weekly analysis")

    config = load_config()
    if not config:
        logger.error("No config loaded — aborting")
        sys.exit(1)

    from shared.market_hours import get_us_market_time

    now_et = get_us_market_time()
    week_label = get_week_label(now_et)

    # 1. Aggregate data
    from services.clio.data_aggregator import aggregate_weekly_data

    data = aggregate_weekly_data(config, now_et.date())

    # 2. Get Claude client
    from shared.claude_client import get_anthropic_client

    client = get_anthropic_client(config)
    if not client:
        logger.error("Failed to create Anthropic client — aborting")
        sys.exit(1)

    # 3. Analyze
    from services.clio.analyst import analyze_weekly_data

    result = analyze_weekly_data(client, data, week_label, config)
    if not result:
        logger.error("Analysis failed — no report generated")
        sys.exit(1)

    full_report, learnings = result

    # 4. Save report
    report_dir = config.get("clio", {}).get("report_dir", "intel/clio")
    os.makedirs(report_dir, exist_ok=True)
    report_filename = f"week_{week_label.replace('-', '_')}.md"
    report_path = os.path.join(report_dir, report_filename)

    with open(report_path, "w") as f:
        f.write(full_report)
    logger.info(f"Report saved: {report_path} ({len(full_report)} chars)")

    # 5. Append learnings to strategy memory
    memory_file = config.get("clio", {}).get(
        "strategy_memory_file", "intel/strategy_memory.md"
    )
    append_learnings(learnings, config)

    # 6. Commit and push to git
    git_commit_and_push(report_path, memory_file, week_label)

    # 7. Retention cleanup
    from services.cleanup_intel import cleanup_old_reports

    cleanup_old_reports(config)

    # 8. Send weekend digest alert
    if config.get("alerts", {}).get("enabled", False):
        try:
            from shared.alert_service import AlertService, AlertType, AlertPriority

            # Build summary from first ~500 chars of report
            summary = full_report[:500]
            if len(full_report) > 500:
                summary += "\n\n... (full report in intel/clio/)"

            alert_service = AlertService(config, "CLIO")
            alert_service.send_alert(
                alert_type=AlertType.DAILY_SUMMARY,
                title=f"CLIO Weekly Report — {week_label}",
                message=summary,
                priority=AlertPriority.LOW,
            )
            logger.info("Weekend digest alert sent")
        except Exception as e:
            logger.error(f"Failed to send alert: {e}")

    logger.info(f"CLIO weekly analysis complete ({week_label})")


if __name__ == "__main__":
    main()
