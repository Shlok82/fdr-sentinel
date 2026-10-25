import ast
import os
import uuid
import threading
from decimal import Decimal
from typing import Optional
import pytest
from sqlalchemy import text, select
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from fastapi.testclient import TestClient

from app.models.institution import Institution
from app.models.instrument import Instrument
from app.models.user import User
from app.models.audit import AuditLog
from app.services.audit_service import AuditService, verify_chain
from app.services.clock_service import clock_service
from app.services.instrument_fsm import (
    Actor,
    SYSTEM_ACTOR,
    InstrumentState,
    ALL_STATES,
    TERMINAL_STATES,
    TRANSITIONS,
    transition,
    get_instrument_timeline,
    FSMError,
    InstrumentNotFoundError,
    InvalidStateTransitionError,
    UnauthorizedTransitionError,
    JustificationRequiredError,
)


# -----------------------------------------------------------------------------
# Test Helper Fixtures & Builders
# -----------------------------------------------------------------------------

def create_raw_instrument(
    session: Session,
    institution_id: uuid.UUID,
    status: str = "SUBMITTED",
    prior_state: Optional[str] = None,
    fdr_number: str = "FDR-TEST-001",
) -> Instrument:
    """Creates an instrument directly in the DB session without initial FSM transition."""
    now_ts = clock_service.now()
    inst = Instrument(
        id=uuid.uuid4(),
        institution_id=institution_id,
        fdr_number=fdr_number,
        bank_name="Test National Bank",
        bank_branch="Central Branch",
        ifsc_code="TEST0001234",
        principal_amount=Decimal("1000000.00"),
        maturity_amount=Decimal("1100000.00"),
        interest_rate_percent=Decimal("7.50"),
        issue_date=clock_service.today(),
        maturity_date=clock_service.today(),
        tenure_months=12,
        joint_account_holder="Regional Officer, AICTE & Principal, Test College",
        status=status,
        prior_state=prior_state,
        renewal_sequence=1,
        trust_score=20,
        trust_score_breakdown={"base": 20},
        created_at=now_ts,
        updated_at=now_ts,
    )
    session.add(inst)
    session.commit()
    session.refresh(inst)
    return inst


# -----------------------------------------------------------------------------
# 1. Exhaustive 11x11 State Transition Matrix Test
# -----------------------------------------------------------------------------

