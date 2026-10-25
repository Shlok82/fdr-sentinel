import os
import uuid
import hashlib
import decimal
import datetime
from pathlib import Path
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Instrument, Document, FieldProvenance, AuditLog
from app.services.audit_service import AuditService, verify_chain
from app.services.submission_service import FDRSubmissionService
from app.schemas.instrument import FDRSubmissionForm
from app.services.clock_service import clock_service


VALID_PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF"


def test_submission_happy_path(client: TestClient, make_user, sample_institution, auth_client_for, app_session: Session):
    """Happy path: submit valid FDR with PDF. Asserts Instrument, Document, FieldProvenance, and Audit log entries."""
    user = make_user("inst.happy", "INSTITUTION_ADMIN", institution_id=sample_institution.id)
    _, csrf = auth_client_for(user)

    form_payload = {
        "fdr_number": "FDR-PUNB-0012984",
        "bank_name": "Punjab National Bank",
        "bank_branch": "Connaught Place, New Delhi",
        "ifsc_code": "PUNB0012345",
        "principal_amount": "3500000.00",
        "maturity_amount": "3745000.00",
        "interest_rate_percent": "7.00",
        "issue_date": "2026-01-15",
        "maturity_date": "2027-01-15",
        "tenure_months": "12",
        "joint_account_holder": "Secretary, AICTE & Principal",
        "csrf_token": csrf,
    }

    files = {
        "scan_file": ("fixed_deposit_receipt.pdf", VALID_PDF_BYTES, "application/pdf")
    }

    res = client.post(
        "/institution/instruments/new",
        data=form_payload,
        files=files,
        follow_redirects=False,
    )

    assert res.status_code == 303
    redirect_url = res.headers["Location"]
    assert redirect_url.startswith("/institution/instruments/")
    created_id = uuid.UUID(redirect_url.split("/")[-1])

    # 1. Assert Instrument properties
    inst = app_session.query(Instrument).filter_by(id=created_id).first()
    assert inst is not None
    assert inst.institution_id == sample_institution.id
    assert inst.fdr_number == "FDR-PUNB-0012984"
    assert inst.bank_name == "Punjab National Bank"
    assert inst.principal_amount == decimal.Decimal("3500000.00")
    assert inst.status == "SUBMITTED"
    assert inst.renewal_sequence == 1
    assert inst.trust_score == 20
    assert inst.trust_score_breakdown.get("base") == 20

    # 2. Assert Document properties
    doc = app_session.query(Document).filter_by(instrument_id=created_id).first()
    assert doc is not None
    assert doc.document_type == "FDR_SCAN"
    assert doc.file_name == "fixed_deposit_receipt.pdf"
    assert doc.file_mime_type == "application/pdf"
    assert doc.sha256_hash == hashlib.sha256(VALID_PDF_BYTES).hexdigest()
    assert len(doc.encrypted_dek) > 20
    assert len(doc.encryption_iv) > 10
    assert len(doc.encryption_tag) > 10

    # Ciphertext file exists in vault
    vault_file = Path(settings.VAULT_PATH) / doc.storage_path
    assert vault_file.is_file()

    # 3. Assert FieldProvenance
    prov_rows = app_session.query(FieldProvenance).filter_by(instrument_id=created_id).all()
    field_names = {p.field_name for p in prov_rows}
    assert "fdr_number" in field_names
    assert "bank_name" in field_names
    assert "principal_amount" in field_names
    assert "maturity_amount" in field_names
    assert len(prov_rows) == 11
    for p in prov_rows:
        assert p.provenance_type == "SELF_DECLARED"
        assert p.confidence_score == decimal.Decimal("1.000")

    # 4. Assert Audit Log entries
    audit_entries = app_session.query(AuditLog).order_by(AuditLog.id.asc()).all()
    actions = [a.action for a in audit_entries]
    assert "INSTRUMENT_SUBMITTED" in actions
    assert "DOCUMENT_UPLOADED" in actions

    # Verify audit hash chain
    chain_res = verify_chain(app_session)
    assert chain_res.ok is True, f"Audit chain broken at {chain_res.first_broken_id}: {chain_res.reason}"


