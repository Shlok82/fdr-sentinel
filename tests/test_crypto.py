import os
import base64
import uuid
import pytest
from app.core.crypto import (
    encrypt_file_envelope,
    decrypt_file_envelope,
    get_validated_master_key,
    CryptoError,
)


def test_crypto_round_trip():
    """Valid plaintext comes back identical after encrypt and decrypt."""
    plaintext = b"Government of India - AICTE Regulation Bureau Test FDR Content 123456"
    doc_id = uuid.uuid4()

    ct_only, enc_dek, enc_iv, enc_tag = encrypt_file_envelope(plaintext, doc_id)

    decrypted = decrypt_file_envelope(ct_only, enc_dek, enc_iv, enc_tag, doc_id)
    assert decrypted == plaintext


def test_crypto_fresh_randomness_and_nontrivial_ciphertext():
    """Ciphertext differs from plaintext, and two encryptions of the same plaintext produce different ciphertexts."""
    plaintext = b"Fixed Deposit Receipt Confidential Content"
    doc_id = uuid.uuid4()

    ct1, dek1, iv1, tag1 = encrypt_file_envelope(plaintext, doc_id)
    ct2, dek2, iv2, tag2 = encrypt_file_envelope(plaintext, doc_id)

    assert ct1 != plaintext
    assert ct2 != plaintext
    # Each call produces a fresh random DEK and IV
    assert ct1 != ct2
    assert dek1 != dek2
    assert iv1 != iv2


def test_crypto_tampered_ciphertext():
    """Tampering with file ciphertext raises CryptoError."""
    plaintext = b"Sensitive Document Bytes"
    doc_id = uuid.uuid4()
    ct_only, enc_dek, enc_iv, enc_tag = encrypt_file_envelope(plaintext, doc_id)

    # Flip one byte in ciphertext
    tampered_ct = bytearray(ct_only)
    tampered_ct[0] ^= 0xFF

    with pytest.raises(CryptoError, match="Ciphertext decryption failed"):
        decrypt_file_envelope(bytes(tampered_ct), enc_dek, enc_iv, enc_tag, doc_id)


def test_crypto_tampered_tag():
    """Tampering with authentication tag raises CryptoError."""
    plaintext = b"Sensitive Document Bytes"
    doc_id = uuid.uuid4()
    ct_only, enc_dek, enc_iv, enc_tag = encrypt_file_envelope(plaintext, doc_id)

    tag_bytes = bytearray(base64.b64decode(enc_tag))
    tag_bytes[0] ^= 0x01
    tampered_tag = base64.b64encode(tag_bytes).decode("ascii")

    with pytest.raises(CryptoError, match="Ciphertext decryption failed"):
        decrypt_file_envelope(ct_only, enc_dek, enc_iv, tampered_tag, doc_id)


def test_crypto_tampered_iv():
    """Tampering with file IV raises CryptoError."""
    plaintext = b"Sensitive Document Bytes"
    doc_id = uuid.uuid4()
    ct_only, enc_dek, enc_iv, enc_tag = encrypt_file_envelope(plaintext, doc_id)

    iv_bytes = bytearray(base64.b64decode(enc_iv))
    iv_bytes[0] ^= 0x01
    tampered_iv = base64.b64encode(iv_bytes).decode("ascii")

    with pytest.raises(CryptoError, match="Ciphertext decryption failed"):
        decrypt_file_envelope(ct_only, enc_dek, tampered_iv, enc_tag, doc_id)


def test_crypto_tampered_wrapped_dek():
    """Tampering with wrapped DEK payload raises CryptoError."""
    plaintext = b"Sensitive Document Bytes"
    doc_id = uuid.uuid4()
    ct_only, enc_dek, enc_iv, enc_tag = encrypt_file_envelope(plaintext, doc_id)

    dek_bytes = bytearray(base64.b64decode(enc_dek))
    dek_bytes[-1] ^= 0x01  # Corrupt the DEK auth tag or payload
    tampered_dek = base64.b64encode(dek_bytes).decode("ascii")

    with pytest.raises(CryptoError, match="DEK unwrap failed"):
        decrypt_file_envelope(ct_only, tampered_dek, enc_iv, enc_tag, doc_id)


def test_crypto_swapped_document_uuid():
    """Using a different document UUID (swapped AAD) raises CryptoError."""
    plaintext = b"Sensitive Document Bytes"
    doc_id = uuid.uuid4()
    wrong_doc_id = uuid.uuid4()

    ct_only, enc_dek, enc_iv, enc_tag = encrypt_file_envelope(plaintext, doc_id)

    # AAD mismatch causes DEK unwrap failure immediately
    with pytest.raises(CryptoError, match="DEK unwrap failed"):
        decrypt_file_envelope(ct_only, enc_dek, enc_iv, enc_tag, wrong_doc_id)


def test_crypto_wrong_master_key():
    """Decrypting with a different valid 32-byte master key raises CryptoError."""
    plaintext = b"Sensitive Document Bytes"
    doc_id = uuid.uuid4()

    key1 = base64.b64encode(os.urandom(32)).decode("ascii")
    key2 = base64.b64encode(os.urandom(32)).decode("ascii")

    ct_only, enc_dek, enc_iv, enc_tag = encrypt_file_envelope(plaintext, doc_id, master_key_b64=key1)

    with pytest.raises(CryptoError, match="DEK unwrap failed"):
        decrypt_file_envelope(ct_only, enc_dek, enc_iv, enc_tag, doc_id, master_key_b64=key2)


@pytest.mark.parametrize("invalid_key,expected_err", [
    ("", "missing or set to placeholder"),
    ("   ", "missing or set to placeholder"),
    ("REPLACE_ME", "missing or set to placeholder"),
    ("NotBase64!@#$%", "base64 decoding failed"),
    (base64.b64encode(b"short_16_bytes!!").decode("ascii"), "must decode to exactly 32 bytes"),
    (base64.b64encode(b"long_48_bytes_" * 3).decode("ascii"), "must decode to exactly 32 bytes"),
])
def test_crypto_invalid_master_key_validation(invalid_key, expected_err):
    """Refuses execution in all modes if key is missing, placeholder, or invalid length."""
    with pytest.raises(ValueError, match=expected_err):
        get_validated_master_key(invalid_key)