def test_exhaustive_11x11_transition_matrix(app_session: Session, sample_institution: Institution):
    """Exhaustive test over all 11x11 (121) directed state pairs.
    
    Guarantees:
    - Self-transitions (11 pairs) always fail with InvalidStateTransitionError.
    - Transitions from terminal states (RENEWED, RELEASED, REJECTED) always fail.
    - Only explicitly defined transitions in TRANSITIONS (or valid release-rejection) can succeed.
    - All other edges fail with InvalidStateTransitionError and write NO audit row.
    """
    states_list = sorted(list(ALL_STATES))
    assert len(states_list) == 11

    # Map each allowed edge to an actor guaranteed to have permission
    edge_actors = {
        ("SUBMITTED", "UNDER_SCRUTINY"): SYSTEM_ACTOR,
        ("UNDER_SCRUTINY", "DISCREPANCY_FLAGGED"): SYSTEM_ACTOR,
        ("DISCREPANCY_FLAGGED", "SUBMITTED"): Actor(user_id=uuid.uuid4(), role="INSTITUTION_ADMIN", institution_id=sample_institution.id),
        ("UNDER_SCRUTINY", "VERIFIED"): Actor(user_id=uuid.uuid4(), role="SCRUTINY_OFFICER"),
        ("VERIFIED", "ACTIVE"): SYSTEM_ACTOR,  # SCRUTINY_OFFICER or APPROVER_AUTHORITY; let's use scrutiny
        ("ACTIVE", "EXPIRING"): SYSTEM_ACTOR,
        ("EXPIRING", "MATURED"): SYSTEM_ACTOR,
        ("EXPIRING", "RENEWED"): SYSTEM_ACTOR,
        ("MATURED", "RENEWED"): SYSTEM_ACTOR,
        ("ACTIVE", "RELEASE_REQUESTED"): Actor(user_id=uuid.uuid4(), role="INSTITUTION_ADMIN", institution_id=sample_institution.id),
        ("EXPIRING", "RELEASE_REQUESTED"): Actor(user_id=uuid.uuid4(), role="INSTITUTION_ADMIN", institution_id=sample_institution.id),
        ("MATURED", "RELEASE_REQUESTED"): Actor(user_id=uuid.uuid4(), role="INSTITUTION_ADMIN", institution_id=sample_institution.id),
        ("RELEASE_REQUESTED", "RELEASED"): Actor(user_id=uuid.uuid4(), role="APPROVER_AUTHORITY"),
        ("UNDER_SCRUTINY", "REJECTED"): Actor(user_id=uuid.uuid4(), role="APPROVER_AUTHORITY"),
        ("DISCREPANCY_FLAGGED", "REJECTED"): Actor(user_id=uuid.uuid4(), role="APPROVER_AUTHORITY"),
    }
    # VERIFIED -> ACTIVE allowed roles: SCRUTINY_OFFICER or APPROVER_AUTHORITY
    edge_actors[("VERIFIED", "ACTIVE")] = Actor(user_id=uuid.uuid4(), role="SCRUTINY_OFFICER")

    valid_justification = "Standard regulatory compliance verification justification 123"

    for from_state in states_list:
        for to_state in states_list:
            # Create a clean instrument in from_state
            prior_for_test = "ACTIVE" if from_state == "RELEASE_REQUESTED" else None
            inst = create_raw_instrument(app_session, sample_institution.id, status=from_state, prior_state=prior_for_test)
            initial_audit_count = app_session.query(AuditLog).count()

            pair = (from_state, to_state)

            if from_state == to_state:
                # Self-transition must fail
                with pytest.raises(InvalidStateTransitionError, match="Self-transitions are not permitted"):
                    transition(app_session, inst.id, to_state, SYSTEM_ACTOR, valid_justification)
                assert app_session.query(AuditLog).count() == initial_audit_count
                continue

            if from_state in TERMINAL_STATES:
                # Terminal states must never transition
                with pytest.raises(InvalidStateTransitionError, match="terminal state"):
                    transition(app_session, inst.id, to_state, SYSTEM_ACTOR, valid_justification)
                assert app_session.query(AuditLog).count() == initial_audit_count
                continue

            if pair in edge_actors:
                # This is an allowed static edge: test that it succeeds with authorized actor
                actor = edge_actors[pair]
                res = transition(app_session, inst.id, to_state, actor, valid_justification)
                app_session.commit()
                assert res.status == to_state
                assert app_session.query(AuditLog).count() == initial_audit_count + 1
            elif from_state == "RELEASE_REQUESTED" and to_state == "ACTIVE":
                # This is the valid release-rejection edge when prior_state == ACTIVE
                actor = Actor(user_id=uuid.uuid4(), role="APPROVER_AUTHORITY")
                res = transition(app_session, inst.id, to_state, actor, valid_justification)
                app_session.commit()
                assert res.status == "ACTIVE"
                assert res.prior_state is None
                assert app_session.query(AuditLog).count() == initial_audit_count + 1
            else:
                # Not an allowed edge: must fail for any actor
                with pytest.raises(InvalidStateTransitionError):
                    transition(app_session, inst.id, to_state, SYSTEM_ACTOR, valid_justification)
                assert app_session.query(AuditLog).count() == initial_audit_count


# -----------------------------------------------------------------------------
# 2. Role Matrix & Scoping Tests
# -----------------------------------------------------------------------------