def test_submission_defective_data_saved(client: TestClient, make_user, sample_institution, sample_institution_b, auth_client_for, app_session: Session):
    """Defective submissions (inverted dates, expired, missing fields, duplicate FDR numbers) are SAVED as SUBMITTED."""
    user_a = make_user("inst.defect.a", "INSTITUTION_ADMIN", institution_id=sample_institution.id)
    _, csrf_a = auth_client_for(user_a)

    # 1. Inverted dates and blank fields: issue in 2027, maturity in 2025; blank IFSC, bank_branch, etc.
    res1 = client.post(
        "/institution/instruments/new",
        data={
            "fdr_number": "DEFECT-FDR-001",
            "bank_name": "State Bank of India",
            "principal_amount": "500000.00",
            "issue_date": "2027-01-01",
            "maturity_date": "2025-01-01",  # Inverted
            "csrf_token": csrf_a,
        },
        files={"scan_file": ("scan1.pdf", VALID_PDF_BYTES, "application/pdf")},
        follow_redirects=False,
    )
    assert res1.status_code == 303
    id1 = uuid.UUID(res1.headers["Location"].split("/")[-1])
    inst1 = app_session.query(Instrument).filter_by(id=id1).first()
    assert inst1 is not None
    assert inst1.status == "SUBMITTED"
    assert inst1.ifsc_code is None
    assert inst1.bank_branch is None

    # Blank fields should NOT have FieldProvenance rows
    prov1 = app_session.query(FieldProvenance).filter_by(instrument_id=id1).all()
    p_fields1 = {p.field_name for p in prov1}
    assert "ifsc_code" not in p_fields1
    assert "bank_branch" not in p_fields1
    assert "fdr_number" in p_fields1

    # 2. Duplicate FDR number in same bank for same college -> saved
    res2 = client.post(
        "/institution/instruments/new",
        data={
            "fdr_number": "DEFECT-FDR-001",  # Duplicate number
            "bank_name": "State Bank of India",
            "principal_amount": "500000.00",
            "csrf_token": csrf_a,
        },
        files={"scan_file": ("scan2.pdf", VALID_PDF_BYTES, "application/pdf")},
        follow_redirects=False,
    )
    assert res2.status_code == 303
    id2 = uuid.UUID(res2.headers["Location"].split("/")[-1])
    assert id2 != id1
    inst2 = app_session.query(Instrument).filter_by(id=id2).first()
    assert inst2.status == "SUBMITTED"

    # 3. Duplicate FDR number across two different colleges -> BOTH saved
    user_b = make_user("inst.defect.b", "INSTITUTION_ADMIN", institution_id=sample_institution_b.id)
    _, csrf_b = auth_client_for(user_b)

    res3 = client.post(
        "/institution/instruments/new",
        data={
            "fdr_number": "DEFECT-FDR-001",  # Same number submitted by College B
            "bank_name": "State Bank of India",
            "principal_amount": "500000.00",
            "csrf_token": csrf_b,
        },
        files={"scan_file": ("scan3.pdf", VALID_PDF_BYTES, "application/pdf")},
        follow_redirects=False,
    )
    assert res3.status_code == 303
    id3 = uuid.UUID(res3.headers["Location"].split("/")[-1])
    inst3 = app_session.query(Instrument).filter_by(id=id3).first()
    assert inst3.institution_id == sample_institution_b.id
    assert inst3.status == "SUBMITTED"


