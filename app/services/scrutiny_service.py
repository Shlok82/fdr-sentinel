import uuid
from typing import Optional, List, Tuple, Dict, Any
from sqlalchemy.orm import Session

from app.models.instrument import Instrument
from app.models.scrutiny import ScrutinyNote
from app.models.discrepancy import DiscrepancyTicket
from app.models.verification import VerificationRecord
from app.models.user import User
from app.services.audit_service import AuditService
from app.services.clock_service import clock_service
from app.services.instrument_fsm import (
    transition,
    Actor,
    InstrumentState,
    TERMINAL_STATES,
    InvalidStateTransitionError,
    UnauthorizedTransitionError,
)
from app.services.verification.base import VerificationResult
from app.services.verification.mock_bank import MockFailureMode
from app.services.verification.service import VerificationService


class ScrutinyError(Exception):
    """Base exception for scrutiny workstation operations."""
    pass


class ScrutinyRoleError(ScrutinyError):
    """Raised when an unauthorized role attempts a scrutiny action."""
    pass


class ScrutinyGuardError(ScrutinyError):
    """Raised when statutory or algorithmic verification preconditions fail."""
    pass


class StaleStateError(ScrutinyError):
    """Raised when an action is attempted on an instrument in an incompatible lifecycle state."""
    pass


class JustificationError(ScrutinyError):
    """Raised when a mandatory regulatory justification is missing or too short."""
    pass