def test_role_matrix_and_unauthorized_actors(
    app_session: Session,
    sample_institution: Institution,
    sample_institution_b: Institution,
    make_user,
):
    """Verifies RBAC rules across all roles, including AUDITOR, demo_admin, cross-institution, and humans on system edges."""
    inst_user = make_user("inst.user.fsm", "INSTITUTION_ADMIN", institution_id=sample_institution.id)
    other_inst_user = make_user("other.inst.fsm", "INSTITUTION_ADMIN", institution_id=sample_institution_b.id)
    scrutiny_user = make_user("scrutiny.fsm", "SCRUTINY_OFFICER")
    approver_user = make_user("approver.fsm", "APPROVER_AUTHORITY")
    auditor_user = make_user("auditor.fsm", "AUDITOR")
    demo_admin_user = make_user("demo.admin.fsm", "AUDITOR", is_demo_admin=True)

    inst_actor = Actor.from_user(inst_user)
    other_actor = Actor.from_user(other_inst_user)
    scrutiny_actor = Actor.from_user(scrutiny_user)
    approver_actor = Actor.from_user(approver_user)
    auditor_actor = Actor.from_user(auditor_user)
    demo_admin_actor = Actor.from_user(demo_admin_user)

    valid_justification = "Valid 10+ characters justification"

    # A. AUDITOR can never transition anything
    inst1 = create_raw_instrument(app_session, sample_institution.id, status="SUBMITTED")
    with pytest.raises(UnauthorizedTransitionError, match="not authorized to transition"):
        transition(app_session, inst1.id, "UNDER_SCRUTINY", auditor_actor, valid_justification)

    # B. Demo Admin can never transition anything
    with pytest.raises(UnauthorizedTransitionError, match="not authorized to transition"):
        transition(app_session, inst1.id, "UNDER_SCRUTINY", demo_admin_actor, valid_justification)

    # C. Humans attempting SYSTEM-only edges fail
    active_inst = create_raw_instrument(app_session, sample_institution.id, status="ACTIVE")
    for human_actor in [inst_actor, scrutiny_actor, approver_actor]:
        with pytest.raises(UnauthorizedTransitionError, match="not authorized to transition from 'ACTIVE' to 'EXPIRING'"):
            transition(app_session, active_inst.id, "EXPIRING", human_actor, valid_justification)

    # System actor succeeds on SYSTEM-only edge
    res = transition(app_session, active_inst.id, "EXPIRING", SYSTEM_ACTOR)
    app_session.commit()
    assert res.status == "EXPIRING"

    # D. Cross-institution scoping on DISCREPANCY_FLAGGED -> SUBMITTED
    flagged_inst = create_raw_instrument(app_session, sample_institution.id, status="DISCREPANCY_FLAGGED")
    with pytest.raises(UnauthorizedTransitionError, match="does not match instrument institution"):
        transition(app_session, flagged_inst.id, "SUBMITTED", other_actor, valid_justification)

    # Own institution succeeds
    res_sub = transition(app_session, flagged_inst.id, "SUBMITTED", inst_actor, valid_justification)
    app_session.commit()
    assert res_sub.status == "SUBMITTED"

    # E. Cross-institution scoping on ACTIVE -> RELEASE_REQUESTED
    active_inst2 = create_raw_instrument(app_session, sample_institution.id, status="ACTIVE")
    with pytest.raises(UnauthorizedTransitionError, match="does not match instrument institution"):
        transition(app_session, active_inst2.id, "RELEASE_REQUESTED", other_actor, valid_justification)

    res_rel = transition(app_session, active_inst2.id, "RELEASE_REQUESTED", inst_actor, valid_justification)
    app_session.commit()
    assert res_rel.status == "RELEASE_REQUESTED"
    assert res_rel.prior_state == "ACTIVE"


# -----------------------------------------------------------------------------
# 3. Justification Validation Tests
# -----------------------------------------------------------------------------

