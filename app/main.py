from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from app.core.config import settings
from app.routers import health, pages

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

# Ensure static and storage directories exist
static_dir = Path("app/static")
static_dir.mkdir(parents=True, exist_ok=True)

storage_dir = Path(settings.UPLOAD_STORAGE_PATH)
storage_dir.mkdir(parents=True, exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Include Routers
app.include_router(health.router)
app.include_router(pages.router)


@app.get("/ping", tags=["Health"], summary="Simple ping probe")
def ping():
    return {"ping": "pong"}
