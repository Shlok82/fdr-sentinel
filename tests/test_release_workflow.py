"""Comprehensive test suite for dual-key security deposit release workflow."""
import uuid
import hashlib
import io
import pytest
from datetime import date, timedelta
from decimal import Decimal
from fastapi import status
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.security import create_access_token, generate_csrf_token
from app.models.instrument import Instrument
from app.models.institution import Institution
from app.models.user import User
from app.models.release import ReleaseRequest
from app.models.document import Document
from app.models.audit import AuditLog
from app.services.audit_service import AuditService, verify_chain, canonical_json
from app.services.clock_service import clock_service
from app.services.instrument_fsm import (
    transition,
    Actor,
    InstrumentState,
    SYSTEM_ACTOR,
)
from app.services.release_service import (
    request_release,
    record_recommendation,
    decide_release,
    compute_release_order_hash,
    ReleaseError,
    ReleaseNotFoundError,
    ReleasePermissionError,
    ReleaseStateError,
    ReleaseChecklistIncompleteError,
    SecondKeyMissingError,
    ReleaseAlreadyDecidedError,
    REQUIRED_CHECKLIST_KEYS,
)
from app.services.demo_documents import generate_demo_fdr_pdf


def dummy_pdf_for(fdr_number: str = "RELEASE-TEST") -> bytes:
    return generate_demo_fdr_pdf({"fdr_number": fdr_number, "bank_name": "State Bank of India"})


@pytest.fixture
def release_test_users(app_session: Session, sample_institution: Institution, owner_session: Session):
    """Creates test users across all roles."""
    now_ts = clock_service.now()

    inst_user = User(
        id=uuid.uuid4(),
        institution_id=sample_institution.id,
        username="inst.admin.release",
        email="inst.admin.release@college.edu",
        hashed_password="hashed_pw_test",
        role="INSTITUTION_ADMIN",
        full_name="Inst Release Admin",
        is_active=True,
        is_demo_admin=False,
        created_at=now_ts,
        updated_at=now_ts,
    )
    scrutiny_user = User(
        id=uuid.uuid4(),
        institution_id=None,
        username="scrutiny.release",
        email="scrutiny.release@aicte.gov.in",
        hashed_password="hashed_pw_test",
        role="SCRUTINY_OFFICER",
        full_name="Scrutiny Release Officer",
        is_active=True,
        is_demo_admin=False,
        created_at=now_ts,
        updated_at=now_ts,
    )
    approver_user = User(
        id=uuid.uuid4(),
        institution_id=None,
        username="approver.release",
        email="approver.release@aicte.gov.in",
        hashed_password="hashed_pw_test",
        role="APPROVER_AUTHORITY",
        full_name="Competent Approver",
        is_active=True,
        is_demo_admin=False,
        created_at=now_ts,
        updated_at=now_ts,
    )
    auditor_user = User(
        id=uuid.uuid4(),
        institution_id=None,
        username="auditor.release",
        email="auditor.release@aicte.gov.in",
        hashed_password="hashed_pw_test",
        role="AUDITOR",
        full_name="Vigilance Auditor",
        is_active=True,
        is_demo_admin=False,
        created_at=now_ts,
        updated_at=now_ts,
    )

    owner_session.add_all([inst_user, scrutiny_user, approver_user, auditor_user])
    owner_session.commit()

    return {
        "inst": inst_user,
        "scrutiny": scrutiny_user,
        "approver": approver_user,
        "auditor": auditor_user,
    }


