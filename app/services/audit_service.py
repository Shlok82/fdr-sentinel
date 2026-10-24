import json
import uuid
import hashlib
from datetime import datetime, timezone
from typing import Optional, Dict, Any, NamedTuple
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.models.audit import AuditLog

# Fixed 64-bit integer constant for PostgreSQL advisory transaction lock
AUDIT_CHAIN_LOCK_KEY = 26265001
GENESIS_HASH = "0" * 64


class VerifyResult(NamedTuple):
    ok: bool
    first_broken_id: Optional[int]
    reason: Optional[str]
    rows_checked: int


def format_iso_timestamp(dt: datetime) -> str:
    """Format UTC datetime into standardized ISO string with 6 microsecond digits."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def canonical_json(data: Dict[str, Any]) -> str:
    """Serialize dictionary to canonical deterministic JSON string."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_entry_hash(previous_hash: str, canonical_payload_str: str) -> str:
    """Compute SHA-256 entry hash over previous hash concatenated with canonical payload."""
    payload_bytes = (previous_hash + canonical_payload_str).encode("utf-8")
    return hashlib.sha256(payload_bytes).hexdigest()


def build_event_data(
    id: int,
    event_timestamp: datetime | str,
    entity_type: str,
    entity_id: uuid.UUID | str,
    action: str,
    actor_user_id: Optional[uuid.UUID | str],
    actor_role: Optional[str],
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Shared single serialization builder for canonical event data in append and verify."""
    ts_str = format_iso_timestamp(event_timestamp) if isinstance(event_timestamp, datetime) else str(event_timestamp)
    actor_id_str = str(actor_user_id).lower() if actor_user_id else None
    role_str = actor_role if actor_id_str else "SYSTEM"
    details_dict = details if details is not None else {}

    return {
        "id": int(id),
        "event_timestamp": ts_str,
        "entity_type": str(entity_type),
        "entity_id": str(entity_id).lower(),
        "action": str(action),
        "actor_user_id": actor_id_str,
        "actor_role": role_str,
        "details": details_dict,
    }


class AuditService:
    """Tamper-detecting cryptographic audit log service.
    
    DEVIATIONS & EXTENSIONS from SPEC:
    1. Previous-Row Chaining (DEC-010): The previous hash is computed from the record
       with the highest id rather than strictly id - 1. This prevents broken chains
       when rolled-back database transactions leave gaps in the PostgreSQL sequence.
    2. Tamper-Evident Details Payload (DEC-011): The canonical payload binds 'details'
       alongside the 7 SPEC fields, ensuring state changes (from_state, to_state, justification)
       are cryptographically sealed against modification.
    3. Actor IP Address: Stored in its own column for operational triage, but deliberately
       omitted from the cryptographic hash payload.
    """

    @staticmethod
    def append(
        session: Session,
        *,
        entity_type: str,
        entity_id: uuid.UUID | str,
        action: str,
        actor_user_id: Optional[uuid.UUID | str],
        actor_role: Optional[str],
        details: Optional[Dict[str, Any]] = None,
        actor_ip: Optional[str] = None,
    ) -> AuditLog:
        """Appends a cryptographically chained audit log record within caller's transaction."""
        # 1. Acquire transaction-level advisory lock on the audit chain
        session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": AUDIT_CHAIN_LOCK_KEY}
        )

        # 2. Allocate the sequence ID
        row_id = session.execute(text("SELECT nextval('audit_log_id_seq')")).scalar()

        # 3. Retrieve authoritative application time from ClockService
        from app.services.clock_service import clock_service
        current_time = clock_service.now()

        # 4. Read the entry_hash of the row with the HIGHEST existing id
        prev_row = session.execute(
            text("SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1")
        ).fetchone()
        previous_hash = prev_row[0] if prev_row else GENESIS_HASH

        # 5. Build canonical event data dictionary
        event_data = build_event_data(
            id=row_id,
            event_timestamp=current_time,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            details=details,
        )

        # 6. Compute SHA-256 hash over previous_hash + canonical_json
        canonical_str = canonical_json(event_data)
        entry_hash = compute_entry_hash(previous_hash, canonical_str)

        # 7. Insert the audit record
        # Note: actor_ip is stored in its dedicated column, outside the cryptographic hash.
        audit_record = AuditLog(
            id=row_id,
            event_timestamp=current_time,
            entity_type=str(entity_type),
            entity_id=uuid.UUID(str(entity_id)),
            action=str(action),
            actor_user_id=uuid.UUID(str(actor_user_id)) if actor_user_id else None,
            actor_ip_address=actor_ip,
            canonical_payload=event_data,
            previous_hash=previous_hash,
            entry_hash=entry_hash,
        )
        session.add(audit_record)
        session.flush()

        return audit_record


def verify_chain(session: Session) -> VerifyResult:
    """Verifies the integrity of the audit log hash chain in linear time.
    
    Streams rows ordered by id ascending. Detects:
    - GENESIS_INVALID: First row previous_hash is not 64 zeros.
    - CHAIN_LINK_BROKEN: Row's previous_hash != previous row's entry_hash.
    - PAYLOAD_MISMATCH: Stored canonical_payload != rebuilt event_data from columns.
    - HASH_MISMATCH: Stored entry_hash != SHA-256(previous_hash + canonical_payload).
    
    Known limitation: Deleting trailing rows leaves a valid shorter chain.
    Signed head checkpoints (SPEC §9.4) will be added in a later tier.
    """
    rows_query = session.query(AuditLog).order_by(AuditLog.id.asc()).yield_per(500)

    rows_checked = 0
    prior_entry_hash: Optional[str] = None

    for row in rows_query:
        # Genesis verification on first row
        if rows_checked == 0:
            if row.previous_hash != GENESIS_HASH:
                return VerifyResult(
                    ok=False,
                    first_broken_id=row.id,
                    reason="GENESIS_INVALID",
                    rows_checked=0
                )
        else:
            # Check linkage to preceding row's entry hash
            if row.previous_hash != prior_entry_hash:
                return VerifyResult(
                    ok=False,
                    first_broken_id=row.id,
                    reason="CHAIN_LINK_BROKEN",
                    rows_checked=rows_checked
                )

        # Rebuild event_data from column values
        row_role = row.canonical_payload.get("actor_role") if isinstance(row.canonical_payload, dict) else None
        row_details = row.canonical_payload.get("details", {}) if isinstance(row.canonical_payload, dict) else {}
        rebuilt_event_data = build_event_data(
            id=row.id,
            event_timestamp=row.event_timestamp,
            entity_type=row.entity_type,
            entity_id=row.entity_id,
            action=row.action,
            actor_user_id=row.actor_user_id,
            actor_role=row_role,
            details=row_details,
        )

        # Verify stored canonical_payload matches rebuilt event_data
        if row.canonical_payload != rebuilt_event_data:
            return VerifyResult(
                ok=False,
                first_broken_id=row.id,
                reason="PAYLOAD_MISMATCH",
                rows_checked=rows_checked
            )

        # Recompute entry hash
        recomputed_hash = compute_entry_hash(row.previous_hash, canonical_json(rebuilt_event_data))
        if recomputed_hash != row.entry_hash:
            return VerifyResult(
                ok=False,
                first_broken_id=row.id,
                reason="HASH_MISMATCH",
                rows_checked=rows_checked
            )

        prior_entry_hash = row.entry_hash
        rows_checked += 1

    return VerifyResult(
        ok=True,
        first_broken_id=None,
        reason=None,
        rows_checked=rows_checked
    )
