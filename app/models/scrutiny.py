import uuid
from sqlalchemy import Column, String, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from app.models.base import Base


class ScrutinyNote(Base):
    __tablename__ = "scrutiny_notes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instrument_id = Column(UUID(as_uuid=True), ForeignKey("instruments.id", ondelete="CASCADE"), index=True, nullable=False)
    officer_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True)
    note_text = Column(Text, nullable=False)
    action_recommended = Column(String(30), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
