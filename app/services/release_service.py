import uuid
import hashlib
from typing import Optional, Dict, Any
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.instrument import Instrument
from app.models.release import ReleaseRequest
from app.models.document import Document
from app.services.instrument_fsm import Actor, transition
from app.services.audit_service import AuditService, canonical_json
from app.services.clock_service import clock_service
from app.services.document_service import DocumentService


class ReleaseError(Exception):
    """Base exception for release workflow operations."""
    pass


class ReleaseNotFoundError(ReleaseError):
    """Raised when an instrument or release request cannot be found."""
    pass


class ReleasePermissionError(ReleaseError):
    """Raised when an actor lacks permission or tenancy scope for release."""
    pass


class ReleaseStateError(ReleaseError):
    """Raised when an operation is invalid for current instrument/request lifecycle state."""
    pass


class ReleaseChecklistIncompleteError(ReleaseError):
    """Raised when statutory checklist is incomplete or conditions not met."""
    pass


class SecondKeyMissingError(ReleaseError):
    """Raised when approver authority attempts approval without scrutiny officer recommendation."""
    pass


class ReleaseAlreadyDecidedError(ReleaseError):
    """Raised when attempting to modify or decide an already finalized release request."""
    pass


REQUIRED_CHECKLIST_KEYS = (
    "course_cycle_completed",
    "no_student_grievances_pending",
    "no_vigilance_inquiry_active",
    "regional_inspection_cleared",
)


def compute_release_order_hash(
    release_request_id: uuid.UUID | str,
    instrument_id: uuid.UUID | str,
    fdr_number: Optional[str],
    institution_id: uuid.UUID | str,
    sanctioned_amount: Any,
    decision: str,
    approver_authority_id: uuid.UUID | str,
    timestamp_iso: str,
    scrutiny_officer_id: Optional[uuid.UUID | str],
    scrutiny_recommendation: Optional[str],
) -> str:
    """Computes SHA-256 integrity hash over canonical JSON payload for release order."""
    payload = {
        "release_request_id": str(release_request_id),
        "instrument_id": str(instrument_id),
        "fdr_number": str(fdr_number or ""),
        "institution_id": str(institution_id),
        "sanctioned_amount": str(sanctioned_amount if sanctioned_amount is not None else ""),
        "decision": str(decision),
        "approver_authority_id": str(approver_authority_id),
        "timestamp": str(timestamp_iso),
        "scrutiny_officer_id": str(scrutiny_officer_id or ""),
        "scrutiny_recommendation": str(scrutiny_recommendation or ""),
    }
    canon_str = canonical_json(payload)
    return hashlib.sha256(canon_str.encode("utf-8")).hexdigest()


