from fastapi.testclient import TestClient


def test_footer_text_present_on_all_pages(client: TestClient, make_user, auth_client_for):
    """Mandatory statutory notice 'Prototype: simulated data, no live bank integration' on every page."""
    mandatory_notice = "Prototype: simulated data, no live bank integration"

    # 1. Login page
    res_login = client.get("/login")
    assert mandatory_notice in res_login.text

    # 2. Public index page
    res_index = client.get("/")
    assert mandatory_notice in res_index.text

    # 3. Authenticated workspaces for each role
    for role, path in [
        ("INSTITUTION_ADMIN", "/institution"),
        ("SCRUTINY_OFFICER", "/scrutiny"),
        ("APPROVER_AUTHORITY", "/approver"),
        ("AUDITOR", "/auditor"),
    ]:
        user = make_user(f"footer.{role.lower()}", role)
        auth_client_for(user)
        res = client.get(path)
        assert res.status_code == 200
        assert mandatory_notice in res.text

    # 4. Error page (404)
    res_404 = client.get("/non-existent-page")
    assert res_404.status_code == 404
    assert mandatory_notice in res_404.text
