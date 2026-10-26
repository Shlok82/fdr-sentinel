"""Router for proactive notifications, alerts log, and scheduler control.
"""
import uuid
import logging
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Request, Response, Depends, Form, Query, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text, func
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.core.templates import templates
from app.core.database import get_db
from app.core.dependencies import get_current_user, verify_csrf
from app.models.user import User
from app.models.alert import Alert
from app.models.notification import Notification
from app.models.scheduler import SchedulerRun, SchedulerState
from app.models.instrument import Instrument
from app.models.institution import Institution
from app.services.clock_service import clock_service
from app.services.audit_service import AuditService
from app.services.temporal_cycle import run_temporal_cycle

logger = logging.getLogger(__name__)

router = APIRouter(include_in_schema=False)


def get_unread_notifications_count(db: Session, user: Optional[User]) -> int:
    """Calculates unread notification count for user's audience scope."""
    if not user or user.role not in ("INSTITUTION_ADMIN", "SCRUTINY_OFFICER", "APPROVER_AUTHORITY"):
        return 0

    query = db.query(func.count(Notification.id)).filter(
        Notification.audience_role == user.role,
        Notification.read_at.is_(None),
    )
    if user.role == "INSTITUTION_ADMIN":
        query = query.filter(Notification.audience_institution_id == user.institution_id)
    else:
        query = query.filter(Notification.audience_institution_id.is_(None))

    return query.scalar() or 0


def base_context(request: Request, user: Optional[User], db: Session) -> dict:
    """Baseline template context with unread count."""
    csrf_token = request.cookies.get(settings.CSRF_COOKIE_NAME, "")
    unread_count = get_unread_notifications_count(db, user)
    return {
        "request": request,
        "user": user,
        "current_time": clock_service.display_time,
        "is_simulated": clock_service.is_simulated,
        "demo_mode": settings.DEMO_MODE,
        "simulated_date_str": clock_service.display_simulated_date,
        "csrf_token": csrf_token,
        "unread_notifications_count": unread_count,
    }


# =============================================================================
# 1. Notifications Routes
# =============================================================================

