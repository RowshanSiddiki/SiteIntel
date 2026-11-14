from sqlalchemy import DateTime, ForeignKey, Index, func

from siteintel.extensions import db


class ReportReview(db.Model):
    __tablename__ = "report_reviews"
    __table_args__ = (Index("ix_report_reviews_document_id", "document_id"),)

    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False)
    reviewed_by_id = db.Column(db.Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    fields = db.Column(db.JSON, nullable=False)
    created_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())