@pytest.fixture
def other_inst_and_user(owner_session: Session):
    """Creates a second institution and its admin for tenancy cross-scoping tests."""
    now_ts = clock_service.now()
    inst = Institution(
        id=uuid.uuid4(),
        aicte_permanent_id="1-REL-002",
        name="Second Release Tech College",
        region="Western",
        state="Maharashtra",
        address="Bandra, Mumbai",
        contact_email="mumbai@college.edu",
        contact_phone="022-12345678",
        institution_type="Engineering & Technology",
        created_at=now_ts,
        updated_at=now_ts,
    )
    owner_session.add(inst)
    owner_session.commit()

    user = User(
        id=uuid.uuid4(),
        institution_id=inst.id,
        username="other.inst.release",
        email="other.inst.release@college.edu",
        hashed_password="hashed_pw_test",
        role="INSTITUTION_ADMIN",
        full_name="Other Inst Admin",
        is_active=True,
        is_demo_admin=False,
        created_at=now_ts,
        updated_at=now_ts,
    )
    owner_session.add(user)
    owner_session.commit()

    return {"inst": inst, "user": user}


def create_instrument(
    session: Session,
    institution_id: uuid.UUID,
    fdr_number: str,
    status: str,
    previous_instrument_id: uuid.UUID = None,
    renewal_sequence: int = 1,
    principal: Decimal = Decimal("3500000.00"),
    maturity_date: date = None,
) -> Instrument:
    now_ts = clock_service.now()
    today = clock_service.today()
    inst = Instrument(
        id=uuid.uuid4(),
        institution_id=institution_id,
        fdr_number=fdr_number,
        bank_name="State Bank of India",
        bank_branch="Main Branch",
        ifsc_code="SBIN0001234",
        principal_amount=principal,
        maturity_amount=principal + Decimal("500000.00"),
        interest_rate_percent=Decimal("6.50"),
        issue_date=today - timedelta(days=365),
        maturity_date=maturity_date or (today + timedelta(days=365)),
        tenure_months=24,
        joint_account_holder="Regional Officer AICTE",
        status=status,
        previous_instrument_id=previous_instrument_id,
        renewal_sequence=renewal_sequence,
        trust_score=20,
        trust_score_breakdown={"base": 20},
        created_at=now_ts,
        updated_at=now_ts,
    )
    session.add(inst)
    session.flush()
    return inst


# =============================================================================
# 1. Happy Path Test
# =============================================================================

def test_happy_path_release_workflow(app_session: Session, sample_institution: Institution, release_test_users):
    """1. Happy path: institution requests -> scrutiny recommends (all 4 true + PDF) -> approver approves -> instrument is RELEASED -> release order hash generated."""
    inst_user = release_test_users["inst"]
    scrutiny_user = release_test_users["scrutiny"]
    approver_user = release_test_users["approver"]

    inst = create_instrument(app_session, sample_institution.id, "FDR-REL-01", "ACTIVE")
    app_session.commit()

    # Step 1: Institution admin requests release
    actor_inst = Actor.from_user(inst_user)
    req = request_release(
        session=app_session,
        instrument_id=inst.id,
        actor=actor_inst,
        justification="Four-year B.Tech cycle completed with zero student arrears or pending complaints.",
    )
    app_session.commit()

    assert req.authority_decision == "PENDING"
    assert req.requested_by_user_id == inst_user.id
    assert inst.status == "RELEASE_REQUESTED"
    assert inst.prior_state == "ACTIVE"

    # Step 2: Scrutiny officer records statutory checklist and recommendation
    actor_scrutiny = Actor.from_user(scrutiny_user)
    checklist = {
        "course_cycle_completed": True,
        "no_student_grievances_pending": True,
        "no_vigilance_inquiry_active": True,
        "regional_inspection_cleared": True,
    }
    pdf_bytes = dummy_pdf_for("FDR-REL-01")
    file_obj = io.BytesIO(pdf_bytes)
    file_obj.filename = "clearance_certificate.pdf"

    req_updated = record_recommendation(
        session=app_session,
        request_id=req.id,
        actor=actor_scrutiny,
        checklist=checklist,
        recommendation="RECOMMENDED",
        justification="Verified regional inspection report and confirmed completion of all academic obligations.",
        evidence_upload=file_obj,
    )
    app_session.commit()

    assert req_updated.scrutiny_recommendation == "RECOMMENDED"
    assert req_updated.scrutiny_officer_id == scrutiny_user.id
    assert req_updated.scrutiny_recorded_at is not None
    assert req_updated.scrutiny_evidence_document_id is not None
    assert inst.status == "RELEASE_REQUESTED"  # State does not change yet

    # Step 3: Competent authority approves
    actor_approver = Actor.from_user(approver_user)
    final_req = decide_release(
        session=app_session,
        request_id=req.id,
        actor=actor_approver,
        decision="APPROVE",
        justification="Approved for full deposit release pursuant to AICTE Approval Process Handbook regulations.",
    )
    app_session.commit()

    assert final_req.authority_decision == "APPROVED"
    assert final_req.approver_authority_id == approver_user.id
    assert final_req.decision_timestamp is not None
    assert final_req.release_order_hash is not None
    assert len(final_req.release_order_hash) == 64
    assert inst.status == "RELEASED"
    assert inst.prior_state is None


