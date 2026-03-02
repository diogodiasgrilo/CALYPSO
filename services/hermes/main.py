#!/usr/bin/env python3
"""
HERMES — Daily Execution Quality Analyst

Runs at 5:00 PM ET on weekdays. Collects today's trading data,
sends it to Claude for analysis, saves a report, and sends a summary alert.

Usage:
    python -m services.hermes.main
    sudo systemctl start hermes.service
"""

import json
import logging
import os
import sys

# Ensure project root is on path
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("hermes")

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
    if now_et.weekday() >= 5:  # Saturday or Sunday
        logger.info(f"Weekend ({now_et.strftime('%A')}) — skipping")
        return False
    if is_market_holiday(now_et):
        logger.info("Market holiday — skipping")
        return False
    return True


def main():
    """Entry point for HERMES daily analysis."""
    logger.info("HERMES starting daily analysis")

    if not is_trading_day():
        return

    config = load_config()
    if not config:
        logger.error("No config loaded — aborting")
        sys.exit(1)

    from shared.market_hours import get_us_market_time

    today_str = get_us_market_time().strftime("%Y-%m-%d")

    # 1. Collect data
    from services.hermes.data_collector import collect_daily_data

    data = collect_daily_data(config, today_str)

    # 2. Get Claude client
    from shared.claude_client import get_anthropic_client

    client = get_anthropic_client(config)
    if not client:
        logger.error("Failed to create Anthropic client — aborting")
        sys.exit(1)

    # 3. Analyze
    from services.hermes.analyzer import analyze_daily_data

    result = analyze_daily_data(client, data, today_str, config)
    if not result:
        logger.error("Analysis failed — no report generated")
        sys.exit(1)

    full_report, summary = result

    # 4. Save report
    report_dir = config.get("hermes", {}).get("report_dir", "intel/hermes")
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(report_dir, f"{today_str}.md")

    with open(report_path, "w") as f:
        f.write(full_report)
    logger.info(f"Report saved: {report_path} ({len(full_report)} chars)")

    # 5. Send alert summary
    if config.get("alerts", {}).get("enabled", False):
        try:
            from shared.alert_service import AlertService, AlertType, AlertPriority

            alert_service = AlertService(config, "HERMES")
            alert_service.send_alert(
                alert_type=AlertType.DAILY_SUMMARY,
                title=f"HERMES Daily Report — {today_str}",
                message=summary,
                priority=AlertPriority.LOW,
            )
            logger.info("Summary alert sent")
        except Exception as e:
            logger.error(f"Failed to send alert: {e}")

    logger.info("HERMES daily analysis complete")


if __name__ == "__main__":
    main()
