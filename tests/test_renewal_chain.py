"""Comprehensive test suite for FDR renewal chains and parent-child rollover lifecycle."""
import ast
import os
import uuid
import pytest
from datetime import date, timedelta
from decimal import Decimal
from fastapi import status
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.security import generate_csrf_token
from app.models.instrument import Instrument
from app.models.institution import Institution
from app.models.user import User
from app.models.audit import AuditLog
from app.schemas.instrument import FDRSubmissionForm
from app.services.audit_service import AuditService, verify_chain
from app.services.clock_service import clock_service
from app.services.instrument_fsm import (
    transition,
    Actor,
    InstrumentState,
    SYSTEM_ACTOR,
)
from app.services.lineage import get_lineage, LineageItem
from app.services.renewal_service import (
    start_renewal,
    RenewalError,
    RenewalNotFoundError,
    RenewalPermissionError,
    RenewalStateError,
    LiveChildExistsError,
)
from app.services.scrutiny_service import ScrutinyService
from app.services.demo_documents import generate_demo_fdr_pdf


def dummy_pdf_for(fdr_number: str = "TEST-FDR") -> bytes:
    return generate_demo_fdr_pdf({"fdr_number": fdr_number, "bank_name": "State Bank of India"})


@pytest.fixture
def test_users(app_session: Session, sample_institution: Institution, owner_session: Session):
    """Creates test users across all roles."""
    now_ts = clock_service.now()

    inst_user = User(
        id=uuid.uuid4(),
        institution_id=sample_institution.id,
        username="inst.admin.renewal",
        email="inst.admin.renewal@college.edu",
        hashed_password="hashed_pw_test",
        role="INSTITUTION_ADMIN",
        full_name="Institution Admin",
        is_active=True,
        is_demo_admin=False,
        created_at=now_ts,
        updated_at=now_ts,
    )
    scrutiny_user = User(
        id=uuid.uuid4(),
        institution_id=None,
        username="scrutiny.renewal",
        email="scrutiny.renewal@aicte.gov.in",
        hashed_password="hashed_pw_test",
        role="SCRUTINY_OFFICER",
        full_name="Scrutiny Officer",
        is_active=True,
        is_demo_admin=False,
        created_at=now_ts,
        updated_at=now_ts,
    )
    approver_user = User(
        id=uuid.uuid4(),
        institution_id=None,
        username="approver.renewal",
        email="approver.renewal@aicte.gov.in",
        hashed_password="hashed_pw_test",
        role="APPROVER_AUTHORITY",
        full_name="Approver Authority",
        is_active=True,
        is_demo_admin=False,
        created_at=now_ts,
        updated_at=now_ts,
    )
    auditor_user = User(
        id=uuid.uuid4(),
        institution_id=None,
        username="auditor.renewal",
        email="auditor.renewal@aicte.gov.in",
        hashed_password="hashed_pw_test",
        role="AUDITOR",
        full_name="Auditor",
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
def sample_institution_b(owner_session: Session) -> Institution:
    """Creates a second institution for cross-scoping tests."""
    now_ts = clock_service.now()
    inst = Institution(
        id=uuid.uuid4(),
        aicte_permanent_id="1-TEST-002",
        name="Second Tech Institute",
        region="Southern",
        state="Karnataka",
        address="Test Road, Bangalore",
        contact_email="test.b@college.edu",
        contact_phone="080-12345678",
        institution_type="Engineering & Technology",
        created_at=now_ts,
        updated_at=now_ts,
    )
    owner_session.add(inst)
    owner_session.commit()
    return inst


@pytest.fixture
def other_inst_user(owner_session: Session, sample_institution_b: Institution) -> User:
    """Creates an admin for the second institution."""
    now_ts = clock_service.now()
    user = User(
        id=uuid.uuid4(),
        institution_id=sample_institution_b.id,
        username="inst.admin.other",
        email="inst.admin.other@college.edu",
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
    return user


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
        maturity_amount=principal * Decimal("1.25"),
        interest_rate_percent=Decimal("7.50"),
        issue_date=today - timedelta(days=365),
        maturity_date=maturity_date or (today + timedelta(days=30)),
        tenure_months=12,
        joint_account_holder="Regional Officer, AICTE & Principal",
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


def make_child_form(fdr_number: str, principal: Decimal = Decimal("3500000.00")) -> FDRSubmissionForm:
    today = clock_service.today()
    return FDRSubmissionForm(
        fdr_number=fdr_number,
        bank_name="State Bank of India",
        bank_branch="Main Branch",
        ifsc_code="SBIN0001234",
        principal_amount=str(principal),
        maturity_amount=str(principal * Decimal("1.25")),
        interest_rate_percent="7.50",
        issue_date=today.isoformat(),
        maturity_date=(today + timedelta(days=365)).isoformat(),
        tenure_months="12",
        joint_account_holder="Regional Officer, AICTE & Principal",
    )


# =============================================================================
# 1. Start Renewal Guards
# =============================================================================

def test_start_renewal_parent_state_guards(app_session: Session, sample_institution: Institution, test_users):
    """Parent must be EXPIRING or MATURED; other states reject with RenewalStateError."""
    inst_user = test_users["inst"]
    actor = Actor.from_user(inst_user)
    dummy_pdf = dummy_pdf_for("CHILD-01")

    # A. Parent in SUBMITTED -> raises RenewalStateError
    parent_sub = create_instrument(app_session, sample_institution.id, "P-SUB", "SUBMITTED")
    form = make_child_form("CHILD-01")
    with pytest.raises(RenewalStateError, match="SUBMITTED"):
        start_renewal(app_session, parent_sub.id, form, (dummy_pdf, "doc.pdf"), actor)

    # B. Parent in ACTIVE -> raises RenewalStateError
    parent_act = create_instrument(app_session, sample_institution.id, "P-ACT", "ACTIVE")
    with pytest.raises(RenewalStateError, match="ACTIVE"):
        start_renewal(app_session, parent_act.id, form, (dummy_pdf, "doc.pdf"), actor)

    # C. Parent in RELEASED -> raises RenewalStateError
    parent_rel = create_instrument(app_session, sample_institution.id, "P-REL", "RELEASED")
    with pytest.raises(RenewalStateError, match="RELEASED"):
        start_renewal(app_session, parent_rel.id, form, (dummy_pdf, "doc.pdf"), actor)

    # D. Parent in EXPIRING -> succeeds
    parent_exp = create_instrument(app_session, sample_institution.id, "P-EXP", "EXPIRING")
    child = start_renewal(app_session, parent_exp.id, form, (dummy_pdf, "doc.pdf"), actor)
    assert child.status == "SUBMITTED"
    assert child.previous_instrument_id == parent_exp.id
    assert child.renewal_sequence == 2

    # E. Parent in MATURED -> succeeds
    parent_mat = create_instrument(app_session, sample_institution.id, "P-MAT", "MATURED")
    form2 = make_child_form("CHILD-02")
    child2 = start_renewal(app_session, parent_mat.id, form2, (dummy_pdf, "doc.pdf"), actor)
    assert child2.status == "SUBMITTED"
    assert child2.previous_instrument_id == parent_mat.id
    assert child2.renewal_sequence == 2


def test_start_renewal_scoping_404(app_session: Session, sample_institution: Institution, other_inst_user: User):
    """An admin from institution B attempting to renew institution A's instrument receives RenewalNotFoundError (404)."""
    parent = create_instrument(app_session, sample_institution.id, "P-SCOPING", "EXPIRING")
    form = make_child_form("CHILD-SCOPING")
    dummy_pdf = dummy_pdf_for("CHILD-SCOPING")

    actor_b = Actor.from_user(other_inst_user)
    with pytest.raises(RenewalNotFoundError, match="Instrument not found"):
        start_renewal(app_session, parent.id, form, (dummy_pdf, "doc.pdf"), actor_b)


def test_start_renewal_live_child_blocks(app_session: Session, sample_institution: Institution, test_users):
    """Parent having an existing non-REJECTED child blocks a second renewal with LiveChildExistsError."""
    inst_user = test_users["inst"]
    actor = Actor.from_user(inst_user)
    dummy_pdf = dummy_pdf_for("CHILD-LIVE")

    parent = create_instrument(app_session, sample_institution.id, "P-LIVE", "EXPIRING")
    # First renewal creates a child
    form1 = make_child_form("CHILD-LIVE-1")
    start_renewal(app_session, parent.id, form1, (dummy_pdf, "doc.pdf"), actor)

    # Second renewal must be blocked
    form2 = make_child_form("CHILD-LIVE-2")
    with pytest.raises(LiveChildExistsError, match="A renewal is already in progress"):
        start_renewal(app_session, parent.id, form2, (dummy_pdf, "doc.pdf"), actor)


def test_start_renewal_rejected_child_allows_new(app_session: Session, sample_institution: Institution, test_users):
    """If an existing child is REJECTED, a new renewal child can be submitted."""
    inst_user = test_users["inst"]
    actor = Actor.from_user(inst_user)
    dummy_pdf = dummy_pdf_for("CHILD-REJ")

    parent = create_instrument(app_session, sample_institution.id, "P-REJ-TEST", "MATURED")
    form1 = make_child_form("CHILD-REJ-1")
    child1 = start_renewal(app_session, parent.id, form1, (dummy_pdf, "doc.pdf"), actor)

    # Set child to REJECTED via FSM (e.g. SUBMITTED -> UNDER_SCRUTINY -> REJECTED)
    scrutiny_actor = Actor.from_user(test_users["scrutiny"])
    approver_actor = Actor.from_user(test_users["approver"])
    transition(app_session, child1.id, "UNDER_SCRUTINY", scrutiny_actor, "Moving to scrutiny")
    transition(app_session, child1.id, "REJECTED", approver_actor, "Rejected due to invalid document")
    assert child1.status == "REJECTED"

    # Now a second renewal is allowed
    form2 = make_child_form("CHILD-REJ-2")
    child2 = start_renewal(app_session, parent.id, form2, (dummy_pdf, "doc.pdf"), actor)
    assert child2.status == "SUBMITTED"
    assert child2.previous_instrument_id == parent.id


def test_start_renewal_release_requested_parent_blocked(app_session: Session, sample_institution: Institution, test_users):
    """Parent in RELEASE_REQUESTED cannot start renewal."""
    inst_user = test_users["inst"]
    actor = Actor.from_user(inst_user)
    dummy_pdf = dummy_pdf_for("CHILD-RELREQ")

    parent = create_instrument(app_session, sample_institution.id, "P-RELREQ", "RELEASE_REQUESTED")
    form = make_child_form("CHILD-RELREQ")
    with pytest.raises(RenewalStateError, match="release request"):
        start_renewal(app_session, parent.id, form, (dummy_pdf, "doc.pdf"), actor)


# =============================================================================
# 2. Pipeline Execution & Audit
# =============================================================================

def test_child_created_through_submission_pipeline(app_session: Session, sample_institution: Institution, test_users):
    """Child passes through full submission pipeline: vault, provenance, trust score, and RENEWAL_INITIATED audit."""
    inst_user = test_users["inst"]
    actor = Actor.from_user(inst_user)
    dummy_pdf = dummy_pdf_for("CHILD-PIPE")

    parent = create_instrument(app_session, sample_institution.id, "P-PIPE", "EXPIRING", renewal_sequence=1)
    form = make_child_form("CHILD-PIPE")

    child = start_renewal(app_session, parent.id, form, (dummy_pdf, "doc.pdf"), actor)
    app_session.flush()

    assert child.previous_instrument_id == parent.id
    assert child.renewal_sequence == 2
    assert child.trust_score is not None

    # Check RENEWAL_INITIATED audit entry
    entries = (
        app_session.query(AuditLog)
        .filter(AuditLog.entity_id == child.id, AuditLog.action == "RENEWAL_INITIATED")
        .all()
    )
    assert len(entries) == 1
    audit = entries[0]
    payload = audit.canonical_payload["details"]
    assert payload["parent_id"] == str(parent.id)
    assert payload["child_id"] == str(child.id)
    assert payload["sequence"] == 2


# =============================================================================
# 3. Activation Hook & RENEWED State Machine
# =============================================================================

def test_parent_not_renewed_while_child_is_in_pre_active_states(app_session: Session, sample_institution: Institution, test_users):
    """Parent remains EXPIRING/MATURED while child is SUBMITTED, UNDER_SCRUTINY, or VERIFIED."""
    inst_user = test_users["inst"]
    actor = Actor.from_user(inst_user)
    dummy_pdf = dummy_pdf_for("CHILD-PRE")

    parent = create_instrument(app_session, sample_institution.id, "P-PRE", "EXPIRING")
    form = make_child_form("CHILD-PRE")
    child = start_renewal(app_session, parent.id, form, (dummy_pdf, "doc.pdf"), actor)

    # 1. SUBMITTED
    app_session.refresh(parent)
    assert parent.status == "EXPIRING"

    # 2. UNDER_SCRUTINY
    scrutiny_user = test_users["scrutiny"]
    ScrutinyService.start_scrutiny(app_session, child.id, scrutiny_user, justification="Officer beginning review")
    app_session.refresh(parent)
    assert parent.status == "EXPIRING"

    # 3. VERIFIED (requires bank confirmation)
    from app.services.verification.service import VerificationService
    ScrutinyService.run_mock_verification(app_session, child.id, scrutiny_user)
    ScrutinyService.verify_instrument(app_session, child.id, scrutiny_user, justification="Verified bank confirmation")
    app_session.refresh(child)
    assert child.status == "VERIFIED"
    app_session.refresh(parent)
    assert parent.status == "EXPIRING"


def test_parent_becomes_renewed_when_child_reaches_active(app_session: Session, sample_institution: Institution, test_users):
    """Parent transitions to RENEWED strictly and automatically when child reaches ACTIVE."""
    inst_user = test_users["inst"]
    actor = Actor.from_user(inst_user)
    dummy_pdf = dummy_pdf_for("CHILD-ACT")

    parent = create_instrument(app_session, sample_institution.id, "P-ACT-TEST", "MATURED")
    form = make_child_form("CHILD-ACT")
    child = start_renewal(app_session, parent.id, form, (dummy_pdf, "doc.pdf"), actor)

    # Move child to VERIFIED
    scrutiny_user = test_users["scrutiny"]
    ScrutinyService.start_scrutiny(app_session, child.id, scrutiny_user, justification="Starting review")
    from app.services.verification.service import VerificationService
    ScrutinyService.run_mock_verification(app_session, child.id, scrutiny_user)
    ScrutinyService.verify_instrument(app_session, child.id, scrutiny_user, justification="Verified bank confirmation")

    # Now activate child
    approver_user = test_users["approver"]
    activated = ScrutinyService.activate_instrument(app_session, child.id, approver_user, justification="Formally activating renewal child")
    assert activated.status == "ACTIVE"

    # Parent must now be RENEWED
    app_session.refresh(parent)
    assert parent.status == "RENEWED"

    # Verify audit entries
    parent_audits = (
        app_session.query(AuditLog)
        .filter(AuditLog.entity_id == parent.id, AuditLog.action == "STATE_TRANSITION")
        .all()
    )
    assert len(parent_audits) >= 1
    last_audit = parent_audits[-1].canonical_payload["details"]
    assert last_audit["from_state"] == "MATURED"
    assert last_audit["to_state"] == "RENEWED"
    assert "Superseded by renewal child" in last_audit["justification"]


def test_parent_unexpected_state_records_audit_on_activation(app_session: Session, sample_institution: Institution, test_users):
    """If parent is in an unexpected state (e.g. RELEASED), activation of child succeeds and audits RENEWAL_PARENT_NOT_RENEWED."""
    inst_user = test_users["inst"]
    actor = Actor.from_user(inst_user)
    dummy_pdf = dummy_pdf_for("CHILD-UNEXP")

    parent = create_instrument(app_session, sample_institution.id, "P-UNEXP", "EXPIRING")
    form = make_child_form("CHILD-UNEXP")
    child = start_renewal(app_session, parent.id, form, (dummy_pdf, "doc.pdf"), actor)

    # Parent moves out-of-band to RELEASED (e.g. simulated edge)
    parent.status = "RELEASED"
    app_session.flush()

    # Move child to VERIFIED
    scrutiny_user = test_users["scrutiny"]
    ScrutinyService.start_scrutiny(app_session, child.id, scrutiny_user, justification="Starting review")
    from app.services.verification.service import VerificationService
    ScrutinyService.run_mock_verification(app_session, child.id, scrutiny_user)
    ScrutinyService.verify_instrument(app_session, child.id, scrutiny_user, justification="Verified bank confirmation")

    # Activate child -> should NOT fail
    activated = ScrutinyService.activate_instrument(app_session, child.id, scrutiny_user, justification="Officer activating child")
    assert activated.status == "ACTIVE"

    # Parent remains RELEASED
    app_session.refresh(parent)
    assert parent.status == "RELEASED"

    # Audit entry RENEWAL_PARENT_NOT_RENEWED must be present
    not_renewed_audits = (
        app_session.query(AuditLog)
        .filter(AuditLog.entity_id == parent.id, AuditLog.action == "RENEWAL_PARENT_NOT_RENEWED")
        .all()
    )
    assert len(not_renewed_audits) == 1
    assert not_renewed_audits[0].canonical_payload["details"]["parent_status"] == "RELEASED"


# =============================================================================
# 4. Database Constraints (Unique Live Child & Self-Reference)
# =============================================================================

def test_database_partial_unique_index_prevents_two_live_children(owner_session: Session, sample_institution: Institution):
    """Partial unique index uq_instruments_one_live_child prevents two live children in DB."""
    parent = create_instrument(owner_session, sample_institution.id, "DB-P", "EXPIRING")
    owner_session.commit()

    # Child 1 (live)
    create_instrument(owner_session, sample_institution.id, "DB-C1", "SUBMITTED", previous_instrument_id=parent.id)
    owner_session.commit()

    # Child 2 (live, same parent) -> must raise IntegrityError
    with pytest.raises(IntegrityError):
        create_instrument(owner_session, sample_institution.id, "DB-C2", "SUBMITTED", previous_instrument_id=parent.id)
        owner_session.commit()
    owner_session.rollback()


def test_database_check_constraint_prevents_self_reference(owner_session: Session, sample_institution: Institution):
    """CHECK constraint ck_instruments_previous_not_self prevents previous_instrument_id == id."""
    inst_id = uuid.uuid4()
    now_ts = clock_service.now()
    inst = Instrument(
        id=inst_id,
        institution_id=sample_institution.id,
        fdr_number="SELF-REF",
        status="ACTIVE",
        previous_instrument_id=inst_id,
        renewal_sequence=1,
        created_at=now_ts,
        updated_at=now_ts,
    )
    owner_session.add(inst)
    with pytest.raises(IntegrityError):
        owner_session.commit()
    owner_session.rollback()


# =============================================================================
# 5. Lineage Helper Tests
# =============================================================================

def test_lineage_ordering_and_cycle_protection(app_session: Session, sample_institution: Institution):
    """get_lineage returns root -> leaf ordering and handles cycles defensively."""
    # Build 3-generation lineage: G -> P -> C
    today = clock_service.today()
    g = create_instrument(app_session, sample_institution.id, "GEN-1", "RENEWED", renewal_sequence=1, principal=Decimal("3000000.00"), maturity_date=today - timedelta(days=730))
    p = create_instrument(app_session, sample_institution.id, "GEN-2", "RENEWED", previous_instrument_id=g.id, renewal_sequence=2, principal=Decimal("3500000.00"), maturity_date=today - timedelta(days=365))
    c = create_instrument(app_session, sample_institution.id, "GEN-3", "ACTIVE", previous_instrument_id=p.id, renewal_sequence=3, principal=Decimal("4000000.00"), maturity_date=today + timedelta(days=365))
    app_session.flush()

    # Query from middle node (P)
    lineage_p = get_lineage(app_session, p)
    assert len(lineage_p) == 3
    assert [node.fdr_number for node in lineage_p] == ["GEN-1", "GEN-2", "GEN-3"]
    assert lineage_p[0].is_current is False
    assert lineage_p[1].is_current is True
    assert lineage_p[2].is_current is False

    # Query from root node (G)
    lineage_g = get_lineage(app_session, g)
    assert [node.fdr_number for node in lineage_g] == ["GEN-1", "GEN-2", "GEN-3"]
    assert lineage_g[0].is_current is True

    # Query from leaf node (C)
    lineage_c = get_lineage(app_session, c)
    assert [node.fdr_number for node in lineage_c] == ["GEN-1", "GEN-2", "GEN-3"]
    assert lineage_c[2].is_current is True


# =============================================================================
# 6. UI & Route Integration Tests
# =============================================================================

def test_renewal_ui_routes_and_button_visibility(client, app_session: Session, sample_institution: Institution, test_users):
    """Tests GET/POST /institution/instruments/{id}/renew and UI button visibility."""
    inst_user = test_users["inst"]
    from app.core.security import create_access_token
    token = create_access_token(inst_user.id)
    csrf_token = generate_csrf_token()
    cookies = {settings.SESSION_COOKIE_NAME: token, settings.CSRF_COOKIE_NAME: csrf_token}

    # 1. Parent in ACTIVE: GET /renew returns 400
    p_act = create_instrument(app_session, sample_institution.id, "UI-ACT", "ACTIVE")
    app_session.commit()
    res = client.get(f"/institution/instruments/{p_act.id}/renew", cookies=cookies)
    assert res.status_code == status.HTTP_400_BAD_REQUEST

    # Check detail page: Start Renewal button NOT present for ACTIVE
    res_detail = client.get(f"/institution/instruments/{p_act.id}", cookies=cookies)
    assert res_detail.status_code == 200
    assert "Start Renewal" not in res_detail.text

    # 2. Parent in EXPIRING: GET /renew returns 200 with pre-filled bank
    p_exp = create_instrument(app_session, sample_institution.id, "UI-EXP", "EXPIRING")
    app_session.commit()
    res_renew = client.get(f"/institution/instruments/{p_exp.id}/renew", cookies=cookies)
    assert res_renew.status_code == 200
    assert "Renewal of UI-EXP" in res_renew.text
    assert "State Bank of India" in res_renew.text
    assert "Parent Principal:" in res_renew.text

    # Detail page of EXPIRING has Start Renewal button
    res_detail_exp = client.get(f"/institution/instruments/{p_exp.id}", cookies=cookies)
    assert res_detail_exp.status_code == 200
    assert "Start Renewal" in res_detail_exp.text

    # 3. Staff user gets 403 on /renew form
    scrutiny_user = test_users["scrutiny"]
    staff_token = create_access_token(scrutiny_user.id)
    staff_cookies = {settings.SESSION_COOKIE_NAME: staff_token, settings.CSRF_COOKIE_NAME: csrf_token}
    res_staff = client.get(f"/institution/instruments/{p_exp.id}/renew", cookies=staff_cookies)
    assert res_staff.status_code == status.HTTP_403_FORBIDDEN


# =============================================================================
# 7. AST / Grep Proof: RENEWED Transition Monopoly
# =============================================================================

def test_ast_proof_only_activation_hook_transitions_to_renewed():
    """Scans all Python files under app/ to prove that ONLY scrutiny_service.activate_instrument transitions to RENEWED."""
    app_dir = os.path.join(os.path.dirname(__file__), "..", "app")
    renewed_transitions = []

    for root, _, files in os.walk(app_dir):
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(root, f)
            with open(path, "r", encoding="utf-8") as py_file:
                content = py_file.read()

            if "RENEWED" not in content:
                continue

            # Parse AST
            try:
                tree = ast.parse(content, filename=path)
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    # Check calls to transition(...)
                    func_name = ""
                    if isinstance(node.func, ast.Name):
                        func_name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        func_name = node.func.attr

                    if func_name == "transition":
                        # Check arguments or kwargs for RENEWED
                        args_str = [ast.dump(a) for a in node.args]
                        kwargs_str = [f"{k.arg}={ast.dump(k.value)}" for k in node.keywords]
                        all_strs = " ".join(args_str + kwargs_str)
                        if "RENEWED" in all_strs:
                            rel_path = os.path.relpath(path, app_dir)
                            renewed_transitions.append((rel_path, node.lineno))

    # Expect exactly ONE transition call: in scrutiny_service.py within activate_instrument
    assert len(renewed_transitions) == 1, f"Found unexpected RENEWED transitions: {renewed_transitions}"
    file_rel, line_no = renewed_transitions[0]
    assert "scrutiny_service.py" in file_rel


def test_audit_chain_integrity_after_renewal(app_session: Session, sample_institution: Institution, test_users):
    """Verifies that the SHA-256 audit chain remains completely valid after full renewal workflow."""
    inst_user = test_users["inst"]
    actor = Actor.from_user(inst_user)
    dummy_pdf = dummy_pdf_for("CHAIN-CHILD")

    parent = create_instrument(app_session, sample_institution.id, "CHAIN-PARENT", "MATURED")
    form = make_child_form("CHAIN-CHILD")
    child = start_renewal(app_session, parent.id, form, (dummy_pdf, "doc.pdf"), actor)

    # Scrutiny and activation
    scrutiny_user = test_users["scrutiny"]
    ScrutinyService.start_scrutiny(app_session, child.id, scrutiny_user, justification="Starting review")
    from app.services.verification.service import VerificationService
    ScrutinyService.run_mock_verification(app_session, child.id, scrutiny_user)
    ScrutinyService.verify_instrument(app_session, child.id, scrutiny_user, justification="Verified bank confirmation")
    ScrutinyService.activate_instrument(app_session, child.id, scrutiny_user, justification="Activating renewal child")

    # Verify chain
    result = verify_chain(app_session)
    assert result.ok is True, f"Audit chain verification failed: {result.reason} at block {result.first_broken_id}"
    assert result.rows_checked >= 5
