from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from app.models.instrument import Instrument
from app.models.document import Document


@dataclass(frozen=True)
class VerificationResult:
    """Standardized outcome from an FDR verification provider."""
    is_verified: bool
    provider_type: str  # "MOCK_BANK" | "MANUAL_LETTER"
    confirmed_fields: List[str] = field(default_factory=list)
    mismatch_flags: List[Dict[str, Any]] = field(default_factory=list)
    raw_response: Dict[str, Any] = field(default_factory=dict)
    simulated: bool = False


class BaseVerificationProvider(ABC):
    """Abstract contract for institutional FDR verification providers."""

    @abstractmethod
    def verify_instrument(
        self,
        db: Session,
        instrument: Instrument,
        document: Optional[Document] = None,
        **kwargs: Any,
    ) -> VerificationResult:
        """Executes verification of the instrument against the external/manual source."""
        pass

    @abstractmethod
    def get_provider_name(self) -> str:
        """Human-readable provider name."""
        pass

    @abstractmethod
    def is_live_authorized(self) -> bool:
        """True only for legally authorized direct CBS/Account Aggregator channels."""
        pass
