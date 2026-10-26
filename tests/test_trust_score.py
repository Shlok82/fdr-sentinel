"""Tests for Earned Trust Score Engine (SPEC §9.1 and Stage 9 Part A)."""
import io
import uuid
from decimal import Decimal
import pytest
from pypdf import PdfWriter
from sqlalchemy.orm import Session

from app.models.institution import Institution
from app.models.instrument import Instrument
from app.models.document import Document
from app.models.provenance import FieldProvenance
from app.models.verification import VerificationRecord
from app.models.discrepancy import DiscrepancyTicket
from app.models.user import User
from app.services.trust_score import (
    compute_trust,
    recompute_trust,
    TrustSnapshot,
    DiscrepancyTicketSnapshot,
    TrustResult,
)
from app.services.scrutiny_service import ScrutinyService
from app.services.verification.mock_bank import MockFailureMode
from app.services.clock_service import clock_service


# -----------------------------------------------------------------------------
# 1. Pure Function Matrix Tests
# -----------------------------------------------------------------------------

def test_trust_score_base_only():
    """Empty snapshot yields base score of 20 with no clamping."""
    res = compute_trust(TrustSnapshot())
    assert res.base == 20
    assert res.total == 20
    assert res.raw_total == 20
    assert res.clamped is False
    assert len(res.additions) == 4
    assert len(res.deductions) == 0


def test_trust_score_additions_alone_and_combined():
    """Verifies each evidence addition alone and combined up to the 80 ceiling."""
    # PDF_TEXT_LAYER alone: +15
    res = compute_trust(TrustSnapshot(has_pdf_text_layer=True))
    assert res.total == 35
    assert res.additions[0].earned is True
    assert res.additions[0].points == 15

    # TEXT_MATCHES_FORM always 0 in MVP
    assert res.additions[1].earned is False
    assert res.additions[1].points == 0

    # JOINT_CLAUSE_CONFIRMED with SELF_DECLARED -> Not earned
    res = compute_trust(TrustSnapshot(joint_account_holder="AICTE & College", joint_account_provenance="SELF_DECLARED"))
    assert res.total == 20
    assert res.additions[2].earned is False
    assert res.additions[2].points == 0

    # JOINT_CLAUSE_CONFIRMED with MOCK_BANK_CONFIRMED -> Earned +15
    res = compute_trust(TrustSnapshot(joint_account_holder="AICTE & College", joint_account_provenance="MOCK_BANK_CONFIRMED"))
    assert res.total == 35
    assert res.additions[2].earned is True
    assert res.additions[2].points == 15

    # JOINT_CLAUSE_CONFIRMED with OFFICER_VERIFIED -> Earned +15
    res = compute_trust(TrustSnapshot(joint_account_holder="AICTE & College", joint_account_provenance="OFFICER_VERIFIED"))
    assert res.total == 35
    assert res.additions[2].earned is True

    # BANK_VERIFICATION alone -> +30
    res = compute_trust(TrustSnapshot(latest_verification_is_verified=True))
    assert res.total == 50
    assert res.additions[3].earned is True
    assert res.additions[3].points == 30

    # Clean verified instrument: 20 + 15 (PDF) + 15 (joint) + 30 (bank) = 80
    clean_res = compute_trust(
        TrustSnapshot(
            has_pdf_text_layer=True,
            joint_account_holder="AICTE & College",
            joint_account_provenance="MOCK_BANK_CONFIRMED",
            latest_verification_is_verified=True,
        )
    )
    assert clean_res.total == 80
    assert clean_res.raw_total == 80
    assert clean_res.clamped is False


def test_trust_score_deductions_alone_and_clamping():
    """Verifies each discrepancy deduction alone and floor clamping at 0."""
    expected_pts = {
        "DISC-01": 30,
        "DISC-02": 40,
        "DISC-03": 20,
        "DISC-04": 30,
        "DISC-05": 50,
        "DISC-06": 15,
        "DISC-07": 25,
        "DISC-08": 15,
    }
    for code, pts in expected_pts.items():
        res = compute_trust(
            TrustSnapshot(
                tickets=[DiscrepancyTicketSnapshot(rule_code=code, status="OPEN")]
            )
        )
        expected_raw = 20 - pts
        expected_total = max(0, expected_raw)
        assert res.raw_total == expected_raw
        assert res.total == expected_total
        assert res.clamped == (expected_raw < 0)
        assert len(res.deductions) == 1
        assert res.deductions[0].rule_code == code
        assert res.deductions[0].points == pts

    # All-NULL instrument: DISC-01 (-30) -> raw -10, clamped 0
    null_res = compute_trust(
        TrustSnapshot(
            has_pdf_text_layer=False,
            tickets=[DiscrepancyTicketSnapshot(rule_code="DISC-01", status="OPEN")],
        )
    )
    assert null_res.raw_total == -10
    assert null_res.total == 0
    assert null_res.clamped is True


def test_trust_score_open_vs_resolved_and_deduplication():
    """RESOLVED tickets deduct nothing; multiple tickets for same rule code deduct once."""
    res = compute_trust(
        TrustSnapshot(
            tickets=[
                DiscrepancyTicketSnapshot(rule_code="DISC-02", status="RESOLVED"),
                DiscrepancyTicketSnapshot(rule_code="DISC-03", status="OPEN"),
                DiscrepancyTicketSnapshot(rule_code="DISC-03", status="OPEN"),  # duplicate open
            ]
        )
    )
    # Only DISC-03 deducts once (20 pts): 20 - 20 = 0
    assert res.total == 0
    assert res.raw_total == 0
    assert len(res.deductions) == 1
    assert res.deductions[0].rule_code == "DISC-03"


