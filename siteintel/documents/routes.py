"""HTTP API for project knowledge documents. Routes only validate, authorise
and delegate to the services in this package."""
import logging
import re

from flask import Blueprint, jsonify, request, send_file, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from werkzeug.exceptions import RequestEntityTooLarge

from siteintel.documents import settings, storage
from siteintel.documents.processing import process_document
from siteintel.documents.qa import answer_project_question, search_project_documents
from siteintel.extensions import db
from siteintel.projects import DOCUMENT_CATEGORIES, Document, DocumentChunk, get_accessible_project
from siteintel.reports.models import DailyConstructionReport

logger = logging.getLogger(__name__)

documents_bp = Blueprint(
    "documents", __name__, url_prefix="/api/projects/<int:project_id>/documents"
)

STATUS_LABELS = {
    "pending": "Pending", "processing": "Processing",
    "completed": "Completed", "failed": "Failed",
}
FILENAME_FALLBACK = re.compile(r"[^\w.\- ()]+")


def _error(code: str, message: str, status: int):
    return jsonify({"error": {"code": code, "message": message}}), status


@documents_bp.before_request
def _require_login():
    if not current_user.is_authenticated:
        return _error("authentication_required", "Please log in to continue.", 401)
    return None


def _project(project_id: int, permission: str = "viewer"):
    return get_accessible_project(int(current_user.get_id()), project_id, permission)


def _knowledge_document(project_id: int, document_id: int) -> Document | None:
    return db.session.scalar(
        db.select(Document).where(
            Document.id == document_id,
            Document.project_id == project_id,
            Document.category.is_not(None),
        )
    )


def _serialize(document: Document, project_id: int) -> dict:
    base = {"project_id": project_id, "document_id": document.id}
    return {
        "id": document.id,
        "file_name": document.file_name,
        "category": document.category,
        "category_label": DOCUMENT_CATEGORIES.get(document.category, document.category),
        "status": document.processing_status,
        "status_label": STATUS_LABELS.get(document.processing_status, "Pending"),
        "error": document.processing_error,
        "page_count": document.page_count,
        "byte_size": document.byte_size,
        "uploaded_at": document.created_at.isoformat() if document.created_at else None,
        "view_url": url_for("documents.view_file", **base),
        "download_url": url_for("documents.view_file", download=1, **base),
    }


@documents_bp.errorhandler(RequestEntityTooLarge)
def _too_large(_error_):
    limit = settings.max_upload_bytes() // (1024 * 1024)
    return _error("file_too_large", f"The file is larger than the {limit} MB limit.", 413)


@documents_bp.get("")
def list_documents(project_id):
    project = _project(project_id)
    if project is None:
        return _error("project_not_found", "Project not found.", 404)
    documents = db.session.scalars(
        db.select(Document)
        .where(Document.project_id == project_id, Document.category.is_not(None))
        .order_by(Document.created_at.desc(), Document.id.desc())
    ).all()
    return jsonify({
        "documents": [_serialize(d, project_id) for d in documents],
        "categories": DOCUMENT_CATEGORIES,
        "editable": _project(project_id, "editor") is not None,
        "max_upload_mb": settings.max_upload_bytes() // (1024 * 1024),
        "llm_configured": settings.llm_settings() is not None,
    })


@documents_bp.post("")
def upload_document(project_id):
    if _project(project_id, "editor") is None:
        return _error("project_not_found", "Project not found or not editable.", 404)
    request.max_content_length = settings.max_upload_bytes() + 1024 * 1024
    category = request.form.get("category", "").strip()
    if category not in DOCUMENT_CATEGORIES:
        return _error("invalid_category", "Choose a document category.", 400)
    upload = request.files.get("file")
    try:
        file_name = storage.validate_upload(upload)
        stored = storage.save_pdf(upload, project_id)
    except storage.InvalidDocument as error:
        return _error("invalid_document", str(error), 400)
    except OSError:
        logger.exception("Could not store an uploaded document")
        return _error("storage_error", "The document could not be stored.", 500)

    try:
        duplicate = db.session.scalar(
            db.select(Document).where(
                Document.project_id == project_id, Document.sha256 == stored.sha256,
                Document.category.is_not(None),
            )
        )
        if duplicate is not None:
            storage.delete_file(stored.storage_key, project_id)
            return _error(
                "duplicate_document",
                f"This file was already uploaded as “{duplicate.file_name}”.", 409,
            )
        document = Document(
            project_id=project_id, uploaded_by_id=int(current_user.get_id()),
            file_name=file_name, storage_key=stored.storage_key,
            content_type="application/pdf", byte_size=stored.byte_size,
            sha256=stored.sha256, category=category, processing_status="pending",
        )
        db.session.add(document)
        db.session.commit()
    except (SQLAlchemyError, IntegrityError):
        db.session.rollback()
        storage.delete_file(stored.storage_key, project_id)
        logger.exception("Could not save a document record")
        return _error("storage_error", "The document could not be saved.", 500)

    process_document(document.id)
    db.session.refresh(document)
    return jsonify(_serialize(document, project_id)), 201


