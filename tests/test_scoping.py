import uuid
import pytest
from fastapi import HTTPException
from app.core.dependencies import scope_institution_query, verify_institution_access
from app.models.instrument import Instrument
from app.services.clock_service import clock_service


class DummyRecord:
    def __init__(self, institution_id):
        self.institution_id = institution_id


def test_verify_institution_access_boundary(make_user, sample_institution, sample_institution_b):
    """Institution user gets 404 (not 403) when attempting to access another institution's record."""
    user_a = make_user("inst.a", "INSTITUTION_ADMIN", institution_id=sample_institution.id)
    user_b = make_user("inst.b", "INSTITUTION_ADMIN", institution_id=sample_institution_b.id)
    staff_user = make_user("staff.officer", "SCRUTINY_OFFICER")

    record_a = DummyRecord(sample_institution.id)
    record_b = DummyRecord(sample_institution_b.id)

    # 1. User A accessing Record A: allowed
    verify_institution_access(record_a, user_a)

    # 2. User A accessing Record B: raises 404 (NOT 403, preventing ID probing)
    with pytest.raises(HTTPException) as exc_info:
        verify_institution_access(record_b, user_a)
    assert exc_info.value.status_code == 404

    # 3. User B accessing Record A: raises 404
    with pytest.raises(HTTPException) as exc_info:
        verify_institution_access(record_a, user_b)
    assert exc_info.value.status_code == 404

    # 4. Staff users are not scoped: can access records from any institution
    verify_institution_access(record_a, staff_user)
    verify_institution_access(record_b, staff_user)


def test_scope_institution_query(app_session, make_user, sample_institution, sample_institution_b):
    """scope_institution_query automatically applies institution_id filter for INSTITUTION_ADMIN."""
    user_a = make_user("inst.query.a", "INSTITUTION_ADMIN", institution_id=sample_institution.id)
    staff_user = make_user("staff.query", "SCRUTINY_OFFICER")

    now_ts = clock_service.now()
    now_date = clock_service.today()

    # Create dummy instruments for each institution
    inst_a = Instrument(
        id=uuid.uuid4(),
        institution_id=sample_institution.id,
        fdr_number="FDR-A-1",
        bank_name="State Bank of India",
        bank_branch="Andheri",
        ifsc_code="SBIN0001234",
        principal_amount=1000000.0,
        maturity_amount=1070000.0,
        interest_rate_percent=7.0,
        issue_date=now_date,
        maturity_date=now_date,
        tenure_months=12,
        joint_account_holder="AICTE & Principal",
        created_at=now_ts,
        updated_at=now_ts,
    )
    inst_b = Instrument(
        id=uuid.uuid4(),
        institution_id=sample_institution_b.id,
        fdr_number="FDR-B-1",
        bank_name="Punjab National Bank",
        bank_branch="Delhi",
        ifsc_code="PUNB0005678",
        principal_amount=2000000.0,
        maturity_amount=2140000.0,
        interest_rate_percent=7.0,
        issue_date=now_date,
        maturity_date=now_date,
        tenure_months=12,
        joint_account_holder="AICTE & Dean",
        created_at=now_ts,
        updated_at=now_ts,
    )
    app_session.add_all([inst_a, inst_b])
    app_session.commit()

    # Query scoped for User A -> returns only Record A
    q_a = scope_institution_query(app_session.query(Instrument), user_a, Instrument.institution_id)
    results_a = q_a.all()
    assert len(results_a) == 1
    assert results_a[0].id == inst_a.id

    # Query for staff user -> returns all records
    q_staff = scope_institution_query(app_session.query(Instrument), staff_user, Instrument.institution_id)
    results_staff = q_staff.all()
    assert len(results_staff) >= 2
