"""Initial schema migration: all 11 tables with RBAC privilege constraints

Revision ID: 0001_initial_schema
Revises: 
Create Date: 2026-10-24 13:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. institutions table
    op.create_table(
        "institutions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("aicte_permanent_id", sa.String(50), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("region", sa.String(50), nullable=False),
        sa.Column("state", sa.String(50), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("contact_email", sa.String(150), nullable=False),
        sa.Column("contact_phone", sa.String(20), nullable=False),
        sa.Column("institution_type", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_institutions_aicte_permanent_id", "institutions", ["aicte_permanent_id"], unique=True)
    op.create_index("ix_institutions_region", "institutions", ["region"])

    # 2. users table
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("username", sa.String(50), nullable=False),
        sa.Column("email", sa.String(150), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(150), nullable=False),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("institution_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("institutions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_demo_admin", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_role", "users", ["role"])
    op.create_index("ix_users_institution_id", "users", ["institution_id"])

    # 3. instruments table
    op.create_table(
        "instruments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("institution_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fdr_number", sa.String(100), nullable=False),
        sa.Column("bank_name", sa.String(150), nullable=False),
        sa.Column("bank_branch", sa.String(150), nullable=False),
        sa.Column("ifsc_code", sa.String(20), nullable=False),
        sa.Column("principal_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("maturity_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("interest_rate_percent", sa.Numeric(5, 2), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("maturity_date", sa.Date(), nullable=False),
        sa.Column("tenure_months", sa.Integer(), nullable=False),
        sa.Column("joint_account_holder", sa.String(255), nullable=False),
        sa.Column("status", sa.String(30), server_default="SUBMITTED", nullable=False),
        sa.Column("previous_instrument_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("instruments.id", ondelete="SET NULL"), nullable=True),
        sa.Column("renewal_sequence", sa.Integer(), server_default="1", nullable=False),
        sa.Column("trust_score", sa.Integer(), server_default="20", nullable=False),
        sa.Column("trust_score_breakdown", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_instruments_institution_id", "instruments", ["institution_id"])
    op.create_index("ix_instruments_fdr_number", "instruments", ["fdr_number"])
    op.create_index("ix_instruments_bank_name", "instruments", ["bank_name"])
    op.create_index("ix_instruments_maturity_date", "instruments", ["maturity_date"])
    op.create_index("ix_instruments_status", "instruments", ["status"])
    op.create_index("ix_instruments_previous_instrument_id", "instruments", ["previous_instrument_id"])

    # 4. documents table
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_type", sa.String(50), nullable=False),
        sa.Column("storage_path", sa.String(500), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("file_mime_type", sa.String(100), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256_hash", sa.String(64), nullable=False),
        sa.Column("encrypted_dek", sa.Text(), nullable=False),
        sa.Column("encryption_iv", sa.String(32), nullable=False),
        sa.Column("encryption_tag", sa.String(32), nullable=False),
        sa.Column("extracted_payload", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_documents_instrument_id", "documents", ["instrument_id"])

    # 5. field_provenance table
    op.create_table(
        "field_provenance",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("field_name", sa.String(50), nullable=False),
        sa.Column("submitted_value", sa.Text(), nullable=True),
        sa.Column("current_value", sa.Text(), nullable=True),
        sa.Column("provenance_type", sa.String(30), nullable=False),
        sa.Column("verified_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confidence_score", sa.Numeric(4, 3), nullable=True),
    )
    op.create_index("ix_field_provenance_instrument_id", "field_provenance", ["instrument_id"])

    # 6. verification_records table
    op.create_table(
        "verification_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider_type", sa.String(50), nullable=False),
        sa.Column("raw_response", postgresql.JSONB(), nullable=True),
        sa.Column("is_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("mismatch_flags", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_verification_records_instrument_id", "verification_records", ["instrument_id"])

    # 7. scrutiny_notes table
    op.create_table(
        "scrutiny_notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("officer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("note_text", sa.Text(), nullable=False),
        sa.Column("action_recommended", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_scrutiny_notes_instrument_id", "scrutiny_notes", ["instrument_id"])
    op.create_index("ix_scrutiny_notes_officer_id", "scrutiny_notes", ["officer_id"])

    # 8. release_requests table
    op.create_table(
        "release_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("institution_justification", sa.Text(), nullable=False),
        sa.Column("statutory_checklist", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("evidence_document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("scrutiny_recommendation", sa.String(30), nullable=True),
        sa.Column("scrutiny_officer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("authority_decision", sa.String(30), server_default="PENDING", nullable=False),
        sa.Column("approver_authority_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("ed25519_signature", sa.Text(), nullable=True),
        sa.Column("decision_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_release_requests_instrument_id", "release_requests", ["instrument_id"])

    # 9. alerts table with UNIQUE(instrument_id, alert_type)
    op.create_table(
        "alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tier", sa.Integer(), nullable=False),
        sa.Column("alert_type", sa.String(50), nullable=False),
        sa.Column("channel", sa.String(30), nullable=False),
        sa.Column("recipient_email", sa.String(150), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalation_level_reached", sa.Integer(), server_default="1", nullable=False),
        sa.UniqueConstraint("instrument_id", "alert_type", name="uq_alerts_instrument_alert_type"),
    )
    op.create_index("ix_alerts_instrument_id", "alerts", ["instrument_id"])

    # 10. audit_log table (BigSerial primary key, hash chain)
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_ip_address", sa.String(45), nullable=True),
        sa.Column("canonical_payload", postgresql.JSONB(), nullable=False),
        sa.Column("previous_hash", sa.String(64), nullable=False),
        sa.Column("entry_hash", sa.String(64), nullable=False),
    )
    op.create_index("ix_audit_log_entity_type", "audit_log", ["entity_type"])
    op.create_index("ix_audit_log_entity_id", "audit_log", ["entity_id"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    op.create_index("ix_audit_log_actor_user_id", "audit_log", ["actor_user_id"])
    op.create_index("ix_audit_log_entry_hash", "audit_log", ["entry_hash"])

    # 11. clock_state table
    op.create_table(
        "clock_state",
        sa.Column("id", sa.Integer(), primary_key=True, server_default="1"),
        sa.Column("offset_seconds", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("is_simulated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Insert default initial clock_state row
    op.execute("""
        INSERT INTO clock_state (id, offset_seconds, is_simulated, updated_at)
        VALUES (1, 0, false, NOW())
        ON CONFLICT (id) DO NOTHING;
    """)

    # Enforce PostgreSQL privilege restriction on audit_log for runtime app user
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'fdr_app_user') THEN
                GRANT SELECT, INSERT ON audit_log TO fdr_app_user;
                REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM fdr_app_user;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.drop_table("clock_state")
    op.drop_table("audit_log")
    op.drop_table("alerts")
    op.drop_table("release_requests")
    op.drop_table("scrutiny_notes")
    op.drop_table("verification_records")
    op.drop_table("field_provenance")
    op.drop_table("documents")
    op.drop_table("instruments")
    op.drop_table("users")
    op.drop_table("institutions")
