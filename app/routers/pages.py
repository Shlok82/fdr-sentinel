from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.core.database import get_db
from app.services.clock_service import clock_service
from app.core.config import settings

templates = Jinja2Templates(directory="app/templates")

router = APIRouter(include_in_schema=False)


@router.get("/", response_class=HTMLResponse)
def index_page(request: Request, db: Session = Depends(get_db)):
    """Render the main landing / portal dashboard skeleton."""
    db_connected = False
    table_names = []
    try:
        db.execute(text("SELECT 1"))
        db_connected = True
        # Query public tables to show system schema readiness
        res = db.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY table_name"
        ))
        table_names = [r[0] for r in res.fetchall()]
    except Exception:
        db_connected = False

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "current_time": clock_service.get_current_time().strftime("%d %b %Y, %H:%M:%S UTC"),
            "is_simulated": clock_service.is_simulated,
            "demo_mode": settings.DEMO_MODE,
            "db_connected": db_connected,
            "tables": table_names,
        }
    )
