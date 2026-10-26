import re
import uuid
from decimal import Decimal
from typing import Dict, List, Optional
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.institution import Institution
from app.models.instrument import Instrument
from app.models.audit import AuditLog
from app.services.clock_service import clock_service
from app.services.instrument_fsm import (
    Actor,
    InstrumentState,
    SYSTEM_ACTOR,
    transition,
    compute_status_counts,
)


def _extract_counter(html: str, element_id: str) -> Optional[int]:
    """Helper to extract numeric value from an HTML tag by id."""
    match = re.search(rf'id="{element_id}"[^>]*>\s*(\d+)\s*<', html)
    if match:
        return int(match.group(1))
    return None


def _extract_table_row_count(html: str) -> int:
    """Helper to extract number of data rows in the table tbody."""
    tbody_match = re.search(r'<tbody[^>]*>(.*?)</tbody>', html, re.DOTALL | re.IGNORECASE)
    if not tbody_match:
        return 0
    tbody_content = tbody_match.group(1)
    return len(re.findall(r'<tr[^>]*>', tbody_content, re.IGNORECASE))


def _create_initial_instrument(
    db: Session,
    institution: Institution,
    fdr_number: Optional[str] = None,
    principal_amount: Optional[Decimal] = Decimal("2500000.00"),
    maturity_date_val=None,
) -> Instrument:
    """Helper to persist a submission in initial SUBMITTED state."""
    now_ts = clock_service.now()
    today = clock_service.today()
    if maturity_date_val is None and principal_amount is not None:
        maturity_date_val = today.replace(year=today.year + 3)

    inst = Instrument(
        id=uuid.uuid4(),
        institution_id=institution.id,
        fdr_number=fdr_number,
        bank_name="State Bank of India" if fdr_number else None,
        bank_branch="Main Branch" if fdr_number else None,
        ifsc_code="SBIN0001234" if fdr_number else None,
        principal_amount=principal_amount,
        maturity_amount=Decimal("3125000.00") if principal_amount else None,
        interest_rate_percent=Decimal("7.50") if principal_amount else None,
        tenure_months=36 if principal_amount else None,
        issue_date=today,
        maturity_date=maturity_date_val,
        status=InstrumentState.SUBMITTED.value,
        renewal_sequence=1,
        trust_score=20,
        created_at=now_ts,
        updated_at=now_ts,
    )
    db.add(inst)
    db.commit()
    db.refresh(inst)
    return inst


