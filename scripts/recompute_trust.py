"""Recompute earned trust scores across all instruments in the database.

Usage:
    python scripts/recompute_trust.py

Idempotent: updates trust_score, trust_breakdown, and trust_computed_at.
Prints summary counts only.
"""
import os
import sys
import logging

# Ensure project root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.instrument import Instrument
from app.services.trust_score import recompute_trust

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("recompute_trust")


def main() -> int:
    db: Session = SessionLocal()
    try:
        instruments = db.query(Instrument).all()
        total = len(instruments)
        success_count = 0
        error_count = 0

        for inst in instruments:
            try:
                recompute_trust(db, inst.id)
                db.commit()
                success_count += 1
            except Exception as exc:
                db.rollback()
                error_count += 1
                logger.error("Failed to recompute trust for instrument %s: %s", inst.id, exc)

        print(
            f"Trust score recomputation complete: {total} examined, "
            f"{success_count} updated, {error_count} failed."
        )
        return 0 if error_count == 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
