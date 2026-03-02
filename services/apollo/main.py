#!/usr/bin/env python3
"""
APOLLO — Morning Scout

Runs at 8:30 AM ET on weekdays. Fetches pre-market data, reads context
(yesterday's HERMES report, strategy memory, economic calendar), calls Claude
for a morning briefing with risk level, and sends the full briefing as an alert.

Usage:
    python -m services.apollo.main
    sudo systemctl start apollo.service
"""

import json
import logging
import os
import sys
from datetime import timedelta

# Ensure project root is on path
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("apollo")

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
    """Check if today is a trading day."""
    from shared.market_hours import is_market_holiday, get_us_market_time

    now_et = get_us_market_time()
    if now_et.weekday() >= 5:
        logger.info(f"Weekend ({now_et.strftime('%A')}) — skipping")
        return False
    if is_market_holiday(now_et):
        logger.info("Market holiday — skipping")
        return False
    return True


def gather_context(config: dict) -> dict:
    """Gather context data: yesterday's HERMES, strategy memory, events."""
    from shared.market_hours import get_us_market_time

    context = {}
    now_et = get_us_market_time()

    # Yesterday's HERMES report
    yesterday_str = (now_et - timedelta(days=1)).strftime("%Y-%m-%d")
    # Check last 3 days (in case yesterday was weekend/holiday)
    hermes_dir = config.get("hermes", {}).get("report_dir", "intel/hermes")
    for days_back in range(1, 4):
        check_date = (now_et - timedelta(days=days_back)).strftime("%Y-%m-%d")
        report_path = os.path.join(hermes_dir, f"{check_date}.md")
        if os.path.exists(report_path):
            try:
                with open(report_path) as f:
                    context["hermes_report"] = f.read()
                logger.info(f"Read HERMES report from {check_date}")
                break
            except IOError as e:
                logger.warning(f"Failed to read HERMES report: {e}")

    # Strategy memory
    memory_file = config.get("apollo", {}).get(
        "strategy_memory_file", "intel/strategy_memory.md"
    )
    if os.path.exists(memory_file):
        try:
            with open(memory_file) as f:
                content = f.read().strip()
            if content:
                context["strategy_memory"] = content
                logger.info(f"Read strategy memory ({len(content)} chars)")
        except IOError as e:
            logger.warning(f"Failed to read strategy memory: {e}")

    # Economic calendar
    try:
        from shared.event_calendar import get_all_upcoming_events

        events = get_all_upcoming_events(days_ahead=7)
        context["events"] = [
            {
                "type": e.event_type.value if hasattr(e.event_type, "value") else str(e.event_type),
                "description": e.description,
                "days_until": e.days_until,
                "date": str(e.event_date),
            }
            for e in events
        ]
        logger.info(f"Found {len(events)} upcoming events")
    except Exception as e:
        logger.warning(f"Failed to get economic calendar: {e}")
        context["events"] = []

    return context


def main():
    """Entry point for APOLLO morning scout."""
    logger.info("APOLLO starting morning scout")

    if not is_trading_day():
        return

    config = load_config()
    if not config:
        logger.error("No config loaded — aborting")
        sys.exit(1)

    from shared.market_hours import get_us_market_time

    today_str = get_us_market_time().strftime("%Y-%m-%d")

    # 1. Fetch market data
    from services.apollo.market_data import fetch_market_snapshot

    market_data = fetch_market_snapshot()

    # 2. Gather context
    context = gather_context(config)

    # 3. Get Claude client
    from shared.claude_client import get_anthropic_client

    client = get_anthropic_client(config)
    if not client:
        logger.error("Failed to create Anthropic client — aborting")
        sys.exit(1)

    # 4. Generate briefing
    from services.apollo.scout import generate_briefing

    result = generate_briefing(client, market_data, context, config)
    if not result:
        logger.error("Briefing generation failed")
        sys.exit(1)

    briefing, risk_level = result

    # 5. Save report
    report_dir = config.get("apollo", {}).get("report_dir", "intel/apollo")
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(report_dir, f"{today_str}.md")

    with open(report_path, "w") as f:
        f.write(briefing)
    logger.info(f"Briefing saved: {report_path} (risk: {risk_level})")

    # 6. Send alert with full briefing
    if config.get("alerts", {}).get("enabled", False):
        try:
            from shared.alert_service import AlertService, AlertType, AlertPriority

            priority_map = {
                "GREEN": AlertPriority.LOW,
                "YELLOW": AlertPriority.MEDIUM,
                "RED": AlertPriority.HIGH,
            }
            alert_service = AlertService(config, "APOLLO")
            alert_service.send_alert(
                alert_type=AlertType.DAILY_SUMMARY,
                title=f"APOLLO Morning Briefing [{risk_level}] — {today_str}",
                message=briefing,
                priority=priority_map.get(risk_level, AlertPriority.MEDIUM),
            )
            logger.info("Briefing alert sent")
        except Exception as e:
            logger.error(f"Failed to send alert: {e}")

    logger.info(f"APOLLO morning scout complete (risk: {risk_level})")


if __name__ == "__main__":
    main()
