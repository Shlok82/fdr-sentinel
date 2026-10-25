import os
import re
import uuid
from datetime import date
from decimal import Decimal
import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.main import app
from app.models import Instrument, Document, FieldProvenance
from app.services.clock_service import clock_service


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

        # Assert no leftover development/stage/roadmap strings are rendered
        assert not re.search(r"stage\s*\d", html, re.IGNORECASE), f"Found 'stage \\d' reference in {url}"
        assert not re.search(r"arrives in", html, re.IGNORECASE), f"Found 'arrives in' reference in {url}"
        assert not re.search(r"coming soon", html, re.IGNORECASE), f"Found 'coming soon' reference in {url}"
        assert not re.search(r"\btodo\b", html, re.IGNORECASE), f"Found 'todo' reference in {url}"
        assert not re.search(r">[^<]*\bplaceholder\b", html, re.IGNORECASE), f"Found rendered 'placeholder' text in {url}"


def test_healthz_endpoint_minimal(client: TestClient):
    """Health endpoint strictly adheres to minimal contract returning only {'status': 'ok'}."""
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}
    assert set(res.json().keys()) == {"status"}


def test_no_raw_formatting_in_templates():
    """Scans all templates to ensure formatting bypasses are forbidden; shared filters are the only allowed formatters."""
    templates_dir = "app/templates"
    forbidden_patterns = [
        (re.compile(r'\.format\('), "Raw .format() call"),
        (re.compile(r'\"\{:,'), "Raw '{:, format string"),
        (re.compile(r'\bstrftime\b'), "Raw .strftime() call"),
        (re.compile(r'\|\s*round'), "Raw |round filter"),
        (re.compile(r'\.date\(\)'), "Raw .date() call"),
    ]

    violations = []
    for root, _, files in os.walk(templates_dir):
        for f in files:
            if f.endswith(".html"):
                p = os.path.join(root, f)
                with open(p, encoding="utf-8") as fh:
                    for line_no, line in enumerate(fh, 1):
                        for pat, desc in forbidden_patterns:
                            if pat.search(line):
                                violations.append(f"{p}:{line_no} [{desc}]: {line.strip()}")

    assert not violations, (
        "Templates must exclusively use shared null-safe filters (format_inr, format_date, format_datetime, format_filesize, default_dash).\n"
        "Found raw formatting violations:\n" + "\n".join(violations)
    )