@documents_bp.get("/<int:document_id>/file")
def view_file(project_id, document_id):
    if _project(project_id) is None:
        return _error("project_not_found", "Project not found.", 404)
    document = _knowledge_document(project_id, document_id)
    path = storage.resolve_path(document.storage_key, project_id) if document else None
    if document is None or path is None:
        return _error("document_not_found", "Document not found.", 404)
    download = request.args.get("download") == "1"
    safe_name = FILENAME_FALLBACK.sub("_", document.file_name) or "document.pdf"
    response = send_file(
        path, mimetype="application/pdf", as_attachment=download,
        download_name=safe_name, conditional=True,
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "private, no-store"
    return response


@documents_bp.post("/<int:document_id>/process")
def reprocess_document(project_id, document_id):
    if _project(project_id, "editor") is None:
        return _error("project_not_found", "Project not found or not editable.", 404)
    document = _knowledge_document(project_id, document_id)
    if document is None:
        return _error("document_not_found", "Document not found.", 404)
    if document.processing_status == "processing":
        return _error("already_processing", "This document is already being processed.", 409)
    process_document(document.id, force=request.args.get("force") == "1")
    db.session.refresh(document)
    return jsonify(_serialize(document, project_id))


@documents_bp.delete("/<int:document_id>")
def delete_document(project_id, document_id):
    if _project(project_id, "editor") is None:
        return _error("project_not_found", "Project not found or not editable.", 404)
    document = _knowledge_document(project_id, document_id)
    if document is None:
        return _error("document_not_found", "Document not found.", 404)
    if db.session.scalar(
        db.select(DailyConstructionReport.id).where(
            DailyConstructionReport.document_id == document.id
        )
    ) is not None:
        return _error(
            "generated_report",
            "Generated reports are retained in project report history and cannot be deleted from Document Assistant.",
            409,
        )
    storage_key = document.storage_key
    try:
        from siteintel.projects.models import FinancialObservation
        db.session.execute(db.delete(FinancialObservation).where(FinancialObservation.document_id == document.id))
        db.session.execute(db.delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
        db.session.delete(document)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Could not delete document %s", document_id)
        return _error("storage_error", "The document could not be deleted.", 500)
    try:
        storage.delete_file(storage_key, project_id)
    except OSError:
        logger.exception("Document record removed but its file could not be deleted")
    return jsonify({"success": True})


def _question_payload():
    payload = request.get_json(silent=True) if request.is_json else None
    if not isinstance(payload, dict):
        return None, _error("invalid_request", "Send the question as JSON.", 400)
    question = payload.get("question")
    if not isinstance(question, str):
        return None, _error("invalid_question", "Enter a question.", 400)
    question = " ".join(question.split())
    if len(question) < settings.MIN_QUESTION_CHARS:
        return None, _error("invalid_question", "Enter a longer question.", 400)
    if len(question) > settings.MAX_QUESTION_CHARS:
        return None, _error(
            "invalid_question", f"Questions are limited to {settings.MAX_QUESTION_CHARS} characters.", 400
        )
    ids = payload.get("document_ids")
    if ids is not None and (
        not isinstance(ids, list) or len(ids) > 100
        or any(isinstance(i, bool) or not isinstance(i, int) for i in ids)
    ):
        return None, _error("invalid_request", "document_ids must be a list of document ids.", 400)
    return (question, ids or None), None


def _with_links(project_id: int, citations: list[dict]) -> list[dict]:
    for citation in citations:
        base = {"project_id": project_id, "document_id": citation["document_id"]}
        citation["view_url"] = (
            url_for("documents.view_file", **base) + f"#page={citation['page_number']}"
        )
    return citations


@documents_bp.post("/ask")
def ask(project_id):
    if _project(project_id) is None:
        return _error("project_not_found", "Project not found.", 404)
    parsed, failure = _question_payload()
    if failure:
        return failure
    question, ids = parsed
    try:
        result = answer_project_question(project_id, question, document_ids=ids)
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Question answering failed")
        return _error("search_failed", "The documents could not be searched. Please try again.", 500)
    data = result.to_dict()
    _with_links(project_id, data["citations"])
    return jsonify(data)


@documents_bp.post("/search")
def search(project_id):
    if _project(project_id) is None:
        return _error("project_not_found", "Project not found.", 404)
    parsed, failure = _question_payload()
    if failure:
        return failure
    question, ids = parsed
    try:
        result = search_project_documents(project_id, question, document_ids=ids)
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Document search failed")
        return _error("search_failed", "The documents could not be searched. Please try again.", 500)
    passages = [{
        "number": number, "document_id": c.document_id, "document_name": c.document_name,
        "category": c.category, "category_label": DOCUMENT_CATEGORIES.get(c.category, c.category),
        "page_number": c.page_number, "excerpt": c.excerpt,
        "reporting_date": c.reporting_date,
        "relevance": round(c.coverage * 100),
    } for number, c in enumerate(result.chunks, start=1)]
    _with_links(project_id, passages)
    return jsonify({
        "question": question, "passages": passages if result.sufficient else [],
        "indexed_documents": result.indexed_documents,
        "status": ("no_documents" if result.indexed_documents == 0
                   else "found" if result.sufficient else "no_results"),
    })
