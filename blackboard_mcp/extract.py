"""Plain text out of course files (slides, PDFs, Word, Excel, text), for reading them without saving them.

Adapted from ntulearn-mcp (https://github.com/kingdonkatsu/ntulearn-mcp), Copyright (c) 2026 kingdonkatsu,
MIT License (see LICENSE). Its PyMuPDF "vision" mode is left out: PyMuPDF is AGPL, and NTU decks are .pptx anyway.
"""
from __future__ import annotations

from io import BytesIO

from bs4 import BeautifulSoup

MAX_XLSX_ROWS_PER_SHEET = 1000

TEXT_EXTS = frozenset({
    "txt", "md", "markdown", "csv", "tsv", "json", "xml", "yaml", "yml", "html", "htm", "log", "sol", "py", "js",
    "ts", "rs", "go", "java", "c", "cpp", "h", "hpp", "sh", "bash", "rb", "swift", "kt", "scala", "r", "ini",
    "toml", "cfg", "conf", "ipynb",
})
TEXT_MIMES = frozenset({"application/json", "application/xml", "application/javascript", "application/x-yaml",
                        "application/yaml", "application/ld+json", "application/x-sh"})


def kind_of(filename: str, content_type: str | None) -> str:
    """'pdf', 'docx', 'pptx', 'xlsx', 'text' or 'binary'. The extension wins: bbcswebdav often serves
    everything as application/octet-stream."""
    ext = filename.rpartition(".")[2].lower() if "." in filename else ""
    if ext in ("pdf", "docx", "pptx", "xlsx"):
        return ext
    if ext in TEXT_EXTS:
        return "text"
    mime = (content_type or "").split(";")[0].strip().lower()
    if mime == "application/pdf":
        return "pdf"
    for marker, kind in (("wordprocessingml", "docx"), ("presentationml", "pptx"), ("spreadsheetml", "xlsx")):
        if marker in mime:
            return kind
    if mime.startswith("text/") or mime in TEXT_MIMES:
        return "text"
    return "binary"


def extract(filename: str, content_type: str | None, data: bytes) -> dict:
    """{"kind", "text", ...counts} on success, {"kind", "error"} when the file can't be read as text."""
    kind = kind_of(filename, content_type)
    try:
        if kind == "pdf":
            return _pdf(data)
        if kind == "pptx":
            return _pptx(data)
        if kind == "docx":
            return _docx(data)
        if kind == "xlsx":
            return _xlsx(data)
        if kind == "text":
            return _text(filename, content_type, data)
    except Exception as e:  # noqa: BLE001 - a corrupt file is reported, not raised
        return {"kind": kind, "error": f"couldn't read this {kind}: {type(e).__name__}: {e}"}
    return {"kind": "binary", "error": f"not a text format ({content_type or 'unknown type'}); use bb_download"}


def _pdf(data: bytes) -> dict:
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data))
    if reader.is_encrypted:
        try:
            reader.decrypt("")  # many "encrypted" course PDFs open with an empty password
        except Exception:  # noqa: BLE001
            return {"kind": "pdf", "error": "the PDF is password-protected"}
    pages = [f"## Page {i}\n\n{(p.extract_text() or '').strip()}" for i, p in enumerate(reader.pages, 1)]
    out = {"kind": "pdf", "text": "\n\n".join(pages), "pages": len(pages)}
    if not any(p.split("\n\n", 1)[-1] for p in pages):
        out["warning"] = "no extractable text (probably scanned images); use bb_download and open the file"
    return out


def _pptx(data: bytes) -> dict:
    from pptx import Presentation

    def shapes(container):  # grouped shapes hold their own shapes; lecture decks group a lot
        for shape in container:
            if hasattr(shape, "shapes"):
                yield from shapes(shape.shapes)
            else:
                yield shape

    prs = Presentation(BytesIO(data))
    blocks = []
    for i, slide in enumerate(prs.slides, 1):
        texts = []
        for shape in shapes(slide.shapes):
            if shape.has_text_frame and shape.text_frame.text.strip():
                texts.append(shape.text_frame.text)
            elif getattr(shape, "has_table", False) and shape.has_table:
                rows = ["\t".join(c.text.strip() for c in row.cells) for row in shape.table.rows]
                texts.append("\n".join(r for r in rows if r.strip()))
        block = f"## Slide {i}" + ("\n\n" + "\n\n".join(texts) if texts else "")
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text if slide.notes_slide.notes_text_frame else ""
            if notes.strip():
                block += f"\n\nSpeaker notes:\n{notes}"
        blocks.append(block)
    return {"kind": "pptx", "text": "\n\n".join(blocks), "slides": len(blocks)}


def _docx(data: bytes) -> dict:
    from docx import Document

    doc = Document(BytesIO(data))
    paras = [p.text for p in doc.paragraphs if p.text.strip()]
    tables = []
    for table in doc.tables:
        rows = ["\t".join(c.text.strip() for c in row.cells) for row in table.rows]
        rows = [r for r in rows if r.strip()]
        if rows:
            tables.append("\n".join(rows))
    text = "\n\n".join(paras) + ("\n\n## Tables\n\n" + "\n\n".join(tables) if tables else "")
    return {"kind": "docx", "text": text, "paragraphs": len(paras), "tables": len(doc.tables)}


def _xlsx(data: bytes) -> dict:
    from openpyxl import load_workbook

    wb = load_workbook(BytesIO(data), data_only=True, read_only=True)
    blocks, truncated = [], False
    try:
        for name in wb.sheetnames:
            rows = []
            for row in wb[name].iter_rows(values_only=True):
                if all(c is None or (isinstance(c, str) and not c.strip()) for c in row):
                    continue
                if len(rows) >= MAX_XLSX_ROWS_PER_SHEET:
                    truncated = True
                    rows.append(f"… (more rows; only the first {MAX_XLSX_ROWS_PER_SHEET} per sheet are shown)")
                    break
                rows.append("\t".join("" if c is None else str(c) for c in row))
            blocks.append(f"## Sheet: {name}" + ("\n\n" + "\n".join(rows) if rows else ""))
    finally:
        wb.close()
    out = {"kind": "xlsx", "text": "\n\n".join(blocks), "sheets": len(blocks)}
    if truncated:
        out["warning"] = "some sheets were cut off; use bb_download for the full data"
    return out


def _text(filename: str, content_type: str | None, data: bytes) -> dict:
    charset = None
    for part in (content_type or "").split(";")[1:]:
        if part.strip().lower().startswith("charset="):
            charset = part.split("=", 1)[1].strip().strip("\"'")
    text = None
    for enc in (charset, "utf-8", "latin-1"):
        if enc:
            try:
                text = data.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue
    text = text if text is not None else data.decode("utf-8", errors="replace")
    if filename.lower().endswith((".html", ".htm")) or (content_type or "").startswith("text/html"):
        lines = BeautifulSoup(text, "html.parser").get_text("\n").splitlines()
        text = "\n".join(s for s in (ln.strip() for ln in lines) if s)
    return {"kind": "text", "text": text}
