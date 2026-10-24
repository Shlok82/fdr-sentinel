from datetime import datetime, date, timezone, timedelta
from typing import Optional


class ClockService:
    """Unified clock service for FDR Sentinel.
    
    All temporal and date calculations across the application (backend services,
    API routes, database queries, and scheduler jobs) MUST utilize this service
    rather than system time directly or SQL CURRENT_DATE/NOW().
    """
    _instance: Optional["ClockService"] = None

    def __init__(self, offset_seconds: int = 0, is_simulated: bool = False):
        self._offset_seconds = offset_seconds
        self._is_simulated = is_simulated

    @classmethod
    def get_instance(cls) -> "ClockService":
        if cls._instance is None:
            cls._instance = ClockService()
        return cls._instance

    def set_offset(self, seconds: int, is_simulated: bool = True) -> None:
        self._offset_seconds = seconds
        self._is_simulated = is_simulated

    def reset(self) -> None:
        self._offset_seconds = 0
        self._is_simulated = False

    @property
    def is_simulated(self) -> bool:
        return self._is_simulated

    @property
    def offset_seconds(self) -> int:
        return self._offset_seconds

    def get_current_time(self) -> datetime:
        """Returns the current application time in UTC."""
        utc_now = datetime.now(timezone.utc)
        if self._offset_seconds != 0:
            utc_now = utc_now + timedelta(seconds=self._offset_seconds)
        return utc_now

    def get_current_date(self) -> date:
        """Returns the current application date in UTC."""
        return self.get_current_time().date()


# Default singleton instance
clock_service = ClockService.get_instance()
