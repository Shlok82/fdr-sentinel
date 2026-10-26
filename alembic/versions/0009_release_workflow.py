"""Add enhancements, columns, constraints and permissions to release_requests

Revision ID: 0009_release_workflow
Revises: 0008_renewal_chain_constraints
Create Date: 2026-10-27 03:45:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0009_release_workflow"
down_revision: Union[str, None] = "0008_renewal_chain_constraints"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Add new columns
    op.add_column("release_requests", sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    op.add_column("release_requests", sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("release_requests", sa.Column("scrutiny_justification", sa.Text(), nullable=True))
    op.add_column("release_requests", sa.Column("scrutiny_recorded_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("release_requests", sa.Column("scrutiny_evidence_document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="SET NULL"), nullable=True))
    op.add_column("release_requests", sa.Column("authority_justification", sa.Text(), nullable=True))
    op.add_column("release_requests", sa.Column("release_order_hash", sa.String(length=64), nullable=True))

    # 2. Update foreign key on instrument_id to RESTRICT
    op.drop_constraint("release_requests_instrument_id_fkey", "release_requests", type_="foreignkey")
    op.create_foreign_key(
        "release_requests_instrument_id_fkey",
        "release_requests",
        "instruments",
        ["instrument_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # 3. Check constraints
    op.create_check_constraint(
        "ck_release_requests_authority_decision",
        "release_requests",
        "authority_decision IN ('PENDING', 'APPROVED', 'REJECTED')",
    )
    op.create_check_constraint(
        "ck_release_requests_scrutiny_rec",
        "release_requests",
        "scrutiny_recommendation IS NULL OR scrutiny_recommendation IN ('RECOMMENDED', 'NOT_RECOMMENDED')",
    )

    # 4. Partial unique index: at most ONE pending release request per instrument
    op.create_index(
        "uq_release_requests_one_pending",
        "release_requests",
        ["instrument_id"],
        unique=True,
        postgresql_where=sa.text("authority_decision = 'PENDING'"),
    )

    # 5. Revoke DELETE and TRUNCATE from runtime role
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'fdr_app_user') THEN
                GRANT SELECT, INSERT, UPDATE ON release_requests TO fdr_app_user;
                REVOKE DELETE, TRUNCATE ON release_requests FROM fdr_app_user;
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'fdr_app_user') THEN
                GRANT DELETE, TRUNCATE ON release_requests TO fdr_app_user;
            END IF;
        END
        $$;
    """)

    op.drop_index("uq_release_requests_one_pending", table_name="release_requests")
    op.drop_constraint("ck_release_requests_scrutiny_rec", table_name="release_requests", type_="check")
    op.drop_constraint("ck_release_requests_authority_decision", table_name="release_requests", type_="check")

    op.drop_constraint("release_requests_instrument_id_fkey", "release_requests", type_="foreignkey")
    op.create_foreign_key(
        "release_requests_instrument_id_fkey",
        "release_requests",
        "instruments",
        ["instrument_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_column("release_requests", "release_order_hash")
    op.drop_column("release_requests", "authority_justification")
    op.drop_column("release_requests", "scrutiny_evidence_document_id")
    op.drop_column("release_requests", "scrutiny_recorded_at")
    op.drop_column("release_requests", "scrutiny_justification")
    op.drop_column("release_requests", "requested_at")
    op.drop_column("release_requests", "requested_by_user_id")
