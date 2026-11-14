import logging
from functools import wraps
from typing import Any, Callable, TypeVar

from flask import Blueprint, current_app, jsonify, request, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from siteintel.extensions import db
from siteintel.ml.document_predictions import MODELS, complete_prediction, create_prediction, serialize, set_current
from siteintel.ml.models import DocumentPrediction, PredictionHead
from siteintel.ml.model_registry import ModelArtifactError
from siteintel.ml.predictors import (
    PredictionInputError,
    predict_cement_demand,
    predict_concrete_strength,
    predict_delay_risk,
)
from siteintel.projects import ConstructionRecord, Document, Expense, Project, get_accessible_project
from siteintel.reports.models import ReportReview


ml_bp = Blueprint("ml", __name__, url_prefix="/api")
logger = logging.getLogger(__name__)
F = TypeVar("F", bound=Callable[..., Any])


def _error(code: str, message: str, status: int):
    return jsonify({"success": False, "error": {"code": code, "message": message}}), status


def require_authenticated_api(view: F) -> F:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any):
        if not current_user.is_authenticated:
            return _error("authentication_required", "Please log in to continue.", 401)
        return view(*args, **kwargs)

    return wrapped  # type: ignore[return-value]


def _request_values():
    if not request.is_json:
        return None, _error(
            "json_required",
            "Send prediction inputs as a JSON object.",
            415,
        )
    values = request.get_json(silent=True)
    if not isinstance(values, dict):
        return None, _error(
            "invalid_json",
            "Request body must be a valid JSON object.",
            400,
        )
    return values, None


def _predict(predictor: Callable[..., dict[str, Any]], *, threshold: float | None = None):
    values, error = _request_values()
    if error is not None:
        return error

    registry = current_app.extensions["siteintel_model_registry"]
    try:
        if threshold is None:
            prediction = predictor(values, registry=registry)
        else:
            prediction = predictor(
                values,
                threshold=threshold,
                registry=registry,
            )
    except PredictionInputError as error:
        return _error("invalid_input", str(error), 400)
    except (ModelArtifactError, OSError):
        logger.exception("Prediction model artifacts are unavailable")
        return _error(
            "model_unavailable",
            "The prediction model is unavailable. Please try again later.",
            503,
        )
    except Exception:
        logger.exception("Prediction failed")
        return _error(
            "prediction_failed",
            "The model could not produce a prediction for these inputs.",
            500,
        )
    return jsonify({"success": True, "prediction": prediction})


@ml_bp.post("/ml/cement-demand")
@require_authenticated_api
def cement_demand():
    return _predict(predict_cement_demand)


@ml_bp.post("/ml/concrete-strength")
@require_authenticated_api
def concrete_strength():
    return _predict(predict_concrete_strength)


@ml_bp.post("/ml/delay-risk")
@require_authenticated_api
def delay_risk():
    threshold = current_app.config["DELAY_RISK_THRESHOLD"]
    return _predict(predict_delay_risk, threshold=threshold)


@ml_bp.route("/projects", methods=["GET", "POST"])
@require_authenticated_api
def projects():
    user_id = int(current_user.get_id())
    if request.method == "POST":
        values, error = _request_values()
        if error is not None:
            return error
        if set(values) != {"name"} or not isinstance(values["name"], str):
            return _error("invalid_input", "Provide a project name.", 400)
        name = values["name"].strip()
        if not name or len(name) > 160:
            return _error("invalid_input", "Project name must be 1 to 160 characters.", 400)
        project = Project(owner_id=user_id, name=name)
        db.session.add(project)
        db.session.commit()
        return jsonify({"id": project.id, "name": project.name, "editable": True,
                        "workspace_url": url_for("dashboard.project_workspace", project_id=project.id)}), 201
    accessible = db.session.scalars(Project.accessible_to(user_id).order_by(Project.name, Project.id)).all()
    editable_ids = set(db.session.scalars(
        Project.accessible_to(user_id, "editor").with_only_columns(Project.id)
    ).all())
    return jsonify({"projects": [
        {"id": project.id, "name": project.name, "editable": project.id in editable_ids}
        for project in accessible
    ]})


