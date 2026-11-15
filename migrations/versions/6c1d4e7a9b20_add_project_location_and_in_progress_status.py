"""Add a project location and the in_progress status.

Revision ID: 6c1d4e7a9b20
Revises: 5b86d3042fa1
"""
from alembic import op
import sqlalchemy as sa

revision = "6c1d4e7a9b20"
down_revision = "5b86d3042fa1"
branch_labels = None
depends_on = None

# The initial migration passed an already-prefixed name through the naming
# convention, so MySQL stores the constraint under this doubled name.
OLD_CONSTRAINT = "ck_projects_ck_projects_status_valid"
NEW_CONSTRAINT = "ck_projects_status_valid"


def upgrade():
    op.add_column("projects", sa.Column("location", sa.String(200), nullable=True))
    op.drop_constraint(op.f(OLD_CONSTRAINT), "projects", type_="check")
    op.create_check_constraint(
        op.f(NEW_CONSTRAINT),
        "projects",
        "status IN ('active', 'in_progress', 'paused', 'completed', 'archived')",
    )


def downgrade():
    # Rows already set to in_progress would violate the old constraint.
    op.execute("UPDATE projects SET status = 'active' WHERE status = 'in_progress'")
    op.drop_constraint(op.f(NEW_CONSTRAINT), "projects", type_="check")
    op.create_check_constraint(
        op.f(OLD_CONSTRAINT),
        "projects",
        "status IN ('active', 'paused', 'completed', 'archived')",
    )
    op.drop_column("projects", "location")
