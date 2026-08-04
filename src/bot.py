"""
bot.py
======
Telegram legal-assistant bot with a trial -> paid sales model, channel
reporting, and an admin dashboard.

Flow:
  /start      -> consent + profile capture (name/phone) + explain trial
  /buy        -> request paid access (flags pending, tells user to pay @rezapilot)
  /analyze    -> if access: run pipeline, reply summary privately + post full
                 report to the channel. Trial users get 1 free analysis.
  (locked)    -> "message @rezapilot to buy"

Admin commands (ADMIN_IDS only):
  /admin      -> dashboard (stats + pending purchases)
  /approve ID -> grant paid access to a user
  /revoke ID  -> downgrade a user to trial
  /stats      -> quick stats
  /broadcast  -> (text after) send a message to all paid users via channel/DM

Environment (.env): see .env.example
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from typing import Optional

# Ensure the directory containing this file (src/) is importable regardless of
# how the bot is launched (python -m src.bot, python src/bot.py, etc.).
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters,
)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from seed_loader import load_records, build_collection, query_collection
from doc_processor import process_document
from lawyer_agent import ask_lawyer
import admin_panel as AP

COLLECTION = None
CFG = {}
_app = None


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def get_profile(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.user_data.get("profile", {})


def set_profile(chat_id: int, context: ContextTypes.DEFAULT_TYPE, **kw):
    p = context.user_data.setdefault("profile", {})
    p.update(kw)


def is_admin(uid: int) -> bool:
    return uid in CFG.get("admin_ids", [])


def _access_blocked_message() -> str:
    return (
        "🔒 دوره آزمایشی رایگان شما به پایان رسید.\n\n"
        "برای فعال‌سازی نامحدود، ابتدا /buy را بزنید، سپس مبلغ را به ادمین @rezapilot "
        "واریز کرده و آیدی تلگرام خود را (از @userinfobot) برای ایشان بفرستید تا "
        "دسترسی شما تایید شود."
    )


# --------------------------------------------------------------------------
# user-facing handlers
# --------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    handle = update.effective_user.username or "—"
    AP.ensure_user(uid, handle=handle, admin_ids=CFG.get("admin_ids"))
    await update.message.reply_text(
        "👋 سلام. من دستیار حقوقی (مشاوره اولیه) هستم.\n\n"
        "⚠️ با استفاده از این ربات، موافقت می‌کنید پرسش و تحلیل شما (بدون افشای نام "
        "و شماره، مگر با اجازه خودتان) جهت بهبود کیفیت در کانال مدیر ثبت شود.\n\n"
        "🎁 دوره آزمایشی: ۱ تحلیل رایگان. پس از آن برای ادامه /buy را بزنید.\n\n"
        "برای دریافت تحلیل:\n"
        "۱. نام و شماره تماس را (اختیاری) بفرستید یا «رد» بنویسید.\n"
        "۲. موضوع حقوقی را بنویسید.\n"
        "۳. مستندات (عکس/PDF) را بفرستید (اختیاری).\n"
        "۴. /analyze را بزنید."
    )
    set_profile(uid, context, consented=True)


async def profile_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id
    if text in ("رد", "نمی‌خوام", "skip", "رد شد"):
        set_profile(uid, context, name="—", phone="—")
        AP.ensure_user(uid, name="—", phone="—", handle=update.effective_user.username or "—",
                       admin_ids=CFG.get("admin_ids"))
        await update.message.reply_text("ثبت شد (بدون نام). موضوع خود را بنویسید.")
        return
    prof = get_profile(uid, context)
    if "name" not in prof or prof.get("name") in (None, "—"):
        set_profile(uid, context, name=text[:60])
        AP.ensure_user(uid, name=text[:60], handle=update.effective_user.username or "—",
                       admin_ids=CFG.get("admin_ids"))
        await update.message.reply_text("✅ نام ثبت شد. حالا شماره تماس را بفرستید یا «رد» را بنویسید.")
        return
    if "phone" not in prof or prof.get("phone") in (None, "—"):
        set_profile(uid, context, phone=text[:20])
        await update.message.reply_text("✅ مشخصات ثبت شد. موضوع حقوقی خود را بنویسید.")
        return
    await handle_text(update, context)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st = context.chat_data.setdefault("conv", {"issue": "", "doc": ""})
    st["issue"] = (st["issue"] + "\n" + update.message.text).strip()
    await update.message.reply_text(
        "✅ موضوع دریافت شد. مستندی دارید؟ بفرستید، وگرنه /analyze را بزنید."
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
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


async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if AP.has_access(uid, CFG.get("admin_ids")):
        await update.message.reply_text("✅ شما در حال حاضر دسترسی کامل دارید.")
        return
    if AP.request_purchase(uid):
        await update.message.reply_text(
            "🛒 درخواست خرید ثبت شد.\n\n"
            "لطفاً مبلغ را به کارت/آیدی ادمین @rezapilot واریز کرده و رسید + آیدی "
            "تلگرام خود (از @userinfobot) را برای ایشان بفرستید. پس از تایید، دسترسی "
            "نامحدود برایتان فعال می‌شود."
        )
    else:
        pass


async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    st = context.chat_data.get("conv", {"issue": "", "doc": ""})
    issue = st.get("issue", "").strip()
    if not issue:
        await update.message.reply_text("اول موضوع خود را بنویسید، سپس /analyze را بزنید.")
        return

    # access gate
    if not AP.has_access(uid, CFG.get("admin_ids")):
        if not AP.consume_trial(uid):
            await update.message.reply_text(_access_blocked_message())
            return

    await update.message.reply_text("🔎 در حال جستجوی مواد قانونی و تحلیل...")

    coll = ensure_collection()
    loop = asyncio.get_event_loop()
    hits = await loop.run_in_executor(None, query_collection, coll, issue, 6)

    try:
        opinion = await loop.run_in_executor(
            None, lambda: ask_lawyer(issue, hits, doc_text=st.get("doc") or None,
                                     openai_key=CFG.get("openai_key"),
                                     model=CFG.get("model", "gpt-4o-mini"))
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ خطا در فراخوانی مدل: {e}")
        return

    await update.message.reply_text(
        opinion.raw if len(opinion.raw) <= 4000 else opinion.raw[:4000] + "\n...(ادامه در کانال)"
    )

    prof = get_profile(uid, context)
    name = prof.get("name", "—")
    phone = prof.get("phone", "—")
    handle = update.effective_user.username or "بدون یوزرنیم"
    report = (
        f"📋 گزارش جدید از ایجنت حقوقی\n━━━━━━━━━━━━━━━━\n"
        f"👤 کاربر: {name} | 📞 {phone}\n🔗 @{handle} | ID: {uid}\n"
        f"━━━━━━━━━━━━━━━━\n📝 موضوع:\n{issue[:1500]}\n\n━━━━━━━━━━━━━━━━\n{opinion.raw}"
    )
    await post_to_channel(report)
    AP.incr_analyses(uid)

    if not AP.has_access(uid, CFG.get("admin_ids")):
        await update.message.reply_text(
            "✅ تحلیل رایگان شما ارائه شد. برای تحلیل‌های بیشتر /buy را بزنید."
        )
    context.chat_data["conv"] = {"issue": "", "doc": ""}


# --------------------------------------------------------------------------
# admin commands
# --------------------------------------------------------------------------
async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text(AP.render_dashboard(CFG.get("admin_ids")))


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    s = AP.stats()
    await update.message.reply_text(
        f"📈 آمار:\nکل: {s['total']} | پرداختی: {s['paid']} | ترایال: {s['trial']} | "
        f"ادمین: {s['admin']} | در انتظار: {s['pending']} | تحلیل‌ها: {s['total_analyses']}"
    )


async def approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("استفاده: /approve <USER_ID>")
        return
    uid = int(args[0])
    if AP.approve_purchase(uid):
        await update.message.reply_text(f"✅ دسترسی نامحدود برای کاربر {uid} فعال شد.")
        # notify the user if possible
        try:
            await _app.bot.send_message(chat_id=uid,
                text="🎉 دسترسی نامحدود شما فعال شد! حالا می‌توانید تحلیل‌های نامحدود داشته باشید.")
        except Exception:
            pass
    else:
        await update.message.reply_text("⚠️ کاربر یافت نشد یا قبلاً فعال بود.")


async def revoke_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("استفاده: /revoke <USER_ID>")
        return
    if AP.revoke_access(int(args[0])):
        await update.message.reply_text(f"🔄 دسترسی کاربر {args[0]} به ترایال تغییر یافت.")
    else:
        await update.message.reply_text("⚠️ عملیات ناموفق (ممکن است ادمین باشد).")


async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text("استفاده: /broadcast <متن پیام>")
        return
    await post_to_channel(f"📢 اطلاعیه:\n{text}")
    await update.message.reply_text("✅ اطلاعیه در کانال منتشر شد.")


# --------------------------------------------------------------------------
# channel
# --------------------------------------------------------------------------
async def post_to_channel(text: str):
    ch = CFG.get("channel_id")
    if not ch:
        return
    for c in _split(text):
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


def main():
    global _app, CFG
    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    CFG = {
        "openai_key": os.environ.get("OPENAI_API_KEY"),
        "model": os.environ.get("MODEL", "gpt-4o-mini"),
        "channel_id": os.environ.get("CHANNEL_ID"),
        "admin_ids": [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()],
    }
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN not set in .env")

    _app = Application.builder().token(token).build()
    _app.add_handler(CommandHandler("start", start))
    _app.add_handler(CommandHandler("buy", buy))
    _app.add_handler(CommandHandler("analyze", analyze))
    _app.add_handler(CommandHandler("admin", admin_cmd))
    _app.add_handler(CommandHandler("stats", stats_cmd))
    _app.add_handler(CommandHandler("approve", approve_cmd))
    _app.add_handler(CommandHandler("revoke", revoke_cmd))
    _app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    _app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    _app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, profile_input))

    print("🤖 Bot started. Press Ctrl+C to stop.")
    _app.run_polling()


if __name__ == "__main__":
    main()
