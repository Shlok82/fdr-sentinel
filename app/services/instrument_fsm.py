import uuid
from dataclasses import dataclass
from enum import Enum
from datetime import datetime
from typing import Optional, Dict, Any, Set, Tuple
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models.instrument import Instrument
from app.models.audit import AuditLog
from app.models.user import User
from app.services.audit_service import AuditService
from app.services.clock_service import clock_service


# -----------------------------------------------------------------------------
# 1. Actor Model
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Actor:
    """Unified security principal performing actions within FDR Sentinel.
    
    Fields:
    - user_id: UUID of the authenticated user, or None for automated background operations.
    - role: Application role string ('INSTITUTION_ADMIN', 'SCRUTINY_OFFICER',
            'APPROVER_AUTHORITY', 'AUDITOR', 'SYSTEM').
    - institution_id: UUID of the institution if caller is scoped, else None.
    - is_demo_admin: True if user is the designated demo administrator.
    """
    user_id: Optional[uuid.UUID] = None
    role: str = "SYSTEM"
    institution_id: Optional[uuid.UUID] = None
    is_demo_admin: bool = False

    @classmethod
    def from_user(cls, user: Any) -> "Actor":
        """Builds an Actor instance from an authenticated User model or user-like object."""
        return cls(
            user_id=getattr(user, "id", None),
            role=getattr(user, "role", "UNKNOWN"),
            institution_id=getattr(user, "institution_id", None),
            is_demo_admin=bool(getattr(user, "is_demo_admin", False)),
        )


# Singleton actor for automated background and scheduled transitions
SYSTEM_ACTOR = Actor(user_id=None, role="SYSTEM", institution_id=None, is_demo_admin=False)


# -----------------------------------------------------------------------------
# 2. State Definitions & Typed Exceptions
# -----------------------------------------------------------------------------

class InstrumentState(str, Enum):
    SUBMITTED = "SUBMITTED"
    UNDER_SCRUTINY = "UNDER_SCRUTINY"
    DISCREPANCY_FLAGGED = "DISCREPANCY_FLAGGED"
    VERIFIED = "VERIFIED"
    ACTIVE = "ACTIVE"
    EXPIRING = "EXPIRING"
    MATURED = "MATURED"
    RENEWED = "RENEWED"
    RELEASE_REQUESTED = "RELEASE_REQUESTED"
    RELEASED = "RELEASED"
    REJECTED = "REJECTED"


ALL_STATES: frozenset[str] = frozenset(s.value for s in InstrumentState)

TERMINAL_STATES: frozenset[str] = frozenset({
    InstrumentState.RENEWED.value,
    InstrumentState.RELEASED.value,
    InstrumentState.REJECTED.value,
})

# States from which an institution admin can request release
RELEASE_REQUEST_ORIGINS: frozenset[str] = frozenset({
    InstrumentState.ACTIVE.value,
    InstrumentState.EXPIRING.value,
    InstrumentState.MATURED.value,
})


class FSMError(Exception):
    """Base exception for all instrument finite state machine errors."""
    pass


class InstrumentNotFoundError(FSMError):
    """Raised when the specified instrument cannot be found in the database."""
    pass


class InvalidStateTransitionError(FSMError):
    """Raised when an illegal transition is attempted."""
    pass


class UnauthorizedTransitionError(FSMError):
    """Raised when an actor's role or institution scope does not permit the transition."""
    pass


class JustificationRequiredError(FSMError):
    """Raised when a mandatory justification is missing, blank, or too short."""
    pass


# -----------------------------------------------------------------------------
# 3. Central Transitions Table
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class TransitionRule:
    allowed_roles: frozenset[str]
    scope_rule: Optional[str] = None  # None or "OWN_INSTITUTION"
    justification_required: bool = False


