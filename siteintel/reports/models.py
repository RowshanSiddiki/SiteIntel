from sqlalchemy import Date, DateTime, ForeignKey, Index, Text, UniqueConstraint, func

from siteintel.extensions import db
from siteintel.projects.models import Document


daily_report_sources = db.Table(
    "daily_report_sources",
    db.Column(
        "daily_report_id",
        db.Integer,
        ForeignKey("daily_construction_reports.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "source_document_id",
        db.Integer,
        ForeignKey("documents.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
)


class ReportReview(db.Model):
    __tablename__ = "report_reviews"
    __table_args__ = (Index("ix_report_reviews_document_id", "document_id"),)

    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False)
    reviewed_by_id = db.Column(db.Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    fields = db.Column(db.JSON, nullable=False)
    created_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class DailyConstructionReport(db.Model):
    __tablename__ = "daily_construction_reports"
    __table_args__ = (
        UniqueConstraint("project_id", "reporting_date", name="uq_daily_report_project_date"),
        UniqueConstraint("document_id", name="uq_daily_report_document"),
        Index("ix_daily_reports_project_date", "project_id", "reporting_date"),
    )

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(
        db.Integer, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    reporting_date = db.Column(Date, nullable=False)
    created_by_id = db.Column(
        db.Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    document_id = db.Column(
        db.Integer, ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False
    )
    fields = db.Column(db.JSON, nullable=False)
    report_text = db.Column(Text, nullable=False)
    created_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = db.Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    generated_document = db.relationship(Document, foreign_keys=[document_id])
    source_documents = db.relationship(
        Document,
        secondary=daily_report_sources,
        order_by=Document.id,
    )
