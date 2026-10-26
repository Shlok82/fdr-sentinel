import uuid
from sqlalchemy import Column, String, Integer, Date, DateTime, Text, ForeignKey, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID
from app.models.base import Base


class SchedulerRun(Base):
    __tablename__ = "scheduler_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trigger = Column(String(30), nullable=False)
    business_date = Column(Date, nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=False)
    transitions_count = Column(Integer, default=0, nullable=False)
    alerts_created = Column(Integer, default=0, nullable=False)
    alerts_superseded = Column(Integer, default=0, nullable=False)
    instruments_examined = Column(Integer, default=0, nullable=False)
    instruments_skipped = Column(Integer, default=0, nullable=False)
    errors_count = Column(Integer, default=0, nullable=False)
    status = Column(String(20), nullable=False)
    error_summary = Column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "trigger IN ('SCHEDULED', 'STARTUP', 'TIME_MACHINE', 'MANUAL')",
            name="chk_scheduler_runs_trigger",
        ),
        CheckConstraint(
            "status IN ('SUCCESS', 'PARTIAL', 'FAILED')",
            name="chk_scheduler_runs_status",
        ),
    )

    @property
    def duration_ms(self) -> int:
        if self.finished_at and self.started_at:
            return int((self.finished_at - self.started_at).total_seconds() * 1000)
        return 0


class SchedulerState(Base):
    __tablename__ = "scheduler_state"

    id = Column(Integer, primary_key=True)
    last_tick_at = Column(DateTime(timezone=True), nullable=True)
    last_success_at = Column(DateTime(timezone=True), nullable=True)
    last_effective_run_id = Column(UUID(as_uuid=True), ForeignKey("scheduler_runs.id", ondelete="SET NULL"), nullable=True)

    __table_args__ = (
        CheckConstraint("id = 1", name="chk_scheduler_state_single_row"),
    )
