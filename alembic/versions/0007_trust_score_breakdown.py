"""Add trust_breakdown and trust_computed_at to instruments

Revision ID: 0007_trust_score_breakdown
Revises: 0006_temporal_engine_and_alerts
Create Date: 2026-10-27 02:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0007_trust_score_breakdown"
down_revision: Union[str, None] = "0006_temporal_engine_and_alerts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add trust_breakdown JSONB NULL if not present
    op.add_column(
        "instruments",
        sa.Column(
            "trust_breakdown",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    # 2. Add trust_computed_at TIMESTAMPTZ NULL if not present
    op.add_column(
        "instruments",
        sa.Column(
            "trust_computed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # 3. Grant runtime role UPDATE/SELECT on instruments (reinforces existing table grant)
    op.execute("GRANT SELECT, INSERT, UPDATE ON TABLE instruments TO fdr_app_user")


def downgrade() -> None:
    op.drop_column("instruments", "trust_computed_at")
    op.drop_column("instruments", "trust_breakdown")
