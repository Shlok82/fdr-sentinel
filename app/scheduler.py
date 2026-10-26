"""APScheduler background job runner for temporal lifecycle automation.
"""
import logging
from typing import Optional
from apscheduler.schedulers.background import BackgroundScheduler
from app.core.config import settings

logger = logging.getLogger(__name__)

_scheduler: Optional[BackgroundScheduler] = None


def _scheduled_tick():
    """Job executed periodically by APScheduler."""
    try:
        from app.services.temporal_cycle import run_temporal_cycle
        run_temporal_cycle(trigger="SCHEDULED")
    except Exception as exc:
        logger.exception("Error during scheduled temporal cycle: %s", exc)


def start_scheduler():
    """Starts the APScheduler background daemon if enabled."""
    global _scheduler
    if not settings.SCHEDULER_ENABLED:
        logger.info("Scheduler is disabled by configuration (SCHEDULER_ENABLED=False).")
        return

    if _scheduler is not None and _scheduler.running:
        logger.warning("Scheduler is already running.")
        return

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        _scheduled_tick,
        "interval",
        minutes=settings.SCHEDULER_INTERVAL_MINUTES,
        id="temporal_cycle_interval",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("APScheduler started (interval: %d minutes)", settings.SCHEDULER_INTERVAL_MINUTES)

    # Immediate startup catch-up run
    try:
        from app.services.temporal_cycle import run_temporal_cycle
        logger.info("Executing immediate STARTUP temporal cycle catch-up run...")
        run_temporal_cycle(trigger="STARTUP")
    except Exception as exc:
        logger.exception("Error during startup temporal cycle catch-up: %s", exc)


def stop_scheduler():
    """Gracefully shuts down APScheduler background daemon."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        logger.info("Shutting down APScheduler...")
        _scheduler.shutdown(wait=False)
        _scheduler = None
