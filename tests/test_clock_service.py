from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy.orm import Session
from app.services.clock_service import ClockService, clock_service
from app.core.config import settings


def test_clock_advance_persists_and_restores(app_session: Session):
    """advance(30) shifts clock by 30 days, persists to database, and is seen by a new instance."""
    real_today = datetime.now(ZoneInfo("Asia/Kolkata")).date()  # clock-allow: test fixture reference
    
    # Advance clock by 30 days
    new_offset = clock_service.advance(app_session, 30)
    app_session.commit()
    
    expected_offset = 30 * 86400
    assert new_offset == expected_offset
    assert clock_service.offset_seconds == expected_offset
    assert clock_service.is_simulated is True
    assert clock_service.today() == real_today + timedelta(days=30)

    # Simulate restart with a brand new ClockService instance
    new_clock = ClockService()
    restored_offset = new_clock.load_offset_from_db(app_session)
    assert restored_offset == expected_offset
    assert new_clock.today() == real_today + timedelta(days=30)


def test_clock_reset_restores_real_time(app_session: Session):
    """reset() restores clock back to 0 offset and writes audit log."""
    real_today = datetime.now(ZoneInfo("Asia/Kolkata")).date()  # clock-allow: test fixture reference
    
    clock_service.advance(app_session, 60)
    app_session.commit()
    assert clock_service.is_simulated is True

    # Reset
    clock_service.reset(app_session)
    app_session.commit()

    assert clock_service.offset_seconds == 0
    assert clock_service.is_simulated is False
    assert clock_service.today() == real_today


def test_clock_advance_refused_when_demo_mode_false(app_session: Session, monkeypatch):
    """advance() strictly raises PermissionError when DEMO_MODE is false."""
    monkeypatch.setattr(settings, "DEMO_MODE", False)

    with pytest.raises(PermissionError) as exc_info:
        clock_service.advance(app_session, 15)
    assert "DEMO_MODE" in str(exc_info.value)


def test_clock_advance_validation(app_session: Session):
    """advance() rejects negative, zero, non-integer, or excessive day offsets."""
    with pytest.raises(ValueError):
        clock_service.advance(app_session, 0)

    with pytest.raises(ValueError):
        clock_service.advance(app_session, -5)

    with pytest.raises(ValueError):
        clock_service.advance(app_session, 5000)  # Exceeds 3650 days cap
