import uuid
from datetime import datetime, date, timezone, timedelta
from typing import Optional
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.config import settings

SYSTEM_CLOCK_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class ClockService:
    """Injected singleton clock service: the SOLE temporal authority for FDR Sentinel.
    
    Single-Process Assumption:
    In accordance with the unified FastAPI architecture (SPEC MVP Addendum), the application
    and its temporal scheduler run as a single process. The persisted offset from the single-row
    'clock_state' table (id=1) is loaded on startup/first use and cached in memory.
    
    All application logic, API routes, database queries, and scheduler tasks MUST query
    ClockService.now() and ClockService.today(). Direct hardware clock access is forbidden
    across the codebase and enforced via automated static grep verification.
    """
    _instance: Optional["ClockService"] = None

    def __init__(self, initial_offset_seconds: int = 0):
        self._offset_seconds: int = initial_offset_seconds

    @classmethod
    def get_instance(cls) -> "ClockService":
        if cls._instance is None:
            cls._instance = ClockService()
        return cls._instance

    @property
    def offset_seconds(self) -> int:
        return self._offset_seconds

    @property
    def is_simulated(self) -> bool:
        return self._offset_seconds != 0

    def load_offset_from_db(self, session: Session) -> int:
        """Loads and caches the persistent offset from the clock_state table."""
        try:
            row = session.execute(
                text("SELECT offset_seconds FROM clock_state WHERE id = 1")
            ).fetchone()
            if row is not None:
                self._offset_seconds = int(row[0])
            else:
                # Seed single row if missing
                current_hw = datetime.now(timezone.utc)  # clock-allow
                session.execute(
                    text("INSERT INTO clock_state (id, offset_seconds, updated_at) VALUES (1, 0, :ts) ON CONFLICT (id) DO NOTHING"),
                    {"ts": current_hw}
                )
                session.commit()
                self._offset_seconds = 0
        except Exception:
            # Fall back to cached offset if database is initializing
            pass
        return self._offset_seconds

    def real_now(self) -> datetime:
        """Returns the real hardware UTC time with NO simulated offset.
        
        CRITICAL ARCHITECTURAL CONSTRAINT:
        This method is strictly reserved for session/token lifetimes (JWT iat/exp)
        and rate-limiting/throttling. It MUST NEVER be used for regulatory business
        logic, temporal alerts, or audit log timestamps.
        """
        return datetime.now(timezone.utc)  # clock-allow

    def now(self) -> datetime:
        """Returns the authoritative application time (UTC) with simulated offset applied."""
        real_utc = datetime.now(timezone.utc)  # clock-allow
        if self._offset_seconds != 0:
            return real_utc + timedelta(seconds=self._offset_seconds)
        return real_utc

    def to_local(self, dt: Optional[datetime]) -> Optional[datetime]:
        """Converts naive (assumed UTC) or aware datetime to the configured DISPLAY_TIMEZONE."""
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(settings.display_tz)

    def now_local(self) -> datetime:
        """Returns the authoritative application time converted to the configured DISPLAY_TIMEZONE."""
        return self.to_local(self.now())

    def today(self) -> date:
        """Returns the authoritative application calendar date in DISPLAY_TIMEZONE."""
        return self.now_local().date()

    # Backwards-compatible aliases
    def get_current_time(self) -> datetime:
        return self.now()

    def get_current_date(self) -> date:
        return self.today()

    def advance(
        self,
        session: Session,
        days: int,
        *,
        actor_user_id: Optional[uuid.UUID | str] = None,
        actor_role: Optional[str] = "DEMO_ADMIN"
    ) -> int:
        """Advances application time by N days.
        
        Mandates DEMO_MODE=true. Persists to clock_state and writes an audit log entry.
        """
        if not isinstance(days, int) or days <= 0 or days > 3650:
            raise ValueError(f"Days to advance must be a positive integer between 1 and 3650 (got {days})")

        if not settings.DEMO_MODE:
            raise PermissionError("Clock advancement is strictly disabled when DEMO_MODE is false.")

        from app.services.audit_service import AuditService

        # Lock single row for update
        row = session.execute(
            text("SELECT offset_seconds FROM clock_state WHERE id = 1 FOR UPDATE")
        ).fetchone()
        old_offset = int(row[0]) if row else 0

        added_seconds = days * 86400
        new_offset = old_offset + added_seconds

        # Persist new offset and updated timestamp
        session.execute(
            text("UPDATE clock_state SET offset_seconds = :new_offset, updated_at = :ts WHERE id = 1"),
            {"new_offset": new_offset, "ts": self.now()}
        )

        # Update cached in-memory offset
        self._offset_seconds = new_offset

        # Record tamper-evident audit trail
        AuditService.append(
            session,
            entity_type="SYSTEM",
            entity_id=SYSTEM_CLOCK_ID,
            action="CLOCK_ADVANCE",
            actor_user_id=actor_user_id,
            actor_role=actor_role if actor_user_id else "SYSTEM",
            details={
                "old_offset_seconds": old_offset,
                "new_offset_seconds": new_offset,
                "days_advanced": days,
            }
        )

        return new_offset

    def reset(
        self,
        session: Session,
        *,
        actor_user_id: Optional[uuid.UUID | str] = None,
        actor_role: Optional[str] = "DEMO_ADMIN"
    ) -> int:
        """Resets the application clock back to real system time (offset = 0).
        
        Mandates DEMO_MODE=true. Persists to clock_state and writes an audit log entry.
        """
        if not settings.DEMO_MODE:
            raise PermissionError("Clock reset is strictly disabled when DEMO_MODE is false.")

        from app.services.audit_service import AuditService

        row = session.execute(
            text("SELECT offset_seconds FROM clock_state WHERE id = 1 FOR UPDATE")
        ).fetchone()
        old_offset = int(row[0]) if row else 0

        # Persist zero offset
        session.execute(
            text("UPDATE clock_state SET offset_seconds = 0, updated_at = :ts WHERE id = 1"),
            {"ts": self.now()}
        )

        self._offset_seconds = 0

        # Record tamper-evident audit trail
        AuditService.append(
            session,
            entity_type="SYSTEM",
            entity_id=SYSTEM_CLOCK_ID,
            action="CLOCK_RESET",
            actor_user_id=actor_user_id,
            actor_role=actor_role if actor_user_id else "SYSTEM",
            details={
                "old_offset_seconds": old_offset,
                "new_offset_seconds": 0,
            }
        )

        return 0


# Default global singleton instance
clock_service = ClockService.get_instance()
