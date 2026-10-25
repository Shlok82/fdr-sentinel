import io
import os
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional, Dict, Any, List, Union
import pypdf
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from app.models.institution import Institution
from app.models.instrument import Instrument
from app.models.document import Document
from app.models.alert import Alert
from app.models.audit import AuditLog
from app.models.discrepancy import DiscrepancyTicket
from app.services.audit_service import AuditService, verify_chain
from app.services.clock_service import clock_service
from app.services.document_service import DocumentService
from app.services.instrument_fsm import Actor, SYSTEM_ACTOR
from app.services.discrepancy_engine import (
    run_checks,
    check_disc_01_mandatory_fields,
    check_disc_02_chronological_inversion,
    check_disc_03_underfunding,
    check_disc_04_expired_instrument,
    check_disc_05_cross_institution_duplicate,
    check_disc_06_maturity_math,
    check_disc_08_suspicious_pdf_metadata,
    Finding,
    NotEvaluated,
)
from app.services.demo_scenarios import compute_quarterly_compound_maturity, get_scenario_data
from app.services.demo_documents import generate_demo_fdr_pdf
from app.services.submission_service import FDRSubmissionService
from app.schemas.instrument import FDRSubmissionForm


@pytest.fixture
def inst_engineering(app_session: Session) -> Institution:
    now_ts = clock_service.now()
    inst = Institution(
        id=uuid.uuid4(),
        aicte_permanent_id=f"1-TEST-{uuid.uuid4().hex[:6]}",
        name="Apex College of Engineering",
        region="Western",
        state="Maharashtra",
        address="Pune Campus",
        contact_email="apex@example.edu",
        contact_phone="020-12345678",
        institution_type="Engineering & Technology",
        created_at=now_ts,
        updated_at=now_ts,
    )
    app_session.add(inst)
    app_session.commit()
    app_session.refresh(inst)
    return inst


@pytest.fixture
def inst_management(app_session: Session) -> Institution:
    now_ts = clock_service.now()
    inst = Institution(
        id=uuid.uuid4(),
        aicte_permanent_id=f"1-MGMT-{uuid.uuid4().hex[:6]}",
        name="Apex School of Management",
        region="Western",
        state="Maharashtra",
        address="Mumbai Campus",
        contact_email="mgmt@example.edu",
        contact_phone="022-12345678",
        institution_type="Management",
        created_at=now_ts,
        updated_at=now_ts,
    )
    app_session.add(inst)
    app_session.commit()
    app_session.refresh(inst)
    return inst


@pytest.fixture
def inst_pharmacy(app_session: Session) -> Institution:
    now_ts = clock_service.now()
    inst = Institution(
        id=uuid.uuid4(),
        aicte_permanent_id=f"1-PHARM-{uuid.uuid4().hex[:6]}",
        name="Apex College of Pharmacy",
        region="Western",
        state="Maharashtra",
        address="Nagpur Campus",
        contact_email="pharm@example.edu",
        contact_phone="0712-12345678",
        institution_type="Pharmacy",
        created_at=now_ts,
        updated_at=now_ts,
    )
    app_session.add(inst)
    app_session.commit()
    app_session.refresh(inst)
    return inst


_UNSET = object()


def create_test_instrument(
    app_session: Session,
    institution_id: uuid.UUID,
    fdr_number: Optional[str] = "FDR-1001",
    principal: Optional[Decimal] = Decimal("3500000.00"),
    maturity: Optional[Decimal] = Decimal("4373418.06"),
    rate: Optional[Decimal] = Decimal("7.50"),
    tenure: Optional[int] = 36,
    issue_date: Any = _UNSET,
    maturity_date: Any = _UNSET,
    joint_holder: Optional[str] = "Regional Officer, AICTE & Chairman, College",
    bank_name: Optional[str] = "Demo National Bank",
    bank_branch: Optional[str] = "Model Town Branch",
    ifsc: Optional[str] = "DEMO0123456",
    status: str = "SUBMITTED",
) -> Instrument:
    now_ts = clock_service.now()
    today = clock_service.today()
    if issue_date is _UNSET:
        issue_date = today
    if maturity_date is _UNSET:
        maturity_date = today + timedelta(days=1095)

    inst = Instrument(
        id=uuid.uuid4(),
        institution_id=institution_id,
        fdr_number=fdr_number,
        bank_name=bank_name,
        bank_branch=bank_branch,
        ifsc_code=ifsc,
        principal_amount=principal,
        maturity_amount=maturity,
        interest_rate_percent=rate,
        issue_date=issue_date,
        maturity_date=maturity_date,
        tenure_months=tenure,
        joint_account_holder=joint_holder,
        status=status,
        created_at=now_ts,
        updated_at=now_ts,
    )
    app_session.add(inst)
    app_session.commit()
    app_session.refresh(inst)
    return inst


# -----------------------------------------------------------------------------
# 1. DISC-01: Mandatory Fields Tests
# -----------------------------------------------------------------------------

def test_disc_01_all_present(app_session: Session, inst_engineering: Institution):
    inst = create_test_instrument(app_session, inst_engineering.id)
    assert check_disc_01_mandatory_fields(inst) is None


