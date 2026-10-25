import re
import uuid
from decimal import Decimal
from typing import Optional
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.institution import Institution
from app.models.instrument import Instrument
from app.services.clock_service import clock_service
from app.services.instrument_fsm import (
    InstrumentState,
    compute_status_counts,
    ALL_STATES,
)


def _extract_counter(html: str, element_id: str) -> Optional[int]:
    """Helper to extract numeric value from an HTML tag by id."""
    match = re.search(rf'id="{element_id}"[^>]*>\s*(\d+)\s*<', html)
    if match:
        return int(match.group(1))
    return None


def _create_instrument_in_state(
    db: Session,
    institution: Institution,
    fdr_number: str,
    status: str,
) -> Instrument:
    """Helper to persist an instrument directly in the specified FSM status."""
    now_ts = clock_service.now()
    today = clock_service.today()
    inst = Instrument(
        id=uuid.uuid4(),
        institution_id=institution.id,
        fdr_number=fdr_number,
        bank_name="State Bank of India",
        bank_branch="Main Branch",
        ifsc_code="SBIN0001234",
        principal_amount=Decimal("3500000.00"),
        maturity_amount=Decimal("4374007.32"),
        interest_rate_percent=Decimal("7.50"),
        tenure_months=36,
        issue_date=today,
        maturity_date=today.replace(year=today.year + 3),
        status=status,
        renewal_sequence=1,
        trust_score=20,
        created_at=now_ts,
        updated_at=now_ts,
    )
    db.add(inst)
    db.commit()
    db.refresh(inst)
    return inst


def test_compute_status_counts_dictionary_structure(app_session: Session, sample_institution: Institution):
    """compute_status_counts returns all 11 exact FSM states plus TOTAL and OTHER."""
    counts = compute_status_counts(app_session, institution_id=sample_institution.id)

    # 1. All 11 FSM states must be present as keys
    for state in InstrumentState:
        assert state.value in counts
        assert isinstance(counts[state.value], int)
        assert counts[state.value] >= 0

    # 2. TOTAL and OTHER keys present
    assert "TOTAL" in counts
    assert "OTHER" in counts

    # 3. Math invariants
    assert counts["TOTAL"] == sum(counts[s.value] for s in InstrumentState)
    assert counts["TOTAL"] == (
        counts[InstrumentState.SUBMITTED.value]
        + counts[InstrumentState.ACTIVE.value]
        + counts[InstrumentState.DISCREPANCY_FLAGGED.value]
        + counts["OTHER"]
    )


