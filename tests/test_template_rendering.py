import re
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.models import Instrument, Document, FieldProvenance


VALID_PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF"


def _create_sample_fdr_with_doc(client: TestClient, user, csrf: str) -> uuid.UUID:
    """Helper to submit an FDR with a valid PDF scan."""
    res = client.post(
        "/institution/instruments/new",
        data={
            "fdr_number": "FDR-RENDER-001",
            "bank_name": "State Bank of India",
            "principal_amount": "2500000.00",
            "csrf_token": csrf,
        },
        files={"scan_file": ("scan_receipt.pdf", VALID_PDF_BYTES, "application/pdf")},
        follow_redirects=False,
    )
    assert res.status_code == 303
    return uuid.UUID(res.headers["Location"].split("/")[-1])


def test_instrument_detail_download_links_render_and_download(
    client: TestClient, make_user, sample_institution, auth_client_for, app_session: Session
):
    """Regression test: detail pages for institution and staff render valid download URLs without unrendered syntax."""
    inst_user = make_user("inst.render", "INSTITUTION_ADMIN", institution_id=sample_institution.id)
    staff_user = make_user("staff.render", "SCRUTINY_OFFICER")
    _, csrf = auth_client_for(inst_user)

    inst_id = _create_sample_fdr_with_doc(client, inst_user, csrf)

    # 1. Test Institution Detail Page
    auth_client_for(inst_user)
    res_inst = client.get(f"/institution/instruments/{inst_id}")
    assert res_inst.status_code == 200
    html_inst = res_inst.text

    # (a) No unrendered Jinja syntax anywhere in HTML
    assert "{{" not in html_inst, f"Unrendered '{{' found in institution detail HTML"
    assert "{%" not in html_inst, f"Unrendered '{{%' found in institution detail HTML"

    # Extract download links from HTML
    inst_download_links = re.findall(r'href="([^"]*download[^"]*)"', html_inst)
    assert len(inst_download_links) >= 1, "No download link found in institution detail page"

    for link in inst_download_links:
        assert "{{" not in link and "%7B%7B" not in link and "doc.id" not in link, f"Malformed download link: {link}"
        # (b) Following download link as owning institution returns 200 with attachment headers
        res_dl = client.get(link)
        assert res_dl.status_code == 200
        assert res_dl.content == VALID_PDF_BYTES
        assert 'attachment; filename="scan_receipt.pdf"' in res_dl.headers.get("Content-Disposition", "")
        assert res_dl.headers.get("X-Content-Type-Options") == "nosniff"
        assert "no-store" in res_dl.headers.get("Cache-Control", "")

    # 2. Test Staff Detail Page
    auth_client_for(staff_user)
    res_staff = client.get(f"/instruments/{inst_id}")
    assert res_staff.status_code == 200
    html_staff = res_staff.text

    # (a) No unrendered Jinja syntax anywhere in HTML
    assert "{{" not in html_staff, f"Unrendered '{{' found in staff detail HTML"
    assert "{%" not in html_staff, f"Unrendered '{{%' found in staff detail HTML"

    # Extract download links from HTML
    staff_download_links = re.findall(r'href="([^"]*download[^"]*)"', html_staff)
    assert len(staff_download_links) >= 1, "No download link found in staff detail page"

    for link in staff_download_links:
        assert "{{" not in link and "%7B%7B" not in link and "doc.id" not in link, f"Malformed download link: {link}"
        # (b) Following download link as staff returns 200 with attachment headers
        res_staff_dl = client.get(link)
        assert res_staff_dl.status_code == 200
        assert res_staff_dl.content == VALID_PDF_BYTES
        assert 'attachment; filename="scan_receipt.pdf"' in res_staff_dl.headers.get("Content-Disposition", "")
        assert res_staff_dl.headers.get("X-Content-Type-Options") == "nosniff"
        assert "no-store" in res_staff_dl.headers.get("Cache-Control", "")


def test_all_role_pages_no_unrendered_template_syntax(
    client: TestClient, make_user, sample_institution, auth_client_for
):
    """Generic test: renders every reachable page across all five roles and asserts zero unrendered Jinja syntax."""
    inst_admin = make_user("role.inst", "INSTITUTION_ADMIN", institution_id=sample_institution.id)
    scrutiny = make_user("role.scrutiny", "SCRUTINY_OFFICER")
    approver = make_user("role.approver", "APPROVER_AUTHORITY")
    auditor = make_user("role.auditor", "AUDITOR")
    demo_admin = make_user("role.demo_admin", "AUDITOR", is_demo_admin=True)

    # Seed an instrument and document for detail page rendering
    _, csrf = auth_client_for(inst_admin)
    inst_id = _create_sample_fdr_with_doc(client, inst_admin, csrf)

    role_pages = [
        # (user_fixture, page_url)
        (None, "/"),
        (None, "/login"),
        (inst_admin, "/institution"),
        (inst_admin, "/institution/instruments"),
        (inst_admin, "/institution/instruments/new"),
        (inst_admin, f"/institution/instruments/{inst_id}"),
        (scrutiny, "/scrutiny"),
        (scrutiny, f"/instruments/{inst_id}"),
        (scrutiny, "/audit/verify"),
        (approver, "/approver"),
        (approver, f"/instruments/{inst_id}"),
        (approver, "/audit/verify"),
        (auditor, "/auditor"),
        (auditor, f"/instruments/{inst_id}"),
        (auditor, "/audit/verify"),
        (demo_admin, "/auditor"),
        (demo_admin, f"/instruments/{inst_id}"),
        (demo_admin, "/audit/verify"),
    ]

    for user, url in role_pages:
        if user is None:
            client.cookies.clear()
        else:
            auth_client_for(user)

        res = client.get(url)
        assert res.status_code == 200, f"Page {url} for role {getattr(user, 'role', 'ANONYMOUS')} returned {res.status_code}"
        html = res.text
        assert "{{" not in html, f"Unrendered '{{' found in {url} for role {getattr(user, 'role', 'ANONYMOUS')}"
        assert "{%" not in html, f"Unrendered '{{%' found in {url} for role {getattr(user, 'role', 'ANONYMOUS')}"
