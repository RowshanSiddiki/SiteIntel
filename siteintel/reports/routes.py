import json
import logging
from functools import wraps
from typing import Any, Callable, TypeVar

import pytesseract
from flask import current_app, jsonify, request
from flask_login import current_user
from PIL import Image
from werkzeug.exceptions import RequestEntityTooLarge

from siteintel.reports import reports_bp
from siteintel.reports.extraction import (
    calculate_readiness,
    extract_report_fields,
    feature_groups,
)
from siteintel.reports.model_registry import MODEL_SPECS, ModelUnavailableError
from siteintel.reports.ocr import extract_ocr
from siteintel.reports.storage import (
    InvalidReportImage,
    load_report_record,
    save_report_image,
    save_report_record,
)

logger = logging.getLogger(__name__)
F = TypeVar("F", bound=Callable[..., Any])


def _error(code: str, message: str, status: int):
    return jsonify({"error": {"code": code, "message": message}}), status


def require_authenticated_api(view: F) -> F:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any):
        if not current_user.is_authenticated:
            return _error("authentication_required", "Please log in to continue.", 401)
        return view(*args, **kwargs)

    return wrapped  # type: ignore[return-value]


def _model_registry():
    return current_app.extensions["siteintel_model_registry"]


def _readiness(fields: dict[str, Any]) -> dict[str, dict[str, Any]]:
    registry = _model_registry()
    workforce = registry.get("workforce")
    supply = registry.get("supply_chain")
    additional_status = {
        model_key: (
            registry.get(model_key).usable,
            registry.get(model_key).issue,
        )
        for model_key in (
            "construction_estimate",
            "civil_risk",
            "cement_demand",
        )
    }
    readiness = calculate_readiness(
        fields,
        workforce_available=workforce.usable,
        workforce_issue=workforce.issue,
        supply_available=supply.usable,
        supply_issue=supply.issue,
        additional_model_status=additional_status,
    )
    for model_key, (stem, target) in MODEL_SPECS.items():
        artifact = registry.get(model_key)
        readiness[model_key].update(
            {
                "model_file": f"{stem}.pkl",
                "metadata_file": f"{stem}_metadata.pkl",
                "target": target,
                "model_loaded": artifact.model is not None,
                "model_status": (
                    "ready"
                    if artifact.usable
                    else (
                        "loaded_with_prediction_blocker"
                        if artifact.model is not None
                        else "load_failed"
                    )
                ),
            }
        )
    return readiness


def _report_response(report: dict[str, Any]):
    return jsonify(
        {
            "report_id": report["report_id"],
            "extracted_text": report["extracted_text"],
            "extracted_information": feature_groups(report["fields"]),
            "prepared_inputs": report["fields"],
            "readiness": report["readiness"],
        }
    )


@reports_bp.post("/extract")
@require_authenticated_api
def extract_report():
    upload = request.files.get("image")
    if upload is None:
        return _error("image_required", "Choose a report image to upload.", 400)

    try:
        report_id, image_path = save_report_image(upload, str(current_user.get_id()))
    except InvalidReportImage as error:
        return _error("invalid_image", str(error), 400)
    except OSError:
        logger.exception("Could not securely store an uploaded report image")
        return _error("storage_error", "The report image could not be stored.", 500)

    try:
        extracted_text, ocr_lines = extract_ocr(image_path)
    except (pytesseract.TesseractNotFoundError, pytesseract.TesseractError):
        logger.exception("Tesseract OCR failed for an uploaded report")
        image_path.unlink(missing_ok=True)
        return _error(
            "ocr_unavailable",
            "Local OCR could not process this image. Check the Tesseract installation and try again.",
            503,
        )
    except (OSError, Image.DecompressionBombError):
        logger.exception("Image processing failed for an uploaded report")
        image_path.unlink(missing_ok=True)
        return _error(
            "image_processing_failed",
            "The image could not be processed. Upload a clear JPG or PNG image.",
            422,
        )

    registry = _model_registry()
    workforce = registry.get("workforce")
    supply = registry.get("supply_chain")
    additional_models = {
        model_key: (
            registry.get(model_key).input_features,
            registry.get(model_key).numeric_features,
        )
        for model_key in (
            "construction_estimate",
            "civil_risk",
            "cement_demand",
        )
    }
    fields = extract_report_fields(
        extracted_text,
        workforce_features=workforce.input_features,
        workforce_numeric=workforce.numeric_features,
        supply_features=supply.input_features,
        supply_numeric=supply.numeric_features,
        ocr_lines=ocr_lines,
        additional_models=additional_models,
    )
    report = {
        "report_id": report_id,
        "user_id": str(current_user.get_id()),
        "image_filename": image_path.name,
        "extracted_text": extracted_text,
        "fields": fields,
        "readiness": _readiness(fields),
    }
    try:
        save_report_record(str(current_user.get_id()), report_id, report)
    except OSError:
        logger.exception("Could not store extracted report information")
        image_path.unlink(missing_ok=True)
        return _error("storage_error", "Extracted report data could not be stored.", 500)
    return _report_response(report), 201


