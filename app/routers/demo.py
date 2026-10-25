from fastapi import APIRouter, Request, Response, Depends, Form, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user, verify_csrf
from app.models.user import User
from app.services.clock_service import clock_service

templates = Jinja2Templates(directory="app/templates")
router = APIRouter(prefix="/api/v1/demo", tags=["Demo Time Machine"])


@router.post("/clock", response_class=Response, dependencies=[Depends(verify_csrf)])
def time_machine_clock_control(
    request: Request,
    action: str = Form(...),
    csrf_token: str = Form(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Demo Time Machine clock controller.
    
    Server-side enforcement:
    1. Returns 404 if DEMO_MODE is not active (feature completely unavailable).
    2. Returns 403 if authenticated user does not have is_demo_admin=true.
    3. Enforces double-submit CSRF token validation.
    4. Records tamper-evident CLOCK_ADVANCE or CLOCK_RESET audit events.
    """
    if not settings.DEMO_MODE:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Demo Time Machine endpoint is disabled in non-demo mode."
        )

    if not user.is_demo_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Time Machine controls require demo administrator privileges."
        )

    action_lower = action.strip().lower()

    if action_lower == "+30d":
        clock_service.advance(db, 30, actor_user_id=user.id, actor_role=user.role)
    elif action_lower == "+60d":
        clock_service.advance(db, 60, actor_user_id=user.id, actor_role=user.role)
    elif action_lower == "+90d":
        clock_service.advance(db, 90, actor_user_id=user.id, actor_role=user.role)
    elif action_lower == "+1y" or action_lower == "+365d":
        clock_service.advance(db, 365, actor_user_id=user.id, actor_role=user.role)
    elif action_lower == "reset":
        clock_service.reset(db, actor_user_id=user.id, actor_role=user.role)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown Time Machine action: '{action}'. Allowed: +30d, +60d, +90d, +1y, reset."
        )

    db.commit()

    is_htmx = bool(request.headers.get("HX-Request"))
    simulated_date_str = clock_service.today().strftime("%d %b %Y")
    current_time_str = clock_service.get_current_time().strftime("%d %b %Y, %H:%M:%S UTC")

    if is_htmx:
        # Return updated toolbar and banner partials
        context = {
            "request": request,
            "current_time": current_time_str,
            "is_simulated": clock_service.is_simulated,
            "simulated_date_str": simulated_date_str if clock_service.is_simulated else None,
            "user": user,
            "demo_mode": settings.DEMO_MODE,
            "csrf_token": request.cookies.get(settings.CSRF_COOKIE_NAME, ""),
        }
        # Include HX-Trigger header so client-side listeners can refresh components
        headers = {"HX-Trigger": "clockUpdated"}
        return templates.TemplateResponse("components/time_machine_toolbar.html", context, headers=headers)

    return JSONResponse({
        "status": "success",
        "action": action,
        "is_simulated": clock_service.is_simulated,
        "offset_seconds": clock_service.offset_seconds,
        "current_time": current_time_str,
        "simulated_date": simulated_date_str,
    })
