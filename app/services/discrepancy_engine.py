import io
import logging
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP, localcontext
from typing import Dict, Any, List, Optional, Union
import pypdf
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.instrument import Instrument
from app.models.institution import Institution
from app.models.document import Document
from app.models.alert import Alert
from app.models.discrepancy import DiscrepancyTicket
from app.core.discrepancy_config import REQUIRED_DEPOSIT_SLABS, SUSPICIOUS_PDF_GENERATORS
from app.services.audit_service import AuditService
from app.services.clock_service import clock_service
from app.services.document_service import DocumentService
from app.services.instrument_fsm import transition, Actor, SYSTEM_ACTOR, InstrumentState

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Data Contracts: Finding, NotEvaluated, CheckRunSummary
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Finding:
    """Represents a deterministic rule violation."""
    rule_code: str
    severity: str          # 'CRITICAL', 'HIGH', 'MEDIUM', 'LOW'
    blocking: bool         # True halts progression to ACTIVE / flags DISCREPANCY_FLAGGED
    message: str          # Safe for institution user to view (no private external info)
    staff_detail: Dict[str, Any]  # Detailed diagnostic info reserved for AICTE staff


@dataclass(frozen=True)
class NotEvaluated:
    """Represents a rule skipped due to missing inputs or unconfigured conditions."""
    rule_code: str
    reason: str


@dataclass
class CheckRunSummary:
    """Result of an orchestrator discrepancy check execution."""
    instrument_id: uuid.UUID
    status: str
    findings: List[Finding]
    not_evaluated: List[NotEvaluated]
    tickets_created: List[DiscrepancyTicket]
    tickets_resolved: List[DiscrepancyTicket]


# -----------------------------------------------------------------------------
# Pure Rule Evaluation Functions
# -----------------------------------------------------------------------------

def check_disc_01_mandatory_fields(instrument: Instrument) -> Optional[Finding]:
    """DISC-01: Missing Mandatory Fields (CRITICAL, blocking).
    
    Checks that core statutory submission fields are non-null and non-blank.
    """
    mandatory_fields = [
        ("fdr_number", instrument.fdr_number),
        ("bank_name", instrument.bank_name),
        ("bank_branch", instrument.bank_branch),
        ("ifsc_code", instrument.ifsc_code),
        ("principal_amount", instrument.principal_amount),
        ("maturity_date", instrument.maturity_date),
        ("joint_account_holder", instrument.joint_account_holder),
    ]
    missing = []
    for name, val in mandatory_fields:
        if val is None:
            missing.append(name)
        elif isinstance(val, str) and not val.strip():
            missing.append(name)

    if missing:
        return Finding(
            rule_code="DISC-01",
            severity="CRITICAL",
            blocking=True,
            message=f"Missing mandatory submission fields: {', '.join(missing)}.",
            staff_detail={"missing_fields": missing},
        )
    return None


def check_disc_02_chronological_inversion(
    instrument: Instrument,
    today: Optional[date] = None,
) -> Union[Finding, NotEvaluated, None]:
    """DISC-02: Chronological Inversion (CRITICAL, blocking).
    
    Triggers if maturity_date <= issue_date OR issue_date > today.
    Returns NOT_EVALUATED if dates are NULL.
    """
    if instrument.issue_date is None or instrument.maturity_date is None:
        return NotEvaluated("DISC-02", "issue_date or maturity_date is missing")

    if today is None:
        today = clock_service.today()

    errors = []
    reasons = []
    if instrument.maturity_date <= instrument.issue_date:
        errors.append("Maturity date is on or before the deposit issue date.")
        reasons.append("maturity_before_or_on_issue")
    if instrument.issue_date > today:
        errors.append("Deposit issue date cannot be in the future.")
        reasons.append("future_issue_date")

    if errors:
        return Finding(
            rule_code="DISC-02",
            severity="CRITICAL",
            blocking=True,
            message=" ".join(errors),
            staff_detail={
                "issue_date": instrument.issue_date.isoformat(),
                "maturity_date": instrument.maturity_date.isoformat(),
                "evaluation_date": today.isoformat(),
                "violation_types": reasons,
            },
        )
    return None


