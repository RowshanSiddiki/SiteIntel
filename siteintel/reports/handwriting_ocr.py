"""Optional handwriting-capable OCR using Azure AI Document Intelligence Read.

When no endpoint and key are configured, uploaded documents continue through
the existing local Tesseract OCR pipeline and are explicitly marked as such.
"""
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from flask import current_app, has_app_context

logger = logging.getLogger(__name__)
API_VERSION = "2024-11-30"
LOW_CONFIDENCE_THRESHOLD = 0.75


class HandwritingOcrError(RuntimeError):
    """Raised when the configured cloud OCR provider cannot process a file."""


@dataclass(frozen=True)
class OcrLine:
    text: str
    confidence: float | None = None
    handwritten: bool = False


@dataclass(frozen=True)
class OcrResult:
    text: str
    provider: str
    handwriting_detected: bool = False
    lines: list[OcrLine] = field(default_factory=list)


def _setting(name: str) -> str:
    if has_app_context():
        if name in current_app.config:
            value = current_app.config.get(name)
            return str(value).strip()
    return os.getenv(name, "").strip()


def handwriting_ocr_configured() -> bool:
    endpoint = _setting("HANDWRITING_OCR_ENDPOINT")
    key = _setting("HANDWRITING_OCR_KEY")
    if bool(endpoint) != bool(key):
        raise HandwritingOcrError(
            "Handwriting OCR is misconfigured. Set both the HTTPS endpoint and key."
        )
    return bool(endpoint and key)


def _timeout() -> int:
    try:
        value = int(_setting("HANDWRITING_OCR_TIMEOUT_SECONDS") or "60")
    except ValueError as error:
        raise HandwritingOcrError(
            "Handwriting OCR timeout must be a whole number of seconds."
        ) from error
    return min(max(value, 10), 180)


def _ranges(spans) -> list[tuple[int, int]]:
    return [
        (int(span["offset"]), int(span["offset"]) + int(span["length"]))
        for span in spans or []
        if isinstance(span, dict) and "offset" in span and "length" in span
    ]


def _overlaps(first: list[tuple[int, int]], second: list[tuple[int, int]]) -> bool:
    return any(a < d and c < b for a, b in first for c, d in second)


def _parse_analyze_result(payload: dict) -> OcrResult:
    result = payload.get("analyzeResult")
    if not isinstance(result, dict):
        raise HandwritingOcrError("The handwriting OCR provider returned an invalid response.")
    content = result.get("content")
    pages = result.get("pages")
    if not isinstance(content, str) or not isinstance(pages, list):
        raise HandwritingOcrError("The handwriting OCR provider returned no readable content.")

    handwritten_spans = [
        _ranges(style.get("spans"))
        for style in result.get("styles", [])
        if isinstance(style, dict) and style.get("isHandwritten") is True
    ]
    words = [
        word for page in pages if isinstance(page, dict)
        for word in page.get("words", []) if isinstance(word, dict)
    ]
    lines = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        for line in page.get("lines", []):
            if not isinstance(line, dict) or not isinstance(line.get("content"), str):
                continue
            line_spans = _ranges(line.get("spans"))
            matching = [
                float(word["confidence"])
                for word in words
                if isinstance(word.get("confidence"), (int, float))
                and _overlaps(line_spans, _ranges(word.get("spans")))
            ]
            confidence = sum(matching) / len(matching) if matching else None
            is_handwritten = any(_overlaps(line_spans, span_set) for span_set in handwritten_spans)
            lines.append(OcrLine(line["content"], confidence, is_handwritten))
    return OcrResult(
        text=content,
        provider="azure_document_intelligence",
        handwriting_detected=any(line.handwritten for line in lines),
        lines=lines,
    )


def _request_json(request: Request, timeout: int) -> tuple[dict, dict]:
    try:
        with urlopen(request, timeout=timeout) as response:
            data = response.read(20 * 1024 * 1024 + 1)
            if len(data) > 20 * 1024 * 1024:
                raise HandwritingOcrError("The handwriting OCR response was too large.")
            return json.loads(data), dict(response.headers.items())
    except HandwritingOcrError:
        raise
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        logger.warning("Handwriting OCR request failed (%s).", type(error).__name__)
        raise HandwritingOcrError(
            "Handwriting recognition is temporarily unavailable. Try again or review the source manually."
        ) from error


