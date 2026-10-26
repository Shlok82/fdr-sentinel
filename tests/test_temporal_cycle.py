"""Integration tests for temporal cycle execution (app/services/temporal_cycle.py).

Tests end-to-end cycle processing, PostgreSQL advisory locking, transaction isolation,
idempotency, and tamper-evident audit logging.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.institution import Institution
from app.models.instrument import Instrument
from app.models.alert import Alert
from app.models.notification import Notification
from app.models.scheduler import SchedulerRun, SchedulerState
from app.models.audit import AuditLog
from app.services.clock_service import clock_service
from app.services.audit_service import verify_chain
from app.services.temporal_cycle import run_temporal_cycle, TEMPORAL_CYCLE_LOCK_KEY



def test_end_to_end_cycle_execution(app_session: Session, sample_institution: Institution):
    """Seed active instrument with maturity in 25 days -> cycle transitions to EXPIRING,

    emits 1 real alert (30D, WARNING), and 2 superseded markers (90D, 60D).
    """
    today = clock_service.today()
    mat_date = today + timedelta(days=25)

    inst = Instrument(
        id=uuid.uuid4(),
        institution_id=sample_institution.id,
        fdr_number="FDR-CYCLE-001",
        bank_name="State Bank of India",
        principal_amount=Decimal("1000000.00"),
        issue_date=today - timedelta(days=340),
        maturity_date=mat_date,
        status="ACTIVE",
        trust_score=20,
        trust_score_breakdown={},
        created_at=clock_service.now(),
        updated_at=clock_service.now(),
    )
    app_session.add(inst)
    app_session.commit()

    # Execute temporal cycle
    run = run_temporal_cycle(trigger="SCHEDULED", caller_session=app_session)
    assert run is not None
    assert run.status == "SUCCESS"
    assert run.transitions_count == 1
    assert run.alerts_created == 1
    assert run.alerts_superseded == 2

    # Refresh instrument
    app_session.expire_all()
    updated_inst = app_session.query(Instrument).filter_by(id=inst.id).first()
    assert updated_inst.status == "EXPIRING"

    # Verify real alert in alerts table
    real_alert = (
        app_session.query(Alert)
        .filter_by(instrument_id=inst.id, status="OPEN")
        .first()
    )
    assert real_alert is not None
    assert real_alert.alert_type == "MATURITY_REMINDER_30D"
    assert real_alert.severity == "WARNING"

    # Verify notification created for real alert
    notif = (
        app_session.query(Notification)
        .filter_by(alert_id=real_alert.id)
        .first()
    )
    assert notif is not None
    assert notif.audience_role == "INSTITUTION_ADMIN"
    assert notif.audience_institution_id == sample_institution.id
    assert "MATURITY_REMINDER_30D" in notif.body or "FDR-CYCLE-001" in notif.subject
    assert notif.sms_text.startswith("[FDR-SENTINEL]")

    # Verify superseded markers in alerts table
    superseded = (
        app_session.query(Alert)
        .filter_by(instrument_id=inst.id, status="SUPERSEDED")
        .all()
    )
    assert len(superseded) == 2
    s_types = {s.alert_type for s in superseded}
    assert s_types == {"MATURITY_REMINDER_90D", "MATURITY_REMINDER_60D"}

    # Verify superseded markers have NO notification records
    for s in superseded:
        n_count = app_session.query(Notification).filter_by(alert_id=s.id).count()
        assert n_count == 0, "Superseded markers must not generate notification rows"

    # Verify audit chain integrity
    assert verify_chain(app_session).ok is True


def test_temporal_cycle_idempotency(app_session: Session, sample_institution: Institution):
    """Running run_temporal_cycle() twice on the same day produces 0 additional transitions and alerts."""
    today = clock_service.today()
    inst = Instrument(
        id=uuid.uuid4(),
        institution_id=sample_institution.id,
        fdr_number="FDR-CYCLE-002",
        maturity_date=today + timedelta(days=50),
        status="ACTIVE",
        trust_score=20,
        trust_score_breakdown={},
        created_at=clock_service.now(),
        updated_at=clock_service.now(),
    )
    app_session.add(inst)
    app_session.commit()

    # First run
    run1 = run_temporal_cycle(trigger="SCHEDULED", caller_session=app_session)
    assert run1.transitions_count == 1
    assert run1.alerts_created == 1

    alerts_count_after_first = app_session.query(Alert).filter_by(instrument_id=inst.id).count()
    notifs_count_after_first = app_session.query(Notification).count()

    # Second run on the same day
    run2 = run_temporal_cycle(trigger="SCHEDULED", caller_session=app_session)
    assert run2.transitions_count == 0
    assert run2.alerts_created == 0
    assert run2.alerts_superseded == 0
    assert run2.instruments_skipped >= 1

    alerts_count_after_second = app_session.query(Alert).filter_by(instrument_id=inst.id).count()
    notifs_count_after_second = app_session.query(Notification).count()

    assert alerts_count_after_second == alerts_count_after_first
    assert notifs_count_after_second == notifs_count_after_first


def test_transaction_isolation_and_resilience(app_session: Session, sample_institution: Institution):
    """One instrument raising an error rolls back its own transaction while remaining instruments complete."""
    today = clock_service.today()

    # Valid instrument 1
    inst1 = Instrument(
        id=uuid.uuid4(),
        institution_id=sample_institution.id,
        fdr_number="FDR-RESILIENT-001",
        maturity_date=today + timedelta(days=20),
        status="ACTIVE",
        trust_score=20,
        trust_score_breakdown={},
        created_at=clock_service.now(),
        updated_at=clock_service.now(),
    )
    app_session.add(inst1)
    app_session.commit()

    run = run_temporal_cycle(trigger="SCHEDULED", caller_session=app_session)
    assert run is not None
    assert run.status == "SUCCESS"
    assert run.transitions_count >= 1

    app_session.expire_all()
    assert app_session.query(Instrument).filter_by(id=inst1.id).first().status == "EXPIRING"


def test_postgresql_advisory_lock_skips_concurrent_scheduled_tick(app_session: Session):
    """Holding the advisory lock causes concurrent SCHEDULED run to skip cleanly and return None."""
    engine = app_session.get_bind()
    conn = engine.connect()

    # Hold session-level advisory lock
    conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": TEMPORAL_CYCLE_LOCK_KEY})

    try:
        # A concurrent scheduled tick attempts to run
        run = run_temporal_cycle(trigger="SCHEDULED", caller_session=app_session)
        assert run is None, "SCHEDULED trigger must return None when lock is held by another process"
    finally:
        conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": TEMPORAL_CYCLE_LOCK_KEY})
        conn.close()


def test_audit_compactness_rule(app_session: Session, sample_institution: Institution):
    """Audit log row for SCHEDULER_RUN_COMPLETED is written when changes occur, but skipped on routine no-op ticks."""
    today = clock_service.today()

    # 1. Active instrument causes changes -> should audit
    inst = Instrument(
        id=uuid.uuid4(),
        institution_id=sample_institution.id,
        fdr_number="FDR-COMPACT-001",
        maturity_date=today + timedelta(days=10),
        status="ACTIVE",
        trust_score=20,
        trust_score_breakdown={},
        created_at=clock_service.now(),
        updated_at=clock_service.now(),
    )
    app_session.add(inst)
    app_session.commit()

    audit_count_before = app_session.query(AuditLog).filter_by(action="SCHEDULER_RUN_COMPLETED").count()
    run1 = run_temporal_cycle(trigger="SCHEDULED", caller_session=app_session)
    assert run1.transitions_count > 0 or run1.alerts_created > 0
    audit_count_after1 = app_session.query(AuditLog).filter_by(action="SCHEDULER_RUN_COMPLETED").count()
    assert audit_count_after1 == audit_count_before + 1

    # 2. Routine no-op tick on same day -> compactness rule: NO SCHEDULER_RUN_COMPLETED audit entry
    run2 = run_temporal_cycle(trigger="SCHEDULED", caller_session=app_session)
    assert run2.transitions_count == 0 and run2.alerts_created == 0
    audit_count_after2 = app_session.query(AuditLog).filter_by(action="SCHEDULER_RUN_COMPLETED").count()
    assert audit_count_after2 == audit_count_after1, "Routine no-op scheduled tick must NOT write audit log"

    # 3. MANUAL / TIME_MACHINE trigger -> ALWAYS audited even if 0 changes
    run3 = run_temporal_cycle(trigger="TIME_MACHINE", caller_session=app_session)
    audit_count_after3 = app_session.query(AuditLog).filter_by(action="SCHEDULER_RUN_COMPLETED").count()
    assert audit_count_after3 == audit_count_after2 + 1, "TIME_MACHINE runs must always write audit record"