def test_realistic_instruments_rendering_null_safety(
    client: TestClient, make_user, sample_institution, auth_client_for, owner_session: Session
):
    """Verifies that templates gracefully handle realistic edge-case instruments without 500 or unrendered syntax."""
    inst_user = make_user("inst.edge", "INSTITUTION_ADMIN", institution_id=sample_institution.id)
    scrutiny_user = make_user("scrutiny.edge", "SCRUTINY_OFFICER")
    approver_user = make_user("approver.edge", "APPROVER_AUTHORITY")
    auditor_user = make_user("auditor.edge", "AUDITOR")
    demo_admin_user = make_user("demo.edge", "AUDITOR", is_demo_admin=True)

    now_ts = clock_service.now()

    # 1. Instrument with EVERY nullable business field as NULL
    inst_all_null = Instrument(
        id=uuid.uuid4(),
        institution_id=sample_institution.id,
        fdr_number=None,
        bank_name=None,
        bank_branch=None,
        ifsc_code=None,
        principal_amount=None,
        maturity_amount=None,
        interest_rate_percent=None,
        issue_date=None,
        maturity_date=None,
        tenure_months=None,
        joint_account_holder=None,
        status="SUBMITTED",
        trust_score=20,
        trust_score_breakdown={},
        created_at=now_ts,
        updated_at=now_ts,
    )

    # 2. Instrument with only SOME fields blank (missing bank_name, branch, IFSC, joint account)
    inst_partial_blank = Instrument(
        id=uuid.uuid4(),
        institution_id=sample_institution.id,
        fdr_number="FDR-PARTIAL-001",
        bank_name=None,
        bank_branch=None,
        ifsc_code=None,
        principal_amount=Decimal("1500000.00"),
        maturity_amount=Decimal("1800000.00"),
        interest_rate_percent=Decimal("7.00"),
        issue_date=date(2025, 1, 1),
        maturity_date=date(2028, 1, 1),
        tenure_months=36,
        joint_account_holder=None,
        status="SUBMITTED",
        trust_score=20,
        trust_score_breakdown={},
        created_at=now_ts,
        updated_at=now_ts,
    )

    # 3. Instrument with an EXPIRED maturity date in the past
    inst_expired = Instrument(
        id=uuid.uuid4(),
        institution_id=sample_institution.id,
        fdr_number="FDR-EXPIRED-002",
        bank_name="State Bank of India",
        bank_branch="Main Branch, Pune",
        ifsc_code="SBIN0000456",
        principal_amount=Decimal("3500000.00"),
        maturity_amount=Decimal("4300000.00"),
        interest_rate_percent=Decimal("7.50"),
        issue_date=date(2020, 1, 1),
        maturity_date=date(2023, 1, 1),
        tenure_months=36,
        joint_account_holder="Regional Officer, AICTE",
        status="SUBMITTED",
        trust_score=20,
        trust_score_breakdown={},
        created_at=now_ts,
        updated_at=now_ts,
    )

    # 4. Instrument with a VERY LARGE principal amount (₹99.99 Crores)
    inst_large_amount = Instrument(
        id=uuid.uuid4(),
        institution_id=sample_institution.id,
        fdr_number="FDR-LARGE-003",
        bank_name="Punjab National Bank",
        bank_branch="Connaught Place, New Delhi",
        ifsc_code="PUNB0001234",
        principal_amount=Decimal("999999999.00"),
        maturity_amount=Decimal("1250000000.00"),
        interest_rate_percent=Decimal("8.00"),
        issue_date=date(2026, 1, 1),
        maturity_date=date(2029, 1, 1),
        tenure_months=36,
        joint_account_holder="Regional Officer, AICTE & Principal",
        status="SUBMITTED",
        trust_score=20,
        trust_score_breakdown={},
        created_at=now_ts,
        updated_at=now_ts,
    )

    # 5. Instrument with a DUPLICATE FDR number
    inst_duplicate = Instrument(
        id=uuid.uuid4(),
        institution_id=sample_institution.id,
        fdr_number="FDR-LARGE-003",  # Duplicate number matching #4
        bank_name="Punjab National Bank",
        bank_branch="Connaught Place, New Delhi",
        ifsc_code="PUNB0001234",
        principal_amount=Decimal("5000000.00"),
        maturity_amount=Decimal("6200000.00"),
        interest_rate_percent=Decimal("7.20"),
        issue_date=date(2026, 2, 1),
        maturity_date=date(2029, 2, 1),
        tenure_months=36,
        joint_account_holder="Regional Officer, AICTE & Principal",
        status="SUBMITTED",
        trust_score=20,
        trust_score_breakdown={},
        created_at=now_ts,
        updated_at=now_ts,
    )

    owner_session.add_all([inst_all_null, inst_partial_blank, inst_expired, inst_large_amount, inst_duplicate])
    owner_session.commit()

    test_instruments = [inst_all_null, inst_partial_blank, inst_expired, inst_large_amount, inst_duplicate]

    # Test 1: GET /institution renders clean HTML with neutral dashes and large amount
    auth_client_for(inst_user)
    res_inst = client.get("/institution")
    assert res_inst.status_code == 200, f"/institution returned {res_inst.status_code}"
    assert "{{" not in res_inst.text
    assert "{%" not in res_inst.text
    assert "—" in res_inst.text  # Neutral dash for null values
    assert "₹ 99,99,99,999.00" in res_inst.text  # Indian format for large amount

    # Test 2: GET /institution/instruments renders clean list
    res_list = client.get("/institution/instruments")
    assert res_list.status_code == 200, f"/institution/instruments returned {res_list.status_code}"
    assert "{{" not in res_list.text
    assert "{%" not in res_list.text
    assert "—" in res_list.text
    assert "₹ 99,99,99,999.00" in res_list.text

    # Test 3: Detail views for all 5 instruments across all roles
    for inst in test_instruments:
        # Institution detail view
        auth_client_for(inst_user)
        res_inst_detail = client.get(f"/institution/instruments/{inst.id}")
        assert res_inst_detail.status_code == 200, f"Institution detail for {inst.id} returned {res_inst_detail.status_code}"
        assert "{{" not in res_inst_detail.text
        assert "{%" not in res_inst_detail.text

        # Staff detail views across regulatory roles
        for staff_role_user in [scrutiny_user, approver_user, auditor_user, demo_admin_user]:
            auth_client_for(staff_role_user)
            res_staff_detail = client.get(f"/instruments/{inst.id}")
            assert res_staff_detail.status_code == 200, (
                f"Staff detail for {inst.id} with role {staff_role_user.role} returned {res_staff_detail.status_code}"
            )
            assert "{{" not in res_staff_detail.text
            assert "{%" not in res_staff_detail.text


def test_unhandled_exception_renders_styled_500_and_leaks_no_traceback():
    """Asserts that unexpected server errors render the styled 500 template and leak zero internal traceback details."""
    client_no_raise = TestClient(app, raise_server_exceptions=False)

    # Register a transient endpoint that simulates an unhandled crash if not already present
    route_paths = [getattr(r, "path", None) for r in app.routes]
    if "/_test_deliberate_crash" not in route_paths:
        @app.get("/_test_deliberate_crash")
        def _crash_route():
            raise TypeError("Simulated unexpected NoneType formatting exception")

    # 1. HTML request gets styled 500 error page without stack trace
    res_html = client_no_raise.get("/_test_deliberate_crash", headers={"Accept": "text/html"})
    assert res_html.status_code == 500
    assert "Internal Server Anomaly (500)" in res_html.text
    assert "Return to Safe Workspace" in res_html.text
    # Verify no leaks of internal details or stack traces
    assert "Traceback" not in res_html.text
    assert "Simulated unexpected NoneType formatting exception" not in res_html.text
    assert "TypeError" not in res_html.text
    assert "{{" not in res_html.text
    assert "{%" not in res_html.text

    # 2. JSON request gets clean JSON 500 without internal trace
    res_json = client_no_raise.get("/_test_deliberate_crash", headers={"Accept": "application/json"})
    assert res_json.status_code == 500
    data = res_json.json()
    assert data == {"detail": "Internal Server Error"}
    assert "Simulated unexpected NoneType" not in res_json.text
