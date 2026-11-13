from __future__ import annotations

from pathlib import Path

import pymupdf as fitz
from docx import Document
from openpyxl import load_workbook
from pptx import Presentation

from app.extractors.base import DocumentExtractor, ExtractedDocument, ExtractedSection, UnsupportedDocumentError


class PdfExtractor(DocumentExtractor):
    def extract(self, path: Path, *, max_chars: int | None = None) -> ExtractedDocument:
        sections = []
        total = 0
        truncated = False
        with fitz.open(path) as pdf:
            for i, page in enumerate(pdf):
                text = page.get_text("text").strip()
                if max_chars is not None and total + len(text) > max_chars:
                    text = text[: max(0, max_chars - total)].rstrip()
                    truncated = True
                if text:
                    sections.append(ExtractedSection(f"Seite {i + 1}", text, i + 1))
                    total += len(text)
                if truncated or (max_chars is not None and total >= max_chars):
                    truncated = truncated or i + 1 < len(pdf)
                    break
        return ExtractedDocument(path.name, sections, "ocr_required" if total < 80 else "ready", truncated)


class DocxExtractor(DocumentExtractor):
    def extract(self, path: Path, *, max_chars: int | None = None) -> ExtractedDocument:
        doc = Document(path)
        text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        for table in doc.tables:
            text += "\n" + "\n".join(" | ".join(cell.text for cell in row.cells) for row in table.rows)
        truncated = max_chars is not None and len(text) > max_chars
        return ExtractedDocument(path.name, [ExtractedSection("Dokument", text[:max_chars].strip() if truncated else text.strip())], truncated=truncated)


class PptxExtractor(DocumentExtractor):
    def extract(self, path: Path, *, max_chars: int | None = None) -> ExtractedDocument:
        deck = Presentation(path)
        sections = []
        total = 0
        truncated = False
        for i, slide in enumerate(deck.slides, 1):
            text = "\n".join(shape.text for shape in slide.shapes if hasattr(shape, "text") and shape.text.strip())
            if max_chars is not None and total + len(text) > max_chars:
                text = text[: max(0, max_chars - total)].rstrip()
                truncated = True
            sections.append(ExtractedSection(f"Folie {i}", text, i))
            total += len(text)
            if truncated or (max_chars is not None and total >= max_chars):
                truncated = truncated or i < len(deck.slides)
                break
        return ExtractedDocument(path.name, sections, truncated=truncated)


class XlsxExtractor(DocumentExtractor):
    def extract(self, path: Path, *, max_chars: int | None = None) -> ExtractedDocument:
        workbook = load_workbook(path, read_only=True, data_only=True)
        sections = []
        total = 0
        truncated = False
        for sheet in workbook.worksheets:
            rows = []
            for row in sheet.iter_rows(values_only=True):
                rendered = " | ".join("" if cell is None else str(cell) for cell in row)
                if max_chars is not None and total + len(rendered) > max_chars:
                    rendered = rendered[: max(0, max_chars - total)].rstrip()
                    truncated = True
                if rendered:
                    rows.append(rendered)
                    total += len(rendered)
                if truncated or (max_chars is not None and total >= max_chars):
                    break
            sections.append(ExtractedSection(f"Tabelle {sheet.title}", "\n".join(rows)))
            if truncated or (max_chars is not None and total >= max_chars):
                truncated = True
                break
        workbook.close()
        return ExtractedDocument(path.name, sections, truncated=truncated)


class TextExtractor(DocumentExtractor):
    def extract(self, path: Path, *, max_chars: int | None = None) -> ExtractedDocument:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            text = handle.read() if max_chars is None else handle.read(max_chars + 1)
        truncated = max_chars is not None and len(text) > max_chars
        return ExtractedDocument(
            path.name,
            [ExtractedSection("Dokument", text[:max_chars] if truncated else text)],
            truncated=truncated,
        )


EXTRACTORS = {
    ".pdf": PdfExtractor(), ".docx": DocxExtractor(), ".pptx": PptxExtractor(),
    ".xlsx": XlsxExtractor(), ".txt": TextExtractor(), ".md": TextExtractor(),
}


def extract_document(path: Path, *, max_chars: int | None = None) -> ExtractedDocument:
    extractor = EXTRACTORS.get(path.suffix.lower())
    if extractor is None:
        raise UnsupportedDocumentError(path.suffix.lower())
    return extractor.extract(path, max_chars=max_chars)
