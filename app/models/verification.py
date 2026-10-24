import uuid
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from app.models.base import Base


class VerificationRecord(Base):
    __tablename__ = "verification_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instrument_id = Column(UUID(as_uuid=True), ForeignKey("instruments.id", ondelete="CASCADE"), index=True, nullable=False)
    provider_type = Column(String(50), nullable=False)
    raw_response = Column(JSONB, nullable=True)
    is_verified = Column(Boolean, default=False, nullable=False)
    mismatch_flags = Column(JSONB, default=list, nullable=False)
    executed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
