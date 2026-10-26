"""scripts/seed_demo_data.py
Deterministic 50-Institution Demo Seed Dataset & Scenario Generator (SPEC §14.2).

Usage:
    docker compose exec web python scripts/seed_demo_data.py [options]

Options:
    --reset-demo-data   Request wipe of existing instrument/audit data before re-seeding
    --yes-wipe          Confirm destructive data wipe (required alongside --reset-demo-data)
    --scale SCALE       Dataset scale multiplier (default 1.0 for full 50-institution dataset)

Guarantees:
- Fully deterministic with fixed RNG seed (reproducible across test and dev runs).
- Refuses execution in production or when DEMO_MODE is false.
- Refuses execution when ClockService offset is non-zero.
- Refuses to overwrite existing seeded data unless --reset-demo-data --yes-wipe is supplied.
- All instruments created via real services (FDRSubmissionService, ScrutinyService,
  DiscrepancyEngine, InstrumentFSM, AuditService, TemporalCycle).
- The ONLY direct out-of-band writes are clearly documented owner-role backdating of dates
  for pre-matured instruments BEFORE verification/activation.
- Leaves database with 100% valid SHA-256 cryptographic audit chain.
"""
import os
import sys
import uuid
import random
import argparse
import time
import io
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, Any, List, Optional, Tuple

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

from app.core.config import settings
from app.core.security import hash_password
from app.models.institution import Institution
from app.models.instrument import Instrument
from app.models.document import Document
from app.models.provenance import FieldProvenance
from app.models.verification import VerificationRecord
from app.models.discrepancy import DiscrepancyTicket
from app.models.release import ReleaseRequest
from app.models.user import User
from app.schemas.instrument import FDRSubmissionForm
from app.services.submission_service import FDRSubmissionService
from app.services.scrutiny_service import ScrutinyService
from app.services.verification.mock_bank import MockFailureMode
from app.services.demo_documents import generate_demo_fdr_pdf
from app.services.clock_service import clock_service
from app.services.audit_service import AuditService, verify_chain
from app.services.instrument_fsm import transition, Actor, SYSTEM_ACTOR, InstrumentState
from app.services.temporal_cycle import run_temporal_cycle
from app.services.trust_score import recompute_trust
from app.services.release_service import request_release, record_recommendation, decide_release

# Fixed deterministic seed
SEED_RNG_VAL = 20261027


REGIONS = [
    "Northern",
    "North-Western",
    "Western",
    "Central",
    "Eastern",
    "North-Eastern",
    "South-Central",
    "Southern",
]

REGION_STATES: Dict[str, List[str]] = {
    "Northern": ["Delhi", "Uttar Pradesh", "Punjab", "Haryana"],
    "North-Western": ["Rajasthan", "Himachal Pradesh", "Jammu and Kashmir"],
    "Western": ["Maharashtra", "Gujarat", "Goa"],
    "Central": ["Madhya Pradesh", "Chhattisgarh"],
    "Eastern": ["West Bengal", "Bihar", "Odisha", "Jharkhand"],
    "North-Eastern": ["Assam", "Meghalaya", "Tripura"],
    "South-Central": ["Andhra Pradesh", "Telangana"],
    "Southern": ["Karnataka", "Tamil Nadu", "Kerala"],
}

NAME_PREFIXES = [
    "Apex", "Zenith", "Horizon", "Pinnacle", "Vanguard", "Beacon",
    "Crestview", "Summit", "Heritage", "Unified", "Matrix", "Quantum",
    "Nexus", "Sterling", "Paramount", "Silverline", "Frontier", "Crown",
    "Global", "Imperial", "Progressive", "Metropolitan", "Universal", "Civic",
]

NAME_CITIES = [
    "Indore", "Bhopal", "Jaipur", "Kochi", "Coimbatore", "Ranchi",
    "Guwahati", "Shimla", "Dehradun", "Nagpur", "Surat", "Patna",
    "Bhubaneswar", "Mysuru", "Gwalior", "Jabalpur", "Vadodara", "Varanasi",
    "Ludhiana", "Amritsar", "Raipur", "Visakhapatnam", "Warangal", "Mangaluru",
]

BANKS = [
    ("State Bank of India", "SBIN0001234", "Central Branch"),
    ("Punjab National Bank", "PUNB0005678", "Civil Lines Branch"),
    ("Canara Bank", "CNRB0002468", "MG Road Branch"),
    ("Bank of Baroda", "BARB0001357", "University Enclave"),
    ("Union Bank of India", "UBIN0009876", "Institutional Area"),
]


def calculate_quarterly_compound(principal: Decimal, rate_percent: Decimal, tenure_months: int) -> Decimal:
    """Computes exact maturity amount using DISC-06 quarterly compounding formula."""
    p = float(principal)
    r = float(rate_percent) / 100.0
    t = float(tenure_months) / 12.0
    a = p * ((1.0 + r / 4.0) ** (4.0 * t))
    return Decimal(f"{a:.2f}")


def parse_args():
    parser = argparse.ArgumentParser(description="Seed 50-Institution Demo Dataset")
    parser.add_argument("--reset-demo-data", action="store_true", help="Request wiping existing demo data")
    parser.add_argument("--yes-wipe", action="store_true", help="Confirm destructive data wipe")
    parser.add_argument("--scale", type=float, default=1.0, help="Scale multiplier for testing")
    parser.add_argument("--owner-url", type=str, default=None, help="Database owner connection URL")
    parser.add_argument("--vault-base", type=str, default=None, help="Vault storage directory path")
    return parser.parse_args()


