import io
import uuid
import pytest
from datetime import date
from decimal import Decimal
from fastapi import status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.instrument import Instrument
from app.models.provenance import FieldProvenance
from app.models.discrepancy import DiscrepancyTicket
from app.models.verification import VerificationRecord
from app.models.scrutiny import ScrutinyNote
from app.services.clock_service import clock_service
from app.services.instrument_fsm import InstrumentState
from app.services.verification.base import VerificationResult
from app.services.verification.mock_bank import MockBankProvider, MockFailureMode
from app.services.verification.manual_letter import ManualConfirmationProvider
from app.services.verification.service import VerificationService
from app.services.scrutiny_service import (
    ScrutinyService,
    ScrutinyError,
    ScrutinyRoleError,
    ScrutinyGuardError,
    StaleStateError,
    JustificationError,
)


@pytest.fixture
def submitted_instrument(app_session: Session, sample_institution) -> Instrument:
    """Creates a sample instrument in SUBMITTED state with declared fields and provenance."""
    now_ts = clock_service.now()
    inst = Instrument(
        id=uuid.uuid4(),
        institution_id=sample_institution.id,
        fdr_number="FDR-TEST-SCRUTINY-001",
        bank_name="State Bank of India",
        bank_branch="Nariman Point Mumbai",
        ifsc_code="SBIN0000123",
        principal_amount=Decimal("500000.00"),
        maturity_amount=Decimal("540000.00"),
        interest_rate_percent=Decimal("8.00"),
        issue_date=date(2026, 1, 1),
        maturity_date=date(2027, 1, 1),
        tenure_months=12,
        joint_account_holder="AICTE and Principal",
        status=InstrumentState.SUBMITTED.value,
        trust_score=20,
        created_at=now_ts,
        updated_at=now_ts,
    )
    app_session.add(inst)
    app_session.flush()

    for field in ["fdr_number", "bank_name", "principal_amount", "maturity_date", "joint_account_holder"]:
        val = str(getattr(inst, field))
        prov = FieldProvenance(
            id=uuid.uuid4(),
            instrument_id=inst.id,
            field_name=field,
            submitted_value=val,
            current_value=val,
            provenance_type="SELF_DECLARED",
            confidence_score=None,
        )
        app_session.add(prov)

    app_session.commit()
    app_session.refresh(inst)
    return inst


# =========================================================================
# 1. Provider Unit Tests
# =========================================================================

def test_mock_bank_provider_success(app_session: Session, submitted_instrument):
    provider = MockBankProvider(failure_mode=MockFailureMode.NONE)
    res = provider.verify_instrument(db=app_session, instrument=submitted_instrument)
    assert res.is_verified is True
    assert len(res.mismatch_flags) == 0
    assert res.provider_type == "MOCK_BANK"
    assert "principal_amount" in res.confirmed_fields


@pytest.mark.parametrize("mode, expected_code", [
    (MockFailureMode.PRINCIPAL_MISMATCH, "PRINCIPAL_MISMATCH"),
    (MockFailureMode.MATURITY_MISMATCH, "MATURITY_MISMATCH"),
    (MockFailureMode.NOT_FOUND, "NOT_FOUND"),
    (MockFailureMode.JOINT_HOLDER_MISSING, "JOINT_HOLDER_MISSING"),
])
def test_mock_bank_provider_failure_modes(app_session: Session, submitted_instrument, mode, expected_code):
    provider = MockBankProvider(failure_mode=mode)
    res = provider.verify_instrument(db=app_session, instrument=submitted_instrument)
    assert res.is_verified is False
    assert any(flag["code"] == expected_code for flag in res.mismatch_flags)


def test_mock_bank_provider_demo_guard(app_session: Session, submitted_instrument, monkeypatch):
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    provider = MockBankProvider(failure_mode=MockFailureMode.NONE)
    with pytest.raises(RuntimeError, match="MockBankProvider is strictly disabled"):
        provider.verify_instrument(db=app_session, instrument=submitted_instrument)


