from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from app.core.config import settings

# Runtime engine (fdr_app_user) - used by FastAPI request endpoints
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Owner engine (fdr_owner) - used by Alembic migrations and administrative scripts
owner_engine = create_engine(
    settings.DATABASE_OWNER_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10
)
OwnerSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=owner_engine)

Base = declarative_base()


def get_db():
    """Dependency that yields a database session for runtime application queries."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_owner_db():
    """Dependency or context manager for administrative / owner-level operations."""
    db = OwnerSessionLocal()
    try:
        yield db
    finally:
        db.close()