def test_submission_structural_garbage_rejected(client: TestClient, make_user, sample_institution, auth_client_for, app_session: Session):
    """Structural garbage (non-numeric, negative amount, malformed non-empty IFSC, bad date, fake file) returns 422."""
    user = make_user("inst.garbage", "INSTITUTION_ADMIN", institution_id=sample_institution.id)
    _, csrf = auth_client_for(user)

    initial_inst_count = app_session.query(Instrument).count()

    # 1. Non-numeric amount
    res = client.post(
        "/institution/instruments/new",
        data={"fdr_number": "G-1", "principal_amount": "one crore", "csrf_token": csrf},
        files={"scan_file": ("receipt.pdf", VALID_PDF_BYTES, "application/pdf")},
    )
    assert res.status_code == 422
    assert "Principal amount must be a valid numeric figure" in res.text

    # 2. Negative amount
    res = client.post(
        "/institution/instruments/new",
        data={"fdr_number": "G-2", "principal_amount": "-50000.00", "csrf_token": csrf},
        files={"scan_file": ("receipt.pdf", VALID_PDF_BYTES, "application/pdf")},
    )
    assert res.status_code == 422
    assert "Principal amount cannot be negative" in res.text

    # 3. Malformed non-empty IFSC
    res = client.post(
        "/institution/instruments/new",
        data={"fdr_number": "G-3", "ifsc_code": "INVALID_IFSC_CODE", "csrf_token": csrf},
        files={"scan_file": ("receipt.pdf", VALID_PDF_BYTES, "application/pdf")},
    )
    assert res.status_code == 422
    assert "Invalid IFSC format" in res.text

    # 4. Impossible date
    res = client.post(
        "/institution/instruments/new",
        data={"fdr_number": "G-4", "issue_date": "2026-02-31", "csrf_token": csrf},
        files={"scan_file": ("receipt.pdf", VALID_PDF_BYTES, "application/pdf")},
    )
    assert res.status_code == 422
    assert "valid calendar date" in res.text

    # 5. Invalid file type (text file with .pdf extension)
    res = client.post(
        "/institution/instruments/new",
        data={"fdr_number": "G-5", "csrf_token": csrf},
        files={"scan_file": ("receipt.pdf", b"This is plain text not PDF", "application/pdf")},
    )
    assert res.status_code == 422
    assert "Invalid file type" in res.text

    # Assert 0 records created in DB across all rejected attempts
    final_inst_count = app_session.query(Instrument).count()
    assert final_inst_count == initial_inst_count


def test_submission_atomic_rollback(app_session: Session, sample_institution, make_user, tmp_path):
    """Simulated failure mid-transaction leaves 0 DB rows and deletes ciphertext file from vault."""
    user = make_user("inst.atomic", "INSTITUTION_ADMIN", institution_id=sample_institution.id)

    form = FDRSubmissionForm(
        fdr_number="ROLLBACK-001",
        bank_name="Test Bank",
        principal_amount="100000.00",
    )

    # Force an exception during audit logging inside submit_instrument
    with patch("app.services.submission_service.AuditService.append", side_effect=RuntimeError("Simulated DB Crash")):
        with pytest.raises(RuntimeError, match="Simulated DB Crash"):
            FDRSubmissionService.submit_instrument(
                db=app_session,
                form=form,
                file_bytes=VALID_PDF_BYTES,
                raw_filename="fdr.pdf",
                user=user,
                actor_ip="127.0.0.1",
                vault_base=str(tmp_path),
            )

    # 1. Assert DB has zero instruments, documents, provenance rows
    assert app_session.query(Instrument).filter_by(fdr_number="ROLLBACK-001").first() is None
    assert app_session.query(Document).count() == 0
    assert app_session.query(FieldProvenance).count() == 0

    # 2. Assert ciphertext file on disk was cleanly deleted
    enc_files = list(tmp_path.rglob("*.enc"))
    assert len(enc_files) == 0, f"Orphan ciphertext file found: {enc_files}"


