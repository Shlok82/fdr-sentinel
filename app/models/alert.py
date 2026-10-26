import uuid
from sqlalchemy import Column, String, Integer, Date, DateTime, ForeignKey, UniqueConstraint, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.models.base import Base


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instrument_id = Column(UUID(as_uuid=True), ForeignKey("instruments.id", ondelete="CASCADE"), index=True, nullable=False)
    tier = Column(Integer, nullable=True)
    alert_type = Column(String(50), nullable=False)
    channel = Column(String(30), nullable=True)
    recipient_email = Column(String(150), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    escalation_level_reached = Column(Integer, default=1, nullable=True)
    severity = Column(String(20), default="INFO", nullable=False)
    status = Column(String(20), default="OPEN", nullable=False)
    business_date = Column(Date, nullable=False)
    details = Column(JSONB, default=dict, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)

    notifications = relationship("Notification", back_populates="alert", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("instrument_id", "alert_type", name="uq_alerts_instrument_alert_type"),
        CheckConstraint("severity IN ('INFO', 'WARNING', 'URGENT', 'CRITICAL')", name="chk_alerts_severity"),
        CheckConstraint("status IN ('OPEN', 'SUPERSEDED')", name="chk_alerts_status"),
    )