# Central static edge definitions (from_state, to_state) -> TransitionRule
TRANSITIONS: Dict[Tuple[str, str], TransitionRule] = {
    # SUBMITTED -> UNDER_SCRUTINY: SCRUTINY_OFFICER or SYSTEM
    ("SUBMITTED", "UNDER_SCRUTINY"): TransitionRule(
        allowed_roles=frozenset({"SCRUTINY_OFFICER", "SYSTEM"}),
        scope_rule=None,
        justification_required=False,
    ),
    # UNDER_SCRUTINY -> DISCREPANCY_FLAGGED: SCRUTINY_OFFICER or SYSTEM
    ("UNDER_SCRUTINY", "DISCREPANCY_FLAGGED"): TransitionRule(
        allowed_roles=frozenset({"SCRUTINY_OFFICER", "SYSTEM"}),
        scope_rule=None,
        justification_required=False,
    ),
    # DISCREPANCY_FLAGGED -> SUBMITTED: INSTITUTION_ADMIN (own institution only)
    ("DISCREPANCY_FLAGGED", "SUBMITTED"): TransitionRule(
        allowed_roles=frozenset({"INSTITUTION_ADMIN"}),
        scope_rule="OWN_INSTITUTION",
        justification_required=False,
    ),
    # UNDER_SCRUTINY -> VERIFIED: SCRUTINY_OFFICER (human-driven: mandatory justification)
    ("UNDER_SCRUTINY", "VERIFIED"): TransitionRule(
        allowed_roles=frozenset({"SCRUTINY_OFFICER"}),
        scope_rule=None,
        justification_required=True,
    ),
    # VERIFIED -> ACTIVE: SCRUTINY_OFFICER or APPROVER_AUTHORITY
    ("VERIFIED", "ACTIVE"): TransitionRule(
        allowed_roles=frozenset({"SCRUTINY_OFFICER", "APPROVER_AUTHORITY"}),
        scope_rule=None,
        justification_required=False,
    ),
    # ACTIVE -> EXPIRING: SYSTEM only
    ("ACTIVE", "EXPIRING"): TransitionRule(
        allowed_roles=frozenset({"SYSTEM"}),
        scope_rule=None,
        justification_required=False,
    ),
    # EXPIRING -> MATURED: SYSTEM only
    ("EXPIRING", "MATURED"): TransitionRule(
        allowed_roles=frozenset({"SYSTEM"}),
        scope_rule=None,
        justification_required=False,
    ),
    # EXPIRING -> RENEWED: SYSTEM only
    ("EXPIRING", "RENEWED"): TransitionRule(
        allowed_roles=frozenset({"SYSTEM"}),
        scope_rule=None,
        justification_required=False,
    ),
    # MATURED -> RENEWED: SYSTEM only
    ("MATURED", "RENEWED"): TransitionRule(
        allowed_roles=frozenset({"SYSTEM"}),
        scope_rule=None,
        justification_required=False,
    ),
    # ACTIVE -> RELEASE_REQUESTED: INSTITUTION_ADMIN (own institution, mandatory justification)
    ("ACTIVE", "RELEASE_REQUESTED"): TransitionRule(
        allowed_roles=frozenset({"INSTITUTION_ADMIN"}),
        scope_rule="OWN_INSTITUTION",
        justification_required=True,
    ),
    # EXPIRING -> RELEASE_REQUESTED: INSTITUTION_ADMIN (own institution, mandatory justification)
    ("EXPIRING", "RELEASE_REQUESTED"): TransitionRule(
        allowed_roles=frozenset({"INSTITUTION_ADMIN"}),
        scope_rule="OWN_INSTITUTION",
        justification_required=True,
    ),
    # MATURED -> RELEASE_REQUESTED: INSTITUTION_ADMIN (own institution, mandatory justification)
    ("MATURED", "RELEASE_REQUESTED"): TransitionRule(
        allowed_roles=frozenset({"INSTITUTION_ADMIN"}),
        scope_rule="OWN_INSTITUTION",
        justification_required=True,
    ),
    # RELEASE_REQUESTED -> RELEASED: APPROVER_AUTHORITY (mandatory justification)
    ("RELEASE_REQUESTED", "RELEASED"): TransitionRule(
        allowed_roles=frozenset({"APPROVER_AUTHORITY"}),
        scope_rule=None,
        justification_required=True,
    ),
    # UNDER_SCRUTINY -> REJECTED: APPROVER_AUTHORITY only (mandatory justification)
    ("UNDER_SCRUTINY", "REJECTED"): TransitionRule(
        allowed_roles=frozenset({"APPROVER_AUTHORITY"}),
        scope_rule=None,
        justification_required=True,
    ),
    # DISCREPANCY_FLAGGED -> REJECTED: APPROVER_AUTHORITY only (mandatory justification)
    ("DISCREPANCY_FLAGGED", "REJECTED"): TransitionRule(
        allowed_roles=frozenset({"APPROVER_AUTHORITY"}),
        scope_rule=None,
        justification_required=True,
    ),
}