def test_dashboard_status_counters_multi_institution_and_staff_scopes(
    client: TestClient,
    make_user,
    sample_institution: Institution,
    sample_institution_b: Institution,
    auth_client_for,
    app_session: Session,
):
    """Instruments in several states for two institutions:
    - Verifies FLAGGED card shows exact DISCREPANCY_FLAGGED count (fixing the 0 bug).
    - Verifies 'In progress / other' card is added when other states exist.
    - Verifies cards add up to Total.
    - Verifies institution isolation (neither institution sees the other's counts).
    - Verifies staff roles see unscoped aggregates across all institutions.
    """
    # -------------------------------------------------------------------------
    # Setup Institution A instruments:
    # 2 SUBMITTED, 1 ACTIVE, 2 DISCREPANCY_FLAGGED, 1 UNDER_SCRUTINY, 1 VERIFIED, 1 MATURED
    # Total = 8 (Submitted: 2, Active: 1, Flagged: 2, Other: 3)
    # -------------------------------------------------------------------------
    _create_instrument_in_state(app_session, sample_institution, "INSTA-SUB-1", InstrumentState.SUBMITTED.value)
    _create_instrument_in_state(app_session, sample_institution, "INSTA-SUB-2", InstrumentState.SUBMITTED.value)
    _create_instrument_in_state(app_session, sample_institution, "INSTA-ACT-1", InstrumentState.ACTIVE.value)
    _create_instrument_in_state(app_session, sample_institution, "INSTA-FLG-1", InstrumentState.DISCREPANCY_FLAGGED.value)
    _create_instrument_in_state(app_session, sample_institution, "INSTA-FLG-2", InstrumentState.DISCREPANCY_FLAGGED.value)
    _create_instrument_in_state(app_session, sample_institution, "INSTA-SCR-1", InstrumentState.UNDER_SCRUTINY.value)
    _create_instrument_in_state(app_session, sample_institution, "INSTA-VER-1", InstrumentState.VERIFIED.value)
    _create_instrument_in_state(app_session, sample_institution, "INSTA-MAT-1", InstrumentState.MATURED.value)

    # -------------------------------------------------------------------------
    # Setup Institution B instruments:
    # 1 SUBMITTED, 3 ACTIVE, 1 DISCREPANCY_FLAGGED, 1 RELEASED
    # Total = 6 (Submitted: 1, Active: 3, Flagged: 1, Other: 1)
    # -------------------------------------------------------------------------
    _create_instrument_in_state(app_session, sample_institution_b, "INSTB-SUB-1", InstrumentState.SUBMITTED.value)
    _create_instrument_in_state(app_session, sample_institution_b, "INSTB-ACT-1", InstrumentState.ACTIVE.value)
    _create_instrument_in_state(app_session, sample_institution_b, "INSTB-ACT-2", InstrumentState.ACTIVE.value)
    _create_instrument_in_state(app_session, sample_institution_b, "INSTB-ACT-3", InstrumentState.ACTIVE.value)
    _create_instrument_in_state(app_session, sample_institution_b, "INSTB-FLG-1", InstrumentState.DISCREPANCY_FLAGGED.value)
    _create_instrument_in_state(app_session, sample_institution_b, "INSTB-REL-1", InstrumentState.RELEASED.value)

    # =========================================================================
    # 1. Test Institution A
    # =========================================================================
    user_a = make_user("inst.admin.a", "INSTITUTION_ADMIN", institution_id=sample_institution.id)
    auth_client_for(user_a)

    res_a = client.get("/institution")
    assert res_a.status_code == 200

    cnt_total_a = _extract_counter(res_a.text, "counter-total")
    cnt_sub_a = _extract_counter(res_a.text, "counter-submitted")
    cnt_act_a = _extract_counter(res_a.text, "counter-active")
    cnt_flg_a = _extract_counter(res_a.text, "counter-flagged")
    cnt_oth_a = _extract_counter(res_a.text, "counter-other")

    assert cnt_total_a == 8
    assert cnt_sub_a == 2
    assert cnt_act_a == 1
    assert cnt_flg_a == 2  # CRITICAL: Was 0 prior to bugfix!
    assert cnt_oth_a == 3  # (UNDER_SCRUTINY + VERIFIED + MATURED)

    # Card set sums to Total
    assert cnt_total_a == cnt_sub_a + cnt_act_a + cnt_flg_a + cnt_oth_a

    # Institution A does NOT see Institution B's total or active counts
    assert "INSTB-" not in res_a.text

    # =========================================================================
    # 2. Test Institution B
    # =========================================================================
    user_b = make_user("inst.admin.b", "INSTITUTION_ADMIN", institution_id=sample_institution_b.id)
    auth_client_for(user_b)

    res_b = client.get("/institution")
    assert res_b.status_code == 200

    cnt_total_b = _extract_counter(res_b.text, "counter-total")
    cnt_sub_b = _extract_counter(res_b.text, "counter-submitted")
    cnt_act_b = _extract_counter(res_b.text, "counter-active")
    cnt_flg_b = _extract_counter(res_b.text, "counter-flagged")
    cnt_oth_b = _extract_counter(res_b.text, "counter-other")

    assert cnt_total_b == 6
    assert cnt_sub_b == 1
    assert cnt_act_b == 3
    assert cnt_flg_b == 1
    assert cnt_oth_b == 1  # (RELEASED)

    # Card set sums to Total
    assert cnt_total_b == cnt_sub_b + cnt_act_b + cnt_flg_b + cnt_oth_b

    # Institution B does NOT see Institution A's instruments
    assert "INSTA-" not in res_b.text

    # =========================================================================
    # 3. Test Scrutiny Officer Workstation (All Institutions, Unscoped)
    # Total = 8 + 6 = 14
    # Under Scrutiny = 1, Flagged = 2 + 1 = 3, Submitted = 2 + 1 = 3
    # =========================================================================
    scrutiny_user = make_user("scrutiny.counters", "SCRUTINY_OFFICER")
    auth_client_for(scrutiny_user)

    res_scr = client.get("/scrutiny")
    assert res_scr.status_code == 200

    assert _extract_counter(res_scr.text, "counter-total") == 14
    assert _extract_counter(res_scr.text, "counter-under-scrutiny") == 1
    assert _extract_counter(res_scr.text, "counter-flagged") == 3
    assert _extract_counter(res_scr.text, "counter-submitted") == 3

    # =========================================================================
    # 4. Test Competent Authority Workstation (All Institutions, Unscoped)
    # Total = 14, Release Requested = 0, Verified = 1, Released = 1
    # =========================================================================
    approver_user = make_user("approver.counters", "APPROVER_AUTHORITY")
    auth_client_for(approver_user)

    res_app = client.get("/approver")
    assert res_app.status_code == 200

    assert _extract_counter(res_app.text, "counter-total") == 14
    assert _extract_counter(res_app.text, "counter-release-requested") == 0
    assert _extract_counter(res_app.text, "counter-verified") == 1
    assert _extract_counter(res_app.text, "counter-released") == 1

    # =========================================================================
    # 5. Test Auditor Console (All Institutions, Unscoped)
    # Total = 14, Active = 1 + 3 = 4, Flagged = 3, Released = 1
    # =========================================================================
    auditor_user = make_user("auditor.counters", "AUDITOR")
    auth_client_for(auditor_user)

    res_aud = client.get("/auditor")
    assert res_aud.status_code == 200

    assert _extract_counter(res_aud.text, "counter-total") == 14
    assert _extract_counter(res_aud.text, "counter-active") == 4
    assert _extract_counter(res_aud.text, "counter-flagged") == 3
    assert _extract_counter(res_aud.text, "counter-released") == 1


