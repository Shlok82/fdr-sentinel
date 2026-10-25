import uuid
from typing import Optional
from fastapi import APIRouter, Request, Response, Depends, Form, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import get_db
from app.core.security import (
    verify_password,
    dummy_verify_password,
    create_access_token,
    generate_csrf_token,
    verify_csrf_token,
    is_safe_redirect_url,
)
from app.core.dependencies import get_optional_current_user, get_client_ip
from app.models.user import User
from app.services.audit_service import AuditService
from app.services.clock_service import clock_service

templates = Jinja2Templates(directory="app/templates")
router = APIRouter(tags=["Authentication"])

SYSTEM_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


def default_dashboard_for_role(role: str) -> str:
    """Returns the landing route for a specific user role."""
    mapping = {
        "INSTITUTION_ADMIN": "/institution",
        "SCRUTINY_OFFICER": "/scrutiny",
        "APPROVER_AUTHORITY": "/approver",
        "AUDITOR": "/auditor",
    }
    return mapping.get(role, "/dashboard")


@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    next: Optional[str] = None,
    user: Optional[User] = Depends(get_optional_current_user)
):
    """Renders the login page with double-submit CSRF token."""
    if user:
        return RedirectResponse(url=default_dashboard_for_role(user.role), status_code=status.HTTP_302_FOUND)

    # Obtain or generate CSRF token
    csrf_token = request.cookies.get(settings.CSRF_COOKIE_NAME)
    set_csrf_cookie = False
    if not csrf_token:
        csrf_token = generate_csrf_token()
        set_csrf_cookie = True

    context = {
        "request": request,
        "csrf_token": csrf_token,
        "next": next or "",
        "current_time": clock_service.get_current_time().strftime("%d %b %Y, %H:%M:%S UTC"),
        "is_simulated": clock_service.is_simulated,
        "demo_mode": settings.DEMO_MODE,
        "simulated_date_str": clock_service.today().strftime("%d %b %Y") if clock_service.is_simulated else None,
        "error": None,
    }

    response = templates.TemplateResponse("auth/login.html", context)
    if set_csrf_cookie:
        response.set_cookie(
            key=settings.CSRF_COOKIE_NAME,
            value=csrf_token,
            httponly=False,  # Accessible to scripts for HTMX headers
            samesite="lax",
            secure=(settings.APP_ENV != "development"),
        )
    return response


@router.post("/login")
def login_action(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: Optional[str] = Form(None),
    next: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    """Processes login credentials, audits attempts, and sets session cookie."""
    client_ip = get_client_ip(request)
    cookie_csrf = request.cookies.get(settings.CSRF_COOKIE_NAME)

    # 1. Enforce CSRF verification
    if not verify_csrf_token(csrf_token, cookie_csrf):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed. Missing or invalid security token."
        )

    # 2. Look up user by username
    user = db.query(User).filter(User.username == username.strip()).first()

    # 3. Handle unknown user with timing mitigation and committed audit log
    if not user:
        dummy_verify_password(password)
        AuditService.append(
            db,
            entity_type="USER",
            entity_id=SYSTEM_USER_ID,
            action="LOGIN_FAILED",
            actor_user_id=None,
            actor_role="SYSTEM",
            details={"attempted_username": username.strip()},
            actor_ip=client_ip
        )
        db.commit()

        context = {
            "request": request,
            "csrf_token": cookie_csrf,
            "next": next or "",
            "current_time": clock_service.get_current_time().strftime("%d %b %Y, %H:%M:%S UTC"),
            "is_simulated": clock_service.is_simulated,
            "demo_mode": settings.DEMO_MODE,
            "simulated_date_str": clock_service.today().strftime("%d %b %Y") if clock_service.is_simulated else None,
            "error": "Invalid username or password.",
        }
        return templates.TemplateResponse("auth/login.html", context, status_code=status.HTTP_401_UNAUTHORIZED)

    # 4. Handle inactive user
    if not user.is_active:
        AuditService.append(
            db,
            entity_type="USER",
            entity_id=user.id,
            action="LOGIN_FAILED",
            actor_user_id=user.id,
            actor_role=user.role,
            details={"reason": "ACCOUNT_DEACTIVATED", "attempted_username": username.strip()},
            actor_ip=client_ip
        )
        db.commit()

        context = {
            "request": request,
            "csrf_token": cookie_csrf,
            "next": next or "",
            "current_time": clock_service.get_current_time().strftime("%d %b %Y, %H:%M:%S UTC"),
            "is_simulated": clock_service.is_simulated,
            "demo_mode": settings.DEMO_MODE,
            "simulated_date_str": clock_service.today().strftime("%d %b %Y") if clock_service.is_simulated else None,
            "error": "Account is deactivated. Please contact your AICTE administrator.",
        }
        return templates.TemplateResponse("auth/login.html", context, status_code=status.HTTP_401_UNAUTHORIZED)

    # 5. Verify password
    if not verify_password(password, user.hashed_password):
        AuditService.append(
            db,
            entity_type="USER",
            entity_id=user.id,
            action="LOGIN_FAILED",
            actor_user_id=user.id,
            actor_role=user.role,
            details={"attempted_username": username.strip()},
            actor_ip=client_ip
        )
        db.commit()

        context = {
            "request": request,
            "csrf_token": cookie_csrf,
            "next": next or "",
            "current_time": clock_service.get_current_time().strftime("%d %b %Y, %H:%M:%S UTC"),
            "is_simulated": clock_service.is_simulated,
            "demo_mode": settings.DEMO_MODE,
            "simulated_date_str": clock_service.today().strftime("%d %b %Y") if clock_service.is_simulated else None,
            "error": "Invalid username or password.",
        }
        return templates.TemplateResponse("auth/login.html", context, status_code=status.HTTP_401_UNAUTHORIZED)

    # 6. Success: record audit entry and issue token
    AuditService.append(
        db,
        entity_type="USER",
        entity_id=user.id,
        action="LOGIN_SUCCESS",
        actor_user_id=user.id,
        actor_role=user.role,
        details={},
        actor_ip=client_ip
    )
    db.commit()

    token = create_access_token(user.id)
    redirect_target = next if is_safe_redirect_url(next) else default_dashboard_for_role(user.role)

    redirect_response = RedirectResponse(url=redirect_target, status_code=status.HTTP_302_FOUND)
    redirect_response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=(settings.APP_ENV != "development"),
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
    return redirect_response


@router.post("/logout")
def logout_action(
    request: Request,
    user: Optional[User] = Depends(get_optional_current_user),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    """Logs out user, audits the action, and clears session cookie (POST only)."""
    client_ip = get_client_ip(request)
    cookie_csrf = request.cookies.get(settings.CSRF_COOKIE_NAME)

    # Verify CSRF token
    if not verify_csrf_token(csrf_token, cookie_csrf):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed."
        )

    if user:
        AuditService.append(
            db,
            entity_type="USER",
            entity_id=user.id,
            action="LOGOUT",
            actor_user_id=user.id,
            actor_role=user.role,
            details={},
            actor_ip=client_ip
        )
        db.commit()

    response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie(
        key=settings.SESSION_COOKIE_NAME,
        httponly=True,
        samesite="lax",
        secure=(settings.APP_ENV != "development"),
    )
    return response
