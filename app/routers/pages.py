from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Request, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.core.templates import templates
from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user, get_optional_current_user, require_roles
from app.models.user import User
from app.models.institution import Institution
from app.models.instrument import Instrument
from app.models.discrepancy import DiscrepancyTicket
from app.models.scrutiny import ScrutinyNote
from app.services.clock_service import clock_service
from app.services.audit_service import verify_chain
from app.services.instrument_fsm import InstrumentState, compute_status_counts, ALL_STATES

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

    # Query institution instruments.
    # Null-Safety & Aggregate Rules:
    # 1. Orders by created_at.desc(); any query ordering by nullable fields (e.g. maturity_date) must use nullslast().
    # 2. Status counts are computed via compute_status_counts using the 11 exact FSM states.
    instruments = (
        db.query(Instrument)
        .filter(Instrument.institution_id == user.institution_id)
        .order_by(Instrument.created_at.desc())
        .all()
    )
    ctx["instruments"] = instruments

    status_counts = compute_status_counts(db, institution_id=user.institution_id)
    ctx["status_counts"] = status_counts
    ctx["total_count"] = status_counts["TOTAL"]
    ctx["submitted_count"] = status_counts[InstrumentState.SUBMITTED.value]
    ctx["active_count"] = status_counts[InstrumentState.ACTIVE.value]
    ctx["flagged_count"] = status_counts[InstrumentState.DISCREPANCY_FLAGGED.value]
    ctx["other_count"] = status_counts["OTHER"]

    return templates.TemplateResponse("roles/institution.html", ctx)


@router.get("/scrutiny", response_class=HTMLResponse)
def scrutiny_workspace(
    request: Request,
    status: Optional[str] = None,
    sort: Optional[str] = "created_at",
    order: Optional[str] = "desc",
    user: User = Depends(require_roles("SCRUTINY_OFFICER")),
    db: Session = Depends(get_db)
):
    """AICTE Scrutiny Officer workstation with active review queue."""
    ctx = common_context(request, user)
    ctx["role_title"] = "Scrutiny Officer Workstation"
    ctx["role_description"] = "Review incoming FDR submissions, inspect automated discrepancy flags, and verify bank provenance."

    status_counts = compute_status_counts(db, institution_id=None)
    ctx["status_counts"] = status_counts
    ctx["total_count"] = status_counts["TOTAL"]
    ctx["submitted_count"] = status_counts[InstrumentState.SUBMITTED.value]
    ctx["under_scrutiny_count"] = status_counts[InstrumentState.UNDER_SCRUTINY.value]
    ctx["flagged_count"] = status_counts[InstrumentState.DISCREPANCY_FLAGGED.value]
    ctx["verified_count"] = status_counts[InstrumentState.VERIFIED.value]
    ctx["active_count"] = status_counts[InstrumentState.ACTIVE.value]
    ctx["other_count"] = status_counts["OTHER"]

    # Build queue query
    query = (
        db.query(Instrument, Institution.name.label("institution_name"))
        .join(Institution, Instrument.institution_id == Institution.id)
    )
    if status and status in ALL_STATES:
        query = query.filter(Instrument.status == status)

    if sort == "maturity_date":
        order_col = Instrument.maturity_date.asc().nullslast() if order == "asc" else Instrument.maturity_date.desc().nullslast()
    elif sort == "principal_amount":
        order_col = Instrument.principal_amount.asc().nullslast() if order == "asc" else Instrument.principal_amount.desc().nullslast()
    else:
        order_col = Instrument.created_at.asc() if order == "asc" else Instrument.created_at.desc()

    queue_rows = query.order_by(order_col).limit(50).all()

    blocking_ticket_counts = dict(
        db.query(DiscrepancyTicket.instrument_id, func.count(DiscrepancyTicket.id))
        .filter(DiscrepancyTicket.status == "OPEN", DiscrepancyTicket.blocking == True)
        .group_by(DiscrepancyTicket.instrument_id)
        .all()
    )

    items = []
    for inst, inst_name in queue_rows:
        items.append({
            "instrument": inst,
            "institution_name": inst_name,
            "blocking_count": blocking_ticket_counts.get(inst.id, 0),
        })

    ctx["items"] = items
    ctx["selected_status"] = status if status in ALL_STATES else None
    ctx["selected_sort"] = sort
    ctx["selected_order"] = order

    return templates.TemplateResponse("roles/scrutiny.html", ctx)