def _current_per_model(records):
    """One prediction per model: the newest completed one, otherwise the newest
    incomplete one, so a model never shows a result and a missing-input form."""
    chosen = {}
    for record in records:  # newest first
        best = chosen.get(record.model_key)
        if best is None or (best.status != "complete" and record.status == "complete"):
            chosen[record.model_key] = record
    return [chosen[key] for key in reversed(list(MODELS)) if key in chosen]


@ml_bp.get("/projects/<int:project_id>/predictions")
@require_authenticated_api
def project_predictions(project_id):
    user_id = int(current_user.get_id())
    if get_accessible_project(user_id, project_id) is None:
        return _error("project_not_found", "Project not found.", 404)
    records = db.session.scalars(
        db.select(DocumentPrediction).join(PredictionHead, PredictionHead.prediction_id == DocumentPrediction.id).join(Document, Document.id == DocumentPrediction.document_id).where(
            Document.project_id == project_id,
            db.exists().where(ReportReview.document_id == Document.id),
        ).order_by(DocumentPrediction.id.desc())
    ).all()
    history = db.session.scalars(
        db.select(DocumentPrediction).join(Document).where(
            Document.project_id == project_id
        ).order_by(DocumentPrediction.id.desc())
    ).all()
    return jsonify({
        "predictions": [serialize(record) for record in records],
        "current": [serialize(record) for record in _current_per_model(records)],
        "history": [serialize(record) for record in history],
        "editable": get_accessible_project(user_id, project_id, "editor") is not None,
    })


@ml_bp.get("/projects/<int:project_id>")
@require_authenticated_api
def project_details(project_id):
    user_id = int(current_user.get_id())
    project = get_accessible_project(user_id, project_id)
    if project is None:
        return _error("project_not_found", "Project not found.", 404)
    documents = db.session.scalars(
        db.select(Document).where(Document.project_id == project_id).order_by(Document.id.desc())
    ).all()
    reviewed = set(db.session.scalars(
        db.select(ReportReview.document_id).join(Document)
        .where(Document.project_id == project_id)
    ).all())
    records = db.session.scalars(
        db.select(ConstructionRecord).where(ConstructionRecord.project_id == project_id)
        .order_by(ConstructionRecord.work_date.desc(), ConstructionRecord.id.desc())
    ).all()
    expenses = db.session.scalars(
        db.select(Expense).where(Expense.project_id == project_id)
        .order_by(Expense.expense_date.desc(), Expense.id.desc())
    ).all()
    reviews = db.session.scalars(
        db.select(ReportReview).join(Document).where(Document.project_id == project_id)
        .order_by(ReportReview.id.desc())
    ).all()
    predictions = db.session.scalars(
        db.select(DocumentPrediction).join(Document).where(Document.project_id == project_id)
        .order_by(DocumentPrediction.id.desc())
    ).all()
    first_review = {}
    later_reviews = []
    for item in sorted(reviews, key=lambda review: review.id):
        (later_reviews if item.document_id in first_review else []).append(item)
        first_review.setdefault(item.document_id, item)
    names = {document.id: document.file_name for document in documents}
    generated = {}
    for item in sorted(predictions, key=lambda prediction: prediction.id):
        if item.status == "complete":
            entry = generated.setdefault(item.document_id, {"time": item.created_at, "models": set()})
            entry["models"].add(item.model_key)
    events = [
        {"time": project.created_at, "type": "project_created", "report": None, "description": "Project created"},
        *[{"time": item.created_at, "type": "report_uploaded", "report": item.file_name,
           "description": "Report uploaded"} for item in documents],
        *[{"time": item.created_at, "type": "report_confirmed", "report": names[item.document_id],
           "description": "Report confirmed"} for item in first_review.values()],
        *[{"time": item.created_at, "type": "report_updated", "report": names[item.document_id],
           "description": "Report updated"} for item in later_reviews],
        *[{"time": entry["time"], "type": "predictions_generated", "report": names[document_id],
           "description": f"{len(entry['models'])} ML prediction{'s' if len(entry['models']) != 1 else ''} generated"}
          for document_id, entry in generated.items()],
        *[{"time": item.created_at, "type": "construction_record", "report": None,
           "description": f"Construction record: {item.title}"} for item in records],
        *[{"time": item.created_at, "type": "expense", "report": None,
           "description": f"Expense: {item.description}"} for item in expenses],
    ]
    # Several uploads of the same file on one day are indistinguishable to the user.
    history, seen = [], set()
    for event in sorted(events, key=lambda event: event["time"]):
        key = (event["type"], event["report"], event["description"], event["time"].date())
        if key not in seen:
            seen.add(key)
            history.append({**event, "time": event["time"].isoformat()})
    return jsonify({
        "id": project.id, "name": project.name, "description": project.description,
        "status": project.status, "created_at": project.created_at.isoformat(),
        "editable": get_accessible_project(user_id, project_id, "editor") is not None,
        "documents": [{
            "id": document.id, "name": document.file_name,
            "created_at": document.created_at.isoformat(), "reviewed": document.id in reviewed,
        } for document in documents],
        "records": [{
            "title": record.title, "date": record.work_date.isoformat(),
            "description": record.description, "type": record.record_type,
        } for record in records],
        "expenses": [{
            "description": expense.description, "date": expense.expense_date.isoformat(),
            "amount": str(expense.amount), "currency": expense.currency, "status": expense.status,
        } for expense in expenses],
        "history": history,
    })