def test_disc_01_missing_single_field(app_session: Session, inst_engineering: Institution):
    inst = create_test_instrument(app_session, inst_engineering.id, bank_name="")
    finding = check_disc_01_mandatory_fields(inst)
    assert isinstance(finding, Finding)
    assert finding.rule_code == "DISC-01"
    assert finding.severity == "CRITICAL"
    assert finding.blocking is True
    assert "bank_name" in finding.staff_detail["missing_fields"]
    assert "bank_name" in finding.message


def test_disc_01_all_null(app_session: Session, inst_engineering: Institution):
    inst = create_test_instrument(
        app_session,
        inst_engineering.id,
        fdr_number=None,
        principal=None,
        maturity=None,
        rate=None,
        tenure=None,
        issue_date=None,
        maturity_date=None,
        joint_holder=None,
        bank_name=None,
        bank_branch=None,
        ifsc=None,
    )
    finding = check_disc_01_mandatory_fields(inst)
    assert isinstance(finding, Finding)
    assert finding.rule_code == "DISC-01"
    assert finding.blocking is True
    assert len(finding.staff_detail["missing_fields"]) >= 6


# -----------------------------------------------------------------------------
# 2. DISC-02: Chronological Inversion Tests
# -----------------------------------------------------------------------------

def test_disc_02_valid_dates(app_session: Session, inst_engineering: Institution):
    today = clock_service.today()
    inst = create_test_instrument(
        app_session,
        inst_engineering.id,
        issue_date=today - timedelta(days=30),
        maturity_date=today + timedelta(days=365),
    )
    assert check_disc_02_chronological_inversion(inst) is None


def test_disc_02_maturity_equals_issue_date(app_session: Session, inst_engineering: Institution):
    today = clock_service.today()
    inst = create_test_instrument(
        app_session,
        inst_engineering.id,
        issue_date=today,
        maturity_date=today,
    )
    finding = check_disc_02_chronological_inversion(inst)
    assert isinstance(finding, Finding)
    assert finding.rule_code == "DISC-02"
    assert finding.severity == "CRITICAL"
    assert finding.blocking is True
    assert "maturity_before_or_on_issue" in finding.staff_detail["violation_types"]


def test_disc_02_maturity_before_issue_date(app_session: Session, inst_engineering: Institution):
    today = clock_service.today()
    inst = create_test_instrument(
        app_session,
        inst_engineering.id,
        issue_date=today,
        maturity_date=today - timedelta(days=1),
    )
    finding = check_disc_02_chronological_inversion(inst)
    assert isinstance(finding, Finding)
    assert finding.blocking is True


def test_disc_02_future_issue_date(app_session: Session, inst_engineering: Institution):
    today = clock_service.today()
    inst = create_test_instrument(
        app_session,
        inst_engineering.id,
        issue_date=today + timedelta(days=5),
        maturity_date=today + timedelta(days=365),
    )
    finding = check_disc_02_chronological_inversion(inst)
    assert isinstance(finding, Finding)
    assert finding.rule_code == "DISC-02"
    assert "future_issue_date" in finding.staff_detail["violation_types"]


def test_disc_02_time_machine_shift(app_session: Session, inst_engineering: Institution):
    """Advancing clock via ClockService affects DISC-02 evaluation."""
    initial_today = clock_service.today()
    # Issue date is 10 days ahead of initial_today
    issue = initial_today + timedelta(days=10)
    maturity = initial_today + timedelta(days=400)
    inst = create_test_instrument(app_session, inst_engineering.id, issue_date=issue, maturity_date=maturity)

    # Before advance: issue date is in future -> flags DISC-02
    res_before = check_disc_02_chronological_inversion(inst)
    assert isinstance(res_before, Finding)

    # Advance clock by 30 days
    clock_service.advance(app_session, 30)
    res_after = check_disc_02_chronological_inversion(inst)
    assert res_after is None


def test_disc_02_null_dates(app_session: Session, inst_engineering: Institution):
    inst = create_test_instrument(app_session, inst_engineering.id, issue_date=None, maturity_date=None)
    res = check_disc_02_chronological_inversion(inst)
    assert isinstance(res, NotEvaluated)
    assert res.rule_code == "DISC-02"


# -----------------------------------------------------------------------------
# 3. DISC-03: Underfunding Tests
# -----------------------------------------------------------------------------

def test_disc_03_engineering_exact_slab(app_session: Session, inst_engineering: Institution):
    inst = create_test_instrument(app_session, inst_engineering.id, principal=Decimal("3500000.00"))
    assert check_disc_03_underfunding(inst, institution=inst_engineering) is None


def test_disc_03_engineering_one_rupee_below(app_session: Session, inst_engineering: Institution):
    inst = create_test_instrument(app_session, inst_engineering.id, principal=Decimal("3499999.00"))
    finding = check_disc_03_underfunding(inst, institution=inst_engineering)
    assert isinstance(finding, Finding)
    assert finding.rule_code == "DISC-03"
    assert finding.severity == "HIGH"
    assert finding.blocking is True
    assert finding.staff_detail["deficit"] == "1.00"
    assert finding.staff_detail["assumption"] == "[ASSUMPTION TO VERIFY - APH Norm]"


