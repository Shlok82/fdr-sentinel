import os
import uuid
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from fastapi.testclient import TestClient
from alembic.config import Config
from alembic import command
from app.core.config import settings
from app.core.database import get_db
from app.core.security import hash_password, create_access_token, generate_csrf_token
from app.main import app
from app.models.institution import Institution
from app.models.user import User
from app.services.clock_service import clock_service

# Lower bcrypt rounds during tests for high performance
settings.BCRYPT_ROUNDS = 4

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
    try:
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

    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", TEST_OWNER_URL)
    command.upgrade(alembic_cfg, "head")

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
    """Truncates audit_log, resets clock_state, and cleans transient test data between tests."""
    with test_owner_engine.connect() as conn:
        conn.execute(text("TRUNCATE TABLE audit_log RESTART IDENTITY CASCADE;"))
        conn.execute(text("TRUNCATE TABLE users CASCADE;"))
        conn.execute(text("TRUNCATE TABLE institutions CASCADE;"))
        conn.execute(text("UPDATE clock_state SET offset_seconds = 0, updated_at = '1970-01-01 00:00:00+00' WHERE id = 1;"))
        conn.commit()

    clock_service._offset_seconds = 0

    yield

    with test_owner_engine.connect() as conn:
        conn.execute(text("TRUNCATE TABLE audit_log RESTART IDENTITY CASCADE;"))
        conn.execute(text("TRUNCATE TABLE users CASCADE;"))
        conn.execute(text("TRUNCATE TABLE institutions CASCADE;"))
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


@pytest.fixture
def client():
    """Yields a TestClient bound to the test database session."""
    def override_get_db():
        session = TestAppSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def sample_institution(owner_session: Session) -> Institution:
    """Creates a sample institution."""
    now_ts = clock_service.now()
    inst = Institution(
        id=uuid.uuid4(),
        aicte_permanent_id="1-TEST-001",
        name="Test Engineering College A",
        region="Western",
        state="Maharashtra",
        address="Test Road, Mumbai",
        contact_email="test.a@college.edu",
        contact_phone="022-12345678",
        institution_type="Engineering & Technology",
        created_at=now_ts,
        updated_at=now_ts,
    )
    owner_session.add(inst)
    owner_session.commit()
    return inst


@pytest.fixture
def sample_institution_b(owner_session: Session) -> Institution:
    """Creates a second sample institution for scoping tests."""
    now_ts = clock_service.now()
    inst = Institution(
        id=uuid.uuid4(),
        aicte_permanent_id="1-TEST-002",
        name="Test Technology Institute B",
        region="Northern",
        state="Delhi",
        address="Test Road, Delhi",
        contact_email="test.b@college.edu",
        contact_phone="011-87654321",
        institution_type="Engineering & Technology",
        created_at=now_ts,
        updated_at=now_ts,
    )
    owner_session.add(inst)
    owner_session.commit()
    return inst


@pytest.fixture
def make_user(owner_session: Session):
    """Factory fixture to create test users with hashed password."""
    def _create(username: str, role: str, institution_id=None, is_demo_admin=False, is_active=True, raw_password="ValidTestPassword123!") -> User:
        now_ts = clock_service.now()
        user = User(
            id=uuid.uuid4(),
            username=username,
            email=f"{username}@test.gov.in",
            hashed_password=hash_password(raw_password),
            full_name=f"Test {username.title()}",
            role=role,
            institution_id=institution_id,
            is_active=is_active,
            is_demo_admin=is_demo_admin,
            created_at=now_ts,
            updated_at=now_ts,
        )
        owner_session.add(user)
        owner_session.commit()
        return user
    return _create


@pytest.fixture
def auth_client_for(client: TestClient):
    """Helper to configure client with session & CSRF cookies for a given user."""
    def _auth(user: User) -> TestClient:
        token = create_access_token(user.id)
        csrf = generate_csrf_token()
        client.cookies.set(settings.SESSION_COOKIE_NAME, token)
        client.cookies.set(settings.CSRF_COOKIE_NAME, csrf)
        return client, csrf
    return _auth
