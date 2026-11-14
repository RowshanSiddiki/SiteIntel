import json
import hashlib
import logging
from functools import wraps
from pathlib import Path
from typing import Any, Callable, TypeVar

import pytesseract
from flask import jsonify, request, send_file
from flask_login import current_user
from PIL import Image
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename
from sqlalchemy.exc import SQLAlchemyError

from siteintel.auth.models import User
from siteintel.extensions import db
from siteintel.ml.document_predictions import generate_for_review
from siteintel.projects import Document, get_accessible_project
from siteintel.reports import reports_bp
from siteintel.reports.extraction import extract_report_fields
from siteintel.reports.models import ReportReview
from siteintel.reports.ocr import (
    PDFProcessingError, extract_ocr, extract_pdf_ocr,
)
from siteintel.reports.storage import (
    InvalidReportImage,
    load_report_record,
    save_report_image,
    save_report_pdf,
    save_report_record,
    report_file_path,
    REPORT_ID_PATTERN,
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
    response = {
            "report_id": report["report_id"],
            "extracted_text": report["extracted_text"],
            "extracted_information": {"report": report["fields"]},
        }
    if "project_id" in report:
        response.update(project_id=report["project_id"], document_id=report["document_id"])
    return jsonify(response)


@reports_bp.post("/extract")
@require_authenticated_api
def extract_report():
    upload = request.files.get("image")
    if upload is None:
        return _error("image_required", "Choose a report image to upload.", 400)

    project = None
    request_id = request.headers.get("Idempotency-Key")
    if request_id and not REPORT_ID_PATTERN.fullmatch(request_id):
        return _error("invalid_request_id", "Invalid upload identifier.", 400)
    project_id = request.form.get("project_id", "").strip()
    if project_id:
        if (
            not project_id.isascii() or not project_id.isdecimal()
            or len(project_id) > 10 or int(project_id) > 2147483647
        ):
            return _error("invalid_project", "Select a valid project.", 400)
        project = get_accessible_project(int(current_user.get_id()), int(project_id), "editor")
        if project is None:
            return _error("project_not_found", "Project not found or not editable.", 404)
    if request_id and project is not None:
        db.session.execute(db.select(User.id).where(User.id == current_user.id).with_for_update()).scalar_one()
        digest = hashlib.sha256()
        for chunk in iter(lambda: upload.stream.read(65536), b""):
            digest.update(chunk)
        upload.stream.seek(0)
        upload_hash = digest.hexdigest()
        existing = db.session.scalar(db.select(Document).where(
            Document.storage_key == f"{current_user.get_id()}/{request_id}.json",
        ))
        if existing:
            if existing.project_id != project.id or existing.sha256 != upload_hash:
                return _error("upload_conflict", "This upload identifier was already used for a different file.", 409)
            try:
                saved = _document_report(existing)
            except (OSError, UnicodeError, json.JSONDecodeError):
                logger.exception("Could not read an existing uploaded report")
                return _error("storage_error", "Report information could not be read.", 500)
            if saved is None:
                return _error("storage_error", "Report information could not be read.", 500)
            saved.update(project_id=project.id, document_id=existing.id)
            return _report_response(saved), 200
    else:
        upload_hash = None

    is_pdf = Path(upload.filename or "").suffix.lower() == ".pdf"
    try:
        save = save_report_pdf if is_pdf else save_report_image
        report_id, image_path = save(upload, str(current_user.get_id()), request_id if project is not None else None)
    except InvalidReportImage as error:
        return _error("invalid_image", str(error), 400)
    except OSError:
        logger.exception("Could not securely store an uploaded report image")
        return _error("storage_error", "The report image could not be stored.", 500)

    try:
        if is_pdf:
            extracted_text = extract_pdf_ocr(image_path)
        else:
            extracted_text = extract_ocr(image_path)[0]
    except PDFProcessingError:
        logger.exception("PDF processing failed")
        image_path.unlink(missing_ok=True)
        return _error("invalid_pdf", "The PDF could not be processed; use an unencrypted PDF of at most 20 pages.", 422)
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
    if project is not None:
        try:
            document = Document(
                project_id=project.id,
                uploaded_by_id=int(current_user.get_id()),
                file_name=(secure_filename(upload.filename or "") or "report")[:255],
                storage_key=f"{current_user.get_id()}/{report_id}.json",
                content_type="application/pdf" if is_pdf else
                    ("image/jpeg" if image_path.suffix == ".jpg" else "image/png"),
                byte_size=image_path.stat().st_size,
                sha256=upload_hash,
            )
            db.session.add(document)
            db.session.flush()
            db.session.commit()
        except (SQLAlchemyError, OSError):
            db.session.rollback()
            logger.exception("Could not save project report and predictions")
            image_path.unlink(missing_ok=True)
            image_path.with_suffix(".json").unlink(missing_ok=True)
            return _error("storage_error", "The project report could not be saved.", 500)
        report["project_id"] = project.id
        report["document_id"] = document.id
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


def _project_document(document_id, permission="viewer"):
    document = db.session.get(Document, document_id)
    if document is None or get_accessible_project(
        int(current_user.get_id()), document.project_id, permission
    ) is None:
        return None
    return document


def _document_report(document):
    owner, separator, filename = document.storage_key.partition("/")
    if (
        not separator or owner != str(document.uploaded_by_id)
        or not filename.endswith(".json") or "/" in filename
    ):
        return None
    report = load_report_record(owner, filename[:-5])
    if report is not None:
        # Reports stored before model-feature scanning lack those fields; the
        # extraction is deterministic, so recompute from the saved OCR text.
        report["fields"] = {**report["fields"], **extract_report_fields(report["extracted_text"])}
    return report


@reports_bp.get("/documents/<int:document_id>")
@require_authenticated_api
def project_report(document_id):
    document = _project_document(document_id)
    if document is None:
        return _error("report_not_found", "Report not found.", 404)
    try:
        report = _document_report(document)
    except (OSError, UnicodeError, json.JSONDecodeError):
        logger.exception("Could not read project report")
        return _error("storage_error", "Report information could not be read.", 500)
    if report is None:
        return _error("report_not_found", "Report information not found.", 404)
    review = db.session.scalar(
        db.select(ReportReview).where(ReportReview.document_id == document.id)
        .order_by(ReportReview.id.desc()).limit(1)
    )
    return jsonify({
        "document_id": document.id,
        "file_name": document.file_name,
        "content_type": document.content_type,
        "extracted_text": report["extracted_text"],
        "fields": {**report["fields"], **review.fields} if review else report["fields"],
        "reviewed": review is not None,
        "review_id": review.id if review else None,
        "editable": _project_document(document_id, "editor") is not None,
    })


@reports_bp.get("/documents/<int:document_id>/file")
@require_authenticated_api
def project_report_file(document_id):
    document = _project_document(document_id)
    if document is None:
        return _error("report_not_found", "Report not found.", 404)
    try:
        report = _document_report(document)
        path = report_file_path(
            str(document.uploaded_by_id), report["report_id"], report["image_filename"]
        ) if report else None
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError):
        logger.exception("Could not read project report file")
        return _error("storage_error", "Report file could not be read.", 500)
    if path is None:
        return _error("report_not_found", "Report file not found.", 404)
    response = send_file(path, mimetype=document.content_type, conditional=True)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "private, no-store"
    return response


