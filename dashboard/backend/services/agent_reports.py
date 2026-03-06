"""Read agent reports (HERMES, APOLLO, CLIO, HOMER, ARGUS)."""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("dashboard.agent_reports")

AGENTS = ["apollo", "hermes", "homer", "clio", "argus"]


class AgentReportReader:
    """Read agent intel reports from the filesystem."""

    def __init__(self, intel_dir: Path):
        self.intel_dir = intel_dir

    def get_latest_report(self, agent_name: str) -> Optional[dict]:
        """Find the most recent report for an agent."""
        agent_name = agent_name.lower()
        agent_dir = self.intel_dir / agent_name

        if not agent_dir.exists():
            return None

        # Find markdown files sorted by name (date-based naming)
        md_files = sorted(agent_dir.glob("*.md"), reverse=True)
        if not md_files:
            return None

        latest = md_files[0]
        try:
            content = latest.read_text(encoding="utf-8")
            return {
                "agent": agent_name,
                "filename": latest.name,
                "date": latest.stem,  # Filename without extension (usually YYYY-MM-DD)
                "content": content,
                "modified": datetime.fromtimestamp(latest.stat().st_mtime).isoformat(),
            }
        except OSError as e:
            logger.warning(f"Error reading {agent_name} report: {e}")
            return None

    def get_report_for_date(self, agent_name: str, date_str: str) -> Optional[dict]:
        """Get a specific date's report."""
        agent_name = agent_name.lower()
        agent_dir = self.intel_dir / agent_name

        # Try common filename patterns
        for pattern in [f"{date_str}.md", f"{date_str}*.md"]:
            matches = list(agent_dir.glob(pattern))
            if matches:
                f = matches[0]
                try:
                    return {
                        "agent": agent_name,
                        "filename": f.name,
                        "date": date_str,
                        "content": f.read_text(encoding="utf-8"),
                        "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                    }
                except OSError:
                    pass

        return None

    def get_all_agent_status(self) -> list[dict]:
        """Get last-run status for all agents."""
        statuses = []
        for agent in AGENTS:
            agent_dir = self.intel_dir / agent
            if not agent_dir.exists():
                statuses.append({
                    "agent": agent,
                    "last_run": None,
                    "last_file": None,
                    "available": False,
                })
                continue

            md_files = sorted(agent_dir.glob("*.md"), reverse=True)
            if md_files:
                latest = md_files[0]
                try:
                    mtime = datetime.fromtimestamp(latest.stat().st_mtime)
                    statuses.append({
                        "agent": agent,
                        "last_run": mtime.isoformat(),
                        "last_file": latest.name,
                        "available": True,
                    })
                except OSError:
                    statuses.append({
                        "agent": agent,
                        "last_run": None,
                        "last_file": None,
                        "available": False,
                    })
            else:
                statuses.append({
                    "agent": agent,
                    "last_run": None,
                    "last_file": None,
                    "available": True,  # Directory exists but no reports yet
                })

        return statuses
