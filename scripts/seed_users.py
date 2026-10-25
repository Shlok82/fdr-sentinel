"""scripts/seed_users.py
Seeds initial AICTE regulatory institutions and user accounts across all RBAC roles.

Execution rule:
- Runs as the administrative DB owner role (`fdr_owner`) using settings.DATABASE_OWNER_URL.
- Passwords come STRICTLY from the SEED_PASSWORD environment variable.
- Refuses to execute if SEED_PASSWORD is unset, 'REPLACE_ME', or shorter than 12 characters.
- Never prints or logs the secret password value.
- Fully idempotent: safe to run repeatedly without duplicate entries.
- Records all actions in the cryptographic audit log via AuditService.
"""
import os
import sys
import uuid
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.core.security import hash_password
from app.models.institution import Institution
from app.models.user import User
from app.services.audit_service import AuditService
from app.services.clock_service import clock_service


def seed_database(db_url: str = None) -> None:
    owner_url = db_url or settings.DATABASE_OWNER_URL
    seed_password = os.getenv("SEED_PASSWORD")

    # 1. Enforce password complexity & presence
    if not seed_password or seed_password.strip() == "REPLACE_ME" or len(seed_password.strip()) < 12:
        print("ERROR: SEED_PASSWORD environment variable must be set to a secure string with at least 12 characters.")
        print("Please configure SEED_PASSWORD in your environment and re-run.")
        sys.exit(1)

    print(f"Connecting to database via owner role ({owner_url.split('@')[-1]})...")
    engine = create_engine(owner_url, pool_pre_ping=True)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # Load offset into ClockService
        clock_service.load_offset_from_db(session)
        now_ts = clock_service.now()
        hashed_pw = hash_password(seed_password.strip())

        print("Seeding sample institutions...")
        # Institution 1: Western Region
        inst1 = session.query(Institution).filter_by(aicte_permanent_id="1-10972341").first()
        if not inst1:
            inst1 = Institution(
                id=uuid.uuid4(),
                aicte_permanent_id="1-10972341",
                name="Sardar Patel Institute of Technology",
                region="Western",
                state="Maharashtra",
                address="Bhavans Campus, Munshi Nagar, Andheri West, Mumbai 400058",
                contact_email="principal@spit.ac.in",
                contact_phone="022-26707440",
                institution_type="Engineering & Technology",
                created_at=now_ts,
                updated_at=now_ts,
            )
            session.add(inst1)
            session.flush()
            AuditService.append(
                session,
                entity_type="INSTITUTION",
                entity_id=inst1.id,
                action="SEED_INSTITUTION",
                actor_user_id=None,
                actor_role="SYSTEM",
                details={"aicte_permanent_id": inst1.aicte_permanent_id, "name": inst1.name}
            )
            print(f"  Created: {inst1.name} ({inst1.aicte_permanent_id})")
        else:
            print(f"  Existing: {inst1.name} ({inst1.aicte_permanent_id})")

        # Institution 2: Northern Region (Needed for institution isolation & scoping tests)
        inst2 = session.query(Institution).filter_by(aicte_permanent_id="1-10984512").first()
        if not inst2:
            inst2 = Institution(
                id=uuid.uuid4(),
                aicte_permanent_id="1-10984512",
                name="Delhi Technological University",
                region="Northern",
                state="Delhi",
                address="Shahbad Daulatpur, Main Bawana Road, Delhi 110042",
                contact_email="admin@dtu.ac.in",
                contact_phone="011-27871018",
                institution_type="Engineering & Technology",
                created_at=now_ts,
                updated_at=now_ts,
            )
            session.add(inst2)
            session.flush()
            AuditService.append(
                session,
                entity_type="INSTITUTION",
                entity_id=inst2.id,
                action="SEED_INSTITUTION",
                actor_user_id=None,
                actor_role="SYSTEM",
                details={"aicte_permanent_id": inst2.aicte_permanent_id, "name": inst2.name}
            )
            print(f"  Created: {inst2.name} ({inst2.aicte_permanent_id})")
        else:
            print(f"  Existing: {inst2.name} ({inst2.aicte_permanent_id})")

        # Users configuration
        users_to_seed = [
            {
                "username": "inst.admin",
                "email": "inst.admin@spit.ac.in",
                "full_name": "Dr. B. N. Chaudhari (Principal, SPIT)",
                "role": "INSTITUTION_ADMIN",
                "institution_id": inst1.id,
                "is_demo_admin": False,
            },
            {
                "username": "inst.admin2",
                "email": "inst.admin2@dtu.ac.in",
                "full_name": "Prof. Prateek Sharma (Dean, DTU)",
                "role": "INSTITUTION_ADMIN",
                "institution_id": inst2.id,
                "is_demo_admin": False,
            },
            {
                "username": "scrutiny.officer",
                "email": "scrutiny.officer@aicte-india.org",
                "full_name": "Dr. Rajesh Sharma (Scrutiny Officer)",
                "role": "SCRUTINY_OFFICER",
                "institution_id": None,
                "is_demo_admin": False,
            },
            {
                "username": "approver.authority",
                "email": "approver.authority@aicte-india.org",
                "full_name": "Prof. T. G. Sitharam (Regulation Bureau Head)",
                "role": "APPROVER_AUTHORITY",
                "institution_id": None,
                "is_demo_admin": False,
            },
            {
                "username": "auditor",
                "email": "auditor@aicte-india.org",
                "full_name": "Shri S. K. Raman (Vigilance Auditor)",
                "role": "AUDITOR",
                "institution_id": None,
                "is_demo_admin": False,
            },
            {
                # Dedicated Demo Administrator user (AUDITOR role with is_demo_admin=True)
                "username": "demo.admin",
                "email": "demo.admin@aicte-india.org",
                "full_name": "Demo Administrator (Evaluation Jury)",
                "role": "AUDITOR",
                "institution_id": None,
                "is_demo_admin": True,
            },
        ]

        print("Seeding RBAC user accounts...")
        for udata in users_to_seed:
            user = session.query(User).filter_by(username=udata["username"]).first()
            if not user:
                user = User(
                    id=uuid.uuid4(),
                    username=udata["username"],
                    email=udata["email"],
                    hashed_password=hashed_pw,
                    full_name=udata["full_name"],
                    role=udata["role"],
                    institution_id=udata["institution_id"],
                    is_active=True,
                    is_demo_admin=udata["is_demo_admin"],
                    created_at=now_ts,
                    updated_at=now_ts,
                )
                session.add(user)
                session.flush()
                AuditService.append(
                    session,
                    entity_type="USER",
                    entity_id=user.id,
                    action="SEED_USER",
                    actor_user_id=None,
                    actor_role="SYSTEM",
                    details={"username": user.username, "role": user.role, "is_demo_admin": user.is_demo_admin}
                )
                print(f"  Created user: {user.username} ({user.role})")
            else:
                user.hashed_password = hashed_pw
                user.is_active = True
                user.is_demo_admin = udata["is_demo_admin"]
                user.updated_at = now_ts
                session.flush()
                print(f"  Updated user credentials: {user.username} ({user.role})")

        session.commit()
        print("Database seeding completed successfully.")

    except Exception as exc:
        session.rollback()
        print(f"Error seeding database: {exc}")
        sys.exit(1)
    finally:
        session.close()


if __name__ == "__main__":
    seed_database()
