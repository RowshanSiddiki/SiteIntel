"""Deterministic daily-report assembly and lightweight PDF rendering."""
import re
import textwrap
import unicodedata
from datetime import date
from typing import Iterable

from siteintel.projects.models import Document, Project


def _section(label: str) -> str:
    key = label.casefold()
    if any(word in key for word in ("work", "activity", "progress", "completed", "task")):
        return "Work completed"
    if any(word in key for word in ("material", "cement", "concrete", "steel", "quantity", "delivery", "received")):
        return "Materials and quantities"
    if any(word in key for word in ("worker", "workforce", "labor", "labour", "crew")):
        return "Workforce"
    if any(word in key for word in ("equipment", "machine", "machinery")):
        return "Equipment"
    if any(word in key for word in ("weather", "temperature", "rain", "site condition")):
        return "Weather and site conditions"
    if any(word in key for word in ("delay", "issue", "problem", "observation", "risk")):
        return "Delays and observations"
    return "Other extracted information"


def assemble_report(
    project: Project,
    reporting_date: date,
    sources: Iterable[tuple[Document, dict[str, str]]],
) -> tuple[dict, str]:
    grouped: dict[str, list[dict]] = {}
    source_rows = []
    seen = set()
    for document, fields in sources:
        source_rows.append({"document_id": document.id, "file_name": document.file_name})
        for label, value in fields.items():
            item = (label.strip(), value.strip(), document.id)
            if not item[0] or not item[1] or item in seen:
                continue
            seen.add(item)
            grouped.setdefault(_section(item[0]), []).append({
                "label": item[0], "value": item[1], "source_document_id": document.id,
                "source_name": document.file_name,
            })

    fields = {
        "sections": [
            {"name": name, "items": items}
            for name, items in grouped.items()
        ],
        "sources": source_rows,
    }
    lines = [
        "DAILY CONSTRUCTION REPORT",
        f"Project: {project.name}",
        *( [f"Location: {project.location}"] if project.location else [] ),
        f"Reporting date: {reporting_date.isoformat()}",
    ]
    for section in fields["sections"]:
        lines.extend(["", section["name"].upper()])
        for item in section["items"]:
            lines.append(f"{item['label']}: {item['value']}")
            lines.append(f"Source: {item['source_name']}")
    lines.extend(["", "SOURCE DOCUMENTS"])
    lines.extend(f"- {source['file_name']}" for source in source_rows)
    report_text = "\n".join(lines)
    return fields, report_text


def _pdf_text(value: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", value).encode("ascii", "replace").decode("ascii")
    return ascii_text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_report_pdf(report_text: str) -> bytes:
    """Render text to a dependency-free, multi-page PDF using built-in Helvetica."""
    lines = []
    for paragraph in report_text.splitlines():
        if not paragraph:
            lines.append("")
        else:
            lines.extend(textwrap.wrap(paragraph, width=96, break_long_words=True, break_on_hyphens=False))
    pages = [lines[index:index + 48] for index in range(0, max(1, len(lines)), 48)]

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    page_ids = []
    for page_lines in pages:
        content = ["BT /F1 10 Tf 48 744 Td 14 TL"]
        for line in page_lines:
            content.append(f"({_pdf_text(line)}) Tj T*")
        content.append("ET")
        stream = "\n".join(content).encode("ascii")
        page_id = len(objects) + 1
        content_id = page_id + 1
        page_ids.append(page_id)
        objects.extend([
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
            ).encode("ascii"),
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"\nendstream",
        ])
    objects[1] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{page_id} 0 R' for page_id in page_ids)}] "
        f"/Count {len(page_ids)} >>"
    ).encode("ascii")

    output = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF".encode("ascii")
    )
    return bytes(output)