def request_release(
    session: Session,
    instrument_id: uuid.UUID | str,
    actor: Actor,
    justification: str,
) -> ReleaseRequest:
    """Initiates a security deposit release request by the Institution Admin.
    
    Guarantees:
    - Actor must be INSTITUTION_ADMIN of the owning institution.
    - Instrument status must be in ('ACTIVE', 'EXPIRING', 'MATURED').
    - Justification must be non-blank and >= 10 characters.
    - No pending release request may exist.
    - No live renewal child instrument (status != 'REJECTED') may exist.
    - Transitions instrument to RELEASE_REQUESTED via instrument_fsm.transition().
    - Persists ReleaseRequest in PENDING state and emits RELEASE_REQUEST_CREATED audit log.
    """
    if isinstance(instrument_id, str):
        try:
            instrument_id = uuid.UUID(instrument_id)
        except ValueError:
            raise ReleaseNotFoundError(f"Invalid UUID: {instrument_id}")

    if actor.role != "INSTITUTION_ADMIN":
        raise ReleasePermissionError("Only Institution Admin can request security deposit release.")

    inst = session.execute(
        select(Instrument).where(Instrument.id == instrument_id).with_for_update()
    ).scalar_one_or_none()
    if not inst:
        raise ReleaseNotFoundError("Instrument not found.")

    if not actor.institution_id or actor.institution_id != inst.institution_id:
        raise ReleasePermissionError("Access forbidden: instrument belongs to another institution.")

    clean_justification = (justification or "").strip()
    if len(clean_justification) < 10:
        raise ReleaseStateError("Justification must be at least 10 characters long.")

    # Verify no pending release request exists (check before status to provide specific error message)
    existing_pending = session.execute(
        select(ReleaseRequest).where(
            ReleaseRequest.instrument_id == inst.id,
            ReleaseRequest.authority_decision == "PENDING",
        )
    ).scalar_one_or_none()
    if existing_pending:
        raise ReleaseStateError("A pending release request already exists for this instrument.")

    if inst.status not in ("ACTIVE", "EXPIRING", "MATURED"):
        raise ReleaseStateError(
            f"Cannot request release for instrument in state '{inst.status}'. Must be ACTIVE, EXPIRING, or MATURED."
        )

    # Verify no live renewal child exists
    live_child = session.execute(
        select(Instrument).where(
            Instrument.previous_instrument_id == inst.id,
            Instrument.status != "REJECTED",
        )
    ).first()
    if live_child:
        raise ReleaseStateError("Cannot request release: a renewal child instrument is active or in progress.")

    # Transition instrument via FSM
    transition(
        session=session,
        instrument_id=inst.id,
        to_state="RELEASE_REQUESTED",
        actor=actor,
        justification=clean_justification,
    )

    now = clock_service.now()
    release_req = ReleaseRequest(
        id=uuid.uuid4(),
        instrument_id=inst.id,
        requested_by_user_id=actor.user_id,
        requested_at=now,
        institution_justification=clean_justification,
        statutory_checklist={},
        authority_decision="PENDING",
        created_at=now,
        updated_at=now,
    )
    session.add(release_req)
    session.flush()

    AuditService.append(
        session=session,
        entity_type="RELEASE_REQUEST",
        entity_id=release_req.id,
        action="RELEASE_REQUEST_CREATED",
        actor_user_id=actor.user_id,
        actor_role=actor.role,
        details={
            "instrument_id": str(inst.id),
            "fdr_number": inst.fdr_number,
            "institution_id": str(inst.institution_id),
            "prior_state": inst.prior_state,
            "justification": clean_justification,
            "requested_at": now.isoformat(),
        },
    )

    return release_req


