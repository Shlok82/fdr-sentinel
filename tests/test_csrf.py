from fastapi.testclient import TestClient
from app.core.config import settings
from app.core.security import generate_csrf_token


def test_login_csrf_enforcement(client: TestClient, make_user):
    """POST /login rejects missing or invalid CSRF token."""
    make_user("csrf.user", "INSTITUTION_ADMIN", raw_password="ValidPassword123!")

    # 1. Missing CSRF cookie and form token
    res = client.post("/login", data={"username": "csrf.user", "password": "ValidPassword123!"})
    assert res.status_code == 403

    # 2. Mismatched token
    client.cookies.set(settings.CSRF_COOKIE_NAME, "real_token_123")
    res = client.post("/login", data={"username": "csrf.user", "password": "ValidPassword123!", "csrf_token": "wrong_token"})
    assert res.status_code == 403

    # 3. Valid token succeeds
    token = generate_csrf_token()
    client.cookies.set(settings.CSRF_COOKIE_NAME, token)
    res = client.post(
        "/login",
        data={"username": "csrf.user", "password": "ValidPassword123!", "csrf_token": token},
        follow_redirects=False
    )
    assert res.status_code == 302


def test_logout_csrf_enforcement(client: TestClient, make_user, auth_client_for):
    """POST /logout enforces CSRF and is POST-only."""
    user = make_user("logout.csrf", "AUDITOR")
    _, real_csrf = auth_client_for(user)

    # Missing token -> 403
    res = client.post("/logout")
    assert res.status_code == 403

    # Invalid token -> 403
    res = client.post("/logout", data={"csrf_token": "tampered"})
    assert res.status_code == 403

    # Valid token -> 302
    res = client.post("/logout", data={"csrf_token": real_csrf}, follow_redirects=False)
    assert res.status_code == 302


def test_time_machine_csrf_header_and_form(client: TestClient, make_user, auth_client_for):
    """POST /api/v1/demo/clock accepts CSRF via form field or X-CSRF-Token header."""
    demo_admin = make_user("clock.admin", "AUDITOR", is_demo_admin=True)
    _, csrf = auth_client_for(demo_admin)

    # 1. Missing CSRF -> 403
    res = client.post("/api/v1/demo/clock", data={"action": "+30d"})
    assert res.status_code == 403

    # 2. Valid CSRF in form -> 200
    res = client.post("/api/v1/demo/clock", data={"action": "+30d", "csrf_token": csrf})
    assert res.status_code == 200

    # 3. Valid CSRF in HTMX X-CSRF-Token header -> 200
    res = client.post(
        "/api/v1/demo/clock",
        data={"action": "+60d"},
        headers={"X-CSRF-Token": csrf, "HX-Request": "true"}
    )
    assert res.status_code == 200