@router.get("/approver", response_class=HTMLResponse)
def approver_workspace(
    request: Request,
    status: Optional[str] = None,
    sort: Optional[str] = "created_at",
    order: Optional[str] = "desc",
    user: User = Depends(require_roles("APPROVER_AUTHORITY")),
    db: Session = Depends(get_db)
):
    """Competent Authority release authorization workstation."""
    ctx = common_context(request, user)
    ctx["role_title"] = "Competent Authority Workstation"
    ctx["role_description"] = "Review compliance checklists and issue dual-key cryptographic authorizations for deposit releases."

    status_counts = compute_status_counts(db, institution_id=None)
    ctx["status_counts"] = status_counts
    ctx["total_count"] = status_counts["TOTAL"]
    ctx["release_requested_count"] = status_counts[InstrumentState.RELEASE_REQUESTED.value]
    ctx["verified_count"] = status_counts[InstrumentState.VERIFIED.value]
    ctx["active_count"] = status_counts[InstrumentState.ACTIVE.value]
    ctx["released_count"] = status_counts[InstrumentState.RELEASED.value]
    ctx["flagged_count"] = status_counts[InstrumentState.DISCREPANCY_FLAGGED.value]
    ctx["other_count"] = status_counts["OTHER"]

    # Queue query: defaults to items needing authority attention
    query = (
        db.query(Instrument, Institution.name.label("institution_name"))
        .join(Institution, Instrument.institution_id == Institution.id)
    )
    if status and status in ALL_STATES:
        query = query.filter(Instrument.status == status)
    else:
        query = query.filter(Instrument.status.in_([
            InstrumentState.UNDER_SCRUTINY.value,
            InstrumentState.DISCREPANCY_FLAGGED.value,
            InstrumentState.VERIFIED.value,
            InstrumentState.RELEASE_REQUESTED.value,
        ]))

    if sort == "maturity_date":
        order_col = Instrument.maturity_date.asc().nullslast() if order == "asc" else Instrument.maturity_date.desc().nullslast()
    elif sort == "principal_amount":
        order_col = Instrument.principal_amount.asc().nullslast() if order == "asc" else Instrument.principal_amount.desc().nullslast()
    else:
        order_col = Instrument.created_at.asc() if order == "asc" else Instrument.created_at.desc()

    queue_rows = query.order_by(order_col).limit(50).all()

    blocking_ticket_counts = dict(
        db.query(DiscrepancyTicket.instrument_id, func.count(DiscrepancyTicket.id))
        .filter(DiscrepancyTicket.status == "OPEN", DiscrepancyTicket.blocking == True)
        .group_by(DiscrepancyTicket.instrument_id)
        .all()
    )

    rejection_recommended_ids = set(
        row[0] for row in db.query(ScrutinyNote.instrument_id)
        .filter(ScrutinyNote.action_recommended == "REJECT")
        .all()
    )

    items = []
    for inst, inst_name in queue_rows:
        items.append({
            "instrument": inst,
            "institution_name": inst_name,
            "blocking_count": blocking_ticket_counts.get(inst.id, 0),
            "rejection_recommended": inst.id in rejection_recommended_ids,
        })

    ctx["items"] = items
    ctx["selected_status"] = status if status in ALL_STATES else None
    ctx["selected_sort"] = sort
    ctx["selected_order"] = order

    return templates.TemplateResponse("roles/approver.html", ctx)


@router.get("/auditor", response_class=HTMLResponse)
def auditor_workspace(
    request: Request,
    user: User = Depends(require_roles("AUDITOR")),
    db: Session = Depends(get_db)
):
    """System Auditor workspace."""
    ctx = common_context(request, user)
    ctx["role_title"] = "Regulatory Audit & Vigilance Console"
    ctx["role_description"] = "Inspect the cryptographic SHA-256 audit log hash chain and verify regulatory compliance."

    status_counts = compute_status_counts(db, institution_id=None)
    ctx["status_counts"] = status_counts
    ctx["total_count"] = status_counts["TOTAL"]
    ctx["active_count"] = status_counts[InstrumentState.ACTIVE.value]
    ctx["flagged_count"] = status_counts[InstrumentState.DISCREPANCY_FLAGGED.value]
    ctx["released_count"] = status_counts[InstrumentState.RELEASED.value]
    ctx["submitted_count"] = status_counts[InstrumentState.SUBMITTED.value]
    ctx["other_count"] = status_counts["OTHER"]

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