def record_recommendation(
    session: Session,
    request_id: uuid.UUID | str,
    actor: Actor,
    checklist: Dict[str, Any],
    recommendation: str,
    justification: str,
    evidence_upload: Optional[Any] = None,
) -> ReleaseRequest:
    """Records statutory checklist review and recommendation by a Scrutiny Officer.
    
    Guarantees:
    - Actor must be SCRUTINY_OFFICER.
    - Request must be in PENDING state and instrument in RELEASE_REQUESTED.
    - Once-only check: scrutiny_recorded_at must be None.
    - Recommendation must be RECOMMENDED or NOT_RECOMMENDED.
    - Checklist must contain all 4 required boolean keys.
    - If RECOMMENDED: all 4 keys must be True and non-empty valid evidence file must be provided.
    - If NOT_RECOMMENDED: evidence file is optional and checklist may have False keys.
    - Stores evidence in document vault as RELEASE_EVIDENCE.
    - Instrument status does not change.
    - Emits RELEASE_RECOMMENDATION_RECORDED audit log.
    """
    if isinstance(request_id, str):
        try:
            request_id = uuid.UUID(request_id)
        except ValueError:
            raise ReleaseNotFoundError(f"Invalid UUID: {request_id}")

    if actor.role != "SCRUTINY_OFFICER":
        raise ReleasePermissionError("Only Scrutiny Officer can record release recommendation.")

    req = session.execute(
        select(ReleaseRequest).where(ReleaseRequest.id == request_id).with_for_update()
    ).scalar_one_or_none()
    if not req:
        raise ReleaseNotFoundError("Release request not found.")

    if req.authority_decision != "PENDING":
        raise ReleaseAlreadyDecidedError(f"Release request already decided with status '{req.authority_decision}'.")

    if req.scrutiny_recorded_at is not None:
        raise ReleaseStateError("Scrutiny recommendation already recorded for this request.")

    inst = session.execute(
        select(Instrument).where(Instrument.id == req.instrument_id).with_for_update()
    ).scalar_one()
    if inst.status != "RELEASE_REQUESTED":
        raise ReleaseStateError(f"Instrument is in state '{inst.status}', expected 'RELEASE_REQUESTED'.")

    clean_justification = (justification or "").strip()
    if len(clean_justification) < 10:
        raise ReleaseStateError("Justification must be at least 10 characters long.")

    clean_rec = (recommendation or "").strip().upper()
    if clean_rec not in ("RECOMMENDED", "NOT_RECOMMENDED"):
        raise ReleaseStateError(f"Invalid recommendation '{recommendation}'. Must be 'RECOMMENDED' or 'NOT_RECOMMENDED'.")

    # Validate statutory checklist contains all 4 boolean keys
    if not isinstance(checklist, dict):
        raise ReleaseChecklistIncompleteError("Checklist must be a dictionary.")

    for k in REQUIRED_CHECKLIST_KEYS:
        if k not in checklist or not isinstance(checklist[k], bool):
            raise ReleaseChecklistIncompleteError(
                f"Missing or invalid boolean checklist item: '{k}'."
            )

    evidence_doc_id: Optional[uuid.UUID] = None

    if clean_rec == "RECOMMENDED":
        for k in REQUIRED_CHECKLIST_KEYS:
            if checklist[k] is not True:
                raise ReleaseChecklistIncompleteError("All statutory conditions must be cleared to recommend release.")

        # Evidence upload is mandatory
        if not evidence_upload:
            raise ReleaseChecklistIncompleteError("Mandatory evidence upload required to recommend release.")

        # Read file bytes
        file_bytes: bytes
        filename: str = getattr(evidence_upload, "filename", "evidence.pdf") or "evidence.pdf"
        if hasattr(evidence_upload, "read"):
            # Could be async or sync or UploadFile or BytesIO
            read_fn = getattr(evidence_upload, "read")
            import inspect
            if inspect.iscoroutinefunction(read_fn):
                import asyncio
                file_bytes = asyncio.run(read_fn())
            else:
                file_bytes = read_fn()
        elif isinstance(evidence_upload, (bytes, bytearray)):
            file_bytes = bytes(evidence_upload)
        else:
            raise ReleaseChecklistIncompleteError("Invalid evidence upload object.")

        if not file_bytes:
            raise ReleaseChecklistIncompleteError("Evidence upload cannot be empty.")

        # Process and store in document vault
        try:
            doc_info = DocumentService.process_and_encrypt_upload(
                file_bytes=file_bytes,
                raw_filename=filename,
            )
        except Exception as e:
            raise ReleaseChecklistIncompleteError(f"Evidence file invalid: {e}")

        now = clock_service.now()
        evidence_doc = Document(
            id=doc_info["id"],
            instrument_id=inst.id,
            document_type="RELEASE_EVIDENCE",
            storage_path=doc_info["storage_path"],
            file_name=doc_info["file_name"],
            file_mime_type=doc_info["file_mime_type"],
            file_size_bytes=doc_info["file_size_bytes"],
            sha256_hash=doc_info["sha256_hash"],
            encrypted_dek=doc_info["encrypted_dek"],
            encryption_iv=doc_info["encryption_iv"],
            encryption_tag=doc_info["encryption_tag"],
            extracted_payload=None,
            created_at=now,
        )
        session.add(evidence_doc)
        session.flush()
        evidence_doc_id = evidence_doc.id

    else:
        # NOT_RECOMMENDED: evidence upload is optional
        if evidence_upload:
            filename = getattr(evidence_upload, "filename", "evidence.pdf") or "evidence.pdf"
            file_bytes = b""
            if hasattr(evidence_upload, "read"):
                read_fn = getattr(evidence_upload, "read")
                import inspect
                if inspect.iscoroutinefunction(read_fn):
                    import asyncio
                    file_bytes = asyncio.run(read_fn())
                else:
                    file_bytes = read_fn()
            elif isinstance(evidence_upload, (bytes, bytearray)):
                file_bytes = bytes(evidence_upload)

            if file_bytes:
                try:
                    doc_info = DocumentService.process_and_encrypt_upload(
                        file_bytes=file_bytes,
                        raw_filename=filename,
                    )
                    now = clock_service.now()
                    evidence_doc = Document(
                        id=doc_info["id"],
                        instrument_id=inst.id,
                        document_type="RELEASE_EVIDENCE",
                        storage_path=doc_info["storage_path"],
                        file_name=doc_info["file_name"],
                        file_mime_type=doc_info["file_mime_type"],
                        file_size_bytes=doc_info["file_size_bytes"],
                        sha256_hash=doc_info["sha256_hash"],
                        encrypted_dek=doc_info["encrypted_dek"],
                        encryption_iv=doc_info["encryption_iv"],
                        encryption_tag=doc_info["encryption_tag"],
                        extracted_payload=None,
                        created_at=now,
                    )
                    session.add(evidence_doc)
                    session.flush()
                    evidence_doc_id = evidence_doc.id
                except Exception:
                    pass

    now = clock_service.now()
    req.statutory_checklist = checklist
    req.scrutiny_recommendation = clean_rec
    req.scrutiny_officer_id = actor.user_id
    req.scrutiny_justification = clean_justification
    req.scrutiny_recorded_at = now
    req.scrutiny_evidence_document_id = evidence_doc_id
    req.evidence_document_id = evidence_doc_id
    req.updated_at = now

    AuditService.append(
        session=session,
        entity_type="RELEASE_REQUEST",
        entity_id=req.id,
        action="RELEASE_RECOMMENDATION_RECORDED",
        actor_user_id=actor.user_id,
        actor_role=actor.role,
        details={
            "instrument_id": str(inst.id),
            "recommendation": clean_rec,
            "checklist": checklist,
            "evidence_document_id": str(evidence_doc_id) if evidence_doc_id else None,
            "justification": clean_justification,
            "recorded_at": now.isoformat(),
        },
    )

    return req