def check_disc_03_underfunding(
    instrument: Instrument,
    institution: Optional[Institution] = None,
) -> Union[Finding, NotEvaluated, None]:
    """DISC-03: Deposit Under-funding (HIGH, blocking).
    
    Triggers if principal_amount < required slab for institution_type.
    Only Engineering & Technology (₹35L) and Management (₹15L) are configured.
    Other institution types return NOT_EVALUATED per SPEC §7.
    """
    if instrument.principal_amount is None:
        return NotEvaluated("DISC-03", "principal_amount is missing")

    inst_type = institution.institution_type if institution else None
    if not inst_type or inst_type not in REQUIRED_DEPOSIT_SLABS:
        return NotEvaluated("DISC-03", "no slab configured for institution type")

    required_slab = REQUIRED_DEPOSIT_SLABS[inst_type]
    principal = Decimal(str(instrument.principal_amount))

    if principal < required_slab:
        deficit = required_slab - principal
        return Finding(
            rule_code="DISC-03",
            severity="HIGH",
            blocking=True,
            message=f"Deposit principal amount is below the required regulatory slab for {inst_type}.",
            staff_detail={
                "principal_amount": str(principal),
                "required_slab": str(required_slab),
                "deficit": str(deficit),
                "institution_type": inst_type,
                "assumption": "[ASSUMPTION TO VERIFY - APH Norm]",
                "program_count_note": "program_count is not in schema and is ignored per SPEC §7",
            },
        )
    return None


def check_disc_04_expired_instrument(
    instrument: Instrument,
    today: Optional[date] = None,
) -> Union[Finding, NotEvaluated, None]:
    """DISC-04: Expired Instrument Ingestion (CRITICAL, blocking).
    
    Triggers if maturity_date < today upon ingestion.
    """
    if instrument.maturity_date is None:
        return NotEvaluated("DISC-04", "maturity_date is missing")

    if today is None:
        today = clock_service.today()

    if instrument.maturity_date < today:
        return Finding(
            rule_code="DISC-04",
            severity="CRITICAL",
            blocking=True,
            message="Instrument maturity date is in the past. Expired instruments cannot be ingested.",
            staff_detail={
                "maturity_date": instrument.maturity_date.isoformat(),
                "evaluation_date": today.isoformat(),
            },
        )
    return None


def check_disc_05_cross_institution_duplicate(
    session: Session,
    instrument: Instrument,
    institution: Optional[Institution] = None,
) -> Union[Finding, NotEvaluated, None]:
    """DISC-05: Cross-Institution Duplicate (CRITICAL, blocking).
    
    Flags ONLY the newer submission if another instrument with the same normalized
    FDR number exists under a DIFFERENT aicte_permanent_id and is older.
    The incumbent instrument is never modified, locked, or ticketed.
    """
    if not instrument.fdr_number or not str(instrument.fdr_number).strip():
        return NotEvaluated("DISC-05", "fdr_number is missing")

    if not institution or not institution.aicte_permanent_id:
        return NotEvaluated("DISC-05", "institution permanent ID is missing")

    norm_fdr = instrument.fdr_number.strip().upper()

    candidates = (
        session.query(Instrument, Institution)
        .join(Institution, Instrument.institution_id == Institution.id)
        .filter(
            func.upper(func.trim(Instrument.fdr_number)) == norm_fdr,
            Instrument.id != instrument.id,
            Institution.aicte_permanent_id != institution.aicte_permanent_id,
        )
        .all()
    )

    if not candidates:
        return None

    # Filter strictly older candidates (created_at earlier, tie-broken by smaller UUID)
    older_candidates = []
    for cand_inst, cand_org in candidates:
        is_older = False
        if cand_inst.created_at < instrument.created_at:
            is_older = True
        elif cand_inst.created_at == instrument.created_at and str(cand_inst.id) < str(instrument.id):
            is_older = True
        if is_older:
            older_candidates.append((cand_inst, cand_org))

    if not older_candidates:
        # Other matches are newer; this instrument is the incumbent, so do not flag
        return None

    older_candidates.sort(key=lambda pair: (pair[0].created_at, str(pair[0].id)))
    incumbent_inst, incumbent_org = older_candidates[0]

    return Finding(
        rule_code="DISC-05",
        severity="CRITICAL",
        blocking=True,
        # Safe generic text; never leaks external institution name or ID to institution users
        message="This FDR number conflicts with an existing record and is under review.",
        staff_detail={
            "conflicting_instrument_id": str(incumbent_inst.id),
            "conflicting_institution_name": incumbent_org.name,
            "conflicting_aicte_permanent_id": incumbent_org.aicte_permanent_id,
            "conflicting_created_at": incumbent_inst.created_at.isoformat(),
            "normalized_fdr_number": norm_fdr,
        },
    )


