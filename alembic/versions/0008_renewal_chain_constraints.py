"""Add renewal chain constraints and partial unique index on instruments

Revision ID: 0008_renewal_chain_constraints
Revises: 0007_trust_score_breakdown
Create Date: 2026-10-27 03:30:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0008_renewal_chain_constraints"
down_revision: Union[str, None] = "0007_trust_score_breakdown"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Loud verification of existing data before applying constraints
    dups = conn.execute(sa.text("""
        SELECT previous_instrument_id, count(*)
        FROM instruments
        WHERE previous_instrument_id IS NOT NULL AND status <> 'REJECTED'
        GROUP BY previous_instrument_id
        HAVING count(*) > 1;
    """)).fetchall()
    if dups:
        raise RuntimeError(
            f"Migration aborted: existing data violates unique live child constraint: {dups}"
        )

    self_refs = conn.execute(sa.text("""
        SELECT id FROM instruments
        WHERE previous_instrument_id IS NOT NULL AND previous_instrument_id = id;
    """)).fetchall()
    if self_refs:
        raise RuntimeError(
            f"Migration aborted: existing data contains self-referencing previous_instrument_id: {self_refs}"
        )

    # 2. Check constraint: self-reference prevention
    op.create_check_constraint(
        "ck_instruments_previous_not_self",
        "instruments",
        "previous_instrument_id IS NULL OR previous_instrument_id <> id",
    )

    # 3. Ensure regular index on previous_instrument_id exists
    conn.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_instruments_previous_instrument_id
        ON instruments (previous_instrument_id);
    """))

    # 4. Partial unique index: at most one live (non-REJECTED) child per parent
    op.create_index(
        "uq_instruments_one_live_child",
        "instruments",
        ["previous_instrument_id"],
        unique=True,
        postgresql_where=sa.text("previous_instrument_id IS NOT NULL AND status <> 'REJECTED'"),
    )


def downgrade() -> None:
    op.drop_index("uq_instruments_one_live_child", table_name="instruments")
    op.drop_constraint("ck_instruments_previous_not_self", table_name="instruments", type_="check")
