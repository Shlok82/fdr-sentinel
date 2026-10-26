import html
import logging
import traceback
from fastapi import FastAPI, Request, Response, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from pathlib import Path
from app.core.config import settings
from app.core.templates import templates
from app.routers import health, auth, demo, pages, instruments
from app.services.clock_service import clock_service

logger = logging.getLogger("uvicorn.error")

# Initialize FastAPI application
app = FastAPI(
    title="FDR Sentinel API",
    description="Intelligent Fixed Deposit & Security Deposit Lifecycle Management Portal for AICTE (SIH 26265)",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)

# CORS middleware configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Basic Security Headers Middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


# Central Exception Handler for HTTP Errors (renders styled HTML or returns JSON)
@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    accept = request.headers.get("accept", "")
    is_htmx = bool(request.headers.get("HX-Request"))
    is_api = (
        request.url.path.startswith("/api/")
        or request.url.path.startswith("/demo/")
        or request.url.path.startswith("/healthz")
        or "application/json" in accept
    )

    if exc.status_code >= 500:
        logger.error(
            "HTTP %s on %s %s: %s",
            exc.status_code,
            request.method,
            request.url.path,
            exc.detail
        )

    # For 401 on standard HTML navigation requests, redirect to login
    if exc.status_code == status.HTTP_401_UNAUTHORIZED and not is_api and not is_htmx:
        next_url = request.url.path
        if request.url.query:
            next_url += f"?{request.url.query}"
        return RedirectResponse(url=f"/login?next={next_url}", status_code=status.HTTP_302_FOUND)

    # Render HTML for browser/page requests (not purely API routes or explicit json requests)
    if not is_api and ("text/html" in accept or "*/*" in accept or not accept):
        template_name = "errors/404.html"
        if exc.status_code == 401:
            template_name = "errors/401.html"
        elif exc.status_code == 403:
            template_name = "errors/403.html"
        elif exc.status_code >= 500:
            template_name = "errors/500.html"

        safe_detail = "Internal Server Error" if exc.status_code >= 500 else exc.detail
        ctx = {
            "request": request,
            "user": None,
            "detail": safe_detail,
            "current_time": clock_service.now_local().strftime("%d %b %Y, %H:%M:%S IST"),
            "is_simulated": clock_service.is_simulated,
            "demo_mode": settings.DEMO_MODE,
            "simulated_date_str": clock_service.today().strftime("%d %b %Y") if clock_service.is_simulated else None,
            "csrf_token": request.cookies.get(settings.CSRF_COOKIE_NAME, ""),
        }
        return templates.TemplateResponse(template_name, ctx, status_code=exc.status_code)

    safe_detail = "Internal Server Error" if exc.status_code >= 500 else exc.detail
    return JSONResponse(status_code=exc.status_code, content={"detail": safe_detail})


# Central Exception Handler for Request Validation Errors (never raw FastAPI JSON in browser)
@app.exception_handler(RequestValidationError)
async def custom_validation_exception_handler(request: Request, exc: RequestValidationError):
    accept = request.headers.get("accept", "")
    is_htmx = bool(request.headers.get("HX-Request"))
    is_api = (
        request.url.path.startswith("/api/")
        or request.url.path.startswith("/demo/")
        or request.url.path.startswith("/healthz")
        or ("application/json" in accept and "text/html" not in accept)
    )

    error_messages = []
    for error in exc.errors():
        field_loc = error.get("loc", [])
        field_name = field_loc[-1] if field_loc else "Field"
        if field_name == "body":
            field_name = "form"
        readable_field = str(field_name).replace("_", " ").title()
        msg = error.get("msg", "Invalid value")
        error_messages.append(f"{readable_field}: {msg}")

    friendly_text = " • ".join(error_messages) if error_messages else "Invalid form submission."

    if is_api and not is_htmx:
        return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content={"detail": exc.errors()})

    if is_htmx:
        return Response(
            content=f'<div class="rounded-lg bg-rose-50 border border-rose-200 p-4 text-sm text-rose-800 font-medium" role="alert" aria-live="polite">⚠ {html.escape(friendly_text)}</div>',
            media_type="text/html",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    ctx = {
        "request": request,
        "user": None,
        "detail": friendly_text,
        "current_time": clock_service.now_local().strftime("%d %b %Y, %H:%M:%S IST"),
        "is_simulated": clock_service.is_simulated,
        "demo_mode": settings.DEMO_MODE,
        "simulated_date_str": clock_service.today().strftime("%d %b %Y") if clock_service.is_simulated else None,
        "csrf_token": request.cookies.get(settings.CSRF_COOKIE_NAME, ""),
    }
    return templates.TemplateResponse("errors/422.html", ctx, status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)


# Global catch-all handler for unexpected Python exceptions (TypeError, AttributeError, etc.)
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Global catch-all exception handler for unexpected Python/server errors.
    
    Guarantees:
    1. Full traceback and request context logged to server log.
    2. Renders styled 500 HTML page without leaking stack trace or internal exception details.
    3. Returns JSON for API/json-accept requests.
    """
    logger.error(
        "Unhandled server exception on %s %s: %s\n%s",
        request.method,
        request.url.path,
        str(exc),
        traceback.format_exc()
    )

    accept = request.headers.get("accept", "")
    is_api = (
        request.url.path.startswith("/api/")
        or request.url.path.startswith("/demo/")
        or request.url.path.startswith("/healthz")
        or "application/json" in accept
    )
    if is_api:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal Server Error"}
        )

    ctx = {
        "request": request,
        "user": None,
        "detail": "Internal Server Error",
        "current_time": clock_service.now_local().strftime("%d %b %Y, %H:%M:%S IST"),
        "is_simulated": clock_service.is_simulated,
        "demo_mode": settings.DEMO_MODE,
        "simulated_date_str": clock_service.today().strftime("%d %b %Y") if clock_service.is_simulated else None,
        "csrf_token": request.cookies.get(settings.CSRF_COOKIE_NAME, ""),
    }
    return templates.TemplateResponse("errors/500.html", ctx, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


# Ensure static and storage directories exist
static_dir = Path("app/static")
static_dir.mkdir(parents=True, exist_ok=True)

storage_dir = Path(settings.UPLOAD_STORAGE_PATH)
storage_dir.mkdir(parents=True, exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Include Routers
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(demo.router)
app.include_router(instruments.router)
app.include_router(pages.router)


@app.get("/ping", tags=["Health"], summary="Simple ping probe")
def ping():
    return {"ping": "pong"}
