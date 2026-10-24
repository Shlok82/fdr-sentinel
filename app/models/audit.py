from sqlalchemy import Column, BigInteger, String, DateTime
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from app.models.base import Base


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    event_timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    entity_type = Column(String(50), nullable=False, index=True)
    entity_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    action = Column(String(50), nullable=False, index=True)
    actor_user_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    actor_ip_address = Column(String(45), nullable=True)
    canonical_payload = Column(JSONB, nullable=False)
    previous_hash = Column(String(64), nullable=False)
    entry_hash = Column(String(64), nullable=False, index=True)
