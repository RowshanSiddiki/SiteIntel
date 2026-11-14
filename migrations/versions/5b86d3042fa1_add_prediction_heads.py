"""Track the current prediction without duplicating historical snapshots.

Revision ID: 5b86d3042fa1
Revises: 4d65d023f786
"""
from alembic import op
import sqlalchemy as sa

revision = "5b86d3042fa1"
down_revision = "4d65d023f786"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "prediction_heads",
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("model_key", sa.String(32), nullable=False),
        sa.Column("prediction_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["prediction_id"], ["document_predictions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("document_id", "model_key"),
    )
    op.execute(
        "INSERT INTO prediction_heads (document_id, model_key, prediction_id) "
        "SELECT document_id, model_key, MAX(id) FROM document_predictions "
        "GROUP BY document_id, model_key"
    )


def downgrade():
    op.drop_table("prediction_heads")
