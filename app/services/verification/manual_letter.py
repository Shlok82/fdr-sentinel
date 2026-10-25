import uuid
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

from app.models.instrument import Instrument
from app.models.document import Document
from app.models.user import User
from app.services.document_service import DocumentService, validate_file_content
from app.services.clock_service import clock_service
from app.services.verification.base import BaseVerificationProvider, VerificationResult


class ManualConfirmationProvider(BaseVerificationProvider):
    """Manual Confirmation Letter Provider.
    
    Processes formal verification letters issued by bank branch managers,
    encrypts the scanned correspondence in the secure vault, and records the
    officer's statutory confirmation.
    """

    VERIFIABLE_FIELDS = [
        "fdr_number",
        "bank_name",
        "bank_branch",
        "ifsc_code",
        "principal_amount",
        "maturity_amount",
        "interest_rate_percent",
        "issue_date",
        "maturity_date",
        "tenure_months",
        "joint_account_holder",
    ]

    def __init__(
        self,
        file_bytes: bytes,
        file_name: str,
        reference_number: str,
        memo: str,
        officer: User,
    ):
        self.file_bytes = file_bytes
        self.file_name = file_name
        self.reference_number = reference_number
        self.memo = memo
        self.officer = officer

    def get_provider_name(self) -> str:
        return "Manual Bank Confirmation Letter"

    def is_live_authorized(self) -> bool:
        return False

    def verify_instrument(
        self,
        db: Session,
        instrument: Instrument,
        document: Optional[Document] = None,
        **kwargs: Any,
    ) -> VerificationResult:
        # 1. Validate reference number and memo
        ref = (self.reference_number or "").strip()
        memo_text = (self.memo or "").strip()

        if not ref:
            raise ValueError("Bank reference number is mandatory for manual confirmation.")
        if len(ref) > 100:
            raise ValueError("Bank reference number cannot exceed 100 characters.")
        if not memo_text:
            raise ValueError("Verification memo is mandatory for manual confirmation.")
        if len(memo_text) > 1000:
            raise ValueError("Verification memo cannot exceed 1000 characters.")

        # 2. Validate uploaded letter file content
        if not self.file_bytes:
            raise ValueError("Bank confirmation letter file is empty.")
        validate_file_content(self.file_bytes, self.file_name)

        # 3. Encrypt and store letter in secure vault
        doc_info = DocumentService.process_and_encrypt_upload(
            file_bytes=self.file_bytes,
            raw_filename=self.file_name,
        )
        doc = Document(
            id=doc_info["id"],
            instrument_id=instrument.id,
            document_type="BANK_CONFIRMATION_LETTER",
            storage_path=doc_info["storage_path"],
            file_name=doc_info["file_name"],
            file_mime_type=doc_info["file_mime_type"],
            file_size_bytes=doc_info["file_size_bytes"],
            sha256_hash=doc_info["sha256_hash"],
            encrypted_dek=doc_info["encrypted_dek"],
            encryption_iv=doc_info["encryption_iv"],
            encryption_tag=doc_info["encryption_tag"],
            extracted_payload={
                "reference_number": ref,
                "memo": memo_text,
                "verified_by_user_id": str(self.officer.id),
                "verified_by_username": self.officer.username,
            },
            created_at=clock_service.now(),
        )
        db.add(doc)
        db.flush()

        populated_fields = [
            f for f in self.VERIFIABLE_FIELDS
            if getattr(instrument, f, None) is not None
        ]

        return VerificationResult(
            is_verified=True,
            provider_type="MANUAL_LETTER",
            confirmed_fields=populated_fields,
            mismatch_flags=[],
            raw_response={
                "document_id": str(doc.id),
                "file_name": doc.file_name,
                "reference_number": ref,
                "memo": memo_text,
                "officer_user_id": str(self.officer.id),
                "officer_username": self.officer.username,
                "recorded_at": clock_service.get_current_time().isoformat(),
                "simulated": False,
            },
            simulated=False,
        )
