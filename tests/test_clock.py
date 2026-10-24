from datetime import datetime, timezone, timedelta
from app.services.clock_service import ClockService


def test_clock_service_hardware_time():
    clock = ClockService()
    now_utc = datetime.now(timezone.utc)
    clock_time = clock.get_current_time()
    
    assert abs((clock_time - now_utc).total_seconds()) < 2
    assert clock.get_current_date() == now_utc.date()
    assert clock.is_simulated is False


def test_clock_service_offset():
    clock = ClockService()
    # Advance clock by 30 days
    thirty_days_sec = 30 * 86400
    clock.set_offset(thirty_days_sec, is_simulated=True)
    
    now_utc = datetime.now(timezone.utc)
    clock_time = clock.get_current_time()
    diff = (clock_time - now_utc).total_seconds()
    
    assert abs(diff - thirty_days_sec) < 2
    assert clock.is_simulated is True
    
    # Reset clock
    clock.reset()
    assert clock.offset_seconds == 0
    assert clock.is_simulated is False
