from sqlalchemy import Column, Integer, BigInteger, DateTime, CheckConstraint
from app.models.base import Base


class ClockState(Base):
    __tablename__ = "clock_state"

    id = Column(Integer, primary_key=True, default=1)
    offset_seconds = Column(BigInteger, default=0, nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("id = 1", name="ck_clock_state_single_row"),
    )
