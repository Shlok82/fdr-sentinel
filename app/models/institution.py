import uuid
from sqlalchemy import Column, String, Text, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from app.models.base import Base


class Institution(Base):
    __tablename__ = "institutions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    aicte_permanent_id = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(String(255), nullable=False)
    region = Column(String(50), index=True, nullable=False)
    state = Column(String(50), nullable=False)
    address = Column(Text, nullable=False)
    contact_email = Column(String(150), nullable=False)
    contact_phone = Column(String(20), nullable=False)
    institution_type = Column(String(50), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
