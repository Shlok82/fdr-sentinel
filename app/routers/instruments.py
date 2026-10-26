import uuid
from typing import Optional
from fastapi import APIRouter, Request, Response, Depends, Form, File, UploadFile, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse
from urllib.parse import quote_plus
import io
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import get_db
from app.core.templates import templates
from app.core.dependencies import get_current_user, require_roles, verify_csrf, get_client_ip
from app.models.user import User
from app.models.institution import Institution
from app.models.instrument import Instrument
from app.models.document import Document
from app.models.provenance import FieldProvenance
from app.models.discrepancy import DiscrepancyTicket
from app.models.scrutiny import ScrutinyNote
from app.models.verification import VerificationRecord
from app.models.audit import AuditLog
from app.models.alert import Alert
from app.models.release import ReleaseRequest
from app.schemas.instrument import FDRSubmissionForm
from app.services.submission_service import FDRSubmissionService
from app.services.document_service import DocumentService, validate_file_content
from app.services.audit_service import AuditService
from app.services.clock_service import clock_service
from app.services.instrument_fsm import (
    get_instrument_timeline,
    InstrumentState,
    compute_status_counts,
    ALL_STATES,
    TERMINAL_STATES,
    Actor,
)
from app.services.verification.mock_bank import MockFailureMode
from app.services.scrutiny_service import (
    ScrutinyService,
    ScrutinyError,
    ScrutinyGuardError,
    ScrutinyRoleError,
    StaleStateError,
    JustificationError,
)
from app.services.renewal_service import (
    start_renewal,
    RenewalError,
    RenewalNotFoundError,
    RenewalStateError,
    LiveChildExistsError,
)
from app.services.release_service import (
    request_release,
    record_recommendation,
    decide_release,
    ReleaseError,
    ReleaseNotFoundError,
    ReleasePermissionError,
    ReleaseStateError,
    ReleaseChecklistIncompleteError,
    SecondKeyMissingError,
    ReleaseAlreadyDecidedError,
)
from app.services.lineage import get_lineage

router = APIRouter(tags=["Instruments & Custody Vault"])


@router.get("/institution/instruments", response_class=HTMLResponse)
def list_institution_instruments(
    request: Request,
    status: Optional[str] = None,
    user: User = Depends(require_roles("INSTITUTION_ADMIN")),
    db: Session = Depends(get_db)
):
    """Lists all FDR instruments belonging strictly to the caller's institution, optionally filtered by status."""
    institution = db.query(Institution).filter(Institution.id == user.institution_id).first()
    query = (
        db.query(Instrument)
        .filter(Instrument.institution_id == user.institution_id)
    )
    if status and status in ALL_STATES:
        query = query.filter(Instrument.status == status)

    instruments = query.order_by(Instrument.created_at.desc()).all()
    status_counts = compute_status_counts(db, institution_id=user.institution_id)

    from app.routers.temporal import get_unread_notifications_count
    unread_count = get_unread_notifications_count(db, user)

    context = {
        "request": request,
        "user": user,
        "institution": institution,
        "instruments": instruments,
        "status_counts": status_counts,
        "selected_status": status if status in ALL_STATES else None,
        "unread_notifications_count": unread_count,
        "current_time": clock_service.display_time,
        "is_simulated": clock_service.is_simulated,
        "demo_mode": settings.DEMO_MODE,
        "simulated_date_str": clock_service.display_simulated_date,
        "csrf_token": request.cookies.get(settings.CSRF_COOKIE_NAME, ""),
    }
    return templates.TemplateResponse("instruments/list.html", context)


@router.get("/institution/instruments/new", response_class=HTMLResponse)
def new_instrument_form(
    request: Request,
    user: User = Depends(require_roles("INSTITUTION_ADMIN")),
    db: Session = Depends(get_db)
):
    """Renders the FDR Submission form for institution administrator."""
    institution = db.query(Institution).filter(Institution.id == user.institution_id).first()
    from app.routers.temporal import get_unread_notifications_count
    unread_count = get_unread_notifications_count(db, user)

    context = {
        "request": request,
        "user": user,
        "institution": institution,
        "form_data": {},
        "errors": {},
        "unread_notifications_count": unread_count,
        "current_time": clock_service.display_time,
        "is_simulated": clock_service.is_simulated,
        "demo_mode": settings.DEMO_MODE,
        "max_upload_mb": settings.MAX_UPLOAD_MB,
        "simulated_date_str": clock_service.display_simulated_date,
        "csrf_token": request.cookies.get(settings.CSRF_COOKIE_NAME, ""),
    }
    return templates.TemplateResponse("instruments/new.html", context)


