"""Extend expenses and add reviewable bill uploads.

Revision ID: 9be43f1862a1
Revises: 8af3c21d7e90
"""
from alembic import op
import sqlalchemy as sa

revision = "9be43f1862a1"
down_revision = "8af3c21d7e90"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("expenses", sa.Column("paid_amount", sa.Numeric(14, 2), nullable=False, server_default="0"))
    op.add_column("expenses", sa.Column("notes", sa.Text(), nullable=True))
    op.add_column("expenses", sa.Column("items", sa.JSON(), nullable=True))
    op.create_table(
        "expense_bills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("uploaded_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("expense_id", sa.Integer(), sa.ForeignKey("expenses.id", ondelete="RESTRICT")),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("storage_id", sa.String(32), nullable=False),
        sa.Column("stored_name", sa.String(40), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("extracted_text", sa.Text()),
        sa.Column("extracted_fields", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "sha256", name="uq_expense_bill_project_hash"),
        sa.UniqueConstraint("expense_id", name="uq_expense_bill_expense"),
    )
    op.create_index("ix_expense_bills_project_id", "expense_bills", ["project_id"])


def downgrade():
    op.drop_index("ix_expense_bills_project_id", table_name="expense_bills")
    op.drop_table("expense_bills")
    op.drop_column("expenses", "items")
    op.drop_column("expenses", "notes")
    op.drop_column("expenses", "paid_amount")