# Special rule for release-rejection edge: RELEASE_REQUESTED -> prior_state
RELEASE_REJECTION_RULE = TransitionRule(
    allowed_roles=frozenset({"APPROVER_AUTHORITY"}),
    scope_rule=None,
    justification_required=True,
)


# -----------------------------------------------------------------------------
# 4. Single Entry Point: transition()
# -----------------------------------------------------------------------------

def transition(
    session: Session,
    instrument_id: uuid.UUID | str,
    to_state: str,
    actor: Actor,
    justification: Optional[str] = None,
) -> Instrument:
    """Executes an atomic, validated lifecycle state transition on an instrument.
    
    Guarantees:
    - Serializes transition via SELECT ... FOR UPDATE on the instrument row.
    - Strictly rejects self-transitions and transitions from terminal states.
    - Validates actor role against central transitions table.
    - AUDITOR and demo_admin can never transition anything.
    - Enforces own-institution scoping for INSTITUTION_ADMIN transitions.
    - Enforces mandatory, non-blank justification (>= 10 characters) for human-driven
      VERIFIED, REJECTED, RELEASED, RELEASE_REQUESTED, and release-rejection.
    - System transitions supply an automated reason if none provided.
    - Sets prior_state upon entering RELEASE_REQUESTED, and on release-rejection
      restores strictly prior_state before clearing it.
    - Updates status and updated_at via ClockService.
    - Emits exactly ONE STATE_TRANSITION cryptographic audit log entry via AuditService
      in the SAME transaction.
    - Illegal transitions raise typed exceptions and change / write NOTHING.
    - Does NOT commit; caller retains transaction boundary control.
    """
    if isinstance(instrument_id, str):
        try:
            instrument_id = uuid.UUID(instrument_id)
        except ValueError:
            raise InstrumentNotFoundError(f"Invalid UUID: {instrument_id}")

    # 1. Acquire transaction row lock on the target instrument
    stmt = select(Instrument).where(Instrument.id == instrument_id).with_for_update()
    instrument = session.execute(stmt).scalar_one_or_none()
    if instrument is None:
        raise InstrumentNotFoundError(f"Instrument '{instrument_id}' does not exist.")

    from_state = instrument.status

    # 2. AUDITOR and demo_admin are strictly forbidden from transitioning any instrument
    if actor.is_demo_admin or actor.role == "AUDITOR":
        raise UnauthorizedTransitionError(
            f"Actor with role '{actor.role}' (demo_admin={actor.is_demo_admin}) is not authorized to transition instruments."
        )

    # 3. Validate state name
    if to_state not in ALL_STATES:
        raise InvalidStateTransitionError(f"Target state '{to_state}' is not a valid instrument state.")

    # 4. Reject self-transitions
    if to_state == from_state:
        raise InvalidStateTransitionError(
            f"Instrument is already in state '{from_state}'. Self-transitions are not permitted."
        )

    # 5. Reject transitions from terminal states
    if from_state in TERMINAL_STATES:
        raise InvalidStateTransitionError(
            f"Instrument is in terminal state '{from_state}' and cannot transition further."
        )

    # 6. Resolve transition rule (handles static edges and dynamic release-rejection)
    rule: Optional[TransitionRule] = None
    is_release_rejection = False

    if from_state == "RELEASE_REQUESTED":
        if to_state == "RELEASED":
            rule = TRANSITIONS.get(("RELEASE_REQUESTED", "RELEASED"))
        elif instrument.prior_state and to_state == instrument.prior_state:
            rule = RELEASE_REJECTION_RULE
            is_release_rejection = True
        else:
            raise InvalidStateTransitionError(
                f"Invalid transition from RELEASE_REQUESTED to '{to_state}'. "
                f"Permitted destinations are 'RELEASED' or prior state '{instrument.prior_state}'."
            )
    else:
        rule = TRANSITIONS.get((from_state, to_state))
        if rule is None:
            raise InvalidStateTransitionError(
                f"Invalid state transition from '{from_state}' to '{to_state}'."
            )

    # 7. Validate Actor Role
    if actor.role not in rule.allowed_roles:
        raise UnauthorizedTransitionError(
            f"Role '{actor.role}' is not authorized to transition from '{from_state}' to '{to_state}'. "
            f"Allowed roles: {sorted(list(rule.allowed_roles))}."
        )

    # 8. Validate Institution Scope
    if rule.scope_rule == "OWN_INSTITUTION":
        if not actor.institution_id or actor.institution_id != instrument.institution_id:
            raise UnauthorizedTransitionError(
                f"Actor institution '{actor.institution_id}' does not match instrument institution '{instrument.institution_id}'."
            )

    # 9. Validate Justification
    final_justification: str
    if actor.role == "SYSTEM":
        if justification and justification.strip():
            final_justification = justification.strip()
        else:
            final_justification = f"Automated system transition from {from_state} to {to_state}."
    else:
        # Human actor
        if rule.justification_required or is_release_rejection:
            if not justification or len(justification.strip()) < 10:
                raise JustificationRequiredError(
                    f"A non-blank justification of at least 10 characters is mandatory for transition from '{from_state}' to '{to_state}'."
                )
            final_justification = justification.strip()
        else:
            if justification and justification.strip():
                final_justification = justification.strip()
            else:
                final_justification = f"Transitioned from {from_state} to {to_state} by {actor.role}."

    # 10. Update instrument state and prior-state tracker
    if to_state == "RELEASE_REQUESTED":
        instrument.prior_state = from_state
    elif from_state == "RELEASE_REQUESTED":
        instrument.prior_state = None

    instrument.status = to_state
    current_time = clock_service.now()
    instrument.updated_at = current_time

    # 11. Write ONE STATE_TRANSITION audit entry in the SAME transaction
    AuditService.append(
        session=session,
        entity_type="INSTRUMENT",
        entity_id=instrument.id,
        action="STATE_TRANSITION",
        actor_user_id=actor.user_id,
        actor_role=actor.role,
        details={
            "from_state": from_state,
            "to_state": to_state,
            "justification": final_justification,
        },
    )

    return instrument