def test_disc_03_management_slabs(app_session: Session, inst_management: Institution):
    inst_ok = create_test_instrument(app_session, inst_management.id, principal=Decimal("1500000.00"))
    assert check_disc_03_underfunding(inst_ok, institution=inst_management) is None

    inst_bad = create_test_instrument(app_session, inst_management.id, principal=Decimal("1499999.00"))
    finding = check_disc_03_underfunding(inst_bad, institution=inst_management)
    assert isinstance(finding, Finding)
    assert finding.rule_code == "DISC-03"
    assert finding.staff_detail["deficit"] == "1.00"


def test_disc_03_unconfigured_type(app_session: Session, inst_pharmacy: Institution):
    inst = create_test_instrument(app_session, inst_pharmacy.id, principal=Decimal("100000.00"))
    res = check_disc_03_underfunding(inst, institution=inst_pharmacy)
    assert isinstance(res, NotEvaluated)
    assert res.rule_code == "DISC-03"
    assert "no slab configured" in res.reason


def test_disc_03_null_principal(app_session: Session, inst_engineering: Institution):
    inst = create_test_instrument(app_session, inst_engineering.id, principal=None)
    res = check_disc_03_underfunding(inst, institution=inst_engineering)
    assert isinstance(res, NotEvaluated)


# -----------------------------------------------------------------------------
# 4. DISC-04: Expired Instrument Tests
# -----------------------------------------------------------------------------

def test_disc_04_future_maturity(app_session: Session, inst_engineering: Institution):
    today = clock_service.today()
    inst = create_test_instrument(app_session, inst_engineering.id, maturity_date=today + timedelta(days=10))
    assert check_disc_04_expired_instrument(inst) is None


def test_disc_04_past_maturity(app_session: Session, inst_engineering: Institution):
    today = clock_service.today()
    inst = create_test_instrument(app_session, inst_engineering.id, maturity_date=today - timedelta(days=1))
    finding = check_disc_04_expired_instrument(inst)
    assert isinstance(finding, Finding)
    assert finding.rule_code == "DISC-04"
    assert finding.severity == "CRITICAL"
    assert finding.blocking is True


def test_disc_04_null_maturity(app_session: Session, inst_engineering: Institution):
    inst = create_test_instrument(app_session, inst_engineering.id, maturity_date=None)
    res = check_disc_04_expired_instrument(inst)
    assert isinstance(res, NotEvaluated)


# -----------------------------------------------------------------------------
# 5. DISC-05: Cross-Institution Duplicate & Privacy Tests
# -----------------------------------------------------------------------------

def test_disc_05_newer_duplicate_flagged_and_incumbent_untouched(
    app_session: Session,
    inst_engineering: Institution,
    inst_management: Institution,
):
    now_ts = clock_service.now()
    # 1. Incumbent instrument submitted 2 days ago by inst_engineering
    incumbent = Instrument(
        id=uuid.uuid4(),
        institution_id=inst_engineering.id,
        fdr_number="FDR-SHARED-999",
        bank_name="State Bank",
        principal_amount=Decimal("3500000.00"),
        status="ACTIVE",
        created_at=now_ts - timedelta(days=2),
        updated_at=now_ts - timedelta(days=2),
    )
    app_session.add(incumbent)
    app_session.commit()
    app_session.refresh(incumbent)

    incumbent_updated_at_before = incumbent.updated_at
    incumbent_status_before = incumbent.status

    # 2. Newer submission with same number from inst_management
    newer = Instrument(
        id=uuid.uuid4(),
        institution_id=inst_management.id,
        fdr_number="  fdr-shared-999  ",  # tests case and trimming normalization
        bank_name="State Bank",
        principal_amount=Decimal("1500000.00"),
        status="SUBMITTED",
        created_at=now_ts,
        updated_at=now_ts,
    )
    app_session.add(newer)
    app_session.commit()
    app_session.refresh(newer)

    # 3. Evaluate newer instrument
    finding = check_disc_05_cross_institution_duplicate(app_session, newer, institution=inst_management)
    assert isinstance(finding, Finding)
    assert finding.rule_code == "DISC-05"
    assert finding.severity == "CRITICAL"
    assert finding.blocking is True

    # Privacy check: message is generic and contains neither institution's name nor incumbent ID
    assert inst_engineering.name not in finding.message
    assert inst_engineering.aicte_permanent_id not in finding.message
    assert str(incumbent.id) not in finding.message
    assert "conflicts with an existing record" in finding.message

    # Diagnostic staff_detail DOES contain the incumbent diagnostic data
    assert finding.staff_detail["conflicting_instrument_id"] == str(incumbent.id)
    assert finding.staff_detail["conflicting_institution_name"] == inst_engineering.name
    assert finding.staff_detail["conflicting_aicte_permanent_id"] == inst_engineering.aicte_permanent_id

    # 4. Verify incumbent was completely untouched
    app_session.refresh(incumbent)
    assert incumbent.status == incumbent_status_before
    assert incumbent.updated_at == incumbent_updated_at_before

    # 5. Reverse check: evaluating the INCUMBENT must NOT flag it
    incumbent_finding = check_disc_05_cross_institution_duplicate(app_session, incumbent, institution=inst_engineering)
    assert incumbent_finding is None