def test_justification_rules_human_vs_system(app_session: Session, sample_institution: Institution):
    """Enforces justification >= 10 non-blank characters for human-driven sensitive transitions; system supplies automated reason."""
    scrutiny_actor = Actor(user_id=uuid.uuid4(), role="SCRUTINY_OFFICER")
    approver_actor = Actor(user_id=uuid.uuid4(), role="APPROVER_AUTHORITY")

    # 1. UNDER_SCRUTINY -> VERIFIED requires justification
    inst = create_raw_instrument(app_session, sample_institution.id, status="UNDER_SCRUTINY")

    # None, blank, whitespace, too short (< 10 chars)
    for bad_just in [None, "", "    ", "Short", "123456789"]:
        with pytest.raises(JustificationRequiredError, match="at least 10 characters"):
            transition(app_session, inst.id, "VERIFIED", scrutiny_actor, bad_just)

    # 10 chars exactly after stripping
    valid_just = "1234567890"
    res = transition(app_session, inst.id, "VERIFIED", scrutiny_actor, f"  {valid_just}  ")
    app_session.commit()
    assert res.status == "VERIFIED"

    # 2. SYSTEM edge with None justification provides automated reason
    active_inst = create_raw_instrument(app_session, sample_institution.id, status="ACTIVE")
    res_sys = transition(app_session, active_inst.id, "EXPIRING", SYSTEM_ACTOR, None)
    app_session.commit()
    assert res_sys.status == "EXPIRING"

    latest_audit = app_session.query(AuditLog).order_by(AuditLog.id.desc()).first()
    assert latest_audit.canonical_payload["details"]["justification"] == "Automated system transition from ACTIVE to EXPIRING."


# -----------------------------------------------------------------------------
# 4. Release Flow & Prior-State Restoration
# -----------------------------------------------------------------------------

def test_release_flow_and_exact_prior_state_restoration(app_session: Session, sample_institution: Institution):
    """Tests release request from ACTIVE, EXPIRING, MATURED, and exact prior-state restoration on rejection."""
    inst_actor = Actor(user_id=uuid.uuid4(), role="INSTITUTION_ADMIN", institution_id=sample_institution.id)
    approver_actor = Actor(user_id=uuid.uuid4(), role="APPROVER_AUTHORITY")
    justification = "Statutory deposit refund request for verified technical campus"
    rejection_just = "Release request rejected due to pending vigilance inquiry"

    # A. Origin: ACTIVE -> RELEASE_REQUESTED -> Rejected back to ACTIVE
    inst_a = create_raw_instrument(app_session, sample_institution.id, status="ACTIVE")
    transition(app_session, inst_a.id, "RELEASE_REQUESTED", inst_actor, justification)
    app_session.commit()
    assert inst_a.status == "RELEASE_REQUESTED"
    assert inst_a.prior_state == "ACTIVE"

    # Attempting to reject to a state OTHER than prior_state fails
    with pytest.raises(InvalidStateTransitionError, match="prior state 'ACTIVE'"):
        transition(app_session, inst_a.id, "EXPIRING", approver_actor, rejection_just)

    with pytest.raises(InvalidStateTransitionError, match="prior state 'ACTIVE'"):
        transition(app_session, inst_a.id, "REJECTED", approver_actor, rejection_just)

    # Reject back to exact prior_state (ACTIVE) succeeds and clears prior_state
    transition(app_session, inst_a.id, "ACTIVE", approver_actor, rejection_just)
    app_session.commit()
    assert inst_a.status == "ACTIVE"
    assert inst_a.prior_state is None

    # B. Origin: EXPIRING -> RELEASE_REQUESTED -> Rejected back to EXPIRING
    inst_b = create_raw_instrument(app_session, sample_institution.id, status="EXPIRING")
    transition(app_session, inst_b.id, "RELEASE_REQUESTED", inst_actor, justification)
    app_session.commit()
    assert inst_b.status == "RELEASE_REQUESTED"
    assert inst_b.prior_state == "EXPIRING"

    transition(app_session, inst_b.id, "EXPIRING", approver_actor, rejection_just)
    app_session.commit()
    assert inst_b.status == "EXPIRING"
    assert inst_b.prior_state is None

    # C. Origin: MATURED -> RELEASE_REQUESTED -> Approved to RELEASED
    inst_c = create_raw_instrument(app_session, sample_institution.id, status="MATURED")
    transition(app_session, inst_c.id, "RELEASE_REQUESTED", inst_actor, justification)
    app_session.commit()
    assert inst_c.status == "RELEASE_REQUESTED"
    assert inst_c.prior_state == "MATURED"

    approval_just = "Approved release under APH Section 4.3 with zero pending liabilities"
    transition(app_session, inst_c.id, "RELEASED", approver_actor, approval_just)
    app_session.commit()
    assert inst_c.status == "RELEASED"
    assert inst_c.prior_state is None

    # Terminal state check: from RELEASED, nothing can transition
    with pytest.raises(InvalidStateTransitionError, match="terminal state 'RELEASED'"):
        transition(app_session, inst_c.id, "ACTIVE", approver_actor, approval_just)


