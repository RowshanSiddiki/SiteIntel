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
from siteintel.reports.extraction import extract_report_fields
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


def _report_response(report: dict[str, Any]):
    return jsonify(
        {
            "report_id": report["report_id"],
            "extracted_text": report["extracted_text"],
            "extracted_information": {"report": report["fields"]},
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
        extracted_text, _ocr_lines = extract_ocr(image_path)
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

    report = {
        "report_id": report_id,
        "user_id": str(current_user.get_id()),
        "image_filename": image_path.name,
        "extracted_text": extracted_text,
        "fields": extract_report_fields(extracted_text),
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
