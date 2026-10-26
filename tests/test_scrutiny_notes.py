import re
import uuid
import pytest
from datetime import date
from decimal import Decimal
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.instrument import Instrument
from app.models.scrutiny import ScrutinyNote
from app.models.audit import AuditLog
from app.services.clock_service import clock_service
from app.services.instrument_fsm import InstrumentState


@pytest.fixture
def active_instrument(app_session: Session, sample_institution) -> Instrument:
    """Fixture providing an ACTIVE instrument for scrutiny testing."""
    now_ts = clock_service.now()
    inst = Instrument(
        id=uuid.uuid4(),
        institution_id=sample_institution.id,
        fdr_number="FDR-NOTE-TEST-001",
        bank_name="State Bank of India",
        bank_branch="Nariman Point",
        ifsc_code="SBIN0001234",
        principal_amount=Decimal("3500000.00"),
        maturity_amount=Decimal("3800000.00"),
        interest_rate_percent=Decimal("7.50"),
        issue_date=date(2026, 1, 1),
        maturity_date=date(2027, 1, 1),
        tenure_months=12,
        joint_account_holder="AICTE and Principal",
        status=InstrumentState.ACTIVE.value,
        trust_score=20,
        created_at=now_ts,
        updated_at=now_ts,
    )
    app_session.add(inst)
    app_session.commit()
    app_session.refresh(inst)
    return inst


def test_action_forms_are_unnested_with_unique_ids(auth_client_for, make_user, active_instrument):
    """Asserts all action forms in staff_detail.html are un-nested, with CSRF tokens and unique IDs."""
    officer = make_user("scrutiny.form.checker", "SCRUTINY_OFFICER")
    client, _ = auth_client_for(officer)

    res = client.get(f"/instruments/{active_instrument.id}")
    assert res.status_code == status.HTTP_200_OK

    html = res.text

    # Extract all <form ...> tags and verify unique ids
    form_tags = re.findall(r'<form\b([^>]*)>', html, re.IGNORECASE)
    assert len(form_tags) >= 2, "Expected multiple action forms"

    # Verify no nested forms: between every <form> and the next <form>, there must be a </form>
    # Form opening and closing indices in the HTML
    form_tokens = [(m.start(), m.group(0)) for m in re.finditer(r'</?form\b[^>]*>', html, re.IGNORECASE)]
    depth = 0
    for idx, token in form_tokens:
        if token.startswith("</"):
            depth -= 1
        else:
            depth += 1
            assert depth == 1, f"Nested <form> detected at token: {token}"
        assert depth >= 0, "Unmatched </form> tag detected"
    assert depth == 0, "Unclosed <form> tag detected"

    # Extract all form ids
    form_ids = re.findall(r'<form[^>]*\bid="([^"]+)"', html, re.IGNORECASE)
    assert len(form_ids) == len(set(form_ids)), f"Duplicate form ids found: {form_ids}"

    # Verify add-note form elements specifically
    assert 'id="form-action-add-note"' in html
    assert 'name="note_text"' in html
    assert 'id="note_text"' in html
    assert 'name="action_recommended"' in html
    assert 'id="action_recommended"' in html


def test_add_scrutiny_note_standard_post(auth_client_for, make_user, active_instrument, app_session: Session):
    """Submitting valid scrutiny note via standard POST redirects back and persists row + audit entry."""
    officer = make_user("scrutiny.note.officer", "SCRUTINY_OFFICER")
    client, csrf = auth_client_for(officer)

    res = client.post(
        f"/instruments/{active_instrument.id}/actions/add-note",
        data={
            "csrf_token": csrf,
            "note_text": "Verified deposit receipt details with issuing branch manager over phone.",
            "action_recommended": "CLEAR",
        },
        follow_redirects=True,
    )
    assert res.status_code == status.HTTP_200_OK
    assert "Scrutiny note recorded." in res.text
    assert "Verified deposit receipt details with issuing branch manager" in res.text

    # Verify ScrutinyNote row in database
    note_row = (
        app_session.query(ScrutinyNote)
        .filter(ScrutinyNote.instrument_id == active_instrument.id)
        .first()
    )
    assert note_row is not None
    assert note_row.officer_id == officer.id
    assert note_row.action_recommended == "CLEAR"
    assert "Verified deposit receipt details" in note_row.note_text

    # Verify AuditLog row
    audit_row = (
        app_session.query(AuditLog)
        .filter(
            AuditLog.entity_id == active_instrument.id,
            AuditLog.action == "SCRUTINY_NOTE_ADDED",
        )
        .first()
    )
    assert audit_row is not None
    assert audit_row.actor_user_id == officer.id
    assert audit_row.canonical_payload["actor_role"] == "SCRUTINY_OFFICER"
    assert audit_row.canonical_payload["details"]["action_recommended"] == "CLEAR"
    assert "note_length" in audit_row.canonical_payload["details"]