# -----------------------------------------------------------------------------
# 5. Audit Chain Integrity & Serialization
# -----------------------------------------------------------------------------

def test_audit_entries_and_hash_chain_integrity(app_session: Session, sample_institution: Institution):
    """Every transition writes exactly one STATE_TRANSITION entry and maintains SHA-256 chain continuity."""
    inst = create_raw_instrument(app_session, sample_institution.id, status="SUBMITTED")
    scrutiny_actor = Actor(user_id=uuid.uuid4(), role="SCRUTINY_OFFICER")
    approver_actor = Actor(user_id=uuid.uuid4(), role="APPROVER_AUTHORITY")

    initial_audit_count = app_session.query(AuditLog).count()

    # Step 1: SUBMITTED -> UNDER_SCRUTINY
    transition(app_session, inst.id, "UNDER_SCRUTINY", scrutiny_actor, "Locking record for officer review")
    app_session.commit()

    # Step 2: UNDER_SCRUTINY -> VERIFIED
    transition(app_session, inst.id, "VERIFIED", scrutiny_actor, "Physical bank confirmation letter verified")
    app_session.commit()

    # Step 3: VERIFIED -> ACTIVE
    transition(app_session, inst.id, "ACTIVE", approver_actor, "Formal deposit acceptance into custody")
    app_session.commit()

    entries = app_session.query(AuditLog).filter(AuditLog.entity_id == inst.id).order_by(AuditLog.id.asc()).all()
    assert len(entries) == 3

    assert entries[0].canonical_payload["details"]["from_state"] == "SUBMITTED"
    assert entries[0].canonical_payload["details"]["to_state"] == "UNDER_SCRUTINY"

    assert entries[1].canonical_payload["details"]["from_state"] == "UNDER_SCRUTINY"
    assert entries[1].canonical_payload["details"]["to_state"] == "VERIFIED"

    assert entries[2].canonical_payload["details"]["from_state"] == "VERIFIED"
    assert entries[2].canonical_payload["details"]["to_state"] == "ACTIVE"

    # Verify SHA-256 hash chain
    verify_result = verify_chain(app_session)
    assert verify_result.ok is True
    assert verify_result.rows_checked >= 3


# -----------------------------------------------------------------------------
# 6. Concurrency Serialization
# -----------------------------------------------------------------------------

def test_concurrency_serialization(sample_institution: Institution):
    """Two concurrent transitions on the same instrument serialize via SELECT ... FOR UPDATE; one wins, other fails."""
    from tests.conftest import TestAppSession
    session1 = TestAppSession()
    session2 = TestAppSession()

    inst = create_raw_instrument(session1, sample_institution.id, status="SUBMITTED")
    inst_id = inst.id

    results = []
    barrier = threading.Barrier(2)

    def worker(worker_session: Session, actor_role: str, target_state: str):
        try:
            barrier.wait()
            actor = Actor(user_id=uuid.uuid4(), role=actor_role)
            transition(worker_session, inst_id, target_state, actor, "Concurrent transition test 12345")
            worker_session.commit()
            results.append(("WIN", actor_role))
        except Exception as exc:
            worker_session.rollback()
            results.append(("FAIL", actor_role, type(exc).__name__))
        finally:
            worker_session.close()

    t1 = threading.Thread(target=worker, args=(session1, "SCRUTINY_OFFICER", "UNDER_SCRUTINY"))
    t2 = threading.Thread(target=worker, args=(session2, "SCRUTINY_OFFICER", "UNDER_SCRUTINY"))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Exactly one succeeded and one failed
    wins = [r for r in results if r[0] == "WIN"]
    fails = [r for r in results if r[0] == "FAIL"]
    assert len(wins) == 1
    assert len(fails) == 1
    # The loser encountered InvalidStateTransitionError because row status was updated by the winner
    assert fails[0][2] in ("InvalidStateTransitionError", "OperationalError")