# =============================================================================
# 2. Recommendation Validation Tests
# =============================================================================

def test_recommendation_blocked_if_any_checklist_item_false(app_session: Session, sample_institution: Institution, release_test_users):
    """2. Recommendation blocked if any checklist item False and recommendation is RECOMMENDED."""
    inst_user = release_test_users["inst"]
    scrutiny_user = release_test_users["scrutiny"]

    inst = create_instrument(app_session, sample_institution.id, "FDR-REL-02", "ACTIVE")
    app_session.commit()

    req = request_release(app_session, inst.id, Actor.from_user(inst_user), "Course cycle completed successfully.")
    app_session.commit()

    # Checklist with 1 False item
    checklist = {
        "course_cycle_completed": True,
        "no_student_grievances_pending": True,
        "no_vigilance_inquiry_active": False,  # Pending inquiry!
        "regional_inspection_cleared": True,
    }
    file_obj = io.BytesIO(dummy_pdf_for())
    file_obj.filename = "evidence.pdf"

    with pytest.raises(ReleaseChecklistIncompleteError, match="All statutory conditions must be cleared"):
        record_recommendation(
            app_session,
            req.id,
            Actor.from_user(scrutiny_user),
            checklist,
            "RECOMMENDED",
            "Attempting recommendation with active inquiry.",
            file_obj,
        )


def test_recommendation_allowed_if_not_recommended_with_false_items(app_session: Session, sample_institution: Institution, release_test_users):
    """3. Recommendation allowed if recommendation is NOT_RECOMMENDED even with False items."""
    inst_user = release_test_users["inst"]
    scrutiny_user = release_test_users["scrutiny"]

    inst = create_instrument(app_session, sample_institution.id, "FDR-REL-03", "ACTIVE")
    app_session.commit()

    req = request_release(app_session, inst.id, Actor.from_user(inst_user), "Requesting deposit release.")
    app_session.commit()

    checklist = {
        "course_cycle_completed": False,
        "no_student_grievances_pending": False,
        "no_vigilance_inquiry_active": False,
        "regional_inspection_cleared": False,
    }

    # NOT_RECOMMENDED with no evidence file
    req_updated = record_recommendation(
        app_session,
        req.id,
        Actor.from_user(scrutiny_user),
        checklist,
        "NOT_RECOMMENDED",
        "Multiple compliance deficiencies observed during inspection.",
        evidence_upload=None,
    )
    app_session.commit()

    assert req_updated.scrutiny_recommendation == "NOT_RECOMMENDED"
    assert req_updated.scrutiny_evidence_document_id is None
    assert inst.status == "RELEASE_REQUESTED"


# =============================================================================
# 3. Dual-Key Enforcement & Approver Tests
# =============================================================================

