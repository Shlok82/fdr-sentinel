from sqlalchemy import text
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
from app.core.config import settings
from app.services.clock_service import clock_service


def test_time_machine_toolbar_visibility(client: TestClient, make_user, auth_client_for):
    """Toolbar renders strictly for users with is_demo_admin=true when DEMO_MODE=true."""
    # 1. Non-demo admin user
    normal_auditor = make_user("normal.auditor", "AUDITOR", is_demo_admin=False)
    auth_client_for(normal_auditor)
    res = client.get("/auditor")
    assert res.status_code == 200
    assert "Demo Time Machine" not in res.text

    # 2. Demo admin user
    demo_admin = make_user("demo.jury", "AUDITOR", is_demo_admin=True)
    auth_client_for(demo_admin)
    res = client.get("/auditor")
    assert res.status_code == 200
    assert "Demo Time Machine" in res.text


def test_time_machine_server_side_authorization(client: TestClient, make_user, auth_client_for):
    """Server rejects clock changes from non-demo users even with valid CSRF."""
    normal_officer = make_user("normal.officer", "SCRUTINY_OFFICER", is_demo_admin=False)
    _, csrf = auth_client_for(normal_officer)

    res = client.post("/api/v1/demo/clock", data={"action": "+30d", "csrf_token": csrf})
    assert res.status_code == 403


def test_time_machine_disabled_when_demo_mode_false(client: TestClient, make_user, auth_client_for, monkeypatch):
    """Server returns 404 when DEMO_MODE is false even for demo admin."""
    demo_admin = make_user("demo.disabled", "AUDITOR", is_demo_admin=True)
    _, csrf = auth_client_for(demo_admin)

    monkeypatch.setattr(settings, "DEMO_MODE", False)
    res = client.post("/api/v1/demo/clock", data={"action": "+30d", "csrf_token": csrf})
    assert res.status_code == 404


def test_advance_and_reset_audit_and_banner(client: TestClient, make_user, auth_client_for, app_session: Session):
    """Advance writes audit log and causes simulated date banner to render; reset clears it."""
    demo_admin = make_user("demo.auditor.test", "AUDITOR", is_demo_admin=True)
    _, csrf = auth_client_for(demo_admin)

    # 1. Advance +60d
    res = client.post(
        "/api/v1/demo/clock",
        data={"action": "+60d", "csrf_token": csrf},
        headers={"HX-Request": "true"}
    )
    assert res.status_code == 200
    assert clock_service.is_simulated is True

    # Check simulated banner appears on web pages
    page_res = client.get("/auditor")
    assert "simulated-date-banner" in page_res.text
    assert "Simulated date:" in page_res.text

    # Verify audit log entry
    audit_row = app_session.execute(
        text("SELECT action, actor_user_id, canonical_payload FROM audit_log WHERE action = 'CLOCK_ADVANCE'")
    ).fetchone()
    assert audit_row is not None
    assert str(audit_row[1]) == str(demo_admin.id)
    assert audit_row[2]["details"]["days_advanced"] == 60

    # 2. Reset clock
    res_reset = client.post(
        "/api/v1/demo/clock",
        data={"action": "reset", "csrf_token": csrf},
        headers={"HX-Request": "true"}
    )
    assert res_reset.status_code == 200
    assert clock_service.is_simulated is False

    # Check simulated banner disappears
    page_res2 = client.get("/auditor")
    assert "Simulated date:" not in page_res2.text


def test_login_works_after_time_machine_one_year_advance(client: TestClient, make_user, auth_client_for, app_session: Session):
    """Regression test: Advancing clock by +1 year does NOT break JWT token generation or validation."""
    demo_admin = make_user("demo.timetravel", "AUDITOR", is_demo_admin=True, raw_password="TravelPassword123!")
    _, csrf = auth_client_for(demo_admin)

    # Advance clock by 365 days
    client.post("/api/v1/demo/clock", data={"action": "+1y", "csrf_token": csrf})
    assert clock_service.is_simulated is True

    # Now perform fresh login with separate client session
    new_client = TestClient(client.app)
    # Fetch login page to get fresh CSRF
    login_get = new_client.get("/login")
    new_csrf = login_get.cookies.get(settings.CSRF_COOKIE_NAME)
    new_client.cookies.set(settings.CSRF_COOKIE_NAME, new_csrf)

    login_post = new_client.post(
        "/login",
        data={"username": "demo.timetravel", "password": "TravelPassword123!", "csrf_token": new_csrf},
        follow_redirects=False
    )
    assert login_post.status_code == 302

    # Verify authenticated route accessible with issued token
    authed_res = new_client.get("/auditor")
    assert authed_res.status_code == 200
