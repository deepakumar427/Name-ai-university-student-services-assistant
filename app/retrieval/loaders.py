"""Read a document into a list of pages: [(page_number, text), ...]."""

import io
import re

from pypdf import PdfReader
from pypdf.errors import PdfReadError

SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".md"}
PAGE_MARKER = "<<<PAGE>>>"


class DocumentReadError(Exception):
    pass


def _clean(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r", "").splitlines()]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def read_pages(file_name: str, data: bytes) -> list[tuple[int, str]]:
    ext = "." + file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    if ext not in SUPPORTED_EXTENSIONS:
        raise DocumentReadError(f"Unsupported file type '{ext}'. Supported: {sorted(SUPPORTED_EXTENSIONS)}")
    if ext == ".pdf":
        try:
            reader = PdfReader(io.BytesIO(data))
            pages = [(i + 1, _clean(p.extract_text() or "")) for i, p in enumerate(reader.pages)]
        except (PdfReadError, ValueError, OSError) as exc:
            raise DocumentReadError(f"Could not read PDF: {exc}") from exc
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DocumentReadError("Text file is not valid UTF-8") from exc
        pages = [(i + 1, _clean(t)) for i, t in enumerate(text.split(PAGE_MARKER))]
    pages = [(n, t) for n, t in pages if t]
    if not pages:
        raise DocumentReadError("No readable text found (scanned PDFs need OCR, which is not enabled)")
    return pages