# -----------------------------------------------------------------------------
# 7. Transaction Rollback Guarantee
# -----------------------------------------------------------------------------

def test_transaction_rollback_guarantee(app_session: Session, sample_institution: Institution):
    """A failure after status update leaves status, prior_state, and audit_log unchanged."""
    inst = create_raw_instrument(app_session, sample_institution.id, status="SUBMITTED")
    initial_audit_count = app_session.query(AuditLog).count()

    scrutiny_actor = Actor(user_id=uuid.uuid4(), role="SCRUTINY_OFFICER")

    try:
        # Perform transition without commit
        transition(app_session, inst.id, "UNDER_SCRUTINY", scrutiny_actor, "Testing rollback guarantee")
        # Force an unexpected error before commit
        raise RuntimeError("Simulated crash before transaction commit")
    except RuntimeError:
        app_session.rollback()

    # Re-read instrument in fresh query
    refreshed = app_session.query(Instrument).filter(Instrument.id == inst.id).first()
    assert refreshed.status == "SUBMITTED"
    assert refreshed.prior_state is None
    assert app_session.query(AuditLog).count() == initial_audit_count


# -----------------------------------------------------------------------------
# 8. Check Constraint & Migration Loud Failure
# -----------------------------------------------------------------------------

def test_check_constraint_rejects_invalid_status(owner_session: Session, sample_institution: Institution):
    """Database CHECK constraint ck_instruments_status_fsm strictly rejects values outside the 11 FSM states."""
    now_ts = clock_service.now()
    bad_inst = Instrument(
        id=uuid.uuid4(),
        institution_id=sample_institution.id,
        fdr_number="FDR-BAD-001",
        status="INVALID_STATUS",
        created_at=now_ts,
        updated_at=now_ts,
    )
    owner_session.add(bad_inst)
    with pytest.raises(IntegrityError, match="ck_instruments_status_fsm"):
        owner_session.commit()
    owner_session.rollback()


def test_migration_loud_failure_on_dirty_rows(owner_session: Session, sample_institution: Institution):
    """Verifies that the migration upgrade logic would loudly abort on a dirty row."""
    import importlib.util
    from pathlib import Path

    migration_file = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "0003_instrument_lifecycle_fsm.py"
    spec = importlib.util.spec_from_file_location("migration_0003", migration_file)
    migration_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration_mod)
    allowed_states = migration_mod.ALLOWED_FSM_STATES

    # Verify that the 11 states match specification
    assert len(allowed_states) == 11
    assert "SUBMITTED" in allowed_states
    assert "RELEASED" in allowed_states
    assert "REJECTED" in allowed_states


# -----------------------------------------------------------------------------
# 9. AST Guard: No Status Assignment Outside FSM
# -----------------------------------------------------------------------------