def test_manual_letter_provider_success(app_session: Session, submitted_instrument, make_user):
    officer = make_user("scrutiny_test_officer", "SCRUTINY_OFFICER")
    pdf_bytes = b"%PDF-1.4 sample bank confirmation letter content"
    provider = ManualConfirmationProvider(
        file_bytes=pdf_bytes,
        file_name="confirmation_letter.pdf",
        reference_number="SBI/CONF/2026/01",
        memo="Verified with branch manager over registered landline.",
        officer=officer,
    )
    res = provider.verify_instrument(db=app_session, instrument=submitted_instrument)
    assert res.is_verified is True
    assert res.provider_type == "MANUAL_LETTER"
    assert res.raw_response["reference_number"] == "SBI/CONF/2026/01"
    assert res.raw_response["officer_username"] == officer.username


def test_manual_letter_provider_validation_guards(app_session: Session, submitted_instrument, make_user):
    officer = make_user("scrutiny_guard_officer", "SCRUTINY_OFFICER")
    pdf_bytes = b"%PDF-1.4 valid content"

    # Missing reference number
    provider1 = ManualConfirmationProvider(
        file_bytes=pdf_bytes,
        file_name="letter.pdf",
        reference_number="   ",
        memo="Valid memo",
        officer=officer,
    )
    with pytest.raises(ValueError, match="mandatory"):
        provider1.verify_instrument(db=app_session, instrument=submitted_instrument)

    # Missing memo
    provider2 = ManualConfirmationProvider(
        file_bytes=pdf_bytes,
        file_name="letter.pdf",
        reference_number="REF123",
        memo="",
        officer=officer,
    )
    with pytest.raises(ValueError, match="mandatory"):
        provider2.verify_instrument(db=app_session, instrument=submitted_instrument)

    # Disallowed MIME / file type
    provider3 = ManualConfirmationProvider(
        file_bytes=b"malicious script",
        file_name="letter.exe",
        reference_number="REF123",
        memo="Valid memo",
        officer=officer,
    )
    with pytest.raises(ValueError, match="Invalid file type"):
        provider3.verify_instrument(db=app_session, instrument=submitted_instrument)


# =========================================================================
# 2. Verification Service & Provenance Tagging Tests
# =========================================================================

def test_verification_service_mock_provenance(app_session: Session, submitted_instrument, make_user):
    officer = make_user("officer_mock_prov", "SCRUTINY_OFFICER")

    record, result = VerificationService.execute_mock_verification(
        db=app_session,
        instrument=submitted_instrument,
        officer=officer,
        failure_mode=MockFailureMode.NONE,
    )
    assert record.is_verified is True
    assert record.provider_type == "MOCK_BANK"

    # Check that provenance updated to MOCK_BANK_CONFIRMED, never BANK_CONFIRMED
    provs = app_session.query(FieldProvenance).filter(FieldProvenance.instrument_id == submitted_instrument.id).all()
    for p in provs:
        assert p.provenance_type == "MOCK_BANK_CONFIRMED"
        assert p.confidence_score == Decimal("1.000")


def test_verification_service_mismatch_leaves_provenance_intact(app_session: Session, submitted_instrument, make_user):
    officer = make_user("officer_mismatch", "SCRUTINY_OFFICER")

    record, result = VerificationService.execute_mock_verification(
        db=app_session,
        instrument=submitted_instrument,
        officer=officer,
        failure_mode=MockFailureMode.PRINCIPAL_MISMATCH,
    )
    assert record.is_verified is False

    # Check that provenance did NOT update
    provs = app_session.query(FieldProvenance).filter(FieldProvenance.instrument_id == submitted_instrument.id).all()
    for p in provs:
        assert p.provenance_type == "SELF_DECLARED"


