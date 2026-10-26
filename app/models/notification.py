import uuid
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.models.base import Base


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alert_id = Column(UUID(as_uuid=True), ForeignKey("alerts.id", ondelete="CASCADE"), index=True, nullable=False)
    audience_role = Column(String(50), nullable=False)
    audience_institution_id = Column(UUID(as_uuid=True), ForeignKey("institutions.id", ondelete="CASCADE"), nullable=True)
    subject = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)
    sms_text = Column(String(320), nullable=False)
    channels = Column(JSONB, default=lambda: ["IN_APP", "EMAIL", "SMS"], nullable=False)
    language = Column(String(10), default="en", nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    read_at = Column(DateTime(timezone=True), nullable=True)
    read_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    alert = relationship("Alert", back_populates="notifications")

    __table_args__ = (
        CheckConstraint(
            "audience_role IN ('INSTITUTION_ADMIN', 'SCRUTINY_OFFICER', 'APPROVER_AUTHORITY')",
            name="chk_notifications_audience_role",
        ),
        CheckConstraint(
            "(audience_role = 'INSTITUTION_ADMIN' AND audience_institution_id IS NOT NULL) OR (audience_role != 'INSTITUTION_ADMIN' AND audience_institution_id IS NULL)",
            name="chk_notifications_audience_institution",
        ),
    )
