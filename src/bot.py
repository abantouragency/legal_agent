"""
bot.py
======
Telegram bot: collects the user's legal issue + documents, runs RAG retrieval,
calls the lawyer agent, and streams the structured opinion back.

Usage:
  set BOT_TOKEN and OPENAI_API_KEY in .env, then:
  python -m src.bot

Conversation flow:
  /start           -> greeting + instructions
  text message     -> stored as the "issue"
  document/file    -> OCR / PDF extraction, stored as "doc"
  /analyze         -> runs the pipeline on collected context
  /reset           -> clears context
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Optional

from telegram import Update, Document, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from seed_loader import load_records, build_collection, query_collection
from doc_processor import process_document
from lawyer_agent import ask_lawyer, SYSTEM_PROMPT

# Per-chat memory (simple in-memory; replace with DB for production)
CHAT_STATE: dict[int, dict] = {}

COLLECTION = None
OPENAI_KEY = ""


def ensure_collection():
    global COLLECTION
    if COLLECTION is None:
        import chromadb
        client = chromadb.PersistentClient(path=os.path.join(PROJECT_ROOT, "data", "chroma"))
        recs = load_records()
        COLLECTION = build_collection(client, records=recs, openai_key=OPENAI_KEY)
    return COLLECTION


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 سلام. من دستیار حقوقی (وکیل پایه یک) هستم.\n\n"
        "برای دریافت تحلیل و راهکار حقوقی، مراحل زیر را انجام دهید:\n"
        "۱. موضوع خود را به صورت متن بنویسید (مثلاً: «کارفرما ۴ ماه حقوقم را نداده»).\n"
        "۲. اگر مستندی (قرارداد، رأی، شکوائیه، عکس) دارید، فایل را بفرستید.\n"
        "۳. دستور /analyze را بزنید تا تحلیل را دریافت کنید.\n"
        "۴. با /reset می‌توانید شروع جدید کنید.\n\n"
        "⚠️ خروجی من مشاوره اولیه است و جایگزین مراجعه به وکیل دادگستری نیست."
    )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    CHAT_STATE[update.effective_chat.id] = {"issue": "", "doc": ""}
    await update.message.reply_text("♻️ حافظه گفتگو پاک شد. موضوع جدید را بنویسید.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    st = CHAT_STATE.setdefault(chat_id, {"issue": "", "doc": ""})
    st["issue"] = (st["issue"] + "\n" + update.message.text).strip()
    await update.message.reply_text(
        "✅ موضوع دریافت شد. مستندی دارید؟ بفرستید، وگرنه /analyze را بزنید."
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    st = CHAT_STATE.setdefault(chat_id, {"issue": "", "doc": ""})
    doc: Document = update.message.document
    if not doc:
        return
    await update.message.reply_text("⏳ در حال پردازش مستند (OCR/استخراج متن)... لطفاً صبر کنید.")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(doc.file_name or "")[1])
    adl = await context.bot.get_file(doc.file_id)
    await adl.download_to_drive(tmp.name)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, process_document, tmp.name)
    os.unlink(tmp.name)
    if result["method"] == "unsupported" or not result["text"]:
        await update.message.reply_text(f"⚠️ نتوانستم مستند را پردازش کنم: {result['note']}")
        return
    st["doc"] = (st["doc"] + "\n" + result["text"]).strip()
    preview = result["text"][:400].replace("\n", " ")
    await update.message.reply_text(
        f"✅ مستند پردازش شد ({result['note']}).\n"
        f"پیش‌نمایش: {preview}...\n\nحالا /analyze را بزنید."
    )


async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    st = CHAT_STATE.get(chat_id, {"issue": "", "doc": ""})
    issue = st.get("issue", "").strip()
    if not issue:
        await update.message.reply_text("اول موضوع خود را بنویسید، سپس /analyze را بزنید.")
        return
    await update.message.reply_text("🔎 در حال جستجوی مواد قانونی مرتبط و تحلیل...")

    coll = ensure_collection()
    loop = asyncio.get_event_loop()
    hits = await loop.run_in_executor(None, query_collection, coll, issue, 6)

    try:
        opinion = await loop.run_in_executor(
            None, lambda: ask_lawyer(issue, hits, doc_text=st.get("doc") or None, openai_key=OPENAI_KEY)
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ خطا در فراخوانی مدل: {e}")
        return

    # Telegram messages are limited to ~4096 chars; split if needed.
    chunks = _split_message(opinion.raw)
    for c in chunks:
        await update.message.reply_text(c)
    if not chunks:
        await update.message.reply_text(opinion.raw or "پاسخی تولید نشد.")


def _split_message(text: str, limit: int = 4000) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for line in text.splitlines():
        if len(cur) + len(line) + 1 > limit:
            parts.append(cur)
            cur = line
        else:
            cur = (cur + "\n" + line).strip()
    if cur:
        parts.append(cur)
    return parts


def main():
    global OPENAI_KEY
    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    token = os.environ.get("BOT_TOKEN")
    OPENAI_KEY = os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("MODEL", "gpt-4o-mini")
    if not token:
        raise RuntimeError("BOT_TOKEN not set in .env")
    # persist model name
    os.environ["MODEL"] = model

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("analyze", analyze))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("🤖 Bot started. Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
