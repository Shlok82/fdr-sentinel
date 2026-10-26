import uuid
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.models.base import Base


class ReleaseRequest(Base):
    __tablename__ = "release_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instrument_id = Column(UUID(as_uuid=True), ForeignKey("instruments.id", ondelete="RESTRICT"), index=True, nullable=False)
    requested_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    requested_at = Column(DateTime(timezone=True), nullable=True)
    institution_justification = Column(Text, nullable=False)
    statutory_checklist = Column(JSONB, default=dict, nullable=False)
    evidence_document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    scrutiny_recommendation = Column(String(30), nullable=True)
    scrutiny_officer_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    scrutiny_justification = Column(Text, nullable=True)
    scrutiny_recorded_at = Column(DateTime(timezone=True), nullable=True)
    scrutiny_evidence_document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    authority_decision = Column(String(30), default="PENDING", nullable=False)
    approver_authority_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    ed25519_signature = Column(Text, nullable=True)
    decision_timestamp = Column(DateTime(timezone=True), nullable=True)
    authority_justification = Column(Text, nullable=True)
    release_order_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    instrument = relationship("Instrument", backref="release_requests", foreign_keys=[instrument_id])
    requested_by = relationship("User", foreign_keys=[requested_by_user_id])
    scrutiny_officer = relationship("User", foreign_keys=[scrutiny_officer_id])
    approver_authority = relationship("User", foreign_keys=[approver_authority_id])
    scrutiny_evidence_document = relationship("Document", foreign_keys=[scrutiny_evidence_document_id])

    __table_args__ = (
        CheckConstraint(
            "authority_decision IN ('PENDING', 'APPROVED', 'REJECTED')",
            name="ck_release_requests_authority_decision",
        ),
        CheckConstraint(
            "scrutiny_recommendation IS NULL OR scrutiny_recommendation IN ('RECOMMENDED', 'NOT_RECOMMENDED')",
            name="ck_release_requests_scrutiny_rec",
        ),
    )
