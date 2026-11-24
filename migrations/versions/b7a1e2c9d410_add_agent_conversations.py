"""Add user- and project-scoped AI Agent conversations.

Revision ID: b7a1e2c9d410
Revises: 2c87e4b91d10
"""
from alembic import op
import sqlalchemy as sa


revision = "b7a1e2c9d410"
down_revision = "2c87e4b91d10"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_conversations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("messages", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"],
            name=op.f("fk_agent_conversations_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name=op.f("fk_agent_conversations_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_conversations")),
    )
    op.create_index(
        "ix_agent_conversations_project_user",
        "agent_conversations",
        ["project_id", "user_id"],
    )
    op.create_index(
        "ix_agent_conversations_updated_at",
        "agent_conversations",
        ["updated_at"],
    )


def downgrade():
    op.drop_index(
        "ix_agent_conversations_updated_at",
        table_name="agent_conversations",
    )
    op.drop_index(
        "ix_agent_conversations_project_user",
        table_name="agent_conversations",
    )
    op.drop_table("agent_conversations")
