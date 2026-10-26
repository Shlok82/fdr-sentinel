"""Temporal automation cycle runner.

Executes lifecycle state transitions (ACTIVE -> EXPIRING -> MATURED) and proactive alerts
with PostgreSQL session-level advisory locking, isolated per-instrument transactions,
and comprehensive audit logging.
"""
import collections
import dataclasses
import logging
import uuid
from typing import Optional, Dict, Set
from datetime import date, datetime
from sqlalchemy import text, func
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.database import engine
from app.models.instrument import Instrument
from app.models.institution import Institution
from app.models.alert import Alert
from app.models.notification import Notification
from app.models.scheduler import SchedulerRun, SchedulerState
from app.models.audit import AuditLog
from app.services.clock_service import clock_service
from app.services.audit_service import AuditService
from app.services import instrument_fsm
from app.services.temporal_rules import (
    InstrumentSnapshot,
    TemporalConfig,
    evaluate_instrument,
)
from app.services.notice_templates import render_notice

logger = logging.getLogger(__name__)

TEMPORAL_CYCLE_LOCK_KEY = 99887766


def run_temporal_cycle(
    trigger: str = "SCHEDULED",
    *,
    caller_session: Optional[Session] = None,
    dry_run: bool = False,
) -> Optional[SchedulerRun]:
    """Runs a single temporal engine automation cycle.

    Args:
        trigger: One of 'SCHEDULED', 'STARTUP', 'TIME_MACHINE', 'MANUAL'.
        caller_session: Optional ORM session used to discover test database engine binding.
        dry_run: If True, calculates decisions without persisting changes.

    Returns:
        The persisted SchedulerRun record, or None if skipped due to active lock.
    """
    if trigger not in ("SCHEDULED", "STARTUP", "TIME_MACHINE", "MANUAL"):
        raise ValueError(f"Invalid trigger: {trigger}")

    # Discover engine
    lock_engine = caller_session.get_bind() if caller_session else engine
    session_factory = sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False, bind=lock_engine)

    # 1. Acquire PostgreSQL advisory lock on a dedicated raw connection
    lock_conn = lock_engine.connect()
    acquired = False

    try:
        if trigger in ("SCHEDULED", "STARTUP"):
            res = lock_conn.execute(
                text("SELECT pg_try_advisory_lock(:key)"),
                {"key": TEMPORAL_CYCLE_LOCK_KEY},
            ).scalar()
            if not res:
                logger.warning("Temporal cycle already in progress; skipping trigger '%s'", trigger)
                return None
            acquired = True
        else:
            # TIME_MACHINE or MANUAL: acquire blocking lock
            lock_conn.execute(
                text("SELECT pg_advisory_lock(:key)"),
                {"key": TEMPORAL_CYCLE_LOCK_KEY},
            )
            acquired = True

        today: date = clock_service.today()
        now_start: datetime = clock_service.now()

        config = TemporalConfig(
            expiring_window_days=settings.EXPIRING_WINDOW_DAYS,
            reminder_thresholds=settings.reminder_thresholds,
            tier2_max_days=settings.TIER2_MAX_DAYS,
            urgent_max_days=settings.URGENT_MAX_DAYS,
            overdue_escalation_days=settings.OVERDUE_ESCALATION_DAYS,
            pending_review_alert_days=settings.PENDING_REVIEW_ALERT_DAYS,
            unresolved_flag_alert_days=settings.UNRESOLVED_FLAG_ALERT_DAYS,
        )

        # 2. Batch load candidate instruments
        monitored_states = (
            "ACTIVE",
            "EXPIRING",
            "MATURED",
            "SUBMITTED",
            "UNDER_SCRUTINY",
            "DISCREPANCY_FLAGGED",
        )

        with session_factory() as read_session:
            candidate_rows = (
                read_session.query(Instrument, Institution.name.label("institution_name"))
                .join(Institution, Instrument.institution_id == Institution.id, isouter=True)
                .filter(Instrument.status.in_(monitored_states))
                .all()
            )

            # Batch query existing alert types (status IN ('OPEN', 'SUPERSEDED'))
            alert_rows = read_session.execute(
                text("SELECT instrument_id, alert_type FROM alerts WHERE status IN ('OPEN', 'SUPERSEDED')")
            ).fetchall()
            recorded_alerts_map: Dict[uuid.UUID, Set[str]] = collections.defaultdict(set)
            for inst_id, a_type in alert_rows:
                recorded_alerts_map[inst_id].add(a_type)

            # Batch query state transitions from audit log for aging alerts
            aging_inst_ids = [
                row[0].id for row in candidate_rows
                if row[0].status in ("SUBMITTED", "UNDER_SCRUTINY", "DISCREPANCY_FLAGGED")
            ]
            transition_dates: Dict[Tuple[uuid.UUID, str], date] = {}
            if aging_inst_ids:
                audit_rows = read_session.execute(
                    text("""
                        SELECT entity_id, canonical_payload->'details'->>'to_state' as to_state, MAX(event_timestamp) as latest_ts
                        FROM audit_log
                        WHERE entity_type = 'INSTRUMENT'
                          AND entity_id = ANY(:ids)
                          AND action = 'STATE_TRANSITION'
                        GROUP BY entity_id, canonical_payload->'details'->>'to_state'
                    """),
                    {"ids": aging_inst_ids},
                ).fetchall()
                for e_id, to_st, max_ts in audit_rows:
                    if max_ts:
                        local_dt = clock_service.to_local(max_ts)
                        transition_dates[(e_id, to_st)] = local_dt.date()

        transitions_count = 0
        alerts_created = 0
        alerts_superseded = 0
        instruments_examined = len(candidate_rows)
        instruments_skipped = 0
        errors_count = 0
        error_summaries = []

        # 3. Process each instrument in isolated transaction
        for inst, inst_name in candidate_rows:
            inst_id = inst.id
            recorded_alerts = recorded_alerts_map[inst_id]

            # Derive submitted_at and flagged_at
            submitted_at: Optional[date] = None
            if inst.status in ("SUBMITTED", "UNDER_SCRUTINY"):
                submitted_at = transition_dates.get((inst_id, "UNDER_SCRUTINY")) or transition_dates.get((inst_id, "SUBMITTED"))
                if not submitted_at and inst.created_at:
                    submitted_at = clock_service.to_local(inst.created_at).date()

            flagged_at: Optional[date] = None
            if inst.status == "DISCREPANCY_FLAGGED":
                flagged_at = transition_dates.get((inst_id, "DISCREPANCY_FLAGGED"))
                if not flagged_at and inst.updated_at:
                    flagged_at = clock_service.to_local(inst.updated_at).date()

            snapshot = InstrumentSnapshot(
                id=inst_id,
                fdr_number=inst.fdr_number,
                status=inst.status,
                maturity_date=inst.maturity_date,
                submitted_at=submitted_at,
                flagged_at=flagged_at,
                institution_id=inst.institution_id,
                institution_name=inst_name,
                bank_name=inst.bank_name,
                principal_amount=inst.principal_amount,
                interest_rate_percent=inst.interest_rate_percent,
            )

            decisions = evaluate_instrument(snapshot, today, config, recorded_alerts)
            if not decisions:
                instruments_skipped += 1
                continue

            if dry_run:
                for decision in decisions:
                    if decision.transition_to:
                        transitions_count += 1
                        snapshot = dataclasses.replace(snapshot, status=decision.transition_to)
                    if decision.real_alert:
                        alerts_created += 1
                    alerts_superseded += len(decision.superseded_markers)
                continue

            # Apply decisions in instrument transaction
            with session_factory() as inst_session:
                try:
                    live_inst = inst_session.query(Instrument).filter_by(id=inst_id).with_for_update().first()
                    if not live_inst:
                        instruments_skipped += 1
                        continue

                    # Refresh snapshot status from live db row
                    snapshot = dataclasses.replace(snapshot, status=live_inst.status)
                    decisions = evaluate_instrument(snapshot, today, config, recorded_alerts)
                    if not decisions:
                        instruments_skipped += 1
                        continue

                    now_utc = clock_service.now()

                    for decision in decisions:
                        if decision.transition_to:
                            instrument_fsm.transition(
                                session=inst_session,
                                instrument_id=live_inst.id,
                                to_state=decision.transition_to,
                                actor=instrument_fsm.Actor(user_id=None, role="SYSTEM"),
                                justification=decision.transition_reason,
                            )
                            transitions_count += 1
                            snapshot = dataclasses.replace(snapshot, status=decision.transition_to)

                        if decision.real_alert:
                            plan = decision.real_alert
                            alert = Alert(
                                id=uuid.uuid4(),
                                instrument_id=live_inst.id,
                                tier=None,
                                alert_type=plan.alert_type,
                                channel=None,
                                recipient_email=None,
                                sent_at=None,
                                acknowledged_at=None,
                                escalation_level_reached=1,
                                severity=plan.severity,
                                status="OPEN",
                                business_date=today,
                                details=plan.details,
                                created_at=now_utc,
                            )
                            inst_session.add(alert)
                            inst_session.flush()

                            subj, body_txt, sms_txt = render_notice(plan.alert_type, plan.details, language="en")
                            notif = Notification(
                                id=uuid.uuid4(),
                                alert_id=alert.id,
                                audience_role=plan.audience_role,
                                audience_institution_id=plan.audience_institution_id,
                                subject=subj,
                                body=body_txt,
                                sms_text=sms_txt,
                                channels=plan.channels,
                                language="en",
                                created_at=now_utc,
                                read_at=None,
                                read_by_user_id=None,
                            )
                            inst_session.add(notif)

                            AuditService.append(
                                session=inst_session,
                                entity_type="ALERT",
                                entity_id=alert.id,
                                action="ALERT_CREATED",
                                actor_user_id=None,
                                actor_role="SYSTEM",
                                details={
                                    "alert_type": alert.alert_type,
                                    "severity": alert.severity,
                                    "instrument_id": str(live_inst.id),
                                    "fdr_number": live_inst.fdr_number,
                                },
                            )
                            alerts_created += 1
                            recorded_alerts.add(alert.alert_type)

                        for marker in decision.superseded_markers:
                            m_alert = Alert(
                                id=uuid.uuid4(),
                                instrument_id=live_inst.id,
                                tier=None,
                                alert_type=marker.alert_type,
                                channel=None,
                                recipient_email=None,
                                sent_at=None,
                                acknowledged_at=None,
                                escalation_level_reached=1,
                                severity=marker.severity,
                                status="SUPERSEDED",
                                business_date=today,
                                details=marker.details,
                                created_at=now_utc,
                            )
                            inst_session.add(m_alert)
                            alerts_superseded += 1
                            recorded_alerts.add(marker.alert_type)

                    inst_session.commit()

                except Exception as exc:
                    inst_session.rollback()
                    errors_count += 1
                    err_msg = f"Instrument {inst_id}: {exc}"
                    error_summaries.append(err_msg)
                    logger.exception("Error processing instrument %s in temporal cycle: %s", inst_id, exc)

        # 4. Post-loop: record the run
        if errors_count == 0:
            run_status = "SUCCESS"
        elif (transitions_count + alerts_created) > 0:
            run_status = "PARTIAL"
        else:
            run_status = "FAILED"

        error_summary_text = "\n".join(error_summaries) if error_summaries else None
        now_finish: datetime = clock_service.now()

        run = SchedulerRun(
            id=uuid.uuid4(),
            trigger=trigger,
            business_date=today,
            started_at=now_start,
            finished_at=now_finish,
            transitions_count=transitions_count,
            alerts_created=alerts_created,
            alerts_superseded=alerts_superseded,
            instruments_examined=instruments_examined,
            instruments_skipped=instruments_skipped,
            errors_count=errors_count,
            status=run_status,
            error_summary=error_summary_text,
        )

        if not dry_run:
            with session_factory() as run_session:
                run_session.add(run)

                # Update scheduler_state
                state = run_session.query(SchedulerState).filter_by(id=1).with_for_update().first()
                if not state:
                    state = SchedulerState(id=1)
                    run_session.add(state)
                state.last_tick_at = now_finish
                if run_status in ("SUCCESS", "PARTIAL"):
                    state.last_success_at = now_finish
                    if transitions_count > 0 or alerts_created > 0:
                        state.last_effective_run_id = run.id

                # Audit compactness rule
                should_audit = (
                    transitions_count > 0
                    or alerts_created > 0
                    or errors_count > 0
                    or trigger in ("TIME_MACHINE", "MANUAL")
                )
                if should_audit:
                    AuditService.append(
                        session=run_session,
                        entity_type="SCHEDULER_RUN",
                        entity_id=run.id,
                        action="SCHEDULER_RUN_COMPLETED",
                        actor_user_id=None,
                        actor_role="SYSTEM",
                        details={
                            "trigger": trigger,
                            "business_date": str(today),
                            "status": run_status,
                            "transitions_count": transitions_count,
                            "alerts_created": alerts_created,
                            "alerts_superseded": alerts_superseded,
                            "errors_count": errors_count,
                        },
                    )

                run_session.commit()

        return run

    finally:
        if acquired:
            try:
                lock_conn.execute(
                    text("SELECT pg_advisory_unlock(:key)"),
                    {"key": TEMPORAL_CYCLE_LOCK_KEY},
                )
            except Exception as unlock_err:
                logger.error("Failed to unlock pg_advisory_unlock: %s", unlock_err)
        lock_conn.close()
