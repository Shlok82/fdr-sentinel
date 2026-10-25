import uuid
from typing import Optional
from fastapi import APIRouter, Request, Response, Depends, Form, File, UploadFile, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
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
from app.schemas.instrument import FDRSubmissionForm
from app.services.submission_service import FDRSubmissionService
from app.services.document_service import DocumentService, validate_file_content
from app.services.audit_service import AuditService
from app.services.clock_service import clock_service
from app.services.instrument_fsm import get_instrument_timeline

router = APIRouter(tags=["Instruments & Custody Vault"])


@router.get("/institution/instruments", response_class=HTMLResponse)
def list_institution_instruments(
    request: Request,
    user: User = Depends(require_roles("INSTITUTION_ADMIN")),
    db: Session = Depends(get_db)
):
    """Lists all FDR instruments belonging strictly to the caller's institution."""
    institution = db.query(Institution).filter(Institution.id == user.institution_id).first()
    # Null-Safety & Query Rule:
    # Orders by created_at.desc(); any future queries ordering by nullable maturity_date must use nullslast().
    # All displayed amounts and dates are formatted exclusively through format_inr and format_date in the template.
    instruments = (
        db.query(Instrument)
        .filter(Instrument.institution_id == user.institution_id)
        .order_by(Instrument.created_at.desc())
        .all()
    )

    context = {
        "request": request,
        "user": user,
        "institution": institution,
        "instruments": instruments,
        "current_time": clock_service.get_current_time().strftime("%d %b %Y, %H:%M:%S UTC"),
        "is_simulated": clock_service.is_simulated,
        "demo_mode": settings.DEMO_MODE,
        "simulated_date_str": clock_service.today().strftime("%d %b %Y") if clock_service.is_simulated else None,
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

    context = {
        "request": request,
        "user": user,
        "institution": institution,
        "form_data": {},
        "errors": {},
        "current_time": clock_service.get_current_time().strftime("%d %b %Y, %H:%M:%S UTC"),
        "is_simulated": clock_service.is_simulated,
        "demo_mode": settings.DEMO_MODE,
        "max_upload_mb": settings.MAX_UPLOAD_MB,
        "simulated_date_str": clock_service.today().strftime("%d %b %Y") if clock_service.is_simulated else None,
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
            "current_time": clock_service.get_current_time().strftime("%d %b %Y, %H:%M:%S UTC"),
            "is_simulated": clock_service.is_simulated,
            "demo_mode": settings.DEMO_MODE,
            "max_upload_mb": settings.MAX_UPLOAD_MB,
            "simulated_date_str": clock_service.today().strftime("%d %b %Y") if clock_service.is_simulated else None,
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
            "current_time": clock_service.get_current_time().strftime("%d %b %Y, %H:%M:%S UTC"),
            "is_simulated": clock_service.is_simulated,
            "demo_mode": settings.DEMO_MODE,
            "max_upload_mb": settings.MAX_UPLOAD_MB,
            "simulated_date_str": clock_service.today().strftime("%d %b %Y") if clock_service.is_simulated else None,
            "csrf_token": request.cookies.get(settings.CSRF_COOKIE_NAME, ""),
        }
        return templates.TemplateResponse("instruments/new.html", context, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # 4. Success: POST-redirect-GET to instrument detail page
    return RedirectResponse(
        url=f"/institution/instruments/{instrument.id}",
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
    documents = db.query(Document).filter(Document.instrument_id == instrument.id).all()

    # Map provenance by field_name for easy template lookups
    prov_map = {p.field_name: p for p in provenance_rows}
    timeline = get_instrument_timeline(db, instrument)

    context = {
        "request": request,
        "user": user,
        "institution": institution,
        "instrument": instrument,
        "prov_map": prov_map,
        "documents": documents,
        "timeline": timeline,
        "current_time": clock_service.get_current_time().strftime("%d %b %Y, %H:%M:%S UTC"),
        "is_simulated": clock_service.is_simulated,
        "demo_mode": settings.DEMO_MODE,
        "simulated_date_str": clock_service.today().strftime("%d %b %Y") if clock_service.is_simulated else None,
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
    if not document:
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
# Staff Routes: Read-only inspection and download for AICTE Officers & Auditors
# -----------------------------------------------------------------------------

@router.get("/instruments/{instrument_id}", response_class=HTMLResponse)
def view_staff_instrument_detail(
    request: Request,
    instrument_id: uuid.UUID,
    user: User = Depends(require_roles("SCRUTINY_OFFICER", "APPROVER_AUTHORITY", "AUDITOR")),
    db: Session = Depends(get_db)
):
    """Displays AICTE-wide read-only view of instrument for scrutiny, approver, and auditor roles."""
    instrument = db.query(Instrument).filter(Instrument.id == instrument_id).first()
    if not instrument:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Instrument not found.")

    institution = db.query(Institution).filter(Institution.id == instrument.institution_id).first()
    provenance_rows = db.query(FieldProvenance).filter(FieldProvenance.instrument_id == instrument.id).all()
    documents = db.query(Document).filter(Document.instrument_id == instrument.id).all()
    prov_map = {p.field_name: p for p in provenance_rows}
    timeline = get_instrument_timeline(db, instrument)

    context = {
        "request": request,
        "user": user,
        "institution": institution,
        "instrument": instrument,
        "prov_map": prov_map,
        "documents": documents,
        "timeline": timeline,
        "current_time": clock_service.get_current_time().strftime("%d %b %Y, %H:%M:%S UTC"),
        "is_simulated": clock_service.is_simulated,
        "demo_mode": settings.DEMO_MODE,
        "simulated_date_str": clock_service.today().strftime("%d %b %Y") if clock_service.is_simulated else None,
        "csrf_token": request.cookies.get(settings.CSRF_COOKIE_NAME, ""),
    }
    return templates.TemplateResponse("instruments/staff_detail.html", context)


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