class ScrutinyService:
    """Orchestrates regulatory scrutiny actions, guards, and status transitions."""

    @classmethod
    def _validate_justification(cls, text: Optional[str], min_length: int = 10) -> str:
        clean = (text or "").strip()
        if not clean or len(clean) < min_length:
            raise JustificationError(
                f"Regulatory justification must be at least {min_length} non-blank characters."
            )
        return clean

    @classmethod
    def start_scrutiny(
        cls,
        db: Session,
        instrument_id: uuid.UUID,
        user: User,
        justification: Optional[str] = None,
        actor_ip: Optional[str] = None,
    ) -> Instrument:
        """Transitions instrument SUBMITTED -> UNDER_SCRUTINY."""
        if user.role != "SCRUTINY_OFFICER":
            raise ScrutinyRoleError("Only Scrutiny Officers can initiate formal review.")

        instrument = db.query(Instrument).filter(Instrument.id == instrument_id).first()
        if not instrument:
            raise StaleStateError("Instrument not found.")

        if instrument.status != InstrumentState.SUBMITTED.value:
            raise StaleStateError(
                f"Cannot start scrutiny: instrument is in status '{instrument.status}', expected 'SUBMITTED'."
            )

        clean_just = cls._validate_justification(
            justification or "Initiated formal regulatory document scrutiny."
        )

        actor = Actor.from_user(user)
        return transition(
            session=db,
            instrument_id=instrument.id,
            to_state=InstrumentState.UNDER_SCRUTINY.value,
            actor=actor,
            justification=clean_just,
        )

    @classmethod
    def run_mock_verification(
        cls,
        db: Session,
        instrument_id: uuid.UUID,
        user: User,
        failure_mode: MockFailureMode = MockFailureMode.NONE,
        actor_ip: Optional[str] = None,
    ) -> Tuple[VerificationRecord, VerificationResult]:
        """Runs simulated CBS verification for an instrument in UNDER_SCRUTINY."""
        if user.role != "SCRUTINY_OFFICER":
            raise ScrutinyRoleError("Only Scrutiny Officers can execute bank verification.")

        instrument = db.query(Instrument).filter(Instrument.id == instrument_id).first()
        if not instrument:
            raise StaleStateError("Instrument not found.")

        if instrument.status != InstrumentState.UNDER_SCRUTINY.value:
            raise StaleStateError(
                f"Bank verification can only be executed while instrument is UNDER_SCRUTINY (current: '{instrument.status}')."
            )

        return VerificationService.execute_mock_verification(
            db=db,
            instrument=instrument,
            officer=user,
            failure_mode=failure_mode,
            actor_ip=actor_ip,
        )

    @classmethod
    def record_manual_confirmation(
        cls,
        db: Session,
        instrument_id: uuid.UUID,
        user: User,
        file_bytes: bytes,
        file_name: str,
        reference_number: str,
        memo: str,
        actor_ip: Optional[str] = None,
    ) -> Tuple[VerificationRecord, VerificationResult]:
        """Records official bank confirmation letter for an instrument in UNDER_SCRUTINY."""
        if user.role != "SCRUTINY_OFFICER":
            raise ScrutinyRoleError("Only Scrutiny Officers can record bank confirmation letters.")

        instrument = db.query(Instrument).filter(Instrument.id == instrument_id).first()
        if not instrument:
            raise StaleStateError("Instrument not found.")

        if instrument.status != InstrumentState.UNDER_SCRUTINY.value:
            raise StaleStateError(
                f"Manual confirmation can only be recorded while instrument is UNDER_SCRUTINY (current: '{instrument.status}')."
            )

        return VerificationService.execute_manual_confirmation(
            db=db,
            instrument=instrument,
            officer=user,
            file_bytes=file_bytes,
            file_name=file_name,
            reference_number=reference_number,
            memo=memo,
            actor_ip=actor_ip,
        )

    @classmethod
    def add_scrutiny_note(
        cls,
        db: Session,
        instrument_id: uuid.UUID,
        user: User,
        note_text: str,
        action_recommended: str,
        actor_ip: Optional[str] = None,
    ) -> ScrutinyNote:
        """Adds an internal staff scrutiny note in any non-terminal state."""
        if user.role != "SCRUTINY_OFFICER":
            raise ScrutinyRoleError("Only Scrutiny Officers can attach scrutiny notes.")

        instrument = db.query(Instrument).filter(Instrument.id == instrument_id).first()
        if not instrument:
            raise StaleStateError("Instrument not found.")

        if instrument.status in TERMINAL_STATES:
            raise StaleStateError(
                f"Cannot add scrutiny notes to terminated instruments (status: '{instrument.status}')."
            )

        if action_recommended not in ("CLEAR", "FLAG_DISCREPANCY", "REJECT"):
            raise ValueError(f"Invalid recommendation: '{action_recommended}'. Must be CLEAR, FLAG_DISCREPANCY, or REJECT.")

        clean_note = cls._validate_justification(note_text, min_length=10)

        note = ScrutinyNote(
            id=uuid.uuid4(),
            instrument_id=instrument.id,
            officer_id=user.id,
            note_text=clean_note,
            action_recommended=action_recommended,
            created_at=clock_service.now(),
        )
        db.add(note)

        AuditService.append(
            session=db,
            entity_type="INSTRUMENT",
            entity_id=instrument.id,
            action="SCRUTINY_NOTE_ADDED",
            actor_user_id=user.id,
            actor_role=user.role,
            details={
                "action_recommended": action_recommended,
                "note_length": len(clean_note),
                "note_id": str(note.id),
            },
            actor_ip=actor_ip,
        )

        return note

    @classmethod
    def flag_discrepancy(
        cls,
        db: Session,
        instrument_id: uuid.UUID,
        user: User,
        institution_message: Optional[str] = None,
        internal_justification: Optional[str] = None,
        reason: Optional[str] = None,
        actor_ip: Optional[str] = None,
    ) -> Instrument:
        """Transitions UNDER_SCRUTINY -> DISCREPANCY_FLAGGED with a formal officer ticket."""
        if user.role != "SCRUTINY_OFFICER":
            raise ScrutinyRoleError("Only Scrutiny Officers can flag discrepancies.")

        # Accept reason as shorthand for both messages
        msg = institution_message or reason
        just = internal_justification or msg

        if not msg:
            raise JustificationError("Reason or message for flagging discrepancy is mandatory.")

        instrument = db.query(Instrument).filter(Instrument.id == instrument_id).first()
        if not instrument:
            raise StaleStateError("Instrument not found.")

        if instrument.status != InstrumentState.UNDER_SCRUTINY.value:
            raise StaleStateError(
                f"Cannot flag discrepancy: instrument must be UNDER_SCRUTINY (current: '{instrument.status}')."
            )

        clean_inst_msg = cls._validate_justification(msg, min_length=10)
        clean_internal = cls._validate_justification(just, min_length=10)

        # 1. Create officer discrepancy ticket
        ticket = DiscrepancyTicket(
            id=uuid.uuid4(),
            instrument_id=instrument.id,
            rule_code="OFFICER-FLAG",
            severity="HIGH",
            blocking=True,
            status="OPEN",
            message=clean_inst_msg,
            staff_detail={
                "internal_justification": clean_internal,
                "officer_name": user.username,
                "officer_id": str(user.id),
            },
            created_at=clock_service.now(),
        )
        db.add(ticket)

        # 2. Add scrutiny note recording officer flag
        note = ScrutinyNote(
            id=uuid.uuid4(),
            instrument_id=instrument.id,
            officer_id=user.id,
            note_text=f"Flagged discrepancy: {clean_internal}",
            action_recommended="FLAG_DISCREPANCY",
            created_at=clock_service.now(),
        )
        db.add(note)

        # 3. Transition FSM
        actor = Actor.from_user(user)
        updated_inst = transition(
            session=db,
            instrument_id=instrument.id,
            to_state=InstrumentState.DISCREPANCY_FLAGGED.value,
            actor=actor,
            justification=clean_internal,
        )

        # 4. Audit discrepancy raised
        AuditService.append(
            session=db,
            entity_type="INSTRUMENT",
            entity_id=instrument.id,
            action="DISCREPANCY_RAISED",
            actor_user_id=user.id,
            actor_role=user.role,
            details={
                "rule_code": "OFFICER-FLAG",
                "severity": "HIGH",
                "blocking": True,
                "ticket_id": str(ticket.id),
                "officer": user.username,
            },
            actor_ip=actor_ip,
        )

        # 5. Recompute earned trust score
        from app.services.trust_score import recompute_trust
        recompute_trust(session=db, instrument_id=instrument.id)

        return updated_inst

    @classmethod
    def verify_instrument(
        cls,
        db: Session,
        instrument_id: uuid.UUID,
        user: User,
        justification: Optional[str] = None,
        actor_ip: Optional[str] = None,
    ) -> Instrument:
        """Transitions UNDER_SCRUTINY -> VERIFIED after enforcing all statutory preconditions."""
        if user.role != "SCRUTINY_OFFICER":
            raise ScrutinyRoleError("Only Scrutiny Officers can verify an instrument.")

        instrument = db.query(Instrument).filter(Instrument.id == instrument_id).first()
        if not instrument:
            raise StaleStateError("Instrument not found.")

        if instrument.status != InstrumentState.UNDER_SCRUTINY.value:
            raise StaleStateError(
                f"Cannot verify instrument: status must be UNDER_SCRUTINY (current: '{instrument.status}')."
            )

        clean_just = cls._validate_justification(
            justification or "Statutory pre-verification criteria satisfied.",
            min_length=10,
        )

        # Guard (i): No OPEN blocking discrepancy tickets
        open_tickets = (
            db.query(DiscrepancyTicket)
            .filter(
                DiscrepancyTicket.instrument_id == instrument.id,
                DiscrepancyTicket.status == "OPEN",
            )
            .all()
        )
        open_blocking = [t for t in open_tickets if t.blocking]
        if open_blocking:
            codes = ", ".join(t.rule_code for t in open_blocking)
            raise ScrutinyGuardError(
                f"Cannot verify instrument: open blocking discrepancy tickets exist ({codes})."
            )

        # Guard (ii): Latest verification record exists and is_verified is True
        latest_ver = (
            db.query(VerificationRecord)
            .filter(VerificationRecord.instrument_id == instrument.id)
            .order_by(VerificationRecord.executed_at.desc(), VerificationRecord.id.desc())
            .first()
        )
        if not latest_ver:
            raise ScrutinyGuardError(
                "Cannot verify instrument: verified bank confirmation record (CBS or manual letter) is required."
            )
        if not latest_ver.is_verified:
            raise ScrutinyGuardError(
                "Cannot verify instrument: latest bank verification attempt reported discrepancies or failed."
            )

        actor = Actor.from_user(user)
        return transition(
            session=db,
            instrument_id=instrument.id,
            to_state=InstrumentState.VERIFIED.value,
            actor=actor,
            justification=clean_just,
        )

    @classmethod
    def activate_instrument(
        cls,
        db: Session,
        instrument_id: uuid.UUID,
        user: User,
        justification: Optional[str] = None,
        actor_ip: Optional[str] = None,
    ) -> Instrument:
        """Transitions VERIFIED -> ACTIVE (SCRUTINY_OFFICER or APPROVER_AUTHORITY)."""
        if user.role not in ("SCRUTINY_OFFICER", "APPROVER_AUTHORITY"):
            raise ScrutinyRoleError("Only Scrutiny Officers or Competent Authority can activate verified deposits.")

        instrument = db.query(Instrument).filter(Instrument.id == instrument_id).first()
        if not instrument:
            raise StaleStateError("Instrument not found.")

        if instrument.status != InstrumentState.VERIFIED.value:
            raise StaleStateError(
                f"Cannot activate instrument: status must be VERIFIED (current: '{instrument.status}')."
            )

        clean_just = cls._validate_justification(
            justification or "Verified deposit formally committed into custody."
        )

        actor = Actor.from_user(user)
        return transition(
            session=db,
            instrument_id=instrument.id,
            to_state=InstrumentState.ACTIVE.value,
            actor=actor,
            justification=clean_just,
        )

    @classmethod
    def reject_instrument(
        cls,
        db: Session,
        instrument_id: uuid.UUID,
        user: User,
        justification: str,
        actor_ip: Optional[str] = None,
    ) -> Instrument:
        """Transitions UNDER_SCRUTINY or DISCREPANCY_FLAGGED -> REJECTED (APPROVER_AUTHORITY ONLY)."""
        if user.role != "APPROVER_AUTHORITY":
            raise ScrutinyRoleError(
                "Strict human-in-the-loop governance: only Competent Authority (APPROVER_AUTHORITY) can reject an instrument."
            )

        instrument = db.query(Instrument).filter(Instrument.id == instrument_id).first()
        if not instrument:
            raise StaleStateError("Instrument not found.")

        if instrument.status not in (InstrumentState.UNDER_SCRUTINY.value, InstrumentState.DISCREPANCY_FLAGGED.value):
            raise StaleStateError(
                f"Cannot reject instrument: status must be UNDER_SCRUTINY or DISCREPANCY_FLAGGED (current: '{instrument.status}')."
            )

        clean_just = cls._validate_justification(justification, min_length=10)

        actor = Actor.from_user(user)
        return transition(
            session=db,
            instrument_id=instrument.id,
            to_state=InstrumentState.REJECTED.value,
            actor=actor,
            justification=clean_just,
        )

    # Alias for convenience
    mark_verified = verify_instrument