@reports_bp.post("/documents/<int:document_id>/confirm")
@require_authenticated_api
def confirm_project_report(document_id):
    document = _project_document(document_id, "editor")
    if document is None:
        return _error("report_not_found", "Report not found or not editable.", 404)
    payload = request.get_json(silent=True) if request.is_json else None
    fields = payload.get("fields") if isinstance(payload, dict) else None
    revision = payload.get("review_id") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or "review_id" not in payload or (
        revision is not None and (isinstance(revision, bool) or not isinstance(revision, int))
    ):
        return _error("invalid_fields", "Reload the report before confirming.", 400)
    if not isinstance(fields, dict) or len(fields) > 500:
        return _error("invalid_fields", "Provide the report fields as an object.", 400)
    if any(not isinstance(key, str) or not isinstance(value, str) or len(value) > 2000
           or "\n" in value or "\r" in value for key, value in fields.items()):
        return _error("invalid_fields", "Each field must be a single line of at most 2,000 characters.", 400)
    try:
        report = _document_report(document)
    except (OSError, UnicodeError, json.JSONDecodeError):
        logger.exception("Could not read project report for review")
        return _error("storage_error", "Report information could not be read.", 500)
    if report is None:
        return _error("report_not_found", "Report information not found.", 404)
    original = report["fields"]
    if set(fields) != set(original):
        return _error("invalid_fields", "Only the extracted fields can be reviewed.", 400)
    db.session.execute(
        db.select(Document.id).where(Document.id == document.id).with_for_update()
    ).scalar_one()
    review = db.session.scalar(
        db.select(ReportReview).where(ReportReview.document_id == document.id)
        .order_by(ReportReview.id.desc()).limit(1).with_for_update()
    )
    if revision != (review.id if review else None):
        return _error("review_conflict", "This report was reviewed elsewhere. Reload it before confirming.", 409)
    previous = {**original, **review.fields} if review else original
    if review is None or fields != previous:
        try:
            review = ReportReview(document_id=document.id, reviewed_by_id=int(current_user.get_id()), fields=fields)
            db.session.add(review)
            generate_for_review(document, int(current_user.get_id()), report["extracted_text"], original, fields, previous)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Could not save report review")
            return _error("storage_error", "Report information could not be saved.", 500)
    else:
        try:
            generate_for_review(document, int(current_user.get_id()), report["extracted_text"], original, fields, previous)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Could not update report predictions")
            return _error("storage_error", "Report predictions could not be saved.", 500)
    return jsonify({"success": True, "document_id": document.id, "review_id": review.id})


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
