from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
    or_,
)

from siteintel.extensions import db


class TimestampMixin:
    created_at = db.Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
    )
    updated_at = db.Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
    )


class Project(TimestampMixin, db.Model):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'paused', 'completed', 'archived')",
            name="status_valid",
        ),
        Index("ix_projects_owner_id", "owner_id"),
        Index("ix_projects_status", "status"),
    )

    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(
        db.Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name = db.Column(String(160), nullable=False)
    description = db.Column(Text)
    status = db.Column(
        String(20),
        nullable=False,
        default="active",
        server_default="active",
    )
    start_date = db.Column(Date)
    target_end_date = db.Column(Date)
    completed_at = db.Column(DateTime(timezone=True))

    owner = db.relationship("User", back_populates="projects_owned")
    members = db.relationship(
        "ProjectMember",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    documents = db.relationship(
        "Document",
        back_populates="project",
        passive_deletes="all",
    )
    construction_records = db.relationship(
        "ConstructionRecord",
        back_populates="project",
        passive_deletes="all",
    )
    expenses = db.relationship(
        "Expense",
        back_populates="project",
        passive_deletes="all",
    )
    quotation_items = db.relationship(
        "QuotationItem",
        back_populates="project",
        passive_deletes="all",
    )

    @classmethod
    def accessible_to(cls, user_id: int, permission: str = "viewer"):
        if permission not in {"viewer", "editor"}:
            raise ValueError("permission must be 'viewer' or 'editor'.")

        membership = db.exists().where(
            ProjectMember.project_id == cls.id,
            ProjectMember.user_id == user_id,
        )
        if permission == "editor":
            membership = db.exists().where(
                ProjectMember.project_id == cls.id,
                ProjectMember.user_id == user_id,
                ProjectMember.role == "editor",
            )
        return db.select(cls).where(or_(cls.owner_id == user_id, membership))


class ProjectMember(db.Model):
    __tablename__ = "project_members"
    __table_args__ = (
        CheckConstraint("role IN ('viewer', 'editor')", name="role_valid"),
        Index("ix_project_members_user_id", "user_id"),
    )

    project_id = db.Column(
        db.Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id = db.Column(
        db.Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role = db.Column(String(16), nullable=False)
    joined_at = db.Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
    )

    project = db.relationship("Project", back_populates="members")
    user = db.relationship("User", back_populates="project_memberships")


class Document(TimestampMixin, db.Model):
    __tablename__ = "documents"
    __table_args__ = (
        db.UniqueConstraint("project_id", "storage_key", name="uq_document_storage"),
        CheckConstraint("byte_size >= 0", name="byte_size_nonnegative"),
        Index("ix_documents_project_id", "project_id"),
        Index("ix_documents_uploaded_by_id", "uploaded_by_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(
        db.Integer,
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    uploaded_by_id = db.Column(
        db.Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    file_name = db.Column(String(255), nullable=False)
    storage_key = db.Column(String(500), nullable=False)
    content_type = db.Column(String(127), nullable=False)
    byte_size = db.Column(BigInteger, nullable=False)
    sha256 = db.Column(String(64))

    project = db.relationship("Project", back_populates="documents")
    uploaded_by = db.relationship("User", back_populates="uploaded_documents")


class ConstructionRecord(TimestampMixin, db.Model):
    __tablename__ = "construction_records"
    __table_args__ = (
        CheckConstraint(
            "worker_count IS NULL OR worker_count >= 0",
            name="worker_count_nonnegative",
        ),
        CheckConstraint(
            "equipment_count IS NULL OR equipment_count >= 0",
            name="equipment_count_nonnegative",
        ),
        CheckConstraint(
            "progress_percent IS NULL OR "
            "(progress_percent >= 0 AND progress_percent <= 100)",
            name="progress_percent_range",
        ),
        Index("ix_construction_records_project_id", "project_id"),
        Index("ix_construction_records_work_date", "work_date"),
        Index("ix_construction_records_created_by_id", "created_by_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(
        db.Integer,
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_by_id = db.Column(
        db.Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    work_date = db.Column(Date, nullable=False)
    record_type = db.Column(String(64), nullable=False)
    title = db.Column(String(200), nullable=False)
    description = db.Column(Text)
    weather_condition = db.Column(String(32))
    worker_count = db.Column(db.Integer)
    equipment_count = db.Column(db.Integer)
    progress_percent = db.Column(Numeric(5, 2))

    project = db.relationship("Project", back_populates="construction_records")
    created_by = db.relationship(
        "User",
        back_populates="construction_records_created",
    )


class Expense(TimestampMixin, db.Model):
    __tablename__ = "expenses"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="amount_nonnegative"),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="status_valid",
        ),
        CheckConstraint("length(currency) = 3", name="currency_length"),
        Index("ix_expenses_project_id", "project_id"),
        Index("ix_expenses_expense_date", "expense_date"),
        Index("ix_expenses_created_by_id", "created_by_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(
        db.Integer,
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_by_id = db.Column(
        db.Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    expense_date = db.Column(Date, nullable=False)
    category = db.Column(String(64), nullable=False)
    description = db.Column(Text, nullable=False)
    vendor = db.Column(String(160))
    reference = db.Column(String(100))
    amount = db.Column(Numeric(14, 2), nullable=False)
    currency = db.Column(String(3), nullable=False)
    status = db.Column(
        String(16),
        nullable=False,
        default="pending",
        server_default="pending",
    )

    project = db.relationship("Project", back_populates="expenses")
    created_by = db.relationship("User", back_populates="expenses_created")


class QuotationItem(TimestampMixin, db.Model):
    __tablename__ = "quotation_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price >= 0", name="unit_price_nonnegative"),
        CheckConstraint("length(currency) = 3", name="currency_length"),
        CheckConstraint(
            "expires_on IS NULL OR quoted_on IS NULL OR expires_on >= quoted_on",
            name="expiry_after_quoted",
        ),
        Index("ix_quotation_items_project_id", "project_id"),
        Index("ix_quotation_items_reference", "quotation_reference"),
        Index("ix_quotation_items_created_by_id", "created_by_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(
        db.Integer,
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_by_id = db.Column(
        db.Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    quotation_reference = db.Column(String(100), nullable=False)
    vendor = db.Column(String(160))
    item_name = db.Column(String(200), nullable=False)
    description = db.Column(Text)
    quantity = db.Column(Numeric(14, 3), nullable=False)
    unit = db.Column(String(32), nullable=False)
    unit_price = db.Column(Numeric(14, 2), nullable=False)
    currency = db.Column(String(3), nullable=False)
    quoted_on = db.Column(Date)
    expires_on = db.Column(Date)

    project = db.relationship("Project", back_populates="quotation_items")
    created_by = db.relationship("User", back_populates="quotation_items_created")
