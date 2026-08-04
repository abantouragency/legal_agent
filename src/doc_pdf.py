"""
doc_pdf.py
==========
Persian-aware PDF generation for legal documents and analyses.

Uses fpdf2 + arabic-reshaper + python-bidi so right-to-left Persian text renders
correctly. A Persian TTF font is required; the module looks for one locally and,
if absent, tries to download Vazirmatn (needs internet, e.g. on Render/Windows).

All heavy imports are lazy so the bot still runs on machines where these
optional deps are not installed (it degrades to plain text with a clear message).
"""
from __future__ import annotations

import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FONT_CANDIDATES = [
    os.path.join(PROJECT_ROOT, "data", "fonts", "persian.ttf"),
    os.path.join(PROJECT_ROOT, "data", "fonts", "Vazirmatn-Regular.ttf"),
    os.path.join(PROJECT_ROOT, "data", "fonts", "Vazir.ttf"),
    r"C:\Windows\Fonts\Vazir.ttf",
    r"C:\Windows\Fonts\tahoma.ttf",
]


def available() -> bool:
    try:
        import fpdf  # noqa: F401
        return True
    except Exception:
        return False


def _ensure_font() -> str:
    """Return a usable Persian TTF path, downloading Vazirmatn if necessary."""
    for f in FONT_CANDIDATES:
        if os.path.isfile(f):
            return f
    # try to download Vazirmatn (requires internet)
    url = ("https://github.com/rastikerdar/vazirmatn/raw/master/fonts/ttf/"
           "Vazirmatn-Regular.ttf")
    out = os.path.join(PROJECT_ROOT, "data", "fonts", "Vazirmatn-Regular.ttf")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    try:
        import urllib.request
        print("📥 downloading Persian font for PDF...")
        urllib.request.urlretrieve(url, out)
        return out
    except Exception as e:
        raise RuntimeError(
            "فونت فارسی برای تولید PDF یافت نشد. لطفاً یک فایل TTF فارسی را در "
            "پوشه data/fonts/ با نام persian.ttf قرار دهید یا اتصال اینترنت را "
            f"بررسی کنید. (خطا: {e})"
        )


def _reshape(text: str) -> str:
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text  # graceful: still produce a PDF (may show unshaped glyphs)


def build_pdf(text: str, title: str, out_path: str) -> str:
    """Render `text` (Persian, RTL) into a PDF at `out_path`. Returns path."""
    from fpdf import FPDF

    font = _ensure_font()
    pdf = FPDF(format="A4")
    pdf.add_font("persian", "", font)
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    # header / institute banner
    pdf.set_font("persian", size=13)
    pdf.set_text_color(30, 30, 30)
    pdf.multi_cell(0, 9, _reshape("⚖️ موسسه حقوقی پدیدآوران عدالت"), align="R")
    pdf.set_font("persian", size=11)
    pdf.set_text_color(90, 90, 90)
    pdf.multi_cell(0, 7, _reshape(f"📑 {title}"), align="R")
    pdf.ln(2)
    pdf.set_draw_color(200, 200, 200)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(3)

    # body
    pdf.set_font("persian", size=11)
    pdf.set_text_color(20, 20, 20)
    for para in text.split("\n"):
        if not para.strip():
            pdf.ln(3)
            continue
        pdf.multi_cell(0, 7, _reshape(para), align="R")

    pdf.ln(4)
    pdf.set_font("persian", size=9)
    pdf.set_text_color(150, 150, 150)
    pdf.multi_cell(
        0, 6,
        _reshape("این پیش‌نویس جهت راهنمایی اولیه است و پیش از تسلیم باید توسط "
                 "وکیل بررسی شود. موسسه حقوقی پدیدآوران عدالت."),
        align="R",
    )

    pdf.output(out_path)
    return out_path