def test_trust_score_officer_flag_informational_only():
    """OFFICER-FLAG tickets deduct 0 points and are listed informationally."""
    res = compute_trust(
        TrustSnapshot(
            tickets=[DiscrepancyTicketSnapshot(rule_code="OFFICER-FLAG", status="OPEN")]
        )
    )
    assert res.total == 20
    assert res.raw_total == 20
    assert len(res.deductions) == 1
    assert res.deductions[0].rule_code == "OFFICER-FLAG"
    assert res.deductions[0].points == 0


def test_trust_score_latest_verification_record_counts():
    """Only the latest verification record counts; subsequent failure removes +30."""
    # When latest is verified: +30
    res_ok = compute_trust(TrustSnapshot(latest_verification_is_verified=True))
    assert res_ok.total == 50

    # When latest is not verified: 0
    res_fail = compute_trust(TrustSnapshot(latest_verification_is_verified=False))
    assert res_fail.total == 20


# -----------------------------------------------------------------------------
# 2. Service & Consistency Integration Tests
# -----------------------------------------------------------------------------

def test_recompute_trust_database_integration(app_session: Session, sample_institution: Institution):
    """Verifies recompute_trust writes trust_score, trust_breakdown, and trust_computed_at in caller transaction."""
    inst = Instrument(
        institution_id=sample_institution.id,
        fdr_number="TRUST-TEST-001",
        principal_amount=Decimal("3500000.00"),
        status="UNDER_SCRUTINY",
        joint_account_holder="Regional Officer, AICTE & Principal, Sample College",
        trust_score=20,
        created_at=clock_service.now(),
        updated_at=clock_service.now(),
    )
    app_session.add(inst)
    app_session.flush()

    # Initially: no PDF, no provenance, no verifications -> base 20
    result = recompute_trust(app_session, inst.id)
    app_session.commit()

    assert inst.trust_score == 20
    assert inst.trust_breakdown is not None
    assert inst.trust_breakdown["total"] == 20
    assert inst.trust_computed_at is not None

    # Add verified provenance for joint holder: +15
    prov = FieldProvenance(
        instrument_id=inst.id,
        field_name="joint_account_holder",
        submitted_value=inst.joint_account_holder,
        current_value=inst.joint_account_holder,
        provenance_type="MOCK_BANK_CONFIRMED",
    )
    app_session.add(prov)

    # Add verified verification record: +30
    ver = VerificationRecord(
        instrument_id=inst.id,
        provider_type="MOCK_BANK",
        raw_response={"status": "CONFIRMED"},
        is_verified=True,
        mismatch_flags=[],
        executed_at=clock_service.now(),
    )
    app_session.add(ver)
    app_session.commit()

    recompute_trust(app_session, inst.id)
    app_session.commit()

    # 20 base + 15 joint + 30 bank = 65 (no PDF attached yet)
    assert inst.trust_score == 65
    assert inst.trust_breakdown["total"] == 65


def test_trust_score_ui_card_rendering_and_privacy(auth_client_for, app_session: Session, sample_institution: Institution, make_user):
    """UI detail pages render the trust card, and institution view enforces DISC-05 privacy rule."""
    inst_user = make_user("inst_admin_trust", "INSTITUTION_ADMIN", sample_institution.id)
    inst = Instrument(
        institution_id=sample_institution.id,
        fdr_number="PRIVACY-001",
        status="DISCREPANCY_FLAGGED",
        trust_score=0,
        trust_breakdown={
            "total": 0,
            "raw_total": -30,
            "clamped": True,
            "base": 20,
            "additions": [
                {"code": "PDF_TEXT_LAYER", "points": 0, "earned": False, "reason": "No readable PDF found."},
                {"code": "TEXT_MATCHES_FORM", "points": 0, "earned": False, "reason": "Not evaluated."},
                {"code": "JOINT_CLAUSE_CONFIRMED", "points": 0, "earned": False, "reason": "Unconfirmed."},
                {"code": "BANK_VERIFICATION", "points": 0, "earned": False, "reason": "Unverified."},
            ],
            "deductions": [
                {
                    "rule_code": "DISC-05",
                    "points": 50,
                    "ticket_id": str(uuid.uuid4()),
                    "description": "Conflict with institution 1-9900001 instrument 12345",
                }
            ],
            "version": 1,
        },
        created_at=clock_service.now(),
        updated_at=clock_service.now(),
    )
    app_session.add(inst)
    app_session.commit()

    # 1. Institution admin requests detail
    authed_client, _ = auth_client_for(inst_user)
    resp = authed_client.get(f"/institution/instruments/{inst.id}")
    assert resp.status_code == 200
    html = resp.text
    assert "Earned Trust Score Breakdown" in html
    assert "Duplicate FDR number (under review)" in html
    # Assert secret conflicting college text is NOT rendered in the trust card for institution user
    assert "Conflict with institution" not in html
    assert "Advisory score. Decisions remain with the named officers." in html


def test_recompute_trust_cli_script_idempotent(app_session: Session, sample_institution: Institution):
    """scripts/recompute_trust.py runs cleanly and idempotently."""
    import subprocess
    cmd = ["python", "scripts/recompute_trust.py"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0
    assert "Trust score recomputation complete:" in res.stdout
