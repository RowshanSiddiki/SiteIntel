"""PDF text extraction that preserves page numbers.

Text-layer pages are read directly. Pages without a usable text layer
(scans) go through the existing Tesseract OCR in ``siteintel.reports.ocr``.
"""
import logging
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import pypdfium2 as pdfium
import pytesseract

from siteintel.documents import settings
from siteintel.reports.ocr import extract_ocr

logger = logging.getLogger(__name__)

# A page with fewer visible characters than this is treated as image-only.
MIN_TEXT_LAYER_CHARS = 20


class ExtractionError(ValueError):
    """Raised with a message that is safe to show to the user."""


@dataclass(frozen=True)
class PageText:
    number: int  # 1-based
    text: str
    ocr: bool = False


@dataclass(frozen=True)
class Extraction:
    pages: list[PageText]
    page_count: int


def _ocr_page(page) -> str:
    width, height = page.get_size()
    if width <= 0 or height <= 0:
        raise ExtractionError("The PDF contains a page with invalid dimensions.")
    scale = min(200 / 72, 2400 / max(width, height))
    with TemporaryDirectory(prefix="siteintel-doc-") as directory:
        image_path = Path(directory) / "page.png"
        with closing(page.render(scale=scale)) as bitmap:
            bitmap.to_pil().save(image_path)
        return extract_ocr(image_path)[0]


def extract_pdf_pages(path: Path) -> Extraction:
    try:
        document = pdfium.PdfDocument(path)
    except pdfium.PdfiumError as error:
        message = str(error).lower()
        if "password" in message:
            raise ExtractionError("The PDF is password-protected. Upload an unprotected copy.") from error
        raise ExtractionError("The PDF is corrupted or could not be opened.") from error

    with document:
        page_count = len(document)
        if page_count < 1:
            raise ExtractionError("The PDF has no pages.")
        if page_count > settings.max_pages():
            raise ExtractionError(f"The PDF has more than {settings.max_pages()} pages, which is the limit.")

        direct: dict[int, str] = {}
        for index in range(page_count):
            try:
                with closing(document[index]) as page, closing(page.get_textpage()) as textpage:
                    direct[index] = textpage.get_text_range() or ""
            except pdfium.PdfiumError as error:
                raise ExtractionError("The PDF is corrupted or could not be read.") from error

        scanned = [i for i, text in direct.items() if len(text.strip()) < MIN_TEXT_LAYER_CHARS]
        if len(scanned) > settings.max_ocr_pages():
            raise ExtractionError(
                f"{len(scanned)} pages are scanned images and need OCR, but OCR is limited "
                f"to {settings.max_ocr_pages()} pages per document."
            )

        pages: list[PageText] = []
        for index in range(page_count):
            if index in scanned:
                try:
                    with closing(document[index]) as page:
                        text = _ocr_page(page)
                except (pytesseract.TesseractNotFoundError, pytesseract.TesseractError) as error:
                    logger.exception("OCR failed for a scanned document page")
                    raise ExtractionError(
                        "This PDF contains scanned pages and OCR is not available on the server."
                    ) from error
                except (OSError, pdfium.PdfiumError) as error:
                    logger.exception("Could not render a scanned document page")
                    raise ExtractionError("A scanned page could not be processed.") from error
                pages.append(PageText(index + 1, text, ocr=True))
            else:
                pages.append(PageText(index + 1, direct[index]))
    return Extraction(pages, page_count)
