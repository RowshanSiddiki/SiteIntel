from flask_login import UserMixin
from sqlalchemy import DateTime, func

from siteintel.extensions import db, login_manager


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(254), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
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

    projects_owned = db.relationship(
        "Project",
        back_populates="owner",
        passive_deletes=True,
    )
    project_memberships = db.relationship(
        "ProjectMember",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    uploaded_documents = db.relationship(
        "Document",
        back_populates="uploaded_by",
        passive_deletes="all",
    )
    construction_records_created = db.relationship(
        "ConstructionRecord",
        back_populates="created_by",
        passive_deletes="all",
    )
    expenses_created = db.relationship(
        "Expense",
        back_populates="created_by",
        passive_deletes="all",
    )
    quotation_items_created = db.relationship(
        "QuotationItem",
        back_populates="created_by",
        passive_deletes="all",
    )

    def __repr__(self) -> str:
        return f"<User id={self.id}>"


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    try:
        identifier = int(user_id)
    except (TypeError, ValueError):
        return None
    return db.session.get(User, identifier)
