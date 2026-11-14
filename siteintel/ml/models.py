from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func

from siteintel.extensions import db


class DocumentPrediction(db.Model):
    __tablename__ = "document_predictions"
    __table_args__ = (
        UniqueConstraint("document_id", "model_key", "input_hash", name="uq_prediction_inputs"),
        Index("ix_document_predictions_document_id", "document_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(
        db.Integer, ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_id = db.Column(
        db.Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    model_key = db.Column(String(32), nullable=False)
    input_hash = db.Column(String(64), nullable=False)
    status = db.Column(String(24), nullable=False)
    inputs = db.Column(db.JSON, nullable=False)
    missing = db.Column(db.JSON, nullable=False)
    result = db.Column(db.JSON)
    created_at = db.Column(
        DateTime(timezone=True), nullable=False, default=func.now(), server_default=func.now()
    )

    document = db.relationship("Document")


class PredictionHead(db.Model):
    __tablename__ = "prediction_heads"

    document_id = db.Column(db.Integer, ForeignKey("documents.id", ondelete="RESTRICT"), primary_key=True)
    model_key = db.Column(String(32), primary_key=True)
    prediction_id = db.Column(db.Integer, ForeignKey("document_predictions.id", ondelete="RESTRICT"), nullable=False)
    prediction = db.relationship("DocumentPrediction")
