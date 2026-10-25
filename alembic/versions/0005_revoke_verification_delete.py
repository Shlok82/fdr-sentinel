"""Revoke delete on verification_records and scrutiny_notes for runtime role

Revision ID: 0005_revoke_verification_delete
Revises: 0004_discrepancy_tickets
Create Date: 2026-10-26 02:25:00.000000

"""
from typing import Sequence, Union
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0005_revoke_verification_delete"
down_revision: Union[str, None] = "0004_discrepancy_tickets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'fdr_app_user') THEN
                GRANT SELECT, INSERT, UPDATE ON verification_records TO fdr_app_user;
                REVOKE DELETE, TRUNCATE ON verification_records FROM fdr_app_user;
                GRANT SELECT, INSERT, UPDATE ON scrutiny_notes TO fdr_app_user;
                REVOKE DELETE, TRUNCATE ON scrutiny_notes FROM fdr_app_user;
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'fdr_app_user') THEN
                GRANT DELETE, TRUNCATE ON verification_records TO fdr_app_user;
                GRANT DELETE, TRUNCATE ON scrutiny_notes TO fdr_app_user;
            END IF;
        END
        $$;
    """)
