import uuid
from sqlalchemy import text
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
from app.services.audit_service import AuditService


def test_audit_verify_fresh_chain(client: TestClient, make_user, auth_client_for, app_session: Session):
    """GET /audit/verify displays STATUS: OK on untampered chain and writes zero audit entries."""
    auditor = make_user("lead.auditor", "AUDITOR")
    auth_client_for(auditor)

    # Append 3 audit records
    for i in range(1, 4):
        AuditService.append(
            app_session,
            entity_type="SYSTEM",
            entity_id=uuid.uuid4(),
            action=f"INITIAL_EVENT_{i}",
            actor_user_id=auditor.id,
            actor_role=auditor.role,
            details={}
        )
    app_session.commit()

    count_before = app_session.execute(text("SELECT count(*) FROM audit_log")).scalar()

    res = client.get("/audit/verify")
    assert res.status_code == 200
    assert "STATUS: OK" in res.text
    assert "ZERO Tampering Detected" in res.text

    # Read-only verification: must NOT write new audit records
    count_after = app_session.execute(text("SELECT count(*) FROM audit_log")).scalar()
    assert count_before == count_after


def test_audit_verify_detects_tampering(client: TestClient, make_user, auth_client_for, owner_session: Session, app_session: Session):
    """GET /audit/verify reports STATUS: BROKEN and identifies altered row ID when tampered."""
    auditor = make_user("vigilance.auditor", "AUDITOR")
    auth_client_for(auditor)

    # Append 3 audit records
    for i in range(1, 4):
        AuditService.append(
            app_session,
            entity_type="INSTRUMENT",
            entity_id=uuid.uuid4(),
            action=f"AUDIT_RECORD_{i}",
            actor_user_id=auditor.id,
            actor_role=auditor.role,
            details={"step": i}
        )
    app_session.commit()

    # Tamper with row 2 using owner connection
    owner_session.execute(text("UPDATE audit_log SET action = 'HACKED_ACTION' WHERE id = 2"))
    owner_session.commit()

    res = client.get("/audit/verify")
    assert res.status_code == 200
    assert "STATUS: BROKEN" in res.text
    assert "TAMPER ALERT" in res.text
    assert "2" in res.text  # Identifies broken row ID 2
    # Auditor role gets technical diagnostic section
    assert "Auditor Vigilance Diagnostic" in res.text


def test_audit_verify_scrutiny_sees_status_only(client: TestClient, make_user, auth_client_for, owner_session: Session, app_session: Session):
    """Scrutiny officer sees status (OK/BROKEN and row ID), but NOT full auditor diagnostic details."""
    scrutiny = make_user("scrutiny.view", "SCRUTINY_OFFICER")
    auth_client_for(scrutiny)

    # Append and tamper
    AuditService.append(app_session, entity_type="INSTRUMENT", entity_id=uuid.uuid4(), action="RECORD_1", actor_user_id=scrutiny.id, actor_role=scrutiny.role)
    app_session.commit()
    owner_session.execute(text("UPDATE audit_log SET action = 'ALTERED' WHERE id = 1"))
    owner_session.commit()

    res = client.get("/audit/verify")
    assert res.status_code == 200
    assert "STATUS: BROKEN" in res.text
    # Diagnostic section hidden for non-auditors
    assert "Auditor Vigilance Diagnostic" not in res.text
