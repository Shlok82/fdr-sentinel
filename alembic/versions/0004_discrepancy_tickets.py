"""Create discrepancy_tickets table with partial unique index and runtime role privileges

Revision ID: 0004_discrepancy_tickets
Revises: 0003_instrument_lifecycle_fsm
Create Date: 2026-10-26 01:10:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004_discrepancy_tickets"
down_revision: Union[str, None] = "0003_instrument_lifecycle_fsm"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create discrepancy_tickets table
    op.create_table(
        "discrepancy_tickets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "instrument_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("instruments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rule_code", sa.String(20), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("blocking", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'OPEN'")),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("staff_detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "resolved_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "severity IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW')",
            name="ck_discrepancy_tickets_severity",
        ),
        sa.CheckConstraint(
            "status IN ('OPEN', 'RESOLVED')",
            name="ck_discrepancy_tickets_status",
        ),
    )

    # 2. Create standard indexes
    op.create_index("ix_discrepancy_tickets_instrument_id", "discrepancy_tickets", ["instrument_id"])
    op.create_index("ix_discrepancy_tickets_rule_code", "discrepancy_tickets", ["rule_code"])
    op.create_index("ix_discrepancy_tickets_status", "discrepancy_tickets", ["status"])

    # 3. Create partial unique index on (instrument_id, rule_code) WHERE status = 'OPEN'
    op.execute(
        """
        CREATE UNIQUE INDEX uq_open_ticket_instrument_rule
        ON discrepancy_tickets (instrument_id, rule_code)
        WHERE status = 'OPEN';
        """
    )

    # 4. Enforce runtime role privileges (SELECT, INSERT, UPDATE granted; DELETE and TRUNCATE revoked)
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'fdr_app_user') THEN
                GRANT SELECT, INSERT, UPDATE ON discrepancy_tickets TO fdr_app_user;
                REVOKE DELETE, TRUNCATE ON discrepancy_tickets FROM fdr_app_user;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_open_ticket_instrument_rule;")
    op.drop_index("ix_discrepancy_tickets_status", table_name="discrepancy_tickets")
    op.drop_index("ix_discrepancy_tickets_rule_code", table_name="discrepancy_tickets")
    op.drop_index("ix_discrepancy_tickets_instrument_id", table_name="discrepancy_tickets")
    op.drop_table("discrepancy_tickets")
