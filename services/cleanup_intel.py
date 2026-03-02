"""
Shared retention cleanup for agent reports.

Called by CLIO after weekly analysis. Deletes reports older than
the configured retention period.

Usage:
    from services.cleanup_intel import cleanup_old_reports
    cleanup_old_reports(config)
"""

import logging
import os
from datetime import datetime, timedelta
from glob import glob
from typing import Any, Dict

logger = logging.getLogger(__name__)

# Default retention periods (days)
DEFAULT_RETENTION = {
    "hermes": 90,
    "apollo": 30,
    "argus": 90,
}


def cleanup_old_reports(config: Dict[str, Any]):
    """
    Delete agent reports older than retention period.

    Args:
        config: Agent config with clio.retention_days overrides.
    """
    retention = config.get("clio", {}).get("retention_days", {})

    # Merge with defaults
    periods = {**DEFAULT_RETENTION, **retention}

    for agent, days in periods.items():
        agent_dir = config.get(agent, {}).get("report_dir", f"intel/{agent}")
        if not os.path.exists(agent_dir):
            continue

        cutoff = datetime.now() - timedelta(days=days)
        deleted = 0

        for filepath in glob(os.path.join(agent_dir, "*")):
            if os.path.isfile(filepath) and not filepath.endswith(".gitkeep"):
                try:
                    mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
                    if mtime < cutoff:
                        os.remove(filepath)
                        deleted += 1
                except OSError as e:
                    logger.warning(f"Failed to remove {filepath}: {e}")

        if deleted > 0:
            logger.info(f"Cleaned up {deleted} old {agent} reports (>{days} days)")

    # Clean up ARGUS incidents separately
    incident_dir = config.get("argus", {}).get("incident_dir", "intel/argus/incidents")
    if os.path.exists(incident_dir):
        cutoff = datetime.now() - timedelta(days=periods.get("argus", 90))
        deleted = 0
        for filepath in glob(os.path.join(incident_dir, "*")):
            if os.path.isfile(filepath):
                try:
                    mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
                    if mtime < cutoff:
                        os.remove(filepath)
                        deleted += 1
                except OSError as e:
                    logger.warning(f"Failed to remove {filepath}: {e}")
        if deleted > 0:
            logger.info(f"Cleaned up {deleted} old argus incidents (>{periods.get('argus', 90)} days)")