def test_approver_approval_blocked_if_not_recommended(app_session: Session, sample_institution: Institution, release_test_users):
    """4. Approver approval BLOCKED (SecondKeyMissingError) if scrutiny recommended NOT_RECOMMENDED."""
    inst_user = release_test_users["inst"]
    scrutiny_user = release_test_users["scrutiny"]
    approver_user = release_test_users["approver"]

    inst = create_instrument(app_session, sample_institution.id, "FDR-REL-04", "ACTIVE")
    app_session.commit()

    req = request_release(app_session, inst.id, Actor.from_user(inst_user), "Requesting deposit release.")
    app_session.commit()

    checklist = {k: False for k in REQUIRED_CHECKLIST_KEYS}
    record_recommendation(
        app_session, req.id, Actor.from_user(scrutiny_user), checklist, "NOT_RECOMMENDED", "Cannot recommend release due to defects."
    )
    app_session.commit()

    with pytest.raises(SecondKeyMissingError, match="Cannot approve release without scrutiny officer recommendation"):
        decide_release(
            app_session,
            req.id,
            Actor.from_user(approver_user),
            decision="APPROVE",
            justification="Approver trying to bypass negative scrutiny recommendation.",
        )


def test_approver_approval_blocked_if_scrutiny_not_recorded(app_session: Session, sample_institution: Institution, release_test_users):
    """5. Approver approval BLOCKED if scrutiny has not yet recorded recommendation."""
    inst_user = release_test_users["inst"]
    approver_user = release_test_users["approver"]

    inst = create_instrument(app_session, sample_institution.id, "FDR-REL-05", "ACTIVE")
    app_session.commit()

    req = request_release(app_session, inst.id, Actor.from_user(inst_user), "Requesting deposit release.")
    app_session.commit()

    # Scrutiny has NOT touched req yet
    with pytest.raises(SecondKeyMissingError, match="Cannot approve release without scrutiny officer recommendation"):
        decide_release(
            app_session,
            req.id,
            Actor.from_user(approver_user),
            decision="APPROVE",
            justification="Approver trying to approve before scrutiny key has turned.",
        )


def test_approver_rejection_restores_prior_state(app_session: Session, sample_institution: Institution, release_test_users):
    """6. Approver REJECTION transitions instrument back to prior_state (ACTIVE -> RELEASE_REQUESTED -> REJECTED -> ACTIVE)."""
    inst_user = release_test_users["inst"]
    approver_user = release_test_users["approver"]

    inst = create_instrument(app_session, sample_institution.id, "FDR-REL-06", "ACTIVE")
    app_session.commit()

    req = request_release(app_session, inst.id, Actor.from_user(inst_user), "Requesting deposit release.")
    app_session.commit()

    assert inst.status == "RELEASE_REQUESTED"
    assert inst.prior_state == "ACTIVE"

    # Approver rejects release request
    decide_release(
        app_session,
        req.id,
        Actor.from_user(approver_user),
        decision="REJECT",
        justification="Release request rejected due to pending high court litigation.",
    )
    app_session.commit()

    assert req.authority_decision == "REJECTED"
    assert inst.status == "ACTIVE"


def test_approver_rejection_clears_prior_state(app_session: Session, sample_institution: Institution, release_test_users):
    """7. Approver REJECTION clears instrument.prior_state (does not linger)."""
    inst_user = release_test_users["inst"]
    approver_user = release_test_users["approver"]

    inst = create_instrument(app_session, sample_institution.id, "FDR-REL-07", "EXPIRING")
    app_session.commit()

    req = request_release(app_session, inst.id, Actor.from_user(inst_user), "Requesting deposit release on expiring deposit.")
    app_session.commit()

    assert inst.prior_state == "EXPIRING"

    decide_release(
        app_session,
        req.id,
        Actor.from_user(approver_user),
        decision="REJECT",
        justification="Release rejected; institution instructed to renew deposit.",
    )
    app_session.commit()

    assert inst.status == "EXPIRING"
    assert inst.prior_state is None  # Prior state tracker cleared!


# =============================================================================
# 4. Eligibility & Uniqueness Guards
# =============================================================================

