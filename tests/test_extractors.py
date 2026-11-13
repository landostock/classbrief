from docx import Document
from pptx import Presentation
import fitz

from app.extractors.documents import extract_document


def test_pdf_extraction_keeps_pages(tmp_path):
    path = tmp_path / "arbeit.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Weimarer Verfassung " * 10)
    pdf.save(path)
    pdf.close()
    result = extract_document(path)
    assert result.sections[0].page == 1
    assert "Weimarer" in result.text


def test_pdf_extraction_stops_at_character_limit(tmp_path):
    path = tmp_path / "lang.pdf"
    pdf = fitz.open()
    for page_number in range(5):
        page = pdf.new_page()
        page.insert_text((72, 72), f"Seite {page_number + 1} " + ("Unterrichtsmaterial " * 30))
    pdf.save(path)
    pdf.close()

    result = extract_document(path, max_chars=250)

    assert result.truncated is True
    assert len(result.sections) < 5
    assert len(result.text) < 400


def test_docx_extraction(tmp_path):
    path = tmp_path / "blatt.docx"
    doc = Document()
    doc.add_paragraph("Artikel 48")
    doc.save(path)
    assert "Artikel 48" in extract_document(path).text


def test_pptx_extraction(tmp_path):
    path = tmp_path / "folien.pptx"
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[1])
    slide.shapes.title.text = "Krisenjahr 1923"
    deck.save(path)
    result = extract_document(path)
    assert result.sections[0].page == 1
    assert "Krisenjahr" in result.text
