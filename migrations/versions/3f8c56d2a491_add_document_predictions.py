"""add document prediction history

Revision ID: 3f8c56d2a491
Revises: 92b14fe3c7a0
"""
from alembic import op
import sqlalchemy as sa

revision = "3f8c56d2a491"
down_revision = "92b14fe3c7a0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "document_predictions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("model_key", sa.String(32), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("missing", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "model_key", "input_hash", name="uq_prediction_inputs"),
    )
    op.create_index("ix_document_predictions_document_id", "document_predictions", ["document_id"])


def downgrade():
    op.drop_index("ix_document_predictions_document_id", table_name="document_predictions")
    op.drop_table("document_predictions")
