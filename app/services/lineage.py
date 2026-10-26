"""Lineage helper for parent-child FDR renewal chains."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import List, Optional, Set
import uuid

from sqlalchemy.orm import Session

from app.models.instrument import Instrument


@dataclass
class LineageItem:
    id: uuid.UUID
    fdr_number: Optional[str]
    status: str
    renewal_sequence: int
    principal_amount: Optional[Decimal]
    maturity_date: Optional[date]
    is_current: bool = False
    previous_instrument_id: Optional[uuid.UUID] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "fdr_number": self.fdr_number,
            "status": self.status,
            "renewal_sequence": self.renewal_sequence,
            "principal_amount": self.principal_amount,
            "maturity_date": self.maturity_date,
            "is_current": self.is_current,
            "previous_instrument_id": self.previous_instrument_id,
        }


def get_lineage(session: Session, instrument: Instrument, depth_cap: int = 20) -> List[LineageItem]:
    """Computes the complete ordered renewal chain from root to leaf containing instrument.
    
    Guarantees:
    - Root -> ... -> current -> ... -> leaf ordering.
    - Defensive cycle detection via visited UUID set.
    - Bounded loop depth cap (default 20).
    - Strict tenancy isolation: only instruments belonging to the same institution_id are included.
    - Null-safe fields.
    """
    if not instrument:
        return []

    target_institution_id = instrument.institution_id
    visited: Set[uuid.UUID] = {instrument.id}

    # 1. Traverse upwards (ancestors)
    ancestors: List[Instrument] = []
    curr = instrument
    for _ in range(depth_cap):
        if not curr.previous_instrument_id:
            break
        if curr.previous_instrument_id in visited:
            # Defensive cycle detection
            break
        parent = (
            session.query(Instrument)
            .filter(
                Instrument.id == curr.previous_instrument_id,
                Instrument.institution_id == target_institution_id,
            )
            .first()
        )
        if not parent:
            break
        visited.add(parent.id)
        ancestors.append(parent)
        curr = parent

    # Reverse ancestors so they run from root to immediate parent
    ancestors.reverse()

    # 2. Traverse downwards (descendants)
    descendants: List[Instrument] = []
    curr = instrument
    for _ in range(depth_cap):
        # Prefer live (non-REJECTED) child; fall back to any child if none
        child = (
            session.query(Instrument)
            .filter(
                Instrument.previous_instrument_id == curr.id,
                Instrument.institution_id == target_institution_id,
                Instrument.status != "REJECTED",
            )
            .first()
        )
        if not child:
            child = (
                session.query(Instrument)
                .filter(
                    Instrument.previous_instrument_id == curr.id,
                    Instrument.institution_id == target_institution_id,
                )
                .first()
            )
        if not child or child.id in visited:
            break
        visited.add(child.id)
        descendants.append(child)
        curr = child

    # 3. Assemble full ordered chain
    full_chain = ancestors + [instrument] + descendants
    if len(full_chain) > depth_cap:
        full_chain = full_chain[:depth_cap]

    result: List[LineageItem] = []
    for inst in full_chain:
        result.append(
            LineageItem(
                id=inst.id,
                fdr_number=inst.fdr_number,
                status=inst.status,
                renewal_sequence=inst.renewal_sequence or 1,
                principal_amount=inst.principal_amount,
                maturity_date=inst.maturity_date,
                is_current=(inst.id == instrument.id),
                previous_instrument_id=inst.previous_instrument_id,
            )
        )

    return result