def test_submission_security_and_scoping(client: TestClient, make_user, sample_institution, sample_institution_b, auth_client_for, app_session: Session):
    """Verifies scoping: forged institution_id ignored, 404 on other college records, staff view/download allowed."""
    user_a = make_user("inst.scope.a", "INSTITUTION_ADMIN", institution_id=sample_institution.id)
    user_b = make_user("inst.scope.b", "INSTITUTION_ADMIN", institution_id=sample_institution_b.id)
    scrutiny_officer = make_user("staff.scrutiny", "SCRUTINY_OFFICER")
    _, csrf_a = auth_client_for(user_a)

    # 1. Attempting to forge institution_id in form is ignored
    res = client.post(
        "/institution/instruments/new",
        data={
            "fdr_number": "FORGE-INST-001",
            "institution_id": str(sample_institution_b.id),  # Attacker tries to submit as College B
            "principal_amount": "200000.00",
            "csrf_token": csrf_a,
        },
        files={"scan_file": ("scan.pdf", VALID_PDF_BYTES, "application/pdf")},
        follow_redirects=False,
    )
    assert res.status_code == 303
    inst_a_id = uuid.UUID(res.headers["Location"].split("/")[-1])
    inst_record = app_session.query(Instrument).filter_by(id=inst_a_id).first()
    assert inst_record.institution_id == sample_institution.id  # Bound to user_a's institution!

    doc_record = app_session.query(Document).filter_by(instrument_id=inst_a_id).first()
    assert doc_record is not None

    # 2. Non-INSTITUTION_ADMIN roles cannot access /institution/instruments/new
    _, csrf_staff = auth_client_for(scrutiny_officer)
    res_forbidden = client.get("/institution/instruments/new")
    assert res_forbidden.status_code == 403

    # 3. User B cannot access Institution A's instrument detail (returns 404, not 403)
    auth_client_for(user_b)
    res_b_view = client.get(f"/institution/instruments/{inst_a_id}")
    assert res_b_view.status_code == 404

    # 4. User B cannot download Institution A's document (returns 404)
    res_b_download = client.get(f"/institution/instruments/{inst_a_id}/documents/{doc_record.id}/download")
    assert res_b_download.status_code == 404

    # 5. User A can view and download their own document
    auth_client_for(user_a)
    res_a_view = client.get(f"/institution/instruments/{inst_a_id}")
    assert res_a_view.status_code == 200
    assert "FORGE-INST-001" in res_a_view.text

    res_a_download = client.get(f"/institution/instruments/{inst_a_id}/documents/{doc_record.id}/download")
    assert res_a_download.status_code == 200
    assert res_a_download.content == VALID_PDF_BYTES
    assert "attachment; filename=\"scan.pdf\"" in res_a_download.headers["Content-Disposition"]
    assert res_a_download.headers["X-Content-Type-Options"] == "nosniff"
    assert "no-store" in res_a_download.headers["Cache-Control"]

    # 6. Staff roles can view /instruments/{id} and download at /instruments/{id}/documents/{doc_id}/download
    auth_client_for(scrutiny_officer)
    res_staff_view = client.get(f"/instruments/{inst_a_id}")
    assert res_staff_view.status_code == 200
    assert "FORGE-INST-001" in res_staff_view.text

    res_staff_download = client.get(f"/instruments/{inst_a_id}/documents/{doc_record.id}/download")
    assert res_staff_download.status_code == 200
    assert res_staff_download.content == VALID_PDF_BYTES

    # 7. Check audit log has DOCUMENT_DOWNLOADED
    app_session.expire_all()
    download_audits = app_session.query(AuditLog).filter_by(action="DOCUMENT_DOWNLOADED").all()
    assert len(download_audits) >= 2  # user_a and scrutiny_officer downloads


def test_download_integrity_failure_audited(client: TestClient, make_user, sample_institution, auth_client_for, app_session: Session):
    """Tampering with document hash causes download to fail with 500 and audit DOCUMENT_INTEGRITY_FAILURE."""
    user = make_user("inst.tamper", "INSTITUTION_ADMIN", institution_id=sample_institution.id)
    _, csrf = auth_client_for(user)

    # 1. Submit valid document
    res = client.post(
        "/institution/instruments/new",
        data={"fdr_number": "TAMPER-001", "principal_amount": "500000.00", "csrf_token": csrf},
        files={"scan_file": ("scan.pdf", VALID_PDF_BYTES, "application/pdf")},
        follow_redirects=False,
    )
    inst_id = uuid.UUID(res.headers["Location"].split("/")[-1])
    doc = app_session.query(Document).filter_by(instrument_id=inst_id).first()

    # 2. Tamper with sha256_hash in DB
    doc.sha256_hash = "f" * 64
    app_session.commit()

    # 3. Attempt download
    res_dl = client.get(f"/institution/instruments/{inst_id}/documents/{doc.id}/download")
    assert res_dl.status_code == 500
    # Assert plaintext was NOT returned
    assert VALID_PDF_BYTES not in res_dl.content

    # 4. Check audit log has DOCUMENT_INTEGRITY_FAILURE
    app_session.expire_all()
    failure_audit = app_session.query(AuditLog).filter_by(action="DOCUMENT_INTEGRITY_FAILURE").first()
    assert failure_audit is not None
    assert failure_audit.actor_user_id == user.id
    assert failure_audit.entity_id == doc.id
