import uuid
from sqlalchemy import Column, String, Text, Numeric, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from app.models.base import Base


class FieldProvenance(Base):
    __tablename__ = "field_provenance"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instrument_id = Column(UUID(as_uuid=True), ForeignKey("instruments.id", ondelete="CASCADE"), index=True, nullable=False)
    field_name = Column(String(50), nullable=False)
    submitted_value = Column(Text, nullable=True)
    current_value = Column(Text, nullable=True)
    provenance_type = Column(String(30), nullable=False)
    verified_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    confidence_score = Column(Numeric(4, 3), nullable=True)