@pytest.fixture
def eleven_state_fixture(app_session: Session, sample_institution: Institution, make_user):
    """Sets up an institution with exactly 1 instrument in each of the 11 FSM states,
    fully transitioned through the FSM/service layer without raw status writes.
    Includes instruments with NULL business fields for null-safety testing.
    """
    inst_user = make_user("inst.fsm.user", "INSTITUTION_ADMIN", institution_id=sample_institution.id)
    scrutiny_user = make_user("scrutiny.fsm.user", "SCRUTINY_OFFICER")
    approver_user = make_user("approver.fsm.user", "APPROVER_AUTHORITY")

    inst_actor = Actor(user_id=inst_user.id, role="INSTITUTION_ADMIN", institution_id=sample_institution.id)
    scrutiny_actor = Actor(user_id=scrutiny_user.id, role="SCRUTINY_OFFICER")
    approver_actor = Actor(user_id=approver_user.id, role="APPROVER_AUTHORITY")

    instruments = {}

    # 1. SUBMITTED (with NULL business fields for null safety)
    i1 = _create_initial_instrument(app_session, sample_institution, fdr_number=None, principal_amount=None, maturity_date_val=None)
    instruments[InstrumentState.SUBMITTED.value] = i1

    # 2. UNDER_SCRUTINY
    i2 = _create_initial_instrument(app_session, sample_institution, fdr_number="FDR-STATE-SCRUTINY-002")
    transition(app_session, i2.id, InstrumentState.UNDER_SCRUTINY.value, scrutiny_actor, "Starting initial scrutiny")
    instruments[InstrumentState.UNDER_SCRUTINY.value] = i2

    # 3. DISCREPANCY_FLAGGED
    i3 = _create_initial_instrument(app_session, sample_institution, fdr_number="FDR-STATE-FLAGGED-003")
    transition(app_session, i3.id, InstrumentState.UNDER_SCRUTINY.value, scrutiny_actor, "Reviewing deposit")
    transition(app_session, i3.id, InstrumentState.DISCREPANCY_FLAGGED.value, scrutiny_actor, "Flagging verification discrepancy")
    instruments[InstrumentState.DISCREPANCY_FLAGGED.value] = i3

    # 4. VERIFIED
    i4 = _create_initial_instrument(app_session, sample_institution, fdr_number="FDR-STATE-VERIFIED-004")
    transition(app_session, i4.id, InstrumentState.UNDER_SCRUTINY.value, scrutiny_actor, "Reviewing deposit")
    transition(app_session, i4.id, InstrumentState.VERIFIED.value, scrutiny_actor, "Bank confirmation verified successfully")
    instruments[InstrumentState.VERIFIED.value] = i4

    # 5. ACTIVE
    i5 = _create_initial_instrument(app_session, sample_institution, fdr_number="FDR-STATE-ACTIVE-005")
    transition(app_session, i5.id, InstrumentState.UNDER_SCRUTINY.value, scrutiny_actor, "Reviewing deposit")
    transition(app_session, i5.id, InstrumentState.VERIFIED.value, scrutiny_actor, "Bank confirmation verified successfully")
    transition(app_session, i5.id, InstrumentState.ACTIVE.value, approver_actor, "Activating verified deposit receipt")
    instruments[InstrumentState.ACTIVE.value] = i5

    # 6. EXPIRING
    i6 = _create_initial_instrument(app_session, sample_institution, fdr_number="FDR-STATE-EXPIRING-006")
    transition(app_session, i6.id, InstrumentState.UNDER_SCRUTINY.value, scrutiny_actor, "Reviewing deposit")
    transition(app_session, i6.id, InstrumentState.VERIFIED.value, scrutiny_actor, "Bank confirmation verified successfully")
    transition(app_session, i6.id, InstrumentState.ACTIVE.value, approver_actor, "Activating verified deposit receipt")
    transition(app_session, i6.id, InstrumentState.EXPIRING.value, SYSTEM_ACTOR, "Within 90-day expiry threshold")
    instruments[InstrumentState.EXPIRING.value] = i6

    # 7. MATURED
    i7 = _create_initial_instrument(app_session, sample_institution, fdr_number="FDR-STATE-MATURED-007")
    transition(app_session, i7.id, InstrumentState.UNDER_SCRUTINY.value, scrutiny_actor, "Reviewing deposit")
    transition(app_session, i7.id, InstrumentState.VERIFIED.value, scrutiny_actor, "Bank confirmation verified successfully")
    transition(app_session, i7.id, InstrumentState.ACTIVE.value, approver_actor, "Activating verified deposit receipt")
    transition(app_session, i7.id, InstrumentState.EXPIRING.value, SYSTEM_ACTOR, "Within 90-day expiry threshold")
    transition(app_session, i7.id, InstrumentState.MATURED.value, SYSTEM_ACTOR, "Past maturity date threshold")
    instruments[InstrumentState.MATURED.value] = i7

    # 8. RENEWED
    i8 = _create_initial_instrument(app_session, sample_institution, fdr_number="FDR-STATE-RENEWED-008")
    transition(app_session, i8.id, InstrumentState.UNDER_SCRUTINY.value, scrutiny_actor, "Reviewing deposit")
    transition(app_session, i8.id, InstrumentState.VERIFIED.value, scrutiny_actor, "Bank confirmation verified successfully")
    transition(app_session, i8.id, InstrumentState.ACTIVE.value, approver_actor, "Activating verified deposit receipt")
    transition(app_session, i8.id, InstrumentState.EXPIRING.value, SYSTEM_ACTOR, "Within 90-day expiry threshold")
    transition(app_session, i8.id, InstrumentState.RENEWED.value, SYSTEM_ACTOR, "Deposit renewed into successor instrument")
    instruments[InstrumentState.RENEWED.value] = i8

    # 9. RELEASE_REQUESTED
    i9 = _create_initial_instrument(app_session, sample_institution, fdr_number="FDR-STATE-RELREQ-009")
    transition(app_session, i9.id, InstrumentState.UNDER_SCRUTINY.value, scrutiny_actor, "Reviewing deposit")
    transition(app_session, i9.id, InstrumentState.VERIFIED.value, scrutiny_actor, "Bank confirmation verified successfully")
    transition(app_session, i9.id, InstrumentState.ACTIVE.value, approver_actor, "Activating verified deposit receipt")
    transition(app_session, i9.id, InstrumentState.RELEASE_REQUESTED.value, inst_actor, "Requesting release of FDR deposit")
    instruments[InstrumentState.RELEASE_REQUESTED.value] = i9

    # 10. RELEASED
    i10 = _create_initial_instrument(app_session, sample_institution, fdr_number="FDR-STATE-RELEASED-010")
    transition(app_session, i10.id, InstrumentState.UNDER_SCRUTINY.value, scrutiny_actor, "Reviewing deposit")
    transition(app_session, i10.id, InstrumentState.VERIFIED.value, scrutiny_actor, "Bank confirmation verified successfully")
    transition(app_session, i10.id, InstrumentState.ACTIVE.value, approver_actor, "Activating verified deposit receipt")
    transition(app_session, i10.id, InstrumentState.RELEASE_REQUESTED.value, inst_actor, "Requesting release of FDR deposit")
    transition(app_session, i10.id, InstrumentState.RELEASED.value, approver_actor, "Authorized release of deposit")
    instruments[InstrumentState.RELEASED.value] = i10

    # 11. REJECTED
    i11 = _create_initial_instrument(app_session, sample_institution, fdr_number="FDR-STATE-REJECTED-011")
    transition(app_session, i11.id, InstrumentState.UNDER_SCRUTINY.value, scrutiny_actor, "Reviewing deposit")
    transition(app_session, i11.id, InstrumentState.REJECTED.value, approver_actor, "Rejected deposit due to statutory non-compliance")
    instruments[InstrumentState.REJECTED.value] = i11

    app_session.commit()

    return {
        "institution": sample_institution,
        "inst_user": inst_user,
        "scrutiny_user": scrutiny_user,
        "approver_user": approver_user,
        "instruments": instruments,
    }