def test_institution_home_card_set_when_no_other_states(
    client: TestClient,
    make_user,
    auth_client_for,
    app_session: Session,
):
    """When an institution only has SUBMITTED, ACTIVE, and DISCREPANCY_FLAGGED instruments,
    the 'In progress / other' card is omitted and the 4 baseline cards exactly equal Total.
    """
    inst_c = Institution(
        id=uuid.uuid4(),
        aicte_permanent_id="1-COUNTER-TEST-C",
        name="Counter Test College C",
        address="Sector 1, Academic City",
        contact_email="principal@college-c.edu",
        contact_phone="9876543210",
        state="Maharashtra",
        region="Western",
        institution_type="Engineering & Technology",
        created_at=clock_service.now(),
        updated_at=clock_service.now(),
    )
    app_session.add(inst_c)
    app_session.commit()

    _create_instrument_in_state(app_session, inst_c, "INSTC-SUB-1", InstrumentState.SUBMITTED.value)
    _create_instrument_in_state(app_session, inst_c, "INSTC-ACT-1", InstrumentState.ACTIVE.value)
    _create_instrument_in_state(app_session, inst_c, "INSTC-FLG-1", InstrumentState.DISCREPANCY_FLAGGED.value)

    user_c = make_user("inst.admin.c", "INSTITUTION_ADMIN", institution_id=inst_c.id)
    auth_client_for(user_c)

    res_c = client.get("/institution")
    assert res_c.status_code == 200

    cnt_total = _extract_counter(res_c.text, "counter-total")
    cnt_sub = _extract_counter(res_c.text, "counter-submitted")
    cnt_act = _extract_counter(res_c.text, "counter-active")
    cnt_flg = _extract_counter(res_c.text, "counter-flagged")

    # 'In progress / other' card should NOT exist when other_count == 0
    assert _extract_counter(res_c.text, "counter-other") is None

    assert cnt_total == 3
    assert cnt_sub == 1
    assert cnt_act == 1
    assert cnt_flg == 1
    assert cnt_total == cnt_sub + cnt_act + cnt_flg


def test_list_status_filter_query_param(
    client: TestClient,
    make_user,
    sample_institution: Institution,
    auth_client_for,
    app_session: Session,
):
    """GET /institution/instruments?status=... filters list strictly by FSM status."""
    _create_instrument_in_state(app_session, sample_institution, "FILTER-SUB-1", InstrumentState.SUBMITTED.value)
    _create_instrument_in_state(app_session, sample_institution, "FILTER-FLG-1", InstrumentState.DISCREPANCY_FLAGGED.value)
    _create_instrument_in_state(app_session, sample_institution, "FILTER-ACT-1", InstrumentState.ACTIVE.value)

    user = make_user("inst.admin.filter", "INSTITUTION_ADMIN", institution_id=sample_institution.id)
    auth_client_for(user)

    # 1. Filter by DISCREPANCY_FLAGGED
    res_flg = client.get("/institution/instruments?status=DISCREPANCY_FLAGGED")
    assert res_flg.status_code == 200
    assert "FILTER-FLG-1" in res_flg.text
    assert "FILTER-SUB-1" not in res_flg.text
    assert "FILTER-ACT-1" not in res_flg.text

    # 2. Filter by ACTIVE
    res_act = client.get("/institution/instruments?status=ACTIVE")
    assert res_act.status_code == 200
    assert "FILTER-ACT-1" in res_act.text
    assert "FILTER-FLG-1" not in res_act.text
    assert "FILTER-SUB-1" not in res_act.text

    # 3. No filter -> all 3 appear
    res_all = client.get("/institution/instruments")
    assert res_all.status_code == 200
    assert "FILTER-SUB-1" in res_all.text
    assert "FILTER-FLG-1" in res_all.text
    assert "FILTER-ACT-1" in res_all.text
