from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from app.services.clock_service import clock_service
from app.models.instrument import Instrument
from app.models.institution import Institution


def compute_quarterly_compound_maturity(
    principal: Decimal,
    rate_percent: Decimal,
    tenure_months: int
) -> Decimal:
    """Computes quarterly-compounded maturity value: A = P * (1 + r/400)^(tenure_months / 3).
    
    Formula per SPEC §7 & §15 (DISC-06):
    Compounding frequency: quarterly (4 times per year).
    Number of quarters: tenure_months / 3.
    """
    n_quarters = tenure_months // 3
    rate_factor = Decimal("1") + (rate_percent / Decimal("400"))
    result = principal * (rate_factor ** n_quarters)
    return result.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _add_years(d: date, years: int) -> date:
    """Adds integer years handling leap year boundaries safely."""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


SCENARIO_METADATA = {
    "clean": {
        "title": "Clean FDR",
        "description": "Valid statutory FDR submission matching AICTE norms with quarterly compounding interest.",
    },
    "inverted_dates": {
        "title": "Inverted Dates",
        "description": "Maturity date is earlier than issue date (violates temporal consistency check DISC-02).",
    },
    "duplicate_number": {
        "title": "Duplicate FDR Number",
        "description": "Re-uses the FDR number of an existing instrument in custody (triggers DISC-05 vigilance flag).",
    },
    "expired": {
        "title": "Expired FDR",
        "description": "Maturity date is already in the past (triggers expired instrument check DISC-03).",
    },
    "low_principal": {
        "title": "Low Principal (Underfunded)",
        "description": "Deposit amount is far below regulatory requirement (triggers underfunding check DISC-04).",
    },
    "missing_mandatory": {
        "title": "Missing Mandatory Fields",
        "description": "Omits bank name, IFSC code, and joint holder clause (triggers missing field check DISC-01).",
    },
    "maturity_mismatch": {
        "title": "Maturity Value Mismatch",
        "description": "Declared maturity amount contradicts mathematical interest compounding (triggers DISC-06).",
    },
}