def test_verification_service_manual_provenance(app_session: Session, submitted_instrument, make_user):
    officer = make_user("officer_manual_prov", "SCRUTINY_OFFICER")

    record, result = VerificationService.execute_manual_confirmation(
        db=app_session,
        instrument=submitted_instrument,
        officer=officer,
        file_bytes=b"%PDF-1.4 test confirmation scan",
        file_name="letter.pdf",
        reference_number="LETTER/100",
        memo="Branch signed confirmation letter verified.",
    )
    assert record.is_verified is True
    assert record.provider_type == "MANUAL_LETTER"

    provs = app_session.query(FieldProvenance).filter(FieldProvenance.instrument_id == submitted_instrument.id).all()
    for p in provs:
        assert p.provenance_type == "OFFICER_VERIFIED"
        assert p.verified_by_user_id == officer.id


# =========================================================================
# 3. Scrutiny Service & FSM Guard Tests
# =========================================================================

def test_start_scrutiny_success(app_session: Session, submitted_instrument, make_user):
    officer = make_user("officer_start_scrutiny", "SCRUTINY_OFFICER")
    inst = ScrutinyService.start_scrutiny(app_session, submitted_instrument.id, officer)
    assert inst.status == InstrumentState.UNDER_SCRUTINY.value


def test_start_scrutiny_role_guard(app_session: Session, submitted_instrument, make_user):
    auditor = make_user("auditor_unauth", "AUDITOR")
    with pytest.raises(ScrutinyRoleError):
        ScrutinyService.start_scrutiny(app_session, submitted_instrument.id, auditor)


def test_add_scrutiny_note(app_session: Session, submitted_instrument, make_user):
    officer = make_user("officer_note", "SCRUTINY_OFFICER")
    ScrutinyService.start_scrutiny(app_session, submitted_instrument.id, officer)

    note = ScrutinyService.add_scrutiny_note(
        db=app_session,
        instrument_id=submitted_instrument.id,
        user=officer,
        note_text="All submitted documents match branch records.",
        action_recommended="CLEAR",
    )
    assert note.officer_id == officer.id
    assert note.action_recommended == "CLEAR"
    assert note.note_text == "All submitted documents match branch records."


def test_flag_discrepancy_creates_ticket(app_session: Session, submitted_instrument, make_user):
    officer = make_user("officer_flagger", "SCRUTINY_OFFICER")
    ScrutinyService.start_scrutiny(app_session, submitted_instrument.id, officer)

    inst = ScrutinyService.flag_discrepancy(
        db=app_session,
        instrument_id=submitted_instrument.id,
        user=officer,
        reason="Maturity date does not match the 3-year term mandated in AICTE approval.",
    )
    assert inst.status == InstrumentState.DISCREPANCY_FLAGGED.value

    # Verify open blocking ticket created
    ticket = app_session.query(DiscrepancyTicket).filter(
        DiscrepancyTicket.instrument_id == inst.id,
        DiscrepancyTicket.rule_code == "OFFICER-FLAG",
    ).first()
    assert ticket is not None
    assert ticket.blocking is True
    assert ticket.status == "OPEN"
    assert "Maturity date does not match" in ticket.message


def test_verify_instrument_blocked_by_tickets(app_session: Session, submitted_instrument, make_user):
    officer = make_user("officer_ticket_block", "SCRUTINY_OFFICER")
    ScrutinyService.start_scrutiny(app_session, submitted_instrument.id, officer)

    # Create an open blocking ticket
    ticket = DiscrepancyTicket(
        id=uuid.uuid4(),
        instrument_id=submitted_instrument.id,
        rule_code="TEST-BLOCK",
        severity="CRITICAL",
        status="OPEN",
        blocking=True,
        message="Critical blocking issue",
        created_at=clock_service.now(),
    )
    app_session.add(ticket)
    app_session.commit()

    with pytest.raises(ScrutinyGuardError, match="blocking"):
        ScrutinyService.mark_verified(app_session, submitted_instrument.id, officer)


