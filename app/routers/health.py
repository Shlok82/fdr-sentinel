from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.core.database import get_db
from app.schemas.health import HealthResponse

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
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database unreachable: {str(exc)}"
        )

    return HealthResponse(status="ok")
