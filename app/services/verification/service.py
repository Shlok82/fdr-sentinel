import uuid
from decimal import Decimal
from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy.orm import Session

from app.models.instrument import Instrument
from app.models.document import Document
from app.models.provenance import FieldProvenance
from app.models.verification import VerificationRecord
from app.models.user import User
from app.services.audit_service import AuditService
from app.services.clock_service import clock_service
from app.services.verification.base import VerificationResult
from app.services.verification.mock_bank import MockBankProvider, MockFailureMode
from app.services.verification.manual_letter import ManualConfirmationProvider


class VerificationService:
    """Orchestrates instrument verification, provenance updates, and audit logging."""

    @classmethod
    def execute_mock_verification(
        cls,
        db: Session,
        instrument: Instrument,
        officer: User,
        failure_mode: MockFailureMode = MockFailureMode.NONE,
        actor_ip: Optional[str] = None,
    ) -> Tuple[VerificationRecord, VerificationResult]:
        """Executes simulated CBS verification via MockBankProvider."""
        provider = MockBankProvider(failure_mode=failure_mode)
        result = provider.verify_instrument(db=db, instrument=instrument)

        # 1. Persist VerificationRecord
        record = VerificationRecord(
            id=uuid.uuid4(),
            instrument_id=instrument.id,
            provider_type=result.provider_type,
            raw_response=result.raw_response,
            is_verified=result.is_verified,
            mismatch_flags=result.mismatch_flags,
            executed_at=clock_service.now(),
        )
        db.add(record)

        # 2. Update FieldProvenance only on success
        if result.is_verified:
            cls._apply_provenance_updates(
                db=db,
                instrument=instrument,
                confirmed_fields=result.confirmed_fields,
                provenance_type="MOCK_BANK_CONFIRMED",
                verified_by_user_id=None,
            )

        # 3. Log audit event
        AuditService.append(
            session=db,
            entity_type="INSTRUMENT",
            entity_id=instrument.id,
            action="VERIFICATION_RUN",
            actor_user_id=officer.id,
            actor_role=officer.role,
            details={
                "provider": result.provider_type,
                "is_verified": result.is_verified,
                "mismatch_summary": [f.get("message", "") for f in result.mismatch_flags],
                "simulated": result.simulated,
                "confirmed_fields": result.confirmed_fields,
                "verification_record_id": str(record.id),
            },
            actor_ip=actor_ip,
        )

        # 4. Recompute earned trust score
        from app.services.trust_score import recompute_trust
        recompute_trust(session=db, instrument_id=instrument.id)

        return record, result

    @classmethod
    def execute_manual_confirmation(
        cls,
        db: Session,
        instrument: Instrument,
        officer: User,
        file_bytes: bytes,
        file_name: str,
        reference_number: str,
        memo: str,
        actor_ip: Optional[str] = None,
    ) -> Tuple[VerificationRecord, VerificationResult]:
        """Records officer-verified bank confirmation letter."""
        provider = ManualConfirmationProvider(
            file_bytes=file_bytes,
            file_name=file_name,
            reference_number=reference_number,
            memo=memo,
            officer=officer,
        )
        result = provider.verify_instrument(db=db, instrument=instrument)

        # 1. Persist VerificationRecord
        record = VerificationRecord(
            id=uuid.uuid4(),
            instrument_id=instrument.id,
            provider_type=result.provider_type,
            raw_response=result.raw_response,
            is_verified=result.is_verified,
            mismatch_flags=result.mismatch_flags,
            executed_at=clock_service.now(),
        )
        db.add(record)

        # 2. Update FieldProvenance to OFFICER_VERIFIED
        cls._apply_provenance_updates(
            db=db,
            instrument=instrument,
            confirmed_fields=result.confirmed_fields,
            provenance_type="OFFICER_VERIFIED",
            verified_by_user_id=officer.id,
        )

        # 3. Log audit entries
        AuditService.append(
            session=db,
            entity_type="INSTRUMENT",
            entity_id=instrument.id,
            action="MANUAL_CONFIRMATION_RECORDED",
            actor_user_id=officer.id,
            actor_role=officer.role,
            details={
                "reference_number": reference_number,
                "document_id": result.raw_response.get("document_id"),
                "verification_record_id": str(record.id),
            },
            actor_ip=actor_ip,
        )

        AuditService.append(
            session=db,
            entity_type="INSTRUMENT",
            entity_id=instrument.id,
            action="VERIFICATION_RUN",
            actor_user_id=officer.id,
            actor_role=officer.role,
            details={
                "provider": result.provider_type,
                "is_verified": result.is_verified,
                "mismatch_summary": [],
                "simulated": False,
                "confirmed_fields": result.confirmed_fields,
                "verification_record_id": str(record.id),
            },
            actor_ip=actor_ip,
        )

        # 4. Recompute earned trust score
        from app.services.trust_score import recompute_trust
        recompute_trust(session=db, instrument_id=instrument.id)

        return record, result

    @classmethod
    def _apply_provenance_updates(
        cls,
        db: Session,
        instrument: Instrument,
        confirmed_fields: List[str],
        provenance_type: str,
        verified_by_user_id: Optional[uuid.UUID],
    ) -> None:
        """Updates provenance for confirmed fields, preserving original submitted_value."""
        existing_rows = {
            p.field_name: p
            for p in db.query(FieldProvenance).filter(FieldProvenance.instrument_id == instrument.id).all()
        }

        now_ts = clock_service.now()

        for field_name in confirmed_fields:
            current_val = str(getattr(instrument, field_name, ""))
            if field_name in existing_rows:
                prov = existing_rows[field_name]
                prov.provenance_type = provenance_type
                prov.current_value = current_val
                prov.verified_by_user_id = verified_by_user_id
                prov.verified_at = now_ts
                prov.confidence_score = Decimal("1.000")
            else:
                prov = FieldProvenance(
                    id=uuid.uuid4(),
                    instrument_id=instrument.id,
                    field_name=field_name,
                    submitted_value=current_val,
                    current_value=current_val,
                    provenance_type=provenance_type,
                    verified_by_user_id=verified_by_user_id,
                    verified_at=now_ts,
                    confidence_score=Decimal("1.000"),
                )
                db.add(prov)
