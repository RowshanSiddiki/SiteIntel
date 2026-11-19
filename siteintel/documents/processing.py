"""Document processing pipeline: extract -> clean -> chunk -> embed -> store."""
import logging
from datetime import datetime, timezone

from sqlalchemy.exc import SQLAlchemyError

from siteintel.documents import storage
from siteintel.documents.chunking import chunk_page, clean_text, strip_repeated_lines
from siteintel.documents.embedding import EMBEDDING_MODEL, embed
from siteintel.documents.extraction import ExtractionError, extract_pdf_pages
from siteintel.extensions import db
from siteintel.projects.models import Document, DocumentChunk

logger = logging.getLogger(__name__)

NO_TEXT_MESSAGE = "No readable text was found in this PDF."


def _claim(document_id: int, allowed: tuple[str, ...]) -> bool:
    """Atomically move a document to 'processing'; False if it is not eligible."""
    claimed = db.session.execute(
        db.update(Document)
        .where(
            Document.id == document_id,
            Document.category.is_not(None),
            Document.processing_status.in_(allowed),
        )
        .values(processing_status="processing", processing_error=None)
    ).rowcount
    db.session.commit()
    return bool(claimed)


def _fail(document_id: int, message: str) -> None:
    db.session.rollback()
    db.session.execute(
        db.update(Document).where(Document.id == document_id).values(
            processing_status="failed", processing_error=message[:500], processed_at=None
        )
    )
    db.session.commit()


def process_document(document_id: int, *, force: bool = False) -> str:
    """Index one knowledge document and return its final status.

    Completed documents are skipped unless ``force`` is set, so repeated calls
    never re-embed unchanged content.
    """
    allowed = ("pending", "failed", "completed") if force else ("pending", "failed")
    if not _claim(document_id, allowed):
        document = db.session.get(Document, document_id)
        return document.processing_status if document else "failed"

    document = db.session.get(Document, document_id)
    try:
        path = storage.resolve_path(document.storage_key, document.project_id)
        if path is None:
            raise ExtractionError("The stored file could not be found.")
        extraction = extract_pdf_pages(path)
        cleaned = [clean_text(page.text) for page in extraction.pages]
        from siteintel.reports.models import DailyConstructionReport

        is_daily_report = db.session.scalar(
            db.select(DailyConstructionReport.id).where(
                DailyConstructionReport.document_id == document_id,
                DailyConstructionReport.project_id == document.project_id,
            )
        ) is not None
        if not is_daily_report:
            cleaned = strip_repeated_lines(cleaned)
        rows = []
        for page, text in zip(extraction.pages, cleaned):
            for piece in chunk_page(text):
                vector, length = embed(piece)
                if length == 0:
                    continue
                rows.append((page.number, piece, vector, length))
        if not rows:
            raise ExtractionError(NO_TEXT_MESSAGE)

        db.session.execute(db.delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
        db.session.add_all(
            DocumentChunk(
                document_id=document_id, project_id=document.project_id, chunk_index=index,
                page_number=number, text=text, token_count=length, embedding=vector,
                embedding_model=EMBEDDING_MODEL,
            )
            for index, (number, text, vector, length) in enumerate(rows)
        )
        db.session.execute(
            db.update(Document).where(Document.id == document_id).values(
                processing_status="completed", processing_error=None,
                page_count=extraction.page_count, processed_at=datetime.now(timezone.utc),
            )
        )
        db.session.commit()
        return "completed"
    except ExtractionError as error:
        logger.info("Document %s could not be processed: %s", document_id, error)
        _fail(document_id, str(error))
    except (SQLAlchemyError, OSError):
        logger.exception("Processing document %s failed", document_id)
        try:
            _fail(document_id, "The document could not be processed. Please try again.")
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Could not record the failure for document %s", document_id)
    except Exception:
        logger.exception("Unexpected error while processing document %s", document_id)
        try:
            _fail(document_id, "The document could not be processed.")
        except SQLAlchemyError:
            db.session.rollback()
    return "failed"
