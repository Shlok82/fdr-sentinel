import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from psycopg import errors
from app.services.audit_service import AuditService, verify_chain, GENESIS_HASH


def test_empty_audit_chain(app_session: Session):
    """An empty audit chain is valid with 0 rows checked."""
    res = verify_chain(app_session)
    assert res.ok is True
    assert res.first_broken_id is None
    assert res.reason is None
    assert res.rows_checked == 0


def test_normal_chain_appends(app_session: Session):
    """Appending 12 records produces a valid hash chain with zero genesis previous_hash."""
    entity_id = uuid.uuid4()
    for i in range(1, 13):
        AuditService.append(
            app_session,
            entity_type="INSTRUMENT",
            entity_id=entity_id,
            action=f"ACTION_{i}",
            actor_user_id=uuid.uuid4(),
            actor_role="SCRUTINY_OFFICER",
            details={"step": i, "status": "OK"},
            actor_ip="192.168.1.100"
        )
        app_session.commit()

    # Verify genesis previous_hash
    genesis_row = app_session.execute(text("SELECT previous_hash FROM audit_log WHERE id = 1")).fetchone()
    assert genesis_row is not None
    assert genesis_row[0] == GENESIS_HASH

    # Verify entire chain
    res = verify_chain(app_session)
    assert res.ok is True
    assert res.first_broken_id is None
    assert res.reason is None
    assert res.rows_checked == 12


@pytest.mark.parametrize("target_position", ["genesis", "middle", "last"])
@pytest.mark.parametrize(
    "tamper_field,tamper_sql",
    [
        ("event_timestamp", "UPDATE audit_log SET event_timestamp = event_timestamp + interval '1 minute' WHERE id = :id"),
        ("entity_type", "UPDATE audit_log SET entity_type = 'TAMPERED_ENTITY' WHERE id = :id"),
        ("entity_id", "UPDATE audit_log SET entity_id = '00000000-0000-0000-0000-999999999999' WHERE id = :id"),
        ("action", "UPDATE audit_log SET action = 'TAMPERED_ACTION' WHERE id = :id"),
        ("actor_user_id", "UPDATE audit_log SET actor_user_id = '00000000-0000-0000-0000-888888888888' WHERE id = :id"),
        ("canonical_payload", "UPDATE audit_log SET canonical_payload = jsonb_set(canonical_payload, '{details,tampered}', 'true') WHERE id = :id"),
        ("previous_hash", "UPDATE audit_log SET previous_hash = repeat('f', 64) WHERE id = :id"),
        ("entry_hash", "UPDATE audit_log SET entry_hash = repeat('e', 64) WHERE id = :id"),
    ]
)
def test_tamper_detection_on_every_field(
    app_session: Session,
    owner_session: Session,
    target_position: str,
    tamper_field: str,
    tamper_sql: str
):
    """Tamper Verification Reporting Rule:
    
    When any cryptographic field in row N is modified:
    - If previous_hash or canonical_payload or any column contributing to the hash is altered,
      recomputed entry_hash or payload mismatch causes verification to fail AT THAT EXACT ROW (id=N).
    - If entry_hash itself is altered:
      * If N is middle or genesis, row N+1's previous_hash will fail to match row N's entry_hash,
        identifying row N+1 with CHAIN_LINK_BROKEN, OR row N will fail with HASH_MISMATCH.
      * In all cases, verify_chain immediately fails (ok=False) and points to the broken id.
    """
    total_rows = 10
    entity_id = uuid.uuid4()
    for i in range(1, total_rows + 1):
        AuditService.append(
            app_session,
            entity_type="INSTRUMENT",
            entity_id=entity_id,
            action=f"ACTION_{i}",
            actor_user_id=uuid.uuid4(),
            actor_role="SCRUTINY_OFFICER",
            details={"index": i},
        )
        app_session.commit()

    target_id = 1 if target_position == "genesis" else (5 if target_position == "middle" else total_rows)

    # Tamper via owner session
    owner_session.execute(text(tamper_sql), {"id": target_id})
    owner_session.commit()

    res = verify_chain(app_session)
    assert res.ok is False
    assert res.reason in ("HASH_MISMATCH", "PAYLOAD_MISMATCH", "CHAIN_LINK_BROKEN", "GENESIS_INVALID")
    # Assert that the broken id points to the tampered row or its immediate downstream dependent
    assert res.first_broken_id in (target_id, target_id + 1)


def test_actor_ip_not_part_of_hash(app_session: Session, owner_session: Session):
    """Documented rule: actor_ip_address is stored in its dedicated column but is excluded from the hash."""
    entity_id = uuid.uuid4()
    AuditService.append(
        app_session,
        entity_type="INSTRUMENT",
        entity_id=entity_id,
        action="LOGIN_TEST",
        actor_user_id=uuid.uuid4(),
        actor_role="INSTITUTION_ADMIN",
        details={},
        actor_ip="10.0.0.1"
    )
    app_session.commit()

    # Alter actor_ip via owner
    owner_session.execute(text("UPDATE audit_log SET actor_ip_address = '10.0.0.99' WHERE id = 1"))
    owner_session.commit()

    # Must still verify OK because actor_ip is not part of the canonical payload or hash
    res = verify_chain(app_session)
    assert res.ok is True
    assert res.rows_checked == 1


