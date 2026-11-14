"""add project monitoring tables

Revision ID: 92b14fe3c7a0
Revises: ad5d0ec2fe8e
Create Date: 2026-11-12 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "92b14fe3c7a0"
down_revision = "ad5d0ec2fe8e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="active",
            nullable=False,
        ),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("target_end_date", sa.Date(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'completed', 'archived')",
            name="ck_projects_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name="fk_projects_owner_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_projects"),
    )
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"])
    op.create_index("ix_projects_status", "projects", ["status"])

    op.create_table(
        "project_members",
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('viewer', 'editor')",
            name="ck_project_members_role_valid",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_project_members_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_project_members_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("project_id", "user_id", name="pk_project_members"),
    )
    op.create_index("ix_project_members_user_id", "project_members", ["user_id"])

    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("uploaded_by_id", sa.Integer(), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("content_type", sa.String(length=127), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
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
        sa.CheckConstraint("byte_size >= 0", name="ck_documents_byte_size_nonnegative"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_documents_project_id_projects",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_id"],
            ["users.id"],
            name="fk_documents_uploaded_by_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
        sa.UniqueConstraint(
            "project_id",
            "storage_key",
            name="uq_document_storage",
        ),
    )
    op.create_index("ix_documents_project_id", "documents", ["project_id"])
    op.create_index("ix_documents_uploaded_by_id", "documents", ["uploaded_by_id"])

    op.create_table(
        "construction_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("record_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("weather_condition", sa.String(length=32), nullable=True),
        sa.Column("worker_count", sa.Integer(), nullable=True),
        sa.Column("equipment_count", sa.Integer(), nullable=True),
        sa.Column("progress_percent", sa.Numeric(precision=5, scale=2), nullable=True),
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
        sa.CheckConstraint(
            "worker_count IS NULL OR worker_count >= 0",
            name="ck_construction_records_worker_count_nonnegative",
        ),
        sa.CheckConstraint(
            "equipment_count IS NULL OR equipment_count >= 0",
            name="ck_construction_records_equipment_count_nonnegative",
        ),
        sa.CheckConstraint(
            "progress_percent IS NULL OR "
            "(progress_percent >= 0 AND progress_percent <= 100)",
            name="ck_construction_records_progress_percent_range",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_construction_records_project_id_projects",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name="fk_construction_records_created_by_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_construction_records"),
    )
    op.create_index(
        "ix_construction_records_project_id",
        "construction_records",
        ["project_id"],
    )
    op.create_index(
        "ix_construction_records_work_date",
        "construction_records",
        ["work_date"],
    )
    op.create_index(
        "ix_construction_records_created_by_id",
        "construction_records",
        ["created_by_id"],
    )

    op.create_table(
        "expenses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("expense_date", sa.Date(), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("vendor", sa.String(length=160), nullable=True),
        sa.Column("reference", sa.String(length=100), nullable=True),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
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
        sa.CheckConstraint("amount >= 0", name="ck_expenses_amount_nonnegative"),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_expenses_status_valid",
        ),
        sa.CheckConstraint("length(currency) = 3", name="ck_expenses_currency_length"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_expenses_project_id_projects",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name="fk_expenses_created_by_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_expenses"),
    )
    op.create_index("ix_expenses_project_id", "expenses", ["project_id"])
    op.create_index("ix_expenses_expense_date", "expenses", ["expense_date"])
    op.create_index("ix_expenses_created_by_id", "expenses", ["created_by_id"])

    op.create_table(
        "quotation_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("quotation_reference", sa.String(length=100), nullable=False),
        sa.Column("vendor", sa.String(length=160), nullable=True),
        sa.Column("item_name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("quoted_on", sa.Date(), nullable=True),
        sa.Column("expires_on", sa.Date(), nullable=True),
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
        sa.CheckConstraint("quantity > 0", name="ck_quotation_items_quantity_positive"),
        sa.CheckConstraint(
            "unit_price >= 0",
            name="ck_quotation_items_unit_price_nonnegative",
        ),
        sa.CheckConstraint(
            "length(currency) = 3",
            name="ck_quotation_items_currency_length",
        ),
        sa.CheckConstraint(
            "expires_on IS NULL OR quoted_on IS NULL OR expires_on >= quoted_on",
            name="ck_quotation_items_expiry_after_quoted",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_quotation_items_project_id_projects",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name="fk_quotation_items_created_by_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_quotation_items"),
    )
    op.create_index("ix_quotation_items_project_id", "quotation_items", ["project_id"])
    op.create_index(
        "ix_quotation_items_reference",
        "quotation_items",
        ["quotation_reference"],
    )
    op.create_index(
        "ix_quotation_items_created_by_id",
        "quotation_items",
        ["created_by_id"],
    )


def downgrade():
    op.drop_table("quotation_items")
    op.drop_table("expenses")
    op.drop_table("construction_records")
    op.drop_table("documents")
    op.drop_table("project_members")
    op.drop_table("projects")
