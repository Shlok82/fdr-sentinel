import io
import re
import pytest
from decimal import Decimal
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
import pypdf

from app.core.config import settings
from app.models.instrument import Instrument
from app.models.user import User
from app.services.clock_service import clock_service
from app.services.demo_scenarios import compute_quarterly_compound_maturity, get_scenario_data
from app.services.demo_documents import generate_sample_fdr_pdf


def test_demo_mode_false_endpoints_and_panel_absent(
    client: TestClient, make_user, sample_institution, auth_client_for, monkeypatch
):
    """When DEMO_MODE is False, demo endpoints return 404 and form does not render the demo panel."""
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    inst_user = make_user("inst.nodemo", "INSTITUTION_ADMIN", institution_id=sample_institution.id)
    auth_client_for(inst_user)

    # 1. API endpoints return 404
    res_sc = client.get("/demo/fdr-scenarios/clean")
    assert res_sc.status_code == 404
    assert "disabled" in res_sc.json()["detail"].lower()

    res_pdf = client.get("/demo/fdr-sample/clean.pdf")
    assert res_pdf.status_code == 404
    assert "disabled" in res_pdf.json()["detail"].lower()

    # 2. Form page does not render demo panel
    res_form = client.get("/institution/instruments/new")
    assert res_form.status_code == 200
    assert "demo-scenarios-panel" not in res_form.text
    assert "AICTE Demo &amp; Evaluation Scenarios" not in res_form.text


def test_demo_endpoints_rbac_and_auth(
    client: TestClient, make_user, sample_institution, auth_client_for, monkeypatch
):
    """Demo endpoints require authenticated INSTITUTION_ADMIN."""
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    inst_user = make_user("inst.demorbac", "INSTITUTION_ADMIN", institution_id=sample_institution.id)
    scrutiny_user = make_user("staff.demorbac", "SCRUTINY_OFFICER")

    # 1. Unauthenticated gets 401 or redirect to login
    client.cookies.clear()
    res_unauth = client.get("/demo/fdr-scenarios/clean", follow_redirects=False)
    assert res_unauth.status_code in (401, 302, 303, 307)

    res_unauth_pdf = client.get("/demo/fdr-sample/clean.pdf", follow_redirects=False)
    assert res_unauth_pdf.status_code in (401, 302, 303, 307)

    # 2. Non-institution user gets 403 Forbidden
    auth_client_for(scrutiny_user)
    res_scrutiny = client.get("/demo/fdr-scenarios/clean")
    assert res_scrutiny.status_code == 403

    res_scrutiny_pdf = client.get("/demo/fdr-sample/clean.pdf")
    assert res_scrutiny_pdf.status_code == 403

    # 3. Institution Admin gets 200 OK
    auth_client_for(inst_user)
    res_ok = client.get("/demo/fdr-scenarios/clean")
    assert res_ok.status_code == 200

    res_pdf_ok = client.get("/demo/fdr-sample/clean.pdf")
    assert res_pdf_ok.status_code == 200


def test_all_seven_scenarios_json(
    client: TestClient, make_user, sample_institution, auth_client_for, monkeypatch
):
    """All 7 demo scenarios return expected JSON structures."""
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    inst_user = make_user("inst.scenarios", "INSTITUTION_ADMIN", institution_id=sample_institution.id)
    auth_client_for(inst_user)

    expected_keys = [
        "clean",
        "inverted_dates",
        "duplicate_number",
        "expired",
        "low_principal",
        "missing_mandatory",
        "maturity_mismatch",
    ]

    for key in expected_keys:
        res = client.get(f"/demo/fdr-scenarios/{key}")
        assert res.status_code == 200, f"Scenario {key} returned {res.status_code}"
        data = res.json()
        assert "key" in data and data["key"] == key
        assert ("label" in data or "title" in data)
        assert "description" in data
        assert "fields" in data
        fields = data["fields"]
        assert isinstance(fields, dict)

    # Unknown key returns 404
    res_bad = client.get("/demo/fdr-scenarios/non_existent_scenario")
    assert res_bad.status_code == 404


