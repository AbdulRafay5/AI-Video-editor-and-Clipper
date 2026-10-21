"""Read text out of campaign documents (PDF, Word, plain text)."""
import io
import re
from pathlib import Path

MAX_BYTES = 10 * 1024 * 1024
MAX_CHARS = 200_000
SUPPORTED = {".pdf", ".docx", ".txt", ".md"}


class DocError(Exception):
    """A document couldn't be read; the message says why in plain words."""


def _decode(data):
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _read_docx(data):
    try:
        import docx
    except ImportError:
        raise DocError("python-docx isn't installed. Run: pip install -r requirements.txt")
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception:
        raise DocError("Could not open this Word file. It must be a .docx (the old .doc format isn't supported).")
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text.strip() for cell in row.cells))
    return "\n".join(parts)


def _read_pdf(data):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise DocError("pypdf isn't installed. Run: pip install -r requirements.txt")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise DocError("This PDF is password protected.")
        pages = [(page.extract_text() or "") for page in reader.pages]
    except DocError:
        raise
    except Exception as e:
        raise DocError(f"Could not read this PDF ({e}).")
    text = "\n".join(pages)
    if len(text.strip()) < 50:
        raise DocError("This PDF has no selectable text (it may be a scan). Scanned documents "
                       "aren't supported yet. Paste the text instead.")
    return text


def _clean(text):
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def read_text(text):
    """For text pasted into the app (for example copied from a Google Doc)."""
    text = _clean(text or "")
    if len(text) < 50:
        raise DocError("There is almost no text here. Paste the full campaign brief.")
    return text[:MAX_CHARS]


def read_document(filename, data):
    """Return the plain text of an uploaded file. `data` is the file's bytes."""
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED:
        raise DocError("Unsupported file type. Use PDF, Word (.docx), .txt or .md, or paste the text.")
    if len(data) > MAX_BYTES:
        raise DocError("This file is too large (limit 10 MB).")
    if ext in (".txt", ".md"):
        text = _decode(data)
    elif ext == ".docx":
        text = _read_docx(data)
    else:
        text = _read_pdf(data)
    return read_text(text)