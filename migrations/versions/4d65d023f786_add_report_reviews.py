"""Store confirmed OCR reviews without altering original reports.

Revision ID: 4d65d023f786
Revises: 3f8c56d2a491
"""
from alembic import op
import sqlalchemy as sa

revision = "4d65d023f786"
down_revision = "3f8c56d2a491"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "report_reviews",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("reviewed_by_id", sa.Integer(), nullable=False),
        sa.Column("fields", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewed_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_report_reviews_document_id", "report_reviews", ["document_id"])


def downgrade():
    op.drop_index("ix_report_reviews_document_id", table_name="report_reviews")
    op.drop_table("report_reviews")
