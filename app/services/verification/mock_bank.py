import time
from enum import Enum
from decimal import Decimal
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.instrument import Instrument
from app.models.document import Document
from app.services.clock_service import clock_service
from app.services.verification.base import BaseVerificationProvider, VerificationResult


class MockFailureMode(str, Enum):
    """Failure simulation modes for the Mock Bank CBS Provider."""
    NONE = "NONE"
    PRINCIPAL_MISMATCH = "PRINCIPAL_MISMATCH"
    MATURITY_MISMATCH = "MATURITY_MISMATCH"
    NOT_FOUND = "NOT_FOUND"
    JOINT_HOLDER_MISSING = "JOINT_HOLDER_MISSING"


class MockBankProvider(BaseVerificationProvider):
    """Deterministic Mock Core Banking System (CBS) Verification Provider.
    
    Exclusively available in demo and non-production environments to simulate
    direct CBS electronic deposit verification.
    """

    VERIFIABLE_FIELDS = [
        "fdr_number",
        "bank_name",
        "bank_branch",
        "ifsc_code",
        "principal_amount",
        "maturity_amount",
        "interest_rate_percent",
        "issue_date",
        "maturity_date",
        "tenure_months",
        "joint_account_holder",
    ]

    def __init__(self, failure_mode: MockFailureMode = MockFailureMode.NONE):
        self.failure_mode = failure_mode

    def get_provider_name(self) -> str:
        return "Mock Bank CBS Gateway (Demo / Simulated)"

    def is_live_authorized(self) -> bool:
        return False

    def verify_instrument(
        self,
        db: Session,
        instrument: Instrument,
        document: Optional[Document] = None,
        **kwargs: Any,
    ) -> VerificationResult:
        # Security Gate: Mock provider is forbidden in production or when demo mode is off
        if not settings.DEMO_MODE or settings.APP_ENV == "production":
            raise RuntimeError(
                "MockBankProvider is strictly disabled in production or when DEMO_MODE is false."
            )

        # Apply configurable latency delay (hard capped at 3000ms)
        latency_ms = min(max(0, getattr(settings, "MOCK_BANK_LATENCY_MS", 800)), 3000)
        if latency_ms > 0:
            time.sleep(latency_ms / 1000.0)

        # Determine populated fields on instrument
        populated_fields = [
            f for f in self.VERIFIABLE_FIELDS
            if getattr(instrument, f, None) is not None
        ]

        if self.failure_mode == MockFailureMode.NONE:
            return VerificationResult(
                is_verified=True,
                provider_type="MOCK_BANK",
                confirmed_fields=populated_fields,
                mismatch_flags=[],
                raw_response={
                    "gateway": "MOCK_CBS_V1",
                    "status": "CONFIRMED",
                    "fdr_number": instrument.fdr_number,
                    "bank_name": instrument.bank_name,
                    "cbs_reference": f"CBS-CONF-{instrument.fdr_number or 'MOCK'}-OK",
                    "simulated": True,
                    "simulated_at": clock_service.get_current_time().isoformat(),
                },
                simulated=True,
            )

        elif self.failure_mode == MockFailureMode.PRINCIPAL_MISMATCH:
            decl_val = str(instrument.principal_amount) if instrument.principal_amount is not None else "0.00"
            cbs_val = str((instrument.principal_amount or Decimal("100000.00")) + Decimal("500000.00"))
            return VerificationResult(
                is_verified=False,
                provider_type="MOCK_BANK",
                confirmed_fields=[],
                mismatch_flags=[{
                    "code": self.failure_mode.value,
                    "field": "principal_amount",
                    "declared_value": decl_val,
                    "cbs_value": cbs_val,
                    "message": f"Principal amount mismatch: declared ₹{decl_val}, bank CBS records show ₹{cbs_val}.",
                }],
                raw_response={
                    "gateway": "MOCK_CBS_V1",
                    "status": "MISMATCH",
                    "error_code": "PRINCIPAL_MISMATCH",
                    "declared_principal": decl_val,
                    "cbs_principal": cbs_val,
                    "simulated": True,
                },
                simulated=True,
            )

        elif self.failure_mode == MockFailureMode.MATURITY_MISMATCH:
            decl_mat = str(instrument.maturity_date) if instrument.maturity_date is not None else "None"
            sim_cbs_date = str(clock_service.today())
            return VerificationResult(
                is_verified=False,
                provider_type="MOCK_BANK",
                confirmed_fields=[],
                mismatch_flags=[{
                    "code": self.failure_mode.value,
                    "field": "maturity_date",
                    "declared_value": decl_mat,
                    "cbs_value": sim_cbs_date,
                    "message": f"Maturity date mismatch: declared {decl_mat}, bank CBS ledger records {sim_cbs_date}.",
                }],
                raw_response={
                    "gateway": "MOCK_CBS_V1",
                    "status": "MISMATCH",
                    "error_code": "MATURITY_MISMATCH",
                    "declared_maturity": decl_mat,
                    "cbs_maturity": sim_cbs_date,
                    "simulated": True,
                },
                simulated=True,
            )

        elif self.failure_mode == MockFailureMode.NOT_FOUND:
            return VerificationResult(
                is_verified=False,
                provider_type="MOCK_BANK",
                confirmed_fields=[],
                mismatch_flags=[{
                    "code": self.failure_mode.value,
                    "field": "fdr_number",
                    "declared_value": str(instrument.fdr_number or "None"),
                    "message": f"Deposit certificate {instrument.fdr_number} not found in issuing bank records.",
                }],
                raw_response={
                    "gateway": "MOCK_CBS_V1",
                    "status": "NOT_FOUND",
                    "error_code": "RECORD_NOT_FOUND",
                    "fdr_number": instrument.fdr_number,
                    "simulated": True,
                },
                simulated=True,
            )

        elif self.failure_mode == MockFailureMode.JOINT_HOLDER_MISSING:
            return VerificationResult(
                is_verified=False,
                provider_type="MOCK_BANK",
                confirmed_fields=[],
                mismatch_flags=[{
                    "code": self.failure_mode.value,
                    "field": "joint_account_holder",
                    "declared_value": str(instrument.joint_account_holder or "None"),
                    "message": "AICTE Regional Officer is not registered as joint holder in bank CBS records.",
                }],
                raw_response={
                    "gateway": "MOCK_CBS_V1",
                    "status": "MISMATCH",
                    "error_code": "JOINT_HOLDER_NOT_FOUND",
                    "joint_account_holder": instrument.joint_account_holder,
                    "simulated": True,
                },
                simulated=True,
            )

        else:
            raise ValueError(f"Unknown mock failure mode: {self.failure_mode}")
