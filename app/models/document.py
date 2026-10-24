import uuid
from sqlalchemy import Column, String, BigInteger, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from app.models.base import Base


class Document(Base):
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instrument_id = Column(UUID(as_uuid=True), ForeignKey("instruments.id", ondelete="CASCADE"), index=True, nullable=False)
    document_type = Column(String(50), nullable=False)
    storage_path = Column(String(500), nullable=False)
    file_name = Column(String(255), nullable=False)
    file_mime_type = Column(String(100), nullable=False)
    file_size_bytes = Column(BigInteger, nullable=False)
    sha256_hash = Column(String(64), nullable=False)
    encrypted_dek = Column(Text, nullable=False)
    encryption_iv = Column(String(32), nullable=False)
    encryption_tag = Column(String(32), nullable=False)
    extracted_payload = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
