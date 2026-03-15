"""Market status using shared/market_hours.py (safe import — no side effects)."""

import logging
import sys
from pathlib import Path

logger = logging.getLogger("dashboard.market_status")

# Add CALYPSO root to path so we can import shared modules
_calypso_root = Path(__file__).resolve().parents[3]  # dashboard/backend/services -> repo root
if str(_calypso_root) not in sys.path:
    sys.path.insert(0, str(_calypso_root))

try:
    from shared.market_hours import (
        get_trading_session,
        is_market_open,
        is_weekend,
        is_market_holiday,
        get_next_market_open,
        is_early_close_day,
        get_us_market_time,
        get_holiday_name,
        get_early_close_reason,
    )
    from shared.event_calendar import (
        is_fomc_meeting_day,
        is_fomc_announcement_day,
        is_fomc_t_plus_one,
        get_next_fomc_date,
    )
    MARKET_HOURS_AVAILABLE = True
except ImportError:
    logger.warning("shared.market_hours not available — using fallback market status")
    MARKET_HOURS_AVAILABLE = False


def get_today_et() -> str:
    """Get today's date in Eastern Time as YYYY-MM-DD string.

    HYDRA and HOMER file all data under ET dates. The VM runs in UTC.
    After 7 PM ET (midnight UTC), date.today() returns tomorrow in UTC
    but trading data is still under today's ET date.
    """
    if MARKET_HOURS_AVAILABLE:
        return get_us_market_time().strftime("%Y-%m-%d")
    # Fallback: UTC (wrong after 7 PM ET, but better than nothing)
    from datetime import date
    return date.today().isoformat()


def get_current_status() -> dict:
    """Get current market status info."""
    if not MARKET_HOURS_AVAILABLE:
        return _fallback_status()

    try:
        session = get_trading_session()
        market_open = is_market_open()
        trading_day = not is_weekend() and not is_market_holiday()
        early_close = is_early_close_day()

        result = {
            "session": session if isinstance(session, str) else str(session),
            "is_open": market_open,
            "is_trading_day": trading_day,
            "is_early_close": early_close,
        }

        # Next market open info
        try:
            next_open_dt, hours_until = get_next_market_open()
            result["next_event"] = {
                "next_open": next_open_dt.isoformat() if next_open_dt else None,
                "hours_until_open": hours_until,
            }
        except Exception:
            pass

        # Holiday context
        try:
            result["holiday_name"] = get_holiday_name() if not trading_day else None
            result["early_close_reason"] = get_early_close_reason() if early_close else None
        except Exception:
            result["holiday_name"] = None
            result["early_close_reason"] = None

        # FOMC context
        try:
            today = get_us_market_time().date()
            result["is_fomc_day"] = is_fomc_meeting_day(today)
            result["is_fomc_announcement"] = is_fomc_announcement_day(today)
            result["is_fomc_t_plus_one"] = is_fomc_t_plus_one(today)
            next_fomc = get_next_fomc_date(today)
            if next_fomc:
                result["next_fomc"] = next_fomc.isoformat()
                result["days_until_fomc"] = (next_fomc - today).days
            else:
                result["next_fomc"] = None
                result["days_until_fomc"] = None
        except Exception:
            result["is_fomc_day"] = False
            result["is_fomc_announcement"] = False
            result["is_fomc_t_plus_one"] = False
            result["next_fomc"] = None
            result["days_until_fomc"] = None

        return result

    except Exception as e:
        logger.warning(f"Error getting market status: {e}")
        return _fallback_status()


def _fallback_status() -> dict:
    """Basic fallback when shared module isn't available."""
    return {
        "session": "unknown",
        "is_open": False,
        "is_trading_day": False,
        "is_early_close": False,
    }