# -----------------------------------------------------------------------------
# 5. Timeline Helper (Read-Only)
# -----------------------------------------------------------------------------

@dataclass
class TimelineEvent:
    timestamp: datetime
    action: str
    from_state: Optional[str]
    to_state: str
    actor_role: str
    actor_name: Optional[str]
    justification: Optional[str]


def get_instrument_timeline(session: Session, instrument: Instrument) -> list[TimelineEvent]:
    """Retrieves ordered timeline events from audit log for an instrument.
    
    Guarantees:
    - Pure read operation (writes zero audit rows).
    - Falls back gracefully to instrument.created_at if no audit entries exist.
    - Handles missing or unusual audit payload structures safely without crashing.
    """
    audit_rows = (
        session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "INSTRUMENT",
            AuditLog.entity_id == instrument.id,
            AuditLog.action.in_(["INSTRUMENT_SUBMITTED", "STATE_TRANSITION"]),
        )
        .order_by(AuditLog.id.asc())
        .all()
    )

    if not audit_rows:
        return [
            TimelineEvent(
                timestamp=instrument.created_at,
                action="INSTRUMENT_SUBMITTED",
                from_state=None,
                to_state="SUBMITTED",
                actor_role="INSTITUTION_ADMIN",
                actor_name="Institution Submitter",
                justification="Initial FDR deposit submission.",
            )
        ]

    # Batch-lookup user names to prevent N+1 queries
    user_ids = {row.actor_user_id for row in audit_rows if row.actor_user_id}
    user_map = {}
    if user_ids:
        users = session.query(User).filter(User.id.in_(user_ids)).all()
        user_map = {u.id: (u.full_name or u.username) for u in users}

    timeline: list[TimelineEvent] = []
    for row in audit_rows:
        payload = row.canonical_payload or {}
        details = payload.get("details")
        if not isinstance(details, dict):
            details = {}

        actor_role = payload.get("actor_role") or "SYSTEM"
        actor_name = user_map.get(row.actor_user_id)
        row_time = row.event_timestamp or instrument.created_at

        if row.action == "INSTRUMENT_SUBMITTED":
            timeline.append(
                TimelineEvent(
                    timestamp=row_time,
                    action="INSTRUMENT_SUBMITTED",
                    from_state=None,
                    to_state=InstrumentState.SUBMITTED.value,
                    actor_role=actor_role,
                    actor_name=actor_name,
                    justification=details.get("justification") or details.get("note") or "Initial FDR deposit submission.",
                )
            )
        elif row.action == "STATE_TRANSITION":
            timeline.append(
                TimelineEvent(
                    timestamp=row_time,
                    action="STATE_TRANSITION",
                    from_state=details.get("from_state"),
                    to_state=details.get("to_state") or instrument.status,
                    actor_role=actor_role,
                    actor_name=actor_name,
                    justification=details.get("justification") or "—",
                )
            )

    return timeline


