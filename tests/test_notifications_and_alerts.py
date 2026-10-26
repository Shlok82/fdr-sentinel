"""Web and UI view tests for notifications, alerts, and scheduler controls.

Tests navbar unread badge, filtering, mark-as-read, role access boundaries,
and Time Machine toolbar lifecycle triggers.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal
import pytest
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from app.core.config import settings
from app.models.institution import Institution
from app.models.instrument import Instrument
from app.models.alert import Alert
from app.models.notification import Notification
from app.models.audit import AuditLog
from app.services.clock_service import clock_service


@pytest.fixture
def test_setup(app_session: Session, sample_institution: Institution, sample_institution_b: Institution, make_user):
    inst_a = sample_institution
    inst_b = sample_institution_b

    user_a = make_user("admin.alpha", "INSTITUTION_ADMIN", institution_id=inst_a.id)
    user_b = make_user("admin.beta", "INSTITUTION_ADMIN", institution_id=inst_b.id)
    scrutiny = make_user("officer.scrutiny", "SCRUTINY_OFFICER")
    approver = make_user("authority.approver", "APPROVER_AUTHORITY")
    auditor = make_user("system.auditor", "AUDITOR")
    demo_admin = make_user("demo.master", "AUDITOR", is_demo_admin=True)

    # Instrument for inst_a
    instrument_a = Instrument(
        id=uuid.uuid4(),
        institution_id=inst_a.id,
        fdr_number="FDR-A-100",
        bank_name="Canara Bank",
        principal_amount=Decimal("500000.00"),
        maturity_date=clock_service.today() + timedelta(days=15),
        status="ACTIVE",
        trust_score=20,
        trust_score_breakdown={},
        created_at=clock_service.now(),
        updated_at=clock_service.now(),
    )
    app_session.add(instrument_a)
    app_session.commit()

    # Alert and Notification for inst_a
    alert_a = Alert(
        id=uuid.uuid4(),
        instrument_id=instrument_a.id,
        alert_type="MATURITY_REMINDER_15D",
        severity="WARNING",
        status="OPEN",
        business_date=clock_service.today(),
        details={"days_to_maturity": 15, "fdr_number": "FDR-A-100"},
        created_at=clock_service.now(),
    )
    app_session.add(alert_a)
    app_session.flush()

    notif_a = Notification(
        id=uuid.uuid4(),
        alert_id=alert_a.id,
        audience_role="INSTITUTION_ADMIN",
        audience_institution_id=inst_a.id,
        subject="[ACTION REQUIRED] FDR Renewal Notice: FDR-A-100",
        body="Dear Institution Administrator,\n\nYour FDR approaches maturity in 15 days.",
        sms_text="[FDR-SENTINEL] URGENT: FDR FDR-A-100 matures in 15 days.",
        channels=["IN_APP", "EMAIL", "SMS"],
        language="en",
        created_at=clock_service.now(),
        read_at=None,
    )
    app_session.add(notif_a)
    app_session.commit()

    return {
        "inst_a": inst_a,
        "inst_b": inst_b,
        "user_a": user_a,
        "user_b": user_b,
        "scrutiny": scrutiny,
        "approver": approver,
        "auditor": auditor,
        "demo_admin": demo_admin,
        "instrument_a": instrument_a,
        "alert_a": alert_a,
        "notif_a": notif_a,
    }


def test_navbar_unread_bell_badge_and_mark_as_read(client: TestClient, test_setup, auth_client_for, app_session: Session):
    """Navbar bell renders unread count and decreases after marking notification as read."""
    user_a = test_setup["user_a"]
    notif_a = test_setup["notif_a"]
    _, csrf = auth_client_for(user_a)

    # 1. Access dashboard, bell should show unread count (1)
    res = client.get("/institution/instruments")
    assert res.status_code == 200
    assert 'aria-label="Notifications"' in res.text
    assert 'bg-rose-600' in res.text  # Red badge presence

    # 2. Mark as read via POST /notifications/{id}/read
    read_res = client.post(
        f"/notifications/{notif_a.id}/read",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert read_res.status_code == 303

    # Check notification updated in DB
    app_session.expire_all()
    updated_notif = app_session.query(Notification).filter_by(id=notif_a.id).first()
    assert updated_notif.read_at is not None
    assert updated_notif.read_by_user_id == user_a.id

    # Check audit log written
    audit = app_session.query(AuditLog).filter_by(action="NOTIFICATION_READ", entity_id=notif_a.id).first()
    assert audit is not None
    assert audit.actor_user_id == user_a.id

    # 3. Access dashboard again, unread badge should disappear
    res_after = client.get("/institution/instruments")
    assert res_after.status_code == 200
    assert 'bg-rose-600 rounded-full' not in res_after.text


def test_notifications_list_and_filter(client: TestClient, test_setup, auth_client_for):
    """Institution user can list notifications and filter by unread/high-severity."""
    user_a = test_setup["user_a"]
    auth_client_for(user_a)

    # 1. All filter
    res = client.get("/notifications?filter=all")
    assert res.status_code == 200
    assert "FDR Renewal Notice: FDR-A-100" in res.text
    assert "MATURITY_REMINDER_15D" in res.text

    # 2. Unread filter
    res_unread = client.get("/notifications?filter=unread")
    assert res_unread.status_code == 200
    assert "FDR Renewal Notice: FDR-A-100" in res_unread.text

    # 3. High severity filter (WARNING is not high severity; URGENT/CRITICAL are)
    res_high = client.get("/notifications?filter=high_severity")
    assert res_high.status_code == 200
    assert "No notifications found" in res_high.text


def test_notifications_detail_view_and_previews(client: TestClient, test_setup, auth_client_for):
    """Detail view renders notice body, simulated email preview, SMS preview, and disclaimer banner."""
    user_a = test_setup["user_a"]
    notif_a = test_setup["notif_a"]
    auth_client_for(user_a)

    res = client.get(f"/notifications/{notif_a.id}")
    assert res.status_code == 200
    assert "Delivery Simulated" in res.text
    assert "Simulated Email Preview" in res.text
    assert "Simulated SMS Preview" in res.text
    assert "[FDR-SENTINEL]" in res.text
    assert "View FDR FDR-A-100" in res.text


def test_role_access_matrix_and_scoping(client: TestClient, test_setup, auth_client_for):
    """Enforces strict role permissions and scoping boundaries across notifications, alerts, and scheduler."""
    user_a = test_setup["user_a"]
    user_b = test_setup["user_b"]
    scrutiny = test_setup["scrutiny"]
    approver = test_setup["approver"]
    auditor = test_setup["auditor"]
    notif_a = test_setup["notif_a"]

    # 1. Institution Admin A accessing own notification -> 200
    auth_client_for(user_a)
    res_own = client.get(f"/notifications/{notif_a.id}")
    assert res_own.status_code == 200

    # 2. Institution Admin B accessing Institution A's notification -> 404 (strict scoping)
    auth_client_for(user_b)
    res_other = client.get(f"/notifications/{notif_a.id}")
    assert res_other.status_code == 404

    # 3. Institution Admin accessing /alerts -> 403 Forbidden
    auth_client_for(user_a)
    res_alerts_inst = client.get("/alerts")
    assert res_alerts_inst.status_code == 403

    # 4. Institution Admin accessing /scheduler -> 403 Forbidden
    res_sched_inst = client.get("/scheduler")
    assert res_sched_inst.status_code == 403

    # 5. Scrutiny Officer -> 200 on /alerts and 200 on /scheduler
    auth_client_for(scrutiny)
    assert client.get("/alerts").status_code == 200
    assert client.get("/scheduler").status_code == 200

    # 6. Approver Authority -> 200 on /alerts and 200 on /scheduler
    auth_client_for(approver)
    assert client.get("/alerts").status_code == 200
    assert client.get("/scheduler").status_code == 200

    # 7. Auditor -> 200 on /alerts and 200 on /scheduler
    auth_client_for(auditor)
    assert client.get("/alerts").status_code == 200
    assert client.get("/scheduler").status_code == 200


def test_time_machine_toolbar_triggers_lifecycle_tick(client: TestClient, test_setup, auth_client_for):
    """Time Machine advance +30d advances clock and executes temporal cycle, returning cycle summary."""
    demo_admin = test_setup["demo_admin"]
    _, csrf = auth_client_for(demo_admin)

    res = client.post(
        "/api/v1/demo/clock",
        data={"action": "+30d", "csrf_token": csrf},
    )
    assert res.status_code == 200
    json_data = res.json()
    assert json_data["status"] == "success"
    assert "Life-cycle tick executed" in json_data["cycle_summary"]
