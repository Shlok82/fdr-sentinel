from sqlalchemy import Column, Integer, BigInteger, Boolean, DateTime
from sqlalchemy.sql import func
from app.models.base import Base


class ClockState(Base):
    __tablename__ = "clock_state"

    id = Column(Integer, primary_key=True, default=1)
    offset_seconds = Column(BigInteger, default=0, nullable=False)
    is_simulated = Column(Boolean, default=False, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
