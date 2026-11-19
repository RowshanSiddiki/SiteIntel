"""Add daily report builder metadata and source links.

Revision ID: 8af3c21d7e90
Revises: 7d2e8f31b4c5
"""
from alembic import op
import sqlalchemy as sa


revision = "8af3c21d7e90"
down_revision = "7d2e8f31b4c5"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("reporting_date", sa.Date(), nullable=True))
    op.create_index(
        "ix_documents_reporting_date", "documents", ["reporting_date"], unique=False
    )
    op.create_table(
        "daily_construction_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("reporting_date", sa.Date(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("fields", sa.JSON(), nullable=False),
        sa.Column("report_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "reporting_date", name="uq_daily_report_project_date"),
        sa.UniqueConstraint("document_id", name="uq_daily_report_document"),
    )
    op.create_index(
        "ix_daily_reports_project_date",
        "daily_construction_reports",
        ["project_id", "reporting_date"],
        unique=False,
    )
    op.create_table(
        "daily_report_sources",
        sa.Column("daily_report_id", sa.Integer(), nullable=False),
        sa.Column("source_document_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["daily_report_id"],
            ["daily_construction_reports.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"], ["documents.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("daily_report_id", "source_document_id"),
    )


def downgrade():
    op.drop_table("daily_report_sources")
    op.drop_index("ix_daily_reports_project_date", table_name="daily_construction_reports")
    op.drop_table("daily_construction_reports")
    op.drop_index("ix_documents_reporting_date", table_name="documents")
    op.drop_column("documents", "reporting_date")
