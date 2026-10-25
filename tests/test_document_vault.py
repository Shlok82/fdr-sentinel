import os
import uuid
import hashlib
import pytest
from pathlib import Path
from app.core.config import settings
from app.services.document_service import (
    sanitize_filename,
    detect_mime_type_from_magic_bytes,
    validate_file_content,
    get_sharded_vault_rel_path,
    get_absolute_vault_path,
    DocumentService,
)
from app.models import Document, AuditLog
from app.services.clock_service import clock_service


def test_magic_bytes_sniffing_garbage_rejected():
    """File named .pdf containing garbage bytes is rejected with clear validation error."""
    garbage = b"This is arbitrary plain text data, not a valid PDF or image file."
    with pytest.raises(ValueError, match="Invalid file type"):
        detect_mime_type_from_magic_bytes(garbage)

    with pytest.raises(ValueError, match="Invalid file type"):
        validate_file_content(garbage, "suspicious.pdf")


def test_magic_bytes_sniffing_extension_mismatch():
    """File named .png containing PDF bytes is rejected with mismatch detection."""
    pdf_bytes = b"%PDF-1.4\n%Fake PDF content for testing extension check"
    with pytest.raises(ValueError, match="File format mismatch"):
        validate_file_content(pdf_bytes, "trick.png")


def test_valid_file_types_accepted():
    """Valid PDF, PNG, and JPEG files are detected and accepted."""
    pdf_bytes = b"%PDF-1.5 test content"
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF"

    mime_pdf, name_pdf = validate_file_content(pdf_bytes, "receipt.pdf")
    assert mime_pdf == "application/pdf"
    assert name_pdf == "receipt.pdf"

    mime_png, name_png = validate_file_content(png_bytes, "scan.png")
    assert mime_png == "image/png"
    assert name_png == "scan.png"

    mime_jpeg, name_jpeg = validate_file_content(jpeg_bytes, "photo.jpg")
    assert mime_jpeg == "image/jpeg"
    assert name_jpeg == "photo.jpg"


def test_empty_file_rejected():
    """Empty 0-byte file is rejected."""
    with pytest.raises(ValueError, match="empty"):
        validate_file_content(b"", "empty.pdf")


def test_oversize_file_rejected():
    """File exceeding MAX_UPLOAD_MB is rejected."""
    limit_mb = 1
    oversize = b"%PDF-" + b"A" * (limit_mb * 1024 * 1024 + 10)
    with pytest.raises(ValueError, match="exceeds maximum allowed limit"):
        validate_file_content(oversize, "large.pdf", max_mb=limit_mb)


def test_filename_sanitization():
    """Sanitizes path traversal, absolute paths, and control characters."""
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("..\\..\\Windows\\System32\\cmd.exe") == "cmd.exe"
    assert sanitize_filename("C:\\bad\\file.pdf") == "file.pdf"
    assert sanitize_filename("/var/tmp/upload.png") == "upload.png"
    assert sanitize_filename("test\x00null.pdf") == "testnull.pdf"
    assert sanitize_filename("...///strange.jpg") == "strange.jpg"
    assert sanitize_filename("") == "document.bin"
    assert sanitize_filename(None) == "document.bin"


def test_vault_directory_sharding_structure():
    """Stored file is at {VAULT_PATH}/{uuid[:2]}/{uuid[2:4]}/{uuid}.enc."""
    test_uuid = uuid.UUID("12345678-abcd-ef01-2345-6789abcdef01")
    rel_path = get_sharded_vault_rel_path(test_uuid)
    clean_hex = "12345678abcdef0123456789abcdef01"
    expected = f"12/34/{clean_hex}.enc"
    assert rel_path == expected


def test_plaintext_never_stored_in_vault(tmp_path):
    """Plaintext canaries must never be discoverable in any stored .enc ciphertext file."""
    canary = b"SUPER_SECRET_AICTE_CANARY_VALUE_998877665544332211"
    doc_id = uuid.uuid4()

    meta = DocumentService.process_and_encrypt_upload(
        file_bytes=b"%PDF-1.4\n" + canary,
        raw_filename="canary.pdf",
        document_id=doc_id,
        vault_base=str(tmp_path),
    )

    abs_path = meta["abs_path"]
    assert abs_path.exists()

    raw_vault_bytes = abs_path.read_bytes()
    assert canary not in raw_vault_bytes
    assert b"SUPER_SECRET" not in raw_vault_bytes


def test_document_integrity_sha256_mismatch(tmp_path):
    """If ciphertext or SHA-256 hash is tampered, read_and_decrypt_document raises ValueError."""
    plaintext = b"%PDF-1.4\nOriginal untampered document bytes"
    doc_id = uuid.uuid4()

    meta = DocumentService.process_and_encrypt_upload(
        file_bytes=plaintext,
        raw_filename="valid.pdf",
        document_id=doc_id,
        vault_base=str(tmp_path),
    )

    # Construct Document model with wrong expected hash
    doc = Document(
        id=doc_id,
        instrument_id=uuid.uuid4(),
        document_type="FDR_SCAN",
        storage_path=meta["storage_path"],
        file_name="valid.pdf",
        file_mime_type="application/pdf",
        file_size_bytes=len(plaintext),
        sha256_hash="0000000000000000000000000000000000000000000000000000000000000000",  # tampered
        encrypted_dek=meta["encrypted_dek"],
        encryption_iv=meta["encryption_iv"],
        encryption_tag=meta["encryption_tag"],
        created_at=clock_service.now(),
    )

    with pytest.raises(ValueError, match="Document integrity check failed"):
        DocumentService.read_and_decrypt_document(doc, vault_base=str(tmp_path))
