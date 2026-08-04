@echo off
REM ============================================================
REM  run_bot.bat  —  اجرای ایجنت حقوقی روی ویندوز
REM  این فایل را در پوشه legal_agent ذخیره کنید و اجرا نمایید.
REM  پیش‌نیاز: Python 3.11 نصب باشد + uv نصب باشد.
REM ============================================================
cd /d "%~dp0"

echo [1/4] ایجاد محیط مجازی...
uv venv
call .venv\Scripts\activate

echo [2/4] نصب پکیج‌های اصلی...
uv add chromadb openai python-telegram-bot pypdf numpy python-dotenv requests

echo [3/4] نصب پکیج‌های OCR (برای عکس/PDF اسکن‌شده)...
uv add easyocr pymupdf

echo [4/4] اجرای ربات...
python -m src.bot

pause
