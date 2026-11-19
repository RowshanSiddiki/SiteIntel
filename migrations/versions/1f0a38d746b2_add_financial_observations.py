"""Add source-linked financial observations.

Revision ID: 1f0a38d746b2
Revises: 9be43f1862a1
"""
from alembic import op
import sqlalchemy as sa

revision = "1f0a38d746b2"
down_revision = "9be43f1862a1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "financial_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("label", sa.String(300), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("observed_date", sa.Date()),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("reviewed_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("document_id", "fingerprint", name="uq_financial_observation_source"),
    )
    op.create_index("ix_financial_observations_project_id", "financial_observations", ["project_id"])


def downgrade():
    op.drop_index("ix_financial_observations_project_id", table_name="financial_observations")
    op.drop_table("financial_observations")
