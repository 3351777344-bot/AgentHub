"""Only UUID-derived names touch disk; original filenames are display metadata."""

import os
from pathlib import Path
from uuid import UUID

from agenthub.core.errors import AppError


def validate_filename(filename: str | None) -> tuple[str, str]:
    if (
        not filename
        or len(filename) > 200
        or filename.strip() != filename
        or any(c in filename for c in "/\\:")
        or any(ord(c) < 32 for c in filename)
    ):
        raise AppError(422, "invalid_filename", "Use a plain filename without a path")
    kind = Path(filename).suffix.lower().lstrip(".")
    if kind not in {"txt", "md", "pdf"}:
        raise AppError(415, "unsupported_document_type", "Supported types: txt, md, pdf")
    return filename, kind


def file_path(root: Path, document_id: UUID) -> Path:
    return root.resolve() / f"{document_id.hex}.raw"


def save_file(root: Path, document_id: UUID, content: bytes) -> None:
    root.mkdir(parents=True, exist_ok=True)
    destination = file_path(root, document_id)
    temporary = destination.with_suffix(".part")
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
