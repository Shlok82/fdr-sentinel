"""Renewal service managing parent-child rollover lifecycle chains."""
from __future__ import annotations

from typing import Optional, Union, Tuple, Any
import uuid

from sqlalchemy.orm import Session

from app.models.instrument import Instrument
from app.models.release import ReleaseRequest
from app.models.user import User
from app.schemas.instrument import FDRSubmissionForm
from app.services.audit_service import AuditService
from app.services.instrument_fsm import Actor
from app.services.submission_service import FDRSubmissionService


class RenewalError(Exception):
    """Base exception for all renewal workflow errors."""
    pass


class RenewalNotFoundError(RenewalError):
    """Raised when the parent instrument is not found or fails institution scoping (404)."""
    pass


class RenewalPermissionError(RenewalError):
    """Raised when an unauthorized actor attempts to initiate renewal."""
    pass


class RenewalStateError(RenewalError):
    """Raised when the parent instrument is not in a valid state for renewal."""
    pass


class LiveChildExistsError(RenewalError):
    """Raised when a non-REJECTED renewal child instrument already exists for the parent."""
    pass


def start_renewal(
    session: Session,
    parent_id: uuid.UUID,
    form_data: FDRSubmissionForm,
    upload: Union[Tuple[bytes, str], Any],
    actor: Union[Actor, User],
    actor_ip: Optional[str] = None,
    vault_base: Optional[str] = None,
    master_key_b64: Optional[str] = None,
) -> Instrument:
    """Initiates a renewal of an EXPIRING or MATURED FDR instrument.
    
    Guarantees:
    - Scoped strictly to INSTITUTION_ADMIN of the owning institution (else 404).
    - Parent must be in EXPIRING or MATURED state, and not in RELEASE_REQUESTED.
    - No existing non-REJECTED child instrument for this parent.
    - Child created through the existing submission pipeline with previous_instrument_id
      and renewal_sequence = parent.renewal_sequence + 1.
    - Audits RENEWAL_INITIATED within the same atomic transaction.
    """
    # 1. Resolve actor and user
    if isinstance(actor, User):
        user = actor
        actor_obj = Actor.from_user(actor)
    elif isinstance(actor, Actor):
        actor_obj = actor
        user = session.query(User).filter(User.id == actor_obj.user_id).first()
        if not user:
            raise RenewalPermissionError(f"User {actor_obj.user_id} not found.")
    else:
        raise TypeError(f"Expected Actor or User, got {type(actor)}")

    if actor_obj.role != "INSTITUTION_ADMIN":
        raise RenewalPermissionError("Only Institution Administrators can initiate deposit renewals.")

    # 2. Fetch and scope parent instrument
    parent = session.query(Instrument).filter(Instrument.id == parent_id).first()
    if not parent or parent.institution_id != actor_obj.institution_id:
        raise RenewalNotFoundError("Instrument not found.")

    # 3. Verify parent lifecycle state
    if parent.status == "RELEASE_REQUESTED":
        raise RenewalStateError("Cannot start renewal: a security deposit release request is currently in progress.")

    if parent.status not in ("EXPIRING", "MATURED"):
        raise RenewalStateError(
            f"Cannot start renewal: instrument is in status '{parent.status}', expected 'EXPIRING' or 'MATURED'."
        )

    # 4. Check for active release requests
    pending_rel = (
        session.query(ReleaseRequest)
        .filter(
            ReleaseRequest.instrument_id == parent.id,
            ReleaseRequest.authority_decision == "PENDING",
        )
        .first()
    )
    if pending_rel:
        raise RenewalStateError("Cannot start renewal: a security deposit release request is currently pending review.")

    # 5. Check for existing live child instrument (status != 'REJECTED')
    live_child = (
        session.query(Instrument)
        .filter(
            Instrument.previous_instrument_id == parent.id,
            Instrument.status != "REJECTED",
        )
        .first()
    )
    if live_child:
        child_fdr = live_child.fdr_number or f"ID {live_child.id}"
        raise LiveChildExistsError(
            f"A renewal is already in progress for this deposit (Child: {child_fdr}, status: {live_child.status})."
        )

    # 6. Extract upload bytes and filename
    if isinstance(upload, tuple):
        file_bytes, file_name = upload
    elif hasattr(upload, "file_bytes") and hasattr(upload, "filename"):
        file_bytes = upload.file_bytes
        file_name = upload.filename
    else:
        raise TypeError(f"Unsupported upload format: {type(upload)}")

    # 7. Create child through existing submission pipeline
    child_sequence = (parent.renewal_sequence or 1) + 1
    child = FDRSubmissionService.submit_instrument(
        db=session,
        form=form_data,
        file_bytes=file_bytes,
        raw_filename=file_name,
        user=user,
        actor_ip=actor_ip,
        vault_base=vault_base,
        master_key_b64=master_key_b64,
        previous_instrument_id=parent.id,
        renewal_sequence=child_sequence,
    )

    # 8. Record RENEWAL_INITIATED audit entry
    AuditService.append(
        session=session,
        entity_type="INSTRUMENT",
        entity_id=child.id,
        action="RENEWAL_INITIATED",
        actor_user_id=actor_obj.user_id,
        actor_role=actor_obj.role,
        details={
            "parent_id": str(parent.id),
            "parent_fdr_number": parent.fdr_number,
            "child_id": str(child.id),
            "child_fdr_number": child.fdr_number,
            "sequence": child.renewal_sequence,
        },
        actor_ip=actor_ip,
    )

    return child
