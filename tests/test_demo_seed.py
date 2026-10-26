"""tests/test_demo_seed.py
Automated Verification Suite for Stage 9 Demo Seed Dataset & Snapshot Tool (SPEC §14.2).
"""
import os
import argparse
from datetime import timedelta
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.institution import Institution
from app.models.instrument import Instrument
from app.models.discrepancy import DiscrepancyTicket
from app.services.clock_service import clock_service
from app.services.audit_service import verify_chain
from scripts.seed_users import seed_database
from scripts.seed_demo_data import run_seed, REGIONS
from tests.conftest import TEST_OWNER_URL, TestOwnerSession


def _make_args(**kwargs):
    defaults = {
        "reset_demo_data": False,
        "yes_wipe": False,
        "scale": 1.0,
        "owner_url": TEST_OWNER_URL,
        "vault_base": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_seed_refuses_when_clock_offset_nonzero(tmp_path):
    """Safety check: seed script must refuse execution if ClockService offset != 0."""
    session: Session = TestOwnerSession()
    try:
        # Simulate non-zero clock offset in DB
        session.execute(text("UPDATE clock_state SET offset_seconds = 86400, updated_at = NOW() WHERE id = 1;"))
        session.commit()

        args = _make_args(vault_base=str(tmp_path))
        with pytest.raises(SystemExit) as exc_info:
            run_seed(args)
        assert exc_info.value.code == 1
    finally:
        session.rollback()
        session.execute(text("UPDATE clock_state SET offset_seconds = 0, updated_at = NOW() WHERE id = 1;"))
        session.commit()
        clock_service.reset(session)
        session.close()


def test_seed_refuses_in_production_or_demo_mode_false(tmp_path):
    """Safety check: seed script must refuse execution when DEMO_MODE=false or APP_ENV=production."""
    orig_demo = settings.DEMO_MODE
    orig_env = settings.APP_ENV
    try:
        settings.DEMO_MODE = False
        args = _make_args(vault_base=str(tmp_path))
        with pytest.raises(SystemExit) as exc_info:
            run_seed(args)
        assert exc_info.value.code == 1

        settings.DEMO_MODE = True
        settings.APP_ENV = "production"
        with pytest.raises(SystemExit) as exc_info2:
            run_seed(args)
        assert exc_info2.value.code == 1
    finally:
        settings.DEMO_MODE = orig_demo
        settings.APP_ENV = orig_env


def test_seed_refuses_overwrite_without_wipe_flags(tmp_path):
    """Safety check: seed script must refuse to overwrite existing synthetic data unless --reset-demo-data --yes-wipe."""
    session: Session = TestOwnerSession()
    try:
        # Create a synthetic institution (1-99*)
        session.execute(
            text("""
                INSERT INTO institutions (id, aicte_permanent_id, name, region, state, address, contact_email, contact_phone, institution_type, created_at, updated_at)
                VALUES (gen_random_uuid(), '1-9999999', 'Existing Synthetic Inst', 'Western', 'Maharashtra', 'Addr', 'e@e.edu', '123', 'Engineering & Technology', NOW(), NOW())
            """)
        )
        session.commit()

        # 1. Without any flag -> Refuse
        args1 = _make_args(vault_base=str(tmp_path))
        with pytest.raises(SystemExit) as exc1:
            run_seed(args1)
        assert exc1.value.code == 1

        # 2. With --reset-demo-data but without --yes-wipe -> Refuse
        args2 = _make_args(reset_demo_data=True, yes_wipe=False, vault_base=str(tmp_path))
        with pytest.raises(SystemExit) as exc2:
            run_seed(args2)
        assert exc2.value.code == 1
    finally:
        session.execute(text("DELETE FROM institutions WHERE aicte_permanent_id LIKE '1-99%';"))
        session.commit()
        session.close()


def test_demo_seed_dataset_full_integrity(tmp_path, monkeypatch):
    """Executes full 50-institution demo seed and validates all SPEC §14.2 & Stage 9 requirements."""
    monkeypatch.setenv("SEED_PASSWORD", "Aicte_Fdr_2026_SecureKey")
    seed_database(TEST_OWNER_URL)

    # Execute demo seed
    args = _make_args(vault_base=str(tmp_path))
    exit_code = run_seed(args)
    assert exit_code == 0

    session: Session = TestOwnerSession()
    try:
        # 1. Total Instruments and Exact Status Counts:
        # 29 ACTIVE, 8 EXPIRING, 5 MATURED, 4 DISCREPANCY_FLAGGED, 4 RENEWED, 4 RELEASED, 2 RELEASE_REQUESTED, 3 SUBMITTED, 2 UNDER_SCRUTINY = 61
        rows = session.execute(text("SELECT status, count(*) FROM instruments GROUP BY status")).fetchall()
        status_map = dict(rows)
        assert sum(status_map.values()) == 61
        assert status_map.get("ACTIVE") == 29
        assert status_map.get("EXPIRING") == 8
        assert status_map.get("MATURED") == 5
        assert status_map.get("DISCREPANCY_FLAGGED") == 4
        assert status_map.get("RENEWED") == 4
        assert status_map.get("RELEASED") == 4
        assert status_map.get("RELEASE_REQUESTED") == 2
        assert status_map.get("SUBMITTED") == 3
        assert status_map.get("UNDER_SCRUTINY") == 2

        # 2. 50 Institutions across 8 regions with at least 5 in every region
        inst_count = session.query(Institution).count()
        assert inst_count == 50

        region_rows = session.execute(text("SELECT region, count(*) FROM institutions GROUP BY region")).fetchall()
        region_map = dict(region_rows)
        assert len(region_map) == 8
        for reg in REGIONS:
            assert reg in region_map
            assert region_map[reg] >= 5, f"Region {reg} has {region_map[reg]} institutions, expected >= 5"

        # 3. Discrepancy tickets: defect instruments have expected rule codes
        tickets = session.query(DiscrepancyTicket).all()
        rule_codes = {t.rule_code for t in tickets}
        assert "DISC-01" in rule_codes
        assert "DISC-02" in rule_codes
        assert "DISC-03" in rule_codes
        assert "DISC-04" in rule_codes
        assert "DISC-05" in rule_codes

        # 4. DISC-05 duplicate ticket references incumbent, incumbent is ACTIVE
        disc05_ticket = session.query(DiscrepancyTicket).filter_by(rule_code="DISC-05").first()
        assert disc05_ticket is not None
        incumbent_id = disc05_ticket.staff_detail.get("conflicting_instrument_id")
        assert incumbent_id is not None
        incumbent_inst = session.query(Instrument).filter_by(id=incumbent_id).first()
        assert incumbent_inst is not None
        assert incumbent_inst.status == "ACTIVE"

        # 5. Maturing instruments span at least 8 of the next 12 calendar months
        today = clock_service.today()
        instruments_with_mat = session.query(Instrument).filter(
            Instrument.maturity_date >= today,
            Instrument.maturity_date <= today + timedelta(days=365)
        ).all()
        mat_months = {(inst.maturity_date.year, inst.maturity_date.month) for inst in instruments_with_mat}
        assert len(mat_months) >= 8, f"Maturing instruments span {len(mat_months)} months, expected >= 8"

        # 6. Renewal parents link to an active child, sequence is 2
        renewed_parents = session.query(Instrument).filter_by(status="RENEWED").all()
        assert len(renewed_parents) == 4
        for parent in renewed_parents:
            child = session.query(Instrument).filter_by(previous_instrument_id=parent.id).first()
            assert child is not None, f"No child found for renewed parent {parent.id}"
            assert child.status == "ACTIVE"
            assert child.renewal_sequence == 2

        # 7. Trust scores recomputed for all instruments
        all_instruments = session.query(Instrument).all()
        for inst in all_instruments:
            assert inst.trust_breakdown is not None
            assert inst.trust_computed_at is not None
            assert 0 <= inst.trust_score <= 100

        # 8. Cryptographic audit chain integrity
        chain_res = verify_chain(session)
        assert chain_res.ok is True
        assert chain_res.rows_checked > 50

    finally:
        session.close()