def test_institution_cannot_request_release_on_ineligible_status(app_session: Session, sample_institution: Institution, release_test_users):
    """8. Institution cannot request release on non-eligible status (e.g. SUBMITTED, UNDER_SCRUTINY, DISCREPANCY_FLAGGED)."""
    inst_user = release_test_users["inst"]
    actor_inst = Actor.from_user(inst_user)

    for bad_status in ("SUBMITTED", "UNDER_SCRUTINY", "DISCREPANCY_FLAGGED", "VERIFIED"):
        inst = create_instrument(app_session, sample_institution.id, f"FDR-BAD-{bad_status}", bad_status)
        app_session.commit()

        with pytest.raises(ReleaseStateError, match="Must be ACTIVE, EXPIRING, or MATURED"):
            request_release(app_session, inst.id, actor_inst, "Attempting release on non-eligible status.")


def test_cannot_request_release_if_pending_request_exists(owner_session: Session, sample_institution: Institution, release_test_users):
    """9. Institution cannot request release if another request is already PENDING (unique constraint)."""
    inst_user = release_test_users["inst"]
    actor_inst = Actor.from_user(inst_user)

    inst = create_instrument(owner_session, sample_institution.id, "FDR-REL-09", "ACTIVE")
    owner_session.commit()

    # First request
    req1 = request_release(owner_session, inst.id, actor_inst, "First legitimate release request.")
    owner_session.commit()

    # Python-level defensive guard
    with pytest.raises(ReleaseStateError, match="A pending release request already exists"):
        request_release(owner_session, inst.id, actor_inst, "Second concurrent release request.")

    # DB partial unique index: uq_release_requests_one_pending
    duplicate_req = ReleaseRequest(
        id=uuid.uuid4(),
        instrument_id=inst.id,
        requested_by_user_id=inst_user.id,
        requested_at=clock_service.now(),
        institution_justification="Direct insert attempting duplicate pending.",
        statutory_checklist={},
        authority_decision="PENDING",
        created_at=clock_service.now(),
        updated_at=clock_service.now(),
    )
    owner_session.add(duplicate_req)
    with pytest.raises(IntegrityError):
        owner_session.commit()
    owner_session.rollback()


def test_cannot_request_release_if_live_renewal_child_exists(app_session: Session, sample_institution: Institution, release_test_users):
    """10. Institution cannot request release if a live renewal child exists."""
    inst_user = release_test_users["inst"]
    actor_inst = Actor.from_user(inst_user)

    parent = create_instrument(app_session, sample_institution.id, "FDR-PARENT", "EXPIRING", renewal_sequence=1)
    # Child is active in progress
    child = create_instrument(app_session, sample_institution.id, "FDR-CHILD", "SUBMITTED", previous_instrument_id=parent.id, renewal_sequence=2)
    app_session.commit()

    with pytest.raises(ReleaseStateError, match="a renewal child instrument is active or in progress"):
        request_release(app_session, parent.id, actor_inst, "Attempting to release while renewal in flight.")


def test_scrutiny_cannot_record_recommendation_twice(app_session: Session, sample_institution: Institution, release_test_users):
    """11. Scrutiny officer cannot record recommendation twice on same request."""
    inst_user = release_test_users["inst"]
    scrutiny_user = release_test_users["scrutiny"]

    inst = create_instrument(app_session, sample_institution.id, "FDR-REL-11", "ACTIVE")
    app_session.commit()

    req = request_release(app_session, inst.id, Actor.from_user(inst_user), "Initial release request.")
    app_session.commit()

    checklist = {k: True for k in REQUIRED_CHECKLIST_KEYS}
    file_obj = io.BytesIO(dummy_pdf_for())
    file_obj.filename = "evidence.pdf"

    record_recommendation(
        app_session, req.id, Actor.from_user(scrutiny_user), checklist, "RECOMMENDED", "First review recorded.", file_obj
    )
    app_session.commit()

    # Second attempt on same request raises ReleaseStateError
    file_obj2 = io.BytesIO(dummy_pdf_for())
    file_obj2.filename = "evidence2.pdf"
    with pytest.raises(ReleaseStateError, match="already recorded"):
        record_recommendation(
            app_session, req.id, Actor.from_user(scrutiny_user), checklist, "RECOMMENDED", "Second review attempt.", file_obj2
        )