def test_approver_workstation_cards_tabs_and_rows_consistency(
    client: TestClient,
    make_user,
    auth_client_for,
    eleven_state_fixture,
    app_session: Session,
):
    """Verifies for APPROVER_AUTHORITY:
    - Cards sum exactly to Total (caption present).
    - Tabs match card numbers and row counts.
    - Action Items is default and contains exactly 4 states (1 in each = 4 total).
    - ACTIVE appears in Active and All, but NOT in Action Items.
    - Fallback for unknown ?status= works safely without 500.
    - Zero audit entries written.
    """
    approver_user = eleven_state_fixture["approver_user"]
    auth_client_for(approver_user)

    initial_audit_count = app_session.query(AuditLog).count()

    # 1. Main approver page (Default: Action Items)
    res_app = client.get("/approver")
    assert res_app.status_code == status.HTTP_200_OK

    # Cards check
    cnt_total = _extract_counter(res_app.text, "counter-total")
    cnt_scrutiny = _extract_counter(res_app.text, "counter-under-scrutiny")
    cnt_flagged = _extract_counter(res_app.text, "counter-flagged")
    cnt_verified = _extract_counter(res_app.text, "counter-verified")
    cnt_active = _extract_counter(res_app.text, "counter-active")
    cnt_rel_req = _extract_counter(res_app.text, "counter-release-requested")
    cnt_released = _extract_counter(res_app.text, "counter-released")
    cnt_other = _extract_counter(res_app.text, "counter-other")

    assert cnt_total == 11
    assert cnt_scrutiny == 1
    assert cnt_flagged == 1
    assert cnt_verified == 1
    assert cnt_active == 1
    assert cnt_rel_req == 1
    assert cnt_released == 1
    # Other covers remaining 5 states: Submitted, Expiring, Matured, Renewed, Rejected
    assert cnt_other == 5

    # Invariant: Cards sum to Total
    assert cnt_total == cnt_scrutiny + cnt_flagged + cnt_verified + cnt_active + cnt_rel_req + cnt_released + cnt_other
    assert "Cards sum to Total" in res_app.text

    # Action Items row count check: UNDER_SCRUTINY + DISCREPANCY_FLAGGED + VERIFIED + RELEASE_REQUESTED = 4
    action_items_rows = _extract_table_row_count(res_app.text)
    assert action_items_rows == 4

    # ACTIVE must NOT appear in Action Items
    active_fdr = eleven_state_fixture["instruments"][InstrumentState.ACTIVE.value].fdr_number
    assert active_fdr not in res_app.text

    # 2. Active Tab: /approver?status=ACTIVE
    res_active = client.get("/approver?status=ACTIVE")
    assert res_active.status_code == status.HTTP_200_OK
    assert _extract_table_row_count(res_active.text) == 1
    assert active_fdr in res_active.text

    # 3. All Tab: /approver?status=ALL
    res_all = client.get("/approver?status=ALL")
    assert res_all.status_code == status.HTTP_200_OK
    assert _extract_table_row_count(res_all.text) == 11
    assert active_fdr in res_all.text

    # 4. Release Requested Tab: /approver?status=RELEASE_REQUESTED
    res_rel_req = client.get("/approver?status=RELEASE_REQUESTED")
    assert res_rel_req.status_code == status.HTTP_200_OK
    assert _extract_table_row_count(res_rel_req.text) == 1
    assert eleven_state_fixture["instruments"][InstrumentState.RELEASE_REQUESTED.value].fdr_number in res_rel_req.text

    # 5. Unknown status fallback: /approver?status=INVALID_UNKNOWN_PARAM
    res_fallback = client.get("/approver?status=INVALID_UNKNOWN_PARAM")
    assert res_fallback.status_code == status.HTTP_200_OK
    # Should fall back safely to Action Items (4 rows, active_fdr not present)
    assert _extract_table_row_count(res_fallback.text) == 4
    assert active_fdr not in res_fallback.text

    # Invariant: READ-ONLY request must write zero audit entries
    final_audit_count = app_session.query(AuditLog).count()
    assert final_audit_count == initial_audit_count


