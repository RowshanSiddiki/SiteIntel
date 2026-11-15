"""Add knowledge-document metadata and the document chunk index.

Existing rows keep NULL in every new column, so Phase 1 reports are untouched.

Revision ID: 7d2e8f31b4c5
Revises: 6c1d4e7a9b20
"""
from alembic import op
import sqlalchemy as sa

revision = "7d2e8f31b4c5"
down_revision = "6c1d4e7a9b20"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("category", sa.String(30), nullable=True))
    op.add_column("documents", sa.Column("processing_status", sa.String(20), nullable=True))
    op.add_column("documents", sa.Column("processing_error", sa.String(500), nullable=True))
    op.add_column("documents", sa.Column("page_count", sa.Integer(), nullable=True))
    op.add_column("documents", sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        op.f("ck_documents_category_valid"),
        "documents",
        "category IS NULL OR category IN "
        "('agreement', 'quotation', 'specification', 'boq', 'other')",
    )
    op.create_check_constraint(
        op.f("ck_documents_processing_status_valid"),
        "documents",
        "processing_status IS NULL OR processing_status IN "
        "('pending', 'processing', 'completed', 'failed')",
    )
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=False),
        sa.Column("embedding_model", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], name=op.f("fk_document_chunks_document_id_documents"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name=op.f("fk_document_chunks_project_id_projects"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_chunks")),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_document_chunk_index"),
        sa.CheckConstraint("page_number >= 1", name=op.f("ck_document_chunks_page_number_positive")),
    )
    op.create_index("ix_document_chunks_project_id", "document_chunks", ["project_id"])


def downgrade():
    # Knowledge documents have no meaning without their new columns; remove
    # them (rows only, never report rows) before dropping the columns.
    op.execute("DELETE FROM documents WHERE category IS NOT NULL")
    # Dropping the table removes its indexes too (MySQL refuses to drop an
    # index that backs a foreign key on its own).
    op.drop_table("document_chunks")
    op.drop_constraint(op.f("ck_documents_processing_status_valid"), "documents", type_="check")
    op.drop_constraint(op.f("ck_documents_category_valid"), "documents", type_="check")
    op.drop_column("documents", "processed_at")
    op.drop_column("documents", "page_count")
    op.drop_column("documents", "processing_error")
    op.drop_column("documents", "processing_status")
    op.drop_column("documents", "category")