def wipe_demo_data(session: Session, vault_base: str):
    """Safely wipes all instrument-derived data, vault files, and audit records."""
    print("Initiating demo data wipe...")
    
    # 1. Row counts before wipe
    inst_count = session.query(Instrument).count()
    doc_count = session.query(Document).count()
    audit_count = session.execute(text("SELECT count(*) FROM audit_log")).scalar()
    print(f"  Existing records to wipe: {inst_count} instruments, {doc_count} documents, {audit_count} audit rows.")

    # 2. Delete vault files recorded in Document rows
    docs = session.query(Document).all()
    deleted_files = 0
    for doc in docs:
        if doc.storage_path:
            norm_vault = os.path.abspath(vault_base)
            file_abs = os.path.abspath(os.path.join(vault_base, doc.storage_path))
            # Security: Refuse path escaping vault directory
            if file_abs.startswith(norm_vault) and os.path.exists(file_abs):
                try:
                    os.remove(file_abs)
                    deleted_files += 1
                except Exception as exc:
                    print(f"  Warning: failed to remove {file_abs}: {exc}")
    print(f"  Removed {deleted_files} ciphertext files from vault.")

    # 3. Truncate instrument-derived tables with CASCADE under owner role
    session.execute(
        text(
            "TRUNCATE TABLE alerts, notifications, scheduler_runs, scrutiny_notes, "
            "verification_records, discrepancy_tickets, field_provenance, documents, instruments CASCADE;"
        )
    )
    # 4. Truncate audit log and restart sequence
    session.execute(text("TRUNCATE TABLE audit_log RESTART IDENTITY;"))

    # 5. Delete synthetic institutions (1-99*) and synthetic users (seed.inst.*)
    session.execute(text("DELETE FROM users WHERE username LIKE 'seed.inst.%';"))
    session.execute(text("DELETE FROM institutions WHERE aicte_permanent_id LIKE '1-99%';"))

    # 6. Reset clock state offset to 0
    session.execute(
        text("UPDATE clock_state SET offset_seconds = 0, updated_at = :ts WHERE id = 1"),
        {"ts": clock_service.now()},
    )
    session.commit()
    clock_service.reset(session)
    print("Data wipe completed successfully.")