def test_verify_instrument_blocked_without_verification_record(app_session: Session, submitted_instrument, make_user):
    officer = make_user("officer_no_rec", "SCRUTINY_OFFICER")
    ScrutinyService.start_scrutiny(app_session, submitted_instrument.id, officer)

    with pytest.raises(ScrutinyGuardError, match="bank confirmation record"):
        ScrutinyService.mark_verified(app_session, submitted_instrument.id, officer)


def test_verify_and_activate_clean_flow(app_session: Session, submitted_instrument, make_user):
    officer = make_user("officer_clean_flow", "SCRUTINY_OFFICER")

    ScrutinyService.start_scrutiny(app_session, submitted_instrument.id, officer)
    ScrutinyService.run_mock_verification(app_session, submitted_instrument.id, officer, failure_mode=MockFailureMode.NONE)

    inst_verified = ScrutinyService.mark_verified(app_session, submitted_instrument.id, officer, justification="All checks clean.")
    assert inst_verified.status == InstrumentState.VERIFIED.value

    inst_active = ScrutinyService.activate_instrument(app_session, submitted_instrument.id, officer, justification="Activating deposit.")
    assert inst_active.status == InstrumentState.ACTIVE.value


def test_approver_rejection_success(app_session: Session, submitted_instrument, make_user):
    officer = make_user("officer_recommender", "SCRUTINY_OFFICER")
    approver = make_user("approver_authority", "APPROVER_AUTHORITY")

    ScrutinyService.start_scrutiny(app_session, submitted_instrument.id, officer)
    ScrutinyService.add_scrutiny_note(app_session, submitted_instrument.id, officer, "Recommend rejection due to forged scan.", "REJECT")

    inst_rejected = ScrutinyService.reject_instrument(
        db=app_session,
        instrument_id=submitted_instrument.id,
        user=approver,
        justification="Confirmed non-conforming deposit receipt after bank contact.",
    )
    assert inst_rejected.status == InstrumentState.REJECTED.value


def test_approver_rejection_guards(app_session: Session, submitted_instrument, make_user):
    officer = make_user("officer_bad_reject", "SCRUTINY_OFFICER")
    approver = make_user("approver_bad_reject", "APPROVER_AUTHORITY")

    ScrutinyService.start_scrutiny(app_session, submitted_instrument.id, officer)

    # Scrutiny officer cannot reject
    with pytest.raises(ScrutinyRoleError):
        ScrutinyService.reject_instrument(app_session, submitted_instrument.id, officer, justification="Valid justification 10+ chars")

    # Short justification fails
    with pytest.raises(JustificationError, match="at least 10"):
        ScrutinyService.reject_instrument(app_session, submitted_instrument.id, approver, justification="Too short")


# =========================================================================
# 4. HTTP Routes, Workstations & UI Case Page Tests
# =========================================================================

def test_scrutiny_workstation_page(auth_client_for, make_user, submitted_instrument):
    officer = make_user("workstation_officer", "SCRUTINY_OFFICER")
    client, _ = auth_client_for(officer)

    resp = client.get("/scrutiny")
    assert resp.status_code == status.HTTP_200_OK
    assert "Scrutiny Officer Workstation" in resp.text
    assert submitted_instrument.fdr_number in resp.text
    assert "Review Case" in resp.text


def test_approver_workstation_page_rejection_badge(app_session: Session, auth_client_for, make_user, submitted_instrument):
    officer = make_user("recom_officer", "SCRUTINY_OFFICER")
    approver = make_user("approver_user", "APPROVER_AUTHORITY")

    ScrutinyService.start_scrutiny(app_session, submitted_instrument.id, officer)
    ScrutinyService.add_scrutiny_note(app_session, submitted_instrument.id, officer, "Reject this deposit due to irregularities", "REJECT")
    app_session.commit()

    client, _ = auth_client_for(approver)
    resp = client.get("/approver")
    assert resp.status_code == status.HTTP_200_OK
    assert "Officer Recommends Rejection" in resp.text