@router.get("/notifications", response_class=HTMLResponse)
def list_notifications(
    request: Request,
    filter: str = Query("all", pattern="^(all|unread|high_severity)$"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List notifications for the current user's audience scope."""
    if user.role not in ("INSTITUTION_ADMIN", "SCRUTINY_OFFICER", "APPROVER_AUTHORITY") and not user.is_demo_admin:
        # Auditors or other roles have no notifications inbox
        pass

    query = (
        db.query(Notification, Alert)
        .join(Alert, Notification.alert_id == Alert.id)
        .options(joinedload(Notification.alert))
    )

    if not user.is_demo_admin or user.role in ("INSTITUTION_ADMIN", "SCRUTINY_OFFICER", "APPROVER_AUTHORITY"):
        query = query.filter(Notification.audience_role == user.role)
        if user.role == "INSTITUTION_ADMIN":
            query = query.filter(Notification.audience_institution_id == user.institution_id)
        else:
            query = query.filter(Notification.audience_institution_id.is_(None))

    if filter == "unread":
        query = query.filter(Notification.read_at.is_(None))
    elif filter == "high_severity":
        query = query.filter(Alert.severity.in_(("URGENT", "CRITICAL")))

    notifications = query.order_by(Notification.created_at.desc()).all()

    ctx = base_context(request, user, db)
    ctx.update({
        "notifications": notifications,
        "selected_filter": filter,
    })
    return templates.TemplateResponse("notifications/list.html", ctx)


@router.get("/notifications/{notification_id}", response_class=HTMLResponse)
def view_notification(
    notification_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Detail view of a single notification with simulated delivery previews."""
    notif = (
        db.query(Notification)
        .filter(Notification.id == notification_id)
        .first()
    )
    if not notif:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")

    # Scoping check: must match role and institution
    if not user.is_demo_admin:
        if notif.audience_role != user.role:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
        if user.role == "INSTITUTION_ADMIN" and notif.audience_institution_id != user.institution_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")

    alert = db.query(Alert).filter(Alert.id == notif.alert_id).first()
    instrument = db.query(Instrument).filter(Instrument.id == alert.instrument_id).first() if alert else None

    # Mark as read if not already read
    if notif.read_at is None:
        notif.read_at = clock_service.now()
        notif.read_by_user_id = user.id
        AuditService.append(
            session=db,
            entity_type="NOTIFICATION",
            entity_id=notif.id,
            action="NOTIFICATION_READ",
            actor_user_id=user.id,
            actor_role=user.role,
            details={
                "alert_id": str(notif.alert_id),
                "alert_type": alert.alert_type if alert else None,
                "subject": notif.subject,
            },
        )
        db.commit()

    ctx = base_context(request, user, db)
    ctx.update({
        "notification": notif,
        "alert": alert,
        "instrument": instrument,
    })
    return templates.TemplateResponse("notifications/detail.html", ctx)


@router.post("/notifications/{notification_id}/read", response_class=Response, dependencies=[Depends(verify_csrf)])
def mark_notification_read(
    notification_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Marks a single notification as read."""
    notif = db.query(Notification).filter(Notification.id == notification_id).first()
    if not notif:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")

    if not user.is_demo_admin:
        if notif.audience_role != user.role:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
        if user.role == "INSTITUTION_ADMIN" and notif.audience_institution_id != user.institution_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")

    if notif.read_at is None:
        notif.read_at = clock_service.now()
        notif.read_by_user_id = user.id
        AuditService.append(
            session=db,
            entity_type="NOTIFICATION",
            entity_id=notif.id,
            action="NOTIFICATION_READ",
            actor_user_id=user.id,
            actor_role=user.role,
            details={"notification_id": str(notif.id), "subject": notif.subject},
        )
        db.commit()

    if bool(request.headers.get("HX-Request")):
        return Response(status_code=status.HTTP_200_OK, headers={"HX-Refresh": "true"})

    return RedirectResponse(url="/notifications", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/notifications/mark-all-read", response_class=Response, dependencies=[Depends(verify_csrf)])
def mark_all_notifications_read(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Marks all unread notifications in current user audience scope as read."""
    query = db.query(Notification).filter(
        Notification.audience_role == user.role,
        Notification.read_at.is_(None),
    )
    if user.role == "INSTITUTION_ADMIN":
        query = query.filter(Notification.audience_institution_id == user.institution_id)
    else:
        query = query.filter(Notification.audience_institution_id.is_(None))

    unread_items = query.all()
    now_ts = clock_service.now()
    count = 0
    for item in unread_items:
        item.read_at = now_ts
        item.read_by_user_id = user.id
        count += 1

    if count > 0:
        AuditService.append(
            session=db,
            entity_type="NOTIFICATION",
            entity_id=user.id,
            action="NOTIFICATIONS_MARKED_ALL_READ",
            actor_user_id=user.id,
            actor_role=user.role,
            details={"count": count, "audience_role": user.role},
        )
        db.commit()

    return RedirectResponse(url="/notifications", status_code=status.HTTP_303_SEE_OTHER)


# =============================================================================
# 2. System-Wide Alerts Log: GET /alerts
# =============================================================================

@router.get("/alerts", response_class=HTMLResponse)
def list_system_alerts(
    request: Request,
    severity: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    alert_type: Optional[str] = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """System-wide alerts log for staff, auditors, and demo admins."""
    if user.role == "INSTITUTION_ADMIN" and not user.is_demo_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. System alerts log is restricted to administrative staff.",
        )

    query = (
        db.query(Alert, Instrument, Institution)
        .join(Instrument, Alert.instrument_id == Instrument.id)
        .join(Institution, Instrument.institution_id == Institution.id, isouter=True)
    )

    if severity:
        query = query.filter(Alert.severity == severity)
    if status_filter:
        query = query.filter(Alert.status == status_filter)
    if alert_type:
        query = query.filter(Alert.alert_type == alert_type)

    alerts = query.order_by(Alert.created_at.desc()).limit(150).all()

    ctx = base_context(request, user, db)
    ctx.update({
        "alerts": alerts,
        "selected_severity": severity,
        "selected_status": status_filter,
        "selected_alert_type": alert_type,
    })
    return templates.TemplateResponse("alerts/list.html", ctx)


# =============================================================================
# 3. Scheduler Status Panel: GET /scheduler & POST /scheduler/run
# =============================================================================

@router.get("/scheduler", response_class=HTMLResponse)
def view_scheduler_status(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Scheduler status panel showing heartbeat, alert rules card, and run history."""
    if user.role == "INSTITUTION_ADMIN" and not user.is_demo_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Scheduler management is restricted to administrative staff.",
        )

    state = db.query(SchedulerState).filter_by(id=1).first()
    runs = db.query(SchedulerRun).order_by(SchedulerRun.started_at.desc()).limit(20).all()

    config_info = {
        "scheduler_enabled": settings.SCHEDULER_ENABLED,
        "interval_minutes": settings.SCHEDULER_INTERVAL_MINUTES,
        "expiring_window_days": settings.EXPIRING_WINDOW_DAYS,
        "reminder_thresholds": settings.reminder_thresholds,
        "tier2_max_days": settings.TIER2_MAX_DAYS,
        "urgent_max_days": settings.URGENT_MAX_DAYS,
        "overdue_escalation_days": settings.OVERDUE_ESCALATION_DAYS,
        "pending_review_alert_days": settings.PENDING_REVIEW_ALERT_DAYS,
        "unresolved_flag_alert_days": settings.UNRESOLVED_FLAG_ALERT_DAYS,
    }

    ctx = base_context(request, user, db)
    ctx.update({
        "state": state,
        "runs": runs,
        "config_info": config_info,
    })
    return templates.TemplateResponse("scheduler/status.html", ctx)


@router.post("/scheduler/run", response_class=Response, dependencies=[Depends(verify_csrf)])
def trigger_manual_scheduler_run(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Manually triggers an immediate temporal automation cycle (demo admin only)."""
    if not user.is_demo_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Manual scheduler execution requires demo administrator privileges.",
        )

    run = run_temporal_cycle(trigger="MANUAL", caller_session=db)
    return RedirectResponse(url="/scheduler", status_code=status.HTTP_303_SEE_OTHER)
