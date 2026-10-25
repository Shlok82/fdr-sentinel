import uuid
from decimal import Decimal
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from app.models.instrument import Instrument
from app.models.document import Document
from app.models.provenance import FieldProvenance
from app.models.user import User
from app.schemas.instrument import FDRSubmissionForm
from app.services.document_service import DocumentService, delete_ciphertext_from_vault
from app.services.audit_service import AuditService
from app.services.clock_service import clock_service
from app.services.instrument_fsm import Actor
from app.services.discrepancy_engine import run_checks


class FDRSubmissionService:
    """Orchestrates atomic submission of FDR instruments, encrypted scans, and audit logs."""

    @staticmethod
    def submit_instrument(
        db: Session,
        form: FDRSubmissionForm,
        file_bytes: bytes,
        raw_filename: str,
        user: User,
        actor_ip: Optional[str] = None,
        vault_base: Optional[str] = None,
        master_key_b64: Optional[str] = None,
    ) -> Instrument:
        """Atomically processes an FDR submission in ONE transaction.
        
        Guarantees:
        1. Validates file and encrypts to sharded vault before DB transaction.
        2. Creates Instrument with status SUBMITTED, renewal_sequence 1, and trust_score 20.
        3. Creates Document record binding DEK, IV, and tag.
        4. Creates FieldProvenance records strictly for non-blank submitted fields (SELF_DECLARED).
        5. Logs INSTRUMENT_SUBMITTED and DOCUMENT_UPLOADED to the cryptographic audit chain.
        6. On any exception, rolls back database and deletes written ciphertext (no orphan files).
        """
        if not user.institution_id:
            raise ValueError("Authenticated user is not linked to any technical institution.")

        # 1. Encrypt and write document to vault BEFORE opening database write lock
        doc_info = DocumentService.process_and_encrypt_upload(
            file_bytes=file_bytes,
            raw_filename=raw_filename,
            vault_base=vault_base,
            master_key_b64=master_key_b64,
        )

        ciphertext_rel_path = doc_info["storage_path"]
        current_time = clock_service.now()

        try:
            # 2. Instantiate and add Instrument record
            instrument = Instrument(
                id=uuid.uuid4(),
                institution_id=user.institution_id,
                fdr_number=form.cleaned_data.get("fdr_number"),
                bank_name=form.cleaned_data.get("bank_name"),
                bank_branch=form.cleaned_data.get("bank_branch"),
                ifsc_code=form.cleaned_data.get("ifsc_code"),
                principal_amount=form.cleaned_data.get("principal_amount"),
                maturity_amount=form.cleaned_data.get("maturity_amount"),
                interest_rate_percent=form.cleaned_data.get("interest_rate_percent"),
                issue_date=form.cleaned_data.get("issue_date"),
                maturity_date=form.cleaned_data.get("maturity_date"),
                tenure_months=form.cleaned_data.get("tenure_months"),
                joint_account_holder=form.cleaned_data.get("joint_account_holder"),
                status="SUBMITTED",
                previous_instrument_id=None,
                renewal_sequence=1,
                trust_score=20,  # Baseline trust floor
                trust_score_breakdown={"base": 20, "note": "Initial submission baseline"},
                created_at=current_time,
                updated_at=current_time,
            )
            db.add(instrument)
            db.flush()

            # 3. Instantiate and add Document record
            document = Document(
                id=doc_info["id"],
                instrument_id=instrument.id,
                document_type="FDR_SCAN",
                storage_path=doc_info["storage_path"],
                file_name=doc_info["file_name"],
                file_mime_type=doc_info["file_mime_type"],
                file_size_bytes=doc_info["file_size_bytes"],
                sha256_hash=doc_info["sha256_hash"],
                encrypted_dek=doc_info["encrypted_dek"],
                encryption_iv=doc_info["encryption_iv"],
                encryption_tag=doc_info["encryption_tag"],
                extracted_payload=None,
                created_at=current_time,
            )
            db.add(document)
            db.flush()

            # 4. Instantiate FieldProvenance rows strictly for non-blank submitted fields (Preflight 5)
            for field_name, raw_val in form.raw_fields.items():
                if raw_val:  # Non-blank field provided
                    # For fdr_number, current_value is uppercase, submitted_value is raw as-entered
                    curr_val = str(form.cleaned_data.get(field_name, raw_val))
                    prov = FieldProvenance(
                        id=uuid.uuid4(),
                        instrument_id=instrument.id,
                        field_name=field_name,
                        submitted_value=raw_val,
                        current_value=curr_val,
                        provenance_type="SELF_DECLARED",
                        verified_by_user_id=None,
                        verified_at=None,
                        confidence_score=Decimal("1.000"),
                    )
                    db.add(prov)

            db.flush()

            # 5. Record Audit Entries within caller's open transaction
            AuditService.append(
                db,
                entity_type="INSTRUMENT",
                entity_id=instrument.id,
                action="INSTRUMENT_SUBMITTED",
                actor_user_id=user.id,
                actor_role=user.role,
                details={
                    "fdr_number": instrument.fdr_number,
                    "bank_name": instrument.bank_name,
                    "principal_amount": str(instrument.principal_amount) if instrument.principal_amount is not None else None,
                    "status": "SUBMITTED",
                },
                actor_ip=actor_ip,
            )

            AuditService.append(
                db,
                entity_type="DOCUMENT",
                entity_id=document.id,
                action="DOCUMENT_UPLOADED",
                actor_user_id=user.id,
                actor_role=user.role,
                details={
                    "document_type": "FDR_SCAN",
                    "file_name": document.file_name,
                    "file_size_bytes": document.file_size_bytes,
                    "instrument_id": str(instrument.id),
                },
                actor_ip=actor_ip,
            )

            # 6. Execute DiscrepancyEngine checks atomically within transaction
            run_checks(
                session=db,
                instrument_id=instrument.id,
                actor=Actor.from_user(user),
                vault_base=vault_base,
                master_key_b64=master_key_b64,
            )

            # 7. Commit the entire transaction atomically
            db.commit()
            db.refresh(instrument)
            return instrument

        except Exception as exc:
            db.rollback()
            # Clean up written ciphertext file so no orphan files remain
            delete_ciphertext_from_vault(ciphertext_rel_path, vault_base=vault_base)
            raise exc