def test_clean_scenario_quarterly_compound_math(
    client: TestClient, make_user, sample_institution, auth_client_for, monkeypatch
):
    """Clean scenario maturity amount matches exact quarterly compound interest formula."""
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    inst_user = make_user("inst.math", "INSTITUTION_ADMIN", institution_id=sample_institution.id)
    auth_client_for(inst_user)

    res = client.get("/demo/fdr-scenarios/clean")
    assert res.status_code == 200
    fields = res.json()["fields"]

    principal = Decimal(fields["principal_amount"])
    rate = Decimal(fields.get("interest_rate_percent") or fields.get("interest_rate"))
    term_months = int(fields["tenure_months"])

    expected_maturity = compute_quarterly_compound_maturity(principal, rate, term_months)
    assert fields["maturity_amount"] == str(expected_maturity)
    # For P=3500000, r=7.50, t=3 years (36 mos), maturity should be 4374007.32
    assert fields["maturity_amount"] == "4374007.32"


def test_scenario_dates_advance_with_clock_service(
    client: TestClient, make_user, sample_institution, auth_client_for, app_session: Session, monkeypatch
):
    """Scenario dates are derived purely from ClockService.today() and update when the clock advances."""
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    inst_user = make_user("inst.clockdyn", "INSTITUTION_ADMIN", institution_id=sample_institution.id)
    auth_client_for(inst_user)

    clock_service.reset(app_session)
    app_session.commit()
    try:
        res_before = client.get("/demo/fdr-scenarios/clean").json()
        deposit_date_before = res_before["fields"]["issue_date"]

        # Advance application clock by 60 days
        clock_service.advance(app_session, days=60)
        app_session.commit()
        res_after = client.get("/demo/fdr-scenarios/clean").json()
        deposit_date_after = res_after["fields"]["issue_date"]

        assert deposit_date_before != deposit_date_after
        # Both dates must match their corresponding today() calculation
        today_after = clock_service.today()
        assert deposit_date_after == today_after.isoformat()
    finally:
        clock_service.reset(app_session)
        app_session.commit()


def test_sample_pdf_generation_and_text_extraction(
    client: TestClient, make_user, sample_institution, auth_client_for, monkeypatch
):
    """Generated sample PDFs are valid, lightweight (<25 KB), and contain extractable text with specimen watermark."""
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    inst_user = make_user("inst.pdfgen", "INSTITUTION_ADMIN", institution_id=sample_institution.id)
    auth_client_for(inst_user)

    for key in ["clean", "inverted_dates", "expired", "maturity_mismatch"]:
        res = client.get(f"/demo/fdr-sample/{key}.pdf")
        assert res.status_code == 200
        assert res.headers["Content-Type"] == "application/pdf"
        assert f'filename="sample_{key}.pdf"' in res.headers["Content-Disposition"]

        content = res.content
        assert content.startswith(b"%PDF-")
        assert len(content) < 25000, f"PDF for {key} is too large ({len(content)} bytes)"

        # Inspect extractable text
        reader = pypdf.PdfReader(io.BytesIO(content))
        text = "".join(page.extract_text() or "" for page in reader.pages)
        assert "SPECIMEN - DEMO ONLY, NOT A REAL INSTRUMENT" in text
        assert "FIXED DEPOSIT RECEIPT" in text
        assert "AICTE" in text


