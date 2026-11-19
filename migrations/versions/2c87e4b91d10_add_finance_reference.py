"""Record financial document identity for conservative duplicate detection.

Revision ID: 2c87e4b91d10
Revises: 1f0a38d746b2
"""
from alembic import op
import sqlalchemy as sa

revision = "2c87e4b91d10"
down_revision = "1f0a38d746b2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("financial_observations", sa.Column("document_reference", sa.String(128), nullable=True))


def downgrade():
    op.drop_column("financial_observations", "document_reference")
