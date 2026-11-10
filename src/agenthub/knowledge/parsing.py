"""Bounded UTF-8/text-PDF extraction and deterministic per-page character chunks."""

import io
import json
import subprocess
import sys
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path

from pypdf import Configuration, PdfReader

from agenthub.core.config import Settings
from agenthub.core.errors import AppError


@dataclass(frozen=True)
class Section:
    page_number: int
    text: str


@dataclass(frozen=True)
class Chunk:
    page_number: int
    char_start: int
    char_end: int
    content: str


def clean(text: str) -> str:
    # Preserve line structure for Markdown and offsets into the normalized page.
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    if (
        "\x00" in text
        or "\ufffd" in text
        or any(unicodedata.category(c) == "Cc" and c not in "\n\t\f" for c in text)
    ):
        raise AppError(422, "document_encoding_error", "Document is not valid readable text")
    return text


def parse_bytes(content: bytes, kind: str, max_characters: int, max_pages: int) -> list[Section]:
    if kind in {"txt", "md"}:
        try:
            sections = [Section(1, clean(content.decode("utf-8-sig")))]
        except UnicodeError:
            raise AppError(422, "document_encoding_error", "Use UTF-8 text") from None
    elif kind == "pdf":
        if not content.startswith(b"%PDF-"):
            raise AppError(422, "invalid_pdf", "Invalid PDF file")
        try:
            Configuration.zlib_maximum_output_length = 10_000_000
            Configuration.stream_decoding_work_maximum_length = 20_000_000
            reader = PdfReader(io.BytesIO(content), strict=True)
            if reader.is_encrypted:
                raise AppError(422, "encrypted_pdf", "Encrypted PDFs are not supported")
            if len(reader.pages) > max_pages:
                raise AppError(422, "document_page_limit", "Too many PDF pages")
            sections = []
            total = 0
            for number, page in enumerate(reader.pages, 1):
                text = clean(page.extract_text() or "")
                total += len(text)
                if total > max_characters:
                    raise AppError(422, "document_text_limit", "Extracted text too large")
                sections.append(Section(number, text))
        except AppError:
            raise
        except Exception:
            raise AppError(422, "invalid_pdf", "PDF text extraction failed") from None
    else:
        raise AppError(415, "unsupported_document_type", "Unsupported document type")
    if sum(len(s.text) for s in sections) > max_characters:
        raise AppError(422, "document_text_limit", "Extracted text too large")
    if not any(s.text.strip() for s in sections):
        code = "pdf_no_text" if kind == "pdf" else "empty_document"
        raise AppError(422, code, "No text found; scanned PDFs require OCR")
    return sections


def split_sections(sections: list[Section], size: int, overlap: int, limit: int) -> list[Chunk]:
    if size < 1 or not 0 <= overlap < size:
        raise ValueError("Invalid chunk configuration")
    chunks = []
    for section in sections:
        start = 0
        while start < len(section.text):
            end = min(start + size, len(section.text))
            content = section.text[start:end]
            if content.strip():
                chunks.append(Chunk(section.page_number, start, end, content))
                if len(chunks) > limit:
                    raise AppError(422, "document_chunk_limit", "Too many document chunks")
            if end == len(section.text):
                break
            start = end - overlap
    return chunks


def parse_file(path: Path, kind: str, settings: Settings) -> list[Section]:
    # A disposable process bounds pathological parser CPU time. Docker bounds
    # worker+child memory. Never put raw exceptions/text in logs or task errors.
    payload = json.dumps(
        {
            "path": str(path),
            "kind": kind,
            "max_bytes": settings.document_max_bytes,
            "max_characters": settings.document_max_characters,
            "max_pages": settings.document_max_pages,
        }
    )
    try:
        result = subprocess.run(
            [sys.executable, "-m", "agenthub.knowledge.parsing"],
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=settings.document_parse_timeout_seconds,
            check=False,
        )
        body = json.loads(result.stdout)
        if "error" in body:
            raise AppError(422, body["error"], "Document parsing failed")
        return [Section(**section) for section in body["sections"]]
    except subprocess.TimeoutExpired:
        raise AppError(422, "document_parse_timeout", "Document parsing timed out") from None
    except (ValueError, KeyError, TypeError):
        raise AppError(422, "document_parse_failed", "Document parsing failed") from None


if __name__ == "__main__":
    try:
        data = json.load(sys.stdin)
        with Path(data["path"]).open("rb") as source:
            raw = source.read(data["max_bytes"] + 1)
        if len(raw) > data["max_bytes"]:
            raise AppError(413, "document_size_limit", "Document too large")
        parsed = parse_bytes(raw, data["kind"], data["max_characters"], data["max_pages"])
        print(json.dumps({"sections": [asdict(section) for section in parsed]}))
    except AppError as error:
        print(json.dumps({"error": error.code}))
    except Exception:
        print(json.dumps({"error": "document_parse_failed"}))