@router.post("/institution/instruments/new", response_class=Response, dependencies=[Depends(verify_csrf)])
async def submit_instrument_action(
    request: Request,
    fdr_number: Optional[str] = Form(None),
    bank_name: Optional[str] = Form(None),
    bank_branch: Optional[str] = Form(None),
    ifsc_code: Optional[str] = Form(None),
    principal_amount: Optional[str] = Form(None),
    maturity_amount: Optional[str] = Form(None),
    interest_rate_percent: Optional[str] = Form(None),
    issue_date: Optional[str] = Form(None),
    maturity_date: Optional[str] = Form(None),
    tenure_months: Optional[str] = Form(None),
    joint_account_holder: Optional[str] = Form(None),
    scan_file: UploadFile = File(...),
    csrf_token: Optional[str] = Form(None),
    user: User = Depends(require_roles("INSTITUTION_ADMIN")),
    db: Session = Depends(get_db)
):
    """Processes FDR submission, validates structure, encrypts scan, and commits atomically."""
    client_ip = get_client_ip(request)

    # 1. Validate form fields structure
    form = FDRSubmissionForm(
        fdr_number=fdr_number,
        bank_name=bank_name,
        bank_branch=bank_branch,
        ifsc_code=ifsc_code,
        principal_amount=principal_amount,
        maturity_amount=maturity_amount,
        interest_rate_percent=interest_rate_percent,
        issue_date=issue_date,
        maturity_date=maturity_date,
        tenure_months=tenure_months,
        joint_account_holder=joint_account_holder,
    )

    # 2. Read and validate uploaded file
    file_bytes = b""
    file_name = scan_file.filename or "upload.bin"
    file_error = None

    try:
        # Read in chunks up to limit + 1 byte to prevent unbounded buffering
        limit_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
        chunks = []
        total_read = 0
        while chunk := await scan_file.read(1024 * 64):
            total_read += len(chunk)
            if total_read > limit_bytes:
                file_error = f"Uploaded file exceeds the maximum allowed limit of {settings.MAX_UPLOAD_MB} MB."
                break
            chunks.append(chunk)

        if not file_error:
            file_bytes = b"".join(chunks)
            if len(file_bytes) == 0:
                file_error = "Uploaded document is empty (0 bytes)."
            else:
                validate_file_content(file_bytes, file_name)
    except ValueError as exc:
        file_error = str(exc)
    except Exception as exc:
        file_error = f"Error processing uploaded file: {str(exc)}"

    if file_error:
        form.errors["scan_file"] = file_error

    # If any structural or file validation error occurred, re-render form with errors
    if not form.is_valid:
        institution = db.query(Institution).filter(Institution.id == user.institution_id).first()
        context = {
            "request": request,
            "user": user,
            "institution": institution,
            "form_data": form.raw_fields,
            "errors": form.errors,
            "current_time": clock_service.display_time,
            "is_simulated": clock_service.is_simulated,
            "demo_mode": settings.DEMO_MODE,
            "max_upload_mb": settings.MAX_UPLOAD_MB,
            "simulated_date_str": clock_service.display_simulated_date,
            "csrf_token": request.cookies.get(settings.CSRF_COOKIE_NAME, ""),
        }
        return templates.TemplateResponse("instruments/new.html", context, status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)

    # 3. Process atomic submission
    try:
        instrument = FDRSubmissionService.submit_instrument(
            db=db,
            form=form,
            file_bytes=file_bytes,
            raw_filename=file_name,
            user=user,
            actor_ip=client_ip,
        )
    except Exception as exc:
        institution = db.query(Institution).filter(Institution.id == user.institution_id).first()
        form.errors["general"] = f"Failed to submit instrument: {str(exc)}"
        context = {
            "request": request,
            "user": user,
            "institution": institution,
            "form_data": form.raw_fields,
            "errors": form.errors,
            "current_time": clock_service.display_time,
            "is_simulated": clock_service.is_simulated,
            "demo_mode": settings.DEMO_MODE,
            "max_upload_mb": settings.MAX_UPLOAD_MB,
            "simulated_date_str": clock_service.display_simulated_date,
            "csrf_token": request.cookies.get(settings.CSRF_COOKIE_NAME, ""),
        }
        return templates.TemplateResponse("instruments/new.html", context, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # 4. Success: POST-redirect-GET to instrument detail page
    return RedirectResponse(
        url=f"/institution/instruments/{instrument.id}",
        status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/institution/instruments/{parent_id}/renew", response_class=HTMLResponse)
def get_renew_instrument_form(
    request: Request,
    parent_id: uuid.UUID,
    user: User = Depends(require_roles("INSTITUTION_ADMIN")),
    db: Session = Depends(get_db),
):
    """Renders the FDR Renewal Rollover form pre-filling bank parameters from the parent."""
    parent = db.query(Instrument).filter(Instrument.id == parent_id).first()
    if not parent or parent.institution_id != user.institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Instrument not found.")

    if parent.status == "RELEASE_REQUESTED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot start renewal: a security deposit release request is currently in progress."
        )

    if parent.status not in ("EXPIRING", "MATURED"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot start renewal: instrument is in status '{parent.status}', expected 'EXPIRING' or 'MATURED'."
        )

    live_child = (
        db.query(Instrument)
        .filter(Instrument.previous_instrument_id == parent.id, Instrument.status != "REJECTED")
        .first()
    )
    if live_child:
        child_fdr = live_child.fdr_number or f"ID {live_child.id}"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"A renewal is already in progress for this deposit (Child: {child_fdr}, status: {live_child.status})."
        )

    institution = db.query(Institution).filter(Institution.id == user.institution_id).first()
    from app.routers.temporal import get_unread_notifications_count
    unread_count = get_unread_notifications_count(db, user)

    form_data = {
        "bank_name": parent.bank_name or "",
        "bank_branch": parent.bank_branch or "",
        "ifsc_code": parent.ifsc_code or "",
        "joint_account_holder": parent.joint_account_holder or "",
    }

    context = {
        "request": request,
        "user": user,
        "institution": institution,
        "form_data": form_data,
        "errors": {},
        "parent_instrument": parent,
        "is_renewal": True,
        "form_action": f"/institution/instruments/{parent.id}/renew",
        "unread_notifications_count": unread_count,
        "current_time": clock_service.display_time,
        "is_simulated": clock_service.is_simulated,
        "demo_mode": settings.DEMO_MODE,
        "max_upload_mb": settings.MAX_UPLOAD_MB,
        "simulated_date_str": clock_service.display_simulated_date,
        "csrf_token": request.cookies.get(settings.CSRF_COOKIE_NAME, ""),
    }
    return templates.TemplateResponse("instruments/new.html", context)


