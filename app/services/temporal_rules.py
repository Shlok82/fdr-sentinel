"""Pure temporal decision function for instrument lifecycle automation and proactive alerts.

Completely pure: No database, no ClockService, no network, no I/O.
Receives today: datetime.date as an argument.
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional, List, Dict, Any, Tuple, Set
from uuid import UUID


@dataclass
class InstrumentSnapshot:
    id: UUID
    fdr_number: Optional[str]
    status: str
    maturity_date: Optional[date]
    submitted_at: Optional[date] = None  # Date entered SUBMITTED/UNDER_SCRUTINY
    flagged_at: Optional[date] = None    # Date entered DISCREPANCY_FLAGGED
    institution_id: Optional[UUID] = None
    institution_name: Optional[str] = None
    bank_name: Optional[str] = None
    principal_amount: Optional[Decimal] = None
    interest_rate_percent: Optional[Decimal] = None


@dataclass
class TemporalConfig:
    expiring_window_days: int = 90
    reminder_thresholds: Tuple[int, ...] = (90, 60, 30, 15, 7, 3, 1)
    tier2_max_days: int = 30
    urgent_max_days: int = 7
    overdue_escalation_days: int = 15
    pending_review_alert_days: int = 7
    unresolved_flag_alert_days: int = 14


@dataclass
class AlertPlan:
    alert_type: str
    severity: str  # 'INFO' | 'WARNING' | 'URGENT' | 'CRITICAL'
    status: str    # 'OPEN' | 'SUPERSEDED'
    business_date: date
    details: Dict[str, Any]
    audience_role: str
    audience_institution_id: Optional[UUID]
    channels: List[str]


@dataclass
class Decision:
    transition_to: Optional[str] = None
    transition_reason: Optional[str] = None
    real_alert: Optional[AlertPlan] = None
    superseded_markers: List[AlertPlan] = field(default_factory=list)


def map_severity(days: int, config: TemporalConfig) -> str:
    """Helper to map reminder threshold days to severity catalogue."""
    if days <= config.urgent_max_days:
        return "URGENT"
    if days <= config.tier2_max_days:
        return "WARNING"
    return "INFO"


def _evaluate_reminder_alerts(
    snapshot: InstrumentSnapshot,
    d: int,
    today: date,
    config: TemporalConfig,
    recorded_alert_types: Set[str],
) -> Optional[Decision]:
    """Catch-up reminder rule: emits single real alert for smallest arrived threshold,

    and superseded markers for any larger unrecorded arrived thresholds.
    """
    arrived = [
        T for T in config.reminder_thresholds
        if d <= T and f"MATURITY_REMINDER_{T}D" not in recorded_alert_types
    ]
    if not arrived:
        return None

    smallest_T = min(arrived)
    channels = ["IN_APP", "EMAIL"] if smallest_T > config.tier2_max_days else ["IN_APP", "EMAIL", "SMS"]
    real_alert = AlertPlan(
        alert_type=f"MATURITY_REMINDER_{smallest_T}D",
        severity=map_severity(smallest_T, config),
        status="OPEN",
        business_date=today,
        details={
            "days_to_maturity": d,
            "threshold_days": smallest_T,
            "maturity_date": snapshot.maturity_date.isoformat() if snapshot.maturity_date else None,
            "fdr_number": snapshot.fdr_number,
            "institution_name": snapshot.institution_name,
            "bank_name": snapshot.bank_name,
        },
        audience_role="INSTITUTION_ADMIN",
        audience_institution_id=snapshot.institution_id,
        channels=channels,
    )

    superseded: List[AlertPlan] = []
    for T in sorted([t for t in arrived if t > smallest_T], reverse=True):
        ch = ["IN_APP", "EMAIL"] if T > config.tier2_max_days else ["IN_APP", "EMAIL", "SMS"]
        superseded.append(
            AlertPlan(
                alert_type=f"MATURITY_REMINDER_{T}D",
                severity=map_severity(T, config),
                status="SUPERSEDED",
                business_date=today,
                details={
                    "days_to_maturity": d,
                    "threshold_days": T,
                    "maturity_date": snapshot.maturity_date.isoformat() if snapshot.maturity_date else None,
                    "fdr_number": snapshot.fdr_number,
                    "institution_name": snapshot.institution_name,
                    "bank_name": snapshot.bank_name,
                },
                audience_role="INSTITUTION_ADMIN",
                audience_institution_id=snapshot.institution_id,
                channels=ch,
            )
        )

    return Decision(real_alert=real_alert, superseded_markers=superseded)


def _evaluate_matured_alerts(
    snapshot: InstrumentSnapshot,
    d: int,
    today: date,
    config: TemporalConfig,
    recorded_alert_types: Set[str],
) -> List[Decision]:
    """Generates MATURED_NOTICE and/or OVERDUE_ESCALATION decisions."""
    out: List[Decision] = []

    # 1. MATURED_NOTICE
    if "MATURED_NOTICE" not in recorded_alert_types:
        superseded: List[AlertPlan] = []
        for T in config.reminder_thresholds:
            a_type = f"MATURITY_REMINDER_{T}D"
            if a_type not in recorded_alert_types:
                ch = ["IN_APP", "EMAIL"] if T > config.tier2_max_days else ["IN_APP", "EMAIL", "SMS"]
                superseded.append(
                    AlertPlan(
                        alert_type=a_type,
                        severity=map_severity(T, config),
                        status="SUPERSEDED",
                        business_date=today,
                        details={
                            "days_to_maturity": d,
                            "threshold_days": T,
                            "maturity_date": snapshot.maturity_date.isoformat() if snapshot.maturity_date else None,
                            "fdr_number": snapshot.fdr_number,
                            "institution_name": snapshot.institution_name,
                            "bank_name": snapshot.bank_name,
                        },
                        audience_role="INSTITUTION_ADMIN",
                        audience_institution_id=snapshot.institution_id,
                        channels=ch,
                    )
                )

        real_matured = AlertPlan(
            alert_type="MATURED_NOTICE",
            severity="CRITICAL",
            status="OPEN",
            business_date=today,
            details={
                "days_overdue": max(0, -d),
                "maturity_date": snapshot.maturity_date.isoformat() if snapshot.maturity_date else None,
                "fdr_number": snapshot.fdr_number,
                "institution_name": snapshot.institution_name,
                "bank_name": snapshot.bank_name,
            },
            audience_role="INSTITUTION_ADMIN",
            audience_institution_id=snapshot.institution_id,
            channels=["IN_APP", "EMAIL", "SMS"],
        )
        out.append(Decision(real_alert=real_matured, superseded_markers=superseded))

    # 2. OVERDUE_ESCALATION: d <= -15
    if d <= -config.overdue_escalation_days:
        if "OVERDUE_ESCALATION" not in recorded_alert_types:
            real_overdue = AlertPlan(
                alert_type="OVERDUE_ESCALATION",
                severity="CRITICAL",
                status="OPEN",
                business_date=today,
                details={
                    "days_overdue": -d,
                    "maturity_date": snapshot.maturity_date.isoformat() if snapshot.maturity_date else None,
                    "fdr_number": snapshot.fdr_number,
                    "institution_name": snapshot.institution_name,
                    "bank_name": snapshot.bank_name,
                },
                audience_role="APPROVER_AUTHORITY",
                audience_institution_id=None,
                channels=["IN_APP", "EMAIL"],
            )
            out.append(Decision(real_alert=real_overdue, superseded_markers=[]))

    return out


def evaluate_instrument(
    snapshot: InstrumentSnapshot,
    today: date,
    config: TemporalConfig,
    recorded_alert_types: Set[str],
) -> List[Decision]:
    """Pure evaluation of an instrument snapshot against temporal lifecycle and alert rules."""
    decisions: List[Decision] = []

    # -------------------------------------------------------------------------
    # (A) Lifecycle transitions and Reminders
    # -------------------------------------------------------------------------
    if snapshot.status == "ACTIVE" and snapshot.maturity_date is not None:
        d = (snapshot.maturity_date - today).days
        if d <= 0:
            # Step 1: ACTIVE -> EXPIRING
            decisions.append(
                Decision(
                    transition_to="EXPIRING",
                    transition_reason=f"Entered maturity window (days_to_maturity={d} <= {config.expiring_window_days})",
                )
            )
            # Step 2: EXPIRING -> MATURED
            decisions.append(
                Decision(
                    transition_to="MATURED",
                    transition_reason=f"Instrument reached maturity date (days_to_maturity={d} <= 0)",
                )
            )
            # Matured notices
            matured_decisions = _evaluate_matured_alerts(snapshot, d, today, config, recorded_alert_types)
            decisions.extend(matured_decisions)
        elif d <= config.expiring_window_days:
            decisions.append(
                Decision(
                    transition_to="EXPIRING",
                    transition_reason=f"Entered maturity window (days_to_maturity={d} <= {config.expiring_window_days})",
                )
            )
            rem_decision = _evaluate_reminder_alerts(snapshot, d, today, config, recorded_alert_types)
            if rem_decision:
                decisions.append(rem_decision)

    elif snapshot.status == "EXPIRING" and snapshot.maturity_date is not None:
        d = (snapshot.maturity_date - today).days
        if d <= 0:
            decisions.append(
                Decision(
                    transition_to="MATURED",
                    transition_reason=f"Instrument reached maturity date (days_to_maturity={d} <= 0)",
                )
            )
            matured_decisions = _evaluate_matured_alerts(snapshot, d, today, config, recorded_alert_types)
            decisions.extend(matured_decisions)
        else:
            rem_decision = _evaluate_reminder_alerts(snapshot, d, today, config, recorded_alert_types)
            if rem_decision:
                decisions.append(rem_decision)

    elif snapshot.status == "MATURED" and snapshot.maturity_date is not None:
        d = (snapshot.maturity_date - today).days
        matured_decisions = _evaluate_matured_alerts(snapshot, d, today, config, recorded_alert_types)
        decisions.extend(matured_decisions)

    # -------------------------------------------------------------------------
    # (D) Aging alerts (audit-derived)
    # -------------------------------------------------------------------------
    if snapshot.status in ("SUBMITTED", "UNDER_SCRUTINY") and snapshot.submitted_at is not None:
        age_days = (today - snapshot.submitted_at).days
        if age_days >= config.pending_review_alert_days and "PENDING_REVIEW_AGE_7D" not in recorded_alert_types:
            decisions.append(
                Decision(
                    real_alert=AlertPlan(
                        alert_type="PENDING_REVIEW_AGE_7D",
                        severity="WARNING",
                        status="OPEN",
                        business_date=today,
                        details={
                            "age_days": age_days,
                            "submitted_at": snapshot.submitted_at.isoformat(),
                            "fdr_number": snapshot.fdr_number,
                            "institution_name": snapshot.institution_name,
                        },
                        audience_role="SCRUTINY_OFFICER",
                        audience_institution_id=None,
                        channels=["IN_APP"],
                    ),
                    superseded_markers=[],
                )
            )

    if snapshot.status == "DISCREPANCY_FLAGGED" and snapshot.flagged_at is not None:
        flag_age_days = (today - snapshot.flagged_at).days
        if flag_age_days >= config.unresolved_flag_alert_days and "UNRESOLVED_FLAG_14D" not in recorded_alert_types:
            decisions.append(
                Decision(
                    real_alert=AlertPlan(
                        alert_type="UNRESOLVED_FLAG_14D",
                        severity="CRITICAL",
                        status="OPEN",
                        business_date=today,
                        details={
                            "flag_age_days": flag_age_days,
                            "flagged_at": snapshot.flagged_at.isoformat(),
                            "fdr_number": snapshot.fdr_number,
                            "institution_name": snapshot.institution_name,
                        },
                        audience_role="APPROVER_AUTHORITY",
                        audience_institution_id=None,
                        channels=["IN_APP", "EMAIL"],
                    ),
                    superseded_markers=[],
                )
            )

    return decisions