@reports_bp.get("/<report_id>")
@require_authenticated_api
def get_report(report_id: str):
    try:
        report = load_report_record(str(current_user.get_id()), report_id)
    except (OSError, UnicodeError, json.JSONDecodeError):
        logger.exception("Could not read an extracted report")
        return _error("storage_error", "Extracted report data could not be read.", 500)
    if report is None:
        return _error("report_not_found", "Report not found.", 404)
    return _report_response(report)


def _predict(report_id: str, model_key: str, readiness_key: str):
    try:
        report = load_report_record(str(current_user.get_id()), report_id)
    except (OSError, UnicodeError, json.JSONDecodeError):
        logger.exception("Could not read an extracted report for prediction")
        return _error("storage_error", "Extracted report data could not be read.", 500)
    if report is None:
        return _error("report_not_found", "Report not found.", 404)

    status = report.get("readiness", {}).get(readiness_key)
    if not isinstance(status, dict):
        return _error(
            "report_reextract_required",
            "This report was extracted with an older schema. Upload it again to prepare inputs for this model.",
            409,
        )
    if not status["data_ready"]:
        return jsonify(
            {
                "error": {
                    "code": "insufficient_report_data",
                    "message": "The report is missing required information.",
                    "missing_fields": status["missing_fields"],
                    "data_issue": status.get("data_issue"),
                }
            }
        ), 422
    if not status["model_available"]:
        return _error(
            "model_unavailable",
            status["model_issue"] or "This model is not available for predictions.",
            409,
        )

    artifact = _model_registry().get(model_key)
    try:
        model_values = report["fields"][readiness_key]
        prediction = artifact.predict(model_values)
        probabilities = (
            artifact.predict_probabilities(model_values)
            if model_key == "civil_risk"
            else None
        )
    except ModelUnavailableError as error:
        return _error("model_unavailable", str(error), 409)
    except (ValueError, TypeError):
        logger.exception("Validated report input did not match prediction schema")
        return _error("invalid_model_input", "The report inputs are invalid.", 422)
    except Exception:
        logger.exception("Prediction failed for model %s", model_key)
        return _error("prediction_failed", "The model prediction failed.", 500)

    if hasattr(prediction, "item"):
        prediction = prediction.item()
    response = {
        "report_id": report_id,
        "model": model_key,
        "target": artifact.target,
        "prediction": prediction,
    }
    if probabilities is not None:
        response["class_probabilities"] = probabilities
    return jsonify(response)


@reports_bp.post("/<report_id>/predict/workforce")
@require_authenticated_api
def predict_workforce(report_id: str):
    return _predict(report_id, "workforce", "workforce")


@reports_bp.post("/<report_id>/predict/supply-chain")
@require_authenticated_api
def predict_supply_chain(report_id: str):
    return _predict(report_id, "supply_chain", "supply_chain")


@reports_bp.post("/<report_id>/predict/construction-estimate")
@require_authenticated_api
def predict_construction_estimate(report_id: str):
    return _predict(
        report_id,
        "construction_estimate",
        "construction_estimate",
    )


@reports_bp.post("/<report_id>/predict/civil-risk")
@require_authenticated_api
def predict_civil_risk(report_id: str):
    return _predict(report_id, "civil_risk", "civil_risk")


@reports_bp.post("/<report_id>/predict/cement-demand")
@require_authenticated_api
def predict_cement_demand(report_id: str):
    return _predict(report_id, "cement_demand", "cement_demand")


@reports_bp.errorhandler(RequestEntityTooLarge)
def handle_upload_too_large(exception: RequestEntityTooLarge):
    logger.info(
        "Report upload exceeded the configured size limit (%s).",
        type(exception).__name__,
    )
    return _error(
        "image_too_large",
        "The image exceeds the maximum upload size.",
        413,
    )
