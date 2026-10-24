import uuid
from sqlalchemy import Column, String, Numeric, Integer, Date, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from app.models.base import Base


class Instrument(Base):
    __tablename__ = "instruments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    institution_id = Column(UUID(as_uuid=True), ForeignKey("institutions.id", ondelete="CASCADE"), index=True, nullable=False)
    fdr_number = Column(String(100), index=True, nullable=False)
    bank_name = Column(String(150), index=True, nullable=False)
    bank_branch = Column(String(150), nullable=False)
    ifsc_code = Column(String(20), nullable=False)
    principal_amount = Column(Numeric(14, 2), nullable=False)
    maturity_amount = Column(Numeric(14, 2), nullable=False)
    interest_rate_percent = Column(Numeric(5, 2), nullable=False)
    issue_date = Column(Date, nullable=False)
    maturity_date = Column(Date, index=True, nullable=False)
    tenure_months = Column(Integer, nullable=False)
    joint_account_holder = Column(String(255), nullable=False)
    status = Column(String(30), index=True, default="SUBMITTED", nullable=False)
    previous_instrument_id = Column(UUID(as_uuid=True), ForeignKey("instruments.id", ondelete="SET NULL"), nullable=True, index=True)
    renewal_sequence = Column(Integer, default=1, nullable=False)
    trust_score = Column(Integer, default=20, nullable=False)
    trust_score_breakdown = Column(JSONB, default=dict, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