# -----------------------------------------------------------------------------
# 6. Status Aggregations & Dashboard Counters
# -----------------------------------------------------------------------------

def compute_status_counts(
    db: Session,
    institution_id: Optional[uuid.UUID] = None,
) -> Dict[str, int]:
    """Computes exact instrument counts across all 11 FSM states and TOTAL.
    
    Args:
        db: Active SQLAlchemy database session.
        institution_id: UUID of institution to scope counts. If None, computes
            unscoped counts across all institutions (for regulatory staff roles).
            
    Returns:
        Dict mapping all 11 InstrumentState values to their count (int >= 0),
        plus:
        - "TOTAL": Sum of all instruments in scope.
        - "OTHER": Count of instruments in states other than SUBMITTED, ACTIVE,
                   or DISCREPANCY_FLAGGED (for the institution combined card).
    """
    query = db.query(Instrument.status, func.count(Instrument.id))
    if institution_id is not None:
        query = query.filter(Instrument.institution_id == institution_id)

    grouped = dict(query.group_by(Instrument.status).all())

    # Initialize all 11 states to 0 so every state key is always present
    counts: Dict[str, int] = {
        state.value: grouped.get(state.value, 0)
        for state in InstrumentState
    }

    total = sum(counts.values())
    counts["TOTAL"] = total

    # Combined "In progress / other" count: Total minus (Submitted + Active + Flagged)
    submitted = counts[InstrumentState.SUBMITTED.value]
    active = counts[InstrumentState.ACTIVE.value]
    flagged = counts[InstrumentState.DISCREPANCY_FLAGGED.value]
    counts["OTHER"] = total - (submitted + active + flagged)

    # Approver specific metrics:
    # 1. Action Items: sum of states needing authority attention
    counts["APPROVER_ACTION_ITEMS"] = (
        counts[InstrumentState.UNDER_SCRUTINY.value]
        + counts[InstrumentState.DISCREPANCY_FLAGGED.value]
        + counts[InstrumentState.VERIFIED.value]
        + counts[InstrumentState.RELEASE_REQUESTED.value]
    )
    # 2. Other: remaining states not displayed as primary cards on approver
    counts["APPROVER_OTHER"] = (
        counts[InstrumentState.SUBMITTED.value]
        + counts[InstrumentState.EXPIRING.value]
        + counts[InstrumentState.MATURED.value]
        + counts[InstrumentState.RENEWED.value]
        + counts[InstrumentState.REJECTED.value]
    )

    # Scrutiny specific other:
    counts["SCRUTINY_OTHER"] = (
        counts[InstrumentState.EXPIRING.value]
        + counts[InstrumentState.MATURED.value]
        + counts[InstrumentState.RENEWED.value]
        + counts[InstrumentState.RELEASED.value]
        + counts[InstrumentState.REJECTED.value]
    )

    # Auditor specific other:
    counts["AUDITOR_OTHER"] = (
        counts[InstrumentState.EXPIRING.value]
        + counts[InstrumentState.MATURED.value]
        + counts[InstrumentState.RENEWED.value]
        + counts[InstrumentState.RELEASE_REQUESTED.value]
        + counts[InstrumentState.REJECTED.value]
    )

    return counts