def test_scrutiny_workstation_cards_tabs_and_rows_consistency(
    client: TestClient,
    make_user,
    auth_client_for,
    eleven_state_fixture,
    app_session: Session,
):
    """Verifies for SCRUTINY_OFFICER:
    - Cards sum exactly to Total (caption present).
    - Tabs match card numbers and row counts.
    - All tab shows all 11 instruments.
    - Active tab shows ACTIVE instrument.
    - Fallback for unknown ?status= works safely without 500.
    - Zero audit entries written.
    """
    scrutiny_user = eleven_state_fixture["scrutiny_user"]
    auth_client_for(scrutiny_user)

    initial_audit_count = app_session.query(AuditLog).count()

    # 1. Main scrutiny page (Default: ALL)
    res_scr = client.get("/scrutiny")
    assert res_scr.status_code == status.HTTP_200_OK

    # Cards check
    cnt_total = _extract_counter(res_scr.text, "counter-total")
    cnt_submitted = _extract_counter(res_scr.text, "counter-submitted")
    cnt_scrutiny = _extract_counter(res_scr.text, "counter-under-scrutiny")
    cnt_flagged = _extract_counter(res_scr.text, "counter-flagged")
    cnt_verified = _extract_counter(res_scr.text, "counter-verified")
    cnt_active = _extract_counter(res_scr.text, "counter-active")
    cnt_rel_req = _extract_counter(res_scr.text, "counter-release-requested")
    cnt_other = _extract_counter(res_scr.text, "counter-other")

    assert cnt_total == 11
    assert cnt_submitted == 1
    assert cnt_scrutiny == 1
    assert cnt_flagged == 1
    assert cnt_verified == 1
    assert cnt_active == 1
    assert cnt_rel_req == 1
    # Other covers remaining states: Expiring, Matured, Renewed, Released, Rejected
    assert cnt_other == 5

    # Invariant: Cards sum to Total
    assert cnt_total == cnt_submitted + cnt_scrutiny + cnt_flagged + cnt_verified + cnt_active + cnt_rel_req + cnt_other
    assert "Cards sum to Total" in res_scr.text

    # Default queue shows all 11 rows
    assert _extract_table_row_count(res_scr.text) == 11

    # 2. Filter tabs: Active, Expiring, Matured
    res_act = client.get("/scrutiny?status=ACTIVE")
    assert res_act.status_code == status.HTTP_200_OK
    assert _extract_table_row_count(res_act.text) == 1

    res_exp = client.get("/scrutiny?status=EXPIRING")
    assert res_exp.status_code == status.HTTP_200_OK
    assert _extract_table_row_count(res_exp.text) == 1

    res_mat = client.get("/scrutiny?status=MATURED")
    assert res_mat.status_code == status.HTTP_200_OK
    assert _extract_table_row_count(res_mat.text) == 1

    # 3. Unknown ?status= fallback
    res_unknown = client.get("/scrutiny?status=NON_EXISTENT_STATE")
    assert res_unknown.status_code == status.HTTP_200_OK
    assert _extract_table_row_count(res_unknown.text) == 11

    # Invariant: Zero audit entries written
    final_audit_count = app_session.query(AuditLog).count()
    assert final_audit_count == initial_audit_count


