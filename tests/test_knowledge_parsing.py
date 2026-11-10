"""Real local parsers; generated PDFs contain actual PDF text operators, no OCR mocks."""

import io
import subprocess
from uuid import uuid4

import pytest
from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
)

from agenthub.core.errors import AppError
from agenthub.knowledge.parsing import Section, parse_bytes, parse_file, split_sections
from agenthub.knowledge.schemas import CreateKnowledgeBase
from agenthub.knowledge.storage import file_path, save_file, validate_filename


def text_pdf(text="Public AgentHub PDF knowledge.", blank=False, encrypted=False):
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    if not blank:
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 50 740 Td ({text}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = stream
    if encrypted:
        writer.encrypt("test-password")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize("kind", ["txt", "md"])
def test_utf8_normalization_offsets_and_overlap(kind):
    sections = parse_bytes("\ufeff你好\r\nCafe\u0301，知识库测试".encode(), kind, 200, 10)
    assert sections[0].text == "你好\nCafé，知识库测试"
    chunks = split_sections(sections, 6, 2, 20)
    assert chunks[0].content[-2:] == chunks[1].content[:2]
    for chunk in chunks:
        assert sections[0].text[chunk.char_start : chunk.char_end] == chunk.content
    assert chunks[-1].char_end == len(sections[0].text)


@pytest.mark.parametrize("length,expected", [(0, 0), (5, 1), (6, 1), (7, 2), (10, 2)])
def test_chunk_boundaries(length, expected):
    assert len(split_sections([Section(1, "x" * length)], 6, 2, 100)) == expected


def test_pages_do_not_merge_and_chunk_limit():
    sections = [Section(1, "a" * 10), Section(3, "b" * 10)]
    chunks = split_sections(sections, 6, 2, 10)
    assert [c.page_number for c in chunks] == [1, 1, 3, 3]
    with pytest.raises(AppError, match="Too many") as exc:
        split_sections(sections, 6, 2, 3)
    assert exc.value.code == "document_chunk_limit"


@pytest.mark.parametrize(
    "raw,code",
    [
        (b"", "empty_document"),
        (b" \r\n\t", "empty_document"),
        (b"\xff", "document_encoding_error"),
        (b"hello\x00world", "document_encoding_error"),
        (b"hello\x01", "document_encoding_error"),
        (b"123456", "document_text_limit"),
    ],
)
def test_invalid_text(raw, code):
    with pytest.raises(AppError) as exc:
        parse_bytes(raw, "txt", 5, 10)
    assert exc.value.code == code


def test_pdf_text_and_empty_encrypted_invalid():
    sections = parse_bytes(text_pdf(), "pdf", 1000, 10)
    assert sections[0].page_number == 1 and "AgentHub" in sections[0].text
    for content, code in [
        (text_pdf(blank=True), "pdf_no_text"),
        (text_pdf(encrypted=True), "encrypted_pdf"),
        (b"not a pdf", "invalid_pdf"),
        (b"%PDF-broken", "invalid_pdf"),
    ]:
        with pytest.raises(AppError) as exc:
            parse_bytes(content, "pdf", 1000, 10)
        assert exc.value.code == code


@pytest.mark.parametrize("filename", ["../a.txt", "C:\\a.md", "a/b.pdf", " a.txt", "a\x00.txt"])
def test_reject_filename_paths(filename):
    with pytest.raises(AppError) as exc:
        validate_filename(filename)
    assert exc.value.code == "invalid_filename"


def test_storage_and_subprocess_parser(settings, tmp_path, monkeypatch):
    doc_id = uuid4()
    save_file(tmp_path, doc_id, b"Public text")
    path = file_path(tmp_path, doc_id)
    assert path.read_bytes() == b"Public text"
    assert parse_file(path, "txt", settings) == [Section(1, "Public text")]
    assert not path.with_suffix(".part").exists()

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("parser", 1)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(AppError) as exc:
        parse_file(path, "txt", settings)
    assert exc.value.code == "document_parse_timeout"


def test_chunk_configuration():
    with pytest.raises(ValueError):
        CreateKnowledgeBase(name="test", chunk_size=100, chunk_overlap=100)
    with pytest.raises(ValueError):
        split_sections([Section(1, "a")], 3, 3, 10)
    assert validate_filename("知识.PDF") == ("知识.PDF", "pdf")
    with pytest.raises(AppError) as exc:
        validate_filename("test.exe")
    assert exc.value.status_code == 415
