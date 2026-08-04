"""
bot.py
======
Telegram legal-assistant bot with a trial -> paid access model and channel
reporting.

Flow:
  /start            -> consent + ask for name/phone (optional); explain trial
  text / document   -> if user has access: run full pipeline, reply summary
                       privately + post full report to the channel.
                       If trial not used: allow ONE free analysis, then lock.
                       If locked: tell them to message @rezapilot to buy.

Environment (.env):
  BOT_TOKEN            Telegram bot token
  OPENAI_API_KEY       OpenAI key
  MODEL                default gpt-4o-mini
  CHANNEL_ID           e.g. -1004458550016  (where reports are posted)
  ADMIN_IDS            comma-separated numeric ids (always have access)
  ALLOWED_FILE         path to allowed_users.txt (default data/allowed_users.txt)
  TRIAL_QUESTIONS      how many free analyses before lock (default 1)
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Optional

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters,
)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from seed_loader import load_records, build_collection, query_collection
from doc_processor import process_document
from lawyer_agent import ask_lawyer

COLLECTION = None
CFG = {}


# --------------------------------------------------------------------------
# access control
# --------------------------------------------------------------------------
def _load_allowed() -> set[int]:
    path = CFG.get("allowed_file") or os.path.join(PROJECT_ROOT, "data", "allowed_users.txt")
    out: set[int] = set()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    out.add(int(line))
                except ValueError:
                    continue
    except FileNotFoundError:
        pass
    for a in CFG.get("admin_ids", []):
        out.add(a)
    return out


def has_access(user_id: int) -> bool:
    return user_id in _load_allowed()


def trial_used(user_id: int) -> bool:
    path = os.path.join(CFG.get("data_dir", os.path.join(PROJECT_ROOT, "data")), "trial_used.txt")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return str(user_id) in {l.strip() for l in fh if l.strip()}
    except FileNotFoundError:
        return False


def mark_trial_used(user_id: int):
    path = os.path.join(CFG.get("data_dir", os.path.join(PROJECT_ROOT, "data")), "trial_used.txt")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"{user_id}\n")


# --------------------------------------------------------------------------
# user profile (name/phone collected with consent)
# --------------------------------------------------------------------------
def get_profile(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.user_data.get("profile", {})


def set_profile(chat_id: int, context: ContextTypes.DEFAULT_TYPE, **kw):
    p = context.user_data.setdefault("profile", {})
    p.update(kw)


# --------------------------------------------------------------------------
# handlers
# --------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(
        "👋 سلام. من دستیار حقوقی (مشاوره اولیه) هستم.\n\n"
        "⚠️ با استفاده از این ربات، موافقت می‌کنید که پرسش و تحلیل شما (بدون افشای "
        "نام و شماره، مگر با اجازه خودتان) جهت بهبود کیفیت در کانال مدیر ثبت شود.\n\n"
        "🎁 دوره آزمایشی: شما ۱ تحلیل رایگان دارید. پس از آن برای ادامه به "
        "@rezapilot پیام بدهید تا اکانت فعال شود.\n\n"
        "برای دریافت تحلیل:\n"
        "۱. نام و شماره تماستان را (اختیاری) بفرستید، یا «رد» بنویسید.\n"
        "۲. موضوع حقوقی خود را بنویسید.\n"
        "۳. مستندات (عکس/PDF) را بفرستید (اختیاری).\n"
        "۴. /analyze را بزنید."
    )
    set_profile(uid, context, consented=True)


async def profile_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Capture name/phone if the user hasn't set it yet."""
    text = update.message.text.strip()
    if text in ("رد", "نمی‌خوام", "skip", "رد شد"):
        set_profile(update.effective_user.id, context, name="—", phone="—")
        await update.message.reply_text("ثبت شد (بدون نام). موضوع خود را بنویسید.")
        return
    # treat first free-text before any issue as name/phone
    prof = get_profile(update.effective_user.id, context)
    if "name" not in prof or prof.get("name") in (None, "—"):
        set_profile(update.effective_user.id, context, name=text[:60])
        await update.message.reply_text(
            "✅ نام ثبت شد. حالا شماره تماس را بفرستید یا «رد» را بنویسید."
        )
        return
    if "phone" not in prof or prof.get("phone") in (None, "—"):
        set_profile(update.effective_user.id, context, phone=text[:20])
        await update.message.reply_text(
            "✅ مشخصات ثبت شد. موضوع حقوقی خود را بنویسید."
        )
        return
    # otherwise treat as the legal issue
    await handle_text(update, context)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    st = context.chat_data.setdefault("conv", {"issue": "", "doc": ""})
    st["issue"] = (st["issue"] + "\n" + update.message.text).strip()
    await update.message.reply_text(
        "✅ موضوع دریافت شد. مستندی دارید؟ بفرستید، وگرنه /analyze را بزنید."
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    st = context.chat_data.setdefault("conv", {"issue": "", "doc": ""})
    doc = update.message.document
    if not doc:
        return
    await update.message.reply_text("⏳ در حال پردازش مستند (OCR/استخراج متن)...")
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
    await update.message.reply_text(
        f"✅ مستند پردازش شد ({result['note']}). حالا /analyze را بزنید."
    )


def _access_blocked_message() -> str:
    return (
        "🔒 دوره آزمایشی رایگان شما به پایان رسید.\n\n"
        "برای فعال‌سازی نامحدود، به ادمین @rezapilot پیام دهید و پس از تایید، "
        "اکانت شما فعال خواهد شد.\n\n"
        "پس از خرید، آیدی تلگرام خود را از @userinfobot دریافت کرده و برای ادمین "
        "بفرستید تا دسترسی ثبت شود."
    )


async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    st = context.chat_data.get("conv", {"issue": "", "doc": ""})
    issue = st.get("issue", "").strip()
    if not issue:
        await update.message.reply_text("اول موضوع خود را بنویسید، سپس /analyze را بزنید.")
        return

    # access control
    if not has_access(uid):
        if not trial_used(uid):
            # allow exactly one free analysis
            pass
        else:
            await update.message.reply_text(_access_blocked_message())
            return

    await update.message.reply_text("🔎 در حال جستجوی مواد قانونی و تحلیل...")

    coll = ensure_collection()
    loop = asyncio.get_event_loop()
    hits = await loop.run_in_executor(None, query_collection, coll, issue, 6)

    try:
        opinion = await loop.run_in_executor(
            None, lambda: ask_lawyer(issue, hits, doc_text=st.get("doc") or None,
                                     openai_key=CFG.get("openai_key"), model=CFG.get("model", "gpt-4o-mini"))
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ خطا در فراخوانی مدل: {e}")
        return

    # 1) reply summary privately
    await update.message.reply_text(opinion.raw[:4000] if len(opinion.raw) <= 4000 else opinion.raw[:4000] + "\n...(ادامه در کانال)")

    # 2) post full report to channel
    prof = get_profile(uid, context)
    name = prof.get("name", "—")
    phone = prof.get("phone", "—")
    user_handle = update.effective_user.username or "بدون یوزرنیم"
    report = (
        f"📋 گزارش جدید از ایجنت حقوقی\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"👤 کاربر: {name} | 📞 {phone}\n"
        f"🔗 یوزرنیم: @{user_handle} | ID: {uid}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📝 موضوع:\n{issue[:1500]}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{opinion.raw}"
    )
    await post_to_channel(report)

    # 3) consume trial if applicable
    if not has_access(uid) and not trial_used(uid):
        mark_trial_used(uid)
        await update.message.reply_text(
            "✅ تحلیل رایگان شما ارائه شد. برای تحلیل‌های بیشتر به @rezapilot پیام دهید."
        )

    # reset conversation
    context.chat_data["conv"] = {"issue": "", "doc": ""}


async def post_to_channel(text: str):
    ch = CFG.get("channel_id")
    if not ch:
        return
    # Telegram messages are limited to ~4096 chars; split if needed.
    chunks = _split(text)
    for c in chunks:
        await ContextTypes.DEFAULT_TYPE  # noop guard (kept for clarity)
        try:
            await _app.bot.send_message(chat_id=ch, text=c, parse_mode=None)
        except Exception as e:
            print(f"channel post failed: {e}")


def _split(text: str, limit: int = 4000) -> list[str]:
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


def ensure_collection():
    global COLLECTION
    if COLLECTION is None:
        import chromadb
        client = chromadb.PersistentClient(path=os.path.join(PROJECT_ROOT, "data", "chroma"))
        recs = load_records()
        COLLECTION = build_collection(client, records=recs, openai_key=CFG.get("openai_key"))
    return COLLECTION


_app = None


def main():
    global _app, CFG
    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    CFG = {
        "openai_key": os.environ.get("OPENAI_API_KEY"),
        "model": os.environ.get("MODEL", "gpt-4o-mini"),
        "channel_id": os.environ.get("CHANNEL_ID"),
        "admin_ids": [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()],
        "allowed_file": os.environ.get("ALLOWED_FILE"),
        "data_dir": os.path.join(PROJECT_ROOT, "data"),
    }
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN not set in .env")

    _app = Application.builder().token(token).build()
    _app.add_handler(CommandHandler("start", start))
    _app.add_handler(CommandHandler("analyze", analyze))
    _app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    # text that is not a command: route through profile capture / issue capture
    _app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, profile_input))

    print("🤖 Bot started. Press Ctrl+C to stop.")
    _app.run_polling()


if __name__ == "__main__":
    main()