def test_auditor_workstation_cards_tabs_and_rows_consistency(
    client: TestClient,
    make_user,
    auth_client_for,
    eleven_state_fixture,
    app_session: Session,
):
    """Verifies for AUDITOR:
    - Cards sum exactly to Total (caption present).
    - Tabs match card numbers and row counts.
    - All tab shows all 11 instruments.
    - Active tab shows ACTIVE instrument.
    - Zero audit entries written.
    """
    auditor_user = make_user("auditor.fsm.user", "AUDITOR")
    auth_client_for(auditor_user)

    initial_audit_count = app_session.query(AuditLog).count()

    # 1. Main auditor page
    res_aud = client.get("/auditor")
    assert res_aud.status_code == status.HTTP_200_OK

    cnt_total = _extract_counter(res_aud.text, "counter-total")
    cnt_active = _extract_counter(res_aud.text, "counter-active")
    cnt_flagged = _extract_counter(res_aud.text, "counter-flagged")
    cnt_released = _extract_counter(res_aud.text, "counter-released")
    cnt_submitted = _extract_counter(res_aud.text, "counter-submitted")
    cnt_scrutiny = _extract_counter(res_aud.text, "counter-under-scrutiny")
    cnt_verified = _extract_counter(res_aud.text, "counter-verified")
    cnt_other = _extract_counter(res_aud.text, "counter-other")

    assert cnt_total == 11
    assert cnt_active == 1
    assert cnt_flagged == 1
    assert cnt_released == 1
    assert cnt_submitted == 1
    assert cnt_scrutiny == 1
    assert cnt_verified == 1
    assert cnt_other == 5

    # Invariant: Cards sum to Total
    assert cnt_total == cnt_active + cnt_flagged + cnt_released + cnt_submitted + cnt_scrutiny + cnt_verified + cnt_other
    assert "Cards sum to Total" in res_aud.text

    # Default queue shows all 11 rows
    assert _extract_table_row_count(res_aud.text) == 11

    # 2. Check tab filters: Active, Expiring, Matured
    res_act = client.get("/auditor?status=ACTIVE")
    assert res_act.status_code == status.HTTP_200_OK
    assert _extract_table_row_count(res_act.text) == 1

    res_exp = client.get("/auditor?status=EXPIRING")
    assert res_exp.status_code == status.HTTP_200_OK
    assert _extract_table_row_count(res_exp.text) == 1

    res_mat = client.get("/auditor?status=MATURED")
    assert res_mat.status_code == status.HTTP_200_OK
    assert _extract_table_row_count(res_mat.text) == 1

    # Invariant: Zero audit entries written
    final_audit_count = app_session.query(AuditLog).count()
    assert final_audit_count == initial_audit_count


