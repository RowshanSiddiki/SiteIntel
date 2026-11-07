import json
import os
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from flask import current_app
from PIL import Image, UnidentifiedImageError
from werkzeug.datastructures import FileStorage


ALLOWED_FORMATS = {
    "JPEG": {".jpg", ".jpeg"},
    "PNG": {".png"},
}
MAX_IMAGE_PIXELS = 40_000_000
REPORT_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")


class InvalidReportImage(ValueError):
    pass


def _owner_directory(user_id: str) -> Path:
    upload_root = Path(
        current_app.config.get("REPORT_UPLOAD_ROOT")
        or Path(current_app.instance_path) / "report_uploads"
    )
    upload_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = upload_root / user_id
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        upload_root.chmod(0o700)
        directory.chmod(0o700)
    except OSError:
        current_app.logger.exception("Could not secure the report upload directory")
        raise
    return directory


def save_report_image(upload: FileStorage, user_id: str) -> tuple[str, Path]:
    if not upload.filename:
        raise InvalidReportImage("Choose an image to upload.")

    extension = Path(upload.filename).suffix.lower()
    try:
        image = Image.open(upload.stream)
        detected_format = image.format
        width, height = image.size
        if detected_format not in ALLOWED_FORMATS:
            raise InvalidReportImage("Upload a JPG, JPEG, or PNG image.")
        if extension not in ALLOWED_FORMATS[detected_format]:
            raise InvalidReportImage(
                "The file extension does not match the image content."
            )
        if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
            raise InvalidReportImage("The image dimensions are too large.")
        image.verify()
        upload.stream.seek(0)
        image = Image.open(upload.stream).convert("RGB")
    except InvalidReportImage:
        raise
    except (
        OSError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as error:
        raise InvalidReportImage("The uploaded file is not a valid image.") from error

    report_id = uuid4().hex
    directory = _owner_directory(user_id)
    suffix = ".jpg" if detected_format == "JPEG" else ".png"
    image_path = directory / f"{report_id}{suffix}"
    image_path.touch(mode=0o600, exist_ok=False)
    try:
        image.save(image_path, format=detected_format, optimize=True)
        image_path.chmod(0o600)
    except OSError:
        image_path.unlink(missing_ok=True)
        raise
    return report_id, image_path


def save_report_record(user_id: str, report_id: str, report: dict[str, Any]) -> None:
    directory = _owner_directory(user_id)
    record_path = directory / f"{report_id}.json"
    temporary_path = directory / f".{report_id}.{uuid4().hex}.tmp"
    try:
        with temporary_path.open("x", encoding="utf-8") as file:
            os.chmod(temporary_path, 0o600)
            json.dump(report, file, ensure_ascii=False, allow_nan=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, record_path)
        record_path.chmod(0o600)
    except OSError:
        temporary_path.unlink(missing_ok=True)
        raise


def load_report_record(user_id: str, report_id: str) -> dict[str, Any] | None:
    if not REPORT_ID_PATTERN.fullmatch(report_id):
        return None
    path = _owner_directory(user_id) / f"{report_id}.json"
    try:
        with path.open(encoding="utf-8") as file:
            data = json.load(file)
    except FileNotFoundError:
        return None
    if (
        not isinstance(data, dict)
        or data.get("report_id") != report_id
        or data.get("user_id") != user_id
    ):
        return None
    return data
