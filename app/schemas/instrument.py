import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional, Dict, Any, Tuple
from pydantic import BaseModel, Field


IFSC_REGEX = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")


class FDRSubmissionForm:
    """Parses and validates raw HTTP form fields for FDR submission (structural validation only)."""

    def __init__(
        self,
        fdr_number: Optional[str] = None,
        bank_name: Optional[str] = None,
        bank_branch: Optional[str] = None,
        ifsc_code: Optional[str] = None,
        principal_amount: Optional[str] = None,
        maturity_amount: Optional[str] = None,
        interest_rate_percent: Optional[str] = None,
        issue_date: Optional[str] = None,
        maturity_date: Optional[str] = None,
        tenure_months: Optional[str] = None,
        joint_account_holder: Optional[str] = None,
    ):
        self.raw_fields = {
            "fdr_number": fdr_number.strip() if fdr_number else "",
            "bank_name": bank_name.strip() if bank_name else "",
            "bank_branch": bank_branch.strip() if bank_branch else "",
            "ifsc_code": ifsc_code.strip() if ifsc_code else "",
            "principal_amount": principal_amount.strip() if principal_amount else "",
            "maturity_amount": maturity_amount.strip() if maturity_amount else "",
            "interest_rate_percent": interest_rate_percent.strip() if interest_rate_percent else "",
            "issue_date": issue_date.strip() if issue_date else "",
            "maturity_date": maturity_date.strip() if maturity_date else "",
            "tenure_months": tenure_months.strip() if tenure_months else "",
            "joint_account_holder": joint_account_holder.strip() if joint_account_holder else "",
        }
        self.errors: Dict[str, str] = {}
        self.cleaned_data: Dict[str, Any] = {}
        self._validate()

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def _validate(self) -> None:
        raw = self.raw_fields

        # 1. fdr_number (normalized to uppercase for query search; raw preserved for provenance)
        if raw["fdr_number"]:
            if len(raw["fdr_number"]) > 100:
                self.errors["fdr_number"] = "FDR number cannot exceed 100 characters."
            else:
                self.cleaned_data["fdr_number"] = raw["fdr_number"].upper()
        else:
            self.cleaned_data["fdr_number"] = None

        # 2. bank_name
        if raw["bank_name"]:
            if len(raw["bank_name"]) > 150:
                self.errors["bank_name"] = "Bank name cannot exceed 150 characters."
            else:
                self.cleaned_data["bank_name"] = raw["bank_name"]
        else:
            self.cleaned_data["bank_name"] = None

        # 3. bank_branch
        if raw["bank_branch"]:
            if len(raw["bank_branch"]) > 150:
                self.errors["bank_branch"] = "Branch name cannot exceed 150 characters."
            else:
                self.cleaned_data["bank_branch"] = raw["bank_branch"]
        else:
            self.cleaned_data["bank_branch"] = None

        # 4. ifsc_code (IFSC format validation only when provided)
        if raw["ifsc_code"]:
            upper_ifsc = raw["ifsc_code"].upper()
            if not IFSC_REGEX.match(upper_ifsc):
                self.errors["ifsc_code"] = "Invalid IFSC format. Expected 4 letters, '0', then 6 alphanumeric characters (e.g., SBIN0001234)."
            else:
                self.cleaned_data["ifsc_code"] = upper_ifsc
        else:
            self.cleaned_data["ifsc_code"] = None

        # 5. principal_amount (Decimal, non-negative, sane range)
        if raw["principal_amount"]:
            try:
                p_val = Decimal(raw["principal_amount"].replace(",", ""))
                if p_val < 0:
                    self.errors["principal_amount"] = "Principal amount cannot be negative."
                elif p_val > Decimal("999999999999.99"):
                    self.errors["principal_amount"] = "Principal amount exceeds maximum allowed value."
                else:
                    self.cleaned_data["principal_amount"] = p_val
            except (InvalidOperation, ValueError):
                self.errors["principal_amount"] = "Principal amount must be a valid numeric figure."
        else:
            self.cleaned_data["principal_amount"] = None

        # 6. maturity_amount (Decimal, non-negative, sane range)
        if raw["maturity_amount"]:
            try:
                m_val = Decimal(raw["maturity_amount"].replace(",", ""))
                if m_val < 0:
                    self.errors["maturity_amount"] = "Maturity amount cannot be negative."
                elif m_val > Decimal("999999999999.99"):
                    self.errors["maturity_amount"] = "Maturity amount exceeds maximum allowed value."
                else:
                    self.cleaned_data["maturity_amount"] = m_val
            except (InvalidOperation, ValueError):
                self.errors["maturity_amount"] = "Maturity amount must be a valid numeric figure."
        else:
            self.cleaned_data["maturity_amount"] = None

        # 7. interest_rate_percent (Decimal, 0.00 to 100.00)
        if raw["interest_rate_percent"]:
            try:
                rate_val = Decimal(raw["interest_rate_percent"])
                if rate_val < 0 or rate_val > Decimal("100.00"):
                    self.errors["interest_rate_percent"] = "Interest rate must be between 0.00% and 100.00%."
                else:
                    self.cleaned_data["interest_rate_percent"] = rate_val
            except (InvalidOperation, ValueError):
                self.errors["interest_rate_percent"] = "Interest rate must be a valid decimal percentage."
        else:
            self.cleaned_data["interest_rate_percent"] = None

        # 8. issue_date (parseable YYYY-MM-DD)
        if raw["issue_date"]:
            try:
                self.cleaned_data["issue_date"] = date.fromisoformat(raw["issue_date"])
            except ValueError:
                self.errors["issue_date"] = "Issue date must be a valid calendar date in YYYY-MM-DD format."
        else:
            self.cleaned_data["issue_date"] = None

        # 9. maturity_date (parseable YYYY-MM-DD)
        if raw["maturity_date"]:
            try:
                self.cleaned_data["maturity_date"] = date.fromisoformat(raw["maturity_date"])
            except ValueError:
                self.errors["maturity_date"] = "Maturity date must be a valid calendar date in YYYY-MM-DD format."
        else:
            self.cleaned_data["maturity_date"] = None

        # 10. tenure_months (integer >= 0)
        if raw["tenure_months"]:
            try:
                t_val = int(raw["tenure_months"])
                if t_val < 0:
                    self.errors["tenure_months"] = "Tenure months cannot be negative."
                else:
                    self.cleaned_data["tenure_months"] = t_val
            except ValueError:
                self.errors["tenure_months"] = "Tenure must be an integer number of months."
        else:
            self.cleaned_data["tenure_months"] = None

        # 11. joint_account_holder
        if raw["joint_account_holder"]:
            if len(raw["joint_account_holder"]) > 255:
                self.errors["joint_account_holder"] = "Joint account holder text cannot exceed 255 characters."
            else:
                self.cleaned_data["joint_account_holder"] = raw["joint_account_holder"]
        else:
            self.cleaned_data["joint_account_holder"] = None
