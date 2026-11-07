from pathlib import Path
from typing import Any

import pytesseract
from PIL import Image, ImageOps
from pytesseract import Output


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
