"""Temporal engine, alerts, notifications, and scheduler state

Revision ID: 0006_temporal_engine_and_alerts
Revises: 0005_revoke_verification_delete
Create Date: 2026-10-27 00:30:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0006_temporal_engine_and_alerts"
down_revision: Union[str, None] = "0005_revoke_verification_delete"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Alter alerts table
    op.add_column(
        "alerts",
        sa.Column(
            "severity",
            sa.String(20),
            server_default="INFO",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "chk_alerts_severity",
        "alerts",
        "severity IN ('INFO', 'WARNING', 'URGENT', 'CRITICAL')",
    )

    op.add_column(
        "alerts",
        sa.Column(
            "status",
            sa.String(20),
            server_default="OPEN",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "chk_alerts_status",
        "alerts",
        "status IN ('OPEN', 'SUPERSEDED')",
    )

    op.add_column(
        "alerts",
        sa.Column(
            "business_date",
            sa.Date(),
            server_default="2026-10-25",
            nullable=False,
        ),
    )
    op.alter_column("alerts", "business_date", server_default=None)

    op.add_column(
        "alerts",
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )

    op.add_column(
        "alerts",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default="2026-10-25 00:00:00+00",
            nullable=False,
        ),
    )
    op.alter_column("alerts", "created_at", server_default=None)

    # Alter existing columns to nullable
    op.alter_column("alerts", "tier", nullable=True)
    op.alter_column("alerts", "channel", nullable=True)
    op.alter_column("alerts", "recipient_email", nullable=True)
    op.alter_column("alerts", "sent_at", nullable=True)
    op.alter_column("alerts", "escalation_level_reached", nullable=True)

    op.create_index("ix_alerts_instrument_id_status", "alerts", ["instrument_id", "status"])
    op.create_index("ix_alerts_status_business_date", "alerts", ["status", "business_date"])

    # 2. notifications table
    op.create_table(
        "notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("alert_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("audience_role", sa.String(50), nullable=False),
        sa.Column("audience_institution_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("institutions.id", ondelete="CASCADE"), nullable=True),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("sms_text", sa.String(320), nullable=False),
        sa.Column("channels", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[\"IN_APP\", \"EMAIL\", \"SMS\"]'::jsonb"), nullable=False),
        sa.Column("language", sa.String(10), server_default="en", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.CheckConstraint(
            "audience_role IN ('INSTITUTION_ADMIN', 'SCRUTINY_OFFICER', 'APPROVER_AUTHORITY')",
            name="chk_notifications_audience_role",
        ),
        sa.CheckConstraint(
            "(audience_role = 'INSTITUTION_ADMIN' AND audience_institution_id IS NOT NULL) OR (audience_role != 'INSTITUTION_ADMIN' AND audience_institution_id IS NULL)",
            name="chk_notifications_audience_institution",
        ),
    )
    op.create_index(
        "ix_notifications_audience_read",
        "notifications",
        ["audience_role", "audience_institution_id", "read_at"],
    )
    op.create_index("ix_notifications_alert_id", "notifications", ["alert_id"])

    # 3. scheduler_runs table
    op.create_table(
        "scheduler_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("trigger", sa.String(30), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("transitions_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("alerts_created", sa.Integer(), server_default="0", nullable=False),
        sa.Column("alerts_superseded", sa.Integer(), server_default="0", nullable=False),
        sa.Column("instruments_examined", sa.Integer(), server_default="0", nullable=False),
        sa.Column("instruments_skipped", sa.Integer(), server_default="0", nullable=False),
        sa.Column("errors_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "trigger IN ('SCHEDULED', 'STARTUP', 'TIME_MACHINE', 'MANUAL')",
            name="chk_scheduler_runs_trigger",
        ),
        sa.CheckConstraint(
            "status IN ('SUCCESS', 'PARTIAL', 'FAILED')",
            name="chk_scheduler_runs_status",
        ),
    )
    op.create_index("ix_scheduler_runs_started_at", "scheduler_runs", [sa.text("started_at DESC")])

    # 4. scheduler_state table
    op.create_table(
        "scheduler_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("last_tick_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_effective_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("scheduler_runs.id", ondelete="SET NULL"), nullable=True),
        sa.CheckConstraint("id = 1", name="chk_scheduler_state_single_row"),
    )
    op.execute("INSERT INTO scheduler_state (id) VALUES (1) ON CONFLICT DO NOTHING;")

    # 5. Permissions
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'fdr_app_user') THEN
                GRANT SELECT, INSERT, UPDATE ON alerts TO fdr_app_user;
                REVOKE DELETE, TRUNCATE ON alerts FROM fdr_app_user;

                GRANT SELECT, INSERT ON notifications TO fdr_app_user;
                GRANT UPDATE (read_at, read_by_user_id) ON notifications TO fdr_app_user;
                REVOKE DELETE, TRUNCATE ON notifications FROM fdr_app_user;

                GRANT SELECT, INSERT ON scheduler_runs TO fdr_app_user;
                REVOKE UPDATE, DELETE, TRUNCATE ON scheduler_runs FROM fdr_app_user;

                GRANT SELECT, UPDATE ON scheduler_state TO fdr_app_user;
                REVOKE DELETE, TRUNCATE ON scheduler_state FROM fdr_app_user;
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'fdr_app_user') THEN
                REVOKE ALL PRIVILEGES ON scheduler_state FROM fdr_app_user;
                REVOKE ALL PRIVILEGES ON scheduler_runs FROM fdr_app_user;
                REVOKE ALL PRIVILEGES ON notifications FROM fdr_app_user;
            END IF;
        END
        $$;
    """)
    op.drop_table("scheduler_state")
    op.drop_table("scheduler_runs")
    op.drop_table("notifications")
    op.drop_index("ix_alerts_status_business_date", table_name="alerts")
    op.drop_index("ix_alerts_instrument_id_status", table_name="alerts")
    op.drop_constraint("chk_alerts_status", "alerts", type_="check")
    op.drop_constraint("chk_alerts_severity", "alerts", type_="check")
    op.drop_column("alerts", "created_at")
    op.drop_column("alerts", "details")
    op.drop_column("alerts", "business_date")
    op.drop_column("alerts", "status")
    op.drop_column("alerts", "severity")
    op.alter_column("alerts", "tier", nullable=False)
    op.alter_column("alerts", "channel", nullable=False)
    op.alter_column("alerts", "recipient_email", nullable=False)
    op.alter_column("alerts", "sent_at", nullable=False)
    op.alter_column("alerts", "escalation_level_reached", nullable=False)