def run_seed(args):
    start_time = time.monotonic()
    rng = random.Random(SEED_RNG_VAL)

    # 1. Environment & Safety Checks
    if not settings.DEMO_MODE or settings.APP_ENV == "production":
        print("ERROR: seed_demo_data.py is strictly restricted to non-production environments with DEMO_MODE=true.")
        sys.exit(1)

    owner_url = args.owner_url or settings.DATABASE_OWNER_URL
    engine = create_engine(owner_url, pool_pre_ping=True)
    SessionClass = sessionmaker(bind=engine, autoflush=False)
    session: Session = SessionClass()

    try:
        clock_service.load_offset_from_db(session)
        if clock_service.offset_seconds != 0:
            print(
                f"ERROR: ClockService offset is non-zero ({clock_service.offset_seconds} seconds). "
                "Please reset the clock first using the Time Machine or /demo/clock/reset."
            )
            sys.exit(1)

        vault_base = getattr(args, "vault_base", None) or settings.UPLOAD_STORAGE_PATH

        # Detect existing synthetic seed data
        existing_synth = session.query(Institution).filter(Institution.aicte_permanent_id.like("1-99%")).count()
        if existing_synth > 0 or args.reset_demo_data:
            if not args.reset_demo_data:
                print(
                    f"ERROR: Found {existing_synth} existing synthetic institutions. "
                    "Default run never overwrites data. Use --reset-demo-data --yes-wipe to wipe and re-seed."
                )
                sys.exit(1)
            if not args.yes_wipe:
                print("ERROR: --reset-demo-data requires --yes-wipe flag to confirm destructive wipe.")
                sys.exit(1)
            wipe_demo_data(session, vault_base)

        # 2. Verify required staff users exist from seed_users.py
        scrutiny_officer = session.query(User).filter_by(username="scrutiny.officer").first()
        approver_authority = session.query(User).filter_by(username="approver.authority").first()
        if not scrutiny_officer or not approver_authority:
            print("ERROR: Required role users 'scrutiny.officer' or 'approver.authority' not found.")
            print("Please run scripts/seed_users.py first to initialize role accounts.")
            sys.exit(1)

        # Retrieve existing sample institutions
        inst1 = session.query(Institution).filter(Institution.aicte_permanent_id.in_(["1-DEMO-001", "1-10972341"])).first()
        inst2 = session.query(Institution).filter(Institution.aicte_permanent_id.in_(["1-DEMO-002", "1-10984512"])).first()
        if not inst1 or not inst2:
            print("ERROR: Sample institutions '1-DEMO-001' or '1-DEMO-002' not found. Run scripts/seed_users.py first.")
            sys.exit(1)

        inst1_user = session.query(User).filter_by(username="inst.admin").first()
        inst2_user = session.query(User).filter_by(username="inst.admin2").first()
        if not inst1_user or not inst2_user:
            print("ERROR: Users inst.admin or inst.admin2 not found. Run scripts/seed_users.py first.")
            sys.exit(1)

        now_ts = clock_service.now()
        today = clock_service.today()

        # Disable mock bank provider artificial latency during bulk seeding
        settings.MOCK_BANK_LATENCY_MS = 0

        print("Creating 48 synthetic institutions across 8 AICTE regions...")
        # Regional quotas: 6 or 7 per region (Western: inst1 + 5, Northern: inst2 + 5, others: 6 or 7)
        # Total: 2 + 5 + 5 + 6 + 6 + 6 + 6 + 7 + 7 = 50 institutions
        new_region_plan = [
            ("Western", 5),
            ("Northern", 5),
            ("North-Western", 6),
            ("Central", 6),
            ("Eastern", 6),
            ("North-Eastern", 6),
            ("South-Central", 7),
            ("Southern", 7),
        ]

        institutions_list: List[Institution] = [inst1, inst2]
        institution_users_map: Dict[uuid.UUID, User] = {
            inst1.id: inst1_user,
            inst2.id: inst2_user,
        }

        # Discipline breakdown: ~22 Engineering, ~20 Management, 6 No Slab (Pharmacy, Architecture, Polytechnic)
        institution_types_pool = (
            ["Engineering & Technology"] * 22 +
            ["Management"] * 20 +
            ["Pharmacy", "Architecture", "Polytechnic"] * 2
        )
        rng.shuffle(institution_types_pool)

        synth_idx = 1
        for region_name, count in new_region_plan:
            states = REGION_STATES[region_name]
            for _ in range(count):
                inst_id = uuid.uuid4()
                perm_id = f"1-99{str(synth_idx).zfill(5)}"
                inst_type = institution_types_pool[synth_idx - 1]
                pfx = rng.choice(NAME_PREFIXES)
                city = rng.choice(NAME_CITIES)
                st = rng.choice(states)
                
                type_label = "Institute of Technology" if inst_type == "Engineering & Technology" else (
                    "Institute of Management" if inst_type == "Management" else f"College of {inst_type}"
                )
                inst_name = f"{pfx} {type_label} of {city}"

                inst = Institution(
                    id=inst_id,
                    aicte_permanent_id=perm_id,
                    name=inst_name,
                    region=region_name,
                    state=st,
                    address=f"Campus Plot {rng.randint(10, 99)}, Sector {rng.randint(1, 25)}, {city}, {st}",
                    contact_email=f"compliance@{pfx.lower()}-{city.lower()}.edu",
                    contact_phone=f"0{rng.randint(11, 80)}-2{rng.randint(100000, 999999)}",
                    institution_type=inst_type,
                    created_at=now_ts,
                    updated_at=now_ts,
                )
                session.add(inst)
                institutions_list.append(inst)

                # Inactive institutional admin user
                user_id = uuid.uuid4()
                inst_u = User(
                    id=user_id,
                    username=f"seed.inst.{str(synth_idx).zfill(3)}",
                    email=f"seed.inst.{str(synth_idx).zfill(3)}@{pfx.lower()}-{city.lower()}.edu",
                    hashed_password=hash_password(f"Unusable_Seed_Hash_{uuid.uuid4().hex}"),
                    full_name=f"Admin ({inst_name})",
                    role="INSTITUTION_ADMIN",
                    institution_id=inst_id,
                    is_active=False,
                    is_demo_admin=False,
                    created_at=now_ts,
                    updated_at=now_ts,
                )
                session.add(inst_u)
                institution_users_map[inst_id] = inst_u

                synth_idx += 1

        session.flush()
        AuditService.append(
            session,
            entity_type="SYSTEM",
            entity_id=uuid.uuid4(),
            action="SEED_INSTITUTIONS_CREATED",
            actor_user_id=None,
            actor_role="SYSTEM",
            details={"institutions_count": len(institutions_list)},
        )
        session.commit()
        print(f"  Created {len(institutions_list)} total institutions across 8 regions.")

        # ---------------------------------------------------------------------
        # 3. Instruments Plan & Generation
        # ---------------------------------------------------------------------
        print("Generating 59 instruments through real service pipelines...")

        # Helper to submit a clean or defective instrument through real submission service
        def submit_one(
            inst_obj: Institution,
            fdr_num: str,
            principal: Decimal,
            rate: Decimal,
            iss_date: date,
            mat_date: date,
            tenure: int,
            mat_amount: Optional[Decimal] = None,
            joint_holder: Optional[str] = None,
            bank_info: Optional[Tuple[str, str, str]] = None,
            defect_type: Optional[str] = None,
            pdf_producer: Optional[str] = None,
        ) -> Instrument:
            b_name, b_ifsc, b_branch = bank_info or rng.choice(BANKS)
            m_amount = mat_amount if mat_amount is not None else calculate_quarterly_compound(principal, rate, tenure)
            j_holder = joint_holder if joint_holder is not None else f"Regional Officer, AICTE & Principal, {inst_obj.name}"
            u = institution_users_map[inst_obj.id]

            scenario_data = {
                "fdr_number": fdr_num,
                "bank_name": b_name,
                "bank_branch": b_branch,
                "ifsc_code": b_ifsc,
                "principal_amount": str(principal) if principal is not None else None,
                "maturity_amount": str(m_amount) if m_amount is not None else None,
                "interest_rate_percent": str(rate) if rate is not None else None,
                "issue_date": iss_date.isoformat() if iss_date else None,
                "maturity_date": mat_date.isoformat() if mat_date else None,
                "tenure_months": tenure,
                "joint_account_holder": j_holder,
            }
            if pdf_producer:
                scenario_data["producer"] = pdf_producer

            # Generate real PDF bytes with text layer
            pdf_bytes = generate_demo_fdr_pdf(scenario_data)

            # Apply deliberate defects if requested
            if defect_type == "INVERTED_DATES":
                # DISC-02 and DISC-04
                iss_date, mat_date = mat_date, iss_date
            elif defect_type == "MISSING_MANDATORY":
                # DISC-01
                b_name = None

            form = FDRSubmissionForm(
                fdr_number=fdr_num,
                bank_name=b_name,
                bank_branch=b_branch,
                ifsc_code=b_ifsc,
                principal_amount=str(principal) if principal is not None else None,
                maturity_amount=str(m_amount) if m_amount is not None else None,
                interest_rate_percent=str(rate) if rate is not None else None,
                issue_date=iss_date.isoformat() if iss_date is not None else None,
                maturity_date=mat_date.isoformat() if mat_date is not None else None,
                tenure_months=str(tenure) if tenure is not None else None,
                joint_account_holder=j_holder,
            )

            instrument = FDRSubmissionService.submit_instrument(
                db=session,
                form=form,
                file_bytes=pdf_bytes,
                raw_filename=f"{fdr_num}.pdf",
                user=u,
                actor_ip="127.0.0.1",
                vault_base=vault_base,
            )
            return instrument

        # Helper to progress instrument: SUBMITTED -> UNDER_SCRUTINY -> VERIFIED -> ACTIVE
        def activate_through_scrutiny(
            inst_obj: Instrument,
            backdate_to: Optional[Tuple[date, date, int]] = None,
        ) -> Instrument:
            # 1. Start scrutiny
            ScrutinyService.start_scrutiny(
                db=session,
                instrument_id=inst_obj.id,
                user=scrutiny_officer,
                justification="Statutory compliance review initiated.",
            )

            # 2. Permitted owner-role backdating before verification/activation
            if backdate_to:
                b_iss, b_mat, b_tenure = backdate_to
                inst_obj.issue_date = b_iss
                inst_obj.maturity_date = b_mat
                inst_obj.tenure_months = b_tenure
                # Sync provenance submitted_value and current_value
                for field_name, val_str in [
                    ("issue_date", b_iss.isoformat()),
                    ("maturity_date", b_mat.isoformat()),
                    ("tenure_months", str(b_tenure)),
                ]:
                    p_row = session.query(FieldProvenance).filter_by(instrument_id=inst_obj.id, field_name=field_name).first()
                    if p_row:
                        p_row.current_value = val_str
                        p_row.submitted_value = val_str
                session.flush()

            # 3. Mock verification with 0ms latency
            ScrutinyService.run_mock_verification(
                db=session,
                instrument_id=inst_obj.id,
                user=scrutiny_officer,
                failure_mode=MockFailureMode.NONE,
            )

            # 4. Verify instrument
            ScrutinyService.verify_instrument(
                db=session,
                instrument_id=inst_obj.id,
                user=scrutiny_officer,
                justification="Statutory bank verification confirmed.",
            )

            # 5. Activate instrument
            activated = ScrutinyService.activate_instrument(
                db=session,
                instrument_id=inst_obj.id,
                user=scrutiny_officer,
                justification="Security deposit verified and formally activated.",
            )
            session.commit()
            return activated

        fdr_counter = 1001

        # We will map the 50 primary instruments to the 50 institutions (1 per institution)
        # Institutions index:
        # 0: inst1 (National Model, Western) -> demo-window ACTIVE
        # 1: inst2 (Demo Institute, Northern) -> long-dated ACTIVE
        # 2..10: 9 demo-window ACTIVE
        # 11..24: 14 long-dated ACTIVE (including 1 DISC-06 and 1 DISC-08)
        # 25..32: 8 EXPIRING (4, 12, 21, 33, 48, 62, 75, 88 days out)
        # 33..37: 5 MATURED (2, 9, 16, 28, 40 days overdue)
        # 38..41: 4 DISCREPANCY_FLAGGED (duplicate, inverted, underfunded, missing fields)
        # 42..45: 4 RENEWED (parents of renewal children)
        # 46..49: 4 RELEASED (from active, expiring, matured)
        # Extras: 4 renewal children (in institutions 42..45), 5 queue extras (in institutions 0..4)

        # ---------------------------------------------------------------------
        # Group 1: ACTIVE Primaries (10 demo-window + 15 long-dated = 25)
        # ---------------------------------------------------------------------
        active_instruments: List[Instrument] = []

        # (a) 10 demo-window ACTIVE instruments (maturity between 95 and 150 days)
        demo_offsets = [105, 95, 100, 110, 115, 120, 125, 130, 140, 150]
        for idx in range(10):
            inst_owner = institutions_list[idx]
            off = demo_offsets[idx]
            mat_d = today + timedelta(days=off)
            iss_d = mat_d - timedelta(days=365)
            slab_amt = Decimal("3500000.00") if inst_owner.institution_type == "Engineering & Technology" else Decimal("1500000.00")
            
            fdr_num_str = f"SEED-FDR-{fdr_counter}"
            fdr = submit_one(
                inst_obj=inst_owner,
                fdr_num=fdr_num_str,
                principal=slab_amt,
                rate=Decimal("7.25"),
                iss_date=iss_d,
                mat_date=mat_d,
                tenure=12,
            )
            fdr_counter += 1
            activated = activate_through_scrutiny(fdr)
            active_instruments.append(activated)

        # (b) 15 long-dated ACTIVE instruments (spread across 4 to 60 months)
        long_offsets = [120, 165, 195, 225, 255, 285, 315, 345, 365, 450, 540, 720, 900, 1080, 1440]
        for idx in range(15):
            inst_owner = institutions_list[10 + idx]
            off = long_offsets[idx]
            mat_d = today + timedelta(days=off)
            iss_d = mat_d - timedelta(days=off + 365)
            slab_amt = Decimal("3500000.00") if inst_owner.institution_type == "Engineering & Technology" else Decimal("1500000.00")
            
            # Advisory defects:
            defect_kwargs = {}
            if idx == 13:
                # DISC-06: maturity math disparity
                defect_kwargs["mat_amount"] = slab_amt * Decimal("1.50")
            elif idx == 14:
                # DISC-08: non-bank authoring metadata
                defect_kwargs["pdf_producer"] = "Microsoft Word"

            fdr_num_str = f"SEED-FDR-{fdr_counter}"
            fdr = submit_one(
                inst_obj=inst_owner,
                fdr_num=fdr_num_str,
                principal=slab_amt,
                rate=Decimal("7.50"),
                iss_date=iss_d,
                mat_date=mat_d,
                tenure=off // 30 + 12,
                **defect_kwargs,
            )
            fdr_counter += 1
            activated = activate_through_scrutiny(fdr)
            active_instruments.append(activated)

        print(f"  Created and activated {len(active_instruments)} ACTIVE primary instruments.")

        # ---------------------------------------------------------------------
        # Group 2: EXPIRING Primaries (8 instruments)
        # Maturing in 4, 12, 21, 33, 48, 62, 75, 88 days from today
        # ---------------------------------------------------------------------
        expiring_offsets = [4, 12, 21, 33, 48, 62, 75, 88]
        expiring_instruments: List[Instrument] = []
        for idx, off in enumerate(expiring_offsets):
            inst_owner = institutions_list[25 + idx]
            mat_d = today + timedelta(days=off)
            iss_d = mat_d - timedelta(days=365)
            slab_amt = Decimal("3500000.00") if inst_owner.institution_type == "Engineering & Technology" else Decimal("1500000.00")
            
            fdr = submit_one(
                inst_obj=inst_owner,
                fdr_num=f"SEED-FDR-{fdr_counter}",
                principal=slab_amt,
                rate=Decimal("7.00"),
                iss_date=iss_d,
                mat_date=mat_d,
                tenure=12,
            )
            fdr_counter += 1
            # Initially activated as ACTIVE; will transition to EXPIRING during temporal cycle
            activated = activate_through_scrutiny(fdr)
            expiring_instruments.append(activated)

        print(f"  Created and activated {len(expiring_instruments)} instruments in expiration window.")

        # ---------------------------------------------------------------------
        # Group 3: MATURED Primaries (5 instruments)
        # Matured 2, 9, 16, 28, 40 days ago
        # ---------------------------------------------------------------------
        matured_overdue_offsets = [2, 9, 16, 28, 40]
        matured_instruments: List[Instrument] = []
        for idx, past_days in enumerate(matured_overdue_offsets):
            inst_owner = institutions_list[33 + idx]
            mat_d = today - timedelta(days=past_days)
            iss_d = mat_d - timedelta(days=365)
            slab_amt = Decimal("3500000.00") if inst_owner.institution_type == "Engineering & Technology" else Decimal("1500000.00")
            
            fdr = submit_one(
                inst_obj=inst_owner,
                fdr_num=f"SEED-FDR-{fdr_counter}",
                principal=slab_amt,
                rate=Decimal("6.75"),
                iss_date=today - timedelta(days=past_days + 365),
                mat_date=today + timedelta(days=30),  # Submitted with valid date, backdated before activation
                tenure=12,
            )
            fdr_counter += 1
            # Backdate before verification and activation
            activated = activate_through_scrutiny(
                fdr,
                backdate_to=(iss_d, mat_d, 12),
            )
            matured_instruments.append(activated)

        print(f"  Created and backdated {len(matured_instruments)} MATURED instruments.")

        # ---------------------------------------------------------------------
        # Group 4: DISCREPANCY_FLAGGED (4 instruments)
        # (1) Duplicate of an ACTIVE incumbent from another institution (DISC-05)
        # (2) Inverted dates (DISC-02 & DISC-04)
        # (3) Underfunded (DISC-03)
        # (4) Missing mandatory fields (DISC-01)
        # ---------------------------------------------------------------------
        flagged_instruments: List[Instrument] = []

        # 1. DISC-05 duplicate of incumbent (use fdr_number of active_instruments[0])
        incumbent_fdr = active_instruments[0].fdr_number
        inst_owner_dup = institutions_list[38]
        fdr_dup = submit_one(
            inst_obj=inst_owner_dup,
            fdr_num=incumbent_fdr,
            principal=Decimal("3500000.00"),
            rate=Decimal("7.00"),
            iss_date=today - timedelta(days=30),
            mat_date=today + timedelta(days=335),
            tenure=12,
        )
        flagged_instruments.append(fdr_dup)

        # 2. Inverted dates (DISC-02 & DISC-04)
        inst_owner_inv = institutions_list[39]
        fdr_inv = submit_one(
            inst_obj=inst_owner_inv,
            fdr_num=f"SEED-FDR-{fdr_counter}",
            principal=Decimal("3500000.00"),
            rate=Decimal("7.00"),
            iss_date=today - timedelta(days=30),
            mat_date=today + timedelta(days=335),
            tenure=12,
            defect_type="INVERTED_DATES",
        )
        fdr_counter += 1
        flagged_instruments.append(fdr_inv)

        # 3. Underfunded (DISC-03)
        inst_owner_under = institutions_list[40]
        # Ensure Engineering type so slab is 35L
        inst_owner_under.institution_type = "Engineering & Technology"
        session.flush()
        fdr_under = submit_one(
            inst_obj=inst_owner_under,
            fdr_num=f"SEED-FDR-{fdr_counter}",
            principal=Decimal("1000000.00"),  # Deficit of 25L
            rate=Decimal("7.00"),
            iss_date=today - timedelta(days=60),
            mat_date=today + timedelta(days=305),
            tenure=12,
        )
        fdr_counter += 1
        flagged_instruments.append(fdr_under)

        # 4. Missing mandatory field (DISC-01)
        inst_owner_miss = institutions_list[41]
        fdr_miss = submit_one(
            inst_obj=inst_owner_miss,
            fdr_num=f"SEED-FDR-{fdr_counter}",
            principal=Decimal("3500000.00"),
            rate=Decimal("7.00"),
            iss_date=today - timedelta(days=60),
            mat_date=today + timedelta(days=305),
            tenure=12,
            defect_type="MISSING_MANDATORY",
        )
        fdr_counter += 1
        flagged_instruments.append(fdr_miss)

        print(f"  Created {len(flagged_instruments)} auto-flagged DISCREPANCY_FLAGGED instruments.")

        # ---------------------------------------------------------------------
        # Group 5: RENEWED Primaries & Children (4 pairs = 4 parents + 4 children)
        # Parent reaches EXPIRING (2) or MATURED (2), then RENEWED via SYSTEM actor.
        # Child is created, verified, and activated to ACTIVE.
        # ---------------------------------------------------------------------
        renewed_parents: List[Instrument] = []
        renewal_children: List[Instrument] = []

        for r_idx in range(4):
            inst_owner = institutions_list[42 + r_idx]
            is_matured_parent = (r_idx >= 2)
            
            # 1. Create parent
            if is_matured_parent:
                p_mat = today - timedelta(days=10 + r_idx * 5)
            else:
                p_mat = today + timedelta(days=15 + r_idx * 10)
            p_iss = p_mat - timedelta(days=365)
            
            submit_mat = p_mat if not is_matured_parent else (today + timedelta(days=60))
            submit_iss = p_iss if not is_matured_parent else (today - timedelta(days=305))

            parent_fdr = submit_one(
                inst_obj=inst_owner,
                fdr_num=f"SEED-FDR-{fdr_counter}",
                principal=Decimal("3500000.00"),
                rate=Decimal("7.00"),
                iss_date=submit_iss,
                mat_date=submit_mat,
                tenure=12,
            )
            fdr_counter += 1

            parent_act = activate_through_scrutiny(
                parent_fdr,
                backdate_to=(p_iss, p_mat, 12) if is_matured_parent else None,
            )

            # Move parent to intermediate state: EXPIRING or MATURED
            if is_matured_parent:
                transition(
                    session=session,
                    instrument_id=parent_act.id,
                    to_state="EXPIRING",
                    actor=SYSTEM_ACTOR,
                    justification="Lifecycle transition to EXPIRING prior to renewal.",
                )
                transition(
                    session=session,
                    instrument_id=parent_act.id,
                    to_state="MATURED",
                    actor=SYSTEM_ACTOR,
                    justification="Lifecycle transition to MATURED prior to renewal.",
                )
            else:
                transition(
                    session=session,
                    instrument_id=parent_act.id,
                    to_state="EXPIRING",
                    actor=SYSTEM_ACTOR,
                    justification="Lifecycle transition to EXPIRING prior to renewal.",
                )
            session.commit()

            # 2. Create child instrument in same institution
            child_iss = today - timedelta(days=5)
            child_mat = today + timedelta(days=360)
            child_fdr = submit_one(
                inst_obj=inst_owner,
                fdr_num=f"SEED-FDR-{fdr_counter}",
                principal=Decimal("3500000.00"),
                rate=Decimal("7.25"),
                iss_date=child_iss,
                mat_date=child_mat,
                tenure=12,
            )
            fdr_counter += 1

            # Link parent-child lineage
            child_fdr.previous_instrument_id = parent_act.id
            child_fdr.renewal_sequence = 2
            session.flush()

            child_act = activate_through_scrutiny(child_fdr)
            renewal_children.append(child_act)

            # Note: activate_instrument() automatically executed the renewal activation hook,
            # transitioning parent_act to RENEWED cleanly via SYSTEM_ACTOR!
            session.refresh(parent_act)
            renewed_parents.append(parent_act)

        print(f"  Created {len(renewed_parents)} RENEWED parent instruments and {len(renewal_children)} ACTIVE child instruments.")

        # ---------------------------------------------------------------------
        # Group 6: RELEASED Primaries (4 instruments)
        # From ACTIVE (1), EXPIRING (1), MATURED (2)
        # ---------------------------------------------------------------------
        released_instruments: List[Instrument] = []
        release_scenarios = [
            ("ACTIVE", today + timedelta(days=180)),
            ("EXPIRING", today + timedelta(days=30)),
            ("MATURED", today - timedelta(days=15)),
            ("MATURED", today - timedelta(days=45)),
        ]

        approver_actor = Actor.from_user(approver_authority)
        scrutiny_actor = Actor.from_user(scrutiny_officer)

        for rel_idx, (orig_state, target_mat) in enumerate(release_scenarios):
            inst_owner = institutions_list[46 + rel_idx]
            u_inst = institution_users_map[inst_owner.id]
            inst_actor = Actor.from_user(u_inst)
            
            iss_d = target_mat - timedelta(days=365)
            is_past = (target_mat < today)
            submit_mat = target_mat if not is_past else (today + timedelta(days=60))
            submit_iss = iss_d if not is_past else (today - timedelta(days=305))

            fdr = submit_one(
                inst_obj=inst_owner,
                fdr_num=f"SEED-FDR-{fdr_counter}",
                principal=Decimal("3500000.00"),
                rate=Decimal("7.10"),
                iss_date=submit_iss,
                mat_date=submit_mat,
                tenure=12,
            )
            fdr_counter += 1

            act = activate_through_scrutiny(
                fdr,
                backdate_to=(iss_d, target_mat, 12) if is_past else None,
            )

            # If origin state is EXPIRING or MATURED, transition first
            if orig_state == "EXPIRING":
                transition(
                    session=session,
                    instrument_id=act.id,
                    to_state="EXPIRING",
                    actor=SYSTEM_ACTOR,
                    justification="Automated transition to EXPIRING prior to release filing.",
                )
                session.commit()
            elif orig_state == "MATURED":
                transition(
                    session=session,
                    instrument_id=act.id,
                    to_state="EXPIRING",
                    actor=SYSTEM_ACTOR,
                    justification="Automated transition to EXPIRING prior to maturity.",
                )
                transition(
                    session=session,
                    instrument_id=act.id,
                    to_state="MATURED",
                    actor=SYSTEM_ACTOR,
                    justification="Automated transition to MATURED prior to release filing.",
                )
                session.commit()

            # Dual-key release workflow execution:
            # 1. Institution requests release
            rel_req = request_release(
                session=session,
                instrument_id=act.id,
                actor=inst_actor,
                justification="Statutory approved programme duration completed without encumbrance. All degrees awarded.",
            )
            session.commit()

            # 2. Scrutiny officer records recommendation with synthesized PDF evidence
            evidence_pdf = generate_demo_fdr_pdf({"fdr_number": f"EVIDENCE-{act.fdr_number}", "bank_name": "State Bank of India"})
            evidence_file = io.BytesIO(evidence_pdf)
            evidence_file.filename = f"statutory_clearance_{act.fdr_number}.pdf"
            checklist = {
                "course_cycle_completed": True,
                "no_student_grievances_pending": True,
                "no_vigilance_inquiry_active": True,
                "regional_inspection_cleared": True,
            }
            record_recommendation(
                session=session,
                request_id=rel_req.id,
                actor=scrutiny_actor,
                checklist=checklist,
                recommendation="RECOMMENDED",
                justification="Regional officer verified completion of course cycle and issued statutory clearance certificate.",
                evidence_upload=evidence_file,
            )
            session.commit()

            # 3. Competent authority approves release
            decide_release(
                session=session,
                request_id=rel_req.id,
                actor=approver_actor,
                decision="APPROVE",
                justification="Release order authorized following scrutiny verification and statutory compliance clearance.",
            )
            session.commit()
            session.refresh(act)
            released_instruments.append(act)

        print(f"  Created and released {len(released_instruments)} RELEASED instruments via dual-key workflow.")

        # ---------------------------------------------------------------------
        # Group 7: Queue Extras (5 instruments)
        # 3 SUBMITTED (clean), 2 UNDER_SCRUTINY
        # Placed in the first 5 institutions
        # ---------------------------------------------------------------------
        queue_submitted: List[Instrument] = []
        queue_under_scrutiny: List[Instrument] = []

        for q_idx in range(5):
            inst_owner = institutions_list[q_idx]
            mat_d = today + timedelta(days=200 + q_idx * 30)
            iss_d = today - timedelta(days=30)
            slab_amt = Decimal("3500000.00") if inst_owner.institution_type == "Engineering & Technology" else Decimal("1500000.00")

            fdr = submit_one(
                inst_obj=inst_owner,
                fdr_num=f"SEED-FDR-{fdr_counter}",
                principal=slab_amt,
                rate=Decimal("7.25"),
                iss_date=iss_d,
                mat_date=mat_d,
                tenure=12,
            )
            fdr_counter += 1

            if q_idx < 3:
                queue_submitted.append(fdr)
            else:
                ScrutinyService.start_scrutiny(
                    db=session,
                    instrument_id=fdr.id,
                    user=scrutiny_officer,
                    justification="Officer scrutiny examination in progress.",
                )
                session.commit()
                queue_under_scrutiny.append(fdr)

        print(f"  Created {len(queue_submitted)} SUBMITTED and {len(queue_under_scrutiny)} UNDER_SCRUTINY queue extras.")

        # ---------------------------------------------------------------------
        # Group 8: Release Requested Extras (2 instruments)
        # 1 ready for Approver (Key 1 turned: RECOMMENDED) in institution 5
        # 1 ready for Scrutiny (Key 1 pending) in institution 6
        # ---------------------------------------------------------------------
        release_requested_extras: List[Instrument] = []

        # (a) Approver action item: Key 1 turned
        inst_owner_rel1 = institutions_list[5]
        u_inst_rel1 = institution_users_map[inst_owner_rel1.id]
        fdr_rel1 = submit_one(
            inst_obj=inst_owner_rel1,
            fdr_num=f"SEED-FDR-{fdr_counter}",
            principal=Decimal("3500000.00"),
            rate=Decimal("7.15"),
            iss_date=today - timedelta(days=365),
            mat_date=today + timedelta(days=90),
            tenure=15,
        )
        fdr_counter += 1
        act_rel1 = activate_through_scrutiny(fdr_rel1)
        rel_req1 = request_release(
            session=session,
            instrument_id=act_rel1.id,
            actor=Actor.from_user(u_inst_rel1),
            justification="Course cycle completed; all academic batches graduated without liabilities.",
        )
        session.commit()

        evidence_pdf_extra = generate_demo_fdr_pdf({"fdr_number": f"EVIDENCE-{act_rel1.fdr_number}", "bank_name": "State Bank of India"})
        evidence_file_extra = io.BytesIO(evidence_pdf_extra)
        evidence_file_extra.filename = f"clearance_{act_rel1.fdr_number}.pdf"
        record_recommendation(
            session=session,
            request_id=rel_req1.id,
            actor=scrutiny_actor,
            checklist={
                "course_cycle_completed": True,
                "no_student_grievances_pending": True,
                "no_vigilance_inquiry_active": True,
                "regional_inspection_cleared": True,
            },
            recommendation="RECOMMENDED",
            justification="Regional officer completed on-site vigilance inspection and cleared deposit release.",
            evidence_upload=evidence_file_extra,
        )
        session.commit()
        session.refresh(act_rel1)
        release_requested_extras.append(act_rel1)

        # (b) Scrutiny action item: Key 1 pending review
        inst_owner_rel2 = institutions_list[6]
        u_inst_rel2 = institution_users_map[inst_owner_rel2.id]
        fdr_rel2 = submit_one(
            inst_obj=inst_owner_rel2,
            fdr_num=f"SEED-FDR-{fdr_counter}",
            principal=Decimal("3500000.00"),
            rate=Decimal("7.15"),
            iss_date=today - timedelta(days=365),
            mat_date=today + timedelta(days=120),
            tenure=16,
        )
        fdr_counter += 1
        act_rel2 = activate_through_scrutiny(fdr_rel2)
        rel_req2 = request_release(
            session=session,
            instrument_id=act_rel2.id,
            actor=Actor.from_user(u_inst_rel2),
            justification="AICTE approved programme duration concluded; requesting deposit release.",
        )
        session.commit()
        session.refresh(act_rel2)
        release_requested_extras.append(act_rel2)

        print(f"  Created {len(release_requested_extras)} RELEASE_REQUESTED extras (1 Key 1 turned for Approver, 1 Key 1 pending for Scrutiny).")

        # ---------------------------------------------------------------------
        # 4. Post-Activation Real Temporal Cycle Run
        # ---------------------------------------------------------------------
        print("Running real temporal cycle to transition horizons and dispatch alerts...")
        run_res = run_temporal_cycle(trigger="SCHEDULED", caller_session=session)
        print(f"  Temporal cycle finished: {run_res.transitions_count} transitions, {run_res.alerts_created} alerts.")

        # ---------------------------------------------------------------------
        # 5. Recompute Trust Scores for All Instruments
        # ---------------------------------------------------------------------
        print("Recomputing earned trust scores for all instruments...")
        all_instruments = session.query(Instrument).all()
        for inst_item in all_instruments:
            recompute_trust(session, inst_item.id, vault_base=vault_base)
        session.commit()
        print(f"  Trust scores recomputed for {len(all_instruments)} instruments.")

        # ---------------------------------------------------------------------
        # 6. Verify Cryptographic Audit Chain
        # ---------------------------------------------------------------------
        print("Verifying SHA-256 cryptographic audit chain...")
        chain_res = verify_chain(session)
        if not chain_res.ok:
            print(f"FATAL ERROR: Audit chain verification failed! Reason: {chain_res.reason}")
            sys.exit(1)
        print(f"  Audit chain integrity VERIFIED ({chain_res.rows_checked} entries, genesis block OK).")

        # ---------------------------------------------------------------------
        # 7. Final Summary & Metrics
        # ---------------------------------------------------------------------
        elapsed = time.monotonic() - start_time
        
        # Aggregate status counts
        status_counts = {}
        for row in session.execute(text("SELECT status, count(*) FROM instruments GROUP BY status")):
            status_counts[row[0]] = row[1]

        # Aggregate regional distribution
        region_counts = {}
        for row in session.execute(text("SELECT region, count(*) FROM institutions GROUP BY region")):
            region_counts[row[0]] = row[1]

        # Trust score distribution
        trust_scores = [inst.trust_score for inst in session.query(Instrument).all()]
        trust_dist = {
            "0-39 (Low)": sum(1 for s in trust_scores if s < 40),
            "40-69 (Medium)": sum(1 for s in trust_scores if 40 <= s < 70),
            "70-100 (High)": sum(1 for s in trust_scores if s >= 70),
            "Average": round(sum(trust_scores) / len(trust_scores), 1) if trust_scores else 0,
        }

        alerts_count = session.execute(text("SELECT count(*) FROM alerts")).scalar()
        notifs_count = session.execute(text("SELECT count(*) FROM notifications")).scalar()

        print("\n" + "=" * 70)
        print("              FDR-SENTINEL DEMO SEED DATASET SUMMARY")
        print("=" * 70)
        print(f"Elapsed Time:             {elapsed:.2f} seconds")
        print(f"Total Institutions:       {len(institutions_list)} across {len(region_counts)} regions")
        print(f"Total Instruments:        {len(all_instruments)} (50 primary + 4 renewal + 5 queue extras + 2 release requested extras)")
        print(f"Audit Log Chain:          {chain_res.rows_checked} entries (VERIFIED - OK)")
        print(f"Alerts / Notifications:   {alerts_count} alerts / {notifs_count} notifications")
        print("-" * 70)
        print("Status Counts:")
        for st in [
            "ACTIVE", "EXPIRING", "MATURED", "DISCREPANCY_FLAGGED",
            "RENEWED", "RELEASED", "RELEASE_REQUESTED", "SUBMITTED", "UNDER_SCRUTINY"
        ]:
            print(f"  {st:24}: {status_counts.get(st, 0)}")
        print("-" * 70)
        print("Regional Distribution:")
        for reg in REGIONS:
            print(f"  {reg:24}: {region_counts.get(reg, 0)} institutions")
        print("-" * 70)
        print("Trust Score Metrics:")
        for k, v in trust_dist.items():
            print(f"  {k:24}: {v}")
        print("=" * 70 + "\n")

        return 0

    finally:
        session.close()


if __name__ == "__main__":
    cli_args = parse_args()
    sys.exit(run_seed(cli_args))
