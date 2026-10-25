import uuid
from typing import Optional, List, Callable, Any
from fastapi import Request, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import get_db
from app.core.security import decode_access_token, verify_csrf_token
from app.models.user import User


def get_client_ip(request: Request) -> str:
    """Extracts client IP address safely considering forward headers."""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


def get_optional_current_user(
    request: Request,
    db: Session = Depends(get_db)
) -> Optional[User]:
    """Retrieves current authenticated user if valid session exists; otherwise None.
    
    CRITICAL: Always reloads active status and role from database on every request.
    """
    token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not token:
        return None

    payload = decode_access_token(token)
    if not payload:
        return None

    user_id_str = payload.get("sub")
    if not user_id_str:
        return None

    try:
        user_uuid = uuid.UUID(user_id_str)
    except ValueError:
        return None

    user = db.query(User).filter(User.id == user_uuid).first()
    if not user or not user.is_active:
        return None

    return user


def get_current_user(
    request: Request,
    user: Optional[User] = Depends(get_optional_current_user)
) -> User:
    """Enforces authentication: returns active User or raises 401 / redirects to login."""
    if not user:
        accept_header = request.headers.get("accept", "")
        # For standard HTML browser requests, redirect to login with next parameter
        if "text/html" in accept_header and not request.headers.get("HX-Request"):
            next_path = request.url.path
            if request.url.query:
                next_path += f"?{request.url.query}"
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"Location": f"/login?next={next_path}"}
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please log in."
        )
    return user


def require_roles(*allowed_roles: str) -> Callable:
    """Dependency factory restricting route access to specified roles."""
    def role_checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Role '{user.role}' is not authorized for this action."
            )
        return user
    return role_checker


async def verify_csrf(request: Request) -> None:
    """Enforces double-submit CSRF token verification on state-changing HTTP methods."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        cookie_token = request.cookies.get(settings.CSRF_COOKIE_NAME)
        # Check header first (common for HTMX and AJAX)
        submitted_token = request.headers.get("X-CSRF-Token")
        
        # If not in header, check form data if request is form-encoded
        if not submitted_token:
            content_type = request.headers.get("content-type", "")
            if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
                try:
                    form_data = await request.form()
                    submitted_token = form_data.get("csrf_token")
                except Exception:
                    pass

        if not verify_csrf_token(submitted_token, cookie_token):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF validation failed. Missing or invalid security token."
            )


def scope_institution_query(query: Any, user: User, institution_id_attr: Any) -> Any:
    """Applies institution isolation filter to SQLAlchemy query.
    
    If the caller is an INSTITUTION_ADMIN, strictly limits rows to user.institution_id.
    Staff roles (SCRUTINY_OFFICER, APPROVER_AUTHORITY, AUDITOR) receive unfiltered queries.
    """
    if user.role == "INSTITUTION_ADMIN":
        return query.filter(institution_id_attr == user.institution_id)
    return query


def verify_institution_access(record: Any, user: User) -> None:
    """Enforces institution data boundary for a single record.
    
    CRITICAL: Raises HTTP 404 Not Found (NOT 403) for institution users attempting
    to access another institution's records, preventing ID enumeration.
    """
    if user.role == "INSTITUTION_ADMIN":
        rec_inst_id = getattr(record, "institution_id", None)
        if rec_inst_id != user.institution_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Requested record not found."
            )