def test_delete_middle_row_breaks_chain(app_session: Session, owner_session: Session):
    """Deleting a row in the middle breaks the previous_hash link of the following row."""
    entity_id = uuid.uuid4()
    for i in range(1, 6):
        AuditService.append(
            app_session,
            entity_type="INSTRUMENT",
            entity_id=entity_id,
            action=f"STEP_{i}",
            actor_user_id=uuid.uuid4(),
            actor_role="AUDITOR",
            details={"step": i}
        )
        app_session.commit()

    # Delete row 3 via owner
    owner_session.execute(text("DELETE FROM audit_log WHERE id = 3"))
    owner_session.commit()

    res = verify_chain(app_session)
    assert res.ok is False
    assert res.first_broken_id == 4
    assert res.reason == "CHAIN_LINK_BROKEN"


def test_alter_row_id_breaks_chain(app_session: Session, owner_session: Session):
    """Altering the id of a row corrupts the payload binding and breaks the chain."""
    entity_id = uuid.uuid4()
    for i in range(1, 4):
        AuditService.append(
            app_session,
            entity_type="INSTRUMENT",
            entity_id=entity_id,
            action=f"STEP_{i}",
            actor_user_id=uuid.uuid4(),
            actor_role="AUDITOR",
            details={"step": i}
        )
        app_session.commit()

    # Alter id of row 2 to 999 via owner
    owner_session.execute(text("UPDATE audit_log SET id = 999 WHERE id = 2"))
    owner_session.commit()

    res = verify_chain(app_session)
    assert res.ok is False
    assert res.reason in ("PAYLOAD_MISMATCH", "HASH_MISMATCH", "CHAIN_LINK_BROKEN")


def test_app_user_privilege_enforcement(app_session: Session):
    """As fdr_app_user, UPDATE, DELETE, and TRUNCATE on audit_log are strictly denied by PostgreSQL."""
    entity_id = uuid.uuid4()
    AuditService.append(
        app_session,
        entity_type="INSTRUMENT",
        entity_id=entity_id,
        action="PERM_TEST",
        actor_user_id=uuid.uuid4(),
        actor_role="INSTITUTION_ADMIN",
        details={}
    )
    app_session.commit()

    # Test UPDATE forbidden
    with pytest.raises(Exception) as exc_update:
        app_session.execute(text("UPDATE audit_log SET action = 'HACKED' WHERE id = 1"))
        app_session.commit()
    assert "permission denied" in str(exc_update.value).lower()
    app_session.rollback()

    # Test DELETE forbidden
    with pytest.raises(Exception) as exc_delete:
        app_session.execute(text("DELETE FROM audit_log WHERE id = 1"))
        app_session.commit()
    assert "permission denied" in str(exc_delete.value).lower()
    app_session.rollback()

    # Test TRUNCATE forbidden
    with pytest.raises(Exception) as exc_truncate:
        app_session.execute(text("TRUNCATE TABLE audit_log"))
        app_session.commit()
    assert "permission denied" in str(exc_truncate.value).lower()
    app_session.rollback()


def test_concurrent_appends_no_forks(setup_test_database):
    """25+ concurrent appends from separate sessions: no forks, all unique IDs, chain verifies OK."""
    from tests.conftest import TestAppSession
    entity_id = uuid.uuid4()
    num_threads = 30

    def append_worker(idx: int):
        session = TestAppSession()
        try:
            record = AuditService.append(
                session,
                entity_type="CONCURRENT_TASK",
                entity_id=entity_id,
                action=f"THREAD_ACTION_{idx}",
                actor_user_id=uuid.uuid4(),
                actor_role="SYSTEM",
                details={"thread_index": idx}
            )
            session.commit()
            return record.id, record.entry_hash, record.previous_hash
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(append_worker, range(num_threads)))

    assert len(results) == num_threads

    # Verify with fresh session
    verify_session = TestAppSession()
    try:
        res = verify_chain(verify_session)
        assert res.ok is True, f"Concurrent chain verification failed: {res}"
        assert res.rows_checked == num_threads

        # Verify uniqueness of IDs
        ids = [r[0] for r in results]
        assert len(set(ids)) == num_threads
    finally:
        verify_session.close()


def test_rollback_sequence_gap_handling(app_session: Session):
    """Rolling back an append leaves a sequence gap; the next append chains to highest committed id."""
    entity_id = uuid.uuid4()

    # 1. First committed append
    AuditService.append(
        app_session,
        entity_type="INSTRUMENT",
        entity_id=entity_id,
        action="FIRST_COMMITTED",
        actor_user_id=uuid.uuid4(),
        actor_role="SCRUTINY_OFFICER",
        details={"seq": 1}
    )
    app_session.commit()

    # 2. Second append rolled back
    try:
        AuditService.append(
            app_session,
            entity_type="INSTRUMENT",
            entity_id=entity_id,
            action="SHOULD_ROLLBACK",
            actor_user_id=uuid.uuid4(),
            actor_role="SCRUTINY_OFFICER",
            details={"seq": 2}
        )
        raise RuntimeError("Intentional rollback trigger")
    except RuntimeError:
        app_session.rollback()

    # 3. Third append committed
    AuditService.append(
        app_session,
        entity_type="INSTRUMENT",
        entity_id=entity_id,
        action="THIRD_COMMITTED",
        actor_user_id=uuid.uuid4(),
        actor_role="SCRUTINY_OFFICER",
        details={"seq": 3}
    )
    app_session.commit()

    rows = app_session.execute(text("SELECT id, previous_hash, entry_hash FROM audit_log ORDER BY id ASC")).fetchall()
    assert len(rows) == 2
    # Verify sequence gap: row 1 followed by row 3
    assert rows[0][0] == 1
    assert rows[1][0] == 3

    # Row 3's previous_hash must match row 1's entry_hash (previous-row chaining DEC-010)
    assert rows[1][1] == rows[0][2]

    res = verify_chain(app_session)
    assert res.ok is True
    assert res.rows_checked == 2
