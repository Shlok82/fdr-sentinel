"""Unit tests for pure temporal rules decision engine (app/services/temporal_rules.py).

Runs 100% in memory with zero database or network dependencies.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services.temporal_rules import (
    InstrumentSnapshot,
    TemporalConfig,
    AlertPlan,
    Decision,
    map_severity,
    evaluate_instrument,
)
from app.services.notice_templates import render_notice


@pytest.fixture
def base_config():
    return TemporalConfig(
        expiring_window_days=90,
        reminder_thresholds=(90, 60, 30, 15, 7, 3, 1),
        tier2_max_days=30,
        urgent_max_days=7,
        overdue_escalation_days=15,
        pending_review_alert_days=7,
        unresolved_flag_alert_days=14,
    )


@pytest.fixture
def sample_snapshot():
    return InstrumentSnapshot(
        id=uuid.uuid4(),
        fdr_number="FDR-TEST-001",
        status="ACTIVE",
        maturity_date=date(2027, 3, 15),
        submitted_at=None,
        flagged_at=None,
        institution_id=uuid.uuid4(),
        institution_name="Government Engineering College",
        bank_name="State Bank of India",
        principal_amount=Decimal("500000.00"),
        interest_rate_percent=Decimal("7.25"),
    )


def test_full_reminder_threshold_matrix(base_config, sample_snapshot):
    """Tests that each reminder threshold fires with correct alert_type, severity, and channels at d = T."""
    today = date(2027, 1, 1)

    expected_catalogue = [
        (90, "MATURITY_REMINDER_90D", "INFO", ["IN_APP", "EMAIL"]),
        (60, "MATURITY_REMINDER_60D", "INFO", ["IN_APP", "EMAIL"]),
        (30, "MATURITY_REMINDER_30D", "WARNING", ["IN_APP", "EMAIL", "SMS"]),
        (15, "MATURITY_REMINDER_15D", "WARNING", ["IN_APP", "EMAIL", "SMS"]),
        (7, "MATURITY_REMINDER_7D", "URGENT", ["IN_APP", "EMAIL", "SMS"]),
        (3, "MATURITY_REMINDER_3D", "URGENT", ["IN_APP", "EMAIL", "SMS"]),
        (1, "MATURITY_REMINDER_1D", "URGENT", ["IN_APP", "EMAIL", "SMS"]),
    ]

    for threshold, exp_type, exp_sev, exp_channels in expected_catalogue:
        sample_snapshot.status = "EXPIRING"
        sample_snapshot.maturity_date = today + timedelta(days=threshold)
        
        decisions = evaluate_instrument(sample_snapshot, today, base_config, set())
        alert_decisions = [d for d in decisions if d.real_alert is not None]
        assert len(alert_decisions) == 1, f"Expected 1 real alert for threshold {threshold}"
        
        plan = alert_decisions[0].real_alert
        assert plan.alert_type == exp_type
        assert plan.severity == exp_sev
        assert plan.channels == exp_channels
        assert plan.status == "OPEN"
        assert plan.audience_role == "INSTITUTION_ADMIN"
        assert plan.audience_institution_id == sample_snapshot.institution_id


def test_catch_up_reminder_rule_jump_to_10_days(base_config, sample_snapshot):
    """Jump from 95 days to 10 days with 0 prior alerts -> 1 real alert (15D, WARNING) and 3 SUPERSEDED markers (90D, 60D, 30D)."""
    today = date(2027, 1, 1)
    sample_snapshot.status = "ACTIVE"
    sample_snapshot.maturity_date = today + timedelta(days=10)

    decisions = evaluate_instrument(sample_snapshot, today, base_config, set())

    # Should have transition ACTIVE -> EXPIRING
    trans_decisions = [d for d in decisions if d.transition_to == "EXPIRING"]
    assert len(trans_decisions) == 1
    assert "days_to_maturity=10" in trans_decisions[0].transition_reason

    # Catch-up alert
    alert_dec = [d for d in decisions if d.real_alert is not None][0]
    assert alert_dec.real_alert.alert_type == "MATURITY_REMINDER_15D"
    assert alert_dec.real_alert.severity == "WARNING"
    assert alert_dec.real_alert.status == "OPEN"

    # Superseded markers: 90D, 60D, 30D
    marker_types = [m.alert_type for m in alert_dec.superseded_markers]
    assert marker_types == ["MATURITY_REMINDER_90D", "MATURITY_REMINDER_60D", "MATURITY_REMINDER_30D"]
    for m in alert_dec.superseded_markers:
        assert m.status == "SUPERSEDED"


def test_catch_up_reminder_rule_jump_to_2_days_with_90d_already_recorded(base_config, sample_snapshot):
    """Jump to 2 days left with 90D recorded -> 1 real alert (3D, URGENT) and 4 superseded markers (60D, 30D, 15D, 7D)."""
    today = date(2027, 1, 1)
    sample_snapshot.status = "EXPIRING"
    sample_snapshot.maturity_date = today + timedelta(days=2)
    recorded = {"MATURITY_REMINDER_90D"}

    decisions = evaluate_instrument(sample_snapshot, today, base_config, recorded)
    alert_dec = [d for d in decisions if d.real_alert is not None][0]

    assert alert_dec.real_alert.alert_type == "MATURITY_REMINDER_3D"
    assert alert_dec.real_alert.severity == "URGENT"

    marker_types = [m.alert_type for m in alert_dec.superseded_markers]
    assert marker_types == ["MATURITY_REMINDER_60D", "MATURITY_REMINDER_30D", "MATURITY_REMINDER_15D", "MATURITY_REMINDER_7D"]


def test_catch_up_jump_to_matured_with_zero_prior_alerts(base_config, sample_snapshot):
    """Jump directly to d <= 0 with 0 prior alerts -> 1 real MATURED_NOTICE and 7 superseded markers."""
    today = date(2027, 1, 1)
    sample_snapshot.status = "EXPIRING"
    sample_snapshot.maturity_date = today  # d = 0

    decisions = evaluate_instrument(sample_snapshot, today, base_config, set())

    # Transitions to MATURED
    trans = [d for d in decisions if d.transition_to == "MATURED"]
    assert len(trans) == 1

    alert_dec = [d for d in decisions if d.real_alert is not None and d.real_alert.alert_type == "MATURED_NOTICE"][0]
    assert alert_dec.real_alert.severity == "CRITICAL"

    # All 7 unrecorded reminder thresholds become superseded markers
    marker_types = [m.alert_type for m in alert_dec.superseded_markers]
    assert marker_types == [
        "MATURITY_REMINDER_90D",
        "MATURITY_REMINDER_60D",
        "MATURITY_REMINDER_30D",
        "MATURITY_REMINDER_15D",
        "MATURITY_REMINDER_7D",
        "MATURITY_REMINDER_3D",
        "MATURITY_REMINDER_1D",
    ]


def test_reminder_no_op_when_all_arrived_thresholds_recorded(base_config, sample_snapshot):
    """No reminder emitted when all arrived thresholds are already recorded."""
    today = date(2027, 1, 1)
    sample_snapshot.status = "EXPIRING"
    sample_snapshot.maturity_date = today + timedelta(days=20)
    # Arrived thresholds for d=20 are 90, 60, 30
    recorded = {"MATURITY_REMINDER_90D", "MATURITY_REMINDER_60D", "MATURITY_REMINDER_30D"}

    decisions = evaluate_instrument(sample_snapshot, today, base_config, recorded)
    alert_decisions = [d for d in decisions if d.real_alert is not None]
    assert len(alert_decisions) == 0


def test_lifecycle_transitions(base_config, sample_snapshot):
    """Tests boundary lifecycle transitions: d=90 triggers EXPIRING, d=91 does not; d=0 triggers MATURED, d=1 does not."""
    today = date(2027, 1, 1)

    # 1. ACTIVE with d = 90 -> transitions to EXPIRING
    sample_snapshot.status = "ACTIVE"
    sample_snapshot.maturity_date = today + timedelta(days=90)
    decisions = evaluate_instrument(sample_snapshot, today, base_config, set())
    assert any(d.transition_to == "EXPIRING" for d in decisions)

    # 2. ACTIVE with d = 91 -> no transition
    sample_snapshot.maturity_date = today + timedelta(days=91)
    decisions = evaluate_instrument(sample_snapshot, today, base_config, set())
    assert not any(d.transition_to for d in decisions)

    # 3. EXPIRING with d = 0 -> transitions to MATURED
    sample_snapshot.status = "EXPIRING"
    sample_snapshot.maturity_date = today
    decisions = evaluate_instrument(sample_snapshot, today, base_config, set())
    assert any(d.transition_to == "MATURED" for d in decisions)

    # 4. EXPIRING with d = 1 -> no transition
    sample_snapshot.maturity_date = today + timedelta(days=1)
    decisions = evaluate_instrument(sample_snapshot, today, base_config, set())
    assert not any(d.transition_to for d in decisions)

    # 5. ACTIVE with d = -5 -> emits TWO sequential transitions: ACTIVE -> EXPIRING, then EXPIRING -> MATURED
    sample_snapshot.status = "ACTIVE"
    sample_snapshot.maturity_date = today - timedelta(days=5)
    decisions = evaluate_instrument(sample_snapshot, today, base_config, set())
    transitions = [d.transition_to for d in decisions if d.transition_to]
    assert transitions == ["EXPIRING", "MATURED"], "Must evaluate two sequential steps, never jump directly"


def test_matured_and_overdue_escalation(base_config, sample_snapshot):
    """Tests overdue escalation boundary: d = -15 emits OVERDUE_ESCALATION, d = -14 does not."""
    today = date(2027, 1, 1)
    sample_snapshot.status = "MATURED"

    # d = -14: no escalation
    sample_snapshot.maturity_date = today - timedelta(days=14)
    decisions = evaluate_instrument(sample_snapshot, today, base_config, {"MATURED_NOTICE"})
    assert not any(d.real_alert and d.real_alert.alert_type == "OVERDUE_ESCALATION" for d in decisions)

    # d = -15: emits OVERDUE_ESCALATION to APPROVER_AUTHORITY
    sample_snapshot.maturity_date = today - timedelta(days=15)
    decisions = evaluate_instrument(sample_snapshot, today, base_config, {"MATURED_NOTICE"})
    overdue_dec = [d for d in decisions if d.real_alert and d.real_alert.alert_type == "OVERDUE_ESCALATION"]
    assert len(overdue_dec) == 1
    assert overdue_dec[0].real_alert.severity == "CRITICAL"
    assert overdue_dec[0].real_alert.audience_role == "APPROVER_AUTHORITY"
    assert overdue_dec[0].real_alert.audience_institution_id is None
    assert overdue_dec[0].real_alert.channels == ["IN_APP", "EMAIL"]


def test_aging_alerts(base_config, sample_snapshot):
    """Tests audit-derived aging alerts for SUBMITTED and DISCREPANCY_FLAGGED."""
    today = date(2027, 1, 15)

    # 1. SUBMITTED aged 7 days
    sample_snapshot.status = "SUBMITTED"
    sample_snapshot.submitted_at = today - timedelta(days=7)
    decisions = evaluate_instrument(sample_snapshot, today, base_config, set())
    p_dec = [d for d in decisions if d.real_alert and d.real_alert.alert_type == "PENDING_REVIEW_AGE_7D"]
    assert len(p_dec) == 1
    assert p_dec[0].real_alert.severity == "WARNING"
    assert p_dec[0].real_alert.audience_role == "SCRUTINY_OFFICER"

    # 2. DISCREPANCY_FLAGGED aged 14 days
    sample_snapshot.status = "DISCREPANCY_FLAGGED"
    sample_snapshot.flagged_at = today - timedelta(days=14)
    decisions = evaluate_instrument(sample_snapshot, today, base_config, set())
    f_dec = [d for d in decisions if d.real_alert and d.real_alert.alert_type == "UNRESOLVED_FLAG_14D"]
    assert len(f_dec) == 1
    assert f_dec[0].real_alert.severity == "CRITICAL"
    assert f_dec[0].real_alert.audience_role == "APPROVER_AUTHORITY"


def test_notice_template_rendering_and_sms_length():
    """Verifies render_notice produces valid plain text and SMS <= 320 characters for all alert types."""
    details = {
        "fdr_number": "FDR-2027-998811",
        "institution_name": "Indian Institute of Science & Technology",
        "bank_name": "Punjab National Bank",
        "maturity_date": "2027-04-15",
        "days_to_maturity": 15,
        "days_overdue": 16,
        "age_days": 8,
        "flag_age_days": 15,
    }

    test_types = [
        "MATURITY_REMINDER_90D",
        "MATURITY_REMINDER_15D",
        "MATURITY_REMINDER_1D",
        "MATURED_NOTICE",
        "OVERDUE_ESCALATION",
        "PENDING_REVIEW_AGE_7D",
        "UNRESOLVED_FLAG_14D",
    ]

    for a_type in test_types:
        subject, body, sms = render_notice(a_type, details, language="en")
        assert len(subject) > 0
        assert len(body) > 0
        assert sms.startswith("[FDR-SENTINEL]")
        assert len(sms) <= 320, f"SMS length {len(sms)} exceeds 320 for {a_type}"


def test_config_strict_validation():
    """Verifies startup validation raises errors for invalid threshold configuration."""
    # 1. Non-descending thresholds
    with pytest.raises(ValidationError):
        Settings(
            SECRET_KEY="x" * 32,
            DOCUMENT_MASTER_KEY="k8T0a7Z3Yx9L1q4W7e2R5t8Y1u4I7o0P3s6D9f2G5h8=",
            REMINDER_THRESHOLDS_DAYS="90,30,60",  # Not strictly descending
        )

    # 2. Non-positive threshold
    with pytest.raises(ValidationError):
        Settings(
            SECRET_KEY="x" * 32,
            DOCUMENT_MASTER_KEY="k8T0a7Z3Yx9L1q4W7e2R5t8Y1u4I7o0P3s6D9f2G5h8=",
            REMINDER_THRESHOLDS_DAYS="90,60,0",
        )

    # 3. TIER2_MAX_DAYS >= EXPIRING_WINDOW_DAYS
    with pytest.raises(ValidationError):
        Settings(
            SECRET_KEY="x" * 32,
            DOCUMENT_MASTER_KEY="k8T0a7Z3Yx9L1q4W7e2R5t8Y1u4I7o0P3s6D9f2G5h8=",
            EXPIRING_WINDOW_DAYS=60,
            TIER2_MAX_DAYS=60,
        )

    # 4. URGENT_MAX_DAYS >= TIER2_MAX_DAYS
    with pytest.raises(ValidationError):
        Settings(
            SECRET_KEY="x" * 32,
            DOCUMENT_MASTER_KEY="k8T0a7Z3Yx9L1q4W7e2R5t8Y1u4I7o0P3s6D9f2G5h8=",
            TIER2_MAX_DAYS=10,
            URGENT_MAX_DAYS=10,
        )
