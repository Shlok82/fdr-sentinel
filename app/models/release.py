import uuid
from sqlalchemy import Column, String, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.models.base import Base


class ReleaseRequest(Base):
    __tablename__ = "release_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instrument_id = Column(UUID(as_uuid=True), ForeignKey("instruments.id", ondelete="CASCADE"), index=True, nullable=False)
    institution_justification = Column(Text, nullable=False)
    statutory_checklist = Column(JSONB, default=dict, nullable=False)
    evidence_document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    scrutiny_recommendation = Column(String(30), nullable=True)
    scrutiny_officer_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    authority_decision = Column(String(30), default="PENDING", nullable=False)
    approver_authority_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    ed25519_signature = Column(Text, nullable=True)
    decision_timestamp = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)
