from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytesseract
import pypdfium2 as pdfium
from PIL import Image, ImageOps
from pytesseract import Output


class PDFProcessingError(ValueError):
    pass


def extract_pdf_ocr(path: Path) -> str:
    try:
        with pdfium.PdfDocument(path) as document:
            if not 1 <= len(document) <= 20:
                raise PDFProcessingError("PDF reports must contain between 1 and 20 pages.")
            texts = []
            with TemporaryDirectory(prefix="siteintel-pdf-") as directory:
                for index in range(len(document)):
                    with closing(document[index]) as page:
                        width, height = page.get_size()
                        if width <= 0 or height <= 0:
                            raise PDFProcessingError("The PDF page dimensions are invalid.")
                        scale = min(150 / 72, 2000 / max(width, height))
                        with closing(page.render(scale=scale)) as bitmap:
                            page_path = Path(directory) / "page.png"
                            bitmap.to_pil().save(page_path)
                    texts.append(extract_ocr(page_path)[0])
            return "\n".join(texts)
    except pdfium.PdfiumError as error:
        raise PDFProcessingError("The PDF could not be processed.") from error


def extract_ocr(path: Path) -> tuple[str, list[dict[str, Any]]]:
    with Image.open(path) as source:
        image = ImageOps.grayscale(source)
        image = ImageOps.autocontrast(image)
        if image.width < 1600:
            scale = min(3, max(1, 1600 // max(image.width, 1)))
            if scale > 1:
                image = image.resize(
                    (image.width * scale, image.height * scale),
                    Image.Resampling.LANCZOS,
                )

        data = pytesseract.image_to_data(
            image,
            lang="eng",
            config="--oem 3 --psm 6",
            output_type=Output.DICT,
        )

    grouped: dict[tuple[int, int, int], dict[str, Any]] = {}
    for index, word in enumerate(data["text"]):
        text = str(word).strip()
        if not text:
            continue
        key = (
            int(data["block_num"][index]),
            int(data["par_num"][index]),
            int(data["line_num"][index]),
        )
        line = grouped.setdefault(
            key,
            {
                "block": key[0],
                "paragraph": key[1],
                "line": key[2],
                "top": int(data["top"][index]),
                "tokens": [],
            },
        )
        left = int(data["left"][index])
        width = int(data["width"][index])
        line["tokens"].append(
            {"text": text, "left": left, "center": left + width / 2}
        )

    lines = sorted(grouped.values(), key=lambda line: (line["top"], line["block"]))
    for line in lines:
        line["tokens"].sort(key=lambda token: token["left"])
        line["text"] = " ".join(token["text"] for token in line["tokens"])
    text = "\n".join(line["text"] for line in lines)
    return text, lines
