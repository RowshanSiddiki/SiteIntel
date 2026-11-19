import json
import hashlib
import logging
import re
from datetime import date
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
from siteintel.documents import storage as knowledge_storage
from siteintel.documents.processing import process_document
from siteintel.extensions import db
from siteintel.ml.document_predictions import generate_for_document, generate_for_review
from siteintel.ml.models import DocumentPrediction
from siteintel.projects import Document, get_accessible_project
from siteintel.reports import reports_bp
from siteintel.reports import builder as report_builder
from siteintel.reports.extraction import extract_report_fields
from siteintel.reports.models import DailyConstructionReport, ReportReview
from siteintel.reports.ocr import PDFProcessingError
from siteintel.reports.handwriting_ocr import (
    HandwritingOcrError,
    add_unstructured_notes,
    OcrResult,
    confidence_by_field,
    extract_with_azure,
    handwriting_ocr_configured,
    low_confidence_fields,
)
from siteintel.reports.ocr import extract_ocr, extract_pdf_ocr
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


def _extract_uploaded_document(path: Path, *, is_pdf: bool) -> OcrResult:
    if handwriting_ocr_configured():
        return extract_with_azure(path)
    if is_pdf:
        text = extract_pdf_ocr(path)
    else:
        text = extract_ocr(path)[0]
    return OcrResult(
        text=text,
        provider="local_tesseract",
        lines=[],
    )


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
            "ocr": report.get("ocr", {}),
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
    reporting_date = None
    requested_date = request.form.get("reporting_date", "").strip()
    if requested_date:
        try:
            reporting_date = date.fromisoformat(requested_date)
        except ValueError:
            return _error("invalid_reporting_date", "Choose a valid reporting date.", 400)
        if reporting_date.isoformat() != requested_date:
            return _error("invalid_reporting_date", "Choose a valid reporting date.", 400)
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
        ocr_result = _extract_uploaded_document(image_path, is_pdf=is_pdf)
    except PDFProcessingError:
        logger.exception("PDF processing failed")
        image_path.unlink(missing_ok=True)
        return _error("invalid_pdf", "The PDF could not be processed; use an unencrypted PDF of at most 20 pages.", 422)
    except HandwritingOcrError:
        logger.exception("Configured handwriting OCR could not process an uploaded report")
        image_path.unlink(missing_ok=True)
        return _error(
            "handwriting_ocr_unavailable",
            "Handwriting recognition could not process this document. Try again or review the source manually.",
            503,
        )
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

    extracted_text = ocr_result.text
    fields = extract_report_fields(extracted_text)
    field_confidences = confidence_by_field(fields, ocr_result.lines)
    field_confidences.update(add_unstructured_notes(fields, extracted_text, ocr_result.lines))
    report = {
        "report_id": report_id,
        "user_id": str(current_user.get_id()),
        "image_filename": image_path.name,
        "extracted_text": extracted_text,
        "fields": fields,
        "ocr": {
            "provider": ocr_result.provider,
            "handwriting_detected": ocr_result.handwriting_detected,
            "field_confidence": field_confidences,
            "low_confidence_fields": low_confidence_fields(field_confidences),
        },
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
                reporting_date=reporting_date,
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
    if document is None or document.category is not None or get_accessible_project(
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
        "ocr": report.get("ocr", {}),
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
    if any(
        not isinstance(key, str) or not isinstance(value, str)
        or len(value) > (4000 if key.startswith("Site notes") else 2000)
        or (("\n" in value or "\r" in value) and not key.startswith("Site notes"))
        for key, value in fields.items()
    ):
        return _error("invalid_fields", "Each field must be valid report text within its size limit.", 400)
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


def _builder_project(project_id: int, permission: str = "viewer"):
    return get_accessible_project(int(current_user.get_id()), project_id, permission)


def _parse_reporting_date(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _report_url_values(project_id: int, report: DailyConstructionReport):
    return {
        "id": report.id,
        "reporting_date": report.reporting_date.isoformat(),
        "project_name": report.generated_document.project.name,
        "document_id": report.document_id,
        "document_name": report.generated_document.file_name,
        "source_count": len(report.source_documents),
        "processing_status": report.generated_document.processing_status,
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "url": f"/dashboard/projects/{project_id}/report-builder/{report.id}/",
        "download_url": f"/api/reports/projects/{project_id}/builder/reports/{report.id}/file",
    }


@reports_bp.get("/projects/<int:project_id>/builder/intake")
@require_authenticated_api
def report_builder_intake(project_id: int):
    project = _builder_project(project_id)
    if project is None:
        return _error("project_not_found", "Project not found.", 404)
    requested_date = request.args.get("date", "")
    reporting_date = _parse_reporting_date(requested_date)
    if reporting_date is None:
        return _error("invalid_reporting_date", "Choose a valid reporting date.", 400)

    documents = db.session.scalars(
        db.select(Document)
        .where(
            Document.project_id == project_id,
            Document.category.is_(None),
            Document.reporting_date == reporting_date,
        )
        .order_by(Document.created_at, Document.id)
    ).all()
    reviews = db.session.scalars(
        db.select(ReportReview)
        .where(ReportReview.document_id.in_([item.id for item in documents]))
        .order_by(ReportReview.id)
    ).all() if documents else []
    latest_reviews = {review.document_id: review for review in reviews}

    result = []
    try:
        for document in documents:
            report = _document_report(document)
            if report is None:
                continue
            review = latest_reviews.get(document.id)
            result.append({
                "id": document.id,
                "file_name": document.file_name,
                "content_type": document.content_type,
                "uploaded_at": document.created_at.isoformat() if document.created_at else None,
                "fields": {**report["fields"], **(review.fields if review else {})},
                "ocr": report.get("ocr", {}),
                "reviewed": review is not None,
                "review_id": review.id if review else None,
                "view_url": f"/api/reports/documents/{document.id}/file",
            })
    except (OSError, UnicodeError, json.JSONDecodeError):
        logger.exception("Could not read daily report intake")
        return _error("storage_error", "Uploaded report information could not be read.", 500)

    return jsonify({
        "project_id": project_id,
        "reporting_date": reporting_date.isoformat(),
        "editable": _builder_project(project_id, "editor") is not None,
        "documents": result,
    })


@reports_bp.get("/projects/<int:project_id>/builder/reports")
@require_authenticated_api
def report_builder_history(project_id: int):
    if _builder_project(project_id) is None:
        return _error("project_not_found", "Project not found.", 404)
    reports = db.session.scalars(
        db.select(DailyConstructionReport)
        .where(DailyConstructionReport.project_id == project_id)
        .order_by(DailyConstructionReport.reporting_date.desc())
    ).all()
    return jsonify({
        "reports": [_report_url_values(project_id, report) for report in reports],
    })


@reports_bp.post("/projects/<int:project_id>/builder/generate")
@require_authenticated_api
def generate_daily_report(project_id: int):
    project = _builder_project(project_id, "editor")
    if project is None:
        return _error("project_not_found", "Project not found or not editable.", 404)
    payload = request.get_json(silent=True) if request.is_json else None
    reporting_date = _parse_reporting_date(payload.get("reporting_date")) if isinstance(payload, dict) else None
    if reporting_date is None:
        return _error("invalid_reporting_date", "Choose a valid reporting date.", 400)

    source_documents = db.session.scalars(
        db.select(Document)
        .where(
            Document.project_id == project_id,
            Document.category.is_(None),
            Document.reporting_date == reporting_date,
        )
        .order_by(Document.created_at, Document.id)
    ).all()
    if not source_documents:
        return _error("no_source_documents", "Upload source documents for this date first.", 409)

    reviews = db.session.scalars(
        db.select(ReportReview)
        .where(ReportReview.document_id.in_([item.id for item in source_documents]))
        .order_by(ReportReview.id)
    ).all()
    reviewed = {review.document_id: review for review in reviews}
    missing_reviews = [
        item.file_name for item in source_documents if item.id not in reviewed
    ]
    if missing_reviews:
        return jsonify({
            "error": {
                "code": "review_required",
                "message": "Review each uploaded document before generating the report.",
                "documents": missing_reviews,
            }
        }), 409

    try:
        fields, report_text = report_builder.assemble_report(
            project,
            reporting_date,
            [
                (document, reviewed[document.id].fields)
                for document in source_documents
            ],
        )
        existing = db.session.scalar(
            db.select(DailyConstructionReport)
            .where(
                DailyConstructionReport.project_id == project_id,
                DailyConstructionReport.reporting_date == reporting_date,
            )
            .with_for_update()
        )
        unchanged_report = existing and existing.report_text == report_text and {
            item.id for item in existing.source_documents
        } == {item.id for item in source_documents}
        if unchanged_report:
            index_status = process_document(existing.document_id)
            report = existing
            should_generate_predictions = db.session.scalar(
                db.select(DocumentPrediction.id)
                .where(DocumentPrediction.document_id == report.document_id)
                .limit(1)
            ) is None
        else:
            should_generate_predictions = True
            pdf_data = report_builder.make_report_pdf(report_text)
            stored = knowledge_storage.save_pdf_bytes(pdf_data, project_id)
            old_storage_key = None
            if existing is None:
                generated_document = Document(
                    project_id=project_id,
                    uploaded_by_id=int(current_user.get_id()),
                    file_name=f"Daily Construction Report - {reporting_date.isoformat()}.pdf",
                    storage_key=stored.storage_key,
                    content_type="application/pdf",
                    byte_size=stored.byte_size,
                    sha256=stored.sha256,
                    category="other",
                    processing_status="pending",
                    reporting_date=reporting_date,
                )
                db.session.add(generated_document)
                db.session.flush()
                report = DailyConstructionReport(
                    project_id=project_id,
                    reporting_date=reporting_date,
                    created_by_id=int(current_user.get_id()),
                    document_id=generated_document.id,
                    fields=fields,
                    report_text=report_text,
                    source_documents=source_documents,
                )
                db.session.add(report)
            else:
                report = existing
                generated_document = report.generated_document
                old_storage_key = generated_document.storage_key
                generated_document.storage_key = stored.storage_key
                generated_document.file_name = f"Daily Construction Report - {reporting_date.isoformat()}.pdf"
                generated_document.byte_size = stored.byte_size
                generated_document.sha256 = stored.sha256
                generated_document.processing_status = "pending"
                generated_document.processing_error = None
                generated_document.processed_at = None
                report.fields = fields
                report.report_text = report_text
                report.source_documents = source_documents

            extracted_fields = extract_report_fields(report_text)
            review = db.session.scalar(
                db.select(ReportReview)
                .where(ReportReview.document_id == report.document_id)
                .order_by(ReportReview.id.desc())
                .limit(1)
            )
            if review is None:
                db.session.add(ReportReview(
                    document_id=report.document_id,
                    reviewed_by_id=int(current_user.get_id()),
                    fields=extracted_fields,
                ))
            else:
                review.reviewed_by_id = int(current_user.get_id())
                review.fields = extracted_fields
            db.session.commit()
            if old_storage_key:
                knowledge_storage.delete_file(old_storage_key, project_id)
            index_status = process_document(report.document_id, force=True)

        prediction_status = "completed"
        try:
            generated_document = report.generated_document
            generated_review = db.session.scalar(
                db.select(ReportReview)
                .where(ReportReview.document_id == generated_document.id)
                .order_by(ReportReview.id.desc())
                .limit(1)
            )
            if should_generate_predictions and generated_review is not None:
                predictions = generate_for_review(
                    generated_document,
                    int(current_user.get_id()),
                    report.report_text,
                    generated_review.fields,
                    generated_review.fields,
                    generated_review.fields,
                )
                from siteintel.ml.document_predictions import set_current

                for prediction in predictions:
                    set_current(prediction)
                db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Could not create ML predictions for daily report %s", report.id)
            prediction_status = "failed"
        except Exception:
            db.session.rollback()
            logger.exception("Unexpected error while creating predictions for daily report %s", report.id)
            prediction_status = "failed"

    except (knowledge_storage.InvalidDocument, OSError):
        db.session.rollback()
        logger.exception("Could not create daily construction report")
        return _error("report_generation_failed", "The report could not be generated. Try again.", 500)
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Could not save daily construction report")
        return _error("report_storage_failed", "The report could not be saved. Try again.", 500)
    except Exception:
        db.session.rollback()
        logger.exception("Unexpected error while generating daily construction report")
        return _error("report_generation_failed", "The report could not be generated. Try again.", 500)

    report_url = f"/dashboard/projects/{project_id}/report-builder/{report.id}/"
    return jsonify({
        "report": _report_url_values(project_id, report),
        "url": report_url,
        "download_url": f"/api/reports/projects/{project_id}/builder/reports/{report.id}/file",
        "predictions_url": f"/dashboard/projects/{project_id}/ml-predictions/",
        "assistant_url": f"/dashboard/projects/{project_id}/document-assistant/?q="
                         "What work was completed in this daily construction report?",
        "index_status": index_status,
        "prediction_status": prediction_status,
    }), 201


def _daily_report_or_404(project_id: int, report_id: int):
    if _builder_project(project_id) is None:
        return None
    return db.session.scalar(
        db.select(DailyConstructionReport).where(
            DailyConstructionReport.project_id == project_id,
            DailyConstructionReport.id == report_id,
        )
    )


@reports_bp.get("/projects/<int:project_id>/builder/reports/<int:report_id>")
@require_authenticated_api
def get_daily_report(project_id: int, report_id: int):
    report = _daily_report_or_404(project_id, report_id)
    if report is None:
        return _error("report_not_found", "Report not found.", 404)
    return jsonify({
        **_report_url_values(project_id, report),
        "fields": report.fields,
        "report_text": report.report_text,
        "sources": [
            {"id": source.id, "file_name": source.file_name}
            for source in report.source_documents
        ],
    })


@reports_bp.get("/projects/<int:project_id>/builder/reports/<int:report_id>/file")
@require_authenticated_api
def download_daily_report(project_id: int, report_id: int):
    report = _daily_report_or_404(project_id, report_id)
    path = (
        knowledge_storage.resolve_path(report.generated_document.storage_key, project_id)
        if report else None
    )
    if report is None or path is None:
        return _error("report_not_found", "Report not found.", 404)
    response = send_file(
        path,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=report.generated_document.file_name,
        conditional=True,
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "private, no-store"
    return response
