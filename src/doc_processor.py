"""
doc_processor.py
================
Turns user-submitted documents (PDF / image) into plain Persian text.

Pipeline:
  - PDF with text layer  -> pypdf extract
  - PDF/image scanned    -> OCR (EasyOCR Arabic model, auto-downloaded once)
  - docx/txt             -> simple read

The OCR model download happens on first run (needs internet on the user's PC).
We keep it lazy so the module imports even without the heavy deps installed.
"""
from __future__ import annotations

import os
import tempfile
from typing import Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def extract_pdf_text(path: str) -> str:
    """Extract text from a PDF. Returns '' if it's a scanned image PDF."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        reader = PdfReader(path)
        parts = []
        for page in reader.pages:
            t = page.extract_text() or ""
            parts.append(t)
        return "\n".join(parts).strip()
    except Exception as e:
        return f"[خطا در خواندن PDF: {e}]"


def ocr_image(path: str) -> str:
    """
    OCR a scanned image or PDF page using EasyOCR (Arabic script).
    Downloads the model on first use (~50MB).
    """
    try:
        import easyocr
    except ImportError:
        return ("[برای پردازش تصاویر اسکن‌شده، پکیج easyocr نصب نیست. "
                "روی ویندوز اجرا کن: uv add easyocr]")

    reader = easyocr.Reader(["fa", "ar"], gpu=False, model_storage_directory=os.path.join(PROJECT_ROOT, "data", "ocr_models"))
    results = reader.readtext(path, detail=0, paragraph=True)
    return "\n".join(results).strip()


def extract_from_pdf_ocr(path: str) -> str:
    """Render PDF pages to images then OCR them (for scanned PDFs)."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return ("[برای OCR روی PDF اسکن‌شده، پکیج PyMuPDF نصب نیست. "
                "روی ویندوز اجرا کن: uv add pymupdf]")
    import glob
    tmpdir = tempfile.mkdtemp()
    doc = fitz.open(path)
    texts = []
    for i, page in enumerate(doc):
        pix = page.get_pixmap(dpi=200)
        img_path = os.path.join(tmpdir, f"page_{i}.png")
        pix.save(img_path)
        texts.append(ocr_image(img_path))
    # cleanup
    for f in glob.glob(os.path.join(tmpdir, "*.png")):
        os.remove(f)
    os.rmdir(tmpdir)
    return "\n".join(t for t in texts if t).strip()


def process_document(path: str, force_ocr: bool = False) -> dict:
    """
    Main entry: accept a file path, return {text, method, note}.
    Auto-detects whether PDF has a text layer.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf" and not force_ocr:
        text = extract_pdf_text(path)
        if len(text) > 30:
            return {"text": text, "method": "pdf_text", "note": "متن از لایه متنی PDF استخراج شد."}
        # fall through to OCR
        ocr_text = extract_from_pdf_ocr(path)
        return {"text": ocr_text, "method": "pdf_ocr", "note": "PDF اسکن بود؛ OCR انجام شد."}
    elif ext in (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"):
        return {"text": ocr_image(path), "method": "image_ocr", "note": "OCR تصویر انجام شد."}
    elif ext in (".txt", ".md"):
        with open(path, "r", encoding="utf-8") as fh:
            return {"text": fh.read().strip(), "method": "text", "note": "فایل متنی خوانده شد."}
    elif ext == ".docx":
        try:
            import docx
            d = docx.Document(path)
            return {"text": "\n".join(p.text for p in d.paragraphs), "method": "docx", "note": "فایل Word خوانده شد."}
        except Exception as e:
            return {"text": "", "method": "error", "note": f"خطا در خواندن docx: {e}"}
    else:
        return {"text": "", "method": "unsupported", "note": f"نوع فایل پشتیبانی‌نشده: {ext}"}


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python doc_processor.py <file>")
    else:
        r = process_document(sys.argv[1])
        print(f"[{r['method']}] {r['note']}")
        print(r["text"][:1500])
