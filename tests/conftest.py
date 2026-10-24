import os
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from alembic.config import Config
from alembic import command
from app.core.config import settings
from app.services.clock_service import clock_service

# Derive test database URLs
TEST_OWNER_URL = os.getenv("TEST_OWNER_DATABASE_URL", settings.TEST_OWNER_DATABASE_URL)
TEST_APP_URL = os.getenv("TEST_DATABASE_URL", settings.TEST_DATABASE_URL)

test_owner_engine = create_engine(TEST_OWNER_URL, pool_pre_ping=True)
test_app_engine = create_engine(TEST_APP_URL, pool_pre_ping=True)

TestOwnerSession = sessionmaker(autocommit=False, autoflush=False, bind=test_owner_engine)
TestAppSession = sessionmaker(autocommit=False, autoflush=False, bind=test_app_engine)


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """Session-scoped fixture: ensures test database exists, applies migrations, and sets permissions."""
    # Ensure test database exists
    try:
        # Connect to template/default db to check/create test db
        admin_url = TEST_OWNER_URL.replace("/fdr_sentinel_test", "/fdr_sentinel")
        admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin_engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = 'fdr_sentinel_test'")
            ).scalar()
            if not exists:
                conn.execute(text("CREATE DATABASE fdr_sentinel_test OWNER fdr_owner"))
        admin_engine.dispose()
    except Exception as exc:
        print(f"Database creation check note: {exc}")

    # Run Alembic migrations against the test database using the owner URL
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", TEST_OWNER_URL)
    command.upgrade(alembic_cfg, "head")

    # Explicitly verify privilege boundary on test database
    with test_owner_engine.connect() as conn:
        conn.execute(text("""
            DO $$
            BEGIN
                IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'fdr_app_user') THEN
                    GRANT CONNECT ON DATABASE fdr_sentinel_test TO fdr_app_user;
                    GRANT USAGE ON SCHEMA public TO fdr_app_user;
                    GRANT SELECT, INSERT ON audit_log TO fdr_app_user;
                    REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM fdr_app_user;
                    GRANT USAGE ON SEQUENCE audit_log_id_seq TO fdr_app_user;
                    GRANT SELECT, UPDATE ON clock_state TO fdr_app_user;
                END IF;
            END $$;
        """))
        conn.commit()

    yield

    test_owner_engine.dispose()
    test_app_engine.dispose()


@pytest.fixture(autouse=True)
def clean_database_between_tests():
    """Truncates audit_log RESTART IDENTITY and resets clock_state before each test."""
    with test_owner_engine.connect() as conn:
        conn.execute(text("TRUNCATE TABLE audit_log RESTART IDENTITY CASCADE;"))
        conn.execute(text("UPDATE clock_state SET offset_seconds = 0, updated_at = '1970-01-01 00:00:00+00' WHERE id = 1;"))
        conn.commit()

    clock_service._offset_seconds = 0

    yield

    with test_owner_engine.connect() as conn:
        conn.execute(text("TRUNCATE TABLE audit_log RESTART IDENTITY CASCADE;"))
        conn.execute(text("UPDATE clock_state SET offset_seconds = 0, updated_at = '1970-01-01 00:00:00+00' WHERE id = 1;"))
        conn.commit()


@pytest.fixture
def app_session():
    """Yields a database session connected strictly as the runtime fdr_app_user."""
    session = TestAppSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def owner_session():
    """Yields a database session connected as the administrative fdr_owner."""
    session = TestOwnerSession()
    try:
        yield session
    finally:
        session.close()