def test_add_scrutiny_note_htmx_post(auth_client_for, make_user, active_instrument, app_session: Session):
    """Submitting valid scrutiny note via HTMX returns updated content with new note visible."""
    officer = make_user("scrutiny.htmx.officer", "SCRUTINY_OFFICER")
    client, csrf = auth_client_for(officer)

    res = client.post(
        f"/instruments/{active_instrument.id}/actions/add-note",
        data={
            "csrf_token": csrf,
            "note_text": "Bank confirmation call pending verification of lien marking clause.",
            "action_recommended": "FLAG_DISCREPANCY",
        },
        headers={"HX-Request": "true"},
    )
    assert res.status_code == status.HTTP_200_OK
    assert "Scrutiny note recorded." in res.text
    assert "Bank confirmation call pending verification" in res.text
    assert "FLAG_DISCREPANCY" in res.text


def test_add_scrutiny_note_reject_recommendation_does_not_change_state(
    auth_client_for, make_user, active_instrument, app_session: Session
):
    """A REJECT recommendation on a note changes no instrument state."""
    officer = make_user("scrutiny.recom.reject", "SCRUTINY_OFFICER")
    client, csrf = auth_client_for(officer)

    res = client.post(
        f"/instruments/{active_instrument.id}/actions/add-note",
        data={
            "csrf_token": csrf,
            "note_text": "Advising competent authority to reject due to suspicious certificate stamps.",
            "action_recommended": "REJECT",
        },
        follow_redirects=True,
    )
    assert res.status_code == status.HTTP_200_OK

    app_session.refresh(active_instrument)
    # State remains ACTIVE; a note recommendation does not alter state
    assert active_instrument.status == InstrumentState.ACTIVE.value


@pytest.mark.parametrize("invalid_note, err_part", [
    ("", "blank or whitespace-only"),
    ("   ", "blank or whitespace-only"),
    ("Short", "at least 10 characters"),
    ("X" * 2001, "cannot exceed 2000 characters"),
])
def test_add_scrutiny_note_validation_failures_friendly_error(
    auth_client_for, make_user, active_instrument, invalid_note, err_part
):
    """Validation errors return status 422 with friendly message, NEVER raw FastAPI JSON."""
    officer = make_user(f"val.officer.{uuid.uuid4().hex[:6]}", "SCRUTINY_OFFICER")
    client, csrf = auth_client_for(officer)

    res = client.post(
        f"/instruments/{active_instrument.id}/actions/add-note",
        data={
            "csrf_token": csrf,
            "note_text": invalid_note,
            "action_recommended": "CLEAR",
        },
    )
    assert res.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    # Must never return raw FastAPI 422 JSON
    assert '{"detail":[{"type":' not in res.text
    assert err_part in res.text


def test_add_scrutiny_note_invalid_recommendation(auth_client_for, make_user, active_instrument):
    """Submitting invalid recommendation returns 422 with friendly message."""
    officer = make_user("val.rec.officer", "SCRUTINY_OFFICER")
    client, csrf = auth_client_for(officer)

    res = client.post(
        f"/instruments/{active_instrument.id}/actions/add-note",
        data={
            "csrf_token": csrf,
            "note_text": "This is a legitimate scrutiny note text of adequate length.",
            "action_recommended": "INVALID_STATE",
        },
    )
    assert res.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "Invalid recommendation" in res.text


def test_add_scrutiny_note_script_tag_escaped(auth_client_for, make_user, active_instrument):
    """Scrutiny note containing <script> tags renders escaped as text, not executable HTML."""
    officer = make_user("xss.officer", "SCRUTINY_OFFICER")
    client, csrf = auth_client_for(officer)

    xss_payload = "<script>alert('pwned')</script> observed on receipt scan"
    res = client.post(
        f"/instruments/{active_instrument.id}/actions/add-note",
        data={
            "csrf_token": csrf,
            "note_text": xss_payload,
            "action_recommended": "CLEAR",
        },
        follow_redirects=True,
    )
    assert res.status_code == status.HTTP_200_OK
    # Must be escaped: either &lt;script&gt; or not an unescaped raw tag
    assert "<script>alert('pwned')</script>" not in res.text
    assert "&lt;script&gt;alert(&#39;pwned&#39;)&lt;/script&gt;" in res.text or "&lt;script&gt;" in res.text


def test_add_scrutiny_note_role_forbidden(auth_client_for, make_user, active_instrument):
    """Only SCRUTINY_OFFICER can add notes; other roles get 403 Forbidden."""
    approver = make_user("forbidden.approver", "APPROVER_AUTHORITY")
    client, csrf = auth_client_for(approver)

    res = client.post(
        f"/instruments/{active_instrument.id}/actions/add-note",
        data={
            "csrf_token": csrf,
            "note_text": "Approver attempting to attach scrutiny note.",
            "action_recommended": "CLEAR",
        },
    )
    assert res.status_code == status.HTTP_403_FORBIDDEN


def test_add_scrutiny_note_terminal_state_forbidden(app_session: Session, auth_client_for, make_user, active_instrument):
    """Cannot add scrutiny note to terminal state (e.g. REJECTED)."""
    active_instrument.status = InstrumentState.REJECTED.value
    app_session.commit()

    officer = make_user("terminal.officer", "SCRUTINY_OFFICER")
    client, csrf = auth_client_for(officer)

    res = client.post(
        f"/instruments/{active_instrument.id}/actions/add-note",
        data={
            "csrf_token": csrf,
            "note_text": "Attempting to add note to terminated instrument.",
            "action_recommended": "CLEAR",
        },
    )
    assert res.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "terminated" in res.text.lower()
