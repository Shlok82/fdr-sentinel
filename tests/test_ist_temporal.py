import uuid
from datetime import datetime, date, timezone, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.templates import format_datetime, format_date
from app.models.instrument import Instrument
from app.models.audit import AuditLog
from app.services.clock_service import clock_service
from app.services.audit_service import AuditService, verify_chain
from app.services.discrepancy_engine import check_disc_02_chronological_inversion, check_disc_04_expired_instrument


def test_display_timezone_setting():
    """Confirms DISPLAY_TIMEZONE is configured to Asia/Kolkata and validated at startup."""
    assert settings.DISPLAY_TIMEZONE == "Asia/Kolkata"
    assert settings.display_tz == ZoneInfo("Asia/Kolkata")


def test_clock_service_to_local_conversion():
    """Asserts ClockService.to_local converts UTC to IST (+5:30 offset)."""
    # 2026-10-25 21:32:00 UTC -> 2026-10-26 03:02:00 IST
    utc_dt = datetime(2026, 10, 25, 21, 32, 0, tzinfo=timezone.utc)
    ist_dt = clock_service.to_local(utc_dt)
    assert ist_dt is not None
    assert ist_dt.year == 2026
    assert ist_dt.month == 10
    assert ist_dt.day == 26
    assert ist_dt.hour == 3
    assert ist_dt.minute == 2


def test_clock_today_ist_rollover_at_1830_utc(monkeypatch):
    """Asserts ClockService.today() correctly rolls over at 18:30 UTC (IST midnight)."""
    # At 2026-10-25 18:29:59 UTC: IST is 23:59:59 on 25 Oct -> date is 2026-10-25
    before_midnight = datetime(2026, 10, 25, 18, 29, 59, tzinfo=timezone.utc)
    monkeypatch.setattr(clock_service, "now", lambda: before_midnight)
    assert clock_service.today() == date(2026, 10, 25)

    # At 2026-10-25 18:30:00 UTC: IST is 00:00:00 on 26 Oct -> date is 2026-10-26
    at_midnight = datetime(2026, 10, 25, 18, 30, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(clock_service, "now", lambda: at_midnight)
    assert clock_service.today() == date(2026, 10, 26)


def test_format_datetime_outputs_ist(monkeypatch):
    """format_datetime always outputs 'DD Mon YYYY, HH:MM IST'."""
    # Test specific timestamp from user prompt
    utc_dt = datetime(2026, 10, 25, 21, 32, 0, tzinfo=timezone.utc)
    formatted = format_datetime(utc_dt)
    assert formatted == "26 Oct 2026, 03:02 IST"
    assert formatted.endswith("IST")

    # Test None
    assert format_datetime(None) == "—"

    # Test naive datetime (treated as UTC)
    naive_dt = datetime(2026, 10, 25, 21, 32, 0)
    assert format_datetime(naive_dt) == "26 Oct 2026, 03:02 IST"


def test_format_date_converts_datetime_to_ist():
    """format_date accurately reflects IST calendar date when given datetime."""
    utc_dt = datetime(2026, 10, 25, 21, 32, 0, tzinfo=timezone.utc)
    # Even though UTC date is 25 Oct, in IST it is 26 Oct
    assert format_date(utc_dt) == "26 Oct 2026"

    # Pure date object remains unchanged
    assert format_date(date(2026, 10, 25)) == "25 Oct 2026"
    assert format_date(None) == "—"


def test_disc_04_expired_instrument_boundary_at_1830_utc(monkeypatch):
    """DISC-04 evaluates expiration against IST date across 18:30 UTC boundary."""
    inst = Instrument(
        id=uuid.uuid4(),
        maturity_date=date(2026, 10, 25),
    )

    # 1. At 18:29 UTC: IST today is 2026-10-25. maturity_date == today is NOT expired (< is False)
    before_midnight = datetime(2026, 10, 25, 18, 29, 59, tzinfo=timezone.utc)
    monkeypatch.setattr(clock_service, "now", lambda: before_midnight)
    res_before = check_disc_04_expired_instrument(inst)
    assert res_before is None, "Instrument should NOT be flagged as expired before IST midnight"

    # 2. At 18:30 UTC: IST today is 2026-10-26. maturity_date (2026-10-25) < 2026-10-26 -> EXPIRED
    at_midnight = datetime(2026, 10, 25, 18, 30, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(clock_service, "now", lambda: at_midnight)
    res_after = check_disc_04_expired_instrument(inst)
    assert res_after is not None, "Instrument should be flagged as expired after IST midnight"
    assert res_after.rule_code == "DISC-04"


def test_time_machine_advancement_and_ist_banner(client: TestClient, make_user, auth_client_for, app_session: Session):
    """Time Machine advance +1d and +60d produce expected IST simulated date and banner."""
    demo_admin = make_user("demo.ist.admin", "AUDITOR", is_demo_admin=True)
    _, csrf = auth_client_for(demo_admin)

    # Reset clock first
    client.post("/api/v1/demo/clock", data={"action": "reset", "csrf_token": csrf})

    # Advance +1d
    res1 = client.post(
        "/api/v1/demo/clock",
        data={"action": "+1d", "csrf_token": csrf},
        headers={"HX-Request": "true"}
    )
    assert res1.status_code == 200
    assert "IST" in res1.text or "Offset Active" in res1.text
    assert clock_service.is_simulated is True

    # Advance +60d
    res60 = client.post(
        "/api/v1/demo/clock",
        data={"action": "+60d", "csrf_token": csrf},
        headers={"HX-Request": "true"}
    )
    assert res60.status_code == 200

    page_res = client.get("/auditor")
    assert "simulated-date-banner" in page_res.text
    assert "Simulated date:" in page_res.text
    # Verify current_time rendered in toolbar has IST
    assert "IST" in page_res.text

    # Reset
    client.post("/api/v1/demo/clock", data={"action": "reset", "csrf_token": csrf})
    assert clock_service.is_simulated is False


def test_stored_timestamps_remain_utc_and_verify_chain(app_session: Session):
    """Storage remains strictly UTC: event_timestamp in AuditLog is unchanged and verify_chain passes."""
    now_utc = clock_service.now()
    AuditService.append(
        session=app_session,
        entity_type="SYSTEM",
        entity_id=uuid.uuid4(),
        action="TEST_TEMPORAL_AUDIT",
        actor_user_id=None,
        actor_role="SYSTEM",
        details={"test": "utc_storage"},
    )
    app_session.commit()

    latest_audit = (
        app_session.query(AuditLog)
        .filter(AuditLog.action == "TEST_TEMPORAL_AUDIT")
        .first()
    )
    assert latest_audit is not None
    # Stored payload event_timestamp ends with Z (UTC format)
    assert latest_audit.canonical_payload["event_timestamp"].endswith("Z")
    
    # Hash chain verification remains OK
    chain_check = verify_chain(app_session)
    assert chain_check.ok is True
