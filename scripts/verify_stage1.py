"""scripts/verify_stage1.py
Stage 1 verification script:
1. Verifies /healthz endpoint returns 200 OK and healthy status.
2. Checks that all 11 expected tables exist in PostgreSQL.
3. Tests database role boundaries:
   - Verifies fdr_app_user CAN read and insert into audit_log.
   - Verifies fdr_app_user CANNOT UPDATE or DELETE records in audit_log (permission denied).
4. Verifies unique constraint on alerts(instrument_id, alert_type).
"""
import sys
import uuid
import json
import httpx
import psycopg
from psycopg import errors

BASE_URL = "http://localhost:8000"
EXPECTED_TABLES = {
    "users",
    "institutions",
    "instruments",
    "documents",
    "field_provenance",
    "verification_records",
    "scrutiny_notes",
    "release_requests",
    "alerts",
    "audit_log",
    "clock_state",
}

OWNER_DSN = "postgresql://fdr_owner:fdr_owner_dev_password@localhost:5432/fdr_sentinel"
APP_DSN = "postgresql://fdr_app_user:fdr_app_dev_password@localhost:5432/fdr_sentinel"


def test_healthz():
    print("--> 1. Testing /healthz endpoint...")
    try:
        response = httpx.get(f"{BASE_URL}/healthz", timeout=10.0)
        assert response.status_code == 200, f"Expected 200 OK, got {response.status_code}: {response.text}"
        data = response.json()
        assert data.get("status") == "healthy", f"Status not healthy: {data}"
        assert data.get("database") == "connected", f"Database not connected: {data}"
        print(f"    PASS: /healthz returned 200 OK: {json.dumps(data)}")
    except Exception as exc:
        print(f"    FAIL: /healthz check failed: {exc}")
        return False
    return True


def test_schema():
    print("--> 2. Verifying all 11 tables exist in PostgreSQL schema...")
    try:
        with psycopg.connect(APP_DSN) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
                )
                tables = {row[0] for row in cur.fetchall()}
                missing = EXPECTED_TABLES - tables
                if missing:
                    print(f"    FAIL: Missing tables: {missing}")
                    return False
                print(f"    PASS: All 11 tables present: {sorted(list(EXPECTED_TABLES))}")
    except Exception as exc:
        print(f"    FAIL: Database schema query failed: {exc}")
        return False
    return True


def test_alert_unique_constraint():
    print("--> 3. Verifying UNIQUE constraint on alerts(instrument_id, alert_type)...")
    try:
        with psycopg.connect(OWNER_DSN) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT conname, contype 
                    FROM pg_constraint 
                    WHERE conrelid = 'alerts'::regclass AND conname = 'uq_alerts_instrument_alert_type'
                """)
                row = cur.fetchone()
                if not row or row[1] != 'u':
                    print("    FAIL: uq_alerts_instrument_alert_type constraint not found")
                    return False
                print("    PASS: Unique constraint uq_alerts_instrument_alert_type verified.")
    except Exception as exc:
        print(f"    FAIL: Constraint query failed: {exc}")
        return False
    return True


def test_audit_log_privilege_boundary():
    print("--> 4. Testing runtime role (fdr_app_user) privilege boundary on audit_log...")
    test_id = None
    try:
        # Step A: Insert test record as fdr_app_user (Should succeed)
        with psycopg.connect(APP_DSN) as conn:
            with conn.cursor() as cur:
                entity_id = str(uuid.uuid4())
                payload = json.dumps({"test": "initial_entry"})
                cur.execute(
                    """
                    INSERT INTO audit_log (
                        entity_type, entity_id, action, canonical_payload, previous_hash, entry_hash
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    ("SYSTEM", entity_id, "TEST_INIT", payload, "0" * 64, "a" * 64)
                )
                test_id = cur.fetchone()[0]
                conn.commit()
                print(f"    PASS: fdr_app_user successfully inserted into audit_log (row id={test_id})")

        # Step B: Select test record as fdr_app_user (Should succeed)
        with psycopg.connect(APP_DSN) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, action FROM audit_log WHERE id = %s", (test_id,))
                row = cur.fetchone()
                assert row is not None and row[1] == "TEST_INIT"
                print("    PASS: fdr_app_user successfully read from audit_log")

        # Step C: Attempt UPDATE as fdr_app_user (MUST FAIL with InsufficientPrivilege)
        update_denied = False
        try:
            with psycopg.connect(APP_DSN) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE audit_log SET action = 'TAMPERED' WHERE id = %s",
                        (test_id,)
                    )
                    conn.commit()
        except errors.InsufficientPrivilege as exc:
            update_denied = True
            print(f"    PASS: fdr_app_user UPDATE correctly DENIED by PostgreSQL ({exc.__class__.__name__})")
        except Exception as exc:
            if "permission denied" in str(exc).lower():
                update_denied = True
                print("    PASS: fdr_app_user UPDATE correctly DENIED (permission denied)")
            else:
                print(f"    FAIL: Unexpected exception during UPDATE test: {exc}")
                return False

        if not update_denied:
            print("    FAIL: SECURITY VIOLATION: fdr_app_user was allowed to UPDATE audit_log!")
            return False

        # Step D: Attempt DELETE as fdr_app_user (MUST FAIL with InsufficientPrivilege)
        delete_denied = False
        try:
            with psycopg.connect(APP_DSN) as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM audit_log WHERE id = %s", (test_id,))
                    conn.commit()
        except errors.InsufficientPrivilege:
            delete_denied = True
            print("    PASS: fdr_app_user DELETE correctly DENIED by PostgreSQL")
        except Exception as exc:
            if "permission denied" in str(exc).lower():
                delete_denied = True
                print("    PASS: fdr_app_user DELETE correctly DENIED (permission denied)")
            else:
                print(f"    FAIL: Unexpected exception during DELETE test: {exc}")
                return False

        if not delete_denied:
            print("    FAIL: SECURITY VIOLATION: fdr_app_user was allowed to DELETE from audit_log!")
            return False

        # Step E: Clean up test row using owner connection
        with psycopg.connect(OWNER_DSN) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM audit_log WHERE id = %s", (test_id,))
                conn.commit()
                print("    PASS: Test row cleaned up via fdr_owner connection")

    except Exception as exc:
        print(f"    FAIL: Privilege boundary test failed: {exc}")
        return False
    return True


def main():
    print("==================================================")
    print("FDR SENTINEL — STAGE 1 VERIFICATION SUITE")
    print("==================================================")
    
    success = True
    success = test_healthz() and success
    success = test_schema() and success
    success = test_alert_unique_constraint() and success
    success = test_audit_log_privilege_boundary() and success

    print("==================================================")
    if success:
        print("ALL STAGE 1 VERIFICATIONS PASSED SUCCESSFULLY!")
        print("==================================================")
        sys.exit(0)
    else:
        print("SOME VERIFICATIONS FAILED. PLEASE CHECK LOGS ABOVE.")
        print("==================================================")
        sys.exit(1)


if __name__ == "__main__":
    main()
