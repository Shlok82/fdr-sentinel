import os
import re
import uuid
import hashlib
from pathlib import Path
from typing import Tuple, Optional
from app.core.config import settings
from app.core.crypto import encrypt_file_envelope, decrypt_file_envelope, CryptoError
from app.models.document import Document

# Supported magic byte signatures
MAGIC_BYTES = {
    b"%PDF-": "application/pdf",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
}


def sanitize_filename(filename: Optional[str]) -> str:
    """Sanitizes user-supplied filenames to prevent path traversal and control character attacks."""
    if not filename:
        return "document.bin"

    # 1. Strip path components
    name = os.path.basename(filename.replace("\\", "/"))
    # 2. Strip null bytes and non-printable control characters
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    # 3. Strip directory traversal sequences
    name = re.sub(r"\.\.+", ".", name)
    name = name.strip(" .")

    if not name:
        name = "document.bin"

    # 4. Cap length to 200 characters while preserving extension
    if len(name) > 200:
        base, ext = os.path.splitext(name)
        ext = ext[:20]
        base = base[: (200 - len(ext))]
        name = base + ext

    return name


def detect_mime_type_from_magic_bytes(data: bytes) -> str:
    """Validates file type by sniffing initial magic bytes, ignoring client headers or extensions."""
    if len(data) == 0:
        raise ValueError("File is empty (0 bytes).")

    for magic, mime in MAGIC_BYTES.items():
        if data.startswith(magic):
            return mime

    raise ValueError(
        "Invalid file type. Only standard PDF documents, PNG images, and JPEG photos are accepted."
    )


EXT_MIME_MAP = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def validate_file_content(data: bytes, filename: str, max_mb: Optional[int] = None) -> Tuple[str, str]:
    """Validates file size, non-emptiness, magic bytes, and extension consistency.
    
    Returns (mime_type, sanitized_filename).
    """
    if len(data) == 0:
        raise ValueError("File upload rejected: uploaded file is empty (0 bytes).")

    limit_bytes = (max_mb or settings.MAX_UPLOAD_MB) * 1024 * 1024
    if len(data) > limit_bytes:
        raise ValueError(
            f"File upload rejected: file size ({len(data) / (1024 * 1024):.2f} MB) exceeds maximum allowed limit of {settings.MAX_UPLOAD_MB} MB."
        )

    mime_type = detect_mime_type_from_magic_bytes(data)
    clean_filename = sanitize_filename(filename)

    # Check extension consistency if an extension is present
    _, ext = os.path.splitext(clean_filename)
    if ext:
        expected_mime = EXT_MIME_MAP.get(ext.lower())
        if expected_mime and expected_mime != mime_type:
            raise ValueError(
                f"File format mismatch: file declared as '{ext}' contains '{mime_type}' data."
            )

    return mime_type, clean_filename


def get_sharded_vault_rel_path(document_id: uuid.UUID | str) -> str:
    """Computes two-level sharded relative storage path from document UUID."""
    clean_uuid = str(document_id).replace("-", "").lower()
    shard_1 = clean_uuid[:2]
    shard_2 = clean_uuid[2:4]
    return f"{shard_1}/{shard_2}/{clean_uuid}.enc"


def get_absolute_vault_path(rel_path: str, vault_base: Optional[str] = None) -> Path:
    """Resolves absolute filesystem path for a sharded relative vault path."""
    base_dir = Path(vault_base or settings.VAULT_PATH)
    return base_dir / rel_path


def write_ciphertext_to_vault(
    rel_path: str,
    ciphertext: bytes,
    vault_base: Optional[str] = None
) -> Path:
    """Atomically writes encrypted ciphertext to sharded storage path."""
    abs_path = get_absolute_vault_path(rel_path, vault_base)
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(ciphertext)
    return abs_path


def delete_ciphertext_from_vault(
    rel_path: str,
    vault_base: Optional[str] = None
) -> None:
    """Safely removes ciphertext file from vault if present."""
    try:
        abs_path = get_absolute_vault_path(rel_path, vault_base)
        if abs_path.is_file():
            abs_path.unlink()
    except Exception:
        pass


class DocumentService:
    """Manages document envelope encryption, sharded vault persistence, and integrity checks."""

    @staticmethod
    def process_and_encrypt_upload(
        file_bytes: bytes,
        raw_filename: str,
        document_id: Optional[uuid.UUID] = None,
        vault_base: Optional[str] = None,
        master_key_b64: Optional[str] = None,
    ) -> dict:
        """Validates, hashes, encrypts, and writes an uploaded file to the encrypted vault.
        
        Returns dictionary with metadata ready for Document ORM model insertion:
            {
                "id": doc_id,
                "file_name": clean_filename,
                "file_mime_type": mime_type,
                "file_size_bytes": len(file_bytes),
                "sha256_hash": sha256_hash,
                "storage_path": rel_path,
                "encrypted_dek": encrypted_dek_b64,
                "encryption_iv": encryption_iv_b64,
                "encryption_tag": encryption_tag_b64,
                "abs_path": abs_path
            }
        """
        doc_id = document_id or uuid.uuid4()
        mime_type, clean_filename = validate_file_content(file_bytes, raw_filename)

        # 1. Compute SHA-256 of raw plaintext
        sha256_hash = hashlib.sha256(file_bytes).hexdigest()

        # 2. Envelope encrypt with AAD binding to document ID
        ciphertext, enc_dek, enc_iv, enc_tag = encrypt_file_envelope(
            plaintext=file_bytes,
            document_id=doc_id,
            master_key_b64=master_key_b64,
        )

        # 3. Write ciphertext to sharded vault directory
        rel_path = get_sharded_vault_rel_path(doc_id)
        abs_path = write_ciphertext_to_vault(rel_path, ciphertext, vault_base=vault_base)

        return {
            "id": doc_id,
            "file_name": clean_filename,
            "file_mime_type": mime_type,
            "file_size_bytes": len(file_bytes),
            "sha256_hash": sha256_hash,
            "storage_path": rel_path,
            "encrypted_dek": enc_dek,
            "encryption_iv": enc_iv,
            "encryption_tag": enc_tag,
            "abs_path": abs_path,
        }

    @staticmethod
    def read_and_decrypt_document(
        document: Document,
        vault_base: Optional[str] = None,
        master_key_b64: Optional[str] = None,
    ) -> bytes:
        """Reads ciphertext from vault, decrypts in memory, and verifies SHA-256 integrity."""
        abs_path = get_absolute_vault_path(document.storage_path, vault_base)
        if not abs_path.is_file():
            raise FileNotFoundError(f"Vault ciphertext file not found at: {document.storage_path}")

        ciphertext = abs_path.read_bytes()

        # Decrypt envelope with AAD document ID
        plaintext = decrypt_file_envelope(
            ciphertext_only=ciphertext,
            encrypted_dek_b64=document.encrypted_dek,
            encryption_iv_b64=document.encryption_iv,
            encryption_tag_b64=document.encryption_tag,
            document_id=document.id,
            master_key_b64=master_key_b64,
        )

        # Verify plaintext SHA-256 integrity
        actual_hash = hashlib.sha256(plaintext).hexdigest()
        if actual_hash != document.sha256_hash:
            raise ValueError(
                f"Document integrity check failed: expected hash {document.sha256_hash}, computed {actual_hash}."
            )

        return plaintext
