from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.core.database import get_db
from app.core.config import settings
from app.schemas.health import HealthResponse
from app.services.clock_service import clock_service

router = APIRouter(tags=["Health"])


@router.get("/healthz", response_model=HealthResponse, summary="System Health & Readiness Probe")
def healthz(db: Session = Depends(get_db)):
    """Health check endpoint verifying application readiness and database connectivity.
    
    Uses the runtime database role (fdr_app_user) to verify database health.
    """
    try:
        # Verify database responsiveness
        result = db.execute(text("SELECT 1")).scalar()
        if result != 1:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database returned unexpected response"
            )
        db_status = "connected"
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database unreachable: {str(exc)}"
        )

    current_time = clock_service.get_current_time().isoformat()
    return HealthResponse(
        status="healthy",
        database=db_status,
        app_name="FDR Sentinel",
        environment=settings.ENVIRONMENT,
        timestamp=current_time,
        version="1.0.0"
    )
