"""Explainable Earned Trust Score Engine (SPEC §9.1).

Computes a deterministic, evidence-based trust score (0 to 100) where trust
is earned rather than assumed by default:
- Base score: 20
- Evidence additions: up to +80 (+15 PDF text layer, +20 text match [0 in MVP], +15 joint clause, +30 bank verification)
- Discrepancy deductions: up to -50 per rule for open tickets
- Result clamped between 0 and 100.
"""
from dataclasses import dataclass, field
import io
import logging
from typing import Optional, List, Dict, Any, Union
import uuid

from sqlalchemy.orm import Session
from pypdf import PdfReader

from app.models.instrument import Instrument
from app.models.document import Document
from app.models.provenance import FieldProvenance
from app.models.verification import VerificationRecord
from app.models.discrepancy import DiscrepancyTicket
from app.services.document_service import DocumentService
from app.services.clock_service import clock_service

logger = logging.getLogger(__name__)

# Deduction point weights per rule code (SPEC §9.1)
DEDUCTION_POINTS: Dict[str, int] = {
    "DISC-01": 30,
    "DISC-02": 40,
    "DISC-03": 20,
    "DISC-04": 30,
    "DISC-05": 50,
    "DISC-06": 15,
    "DISC-07": 25,
    "DISC-08": 15,
    "OFFICER-FLAG": 0,
}

RULE_DESCRIPTIONS: Dict[str, str] = {
    "DISC-01": "Missing mandatory business fields",
    "DISC-02": "Chronological date inversion",
    "DISC-03": "Deposit below required regulatory slab",
    "DISC-04": "Expired instrument submitted",
    "DISC-05": "Duplicate FDR number",
    "DISC-06": "Maturity math disparity",
    "DISC-07": "Text extraction mismatch",
    "DISC-08": "Non-bank authoring metadata",
    "OFFICER-FLAG": "Scrutiny officer manual flag (informational)",
}


@dataclass(frozen=True)
class DiscrepancyTicketSnapshot:
    rule_code: str
    status: str  # "OPEN", "RESOLVED", etc.
    ticket_id: Optional[str] = None
    description: Optional[str] = None


@dataclass(frozen=True)
class TrustSnapshot:
    has_pdf_text_layer: bool = False
    joint_account_holder: Optional[str] = None
    joint_account_provenance: Optional[str] = None
    latest_verification_is_verified: Optional[bool] = None
    tickets: List[DiscrepancyTicketSnapshot] = field(default_factory=list)


@dataclass
class TrustAddition:
    code: str
    points: int
    earned: bool
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "points": self.points,
            "earned": self.earned,
            "reason": self.reason,
        }


@dataclass
class TrustDeduction:
    rule_code: str
    points: int
    ticket_id: Optional[str]
    description: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_code": self.rule_code,
            "points": self.points,
            "ticket_id": self.ticket_id,
            "description": self.description,
        }


@dataclass
class TrustResult:
    total: int
    raw_total: int
    clamped: bool
    base: int
    additions: List[TrustAddition]
    deductions: List[TrustDeduction]
    version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "raw_total": self.raw_total,
            "clamped": self.clamped,
            "base": self.base,
            "additions": [a.to_dict() for a in self.additions],
            "deductions": [d.to_dict() for d in self.deductions],
            "version": self.version,
        }


def compute_trust(snapshot: TrustSnapshot) -> TrustResult:
    """Pure decision function computing trust score from an in-memory snapshot.

    Formula:
        total = clamp(20 + sum(additions) - sum(deductions), 0, 100)

    Inputs are plain data; executes zero database queries or I/O.
    """
    base = 20
    additions: List[TrustAddition] = []

    # 1. PDF_TEXT_LAYER (+15)
    pdf_earned = bool(snapshot.has_pdf_text_layer)
    additions.append(
        TrustAddition(
            code="PDF_TEXT_LAYER",
            points=15 if pdf_earned else 0,
            earned=pdf_earned,
            reason=(
                "Valid PDF scan with extractable digital text layer attached."
                if pdf_earned
                else "No readable PDF with extractable text layer found."
            ),
        )
    )

    # 2. TEXT_MATCHES_FORM (+20) - Not evaluated in MVP release
    additions.append(
        TrustAddition(
            code="TEXT_MATCHES_FORM",
            points=0,
            earned=False,
            reason="Not evaluated: automated text matching is not enabled",
        )
    )

    # 3. JOINT_CLAUSE_CONFIRMED (+15)
    # Tier-1 definition: joint_account_holder non-blank and provenance is MOCK_BANK_CONFIRMED or OFFICER_VERIFIED
    joint_val = (snapshot.joint_account_holder or "").strip()
    joint_prov = snapshot.joint_account_provenance or ""
    joint_earned = bool(joint_val and joint_prov in ("MOCK_BANK_CONFIRMED", "OFFICER_VERIFIED"))
    additions.append(
        TrustAddition(
            code="JOINT_CLAUSE_CONFIRMED",
            points=15 if joint_earned else 0,
            earned=joint_earned,
            reason=(
                "Joint AICTE holding / lien clause confirmed by bank or officer."
                if joint_earned
                else "Joint holding clause is unconfirmed or not declared."
            ),
        )
    )

    # 4. BANK_VERIFICATION (+30)
    bank_earned = (snapshot.latest_verification_is_verified is True)
    additions.append(
        TrustAddition(
            code="BANK_VERIFICATION",
            points=30 if bank_earned else 0,
            earned=bank_earned,
            reason=(
                "Bank confirmation verified via CBS gateway or manual letter."
                if bank_earned
                else "Bank confirmation pending or unverified."
            ),
        )
    )

    # Deductions: Apply once per rule_code, only for OPEN tickets
    deductions: List[TrustDeduction] = []
    seen_rules = set()

    for ticket in snapshot.tickets:
        if (ticket.status or "").upper() != "OPEN":
            continue
        code = ticket.rule_code
        if code in seen_rules:
            continue
        seen_rules.add(code)

        pts = DEDUCTION_POINTS.get(code, 0)
        desc = ticket.description or RULE_DESCRIPTIONS.get(code, f"Discrepancy rule {code}")
        if code == "DISC-05":
            desc = "Duplicate FDR number (under review)"

        deductions.append(
            TrustDeduction(
                rule_code=code,
                points=pts,
                ticket_id=ticket.ticket_id,
                description=desc,
            )
        )

    total_additions = sum(a.points for a in additions if a.earned)
    total_deductions = sum(d.points for d in deductions)
    raw_total = base + total_additions - total_deductions
    total = max(0, min(100, raw_total))
    clamped = (total != raw_total)

    return TrustResult(
        total=total,
        raw_total=raw_total,
        clamped=clamped,
        base=base,
        additions=additions,
        deductions=deductions,
        version=1,
    )