def test_disc_05_same_institution_repeat_not_flagged(app_session: Session, inst_engineering: Institution):
    """Same institution re-using FDR number (e.g. renewal rollover) is not flagged by DISC-05."""
    now_ts = clock_service.now()
    older = create_test_instrument(app_session, inst_engineering.id, fdr_number="FDR-RENEW-01")
    newer = create_test_instrument(app_session, inst_engineering.id, fdr_number="FDR-RENEW-01")

    finding = check_disc_05_cross_institution_duplicate(app_session, newer, institution=inst_engineering)
    assert finding is None


# -----------------------------------------------------------------------------
# 6. DISC-06: Maturity Math Tests
# -----------------------------------------------------------------------------

def test_disc_06_exact_and_within_tolerance(app_session: Session, inst_engineering: Institution):
    P = Decimal("3500000.00")
    r = Decimal("7.50")
    tenure = 36
    expected_A = compute_quarterly_compound_maturity(P, r, tenure)

    # Exact expected amount
    inst = create_test_instrument(app_session, inst_engineering.id, principal=P, maturity=expected_A, rate=r, tenure=tenure)
    assert check_disc_06_maturity_math(inst) is None

    # Within tolerance: 2.5% of ~43.7L is ~1.09L. Delta of 1000 is well within tolerance
    inst_close = create_test_instrument(app_session, inst_engineering.id, principal=P, maturity=expected_A + Decimal("1000.00"), rate=r, tenure=tenure)
    assert check_disc_06_maturity_math(inst_close) is None


def test_disc_06_exceeding_tolerance(app_session: Session, inst_engineering: Institution):
    P = Decimal("3500000.00")
    r = Decimal("7.50")
    tenure = 36
    # Deliberate large delta: 36,00,000 declared vs ~43,73,418 expected
    inst_bad = create_test_instrument(app_session, inst_engineering.id, principal=P, maturity=Decimal("3600000.00"), rate=r, tenure=tenure)
    finding = check_disc_06_maturity_math(inst_bad)
    assert isinstance(finding, Finding)
    assert finding.rule_code == "DISC-06"
    assert finding.severity == "MEDIUM"
    assert finding.blocking is False  # Advisory only!
    assert "expected_maturity_amount" in finding.staff_detail
    assert "tolerance" in finding.staff_detail


def test_disc_06_null_math_inputs(app_session: Session, inst_engineering: Institution):
    inst = create_test_instrument(app_session, inst_engineering.id, rate=None)
    res = check_disc_06_maturity_math(inst)
    assert isinstance(res, NotEvaluated)
    assert res.rule_code == "DISC-06"


# -----------------------------------------------------------------------------
# 7. DISC-08: PDF Metadata Tests
# -----------------------------------------------------------------------------

def test_disc_08_clean_fpdf2_generated_pdf(app_session: Session, inst_engineering: Institution):
    """PDF generated by demo tools (fpdf2) must NOT match suspicious non-bank authoring list."""
    inst = create_test_instrument(app_session, inst_engineering.id)
    scenario_data = get_scenario_data("clean", db=app_session, institution_name=inst_engineering.name)
    pdf_bytes = generate_demo_fdr_pdf(scenario_data)

    doc_info = DocumentService.process_and_encrypt_upload(pdf_bytes, "clean.pdf")
    doc = Document(
        id=doc_info["id"],
        instrument_id=inst.id,
        document_type="FDR_SCAN",
        storage_path=doc_info["storage_path"],
        file_name="clean.pdf",
        file_mime_type="application/pdf",
        file_size_bytes=len(pdf_bytes),
        sha256_hash=doc_info["sha256_hash"],
        encrypted_dek=doc_info["encrypted_dek"],
        encryption_iv=doc_info["encryption_iv"],
        encryption_tag=doc_info["encryption_tag"],
        created_at=clock_service.now(),
    )
    app_session.add(doc)
    app_session.commit()

    finding = check_disc_08_suspicious_pdf_metadata(app_session, inst)
    assert finding is None