def test_staff_case_page_renders_sections(auth_client_for, make_user, submitted_instrument):
    officer = make_user("case_page_officer", "SCRUTINY_OFFICER")
    client, _ = auth_client_for(officer)

    resp = client.get(f"/instruments/{submitted_instrument.id}")
    assert resp.status_code == status.HTTP_200_OK
    assert "Custody Workflow Actions" in resp.text
    assert "Start Scrutiny Process" in resp.text
    assert "Declared Field Attributes & Provenance Audit" in resp.text
    assert "Scrutiny Notes & Recommendations" in resp.text
    assert "Bank Verification Records" in resp.text
    assert "Audit Log & Event Trail" in resp.text


def test_http_start_scrutiny_action(auth_client_for, make_user, submitted_instrument):
    officer = make_user("action_officer", "SCRUTINY_OFFICER")
    client, csrf = auth_client_for(officer)

    # Missing CSRF fails
    resp = client.post(f"/instruments/{submitted_instrument.id}/actions/start-scrutiny")
    assert resp.status_code == status.HTTP_403_FORBIDDEN

    # Valid POST with CSRF
    resp = client.post(
        f"/instruments/{submitted_instrument.id}/actions/start-scrutiny",
        data={"csrf_token": csrf},
        follow_redirects=True,
    )
    assert resp.status_code == status.HTTP_200_OK
    assert "UNDER SCRUTINY" in resp.text


def test_http_mock_verify_action(auth_client_for, make_user, submitted_instrument, app_session: Session):
    officer = make_user("mock_action_officer", "SCRUTINY_OFFICER")
    ScrutinyService.start_scrutiny(app_session, submitted_instrument.id, officer)
    app_session.commit()

    client, csrf = auth_client_for(officer)
    resp = client.post(
        f"/instruments/{submitted_instrument.id}/actions/mock-verify",
        data={"csrf_token": csrf, "failure_mode": "NONE"},
        follow_redirects=True,
    )
    assert resp.status_code == status.HTTP_200_OK
    assert "Demo / Simulated" in resp.text


def test_http_manual_verify_action(auth_client_for, make_user, submitted_instrument, app_session: Session):
    officer = make_user("manual_action_officer", "SCRUTINY_OFFICER")
    ScrutinyService.start_scrutiny(app_session, submitted_instrument.id, officer)
    app_session.commit()

    client, csrf = auth_client_for(officer)
    fake_file = io.BytesIO(b"%PDF-1.4 sample confirmation letter")
    resp = client.post(
        f"/instruments/{submitted_instrument.id}/actions/manual-verify",
        data={
            "csrf_token": csrf,
            "reference_number": "BANK/CONF/999",
            "memo": "Letter hand-delivered by branch representative.",
        },
        files={"letter_file": ("confirmation.pdf", fake_file, "application/pdf")},
        follow_redirects=True,
    )
    assert resp.status_code == status.HTTP_200_OK
    assert "Officer Verified" in resp.text
    assert "BANK/CONF/999" in resp.text


def test_http_approver_reject_action(auth_client_for, make_user, submitted_instrument, app_session: Session):
    officer = make_user("officer_for_rejection", "SCRUTINY_OFFICER")
    approver = make_user("approver_for_rejection", "APPROVER_AUTHORITY")
    ScrutinyService.start_scrutiny(app_session, submitted_instrument.id, officer)
    app_session.commit()

    client, csrf = auth_client_for(approver)
    resp = client.post(
        f"/instruments/{submitted_instrument.id}/actions/reject",
        data={
            "csrf_token": csrf,
            "justification": "Irregular joint endorsement confirmed during audit check.",
        },
        follow_redirects=True,
    )
    assert resp.status_code == status.HTTP_200_OK
    assert "REJECTED" in resp.text