def test_duplicate_scenario_dynamic_fdr_lookup(
    client: TestClient, make_user, sample_institution, auth_client_for, app_session: Session, monkeypatch
):
    """Duplicate scenario adopts the FDR number of the latest submitted instrument or provides fallback."""
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    inst_user = make_user("inst.dupcheck", "INSTITUTION_ADMIN", institution_id=sample_institution.id)
    _, csrf = auth_client_for(inst_user)

    # 1. Before submitting any instrument, duplicate scenario returns default/fallback
    res_empty = client.get("/demo/fdr-scenarios/duplicate_number").json()
    assert "DEMO-FDR-2026-001" in res_empty["fields"]["fdr_number"]

    # 2. Submit an actual instrument
    sample_pdf = generate_sample_fdr_pdf("clean")
    res_sub = client.post(
        "/institution/instruments/new",
        data={
            "fdr_number": "SBI-PUNE-SPECIAL-789",
            "bank_name": "State Bank of India",
            "bank_branch": "Camp Branch, Pune",
            "ifsc_code": "SBIN0001234",
            "principal_amount": "3500000.00",
            "maturity_amount": "4374007.32",
            "interest_rate_percent": "7.50",
            "tenure_months": "36",
            "issue_date": "2025-01-15",
            "maturity_date": "2028-01-15",
            "joint_account_holder": "Regional Officer, AICTE & Principal, National Model Institute of Technology",
            "csrf_token": csrf,
        },
        files={"scan_file": ("sbi_specimen.pdf", sample_pdf, "application/pdf")},
        follow_redirects=False,
    )
    assert res_sub.status_code == 303

    # 3. Query duplicate scenario again: it should target the newly submitted instrument's FDR number!
    res_after = client.get("/demo/fdr-scenarios/duplicate_number").json()
    assert res_after["fields"]["fdr_number"] == "SBI-PUNE-SPECIAL-789"
    assert res_after["fields"]["bank_name"] == "State Bank of India"
    assert "Targets existing deposit SBI-PUNE-SPECIAL-789" in res_after["note"]


def test_submit_clean_scenario_from_demo_payload(
    client: TestClient, make_user, sample_institution, auth_client_for, monkeypatch
):
    """Submitting a full clean demo scenario with sample PDF successfully registers instrument in SUBMITTED state."""
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    inst_user = make_user("inst.fullsub", "INSTITUTION_ADMIN", institution_id=sample_institution.id)
    _, csrf = auth_client_for(inst_user)

    # Fetch clean scenario
    scenario_res = client.get("/demo/fdr-scenarios/clean").json()
    fields = scenario_res["fields"].copy()
    fields["csrf_token"] = csrf

    # Fetch clean sample PDF
    pdf_res = client.get("/demo/fdr-sample/clean.pdf")
    assert pdf_res.status_code == 200

    # Submit
    res = client.post(
        "/institution/instruments/new",
        data=fields,
        files={"scan_file": ("clean_fdr.pdf", pdf_res.content, "application/pdf")},
        follow_redirects=False,
    )
    assert res.status_code == 303
    inst_id = res.headers["Location"].split("/")[-1]

    # Verify detail view
    detail_res = client.get(f"/institution/instruments/{inst_id}")
    assert detail_res.status_code == 200
    assert fields["fdr_number"] in detail_res.text
    assert "SUBMITTED" in detail_res.text


def test_form_initial_upload_ux_state(
    client: TestClient, make_user, sample_institution, auth_client_for, monkeypatch
):
    """The new submission form loads with clean initial UX state without premature validation errors."""
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    inst_user = make_user("inst.uxcheck", "INSTITUTION_ADMIN", institution_id=sample_institution.id)
    auth_client_for(inst_user)

    res = client.get("/institution/instruments/new")
    assert res.status_code == 200
    html = res.text

    # Upload dropzone exists with max size attribute matching settings
    assert f'data-max-mb="{settings.MAX_UPLOAD_MB}"' in html

    # Selected-file card is initially hidden
    assert 'id="selected-file-display"' in html
    match_card = re.search(r'id="selected-file-display"[^>]*class="([^"]*)"', html)
    assert match_card and "hidden" in match_card.group(1)

    # Client error box is initially hidden
    assert 'id="client-file-error"' in html
    match_err = re.search(r'id="client-file-error"[^>]*class="([^"]*)"', html)
    assert match_err and "hidden" in match_err.group(1)

    # Accessible announcer is present
    assert 'id="file-status-announcer"' in html
    assert 'aria-live="polite"' in html

    # Standalone fdr_form.js script is loaded
    assert '<script src="/static/js/fdr_form.js" defer></script>' in html