def test_disc_08_suspicious_producer_flagged(app_session: Session, inst_engineering: Institution):
    """PDF with Producer='Microsoft Word' is flagged as DISC-08."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_metadata({"/Producer": "Microsoft Word 2019", "/Creator": "Word"})
    buf = io.BytesIO()
    writer.write(buf)
    pdf_bytes = buf.getvalue()

    inst = create_test_instrument(app_session, inst_engineering.id)
    doc_info = DocumentService.process_and_encrypt_upload(pdf_bytes, "suspicious.pdf")
    doc = Document(
        id=doc_info["id"],
        instrument_id=inst.id,
        document_type="FDR_SCAN",
        storage_path=doc_info["storage_path"],
        file_name="suspicious.pdf",
        file_mime_type="application/pdf",
        file_size_bytes=len(pdf_bytes),
        sha256_hash=doc_info["sha256_hash"],
        encrypted_dek=doc_info["encrypted_dek"],
        encryption_iv=doc_info["encryption_iv"],
        encryption_tag=doc_info["encryption_tag"],
        created_at=clock_service.now(),
    )
    app_session.add(doc)
    app_session.commit()

    finding = check_disc_08_suspicious_pdf_metadata(app_session, inst)
    assert isinstance(finding, Finding)
    assert finding.rule_code == "DISC-08"
    assert finding.severity == "LOW"
    assert finding.blocking is False
    assert "Microsoft Word" in finding.staff_detail["producer"]


def test_disc_08_non_pdf_not_evaluated(app_session: Session, inst_engineering: Institution):
    """Image uploads (JPEG/PNG) return NOT_EVALUATED."""
    inst = create_test_instrument(app_session, inst_engineering.id)
    # Valid 1x1 PNG image with standard magic bytes
    valid_png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00"
        b"\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    doc_info = DocumentService.process_and_encrypt_upload(valid_png_bytes, "scan.png")
    doc = Document(
        id=doc_info["id"],
        instrument_id=inst.id,
        document_type="FDR_SCAN",
        storage_path=doc_info["storage_path"],
        file_name="scan.png",
        file_mime_type="image/png",
        file_size_bytes=len(valid_png_bytes),
        sha256_hash=doc_info["sha256_hash"],
        encrypted_dek=doc_info["encrypted_dek"],
        encryption_iv=doc_info["encryption_iv"],
        encryption_tag=doc_info["encryption_tag"],
        created_at=clock_service.now(),
    )
    app_session.add(doc)
    app_session.commit()

    res = check_disc_08_suspicious_pdf_metadata(app_session, inst)
    assert isinstance(res, NotEvaluated)
    assert res.rule_code == "DISC-08"


# -----------------------------------------------------------------------------
# 8. Orchestrator: run_checks() Lifecycle Transitions & Idempotency
# -----------------------------------------------------------------------------

def test_orchestration_blocking_transitions_to_flagged(app_session: Session, inst_engineering: Institution):
    """Blocking findings transition instrument from SUBMITTED -> UNDER_SCRUTINY -> DISCREPANCY_FLAGGED."""
    # Underfunded instrument (principal 50k < 35L)
    inst = create_test_instrument(app_session, inst_engineering.id, principal=Decimal("50000.00"))
    assert inst.status == "SUBMITTED"

    summary = run_checks(app_session, inst.id, actor=SYSTEM_ACTOR)
    app_session.commit()
    app_session.refresh(inst)

    assert inst.status == "DISCREPANCY_FLAGGED"
    assert any(f.rule_code == "DISC-03" for f in summary.findings)
    assert len(summary.tickets_created) >= 1

    # Verify audit entries written: STATE_TRANSITION (two steps), DISCREPANCY_RAISED, DISCREPANCY_CHECK_RUN
    audits = (
        app_session.query(AuditLog)
        .filter(AuditLog.entity_id.in_([inst.id] + [t.id for t in summary.tickets_created]))
        .all()
    )
    actions = [a.action for a in audits]
    assert "STATE_TRANSITION" in actions
    assert "DISCREPANCY_RAISED" in actions
    assert "DISCREPANCY_CHECK_RUN" in actions

    # Verify hash chain integrity
    assert verify_chain(app_session).ok is True


def test_orchestration_clean_stays_submitted(app_session: Session, inst_engineering: Institution):
    """A clean submission produces no blocking findings and STAYS in SUBMITTED."""
    P = Decimal("3500000.00")
    r = Decimal("7.50")
    A = compute_quarterly_compound_maturity(P, r, 36)
    inst = create_test_instrument(app_session, inst_engineering.id, principal=P, maturity=A, rate=r, tenure=36)

    # Attach clean demo PDF scan
    scenario_data = get_scenario_data("clean", db=app_session, institution_name=inst_engineering.name)
    pdf_bytes = generate_demo_fdr_pdf(scenario_data)
    doc_info = DocumentService.process_and_encrypt_upload(pdf_bytes, "clean.pdf")
    doc = Document(
        id=doc_info["id"],
        instrument_id=inst.id,
        document_type="FDR_SCAN",
        storage_path=doc_info["storage_path"],
        file_name="clean.pdf",
        file_mime_type="application/pdf",
        file_size_bytes=len(pdf_bytes),
        sha256_hash=doc_info["sha256_hash"],
        encrypted_dek=doc_info["encrypted_dek"],
        encryption_iv=doc_info["encryption_iv"],
        encryption_tag=doc_info["encryption_tag"],
        created_at=clock_service.now(),
    )
    app_session.add(doc)
    app_session.commit()

    summary = run_checks(app_session, inst.id, actor=SYSTEM_ACTOR)
    app_session.commit()
    app_session.refresh(inst)

    assert inst.status == "SUBMITTED"
    assert len(summary.findings) == 0
    assert len(summary.tickets_created) == 0


def test_orchestration_advisory_only_stays_submitted(app_session: Session, inst_engineering: Institution):
    """Advisory-only finding (e.g. DISC-06 maturity mismatch) saves ticket but leaves status as SUBMITTED."""
    P = Decimal("3500000.00")
    r = Decimal("7.50")
    # Deliberate maturity mismatch
    inst = create_test_instrument(app_session, inst_engineering.id, principal=P, maturity=Decimal("3600000.00"), rate=r, tenure=36)

    summary = run_checks(app_session, inst.id, actor=SYSTEM_ACTOR)
    app_session.commit()
    app_session.refresh(inst)

    assert inst.status == "SUBMITTED"
    assert len(summary.findings) == 1
    assert summary.findings[0].rule_code == "DISC-06"
    assert summary.findings[0].blocking is False
    assert len(summary.tickets_created) == 1
    assert summary.tickets_created[0].status == "OPEN"


def test_re_run_idempotency_and_resolution(app_session: Session, inst_engineering: Institution):
    """Re-running checks updates open tickets without duplicates, and marks resolved tickets."""
    # 1. Underfunded instrument with mathematically matching maturity -> creates DISC-03 only
    P_low = Decimal("50000.00")
    r = Decimal("7.50")
    A_low = compute_quarterly_compound_maturity(P_low, r, 36)
    inst = create_test_instrument(app_session, inst_engineering.id, principal=P_low, maturity=A_low, rate=r, tenure=36)
    summary1 = run_checks(app_session, inst.id, actor=SYSTEM_ACTOR)
    app_session.commit()

    tickets_count1 = (
        app_session.query(DiscrepancyTicket)
        .filter(DiscrepancyTicket.instrument_id == inst.id, DiscrepancyTicket.status == "OPEN")
        .count()
    )
    assert tickets_count1 == 1

    # 2. Second run with same state -> updates existing ticket, NO duplicates
    summary2 = run_checks(app_session, inst.id, actor=SYSTEM_ACTOR)
    app_session.commit()

    tickets_count2 = (
        app_session.query(DiscrepancyTicket)
        .filter(DiscrepancyTicket.instrument_id == inst.id, DiscrepancyTicket.status == "OPEN")
        .count()
    )
    assert tickets_count2 == 1
    assert len(summary2.tickets_created) == 0

    # 3. Rectify principal in instrument row to meet slab (35L) and matching maturity
    inst.principal_amount = Decimal("3500000.00")
    inst.maturity_amount = compute_quarterly_compound_maturity(inst.principal_amount, r, 36)
    app_session.commit()

    # Re-run -> DISC-03 no longer fires, so existing open ticket becomes RESOLVED
    summary3 = run_checks(app_session, inst.id, actor=SYSTEM_ACTOR)
    app_session.commit()

    resolved_ticket = (
        app_session.query(DiscrepancyTicket)
        .filter(DiscrepancyTicket.instrument_id == inst.id, DiscrepancyTicket.rule_code == "DISC-03")
        .first()
    )
    assert resolved_ticket.status == "RESOLVED"
    assert resolved_ticket.resolved_at is not None
    assert len(summary3.tickets_resolved) == 1


def test_disc_05_creates_vigilance_alert(
    app_session: Session,
    inst_engineering: Institution,
    inst_management: Institution,
):
    """DISC-05 creates a high-priority vigilance alert row in the alerts table."""
    now_ts = clock_service.now()
    # Incumbent instrument
    incumbent = Instrument(
        id=uuid.uuid4(),
        institution_id=inst_engineering.id,
        fdr_number="FDR-VIGILANCE-1",
        bank_name="Bank of Baroda",
        status="ACTIVE",
        created_at=now_ts - timedelta(days=5),
        updated_at=now_ts - timedelta(days=5),
    )
    app_session.add(incumbent)
    app_session.commit()

    # Duplicate submission from inst_management
    dup_inst = create_test_instrument(app_session, inst_management.id, fdr_number="FDR-VIGILANCE-1")
    run_checks(app_session, dup_inst.id, actor=SYSTEM_ACTOR)
    app_session.commit()

    alert = (
        app_session.query(Alert)
        .filter(
            Alert.instrument_id == dup_inst.id,
            Alert.alert_type == "VIGILANCE_DUPLICATE_FDR",
        )
        .first()
    )
    assert alert is not None
    assert alert.tier == 2
    assert alert.recipient_email == "vigilance@aicte.gov.in"


# -----------------------------------------------------------------------------
# 9. UI Rendering & XSS Tests
# -----------------------------------------------------------------------------

def test_ui_ticket_rendering_and_xss_protection(
    app_session: Session,
    inst_engineering: Institution,
    make_user,
    auth_client_for,
):
    """Verifies discrepancy card rendering on institution and staff views, and XSS sanitization."""
    inst_user = make_user("xss.inst.user", "INSTITUTION_ADMIN", institution_id=inst_engineering.id)
    staff_user = make_user("xss.staff.user", "SCRUTINY_OFFICER")

    inst = create_test_instrument(app_session, inst_engineering.id)
    now_ts = clock_service.now()

    # Create ticket with malicious HTML/XSS payload
    xss_ticket = DiscrepancyTicket(
        id=uuid.uuid4(),
        instrument_id=inst.id,
        rule_code="DISC-01",
        severity="CRITICAL",
        blocking=True,
        status="OPEN",
        message="<script>alert('XSS_ATTACK_MSG')</script> Missing field",
        staff_detail={"payload": "<script>alert('XSS_STAFF_DETAIL')</script>"},
        created_at=now_ts,
    )
    app_session.add(xss_ticket)
    app_session.commit()

    # 1. Institution detail view
    client_inst, _ = auth_client_for(inst_user)
    res_inst = client_inst.get(f"/institution/instruments/{inst.id}")
    assert res_inst.status_code == 200
    assert "Discrepancy Records" in res_inst.text
    assert "CRITICAL" in res_inst.text
    # Script tag from ticket message must be escaped, not rendered as raw executable HTML
    assert "<script>alert" not in res_inst.text
    assert "&lt;script&gt;alert" in res_inst.text
    assert "XSS_ATTACK_MSG" in res_inst.text
    # Institution view must NOT show staff diagnostic payload
    assert "XSS_STAFF_DETAIL" not in res_inst.text

    # 2. Staff detail view
    client_staff, _ = auth_client_for(staff_user)
    res_staff = client_staff.get(f"/instruments/{inst.id}")
    assert res_staff.status_code == 200
    assert "Discrepancy Tickets" in res_staff.text
    assert "<script>alert" not in res_staff.text
    assert "&lt;script&gt;alert" in res_staff.text
    assert "XSS_STAFF_DETAIL" in res_staff.text


# -----------------------------------------------------------------------------
# 10. Rule Failure Handling: Exceptions Logged & Audited Without Crashing
# -----------------------------------------------------------------------------

def test_rule_exception_handling_audited_and_not_evaluated(
    app_session: Session,
    inst_engineering: Institution,
    monkeypatch,
):
    """An unhandled error inside a rule is caught, audited as DISCREPANCY_RULE_ERROR, and treated as NOT_EVALUATED."""
    inst = create_test_instrument(app_session, inst_engineering.id)

    # Monkeypatch check_disc_01 to raise an unexpected runtime error
    def crashing_rule(instrument):
        raise RuntimeError("Simulated database driver disconnect during rule check")

    monkeypatch.setattr("app.services.discrepancy_engine.check_disc_01_mandatory_fields", crashing_rule)

    summary = run_checks(app_session, inst.id, actor=SYSTEM_ACTOR)
    app_session.commit()

    # The check run completes without crashing
    assert any(ne.rule_code == "DISC-01" for ne in summary.not_evaluated)
    assert any("RuntimeError" in ne.reason for ne in summary.not_evaluated)

    # DISCREPANCY_RULE_ERROR audit entry is recorded
    rule_error_audit = (
        app_session.query(AuditLog)
        .filter(AuditLog.entity_id == inst.id, AuditLog.action == "DISCREPANCY_RULE_ERROR")
        .first()
    )
    assert rule_error_audit is not None
    assert rule_error_audit.canonical_payload["details"]["rule_code"] == "DISC-01"


# -----------------------------------------------------------------------------
# 11. End-to-End Demo Scenarios Submission & FSM Validation
# -----------------------------------------------------------------------------

def test_demo_scenarios_end_to_end(
    app_session: Session,
    inst_engineering: Institution,
    inst_management: Institution,
    make_user,
):
    """Verifies that each demo scenario produces its expected discrepancy behavior through real submission."""
    user = make_user("sub.inst.admin", "INSTITUTION_ADMIN", institution_id=inst_engineering.id)

    # 1. Clean Scenario -> Stays SUBMITTED, 0 tickets
    clean_data = get_scenario_data("clean", db=app_session, institution_name=inst_engineering.name)
    clean_pdf = generate_demo_fdr_pdf(clean_data)
    form_clean = FDRSubmissionForm(
        fdr_number=clean_data["fdr_number"],
        bank_name=clean_data["bank_name"],
        bank_branch=clean_data["bank_branch"],
        ifsc_code=clean_data["ifsc_code"],
        principal_amount=clean_data["principal_amount"],
        maturity_amount=clean_data["maturity_amount"],
        interest_rate_percent=clean_data["interest_rate_percent"],
        tenure_months=clean_data["tenure_months"],
        issue_date=clean_data["issue_date"],
        maturity_date=clean_data["maturity_date"],
        joint_account_holder=clean_data["joint_account_holder"],
    )
    inst_clean = FDRSubmissionService.submit_instrument(
        db=app_session,
        form=form_clean,
        file_bytes=clean_pdf,
        raw_filename="clean_fdr.pdf",
        user=user,
    )
    assert inst_clean.status == "SUBMITTED"
    tickets_clean = app_session.query(DiscrepancyTicket).filter(DiscrepancyTicket.instrument_id == inst_clean.id).all()
    assert len(tickets_clean) == 0

    # 2. Inverted Dates Scenario -> DISC-02 + DISC-04, DISCREPANCY_FLAGGED
    invert_data = get_scenario_data("inverted_dates", db=app_session, institution_name=inst_engineering.name)
    invert_pdf = generate_demo_fdr_pdf(invert_data)
    form_invert = FDRSubmissionForm(
        fdr_number=invert_data["fdr_number"],
        bank_name=invert_data["bank_name"],
        bank_branch=invert_data["bank_branch"],
        ifsc_code=invert_data["ifsc_code"],
        principal_amount=invert_data["principal_amount"],
        maturity_amount=invert_data["maturity_amount"],
        interest_rate_percent=invert_data["interest_rate_percent"],
        tenure_months=invert_data["tenure_months"],
        issue_date=invert_data["issue_date"],
        maturity_date=invert_data["maturity_date"],
        joint_account_holder=invert_data["joint_account_holder"],
    )
    inst_invert = FDRSubmissionService.submit_instrument(
        db=app_session,
        form=form_invert,
        file_bytes=invert_pdf,
        raw_filename="invert_fdr.pdf",
        user=user,
    )
    assert inst_invert.status == "DISCREPANCY_FLAGGED"
    tickets_invert = app_session.query(DiscrepancyTicket).filter(DiscrepancyTicket.instrument_id == inst_invert.id).all()
    rules_invert = {t.rule_code for t in tickets_invert}
    assert "DISC-02" in rules_invert
    assert "DISC-04" in rules_invert

    # 3. Underfunded Scenario -> DISC-03, DISCREPANCY_FLAGGED
    low_data = get_scenario_data("low_principal", db=app_session, institution_name=inst_engineering.name)
    low_pdf = generate_demo_fdr_pdf(low_data)
    form_low = FDRSubmissionForm(
        fdr_number=low_data["fdr_number"],
        bank_name=low_data["bank_name"],
        bank_branch=low_data["bank_branch"],
        ifsc_code=low_data["ifsc_code"],
        principal_amount=low_data["principal_amount"],
        maturity_amount=low_data["maturity_amount"],
        interest_rate_percent=low_data["interest_rate_percent"],
        tenure_months=low_data["tenure_months"],
        issue_date=low_data["issue_date"],
        maturity_date=low_data["maturity_date"],
        joint_account_holder=low_data["joint_account_holder"],
    )
    inst_low = FDRSubmissionService.submit_instrument(
        db=app_session,
        form=form_low,
        file_bytes=low_pdf,
        raw_filename="low_fdr.pdf",
        user=user,
    )
    assert inst_low.status == "DISCREPANCY_FLAGGED"
    tickets_low = app_session.query(DiscrepancyTicket).filter(DiscrepancyTicket.instrument_id == inst_low.id).all()
    assert any(t.rule_code == "DISC-03" for t in tickets_low)

    # 4. Duplicate Scenario -> DISC-05
    # First, user from institution 2 submits with duplicate FDR number of inst_clean
    user2 = make_user("sub.inst.admin2", "INSTITUTION_ADMIN", institution_id=inst_management.id)
    dup_data = get_scenario_data(
        "duplicate_number",
        db=app_session,
        institution_name=inst_management.name,
        caller_institution_id=inst_management.id,
    )
    dup_pdf = generate_demo_fdr_pdf(dup_data)
    form_dup = FDRSubmissionForm(
        fdr_number=dup_data["fdr_number"],
        bank_name=dup_data["bank_name"],
        bank_branch=dup_data["bank_branch"],
        ifsc_code=dup_data["ifsc_code"],
        principal_amount=dup_data["principal_amount"],
        maturity_amount=dup_data["maturity_amount"],
        interest_rate_percent=dup_data["interest_rate_percent"],
        tenure_months=dup_data["tenure_months"],
        issue_date=dup_data["issue_date"],
        maturity_date=dup_data["maturity_date"],
        joint_account_holder=dup_data["joint_account_holder"],
    )
    inst_dup = FDRSubmissionService.submit_instrument(
        db=app_session,
        form=form_dup,
        file_bytes=dup_pdf,
        raw_filename="dup_fdr.pdf",
        user=user2,
    )
    assert inst_dup.status == "DISCREPANCY_FLAGGED"
    tickets_dup = app_session.query(DiscrepancyTicket).filter(DiscrepancyTicket.instrument_id == inst_dup.id).all()
    assert any(t.rule_code == "DISC-05" for t in tickets_dup)

    # Confirm the incumbent (inst_clean) remained untouched in SUBMITTED
    app_session.refresh(inst_clean)
    assert inst_clean.status == "SUBMITTED"


# -----------------------------------------------------------------------------
# 12. Institution List Page "Needs Attention" Marker Test
# -----------------------------------------------------------------------------

def test_institution_list_needs_attention_marker(
    app_session: Session,
    inst_engineering: Institution,
    make_user,
    auth_client_for,
):
    """Instruments in DISCREPANCY_FLAGGED display a 'Needs attention' marker on list views."""
    inst_user = make_user("list.user", "INSTITUTION_ADMIN", institution_id=inst_engineering.id)

    # Create flagged instrument
    create_test_instrument(app_session, inst_engineering.id, fdr_number="FLAGGED-01", status="DISCREPANCY_FLAGGED")
    # Create active instrument
    create_test_instrument(app_session, inst_engineering.id, fdr_number="ACTIVE-01", status="ACTIVE")

    client, _ = auth_client_for(inst_user)

    # 1. /institution/instruments
    res = client.get("/institution/instruments")
    assert res.status_code == 200
    assert "Needs attention" in res.text

    # 2. /institution dashboard
    res_dash = client.get("/institution")
    assert res_dash.status_code == 200
    assert "Needs attention" in res_dash.text

