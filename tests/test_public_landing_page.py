import pytest
from unittest.mock import MagicMock
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def test_anonymous_landing_page_clean_of_developer_internals(client: TestClient):
    """GET / as an anonymous user returns 200 and contains zero infrastructure internals."""
    res = client.get("/")
    assert res.status_code == status.HTTP_200_OK

    html = res.text

    # Forbidden internal strings from prompt C3:
    forbidden = [
        "fdr_owner",
        "fdr_app_user",
        "Alembic",
        "Unreachable",
        "Tables",
        "pg_catalog",
        "information_schema",
        "UTC",
        "healthz",
    ]
    for term in forbidden:
        assert term not in html, f"Forbidden internal detail '{term}' found on public homepage!"

    # Positive assertions: Public branding and actions
    assert "AICTE Security Deposit Custody & Lifecycle Management" in html
    assert "Sign In to Portal" in html
    assert "System Status: Operational" in html


def test_landing_page_system_status_degraded_when_db_down(client: TestClient, monkeypatch):
    """When DB check fails, page shows 'Degraded' and leaks zero traceback or error messages."""
    def fail_execute(*args, **kwargs):
        raise ConnectionRefusedError("Simulated postgres port 5432 unreachable exception")

    # Patch session execute on app database sessions
    from sqlalchemy.orm import Session
    monkeypatch.setattr(Session, "execute", fail_execute)

    res = client.get("/")
    assert res.status_code == status.HTTP_200_OK
    assert "Degraded" in res.text
    # Ensure zero traceback or internal error strings leaked
    assert "ConnectionRefusedError" not in res.text
    assert "Traceback" not in res.text
    assert "port 5432" not in res.text


def test_healthz_endpoint_unaffected(client: TestClient):
    """GET /healthz remains available and returns exactly {'status': 'ok'}."""
    res = client.get("/healthz")
    assert res.status_code == status.HTTP_200_OK
    assert res.json() == {"status": "ok"}


def test_anonymous_access_to_staff_pages_denied(client: TestClient):
    """Anonymous access to staff pages redirects to login or returns 401."""
    res_scrutiny = client.get("/scrutiny", follow_redirects=False)
    assert res_scrutiny.status_code in (status.HTTP_302_FOUND, status.HTTP_401_UNAUTHORIZED)

    res_approver = client.get("/approver", follow_redirects=False)
    assert res_approver.status_code in (status.HTTP_302_FOUND, status.HTTP_401_UNAUTHORIZED)


def test_authenticated_user_redirected_from_landing_page(auth_client_for, make_user):
    """Authenticated users requesting '/' are redirected to their role workspace."""
    officer = make_user("landing.officer", "SCRUTINY_OFFICER")
    client, _ = auth_client_for(officer)

    res = client.get("/", follow_redirects=False)
    assert res.status_code == status.HTTP_302_FOUND
    assert res.headers["Location"] == "/scrutiny"
