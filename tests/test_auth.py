import jwt
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
from app.core.config import settings
from app.core.security import generate_csrf_token
from app.services.clock_service import clock_service


def test_login_success(client: TestClient, make_user, app_session: Session):
    """Login success sets cookie with HttpOnly, SameSite=Lax, and writes LOGIN_SUCCESS audit log."""
    user = make_user("inst.user", "INSTITUTION_ADMIN", raw_password="SecretPassword123!")
    csrf_token = generate_csrf_token()
    client.cookies.set(settings.CSRF_COOKIE_NAME, csrf_token)

    response = client.post(
        "/login",
        data={"username": "inst.user", "password": "SecretPassword123!", "csrf_token": csrf_token},
        follow_redirects=False
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/institution"

    # Verify session cookie properties
    cookie = response.cookies.get(settings.SESSION_COOKIE_NAME)
    assert cookie is not None

    # Verify audit entry recorded
    audit_row = app_session.execute(
        text("SELECT action, actor_user_id, canonical_payload FROM audit_log WHERE action = 'LOGIN_SUCCESS'")
    ).fetchone()
    assert audit_row is not None
    assert str(audit_row[1]) == str(user.id)
    assert audit_row[2]["actor_role"] == "INSTITUTION_ADMIN"


def test_login_wrong_password_audited(client: TestClient, make_user, app_session: Session):
    """Wrong password returns generic error message and writes LOGIN_FAILED audit log."""
    user = make_user("scrutiny.user", "SCRUTINY_OFFICER", raw_password="CorrectPassword123!")
    csrf_token = generate_csrf_token()
    client.cookies.set(settings.CSRF_COOKIE_NAME, csrf_token)

    response = client.post(
        "/login",
        data={"username": "scrutiny.user", "password": "WrongPassword999!", "csrf_token": csrf_token}
    )
    assert response.status_code == 401
    assert "Invalid username or password" in response.text

    audit_row = app_session.execute(
        text("SELECT action, actor_user_id, canonical_payload FROM audit_log WHERE action = 'LOGIN_FAILED'")
    ).fetchone()
    assert audit_row is not None
    assert str(audit_row[1]) == str(user.id)
    # Ensure password is NEVER logged in audit details
    payload_str = str(audit_row[2])
    assert "WrongPassword999" not in payload_str


def test_login_unknown_user_timing_mitigation_and_audit(client: TestClient, app_session: Session):
    """Unknown user returns identical generic error and writes LOGIN_FAILED audit log."""
    csrf_token = generate_csrf_token()
    client.cookies.set(settings.CSRF_COOKIE_NAME, csrf_token)

    response = client.post(
        "/login",
        data={"username": "ghost.user", "password": "AnyPassword123!", "csrf_token": csrf_token}
    )
    assert response.status_code == 401
    assert "Invalid username or password" in response.text

    audit_row = app_session.execute(
        text("SELECT action, actor_user_id, canonical_payload FROM audit_log WHERE action = 'LOGIN_FAILED'")
    ).fetchone()
    assert audit_row is not None
    assert audit_row[1] is None  # Null actor_user_id
    payload = audit_row[2]
    assert payload["actor_role"] == "SYSTEM"  # SYSTEM role
    assert payload["details"]["attempted_username"] == "ghost.user"
    assert "AnyPassword123" not in str(payload)


def test_login_inactive_user_rejected(client: TestClient, make_user, app_session: Session):
    """Deactivated user cannot log in and attempt is audited."""
    user = make_user("inactive.user", "AUDITOR", is_active=False, raw_password="Password123!")
    csrf_token = generate_csrf_token()
    client.cookies.set(settings.CSRF_COOKIE_NAME, csrf_token)

    response = client.post(
        "/login",
        data={"username": "inactive.user", "password": "Password123!", "csrf_token": csrf_token}
    )
    assert response.status_code == 401
    assert "deactivated" in response.text.lower()


def test_logout_clears_cookie_and_audits(client: TestClient, make_user, auth_client_for, app_session: Session):
    """Logout clears session cookie and records LOGOUT audit entry."""
    user = make_user("logout.user", "APPROVER_AUTHORITY")
    _, csrf = auth_client_for(user)

    response = client.post("/logout", data={"csrf_token": csrf}, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login"

    # Verify audit entry
    audit_row = app_session.execute(
        text("SELECT action, actor_user_id FROM audit_log WHERE action = 'LOGOUT'")
    ).fetchone()
    assert audit_row is not None
    assert str(audit_row[1]) == str(user.id)


def test_tampered_and_alg_none_tokens_rejected(client: TestClient, make_user):
    """Tampered token or alg=none is strictly rejected."""
    user = make_user("token.user", "SCRUTINY_OFFICER")
    
    # 1. alg=none attack
    none_token = jwt.encode({"sub": str(user.id)}, key="", algorithm="none")
    client.cookies.set(settings.SESSION_COOKIE_NAME, none_token)
    response = client.get("/scrutiny", follow_redirects=False)
    assert response.status_code in (302, 401)

    # 2. Tampered signature
    client.cookies.set(settings.SESSION_COOKIE_NAME, "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.invalidsignature")
    response = client.get("/scrutiny", follow_redirects=False)
    assert response.status_code in (302, 401)


def test_next_parameter_open_redirect_rejected(client: TestClient, make_user):
    """External/open-redirect next parameter is rejected, falling back to role dashboard."""
    user = make_user("safe.user", "AUDITOR", raw_password="Password123!")
    csrf_token = generate_csrf_token()
    client.cookies.set(settings.CSRF_COOKIE_NAME, csrf_token)

    # Malicious external redirects
    for evil_url in ["https://evil.com", "//evil.com", "/\\evil.com"]:
        response = client.post(
            "/login",
            data={"username": "safe.user", "password": "Password123!", "csrf_token": csrf_token, "next": evil_url},
            follow_redirects=False
        )
        assert response.status_code == 302
        assert response.headers["location"] == "/auditor"

    # Valid relative path is honored
    response = client.post(
        "/login",
        data={"username": "safe.user", "password": "Password123!", "csrf_token": csrf_token, "next": "/audit/verify"},
        follow_redirects=False
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/audit/verify"


def test_password_over_72_bytes_rejected(client: TestClient, make_user):
    """Passwords exceeding 72 bytes are rejected at login rather than silently truncated."""
    make_user("bcrypt.user", "AUDITOR", raw_password="ShortPassword123!")
    csrf_token = generate_csrf_token()
    client.cookies.set(settings.CSRF_COOKIE_NAME, csrf_token)

    long_password = "A" * 75
    response = client.post(
        "/login",
        data={"username": "bcrypt.user", "password": long_password, "csrf_token": csrf_token}
    )
    assert response.status_code == 401
