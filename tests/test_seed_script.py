import os
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from scripts.seed_users import seed_database
from tests.conftest import TEST_OWNER_URL


def test_seed_script_refuses_missing_or_short_password(monkeypatch):
    """Seed script refuses execution if SEED_PASSWORD is unset, REPLACE_ME, or <12 chars."""
    # 1. Unset
    monkeypatch.delenv("SEED_PASSWORD", raising=False)
    with pytest.raises(SystemExit):
        seed_database(TEST_OWNER_URL)

    # 2. REPLACE_ME
    monkeypatch.setenv("SEED_PASSWORD", "REPLACE_ME")
    with pytest.raises(SystemExit):
        seed_database(TEST_OWNER_URL)

    # 3. Too short (<12 chars)
    monkeypatch.setenv("SEED_PASSWORD", "Short123!")
    with pytest.raises(SystemExit):
        seed_database(TEST_OWNER_URL)


def test_seed_script_execution_and_idempotence(monkeypatch, owner_session: Session):
    """Seed script creates all mandated institutions and users, and is completely idempotent."""
    monkeypatch.setenv("SEED_PASSWORD", "ComplexDevPassword2026!@#")

    # First execution: creates records
    seed_database(TEST_OWNER_URL)

    # Verify institutions seeded
    inst_count = owner_session.execute(text("SELECT count(*) FROM institutions")).scalar()
    assert inst_count == 2

    # Verify users seeded (4 roles + demo.admin + 2nd inst admin = 6 users)
    user_count = owner_session.execute(text("SELECT count(*) FROM users")).scalar()
    assert user_count == 6

    # Verify audit records for seeding
    audit_count = owner_session.execute(
        text("SELECT count(*) FROM audit_log WHERE action IN ('SEED_INSTITUTION', 'SEED_USER')")
    ).scalar()
    assert audit_count >= 8

    # Second execution: idempotence check (must not duplicate records)
    seed_database(TEST_OWNER_URL)

    inst_count_after = owner_session.execute(text("SELECT count(*) FROM institutions")).scalar()
    user_count_after = owner_session.execute(text("SELECT count(*) FROM users")).scalar()
    assert inst_count == inst_count_after
    assert user_count == user_count_after
