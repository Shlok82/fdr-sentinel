"""Make instruments business fields nullable for Stage 6 discrepancy engine

Revision ID: 0002_nullable_instrument_fields
Revises: 0001_initial_schema
Create Date: 2026-10-25 19:45:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0002_nullable_instrument_fields"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BUSINESS_COLUMNS = [
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


def upgrade() -> None:
    for col in BUSINESS_COLUMNS:
        op.alter_column("instruments", col, nullable=True)


def downgrade() -> None:
    for col in BUSINESS_COLUMNS:
        op.alter_column("instruments", col, nullable=False)
