import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize(
    "route,allowed_role",
    [
        ("/institution", "INSTITUTION_ADMIN"),
        ("/scrutiny", "SCRUTINY_OFFICER"),
        ("/approver", "APPROVER_AUTHORITY"),
        ("/auditor", "AUDITOR"),
    ]
)
def test_rbac_role_gatekeeping(client: TestClient, make_user, auth_client_for, route, allowed_role):
    """Enforces that role workspaces are accessible only by the designated role."""
    all_roles = ["INSTITUTION_ADMIN", "SCRUTINY_OFFICER", "APPROVER_AUTHORITY", "AUDITOR"]

    for role in all_roles:
        user = make_user(f"user.{role.lower()}", role)
        auth_client_for(user)

        res = client.get(route, headers={"accept": "text/html"}, follow_redirects=False)
        if role == allowed_role:
            assert res.status_code == 200, f"Expected 200 for {role} at {route}, got {res.status_code}"
        else:
            assert res.status_code == 403, f"Expected 403 for {role} at {route}, got {res.status_code}"


def test_audit_verify_role_visibility(client: TestClient, make_user, auth_client_for):
    """SPEC §3: INSTITUTION_ADMIN gets 403; staff roles and demo admin can access /audit/verify."""
    # 1. Institution admin gets 403
    inst_user = make_user("inst.test", "INSTITUTION_ADMIN")
    auth_client_for(inst_user)
    res = client.get("/audit/verify")
    assert res.status_code == 403

    # 2. Staff roles get 200
    for staff_role in ["SCRUTINY_OFFICER", "APPROVER_AUTHORITY", "AUDITOR"]:
        staff_user = make_user(f"staff.{staff_role.lower()}", staff_role)
        auth_client_for(staff_user)
        res = client.get("/audit/verify")
        assert res.status_code == 200
        assert "Audit Log Tamper-Detection Verification" in res.text

    # 3. Demo admin gets 200
    demo_admin = make_user("demo.auditor", "AUDITOR", is_demo_admin=True)
    auth_client_for(demo_admin)
    res = client.get("/audit/verify")
    assert res.status_code == 200


def test_unauthenticated_request_redirects_to_login(client: TestClient):
    """Unauthenticated access to protected routes redirects to /login with next parameter."""
    res = client.get("/scrutiny", headers={"accept": "text/html"}, follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"] == "/login?next=/scrutiny"
