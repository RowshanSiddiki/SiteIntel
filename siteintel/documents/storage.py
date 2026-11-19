"""Secure storage for project knowledge documents (PDF)."""
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from flask import current_app
from werkzeug.datastructures import FileStorage

from siteintel.documents import settings

ALLOWED_EXTENSIONS = {".pdf"}
ALLOWED_MIME_TYPES = {"application/pdf", "application/x-pdf"}
STORAGE_KEY_PATTERN = re.compile(r"^(\d+)/([a-f0-9]{32})\.pdf$")


class InvalidDocument(ValueError):
    """Raised with a message that is safe to show to the user."""


@dataclass(frozen=True)
class StoredFile:
    storage_key: str
    path: Path
    sha256: str
    byte_size: int


def storage_root() -> Path:
    root = Path(
        current_app.config.get("DOCUMENT_UPLOAD_ROOT")
        or Path(current_app.instance_path) / "project_documents"
    )
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root.resolve()


def resolve_path(storage_key: str, project_id: int) -> Path | None:
    """Map a storage key to its file, refusing anything outside the root."""
    match = STORAGE_KEY_PATTERN.fullmatch(storage_key or "")
    if match is None or int(match.group(1)) != project_id:
        return None
    root = storage_root()
    path = (root / storage_key).resolve()
    if not path.is_relative_to(root):
        return None
    return path if path.is_file() else None


def validate_upload(upload: FileStorage | None) -> str:
    """Check name, extension and declared type; return the display filename."""
    if upload is None or not upload.filename:
        raise InvalidDocument("Choose a PDF file to upload.")
    name = Path(upload.filename.replace("\\", "/")).name.strip()
    if not name or Path(name).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise InvalidDocument("Only PDF files are supported.")
    if (upload.mimetype or "").lower() not in ALLOWED_MIME_TYPES:
        raise InvalidDocument("The file type is not supported. Upload a PDF.")
    return name[:255]


def save_pdf(upload: FileStorage, project_id: int) -> StoredFile:
    """Stream the upload to a collision-free, project-scoped file."""
    validate_upload(upload)
    if upload.stream.read(5) != b"%PDF-":
        raise InvalidDocument("The uploaded file is not a valid PDF.")
    upload.stream.seek(0)

    limit = settings.max_upload_bytes()
    directory = storage_root() / str(project_id)
    directory.mkdir(mode=0o700, exist_ok=True)
    storage_key = f"{project_id}/{uuid4().hex}.pdf"
    path = storage_root() / storage_key
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("xb") as target:
            os.chmod(path, 0o600)
            while chunk := upload.stream.read(65536):
                size += len(chunk)
                if size > limit:
                    raise InvalidDocument(
                        f"The file is larger than the {limit // (1024 * 1024)} MB limit."
                    )
                digest.update(chunk)
                target.write(chunk)
    except (InvalidDocument, OSError):
        path.unlink(missing_ok=True)
        raise
    if size == 0:
        path.unlink(missing_ok=True)
        raise InvalidDocument("The uploaded file is empty.")
    return StoredFile(storage_key, path, digest.hexdigest(), size)


def save_pdf_bytes(data: bytes, project_id: int) -> StoredFile:
    """Store an application-generated PDF in the same project-scoped store."""
    if not isinstance(data, bytes) or not data.startswith(b"%PDF-") or len(data) > settings.max_upload_bytes():
        raise InvalidDocument("The generated PDF is invalid or exceeds the file size limit.")
    directory = storage_root() / str(project_id)
    directory.mkdir(mode=0o700, exist_ok=True)
    storage_key = f"{project_id}/{uuid4().hex}.pdf"
    path = storage_root() / storage_key
    digest = hashlib.sha256(data).hexdigest()
    try:
        with path.open("xb") as target:
            os.chmod(path, 0o600)
            target.write(data)
    except OSError:
        path.unlink(missing_ok=True)
        raise
    return StoredFile(storage_key, path, digest, len(data))


def delete_file(storage_key: str, project_id: int) -> None:
    path = resolve_path(storage_key, project_id)
    if path is not None:
        path.unlink(missing_ok=True)