def _same_service(operation_url: str, endpoint: str) -> bool:
    operation = urlparse(operation_url)
    service = urlparse(endpoint)
    return (
        operation.scheme == "https"
        and operation.hostname == service.hostname
        and operation.port == service.port
        and operation.username is None
        and operation.password is None
    )


def extract_with_azure(path: Path) -> OcrResult:
    endpoint = _setting("HANDWRITING_OCR_ENDPOINT").rstrip("/")
    key = _setting("HANDWRITING_OCR_KEY")
    parsed = urlparse(endpoint)
    if not endpoint or not key or parsed.scheme != "https" or not parsed.hostname:
        raise HandwritingOcrError(
            "Handwriting OCR is misconfigured. Set a valid HTTPS endpoint and key."
        )
    try:
        data = path.read_bytes()
    except OSError as error:
        raise HandwritingOcrError("The uploaded document could not be read.") from error
    if not data:
        raise HandwritingOcrError("The uploaded document is empty.")

    operation = (
        f"{endpoint}/documentintelligence/documentModels/prebuilt-read:analyze"
        f"?api-version={API_VERSION}"
    )
    request = Request(
        operation,
        data=data,
        headers={
            "Content-Type": "application/octet-stream",
            "Ocp-Apim-Subscription-Key": key,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=_timeout()) as response:
            operation_url = response.headers.get("Operation-Location")
            if response.status not in {200, 202} or not operation_url:
                raise HandwritingOcrError("The handwriting OCR provider did not accept the document.")
    except HandwritingOcrError:
        raise
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        logger.warning("Handwriting OCR submission failed (%s).", type(error).__name__)
        raise HandwritingOcrError(
            "Handwriting recognition is temporarily unavailable. Try again or review the source manually."
        ) from error
    if not _same_service(operation_url, endpoint):
        raise HandwritingOcrError("The handwriting OCR provider returned an invalid operation address.")

    deadline = time.monotonic() + _timeout()
    while time.monotonic() < deadline:
        payload, _ = _request_json(Request(
            operation_url,
            headers={"Ocp-Apim-Subscription-Key": key},
            method="GET",
        ), min(20, max(1, int(deadline - time.monotonic()))))
        status = payload.get("status")
        if status == "succeeded":
            return _parse_analyze_result(payload)
        if status == "failed":
            logger.warning("Handwriting OCR provider reported a processing failure.")
            raise HandwritingOcrError(
                "The document could not be read. Check the scan and upload it again."
            )
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    raise HandwritingOcrError(
        "Handwriting recognition timed out. Try again or review the source manually."
    )


def confidence_by_field(fields: dict[str, str], lines: list[OcrLine]) -> dict[str, float]:
    """Match extracted field rows back to OCR line confidences."""
    from siteintel.reports.extraction import extract_line_fields

    confidences = {}
    for label, value in fields.items():
        matches = []
        for line in lines:
            if line.confidence is None:
                continue
            parsed = extract_line_fields(line.text)
            if any(
                candidate.casefold() == label.casefold()
                and extracted.strip().casefold() == value.strip().casefold()
                for candidate, extracted in parsed.items()
            ):
                matches.append(line.confidence)
        if matches:
            confidences[label] = round(min(matches), 4)
    return confidences


def add_unstructured_notes(
    fields: dict[str, str], text: str, lines: list[OcrLine]
) -> dict[str, float]:
    """Keep OCR lines that are not label/value fields in the reviewed report."""
    from siteintel.reports.extraction import extract_line_fields

    notes = []
    for line in text.splitlines():
        value = line.strip()
        if value and not extract_line_fields(value):
            notes.append(value)
    if not notes:
        return {}

    chunks = []
    current = []
    length = 0
    for note in notes:
        if current and length + len(note) + 1 > 1800:
            chunks.append(current)
            current, length = [], 0
        current.append(note)
        length += len(note) + 1
    if current:
        chunks.append(current)

    confidences = {}
    line_confidence = {line.text.strip().casefold(): line.confidence for line in lines}
    for index, chunk in enumerate(chunks, 1):
        label = "Site notes" if index == 1 else f"Site notes {index}"
        fields[label] = "\n".join(chunk)
        values = [
            line_confidence[item.casefold()]
            for item in chunk
            if line_confidence.get(item.casefold()) is not None
        ]
        if values:
            confidences[label] = round(min(values), 4)
    return confidences


def low_confidence_fields(confidences: dict[str, float]) -> list[str]:
    return [
        label for label, confidence in confidences.items()
        if confidence < LOW_CONFIDENCE_THRESHOLD
    ]
