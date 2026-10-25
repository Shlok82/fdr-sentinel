from typing import Optional
from fastapi import APIRouter, Request, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user, get_optional_current_user, require_roles
from app.models.user import User
from app.models.instrument import Instrument
from app.services.clock_service import clock_service
from app.services.audit_service import verify_chain

templates = Jinja2Templates(directory="app/templates")
router = APIRouter(include_in_schema=False)


def common_context(request: Request, user: Optional[User]) -> dict:
    """Constructs baseline template context available across all pages."""
    csrf_token = request.cookies.get(settings.CSRF_COOKIE_NAME, "")
    return {
        "request": request,
        "user": user,
        "current_time": clock_service.get_current_time().strftime("%d %b %Y, %H:%M:%S UTC"),
        "is_simulated": clock_service.is_simulated,
        "demo_mode": settings.DEMO_MODE,
        "simulated_date_str": clock_service.today().strftime("%d %b %Y") if clock_service.is_simulated else None,
        "csrf_token": csrf_token,
    }


@router.get("/", response_class=HTMLResponse)
def index_page(
    request: Request,
    user: Optional[User] = Depends(get_optional_current_user)
):
    """Landing page: redirects authenticated users to their role workspace."""
    if user:
        mapping = {
            "INSTITUTION_ADMIN": "/institution",
            "SCRUTINY_OFFICER": "/scrutiny",
            "APPROVER_AUTHORITY": "/approver",
            "AUDITOR": "/auditor",
        }
        return RedirectResponse(url=mapping.get(user.role, "/dashboard"), status_code=status.HTTP_302_FOUND)

    ctx = common_context(request, user)
    return templates.TemplateResponse("index.html", ctx)


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_redirect(user: User = Depends(get_current_user)):
    """Central router directing authenticated users to their role-specific workspace."""
    mapping = {
        "INSTITUTION_ADMIN": "/institution",
        "SCRUTINY_OFFICER": "/scrutiny",
        "APPROVER_AUTHORITY": "/approver",
        "AUDITOR": "/auditor",
    }
    return RedirectResponse(url=mapping.get(user.role, "/institution"), status_code=status.HTTP_302_FOUND)


@router.get("/institution", response_class=HTMLResponse)
def institution_workspace(
    request: Request,
    user: User = Depends(require_roles("INSTITUTION_ADMIN")),
    db: Session = Depends(get_db)
):
    """Institution administrator workspace."""
    ctx = common_context(request, user)
    ctx["role_title"] = "Institution Portal — FDR Custody & Submissions"
    ctx["role_description"] = "Submit new Fixed Deposit Receipts, track verification status, and manage deposit custody."

    instruments = (
        db.query(Instrument)
        .filter(Instrument.institution_id == user.institution_id)
        .order_by(Instrument.created_at.desc())
        .all()
    )
    ctx["instruments"] = instruments
    ctx["total_count"] = len(instruments)
    ctx["submitted_count"] = sum(1 for inst in instruments if inst.status == "SUBMITTED")
    ctx["active_count"] = sum(1 for inst in instruments if inst.status == "ACTIVE")
    ctx["flagged_count"] = sum(1 for inst in instruments if inst.status in ("DISCREPANCY", "FLAGGED"))

    return templates.TemplateResponse("roles/institution.html", ctx)


@router.get("/scrutiny", response_class=HTMLResponse)
def scrutiny_workspace(
    request: Request,
    user: User = Depends(require_roles("SCRUTINY_OFFICER"))
):
    """AICTE Scrutiny Officer workstation."""
    ctx = common_context(request, user)
    ctx["role_title"] = "Scrutiny Officer Workstation"
    ctx["role_description"] = "Review incoming FDR submissions, inspect automated discrepancy flags, and verify bank provenance."
    return templates.TemplateResponse("roles/scrutiny.html", ctx)


@router.get("/approver", response_class=HTMLResponse)
def approver_workspace(
    request: Request,
    user: User = Depends(require_roles("APPROVER_AUTHORITY"))
):
    """Competent Authority release authorization workstation."""
    ctx = common_context(request, user)
    ctx["role_title"] = "Competent Authority Workstation"
    ctx["role_description"] = "Review compliance checklists and issue dual-key cryptographic authorizations for deposit releases."
    return templates.TemplateResponse("roles/approver.html", ctx)


@router.get("/auditor", response_class=HTMLResponse)
def auditor_workspace(
    request: Request,
    user: User = Depends(require_roles("AUDITOR"))
):
    """System Auditor workspace."""
    ctx = common_context(request, user)
    ctx["role_title"] = "Regulatory Audit & Vigilance Console"
    ctx["role_description"] = "Inspect the cryptographic SHA-256 audit log hash chain and verify regulatory compliance."
    return templates.TemplateResponse("roles/auditor.html", ctx)


@router.get("/audit/verify", response_class=HTMLResponse)
def audit_verify_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Tamper-detection audit verification view.
    
    Access Control per SPEC §3:
    - AUDITOR: Full audit report (status, rows checked, first broken id, reason, simulated time).
    - SCRUTINY_OFFICER, APPROVER_AUTHORITY, demo.admin: Status-only summary (OK/BROKEN and broken ID).
    - INSTITUTION_ADMIN: Strictly HTTP 403 Forbidden.
    
    Read-only: Never writes audit entries.
    """
    if user.role == "INSTITUTION_ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Audit verification is restricted to regulatory officers."
        )

    # Perform linear-time verification of the audit log hash chain
    result = verify_chain(db)

    ctx = common_context(request, user)
    ctx["verify_result"] = result
    ctx["is_full_auditor"] = (user.role == "AUDITOR" and not user.is_demo_admin)
    ctx["checked_at"] = clock_service.get_current_time().strftime("%d %b %Y, %H:%M:%S UTC")

    return templates.TemplateResponse("audit/verify.html", ctx)