# =============================================================================
# 5. UI Route & Scoping Tests
# =============================================================================

def test_release_order_view_routing_and_status(client, app_session: Session, sample_institution: Institution, release_test_users):
    """12. Release order view: returns 200 with hash for RELEASED instrument; returns 404 for non-released instrument."""
    inst_user = release_test_users["inst"]
    token = create_access_token(inst_user.id)
    csrf_token = generate_csrf_token()
    cookies = {settings.SESSION_COOKIE_NAME: token, settings.CSRF_COOKIE_NAME: csrf_token}

    # 1. Non-released instrument returns 404
    active_inst = create_instrument(app_session, sample_institution.id, "FDR-NOT-REL", "ACTIVE")
    app_session.commit()

    res_404 = client.get(f"/institution/instruments/{active_inst.id}/release-order", cookies=cookies)
    assert res_404.status_code == status.HTTP_404_NOT_FOUND

    # 2. Fully approved released instrument returns 200
    rel_inst = create_instrument(app_session, sample_institution.id, "FDR-IS-REL", "ACTIVE")
    app_session.commit()

    req = request_release(app_session, rel_inst.id, Actor.from_user(inst_user), "Complete cycle justification.")
    checklist = {k: True for k in REQUIRED_CHECKLIST_KEYS}
    file_obj = io.BytesIO(dummy_pdf_for())
    file_obj.filename = "clearance.pdf"
    record_recommendation(app_session, req.id, Actor.from_user(release_test_users["scrutiny"]), checklist, "RECOMMENDED", "Approved clearance.", file_obj)
    decide_release(app_session, req.id, Actor.from_user(release_test_users["approver"]), "APPROVE", "Final authority release order.")
    app_session.commit()

    res_200 = client.get(f"/institution/instruments/{rel_inst.id}/release-order", cookies=cookies)
    assert res_200.status_code == status.HTTP_200_OK
    assert "REGULATORY COMPLIANCE RELEASE ORDER" in res_200.text
    assert req.release_order_hash in res_200.text


def test_release_order_cross_tenancy_scoping(client, app_session: Session, sample_institution: Institution, release_test_users, other_inst_and_user):
    """13. Release order scoping: other institution gets 404."""
    inst_user = release_test_users["inst"]
    other_user = other_inst_and_user["user"]

    rel_inst = create_instrument(app_session, sample_institution.id, "FDR-SCOPED", "ACTIVE")
    app_session.commit()

    req = request_release(app_session, rel_inst.id, Actor.from_user(inst_user), "Complete cycle justification.")
    checklist = {k: True for k in REQUIRED_CHECKLIST_KEYS}
    file_obj = io.BytesIO(dummy_pdf_for())
    file_obj.filename = "clearance.pdf"
    record_recommendation(app_session, req.id, Actor.from_user(release_test_users["scrutiny"]), checklist, "RECOMMENDED", "Approved clearance.", file_obj)
    decide_release(app_session, req.id, Actor.from_user(release_test_users["approver"]), "APPROVE", "Final authority release order.")
    app_session.commit()

    other_token = create_access_token(other_user.id)
    other_cookies = {settings.SESSION_COOKIE_NAME: other_token, settings.CSRF_COOKIE_NAME: generate_csrf_token()}

    # Other institution user requests Institution A's release order -> 404
    res_forbidden = client.get(f"/institution/instruments/{rel_inst.id}/release-order", cookies=other_cookies)
    assert res_forbidden.status_code == status.HTTP_404_NOT_FOUND