def decide_release(
    session: Session,
    request_id: uuid.UUID | str,
    actor: Actor,
    decision: str,
    justification: str,
) -> ReleaseRequest:
    """Decides a pending release request by the Competent Authority.
    
    Guarantees:
    - Actor must be APPROVER_AUTHORITY.
    - Request must be in PENDING state and instrument in RELEASE_REQUESTED.
    - Justification must be non-blank and >= 10 characters.
    - If decision == 'APPROVE':
        - Scrutiny recommendation must be 'RECOMMENDED' (second key).
        - Transitions instrument to RELEASED via instrument_fsm.transition().
        - Generates cryptographically verifiable release_order_hash.
        - Emits RELEASE_DECISION_RECORDED audit log with action='RELEASE_APPROVED'.
    - If decision == 'REJECT':
        - Transitions instrument back to prior_state (clearing prior_state).
        - Emits RELEASE_DECISION_RECORDED audit log with action='RELEASE_REJECTED'.
    """
    if isinstance(request_id, str):
        try:
            request_id = uuid.UUID(request_id)
        except ValueError:
            raise ReleaseNotFoundError(f"Invalid UUID: {request_id}")

    if actor.role != "APPROVER_AUTHORITY":
        raise ReleasePermissionError("Only Approver Authority can decide security deposit release.")

    req = session.execute(
        select(ReleaseRequest).where(ReleaseRequest.id == request_id).with_for_update()
    ).scalar_one_or_none()
    if not req:
        raise ReleaseNotFoundError("Release request not found.")

    if req.authority_decision != "PENDING":
        raise ReleaseAlreadyDecidedError(f"Release request already decided with status '{req.authority_decision}'.")

    inst = session.execute(
        select(Instrument).where(Instrument.id == req.instrument_id).with_for_update()
    ).scalar_one()
    if inst.status != "RELEASE_REQUESTED":
        raise ReleaseStateError(f"Instrument is in state '{inst.status}', expected 'RELEASE_REQUESTED'.")

    clean_justification = (justification or "").strip()
    if len(clean_justification) < 10:
        raise ReleaseStateError("Justification must be at least 10 characters long.")

    clean_decision = (decision or "").strip().upper()
    if clean_decision not in ("APPROVE", "REJECT"):
        raise ReleaseStateError(f"Invalid decision '{decision}'. Expected 'APPROVE' or 'REJECT'.")

    now = clock_service.now()

    if clean_decision == "APPROVE":
        if req.scrutiny_recommendation != "RECOMMENDED":
            raise SecondKeyMissingError("Cannot approve release without scrutiny officer recommendation.")

        # Transition instrument to RELEASED
        transition(
            session=session,
            instrument_id=inst.id,
            to_state="RELEASED",
            actor=actor,
            justification=clean_justification,
        )

        req.authority_decision = "APPROVED"
        req.approver_authority_id = actor.user_id
        req.decision_timestamp = now
        req.authority_justification = clean_justification
        req.updated_at = now

        order_hash = compute_release_order_hash(
            release_request_id=req.id,
            instrument_id=inst.id,
            fdr_number=inst.fdr_number,
            institution_id=inst.institution_id,
            sanctioned_amount=inst.principal_amount,
            decision="APPROVED",
            approver_authority_id=actor.user_id,
            timestamp_iso=now.isoformat(),
            scrutiny_officer_id=req.scrutiny_officer_id,
            scrutiny_recommendation=req.scrutiny_recommendation,
        )
        req.release_order_hash = order_hash

        AuditService.append(
            session=session,
            entity_type="RELEASE_REQUEST",
            entity_id=req.id,
            action="RELEASE_DECISION_RECORDED",
            actor_user_id=actor.user_id,
            actor_role=actor.role,
            details={
                "decision": "APPROVED",
                "instrument_id": str(inst.id),
                "release_order_hash": order_hash,
                "approver_authority_id": str(actor.user_id),
                "justification": clean_justification,
                "decision_timestamp": now.isoformat(),
            },
        )

    else:
        # clean_decision == "REJECT"
        target_state = inst.prior_state or "ACTIVE"
        if target_state not in ("ACTIVE", "EXPIRING", "MATURED"):
            target_state = "ACTIVE"

        # Transition instrument back to prior state
        transition(
            session=session,
            instrument_id=inst.id,
            to_state=target_state,
            actor=actor,
            justification=clean_justification,
        )

        req.authority_decision = "REJECTED"
        req.approver_authority_id = actor.user_id
        req.decision_timestamp = now
        req.authority_justification = clean_justification
        req.updated_at = now

        AuditService.append(
            session=session,
            entity_type="RELEASE_REQUEST",
            entity_id=req.id,
            action="RELEASE_DECISION_RECORDED",
            actor_user_id=actor.user_id,
            actor_role=actor.role,
            details={
                "decision": "REJECTED",
                "instrument_id": str(inst.id),
                "prior_state": target_state,
                "approver_authority_id": str(actor.user_id),
                "justification": clean_justification,
                "decision_timestamp": now.isoformat(),
            },
        )

    return req