def test_institution_workstation_cards_tabs_and_scoping(
    client: TestClient,
    make_user,
    auth_client_for,
    eleven_state_fixture,
    sample_institution: Institution,
    sample_institution_b: Institution,
    app_session: Session,
):
    """Verifies for INSTITUTION_ADMIN:
    - Cards sum exactly to Total (caption present).
    - Tabs match card numbers and row counts.
    - Institution user sees ONLY own institution's instruments in every count and queue.
    - Null business fields render dashes without crashing.
    - Sorting by principal_amount and maturity_date preserves tab filter and handles nulls safely.
    - Zero audit entries written.
    """
    # Create an instrument for Institution B so cross-tenancy isolation can be verified
    _create_initial_instrument(app_session, sample_institution_b, fdr_number="INSTB-ISOLATION-999")

    inst_user = eleven_state_fixture["inst_user"]
    auth_client_for(inst_user)

    initial_audit_count = app_session.query(AuditLog).count()

    # 1. Main institution page
    res_inst = client.get("/institution")
    assert res_inst.status_code == status.HTTP_200_OK

    cnt_total = _extract_counter(res_inst.text, "counter-total")
    cnt_sub = _extract_counter(res_inst.text, "counter-submitted")
    cnt_act = _extract_counter(res_inst.text, "counter-active")
    cnt_flg = _extract_counter(res_inst.text, "counter-flagged")
    cnt_oth = _extract_counter(res_inst.text, "counter-other")

    # Scoped strictly to Institution A: exactly 11 instruments
    assert cnt_total == 11
    assert cnt_sub == 1
    assert cnt_act == 1
    assert cnt_flg == 1
    assert cnt_oth == 8  # 11 - (1 + 1 + 1) = 8

    # Invariant: Cards sum to Total
    assert cnt_total == cnt_sub + cnt_act + cnt_flg + cnt_oth
    assert "Cards sum to Total" in res_inst.text

    # Tenancy isolation: Institution B instrument must NOT appear anywhere
    assert "INSTB-ISOLATION-999" not in res_inst.text

    # Default queue shows 11 rows for Institution A
    assert _extract_table_row_count(res_inst.text) == 11

    # 2. Filter tabs on /institution
    res_act = client.get("/institution?status=ACTIVE")
    assert res_act.status_code == status.HTTP_200_OK
    assert _extract_table_row_count(res_act.text) == 1
    assert eleven_state_fixture["instruments"][InstrumentState.ACTIVE.value].fdr_number in res_act.text

    res_exp = client.get("/institution?status=EXPIRING")
    assert res_exp.status_code == status.HTTP_200_OK
    assert _extract_table_row_count(res_exp.text) == 1

    res_mat = client.get("/institution?status=MATURED")
    assert res_mat.status_code == status.HTTP_200_OK
    assert _extract_table_row_count(res_mat.text) == 1

    # 3. Check null business field rendering (SUBMITTED instrument has null fields)
    res_sub = client.get("/institution?status=SUBMITTED")
    assert res_sub.status_code == status.HTTP_200_OK
    assert _extract_table_row_count(res_sub.text) == 1
    # Check that null fields render as dashes ("—")
    assert "—" in res_sub.text

    # 4. Sorting with null fields handles nulls safely with nullslast()
    res_sort_asc = client.get("/institution?sort=principal_amount&order=asc")
    assert res_sort_asc.status_code == status.HTTP_200_OK
    assert _extract_table_row_count(res_sort_asc.text) == 11

    res_sort_mat = client.get("/institution?sort=maturity_date&order=desc")
    assert res_sort_mat.status_code == status.HTTP_200_OK
    assert _extract_table_row_count(res_sort_mat.text) == 11

    # 5. Unknown ?status= fallback
    res_unknown = client.get("/institution?status=UNKNOWN_STATE_XYZ")
    assert res_unknown.status_code == status.HTTP_200_OK
    assert _extract_table_row_count(res_unknown.text) == 11

    # Invariant: Zero audit entries written
    final_audit_count = app_session.query(AuditLog).count()
    assert final_audit_count == initial_audit_count