def check_disc_06_maturity_math(instrument: Instrument) -> Union[Finding, NotEvaluated, None]:
    """DISC-06: Maturity Math Disparity (MEDIUM, advisory, non-blocking).
    
    Formula: A = P * (1 + r/400)^(4t) with 4t = tenure_months / 3.
    Flag if |declared_maturity - A| > max(0.025 * A, 500).
    Uses Decimal context exclusively without float casting.
    """
    if (
        instrument.principal_amount is None
        or instrument.interest_rate_percent is None
        or instrument.tenure_months is None
        or instrument.maturity_amount is None
    ):
        return NotEvaluated("DISC-06", "principal, rate, tenure, or maturity amount is missing")

    try:
        P = Decimal(str(instrument.principal_amount))
        r = Decimal(str(instrument.interest_rate_percent))
        tenure_months = Decimal(str(instrument.tenure_months))
        declared_A = Decimal(str(instrument.maturity_amount))

        with localcontext() as ctx:
            ctx.prec = 28
            rate_factor = Decimal("1") + (r / Decimal("400"))
            exponent = tenure_months / Decimal("3")
            expected_A = P * (rate_factor ** exponent)

        tolerance = max(Decimal("0.025") * expected_A, Decimal("500.00"))
        delta = abs(declared_A - expected_A)

        if delta > tolerance:
            return Finding(
                rule_code="DISC-06",
                severity="MEDIUM",
                blocking=False,
                message="Declared maturity amount deviates from quarterly compounding expectation.",
                staff_detail={
                    "expected_maturity_amount": str(expected_A.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                    "declared_maturity_amount": str(declared_A),
                    "delta": str(delta.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                    "tolerance": str(tolerance.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                    "formula": "A = P * (1 + r/400)^(4t)",
                },
            )
    except Exception as exc:
        return NotEvaluated("DISC-06", f"calculation error: {exc.__class__.__name__}")

    return None


def check_disc_08_suspicious_pdf_metadata(
    session: Session,
    instrument: Instrument,
    vault_base: Optional[str] = None,
    master_key_b64: Optional[str] = None,
) -> Union[Finding, NotEvaluated, None]:
    """DISC-08: Suspicious PDF Metadata (LOW, advisory, non-blocking).
    
    Decrypts the uploaded scan in-memory and parses metadata via pypdf.
    Flags if creation date is > 1 year after issue date or if generator
    matches configurable list of non-bank tools. Never logs file contents.
    """
    document = (
        session.query(Document)
        .filter(Document.instrument_id == instrument.id, Document.document_type == "FDR_SCAN")
        .first()
    )
    if not document or document.file_mime_type != "application/pdf":
        return NotEvaluated("DISC-08", "document is missing or not a PDF")

    try:
        pdf_bytes = DocumentService.read_and_decrypt_document(
            document,
            vault_base=vault_base,
            master_key_b64=master_key_b64,
        )
    except Exception as exc:
        return NotEvaluated("DISC-08", f"document decryption error: {exc.__class__.__name__}")

    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        metadata = reader.metadata
    except Exception:
        return NotEvaluated("DISC-08", "unparseable or corrupt PDF")

    if not metadata:
        return NotEvaluated("DISC-08", "no PDF metadata present")

    producer = str(metadata.producer or "")
    creator = str(metadata.creator or "")

    reasons = []

    # 1. Non-bank tool match
    for tool in SUSPICIOUS_PDF_GENERATORS:
        if tool.lower() in producer.lower() or tool.lower() in creator.lower():
            reasons.append(f"Generator matches non-bank authoring tool: {tool}")

    # 2. Creation date vs issue date
    pdf_creation_date = None
    try:
        if metadata.creation_date:
            cd = metadata.creation_date
            if hasattr(cd, "date"):
                pdf_creation_date = cd.date()
    except Exception:
        pass

    if pdf_creation_date and instrument.issue_date:
        if pdf_creation_date > (instrument.issue_date + timedelta(days=365)):
            reasons.append(
                f"PDF creation date ({pdf_creation_date.isoformat()}) is more than 1 year after instrument issue date ({instrument.issue_date.isoformat()})"
            )

    if reasons:
        return Finding(
            rule_code="DISC-08",
            severity="LOW",
            blocking=False,
            message="Document metadata indicates potential non-bank generation or chronological mismatch.",
            staff_detail={
                "producer": producer,
                "creator": creator,
                "pdf_creation_date": pdf_creation_date.isoformat() if pdf_creation_date else None,
                "issue_date": instrument.issue_date.isoformat() if instrument.issue_date else None,
                "suspicious_reasons": reasons,
                "assumption": "[ASSUMPTION TO VERIFY - Non-bank generator list]",
            },
        )

    return None


# -----------------------------------------------------------------------------
# Orchestrator: run_checks()
# -----------------------------------------------------------------------------

def run_checks(
    session: Session,
    instrument_id: Union[uuid.UUID, str],
    actor: Optional[Actor] = None,
    vault_base: Optional[str] = None,
    master_key_b64: Optional[str] = None,
) -> CheckRunSummary:
    """Orchestrates deterministic discrepancy evaluation over an instrument.
    
    Guarantees:
    1. Pure rule evaluation over loaded domain entities.
    2. Any exception inside a rule is caught, audited, and logged without breaking submission.
    3. Persists tickets idempotently via partial unique index.
    4. Automatically invokes FSM transition if blocking findings exist.
    5. Clean or advisory-only findings leave instrument in SUBMITTED.
    6. Creates high-priority vigilance alert for cross-institution duplicates (DISC-05).
    7. Writes audit entries for newly raised/resolved tickets and overall run summary.
    """
    if actor is None:
        actor = SYSTEM_ACTOR

    if isinstance(instrument_id, str):
        instrument_id = uuid.UUID(instrument_id)

    instrument = session.query(Instrument).filter(Instrument.id == instrument_id).first()
    if not instrument:
        raise ValueError(f"Instrument not found with ID: {instrument_id}")

    institution = session.query(Institution).filter(Institution.id == instrument.institution_id).first()

    today = clock_service.today()
    findings: List[Finding] = []
    not_evaluated: List[NotEvaluated] = []
    evaluated_rules: List[str] = []

    # Helper to execute rule safely
    def execute_rule(rule_code: str, func_call):
        try:
            res = func_call()
            if isinstance(res, Finding):
                findings.append(res)
                evaluated_rules.append(rule_code)
            elif isinstance(res, NotEvaluated):
                not_evaluated.append(res)
            else:
                evaluated_rules.append(rule_code)
        except Exception as exc:
            logger.error("Error executing discrepancy rule %s on instrument %s: %s", rule_code, instrument_id, exc)
            not_evaluated.append(NotEvaluated(rule_code, f"internal rule execution error: {exc.__class__.__name__}"))
            AuditService.append(
                session,
                entity_type="INSTRUMENT",
                entity_id=instrument.id,
                action="DISCREPANCY_RULE_ERROR",
                actor_user_id=actor.user_id,
                actor_role=actor.role,
                details={
                    "rule_code": rule_code,
                    "error_type": exc.__class__.__name__,
                    "message": "Internal error during rule evaluation",
                },
            )

    # 1. DISC-01: Mandatory Fields
    execute_rule("DISC-01", lambda: check_disc_01_mandatory_fields(instrument))

    # 2. DISC-02: Chronological Inversion
    execute_rule("DISC-02", lambda: check_disc_02_chronological_inversion(instrument, today=today))

    # 3. DISC-03: Underfunding
    execute_rule("DISC-03", lambda: check_disc_03_underfunding(instrument, institution=institution))

    # 4. DISC-04: Expired Instrument
    execute_rule("DISC-04", lambda: check_disc_04_expired_instrument(instrument, today=today))

    # 5. DISC-05: Cross-Institution Duplicate
    execute_rule("DISC-05", lambda: check_disc_05_cross_institution_duplicate(session, instrument, institution=institution))

    # 6. DISC-06: Maturity Math
    execute_rule("DISC-06", lambda: check_disc_06_maturity_math(instrument))

    # 7. DISC-07: Text Extraction Diff (Deferred to Tier 4)
    not_evaluated.append(NotEvaluated("DISC-07", "extraction not available"))

    # 8. DISC-08: Suspicious PDF Metadata
    execute_rule(
        "DISC-08",
        lambda: check_disc_08_suspicious_pdf_metadata(
            session,
            instrument,
            vault_base=vault_base,
            master_key_b64=master_key_b64,
        ),
    )

    # -------------------------------------------------------------------------
    # Ticket Persistence & Re-run Reconciliation
    # -------------------------------------------------------------------------
    existing_tickets = (
        session.query(DiscrepancyTicket)
        .filter(DiscrepancyTicket.instrument_id == instrument.id)
        .all()
    )
    open_tickets_by_code = {t.rule_code: t for t in existing_tickets if t.status == "OPEN"}
    finding_codes = {f.rule_code for f in findings}

    tickets_created: List[DiscrepancyTicket] = []
    tickets_resolved: List[DiscrepancyTicket] = []

    now_ts = clock_service.now()

    for finding in findings:
        if finding.rule_code in open_tickets_by_code:
            # Update existing open ticket
            ticket = open_tickets_by_code[finding.rule_code]
            ticket.severity = finding.severity
            ticket.blocking = finding.blocking
            ticket.message = finding.message
            ticket.staff_detail = finding.staff_detail
        else:
            # Create new open ticket
            ticket = DiscrepancyTicket(
                id=uuid.uuid4(),
                instrument_id=instrument.id,
                rule_code=finding.rule_code,
                severity=finding.severity,
                blocking=finding.blocking,
                status="OPEN",
                message=finding.message,
                staff_detail=finding.staff_detail,
                created_at=now_ts,
                resolved_at=None,
                resolved_by_user_id=None,
            )
            session.add(ticket)
            session.flush()
            tickets_created.append(ticket)

            # Audit ticket creation
            AuditService.append(
                session,
                entity_type="TICKET",
                entity_id=ticket.id,
                action="DISCREPANCY_RAISED",
                actor_user_id=actor.user_id,
                actor_role=actor.role,
                details={
                    "rule_code": ticket.rule_code,
                    "severity": ticket.severity,
                    "blocking": ticket.blocking,
                    "instrument_id": str(instrument.id),
                    "ticket_id": str(ticket.id),
                },
            )

    # Resolve any open tickets for rules that no longer fire
    for rule_code, open_ticket in open_tickets_by_code.items():
        if rule_code not in finding_codes:
            setattr(open_ticket, "status", "RESOLVED")
            open_ticket.resolved_at = now_ts
            open_ticket.resolved_by_user_id = actor.user_id
            tickets_resolved.append(open_ticket)

            AuditService.append(
                session,
                entity_type="TICKET",
                entity_id=open_ticket.id,
                action="DISCREPANCY_RESOLVED",
                actor_user_id=actor.user_id,
                actor_role=actor.role,
                details={
                    "rule_code": open_ticket.rule_code,
                    "instrument_id": str(instrument.id),
                    "ticket_id": str(open_ticket.id),
                },
            )

    # -------------------------------------------------------------------------
    # Vigilance Alert for DISC-05
    # -------------------------------------------------------------------------
    if "DISC-05" in finding_codes:
        existing_alert = (
            session.query(Alert)
            .filter(
                Alert.instrument_id == instrument.id,
                Alert.alert_type == "VIGILANCE_DUPLICATE_FDR",
            )
            .first()
        )
        if not existing_alert:
            alert = Alert(
                id=uuid.uuid4(),
                instrument_id=instrument.id,
                tier=2,
                alert_type="VIGILANCE_DUPLICATE_FDR",
                channel="PORTAL_NOTIFICATION",
                recipient_email="vigilance@aicte.gov.in",
                sent_at=now_ts,
                acknowledged_at=None,
                escalation_level_reached=1,
            )
            session.add(alert)
            session.flush()

    # -------------------------------------------------------------------------
    # FSM State Transition Orchestration
    # -------------------------------------------------------------------------
    blocking_findings = [f for f in findings if f.blocking]

    if blocking_findings:
        blocking_codes = sorted([f.rule_code for f in blocking_findings])
        justification_msg = f"Automated discrepancy checks: {', '.join(blocking_codes)}"

        if instrument.status == InstrumentState.SUBMITTED.value:
            transition(
                session,
                instrument.id,
                InstrumentState.UNDER_SCRUTINY.value,
                SYSTEM_ACTOR,
                justification=justification_msg,
            )
            transition(
                session,
                instrument.id,
                InstrumentState.DISCREPANCY_FLAGGED.value,
                SYSTEM_ACTOR,
                justification=f"Blocking discrepancies flagged: {', '.join(blocking_codes)}",
            )
        elif instrument.status == InstrumentState.UNDER_SCRUTINY.value:
            transition(
                session,
                instrument.id,
                InstrumentState.DISCREPANCY_FLAGGED.value,
                SYSTEM_ACTOR,
                justification=f"Blocking discrepancies flagged: {', '.join(blocking_codes)}",
            )
        # If already in DISCREPANCY_FLAGGED, status remains unchanged
    else:
        # Clean or advisory-only findings: instrument remains in SUBMITTED (or its existing state)
        pass

    # -------------------------------------------------------------------------
    # Run Summary Audit Entry
    # -------------------------------------------------------------------------
    AuditService.append(
        session,
        entity_type="INSTRUMENT",
        entity_id=instrument.id,
        action="DISCREPANCY_CHECK_RUN",
        actor_user_id=actor.user_id,
        actor_role=actor.role,
        details={
            "rules_evaluated": evaluated_rules,
            "rules_raised": [f.rule_code for f in findings],
            "not_evaluated": {ne.rule_code: ne.reason for ne in not_evaluated},
            "resulting_state": instrument.status,
        },
    )

    return CheckRunSummary(
        instrument_id=instrument.id,
        status=instrument.status,
        findings=findings,
        not_evaluated=not_evaluated,
        tickets_created=tickets_created,
        tickets_resolved=tickets_resolved,
    )
