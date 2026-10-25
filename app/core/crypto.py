import os
import base64
import uuid
from typing import Optional, Tuple
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag
from app.core.config import settings


class CryptoError(Exception):
    """Generic cryptographic operation failure."""
    pass


def get_validated_master_key(raw_key: Optional[str] = None) -> bytes:
    """Retrieves and strictly validates the 32-byte DOCUMENT_MASTER_KEY.
    
    Refuses execution in ALL environments if key is missing, empty,
    placeholder ('REPLACE_ME'), or does not decode to exactly 32 bytes.
    Never prints or logs the master key.
    """
    key_str = settings.DOCUMENT_MASTER_KEY if raw_key is None else raw_key
    if not key_str or not key_str.strip() or key_str.strip() == "REPLACE_ME":
        raise ValueError("Invalid DOCUMENT_MASTER_KEY: master key is missing or set to placeholder.")

    try:
        decoded = base64.b64decode(key_str.strip(), validate=True)
    except Exception as exc:
        raise ValueError("Invalid DOCUMENT_MASTER_KEY: base64 decoding failed.") from exc

    if len(decoded) != 32:
        raise ValueError(
            f"Invalid DOCUMENT_MASTER_KEY: must decode to exactly 32 bytes (got {len(decoded)} bytes)."
        )

    return decoded


def encrypt_file_envelope(
    plaintext: bytes,
    document_id: uuid.UUID | str,
    master_key_b64: Optional[str] = None,
) -> Tuple[bytes, str, str, str]:
    """Encrypts plaintext with a fresh random 256-bit DEK via AES-256-GCM.
    
    Security Guarantees:
    1. Fresh 256-bit DEK generated per file.
    2. Fresh 96-bit random nonce generated for file ciphertext.
    3. Fresh 96-bit random nonce generated for wrapping DEK.
    4. Associated Authenticated Data (AAD) binds the document UUID to BOTH
       file encryption and key wrapping (preventing ciphertext swapping).
    5. Final 16 bytes of file AES-GCM output are split into encryption_tag.
    6. Returns compound base64(dek_nonce || wrapped_dek_ciphertext_with_tag)
       for storage in encrypted_dek column.
    
    Returns:
        (ciphertext_only, encrypted_dek, encryption_iv, encryption_tag)
    """
    doc_id_str = str(document_id)
    aad = doc_id_str.encode("utf-8")
    master_key = get_validated_master_key(master_key_b64)

    # 1. Generate fresh 256-bit DEK and 96-bit nonce
    dek = os.urandom(32)
    file_nonce = os.urandom(12)

    # 2. Encrypt plaintext using AES-256-GCM with document ID bound as AAD
    file_gcm = AESGCM(dek)
    file_ct_with_tag = file_gcm.encrypt(file_nonce, plaintext, aad)

    # Split final 16 bytes into auth tag per Preflight 7
    ciphertext_only = file_ct_with_tag[:-16]
    file_tag = file_ct_with_tag[-16:]

    # 3. Wrap DEK with DOCUMENT_MASTER_KEY using fresh 96-bit nonce and document ID AAD
    dek_nonce = os.urandom(12)
    master_gcm = AESGCM(master_key)
    wrapped_dek_with_tag = master_gcm.encrypt(dek_nonce, dek, aad)

    # Compound wrapped DEK format: dek_nonce (12 bytes) || wrapped_dek_with_tag (48 bytes) = 60 bytes
    compound_wrapped_dek = dek_nonce + wrapped_dek_with_tag

    # 4. Base64 encode metadata for database storage
    encrypted_dek_b64 = base64.b64encode(compound_wrapped_dek).decode("ascii")
    encryption_iv_b64 = base64.b64encode(file_nonce).decode("ascii")
    encryption_tag_b64 = base64.b64encode(file_tag).decode("ascii")

    return ciphertext_only, encrypted_dek_b64, encryption_iv_b64, encryption_tag_b64


def decrypt_file_envelope(
    ciphertext_only: bytes,
    encrypted_dek_b64: str,
    encryption_iv_b64: str,
    encryption_tag_b64: str,
    document_id: uuid.UUID | str,
    master_key_b64: Optional[str] = None,
) -> bytes:
    """Decrypts envelope-encrypted ciphertext, strictly verifying all tags and AAD.
    
    Fails safely with generic CryptoError if key is wrong, ciphertext is tampered,
    tag is corrupted, or document UUID was swapped. Never leaks secrets or key values.
    """
    doc_id_str = str(document_id)
    aad = doc_id_str.encode("utf-8")
    master_key = get_validated_master_key(master_key_b64)

    # 1. Decode compound wrapped DEK
    try:
        compound_wrapped_dek = base64.b64decode(encrypted_dek_b64, validate=True)
    except Exception as exc:
        raise CryptoError("Failed to decode encrypted DEK payload.") from exc

    if len(compound_wrapped_dek) != (12 + 32 + 16):
        raise CryptoError(f"Malformed wrapped DEK length: {len(compound_wrapped_dek)} bytes.")

    dek_nonce = compound_wrapped_dek[:12]
    wrapped_dek_with_tag = compound_wrapped_dek[12:]

    # 2. Unwrap DEK using master key
    master_gcm = AESGCM(master_key)
    try:
        dek = master_gcm.decrypt(dek_nonce, wrapped_dek_with_tag, aad)
    except InvalidTag as exc:
        raise CryptoError("DEK unwrap failed: authentication tag mismatch or wrong master key.") from exc
    except Exception as exc:
        raise CryptoError("DEK unwrap failed.") from exc

    # 3. Decode file nonce and tag
    try:
        file_nonce = base64.b64decode(encryption_iv_b64, validate=True)
        file_tag = base64.b64decode(encryption_tag_b64, validate=True)
    except Exception as exc:
        raise CryptoError("Failed to decode file nonce or authentication tag.") from exc

    if len(file_nonce) != 12:
        raise CryptoError(f"Malformed file IV length: {len(file_nonce)} bytes.")
    if len(file_tag) != 16:
        raise CryptoError(f"Malformed authentication tag length: {len(file_tag)} bytes.")

    # 4. Reconstruct ciphertext + tag and decrypt
    file_ct_with_tag = ciphertext_only + file_tag
    file_gcm = AESGCM(dek)
    try:
        plaintext = file_gcm.decrypt(file_nonce, file_ct_with_tag, aad)
    except InvalidTag as exc:
        raise CryptoError("Ciphertext decryption failed: tag mismatch or tampered data.") from exc
    except Exception as exc:
        raise CryptoError("Ciphertext decryption failed.") from exc

    return plaintext
