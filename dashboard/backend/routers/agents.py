"""Agent status and report endpoints."""

from fastapi import APIRouter, HTTPException

from dashboard.backend.config import settings
from dashboard.backend.services.agent_reports import AgentReportReader

router = APIRouter(prefix="/api/agents", tags=["agents"])

report_reader = AgentReportReader(settings.agent_intel_dir)


@router.get("/status")
async def get_agent_status():
    """All agent last-run times and availability."""
    return {"agents": report_reader.get_all_agent_status()}


@router.get("/report/{agent}/{date_str}")
async def get_report(agent: str, date_str: str):
    """Get a specific agent's report for a date."""
    report = report_reader.get_report_for_date(agent, date_str)
    if report is None:
        raise HTTPException(status_code=404, detail=f"No {agent} report for {date_str}")
    return report


@router.get("/report/{agent}")
async def get_latest_report(agent: str):
    """Get the most recent report for an agent."""
    report = report_reader.get_latest_report(agent)
    if report is None:
        raise HTTPException(status_code=404, detail=f"No reports found for {agent}")
    return report