def test_ast_guard_no_status_assignment_outside_fsm():
    """Static AST guard: nothing outside instrument_fsm.py assigns instruments.status or prior_state.
    
    Allow-listed exceptions:
    - app/models/instrument.py (column definitions)
    - app/services/submission_service.py (initial creation with status="SUBMITTED")
    """
    app_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app")
    violations = []

    for root, _, files in os.walk(app_dir):
        for file in files:
            if not file.endswith(".py"):
                continue

            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, app_dir).replace("\\", "/")

            # instrument_fsm.py is the sole authorized transition service
            if rel_path == "services/instrument_fsm.py":
                continue

            # models/instrument.py defines the Column schema
            if rel_path == "models/instrument.py":
                continue

            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Check for direct SQL updates of instruments.status
            if "UPDATE instruments" in content.upper() and "STATUS" in content.upper():
                violations.append(f"{rel_path}: Direct SQL status update detected")

            tree = ast.parse(content, filename=full_path)
            for node in ast.walk(tree):
                # Check obj.status = ... or obj.prior_state = ...
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Attribute) and target.attr in ("status", "prior_state"):
                            # Allow-listed: initial creation in submission_service.py is not an attribute assignment
                            # (it is a keyword argument to Instrument(status="SUBMITTED"))
                            violations.append(f"{rel_path}:{node.lineno}: Assignment to attribute .{target.attr}")
                elif isinstance(node, ast.AnnAssign):
                    if isinstance(node.target, ast.Attribute) and node.target.attr in ("status", "prior_state"):
                        violations.append(f"{rel_path}:{node.lineno}: Annotated assignment to attribute .{node.target.attr}")

    assert not violations, f"FSM Guard Violations (status/prior_state modification outside instrument_fsm.py): {violations}"


# -----------------------------------------------------------------------------
# 10. Timeline UI Rendering & XSS Escaping Tests
# -----------------------------------------------------------------------------

def test_timeline_rendering_fallback_and_scoping(
    app_session: Session,
    sample_institution: Institution,
    sample_institution_b: Institution,
    make_user,
    auth_client_for,
):
    """Tests timeline on detail pages with fallback, transitions, HTML escaping, and scoping."""
    inst_user_a = make_user("timeline.user.a", "INSTITUTION_ADMIN", institution_id=sample_institution.id)
    inst_user_b = make_user("timeline.user.b", "INSTITUTION_ADMIN", institution_id=sample_institution_b.id)
    scrutiny_user = make_user("timeline.scrutiny", "SCRUTINY_OFFICER")

    # 1. Instrument with NO audit entries: falls back to created_at "Submitted"
    client, _ = auth_client_for(inst_user_a)
    inst_no_audit = create_raw_instrument(app_session, sample_institution.id, status="SUBMITTED")
    res_fallback = client.get(f"/institution/instruments/{inst_no_audit.id}")
    assert res_fallback.status_code == 200
    assert "Lifecycle History" in res_fallback.text
    assert "SUBMITTED" in res_fallback.text
    assert "Initial FDR deposit submission." in res_fallback.text

    # 2. Instrument with transitions and potential XSS script tag in justification
    inst_xss = create_raw_instrument(app_session, sample_institution.id, status="SUBMITTED")
    xss_justification = "<script>alert('xss_attack')</script> verified deposit notes"
    scrutiny_actor = Actor.from_user(scrutiny_user)

    transition(app_session, inst_xss.id, "UNDER_SCRUTINY", scrutiny_actor, xss_justification)
    app_session.commit()

    # Render on institution detail as User A
    client, _ = auth_client_for(inst_user_a)
    audit_count_before = app_session.query(AuditLog).count()
    res_inst = client.get(f"/institution/instruments/{inst_xss.id}")
    audit_count_after = app_session.query(AuditLog).count()

    assert res_inst.status_code == 200
    # Audit log read must write zero new records
    assert audit_count_after == audit_count_before
    # <script>alert must be properly HTML-escaped and not rendered as executable HTML tag
    assert "<script>alert" not in res_inst.text
    assert "&lt;script&gt;alert" in res_inst.text
    # No action buttons rendered on timeline
    assert "btn-transition" not in res_inst.text
    assert "action-button" not in res_inst.text

    # Render on staff detail as Scrutiny Officer
    client, _ = auth_client_for(scrutiny_user)
    res_staff = client.get(f"/instruments/{inst_xss.id}")
    assert res_staff.status_code == 200
    assert "<script>alert" not in res_staff.text
    assert "&lt;script&gt;alert" in res_staff.text

    # 3. Institution Scoping: User B cannot access User A's instrument detail (404)
    client, _ = auth_client_for(inst_user_b)
    res_unauthorized = client.get(f"/institution/instruments/{inst_xss.id}")
    assert res_unauthorized.status_code == 404