@ml_bp.post("/predictions/<int:prediction_id>/complete")
@require_authenticated_api
def complete_document_prediction(prediction_id):
    record = db.session.get(DocumentPrediction, prediction_id)
    user_id = int(current_user.get_id())
    if record is None or get_accessible_project(user_id, record.document.project_id, "editor") is None:
        return _error("prediction_not_found", "Prediction not found or not editable.", 404)
    if record.status != "missing_information":
        return _error("invalid_status", "This prediction does not need additional information.", 400)
    values, error = _request_values()
    if error is not None:
        return error
    try:
        db.session.execute(db.select(Document.id).where(Document.id == record.document_id).with_for_update()).scalar_one()
        head = db.session.get(PredictionHead, (record.document_id, record.model_key))
        if head is None or head.prediction_id != record.id:
            return _error("prediction_conflict", "This prediction has changed. Refresh and try again.", 409)
        updated = complete_prediction(record, user_id, values)
        set_current(updated)
        result = serialize(updated)
        db.session.commit()
    except PredictionInputError as exc:
        return _error("invalid_input", str(exc), 400)
    except IntegrityError:
        db.session.rollback()
        logger.exception("Concurrent prediction update")
        return _error("prediction_conflict", "This prediction was updated. Refresh and try again.", 409)
    return jsonify({"prediction": result})


@ml_bp.post("/predictions/<int:prediction_id>/rerun")
@require_authenticated_api
def rerun_document_prediction(prediction_id):
    record = db.session.get(DocumentPrediction, prediction_id)
    user_id = int(current_user.get_id())
    if record is None or get_accessible_project(user_id, record.document.project_id, "editor") is None:
        return _error("prediction_not_found", "Prediction not found or not editable.", 404)
    if record.missing:
        return _error("invalid_status", "Complete the missing information first.", 400)
    db.session.execute(db.select(Document.id).where(Document.id == record.document_id).with_for_update()).scalar_one()
    head = db.session.get(PredictionHead, (record.document_id, record.model_key))
    if head is None or head.prediction_id != record.id:
        return _error("prediction_conflict", "This prediction has changed. Refresh and try again.", 409)
    updated = create_prediction(
        record.document, user_id, record.model_key, record.inputs, {}
    )
    set_current(updated)
    result = serialize(updated)
    db.session.commit()
    return jsonify({"prediction": result}), 201