def get_scenario_data(
    key: str,
    db: Optional[Session] = None,
    institution_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Single source of truth for prototype evaluation scenarios.
    
    All dates derive purely from ClockService.today(), never hardware clocks.
    All data is purely fictional (Demo National Bank, DEMO0123456).
    Joint account holder is dynamically constructed using the caller's actual institution name.
    """
    if key not in SCENARIO_METADATA:
        return None

    today = clock_service.today()
    meta = SCENARIO_METADATA[key]

    base_fictional_bank = "Demo National Bank"
    base_fictional_branch = "Model Town Branch, New Delhi"
    base_fictional_ifsc = "DEMO0123456"

    if institution_name and institution_name.strip():
        inst_title = institution_name.strip()
    elif db:
        first_inst = db.query(Institution).first()
        inst_title = first_inst.name if first_inst else "National Model Institute of Technology"
    else:
        inst_title = "National Model Institute of Technology"

    base_joint_holder = f"Regional Officer, AICTE & Head of Institution, {inst_title}"

    principal_std = Decimal("3500000.00")
    rate_std = Decimal("7.50")
    tenure_std = 36
    maturity_std = compute_quarterly_compound_maturity(principal_std, rate_std, tenure_std)

    data: Dict[str, Any] = {
        "key": key,
        "title": meta["title"],
        "label": meta["title"],
        "description": meta["description"],
        "note": None,
        "filename": f"sample_{key}.pdf",
    }

    if key == "clean":
        data.update({
            "fdr_number": "DEMO-FDR-2026-001",
            "bank_name": base_fictional_bank,
            "bank_branch": base_fictional_branch,
            "ifsc_code": base_fictional_ifsc,
            "principal_amount": str(principal_std),
            "maturity_amount": str(maturity_std),
            "interest_rate_percent": str(rate_std),
            "tenure_months": str(tenure_std),
            "issue_date": today.isoformat(),
            "maturity_date": _add_years(today, 3).isoformat(),
            "joint_account_holder": base_joint_holder,
        })

    elif key == "inverted_dates":
        data.update({
            "fdr_number": "DEMO-FDR-INVERT-002",
            "bank_name": base_fictional_bank,
            "bank_branch": base_fictional_branch,
            "ifsc_code": base_fictional_ifsc,
            "principal_amount": str(principal_std),
            "maturity_amount": str(maturity_std),
            "interest_rate_percent": str(rate_std),
            "tenure_months": str(tenure_std),
            "issue_date": today.isoformat(),
            "maturity_date": (today - timedelta(days=365)).isoformat(),
            "joint_account_holder": base_joint_holder,
        })

    elif key == "duplicate_number":
        target_fdr = None
        target_bank = base_fictional_bank
        if db is not None:
            latest = (
                db.query(Instrument)
                .filter(Instrument.fdr_number.isnot(None))
                .order_by(Instrument.created_at.desc())
                .first()
            )
            if latest and latest.fdr_number:
                target_fdr = latest.fdr_number
                if latest.bank_name:
                    target_bank = latest.bank_name

        if not target_fdr:
            target_fdr = "DEMO-FDR-2026-001"
            data["note"] = "Submit a clean FDR first"
        else:
            data["note"] = f"Targets existing deposit {target_fdr}"

        data.update({
            "fdr_number": target_fdr,
            "bank_name": target_bank,
            "bank_branch": base_fictional_branch,
            "ifsc_code": base_fictional_ifsc,
            "principal_amount": str(principal_std),
            "maturity_amount": str(maturity_std),
            "interest_rate_percent": str(rate_std),
            "tenure_months": str(tenure_std),
            "issue_date": today.isoformat(),
            "maturity_date": _add_years(today, 3).isoformat(),
            "joint_account_holder": base_joint_holder,
        })

    elif key == "expired":
        data.update({
            "fdr_number": "DEMO-FDR-EXPIRED-004",
            "bank_name": base_fictional_bank,
            "bank_branch": base_fictional_branch,
            "ifsc_code": base_fictional_ifsc,
            "principal_amount": str(principal_std),
            "maturity_amount": str(maturity_std),
            "interest_rate_percent": str(rate_std),
            "tenure_months": str(tenure_std),
            "issue_date": (today - timedelta(days=730)).isoformat(),
            "maturity_date": (today - timedelta(days=30)).isoformat(),
            "joint_account_holder": base_joint_holder,
        })

    elif key == "low_principal":
        principal_low = Decimal("50000.00")
        maturity_low = compute_quarterly_compound_maturity(principal_low, rate_std, tenure_std)
        data.update({
            "fdr_number": "DEMO-FDR-UNDERFUND-005",
            "bank_name": base_fictional_bank,
            "bank_branch": base_fictional_branch,
            "ifsc_code": base_fictional_ifsc,
            "principal_amount": str(principal_low),
            "maturity_amount": str(maturity_low),
            "interest_rate_percent": str(rate_std),
            "tenure_months": str(tenure_std),
            "issue_date": today.isoformat(),
            "maturity_date": _add_years(today, 3).isoformat(),
            "joint_account_holder": base_joint_holder,
        })

    elif key == "missing_mandatory":
        data.update({
            "fdr_number": "DEMO-FDR-MISSING-006",
            "bank_name": "",
            "bank_branch": "",
            "ifsc_code": "",
            "principal_amount": str(principal_std),
            "maturity_amount": str(maturity_std),
            "interest_rate_percent": str(rate_std),
            "tenure_months": str(tenure_std),
            "issue_date": today.isoformat(),
            "maturity_date": _add_years(today, 3).isoformat(),
            "joint_account_holder": "",
        })

    elif key == "maturity_mismatch":
        data.update({
            "fdr_number": "DEMO-FDR-MATH-007",
            "bank_name": base_fictional_bank,
            "bank_branch": base_fictional_branch,
            "ifsc_code": base_fictional_ifsc,
            "principal_amount": str(principal_std),
            "maturity_amount": "3600000.00",  # Deliberate calculation mismatch
            "interest_rate_percent": str(rate_std),
            "tenure_months": str(tenure_std),
            "issue_date": today.isoformat(),
            "maturity_date": _add_years(today, 3).isoformat(),
            "joint_account_holder": base_joint_holder,
        })

    # Populate fields dictionary for API consumers and tests
    field_keys = [
        "fdr_number", "bank_name", "bank_branch", "ifsc_code",
        "principal_amount", "maturity_amount", "interest_rate_percent",
        "tenure_months", "issue_date", "maturity_date", "joint_account_holder"
    ]
    data["fields"] = {k: data[k] for k in field_keys if k in data}
    # Convenient aliases
    if "issue_date" in data:
        data["fields"]["deposit_date"] = data["issue_date"]
    if "interest_rate_percent" in data:
        data["fields"]["interest_rate"] = data["interest_rate_percent"]
    if "joint_account_holder" in data:
        data["fields"]["joint_holder"] = data["joint_account_holder"]

    return data
