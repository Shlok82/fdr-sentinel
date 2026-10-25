import uuid
from sqlalchemy import Column, String, Boolean, Text, DateTime, ForeignKey, Index, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.models.base import Base


class DiscrepancyTicket(Base):
    __tablename__ = "discrepancy_tickets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instrument_id = Column(
        UUID(as_uuid=True),
        ForeignKey("instruments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rule_code = Column(String(20), nullable=False, index=True)
    severity = Column(String(20), nullable=False)
    blocking = Column(Boolean, nullable=False, default=False)
    status = Column(String(20), nullable=False, default="OPEN", index=True)
    message = Column(Text, nullable=False)
    staff_detail = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "severity IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW')",
            name="ck_discrepancy_tickets_severity",
        ),
        CheckConstraint(
            "status IN ('OPEN', 'RESOLVED')",
            name="ck_discrepancy_tickets_status",
        ),
        Index(
            "uq_open_ticket_instrument_rule",
            "instrument_id",
            "rule_code",
            unique=True,
            postgresql_where=(Column("status") == "OPEN"),
        ),
    )
