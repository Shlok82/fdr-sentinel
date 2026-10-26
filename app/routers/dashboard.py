"""app/routers/dashboard.py
Executive Dashboard & Maturity Wall Router (SPEC §5.1, §9.1, §9.2).
"""
import calendar
from datetime import date
from decimal import Decimal
from typing import Dict, Any, List, Optional, Tuple
from fastapi import APIRouter, Request, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import require_roles
from app.core.templates import templates
from app.models.user import User
from app.models.institution import Institution
from app.models.instrument import Instrument
from app.services.clock_service import clock_service
from app.services.instrument_fsm import compute_status_counts, InstrumentState

router = APIRouter(include_in_schema=False)

AICTE_REGIONS = [
    "Northern",
    "North-Western",
    "Western",
    "Central",
    "Eastern",
    "North-Eastern",
    "South-Central",
    "Southern",
]

IN_CUSTODY_STATES = ["ACTIVE", "EXPIRING", "MATURED", "RELEASE_REQUESTED"]
AT_RISK_STATES = ["EXPIRING", "MATURED"]


def _get_month_range(year: int, month: int) -> Tuple[date, date]:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


@router.get("/dashboard", response_class=HTMLResponse)
def executive_dashboard(
    request: Request,
    user: User = Depends(require_roles("SCRUTINY_OFFICER", "APPROVER_AUTHORITY", "AUDITOR")),
    db: Session = Depends(get_db),
):
    """Executive Dashboard & Maturity Wall.
    
    Access Control:
    - SCRUTINY_OFFICER, APPROVER_AUTHORITY, AUDITOR: 200 OK
    - INSTITUTION_ADMIN: 403 Forbidden
    - Anonymous: Redirect to login
    
    Read-only: Zero writes, zero audit log entries.
    """
    today = clock_service.today()

    # 1. Status Counts & Totals
    status_counts = compute_status_counts(db)
    total_instruments = status_counts["TOTAL"]

    # 2. Financial Aggregates
    # In Custody: ACTIVE, EXPIRING, MATURED, RELEASE_REQUESTED
    in_custody_sum = (
        db.query(func.sum(Instrument.principal_amount))
        .filter(Instrument.status.in_(IN_CUSTODY_STATES))
        .scalar()
        or Decimal("0.00")
    )
    in_custody_null_principal_count = (
        db.query(func.count(Instrument.id))
        .filter(
            Instrument.status.in_(IN_CUSTODY_STATES),
            Instrument.principal_amount.is_(None),
        )
        .scalar()
        or 0
    )

    # Deposit at Risk: EXPIRING, MATURED
    at_risk_sum = (
        db.query(func.sum(Instrument.principal_amount))
        .filter(Instrument.status.in_(AT_RISK_STATES))
        .scalar()
        or Decimal("0.00")
    )
    at_risk_null_principal_count = (
        db.query(func.count(Instrument.id))
        .filter(
            Instrument.status.in_(AT_RISK_STATES),
            Instrument.principal_amount.is_(None),
        )
        .scalar()
        or 0
    )

    # Under Review & Pending Action
    under_review_count = status_counts.get("DISCREPANCY_FLAGGED", 0)
    pending_action_count = (
        status_counts.get("SUBMITTED", 0)
        + status_counts.get("UNDER_SCRUTINY", 0)
        + status_counts.get("VERIFIED", 0)
        + status_counts.get("RELEASE_REQUESTED", 0)
    )

    # 3. Earned Trust Score Distribution
    avg_trust_raw = db.query(func.avg(Instrument.trust_score)).scalar()
    avg_trust_score = round(float(avg_trust_raw), 1) if avg_trust_raw is not None else 0.0

    low_trust_count = (
        db.query(func.count(Instrument.id))
        .filter(Instrument.trust_score < 40)
        .scalar()
        or 0
    )
    med_trust_count = (
        db.query(func.count(Instrument.id))
        .filter(Instrument.trust_score >= 40, Instrument.trust_score < 70)
        .scalar()
        or 0
    )
    high_trust_count = (
        db.query(func.count(Instrument.id))
        .filter(Instrument.trust_score >= 70)
        .scalar()
        or 0
    )

    # 4. Maturity Wall: 12 Calendar Months + Overdue + Later
    # Active Wall states: ACTIVE and EXPIRING
    wall_query = (
        db.query(
            Instrument.id,
            Instrument.status,
            Instrument.maturity_date,
            Instrument.principal_amount,
        )
        .filter(Instrument.status.in_(["ACTIVE", "EXPIRING"]))
        .all()
    )

    wall_null_date_count = 0
    wall_null_amount_count = 0

    # Build 12 monthly slots starting with current month
    cur_year = today.year
    cur_month = today.month

    monthly_slots = []
    y, m = cur_year, cur_month
    for _ in range(12):
        last_d = calendar.monthrange(y, m)[1]
        m_start = date(y, m, 1)
        m_end = date(y, m, last_d)
        m_label = m_start.strftime("%b %Y")
        monthly_slots.append({
            "key": f"{y}-{m:02d}",
            "label": m_label,
            "start": m_start,
            "end": m_end,
            "active_count": 0,
            "active_amount": Decimal("0.00"),
            "expiring_count": 0,
            "expiring_amount": Decimal("0.00"),
        })
        m += 1
        if m > 12:
            m = 1
            y += 1

    horizon_end = monthly_slots[-1]["end"]

    overdue_slot = {
        "label": "Overdue",
        "active_count": 0,
        "active_amount": Decimal("0.00"),
        "expiring_count": 0,
        "expiring_amount": Decimal("0.00"),
    }
    later_slot = {
        "label": "Later (> 12m)",
        "active_count": 0,
        "active_amount": Decimal("0.00"),
        "expiring_count": 0,
        "expiring_amount": Decimal("0.00"),
    }

    for row in wall_query:
        if row.maturity_date is None:
            wall_null_date_count += 1
            continue
        amt = row.principal_amount
        if amt is None:
            wall_null_amount_count += 1
            amt = Decimal("0.00")

        m_date = row.maturity_date
        st = row.status

        if m_date < today:
            if st == "ACTIVE":
                overdue_slot["active_count"] += 1
                overdue_slot["active_amount"] += amt
            elif st == "EXPIRING":
                overdue_slot["expiring_count"] += 1
                overdue_slot["expiring_amount"] += amt
        elif m_date > horizon_end:
            if st == "ACTIVE":
                later_slot["active_count"] += 1
                later_slot["active_amount"] += amt
            elif st == "EXPIRING":
                later_slot["expiring_count"] += 1
                later_slot["expiring_amount"] += amt
        else:
            # Place in corresponding monthly slot
            for slot in monthly_slots:
                if slot["start"] <= m_date <= slot["end"]:
                    if st == "ACTIVE":
                        slot["active_count"] += 1
                        slot["active_amount"] += amt
                    elif st == "EXPIRING":
                        slot["expiring_count"] += 1
                        slot["expiring_amount"] += amt
                    break

    tranches_data = [overdue_slot] + monthly_slots + [later_slot]
    for t in tranches_data:
        t["total_amount"] = t["active_amount"] + t["expiring_amount"]
        t["total_count"] = t["active_count"] + t["expiring_count"]

    # Chart.js JSON-serializable payloads
    maturity_chart_labels = [t["label"] for t in tranches_data]
    maturity_chart_active = [float(t["active_amount"]) for t in tranches_data]
    maturity_chart_expiring = [float(t["expiring_amount"]) for t in tranches_data]

    # 5. Regional Exposure
    regional_data = []
    for reg in AICTE_REGIONS:
        inst_count = db.query(func.count(Institution.id)).filter(Institution.region == reg).scalar() or 0
        reg_inst_stats = (
            db.query(
                func.count(Instrument.id),
                func.sum(Instrument.principal_amount),
            )
            .join(Institution, Instrument.institution_id == Institution.id)
            .filter(
                Institution.region == reg,
                Instrument.status.in_(IN_CUSTODY_STATES),
            )
            .first()
        )
        fdr_count = reg_inst_stats[0] if reg_inst_stats else 0
        fdr_principal = reg_inst_stats[1] or Decimal("0.00") if reg_inst_stats else Decimal("0.00")
        regional_data.append({
            "region": reg,
            "institutions_count": inst_count,
            "in_custody_count": fdr_count,
            "principal_amount": fdr_principal,
        })

    regional_chart_labels = [r["region"] for r in regional_data]
    regional_chart_amounts = [float(r["principal_amount"]) for r in regional_data]

    # 6. "Needs Attention" Priority Queue (Up to 10 rows)
    # Order: DISCREPANCY_FLAGGED (1), MATURED (2), EXPIRING (3), RELEASE_REQUESTED (4)
    # then trust_score ASC, maturity_date ASC
    status_priority_sql = text("""
        CASE 
            WHEN instruments.status = 'DISCREPANCY_FLAGGED' THEN 1
            WHEN instruments.status = 'MATURED' THEN 2
            WHEN instruments.status = 'EXPIRING' THEN 3
            WHEN instruments.status = 'RELEASE_REQUESTED' THEN 4
            ELSE 5
        END
    """)

    attention_rows = (
        db.query(
            Instrument,
            Institution.name.label("institution_name"),
            Institution.aicte_permanent_id.label("aicte_permanent_id"),
            Institution.region.label("region"),
        )
        .join(Institution, Instrument.institution_id == Institution.id, isouter=True)
        .filter(Instrument.status.in_(["DISCREPANCY_FLAGGED", "MATURED", "EXPIRING", "RELEASE_REQUESTED"]))
        .order_by(
            status_priority_sql,
            Instrument.trust_score.asc().nulls_last(),
            Instrument.maturity_date.asc().nulls_last(),
        )
        .limit(10)
        .all()
    )

    attention_items = []
    for inst, inst_name, aicte_id, reg in attention_rows:
        attention_items.append({
            "id": inst.id,
            "fdr_number": inst.fdr_number,
            "institution_name": inst_name or "—",
            "aicte_permanent_id": aicte_id or "—",
            "region": reg or "—",
            "status": inst.status,
            "principal_amount": inst.principal_amount,
            "maturity_date": inst.maturity_date,
            "trust_score": inst.trust_score,
        })

    unread_count = 0
    from app.routers.temporal import get_unread_notifications_count
    unread_count = get_unread_notifications_count(db, user)

    ctx = {
        "request": request,
        "user": user,
        "current_time": clock_service.display_time,
        "is_simulated": clock_service.is_simulated,
        "demo_mode": settings.DEMO_MODE,
        "simulated_date_str": clock_service.display_simulated_date,
        "csrf_token": request.cookies.get(settings.CSRF_COOKIE_NAME, ""),
        "unread_notifications_count": unread_count,
        # Executive Cards
        "in_custody_sum": in_custody_sum,
        "in_custody_null_principal_count": in_custody_null_principal_count,
        "at_risk_sum": at_risk_sum,
        "at_risk_null_principal_count": at_risk_null_principal_count,
        "total_instruments": total_instruments,
        "active_count": status_counts.get("ACTIVE", 0),
        "expiring_count": status_counts.get("EXPIRING", 0),
        "matured_count": status_counts.get("MATURED", 0),
        "renewed_count": status_counts.get("RENEWED", 0),
        "released_count": status_counts.get("RELEASED", 0),
        "under_review_count": under_review_count,
        "pending_action_count": pending_action_count,
        # Trust Metrics
        "avg_trust_score": avg_trust_score,
        "low_trust_count": low_trust_count,
        "med_trust_count": med_trust_count,
        "high_trust_count": high_trust_count,
        # Maturity Wall
        "tranches_data": tranches_data,
        "wall_null_date_count": wall_null_date_count,
        "wall_null_amount_count": wall_null_amount_count,
        "maturity_chart_labels": maturity_chart_labels,
        "maturity_chart_active": maturity_chart_active,
        "maturity_chart_expiring": maturity_chart_expiring,
        # Regional Exposure
        "regional_data": regional_data,
        "regional_chart_labels": regional_chart_labels,
        "regional_chart_amounts": regional_chart_amounts,
        # Attention Table
        "attention_items": attention_items,
    }

    return templates.TemplateResponse("dashboard.html", ctx)
