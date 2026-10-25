"""Add instrument FSM status check constraint and prior_state column

Revision ID: 0003_instrument_lifecycle_fsm
Revises: 0002_nullable_instrument_fields
Create Date: 2026-10-26 00:15:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0003_instrument_lifecycle_fsm"
down_revision: Union[str, None] = "0002_nullable_instrument_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ALLOWED_FSM_STATES = (
    "SUBMITTED",
    "UNDER_SCRUTINY",
    "DISCREPANCY_FLAGGED",
    "VERIFIED",
    "ACTIVE",
    "EXPIRING",
    "MATURED",
    "RENEWED",
    "RELEASE_REQUESTED",
    "RELEASED",
    "REJECTED",
)


def upgrade() -> None:
    # 1. Fail loudly if any existing instrument row contains a status outside the 11 FSM states
    conn = op.get_bind()
    # Format literal SQL tuple for postgresql compatibility
    in_clause = ", ".join(f"'{s}'" for s in ALLOWED_FSM_STATES)
    invalid_rows = conn.execute(
        sa.text(
            f"""
            SELECT id, status FROM instruments 
            WHERE status NOT IN ({in_clause})
            """
        )
    ).fetchall()

    if invalid_rows:
        sample = [(str(r[0]), str(r[1])) for r in invalid_rows[:5]]
        raise RuntimeError(
            f"Migration aborted: {len(invalid_rows)} row(s) in 'instruments' contain invalid status values "
            f"outside the 11 allowed FSM states: {sample}. Database migration will not proceed."
        )

    # 2. Add nullable prior_state column to instruments
    op.add_column("instruments", sa.Column("prior_state", sa.String(30), nullable=True))

    # 3. Add CHECK constraint on instruments.status
    op.create_check_constraint(
        "ck_instruments_status_fsm",
        "instruments",
        f"status IN ({in_clause})"
    )


def downgrade() -> None:
    op.drop_constraint("ck_instruments_status_fsm", "instruments", type_="check")
    op.drop_column("instruments", "prior_state")
