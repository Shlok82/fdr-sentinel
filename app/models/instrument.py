import uuid
from sqlalchemy import Column, String, Numeric, Integer, Date, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.models.base import Base


class Instrument(Base):
    __tablename__ = "instruments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    institution_id = Column(UUID(as_uuid=True), ForeignKey("institutions.id", ondelete="CASCADE"), index=True, nullable=False)
    fdr_number = Column(String(100), index=True, nullable=True)
    bank_name = Column(String(150), index=True, nullable=True)
    bank_branch = Column(String(150), nullable=True)
    ifsc_code = Column(String(20), nullable=True)
    principal_amount = Column(Numeric(14, 2), nullable=True)
    maturity_amount = Column(Numeric(14, 2), nullable=True)
    interest_rate_percent = Column(Numeric(5, 2), nullable=True)
    issue_date = Column(Date, nullable=True)
    maturity_date = Column(Date, index=True, nullable=True)
    tenure_months = Column(Integer, nullable=True)
    joint_account_holder = Column(String(255), nullable=True)
    status = Column(String(30), index=True, default="SUBMITTED", nullable=False)
    prior_state = Column(String(30), nullable=True)
    previous_instrument_id = Column(UUID(as_uuid=True), ForeignKey("instruments.id", ondelete="SET NULL"), nullable=True, index=True)
    renewal_sequence = Column(Integer, default=1, nullable=False)
    trust_score = Column(Integer, default=20, nullable=False)
    trust_score_breakdown = Column(JSONB, default=dict, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('SUBMITTED', 'UNDER_SCRUTINY', 'DISCREPANCY_FLAGGED', 'VERIFIED', 'ACTIVE', 'EXPIRING', 'MATURED', 'RENEWED', 'RELEASE_REQUESTED', 'RELEASED', 'REJECTED')",
            name="ck_instruments_status_fsm",
        ),
    )