def recompute_trust(
    session: Session,
    instrument_id: Union[uuid.UUID, str],
    *,
    vault_base: Optional[str] = None,
    master_key_b64: Optional[str] = None,
) -> TrustResult:
    """Recomputes and writes the trust score and breakdown for an instrument in caller's transaction.

    Derived data: Recomputation is NOT individually audited.
    This is the SOLE service authorized to write instruments.trust_score,
    instruments.trust_breakdown, and instruments.trust_computed_at.
    """
    if isinstance(instrument_id, str):
        instrument_id = uuid.UUID(instrument_id)

    instrument = (
        session.query(Instrument)
        .filter(Instrument.id == instrument_id)
        .with_for_update()
        .first()
    )
    if not instrument:
        raise ValueError(f"Instrument {instrument_id} not found.")

    # 1. Check FDR_SCAN document for extractable PDF text layer
    has_pdf_text = False
    doc = (
        session.query(Document)
        .filter(
            Document.instrument_id == instrument.id,
            Document.document_type == "FDR_SCAN",
        )
        .first()
    )
    if doc:
        try:
            raw_bytes = DocumentService.decrypt_stored_document(
                doc=doc,
                vault_base=vault_base,
                master_key_b64=master_key_b64,
            )
            reader = PdfReader(io.BytesIO(raw_bytes))
            if len(reader.pages) > 0:
                has_pdf_text = any(
                    len((page.extract_text() or "").strip()) > 0
                    for page in reader.pages
                )
        except Exception as exc:
            logger.debug("PDF text layer check failed for doc %s: %s", doc.id, exc)
            has_pdf_text = False

    # 2. Check joint_account_holder provenance
    joint_prov_type = None
    prov_row = (
        session.query(FieldProvenance)
        .filter(
            FieldProvenance.instrument_id == instrument.id,
            FieldProvenance.field_name == "joint_account_holder",
        )
        .first()
    )
    if prov_row:
        joint_prov_type = prov_row.provenance_type

    # 3. Check latest verification record
    latest_ver = (
        session.query(VerificationRecord)
        .filter(VerificationRecord.instrument_id == instrument.id)
        .order_by(VerificationRecord.executed_at.desc(), VerificationRecord.id.desc())
        .first()
    )
    latest_is_verified = latest_ver.is_verified if latest_ver else None

    # 4. Check discrepancy tickets
    tickets = (
        session.query(DiscrepancyTicket)
        .filter(DiscrepancyTicket.instrument_id == instrument.id)
        .all()
    )
    ticket_snapshots = [
        DiscrepancyTicketSnapshot(
            rule_code=t.rule_code,
            status=t.status,
            ticket_id=str(t.id),
            description=t.message,
        )
        for t in tickets
    ]

    snapshot = TrustSnapshot(
        has_pdf_text_layer=has_pdf_text,
        joint_account_holder=instrument.joint_account_holder,
        joint_account_provenance=joint_prov_type,
        latest_verification_is_verified=latest_is_verified,
        tickets=ticket_snapshots,
    )

    result = compute_trust(snapshot)

    # Persist derived metrics in the caller's transaction
    now_ts = clock_service.now()
    breakdown_data = result.to_dict()
    breakdown_data["computed_at"] = now_ts.isoformat()

    instrument.trust_score = result.total
    instrument.trust_breakdown = breakdown_data
    instrument.trust_score_breakdown = breakdown_data
    instrument.trust_computed_at = now_ts

    return result