@router.post("/institution/instruments/{parent_id}/renew", response_class=Response, dependencies=[Depends(verify_csrf)])
async def submit_renew_instrument_action(
    request: Request,
    parent_id: uuid.UUID,
    fdr_number: Optional[str] = Form(None),
    bank_name: Optional[str] = Form(None),
    bank_branch: Optional[str] = Form(None),
    ifsc_code: Optional[str] = Form(None),
    principal_amount: Optional[str] = Form(None),
    maturity_amount: Optional[str] = Form(None),
    interest_rate_percent: Optional[str] = Form(None),
    issue_date: Optional[str] = Form(None),
    maturity_date: Optional[str] = Form(None),
    tenure_months: Optional[str] = Form(None),
    joint_account_holder: Optional[str] = Form(None),
    scan_file: UploadFile = File(...),
    csrf_token: Optional[str] = Form(None),
    user: User = Depends(require_roles("INSTITUTION_ADMIN")),
    db: Session = Depends(get_db),
):
    """Processes renewal FDR submission, attaching child to parent and recording RENEWAL_INITIATED."""
    client_ip = get_client_ip(request)

    parent = db.query(Instrument).filter(Instrument.id == parent_id).first()
    if not parent or parent.institution_id != user.institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Instrument not found.")

    form = FDRSubmissionForm(
        fdr_number=fdr_number,
        bank_name=bank_name,
        bank_branch=bank_branch,
        ifsc_code=ifsc_code,
        principal_amount=principal_amount,
        maturity_amount=maturity_amount,
        interest_rate_percent=interest_rate_percent,
        issue_date=issue_date,
        maturity_date=maturity_date,
        tenure_months=tenure_months,
        joint_account_holder=joint_account_holder,
    )

    file_bytes = b""
    file_name = scan_file.filename or "upload.bin"
    file_error = None

    try:
        limit_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
        chunks = []
        total_read = 0
        while chunk := await scan_file.read(1024 * 64):
            total_read += len(chunk)
            if total_read > limit_bytes:
                file_error = f"Uploaded file exceeds the maximum allowed limit of {settings.MAX_UPLOAD_MB} MB."
                break
            chunks.append(chunk)

        if not file_error:
            file_bytes = b"".join(chunks)
            if len(file_bytes) == 0:
                file_error = "Uploaded document is empty (0 bytes)."
            else:
                validate_file_content(file_bytes, file_name)
    except ValueError as exc:
        file_error = str(exc)
    except Exception as exc:
        file_error = f"Error processing uploaded file: {str(exc)}"

    if file_error:
        form.errors["scan_file"] = file_error

    if not form.is_valid:
        institution = db.query(Institution).filter(Institution.id == user.institution_id).first()
        context = {
            "request": request,
            "user": user,
            "institution": institution,
            "form_data": form.raw_fields,
            "errors": form.errors,
            "parent_instrument": parent,
            "is_renewal": True,
            "form_action": f"/institution/instruments/{parent.id}/renew",
            "current_time": clock_service.display_time,
            "is_simulated": clock_service.is_simulated,
            "demo_mode": settings.DEMO_MODE,
            "max_upload_mb": settings.MAX_UPLOAD_MB,
            "simulated_date_str": clock_service.display_simulated_date,
            "csrf_token": request.cookies.get(settings.CSRF_COOKIE_NAME, ""),
        }
        return templates.TemplateResponse("instruments/new.html", context, status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)

    try:
        child = start_renewal(
            session=db,
            parent_id=parent.id,
            form_data=form,
            upload=(file_bytes, file_name),
            actor=user,
            actor_ip=client_ip,
        )
        db.commit()
    except RenewalNotFoundError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Instrument not found.")
    except (RenewalStateError, LiveChildExistsError) as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        db.rollback()
        institution = db.query(Institution).filter(Institution.id == user.institution_id).first()
        form.errors["general"] = f"Failed to submit renewal instrument: {str(exc)}"
        context = {
            "request": request,
            "user": user,
            "institution": institution,
            "form_data": form.raw_fields,
            "errors": form.errors,
            "parent_instrument": parent,
            "is_renewal": True,
            "form_action": f"/institution/instruments/{parent.id}/renew",
            "current_time": clock_service.display_time,
            "is_simulated": clock_service.is_simulated,
            "demo_mode": settings.DEMO_MODE,
            "max_upload_mb": settings.MAX_UPLOAD_MB,
            "simulated_date_str": clock_service.display_simulated_date,
            "csrf_token": request.cookies.get(settings.CSRF_COOKIE_NAME, ""),
        }
        return templates.TemplateResponse("instruments/new.html", context, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

    return RedirectResponse(
        url=f"/institution/instruments/{child.id}",
        status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/institution/instruments/{instrument_id}", response_class=HTMLResponse)
def view_institution_instrument_detail(
    request: Request,
    instrument_id: uuid.UUID,
    user: User = Depends(require_roles("INSTITUTION_ADMIN")),
    db: Session = Depends(get_db)
):
    """Displays instrument details strictly scoped to the caller's institution."""
    instrument = (
        db.query(Instrument)
        .filter(Instrument.id == instrument_id, Instrument.institution_id == user.institution_id)
        .first()
    )
    if not instrument:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="FDR Instrument not found or access unauthorized."
        )

    institution = db.query(Institution).filter(Institution.id == instrument.institution_id).first()
    provenance_rows = db.query(FieldProvenance).filter(FieldProvenance.instrument_id == instrument.id).all()
    # Scoping: institution users cannot view or download internal RELEASE_EVIDENCE
    documents = (
        db.query(Document)
        .filter(Document.instrument_id == instrument.id, Document.document_type != "RELEASE_EVIDENCE")
        .all()
    )

    # Map provenance by field_name for easy template lookups
    prov_map = {p.field_name: p for p in provenance_rows}
    timeline = get_instrument_timeline(db, instrument)
    tickets = (
        db.query(DiscrepancyTicket)
        .filter(DiscrepancyTicket.instrument_id == instrument.id)
        .order_by(DiscrepancyTicket.created_at.desc())
        .all()
    )
    instrument_alerts = (
        db.query(Alert)
        .filter(Alert.instrument_id == instrument.id)
        .order_by(Alert.created_at.desc())
        .all()
    )
    days_to_maturity = None
    if instrument.maturity_date:
        days_to_maturity = (instrument.maturity_date - clock_service.today()).days

    lineage = get_lineage(db, instrument)
    live_child = (
        db.query(Instrument)
        .filter(Instrument.previous_instrument_id == instrument.id, Instrument.status != "REJECTED")
        .first()
    )
    can_start_renewal = (
        instrument.status in ("EXPIRING", "MATURED")
        and live_child is None
        and instrument.status != "RELEASE_REQUESTED"
    )

    latest_release_request = (
        db.query(ReleaseRequest)
        .filter(ReleaseRequest.instrument_id == instrument.id)
        .order_by(ReleaseRequest.created_at.desc())
        .first()
    )
    has_pending_release = (
        latest_release_request is not None and latest_release_request.authority_decision == "PENDING"
    )
    can_request_release = (
        instrument.status in ("ACTIVE", "EXPIRING", "MATURED")
        and not has_pending_release
        and live_child is None
    )

    from app.routers.temporal import get_unread_notifications_count
    unread_count = get_unread_notifications_count(db, user)

    context = {
        "request": request,
        "user": user,
        "institution": institution,
        "instrument": instrument,
        "prov_map": prov_map,
        "documents": documents,
        "timeline": timeline,
        "tickets": tickets,
        "alerts": instrument_alerts,
        "days_to_maturity": days_to_maturity,
        "lineage": lineage,
        "live_child": live_child,
        "can_start_renewal": can_start_renewal,
        "latest_release_request": latest_release_request,
        "can_request_release": can_request_release,
        "unread_notifications_count": unread_count,
        "current_time": clock_service.display_time,
        "is_simulated": clock_service.is_simulated,
        "demo_mode": settings.DEMO_MODE,
        "simulated_date_str": clock_service.display_simulated_date,
        "csrf_token": request.cookies.get(settings.CSRF_COOKIE_NAME, ""),
    }
    return templates.TemplateResponse("instruments/detail.html", context)


@router.get("/institution/instruments/{instrument_id}/documents/{document_id}/download")
def download_institution_document(
    request: Request,
    instrument_id: uuid.UUID,
    document_id: uuid.UUID,
    user: User = Depends(require_roles("INSTITUTION_ADMIN")),
    db: Session = Depends(get_db)
):
    """Streams decrypted document for institution user with audit logging and integrity check."""
    client_ip = get_client_ip(request)

    # 1. Verify institution tenancy scoping
    instrument = (
        db.query(Instrument)
        .filter(Instrument.id == instrument_id, Instrument.institution_id == user.institution_id)
        .first()
    )
    if not instrument:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    document = (
        db.query(Document)
        .filter(Document.id == document_id, Document.instrument_id == instrument.id)
        .first()
    )
    if not document or document.document_type == "RELEASE_EVIDENCE":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    # 2. Decrypt in memory and verify SHA-256
    try:
        plaintext = DocumentService.read_and_decrypt_document(document)
    except ValueError as exc:
        # Integrity verification failure -> audit and abort
        AuditService.append(
            db,
            entity_type="DOCUMENT",
            entity_id=document.id,
            action="DOCUMENT_INTEGRITY_FAILURE",
            actor_user_id=user.id,
            actor_role=user.role,
            details={"error": str(exc), "instrument_id": str(instrument.id)},
            actor_ip=client_ip,
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Document integrity verification failed.")
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Decryption failed: {str(exc)}")

    # 3. Log download audit event
    AuditService.append(
        db,
        entity_type="DOCUMENT",
        entity_id=document.id,
        action="DOCUMENT_DOWNLOADED",
        actor_user_id=user.id,
        actor_role=user.role,
        details={"file_name": document.file_name, "instrument_id": str(instrument.id)},
        actor_ip=client_ip,
    )
    db.commit()

    # 4. Stream response with security headers
    headers = {
        "Content-Disposition": f'attachment; filename="{document.file_name}"',
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
    }
    return StreamingResponse(io.BytesIO(plaintext), media_type=document.file_mime_type, headers=headers)


# -----------------------------------------------------------------------------
# Staff Routes: Case Page, Review Actions, and Document Inspection
# -----------------------------------------------------------------------------

def _render_staff_case_page(
    request: Request,
    instrument_id: uuid.UUID,
    user: User,
    db: Session,
    action_error: Optional[str] = None,
    action_success: Optional[str] = None,
    status_code: int = 200,
) -> HTMLResponse:
    instrument = db.query(Instrument).filter(Instrument.id == instrument_id).first()
    if not instrument:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Instrument not found.")

    institution = db.query(Institution).filter(Institution.id == instrument.institution_id).first()
    provenance_rows = db.query(FieldProvenance).filter(FieldProvenance.instrument_id == instrument.id).all()
    documents = db.query(Document).filter(Document.instrument_id == instrument.id).all()
    prov_map = {p.field_name: p for p in provenance_rows}
    timeline = get_instrument_timeline(db, instrument)
    tickets = (
        db.query(DiscrepancyTicket)
        .filter(DiscrepancyTicket.instrument_id == instrument.id)
        .order_by(DiscrepancyTicket.created_at.desc())
        .all()
    )

    scrutiny_notes = (
        db.query(ScrutinyNote)
        .filter(ScrutinyNote.instrument_id == instrument.id)
        .order_by(ScrutinyNote.created_at.desc())
        .all()
    )

    verification_records = (
        db.query(VerificationRecord)
        .filter(VerificationRecord.instrument_id == instrument.id)
        .order_by(VerificationRecord.executed_at.desc(), VerificationRecord.id.desc())
        .all()
    )

    audit_events = (
        db.query(AuditLog)
        .filter(AuditLog.entity_type == "INSTRUMENT", AuditLog.entity_id == instrument.id)
        .order_by(AuditLog.id.desc())
        .all()
    )

    officer_ids = {n.officer_id for n in scrutiny_notes if n.officer_id}
    officer_ids.update({a.actor_user_id for a in audit_events if a.actor_user_id})
    user_map = {}
    if officer_ids:
        users = db.query(User).filter(User.id.in_(officer_ids)).all()
        user_map = {u.id: u.username for u in users}

    latest_check_run = (
        db.query(AuditLog)
        .filter(
            AuditLog.entity_type == "INSTRUMENT",
            AuditLog.entity_id == instrument.id,
            AuditLog.action == "DISCREPANCY_CHECK_RUN",
        )
        .order_by(AuditLog.id.desc())
        .first()
    )
    not_evaluated = {}
    if latest_check_run and latest_check_run.canonical_payload:
        not_evaluated = latest_check_run.canonical_payload.get("details", {}).get("not_evaluated", {})

    is_demo_admin = getattr(user, "is_demo_admin", False)

    # Permission flags for action panel
    can_start_scrutiny = (
        user.role == "SCRUTINY_OFFICER"
        and instrument.status == InstrumentState.SUBMITTED.value
        and not is_demo_admin
    )
    can_verify_mock = (
        user.role == "SCRUTINY_OFFICER"
        and instrument.status == InstrumentState.UNDER_SCRUTINY.value
        and settings.DEMO_MODE
        and settings.APP_ENV != "production"
        and not is_demo_admin
    )
    can_verify_manual = (
        user.role == "SCRUTINY_OFFICER"
        and instrument.status == InstrumentState.UNDER_SCRUTINY.value
        and not is_demo_admin
    )
    can_add_note = (
        user.role == "SCRUTINY_OFFICER"
        and instrument.status not in TERMINAL_STATES
        and not is_demo_admin
    )
    can_flag = (
        user.role == "SCRUTINY_OFFICER"
        and instrument.status == InstrumentState.UNDER_SCRUTINY.value
        and not is_demo_admin
    )
    can_verify = (
        user.role == "SCRUTINY_OFFICER"
        and instrument.status == InstrumentState.UNDER_SCRUTINY.value
        and not is_demo_admin
    )
    can_activate = (
        user.role in ("SCRUTINY_OFFICER", "APPROVER_AUTHORITY")
        and instrument.status == InstrumentState.VERIFIED.value
        and not is_demo_admin
    )
    can_reject = (
        user.role == "APPROVER_AUTHORITY"
        and instrument.status in (InstrumentState.UNDER_SCRUTINY.value, InstrumentState.DISCREPANCY_FLAGGED.value)
        and not is_demo_admin
    )

    has_rejection_recommendation = any(n.action_recommended == "REJECT" for n in scrutiny_notes)

    instrument_alerts = (
        db.query(Alert)
        .filter(Alert.instrument_id == instrument.id)
        .order_by(Alert.created_at.desc())
        .all()
    )
    days_to_maturity = None
    if instrument.maturity_date:
        days_to_maturity = (instrument.maturity_date - clock_service.today()).days

    lineage = get_lineage(db, instrument)

    latest_release_request = (
        db.query(ReleaseRequest)
        .filter(ReleaseRequest.instrument_id == instrument.id)
        .order_by(ReleaseRequest.created_at.desc())
        .first()
    )

    from app.routers.temporal import get_unread_notifications_count
    unread_count = get_unread_notifications_count(db, user)

    context = {
        "request": request,
        "user": user,
        "institution": institution,
        "instrument": instrument,
        "lineage": lineage,
        "latest_release_request": latest_release_request,
        "prov_map": prov_map,
        "documents": documents,
        "timeline": timeline,
        "tickets": tickets,
        "alerts": instrument_alerts,
        "days_to_maturity": days_to_maturity,
        "unread_notifications_count": unread_count,
        "not_evaluated": not_evaluated,
        "scrutiny_notes": scrutiny_notes,
        "verification_records": verification_records,
        "audit_events": audit_events,
        "user_map": user_map,
        "can_start_scrutiny": can_start_scrutiny,
        "can_verify_mock": can_verify_mock,
        "can_verify_manual": can_verify_manual,
        "can_add_note": can_add_note,
        "can_flag": can_flag,
        "can_verify": can_verify,
        "can_activate": can_activate,
        "can_reject": can_reject,
        "has_rejection_recommendation": has_rejection_recommendation,
        "mock_failure_modes": [m.value for m in MockFailureMode],
        "action_error": action_error,
        "action_success": action_success,
        "current_time": clock_service.display_time,
        "is_simulated": clock_service.is_simulated,
        "demo_mode": settings.DEMO_MODE,
        "simulated_date_str": clock_service.display_simulated_date,
        "csrf_token": request.cookies.get(settings.CSRF_COOKIE_NAME, ""),
    }
    return templates.TemplateResponse("instruments/staff_detail.html", context, status_code=status_code)


def _action_success_response(
    request: Request,
    instrument_id: uuid.UUID,
    user: User,
    db: Session,
    message: str,
) -> Response:
    if request.headers.get("HX-Request") == "true":
        return _render_staff_case_page(
            request, instrument_id, user, db, action_success=message, status_code=status.HTTP_200_OK
        )
    return RedirectResponse(
        url=f"/instruments/{instrument_id}?success={quote_plus(message)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _action_error_response(
    request: Request,
    instrument_id: uuid.UUID,
    user: User,
    db: Session,
    error_msg: str,
    status_code: int = status.HTTP_422_UNPROCESSABLE_ENTITY,
) -> Response:
    accept = request.headers.get("accept", "")
    if "application/json" in accept and "text/html" not in accept:
        return JSONResponse(status_code=status_code, content={"detail": error_msg})
    return _render_staff_case_page(
        request, instrument_id, user, db, action_error=error_msg, status_code=status_code
    )


@router.get("/instruments/{instrument_id}", response_class=HTMLResponse)
def view_staff_instrument_detail(
    request: Request,
    instrument_id: uuid.UUID,
    user: User = Depends(require_roles("SCRUTINY_OFFICER", "APPROVER_AUTHORITY", "AUDITOR")),
    db: Session = Depends(get_db)
):
    """Displays AICTE-wide read-only view of instrument for scrutiny, approver, and auditor roles."""
    action_success = request.query_params.get("success")
    action_error = request.query_params.get("error")
    return _render_staff_case_page(
        request, instrument_id, user, db, action_error=action_error, action_success=action_success
    )


@router.post("/instruments/{instrument_id}/actions/start-scrutiny", dependencies=[Depends(verify_csrf)])
def action_start_scrutiny(
    request: Request,
    instrument_id: uuid.UUID,
    justification: Optional[str] = Form(None),
    user: User = Depends(require_roles("SCRUTINY_OFFICER")),
    db: Session = Depends(get_db),
):
    if getattr(user, "is_demo_admin", False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Demo Admin has read-only access to scrutiny cases.")
    client_ip = get_client_ip(request)
    try:
        ScrutinyService.start_scrutiny(db, instrument_id, user, justification, actor_ip=client_ip)
        db.commit()
        return _action_success_response(request, instrument_id, user, db, "Document scrutiny formally initiated.")
    except (ScrutinyError, ValueError) as exc:
        db.rollback()
        return _action_error_response(request, instrument_id, user, db, str(exc), status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)


@router.post("/instruments/{instrument_id}/actions/mock-verify", dependencies=[Depends(verify_csrf)])
def action_mock_verify(
    request: Request,
    instrument_id: uuid.UUID,
    failure_mode: str = Form("NONE"),
    user: User = Depends(require_roles("SCRUTINY_OFFICER")),
    db: Session = Depends(get_db),
):
    if getattr(user, "is_demo_admin", False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Demo Admin has read-only access to scrutiny cases.")
    client_ip = get_client_ip(request)
    try:
        mode = MockFailureMode(failure_mode) if failure_mode in MockFailureMode.__members__ else MockFailureMode.NONE
        rec, res = ScrutinyService.run_mock_verification(db, instrument_id, user, mode, actor_ip=client_ip)
        db.commit()
        msg = "Mock CBS verification completed successfully." if res.is_verified else "Mock CBS verification reported discrepancies."
        return _action_success_response(request, instrument_id, user, db, msg)
    except (ScrutinyError, ValueError, RuntimeError) as exc:
        db.rollback()
        return _action_error_response(request, instrument_id, user, db, str(exc), status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)


@router.post("/instruments/{instrument_id}/actions/manual-verify", dependencies=[Depends(verify_csrf)])
async def action_manual_verify(
    request: Request,
    instrument_id: uuid.UUID,
    letter_file: Optional[UploadFile] = File(None),
    confirmation_file: Optional[UploadFile] = File(None),
    reference_number: Optional[str] = Form(None),
    memo: Optional[str] = Form(None),
    user: User = Depends(require_roles("SCRUTINY_OFFICER")),
    db: Session = Depends(get_db),
):
    if getattr(user, "is_demo_admin", False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Demo Admin has read-only access to scrutiny cases.")
    if not reference_number or not reference_number.strip():
        return _action_error_response(request, instrument_id, user, db, "Letter reference number is required.")
    if not memo or not memo.strip():
        return _action_error_response(request, instrument_id, user, db, "Verification memo is required.")
    upload = letter_file or confirmation_file
    if not upload:
        return _action_error_response(request, instrument_id, user, db, "Confirmation letter scan file is required.")

    file_bytes = await upload.read()
    if not file_bytes:
        return _action_error_response(request, instrument_id, user, db, "Uploaded confirmation letter file is empty.")
    file_name = upload.filename or "confirmation.pdf"
    client_ip = get_client_ip(request)
    try:
        ScrutinyService.record_manual_confirmation(
            db, instrument_id, user, file_bytes, file_name, reference_number.strip(), memo.strip(), actor_ip=client_ip
        )
        db.commit()
        return _action_success_response(request, instrument_id, user, db, "Bank confirmation letter encrypted and recorded successfully.")
    except (ScrutinyError, ValueError) as exc:
        db.rollback()
        return _action_error_response(request, instrument_id, user, db, str(exc), status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)


@router.post("/instruments/{instrument_id}/actions/add-note", dependencies=[Depends(verify_csrf)])
def action_add_note(
    request: Request,
    instrument_id: uuid.UUID,
    note_text: Optional[str] = Form(None),
    note: Optional[str] = Form(None),
    action_recommended: Optional[str] = Form("CLEAR"),
    user: User = Depends(require_roles("SCRUTINY_OFFICER")),
    db: Session = Depends(get_db),
):
    if getattr(user, "is_demo_admin", False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Demo Admin has read-only access to scrutiny cases.")

    clean_text = (note_text or note or "").strip()
    if not clean_text:
        return _action_error_response(request, instrument_id, user, db, "Note content cannot be blank or whitespace-only.")

    if len(clean_text) < 10:
        return _action_error_response(request, instrument_id, user, db, "Note content must be at least 10 characters.")

    if len(clean_text) > 2000:
        return _action_error_response(request, instrument_id, user, db, "Note content cannot exceed 2000 characters.")

    rec = (action_recommended or "CLEAR").strip().upper()
    if rec not in ("CLEAR", "FLAG_DISCREPANCY", "REJECT"):
        return _action_error_response(
            request, instrument_id, user, db,
            f"Invalid recommendation: '{action_recommended}'. Must be CLEAR, FLAG_DISCREPANCY, or REJECT."
        )

    client_ip = get_client_ip(request)
    try:
        ScrutinyService.add_scrutiny_note(db, instrument_id, user, clean_text, rec, actor_ip=client_ip)
        db.commit()
        return _action_success_response(request, instrument_id, user, db, "Scrutiny note recorded.")
    except (ScrutinyError, ValueError) as exc:
        db.rollback()
        return _action_error_response(request, instrument_id, user, db, str(exc), status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)


@router.post("/instruments/{instrument_id}/actions/flag-discrepancy", dependencies=[Depends(verify_csrf)])
def action_flag_discrepancy(
    request: Request,
    instrument_id: uuid.UUID,
    institution_message: Optional[str] = Form(None),
    internal_justification: Optional[str] = Form(None),
    reason: Optional[str] = Form(None),
    user: User = Depends(require_roles("SCRUTINY_OFFICER")),
    db: Session = Depends(get_db),
):
    if getattr(user, "is_demo_admin", False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Demo Admin has read-only access to scrutiny cases.")
    client_ip = get_client_ip(request)
    try:
        ScrutinyService.flag_discrepancy(
            db=db,
            instrument_id=instrument_id,
            user=user,
            institution_message=institution_message,
            internal_justification=internal_justification,
            reason=reason,
            actor_ip=client_ip,
        )
        db.commit()
        return _action_success_response(request, instrument_id, user, db, "Discrepancy formally flagged. Instrument status updated.")
    except (ScrutinyError, ValueError) as exc:
        db.rollback()
        return _action_error_response(request, instrument_id, user, db, str(exc), status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)


@router.post("/instruments/{instrument_id}/actions/verify", dependencies=[Depends(verify_csrf)])
def action_verify(
    request: Request,
    instrument_id: uuid.UUID,
    justification: Optional[str] = Form(None),
    user: User = Depends(require_roles("SCRUTINY_OFFICER")),
    db: Session = Depends(get_db),
):
    if getattr(user, "is_demo_admin", False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Demo Admin has read-only access to scrutiny cases.")
    client_ip = get_client_ip(request)
    try:
        ScrutinyService.verify_instrument(db, instrument_id, user, justification, actor_ip=client_ip)
        db.commit()
        return _action_success_response(request, instrument_id, user, db, "Instrument formally verified.")
    except (ScrutinyError, ValueError) as exc:
        db.rollback()
        return _action_error_response(request, instrument_id, user, db, str(exc), status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)


@router.post("/instruments/{instrument_id}/actions/activate", dependencies=[Depends(verify_csrf)])
def action_activate(
    request: Request,
    instrument_id: uuid.UUID,
    justification: Optional[str] = Form(None),
    user: User = Depends(require_roles("SCRUTINY_OFFICER", "APPROVER_AUTHORITY")),
    db: Session = Depends(get_db),
):
    if getattr(user, "is_demo_admin", False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Demo Admin has read-only access to scrutiny cases.")
    client_ip = get_client_ip(request)
    try:
        ScrutinyService.activate_instrument(db, instrument_id, user, justification, actor_ip=client_ip)
        db.commit()
        return _action_success_response(request, instrument_id, user, db, "Deposit activated and committed into AICTE custody.")
    except (ScrutinyError, ValueError) as exc:
        db.rollback()
        return _action_error_response(request, instrument_id, user, db, str(exc), status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)


@router.post("/instruments/{instrument_id}/actions/reject", dependencies=[Depends(verify_csrf)])
def action_reject(
    request: Request,
    instrument_id: uuid.UUID,
    justification: Optional[str] = Form(None),
    user: User = Depends(require_roles("APPROVER_AUTHORITY")),
    db: Session = Depends(get_db),
):
    if getattr(user, "is_demo_admin", False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Demo Admin has read-only access to scrutiny cases.")
    clean_justification = (justification or "").strip()
    if not clean_justification:
        return _action_error_response(request, instrument_id, user, db, "Rejection justification cannot be blank.")
    if len(clean_justification) < 10:
        return _action_error_response(request, instrument_id, user, db, "Rejection justification must be at least 10 characters.")
    client_ip = get_client_ip(request)
    try:
        ScrutinyService.reject_instrument(db, instrument_id, user, clean_justification, actor_ip=client_ip)
        db.commit()
        return _action_success_response(request, instrument_id, user, db, "Instrument formally rejected by Competent Authority.")
    except (ScrutinyError, ValueError) as exc:
        db.rollback()
        return _action_error_response(request, instrument_id, user, db, str(exc), status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)


@router.get("/instruments/{instrument_id}/documents/{document_id}/download")
def download_staff_document(
    request: Request,
    instrument_id: uuid.UUID,
    document_id: uuid.UUID,
    user: User = Depends(require_roles("SCRUTINY_OFFICER", "APPROVER_AUTHORITY", "AUDITOR")),
    db: Session = Depends(get_db)
):
    """Streams decrypted document for authorized staff roles with audit trail."""
    client_ip = get_client_ip(request)

    instrument = db.query(Instrument).filter(Instrument.id == instrument_id).first()
    if not instrument:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    document = (
        db.query(Document)
        .filter(Document.id == document_id, Document.instrument_id == instrument.id)
        .first()
    )
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    try:
        plaintext = DocumentService.read_and_decrypt_document(document)
    except ValueError as exc:
        AuditService.append(
            db,
            entity_type="DOCUMENT",
            entity_id=document.id,
            action="DOCUMENT_INTEGRITY_FAILURE",
            actor_user_id=user.id,
            actor_role=user.role,
            details={"error": str(exc), "instrument_id": str(instrument.id)},
            actor_ip=client_ip,
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Document integrity verification failed.")
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Decryption failed: {str(exc)}")

    # Audit staff download
    AuditService.append(
        db,
        entity_type="DOCUMENT",
        entity_id=document.id,
        action="DOCUMENT_DOWNLOADED",
        actor_user_id=user.id,
        actor_role=user.role,
        details={"file_name": document.file_name, "instrument_id": str(instrument.id)},
        actor_ip=client_ip,
    )
    db.commit()

    headers = {
        "Content-Disposition": f'attachment; filename="{document.file_name}"',
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
    }
    return StreamingResponse(io.BytesIO(plaintext), media_type=document.file_mime_type, headers=headers)


# -----------------------------------------------------------------------------
# Dual-Key Release Workflow Routes
# -----------------------------------------------------------------------------

@router.post("/institution/instruments/{instrument_id}/request-release", response_class=Response, dependencies=[Depends(verify_csrf)])
async def request_instrument_release_action(
    request: Request,
    instrument_id: uuid.UUID,
    justification: str = Form(...),
    user: User = Depends(require_roles("INSTITUTION_ADMIN")),
    db: Session = Depends(get_db)
):
    """Processes request for security deposit release by institution admin."""
    actor = Actor.from_user(user)
    try:
        request_release(
            session=db,
            instrument_id=instrument_id,
            actor=actor,
            justification=justification,
        )
        db.commit()
    except ReleaseNotFoundError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Instrument not found.")
    except ReleasePermissionError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unauthorized.")
    except ReleaseStateError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))

    return RedirectResponse(
        url=f"/institution/instruments/{instrument_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/institution/instruments/{instrument_id}/release-order", response_class=HTMLResponse)
def view_institution_release_order(
    request: Request,
    instrument_id: uuid.UUID,
    user: User = Depends(require_roles("INSTITUTION_ADMIN")),
    db: Session = Depends(get_db)
):
    """Renders verifiable release order strictly for owning institution if status is RELEASED."""
    instrument = (
        db.query(Instrument)
        .filter(Instrument.id == instrument_id, Instrument.institution_id == user.institution_id)
        .first()
    )
    if not instrument or instrument.status != "RELEASED":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Release order not found.")

    release_req = (
        db.query(ReleaseRequest)
        .filter(ReleaseRequest.instrument_id == instrument.id, ReleaseRequest.authority_decision == "APPROVED")
        .order_by(ReleaseRequest.created_at.desc())
        .first()
    )
    if not release_req:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Release order not found.")

    institution = db.query(Institution).filter(Institution.id == instrument.institution_id).first()
    requester = db.query(User).filter(User.id == release_req.requested_by_user_id).first() if release_req.requested_by_user_id else None
    scrutiny_officer = db.query(User).filter(User.id == release_req.scrutiny_officer_id).first() if release_req.scrutiny_officer_id else None
    approver_user = db.query(User).filter(User.id == release_req.approver_authority_id).first() if release_req.approver_authority_id else None

    context = {
        "request": request,
        "user": user,
        "instrument": instrument,
        "institution": institution,
        "release_request": release_req,
        "requester": requester,
        "scrutiny_officer": scrutiny_officer,
        "approver_user": approver_user,
        "is_staff": False,
        "current_time": clock_service.display_time,
        "is_simulated": clock_service.is_simulated,
        "demo_mode": settings.DEMO_MODE,
        "simulated_date_str": clock_service.display_simulated_date,
        "csrf_token": request.cookies.get(settings.CSRF_COOKIE_NAME, ""),
    }
    return templates.TemplateResponse("instruments/release_order.html", context)


@router.get("/instruments/{instrument_id}/release-order", response_class=HTMLResponse)
def view_staff_release_order(
    request: Request,
    instrument_id: uuid.UUID,
    user: User = Depends(require_roles("SCRUTINY_OFFICER", "APPROVER_AUTHORITY", "AUDITOR", "DEMO_ADMIN")),
    db: Session = Depends(get_db)
):
    """Renders verifiable release order with full audit details for authorized staff."""
    instrument = db.query(Instrument).filter(Instrument.id == instrument_id).first()
    if not instrument or instrument.status != "RELEASED":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Release order not found.")

    release_req = (
        db.query(ReleaseRequest)
        .filter(ReleaseRequest.instrument_id == instrument.id, ReleaseRequest.authority_decision == "APPROVED")
        .order_by(ReleaseRequest.created_at.desc())
        .first()
    )
    if not release_req:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Release order not found.")

    institution = db.query(Institution).filter(Institution.id == instrument.institution_id).first()
    requester = db.query(User).filter(User.id == release_req.requested_by_user_id).first() if release_req.requested_by_user_id else None
    scrutiny_officer = db.query(User).filter(User.id == release_req.scrutiny_officer_id).first() if release_req.scrutiny_officer_id else None
    approver_user = db.query(User).filter(User.id == release_req.approver_authority_id).first() if release_req.approver_authority_id else None

    context = {
        "request": request,
        "user": user,
        "instrument": instrument,
        "institution": institution,
        "release_request": release_req,
        "requester": requester,
        "scrutiny_officer": scrutiny_officer,
        "approver_user": approver_user,
        "is_staff": True,
        "current_time": clock_service.display_time,
        "is_simulated": clock_service.is_simulated,
        "demo_mode": settings.DEMO_MODE,
        "simulated_date_str": clock_service.display_simulated_date,
        "csrf_token": request.cookies.get(settings.CSRF_COOKIE_NAME, ""),
    }
    return templates.TemplateResponse("instruments/release_order.html", context)


@router.post("/scrutiny/release-requests/{request_id}/recommend", response_class=Response, dependencies=[Depends(verify_csrf)])
async def record_release_recommendation_action(
    request: Request,
    request_id: uuid.UUID,
    recommendation: str = Form(...),
    justification: str = Form(...),
    course_cycle_completed: Optional[str] = Form(None),
    no_student_grievances_pending: Optional[str] = Form(None),
    no_vigilance_inquiry_active: Optional[str] = Form(None),
    regional_inspection_cleared: Optional[str] = Form(None),
    evidence_file: Optional[UploadFile] = File(None),
    user: User = Depends(require_roles("SCRUTINY_OFFICER")),
    db: Session = Depends(get_db)
):
    """Processes scrutiny officer statutory checklist review and recommendation."""
    checklist = {
        "course_cycle_completed": course_cycle_completed in ("true", "True", "on", "1"),
        "no_student_grievances_pending": no_student_grievances_pending in ("true", "True", "on", "1"),
        "no_vigilance_inquiry_active": no_vigilance_inquiry_active in ("true", "True", "on", "1"),
        "regional_inspection_cleared": regional_inspection_cleared in ("true", "True", "on", "1"),
    }
    actor = Actor.from_user(user)

    upload_data = None
    if evidence_file and getattr(evidence_file, "filename", None):
        content = await evidence_file.read()
        if content:
            upload_data = (content, evidence_file.filename)

    try:
        file_obj = None
        if upload_data:
            file_obj = io.BytesIO(upload_data[0])
            file_obj.filename = upload_data[1]

        rel_req = record_recommendation(
            session=db,
            request_id=request_id,
            actor=actor,
            checklist=checklist,
            recommendation=recommendation,
            justification=justification,
            evidence_upload=file_obj,
        )
        db.commit()
        inst_id = rel_req.instrument_id
    except ReleaseNotFoundError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Release request not found.")
    except (ReleaseStateError, ReleaseChecklistIncompleteError, ReleaseAlreadyDecidedError) as exc:
        db.rollback()
        req = db.query(ReleaseRequest).filter(ReleaseRequest.id == request_id).first()
        inst_id = req.instrument_id if req else None
        if inst_id:
            return RedirectResponse(
                url=f"/instruments/{inst_id}?error={quote_plus(str(exc))}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))

    return RedirectResponse(
        url=f"/instruments/{inst_id}?success=Recommendation+recorded+successfully",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/approver/release-requests/{request_id}/decide", response_class=Response, dependencies=[Depends(verify_csrf)])
async def decide_release_action(
    request: Request,
    request_id: uuid.UUID,
    decision: str = Form(...),
    justification: str = Form(...),
    user: User = Depends(require_roles("APPROVER_AUTHORITY")),
    db: Session = Depends(get_db)
):
    """Processes final release decision by competent authority."""
    actor = Actor.from_user(user)
    try:
        rel_req = decide_release(
            session=db,
            request_id=request_id,
            actor=actor,
            decision=decision,
            justification=justification,
        )
        db.commit()
        inst_id = rel_req.instrument_id
    except ReleaseNotFoundError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Release request not found.")
    except (SecondKeyMissingError, ReleaseStateError, ReleaseAlreadyDecidedError) as exc:
        db.rollback()
        req = db.query(ReleaseRequest).filter(ReleaseRequest.id == request_id).first()
        inst_id = req.instrument_id if req else None
        if inst_id:
            return RedirectResponse(
                url=f"/instruments/{inst_id}?error={quote_plus(str(exc))}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))

    return RedirectResponse(
        url=f"/instruments/{inst_id}?success=Decision+recorded+successfully",
        status_code=status.HTTP_303_SEE_OTHER,
    )

