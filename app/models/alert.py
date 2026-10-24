import uuid
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from app.models.base import Base


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instrument_id = Column(UUID(as_uuid=True), ForeignKey("instruments.id", ondelete="CASCADE"), index=True, nullable=False)
    tier = Column(Integer, nullable=False)
    alert_type = Column(String(50), nullable=False)
    channel = Column(String(30), nullable=False)
    recipient_email = Column(String(150), nullable=False)
    sent_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    escalation_level_reached = Column(Integer, default=1, nullable=False)

    __table_args__ = (
        UniqueConstraint("instrument_id", "alert_type", name="uq_alerts_instrument_alert_type"),
    )