def test_release_evidence_document_download_scoping(client, app_session: Session, sample_institution: Institution, release_test_users):
    """14. Release evidence document: staff can download, institution user gets 404."""
    inst_user = release_test_users["inst"]
    scrutiny_user = release_test_users["scrutiny"]

    inst = create_instrument(app_session, sample_institution.id, "FDR-DOC-SCOPE", "ACTIVE")
    app_session.commit()

    req = request_release(app_session, inst.id, Actor.from_user(inst_user), "Release request with statutory evidence.")
    checklist = {k: True for k in REQUIRED_CHECKLIST_KEYS}
    file_obj = io.BytesIO(dummy_pdf_for())
    file_obj.filename = "statutory_evidence.pdf"
    record_recommendation(app_session, req.id, Actor.from_user(scrutiny_user), checklist, "RECOMMENDED", "All conditions met.", file_obj)
    app_session.commit()

    evidence_doc_id = req.scrutiny_evidence_document_id
    assert evidence_doc_id is not None

    # Staff download: succeeds with 200
    staff_token = create_access_token(scrutiny_user.id)
    staff_cookies = {settings.SESSION_COOKIE_NAME: staff_token, settings.CSRF_COOKIE_NAME: generate_csrf_token()}
    res_staff = client.get(f"/instruments/{inst.id}/documents/{evidence_doc_id}/download", cookies=staff_cookies)
    assert res_staff.status_code == status.HTTP_200_OK

    # Institution download: blocked with 404
    inst_token = create_access_token(inst_user.id)
    inst_cookies = {settings.SESSION_COOKIE_NAME: inst_token, settings.CSRF_COOKIE_NAME: generate_csrf_token()}
    res_inst = client.get(f"/institution/instruments/{inst.id}/documents/{evidence_doc_id}/download", cookies=inst_cookies)
    assert res_inst.status_code == status.HTTP_404_NOT_FOUND


# =============================================================================
# 6. Integrity & Audit Chain Verification
# =============================================================================

def test_release_order_hash_and_audit_chain_integrity(owner_session: Session, sample_institution: Institution, release_test_users):
    """15. Concurrency / integrity: verify release_order_hash matches SHA-256 of canonical fields, audit chain intact."""
    inst_user = release_test_users["inst"]
    scrutiny_user = release_test_users["scrutiny"]
    approver_user = release_test_users["approver"]

    inst = create_instrument(owner_session, sample_institution.id, "FDR-INT-15", "ACTIVE")
    owner_session.commit()

    req = request_release(owner_session, inst.id, Actor.from_user(inst_user), "Audited security deposit release.")
    checklist = {k: True for k in REQUIRED_CHECKLIST_KEYS}
    file_obj = io.BytesIO(dummy_pdf_for())
    file_obj.filename = "audit_evidence.pdf"
    record_recommendation(owner_session, req.id, Actor.from_user(scrutiny_user), checklist, "RECOMMENDED", "Audited verification completed.", file_obj)
    decide_release(owner_session, req.id, Actor.from_user(approver_user), "APPROVE", "Audited approval decision.")
    owner_session.commit()

    # 1. Verify computed hash matches exactly
    expected_hash = compute_release_order_hash(
        release_request_id=req.id,
        instrument_id=inst.id,
        fdr_number=inst.fdr_number,
        institution_id=inst.institution_id,
        sanctioned_amount=inst.principal_amount,
        decision="APPROVED",
        approver_authority_id=approver_user.id,
        timestamp_iso=req.decision_timestamp.isoformat(),
        scrutiny_officer_id=scrutiny_user.id,
        scrutiny_recommendation=req.scrutiny_recommendation,
    )
    assert req.release_order_hash == expected_hash

    # 2. Verify complete audit chain
    chain_result = verify_chain(owner_session)
    assert chain_result.ok is True, f"Audit chain verification failed: {chain_result.reason}"

    # 3. Verify audit log actions
    audit_actions = [
        row.action
        for row in owner_session.query(AuditLog)
        .filter(AuditLog.entity_id == req.id)
        .order_by(AuditLog.id.asc())
        .all()
    ]
    assert "RELEASE_REQUEST_CREATED" in audit_actions
    assert "RELEASE_RECOMMENDATION_RECORDED" in audit_actions
    assert "RELEASE_DECISION_RECORDED" in audit_actions
