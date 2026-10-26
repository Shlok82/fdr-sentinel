"""tests/test_executive_dashboard.py
Automated Verification Suite for Stage 9 Executive Dashboard & Maturity Wall (SPEC §5.1, §9.1, §9.2).
"""
import uuid
from decimal import Decimal
from datetime import date, timedelta
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.templates import format_inr_compact
from app.models.institution import Institution
from app.models.instrument import Instrument
from app.models.user import User
from app.services.clock_service import clock_service
from tests.conftest import TestOwnerSession


def test_format_inr_compact_unit():
    """Validates compact Indian Rupee formatting across crore, lakh, thousands, zero, negative, and null."""
    assert format_inr_compact(Decimal("124000000.00")) == "₹ 12.40 Cr"
    assert format_inr_compact(Decimal("3500000.00")) == "₹ 35.00 L"
    assert format_inr_compact(Decimal("50000.00")) == "₹ 50,000"
    assert format_inr_compact(Decimal("0")) == "₹ 0"
    assert format_inr_compact(Decimal("-3500000.00")) == "-₹ 35.00 L"
    assert format_inr_compact(None) == "—"
    assert format_inr_compact("") == "—"
    assert format_inr_compact("invalid") == "—"


def test_dashboard_rbac_access_control(client, make_user, auth_client_for):
    """SPEC §6.1, §6.2, §9.2: Enforces role-based access control on /dashboard."""
    # 1. Anonymous visitor -> 302 Redirect to /login
    anon_resp = client.get("/dashboard", headers={"accept": "text/html"}, follow_redirects=False)
    assert anon_resp.status_code in (302, 303)
    assert "/login" in anon_resp.headers["location"]
    assert "next=%2Fdashboard" in anon_resp.headers["location"] or "next=/dashboard" in anon_resp.headers["location"]

    # 2. INSTITUTION_ADMIN -> 403 Forbidden
    inst_user = make_user("inst_admin_dash", "INSTITUTION_ADMIN")
    authed_inst, _ = auth_client_for(inst_user)
    resp_inst = authed_inst.get("/dashboard", headers={"accept": "text/html"})
    assert resp_inst.status_code == 403

    # 3. SCRUTINY_OFFICER -> 200 OK
    officer_user = make_user("officer_dash", "SCRUTINY_OFFICER")
    authed_off, _ = auth_client_for(officer_user)
    resp_off = authed_off.get("/dashboard")
    assert resp_off.status_code == 200
    assert "Executive Dashboard &amp; Maturity Wall" in resp_off.text or "Executive Dashboard & Maturity Wall" in resp_off.text

    # 4. APPROVER_AUTHORITY -> 200 OK
    appr_user = make_user("approver_dash", "APPROVER_AUTHORITY")
    authed_appr, _ = auth_client_for(appr_user)
    resp_appr = authed_appr.get("/dashboard")
    assert resp_appr.status_code == 200

    # 5. AUDITOR -> 200 OK
    aud_user = make_user("auditor_dash", "AUDITOR")
    authed_aud, _ = auth_client_for(aud_user)
    resp_aud = authed_aud.get("/dashboard")
    assert resp_aud.status_code == 200


def test_dashboard_read_only_zero_audit_writes(make_user, auth_client_for):
    """Guarantees that viewing /dashboard generates zero DB mutations and zero audit log entries."""
    officer_user = make_user("officer_readonly", "SCRUTINY_OFFICER")
    authed_off, _ = auth_client_for(officer_user)

    session: Session = TestOwnerSession()
    try:
        initial_audit_count = session.execute(text("SELECT count(*) FROM audit_log")).scalar()
        
        # Load dashboard multiple times
        resp1 = authed_off.get("/dashboard")
        assert resp1.status_code == 200
        resp2 = authed_off.get("/dashboard")
        assert resp2.status_code == 200

        final_audit_count = session.execute(text("SELECT count(*) FROM audit_log")).scalar()
        assert final_audit_count == initial_audit_count, "Executive dashboard must never emit audit log entries on read"
    finally:
        session.close()


def test_dashboard_metrics_and_canvas_elements(make_user, auth_client_for):
    """Validates presence of KPI cards, Chart.js canvases, data tables, and Time Machine integration."""
    officer_user = make_user("officer_metrics", "SCRUTINY_OFFICER")
    authed_off, _ = auth_client_for(officer_user)

    # Seed a couple of test instruments with known values
    session: Session = TestOwnerSession()
    try:
        now_ts = clock_service.now()
        today = clock_service.today()

        inst_org = Institution(
            id=uuid.uuid4(),
            aicte_permanent_id="1-TEST-DASH-1",
            name="Dashboard Test College",
            region="Western",
            state="Maharashtra",
            address="Testing Road",
            contact_email="test@dash.edu",
            contact_phone="123456",
            institution_type="Engineering & Technology",
            created_at=now_ts,
            updated_at=now_ts,
        )
        session.add(inst_org)
        session.flush()

        # 1 ACTIVE instrument
        fdr1 = Instrument(
            id=uuid.uuid4(),
            institution_id=inst_org.id,
            fdr_number="TEST-DASH-FDR-1",
            bank_name="State Bank of India",
            principal_amount=Decimal("3500000.00"),
            maturity_amount=Decimal("3750000.00"),
            interest_rate_percent=Decimal("7.00"),
            issue_date=today - timedelta(days=100),
            maturity_date=today + timedelta(days=200),
            tenure_months=12,
            joint_account_holder="AICTE & Principal",
            status="ACTIVE",
            trust_score=75,
            created_at=now_ts,
            updated_at=now_ts,
        )
        # 1 EXPIRING instrument
        fdr2 = Instrument(
            id=uuid.uuid4(),
            institution_id=inst_org.id,
            fdr_number="TEST-DASH-FDR-2",
            bank_name="Canara Bank",
            principal_amount=Decimal("1500000.00"),
            maturity_amount=Decimal("1600000.00"),
            interest_rate_percent=Decimal("6.50"),
            issue_date=today - timedelta(days=340),
            maturity_date=today + timedelta(days=25),
            tenure_months=12,
            joint_account_holder="AICTE & Principal",
            status="EXPIRING",
            trust_score=35,
            created_at=now_ts,
            updated_at=now_ts,
        )
        session.add(fdr1)
        session.add(fdr2)
        session.commit()

        resp = authed_off.get("/dashboard")
        assert resp.status_code == 200
        html = resp.text

        # 1. Vendored Chart.js asset included
        assert "/static/vendor/chart.umd.min.js" in html

        # 2. Both Chart.js canvases present with accessibility roles
        assert 'id="maturity-wall-chart"' in html
        assert 'id="regional-exposure-chart"' in html
        assert 'role="img"' in html

        # 3. Tabular accessible fallback tables present
        assert 'id="table-maturity-wall"' in html
        assert 'id="table-regional-exposure"' in html
        assert 'id="table-attention-queue"' in html

        # 4. KPI values rendered
        # In custody: 35L + 15L = 50L (₹ 50.00 L)
        assert "₹ 50.00 L" in html
        # Deposit at risk: 15L (₹ 15.00 L)
        assert "₹ 15.00 L" in html

        # 5. Attention Queue includes the low-trust expiring instrument
        assert "TEST-DASH-FDR-2" in html

        # 6. Time Machine synchronizer listener present
        assert "clockUpdated" in html
        assert "window.location.reload()" in html

    finally:
        session.close()
