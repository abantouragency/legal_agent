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
  /draft      -> generate a ready-to-use legal document (دادخواست/لایحه/
                 اظهارنامه/شکوائیه/قرارداد) from the current case.
  (locked)    -> "message @rezapilot to buy"

Admin commands (ADMIN_IDS only):
  /admin      -> dashboard (stats + pending purchases)
  /approve ID -> grant paid access to a user
  /revoke ID  -> downgrade a user to trial
  /stats      -> quick stats
  /broadcast  -> (text after) send a message to all paid users via channel/DM
  /draft <نوع> -> generate a legal document (admin/paid only)

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

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler,
)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from seed_loader import load_records, build_collection, query_collection
from doc_processor import process_document
from lawyer_agent import ask_lawyer
import admin_panel as AP
import drafter as DR
import doc_pdf as PDF

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
        "⚖️ **موسسه حقوقی پدیدآوران عدالت**\n"
        "🤖 دستیار و مشاور حقوقی هوشمند\n\n"
        "با سلام و احترام؛ من دستیار هوشمند موسسه حقوقی «پدیدآوران عدالت» هستم. "
        "وظیفه من پاسخگویی دقیق، سریع و مبتنی بر قوانین جمهوری اسلامی ایران به "
        "پرسش‌های حقوقی شماست.\n\n"
        "🧠 من چگونه کار می‌کنم:\n"
        "• موضوع شما را بر اساس قوانین مدنی، کیفری، آیین دادرسی، کار، تجارت و "
        "مسئولیت مدنی تحلیل می‌کنم.\n"
        "• اگر اطلاعات موضوع ناقص باشد، خودم سوالات تکمیلی می‌پرسم تا دقیق‌ترین "
        "تحلیل را ارائه دهم.\n"
        "• موارد قانونی را با ذکر دقیق عنوان قانون و شماره ماده نقل می‌کنم.\n"
        "• راهکارهای عملیاتی گام‌به‌گام، برآورد هزینه/زمان و ریسک‌های پرونده را "
        "برایتان بازگو می‌کنم.\n\n"
        "⚠️ نکته مهم: پاسخ‌های من «مشاوره حقوقی اولیه» هستند و جایگزین مراجعه حضوری "
        "به وکیل دادگستری و بررسی پرونده اصلی نمی‌شوند.\n\n"
        "🎁 دوره آزمایشی: ۱ تحلیل رایگان. پس از آن برای ادامه /buy را بزنید.\n\n"
        "برای شروع:\n"
        "۱. نام و شماره تماس را (اختیاری) بفرستید یا «رد» بنویسید.\n"
        "۲. موضوع حقوقی خود را مستقیماً بنویسید؛ من خودم ادامه می‌دهم.\n"
        "۳. اگر سوال تکمیلی پرسیدم، پاسخ دهید.\n"
        "۴. مستندات (عکس/PDF) را بفرستید (اختیاری).\n\n"
        "🌐 کانال موسسه: @Padid_Avaran_Edalat"
    )
    set_profile(uid, context, consented=True)

    # persistent smart menu (inline keyboard)
    await update.message.reply_text(
        "📲 منوی سریع:",
        reply_markup=main_menu_keyboard(),
    )


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Smart inline keyboard shown to users."""
    buttons = [
        [InlineKeyboardButton("⚖️ تحلیل موضوع حقوقی", callback_data="act:analyze")],
        [InlineKeyboardButton("📄 صدور سند حقوقی", callback_data="act:draft")],
        [InlineKeyboardButton("🛒 خرید دسترسی", callback_data="act:buy")],
        [InlineKeyboardButton("🌐 کانال موسسه", url="https://t.me/Padid_Avaran_Edalat")],
    ]
    return InlineKeyboardMarkup(buttons)


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline keyboard button presses."""
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    if data == "act:analyze":
        await q.message.reply_text("✍️ موضوع حقوقی خود را بنویسید؛ من خودم تحلیل می‌کنم.")
    elif data == "act:draft":
        if AP.has_access(update.effective_user.id, CFG.get("admin_ids")):
            await q.message.reply_text("📑 نوع سند را بنویسید یا دستور بزنید:\n/draft دادخواست")
        else:
            await q.message.reply_text("🔒 صدور سند ویژه کاربران فعال است. اول /buy را بزنید.")
    elif data == "act:buy":
        await buy(update, context)



async def profile_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Text handler. Two modes:
       - If awaiting clarification answers (conv['awaiting']), fold the reply
         into the issue and run the analysis.
       - Else collect name/phone on first interaction, then hand to handle_text.
    """
    text = update.message.text.strip()
    uid = update.effective_user.id

    st = context.chat_data.setdefault("conv", {"issue": "", "doc": "",
                                               "history": [], "awaiting": False})

    # Mode A: user is answering the clarification questions -> analyze now
    if st.get("awaiting"):
        st["issue"] = (st["issue"] + "\n" + text).strip()
        st["awaiting"] = False
        coll = ensure_collection()
        loop = asyncio.get_event_loop()
        hits = await loop.run_in_executor(None, query_collection, coll, st["issue"], 6)
        await _run_analysis(update, context, hits, st.get("history", []))
        return

    # Mode B: initial name/phone capture
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
    """User sent free text. Run the smart workflow: clarify -> (ask | analyze)."""
    uid = update.effective_user.id
    text = update.message.text.strip()

    # preserve conversation history for "remembering" context
    st = context.chat_data.setdefault("conv", {"issue": "", "doc": "",
                                               "history": [], "awaiting": False})
    hist = st.setdefault("history", [])
    # append the user's previous issue + this message as history for LLM context
    if st.get("issue"):
        hist.append({"role": "user", "content": st["issue"]})
    st["issue"] = (st["issue"] + "\n" + text).strip()

    if not AP.has_access(uid, CFG.get("admin_ids")):
        if not AP.consume_trial(uid):
            await update.message.reply_text(_access_blocked_message())
            return

    await update.message.reply_text("🔎 در حال بررسی موضوع و جستجوی مواد قانونی...")

    coll = ensure_collection()
    loop = asyncio.get_event_loop()
    try:
        hits = await loop.run_in_executor(None, query_collection, coll, text, 6)
    except Exception as e:
        if "insufficient_quota" in str(e) or "RateLimitError" in str(e) or "429" in str(e):
            await update.message.reply_text(
                "⚠️ حساب OpenAI شارژ نشده یا سقف استفاده (quota) تمام شده.\nلطفاً در "
                "https://platform.openai.com روی Billing شارژ کنید، سپس دوباره پیام دهید."
            )
            return
        raise

    try:
        cl = await loop.run_in_executor(
            None, lambda: clarify(text, hits, doc_text=st.get("doc") or None,
                                  history=hist, openai_key=CFG.get("openai_key"),
                                  model=CFG.get("model", "gpt-4o-mini"))
        )
    except Exception as e:
        if "insufficient_quota" in str(e) or "RateLimitError" in str(e) or "429" in str(e):
            await update.message.reply_text(
                "⚠️ حساب OpenAI شارژ نشده یا سقف استفاده (quota) تمام شده.\nلطفاً شارژ کنید."
            )
            return
        await update.message.reply_text(f"⚠️ خطا در پردازش: {str(e)[:200]}")
        return

    if cl.needs_info and cl.questions:
        # ask targeted questions, keep the issue + history, await answers
        st["awaiting"] = True
        q_text = "\n".join(f"❓ {i}. {q}" for i, q in enumerate(cl.questions, 1))
        await update.message.reply_text(
            f"📋 موضوع شما دریافت شد (نوع پرونده احتمالی: {cl.case_type or 'نامشخص'}).\n"
            f"برای تحلیل دقیق‌تر، لطفاً به این سوالات پاسخ دهید:\n\n{q_text}\n\n"
            f"پس از پاسخ، تحلیل کامل را دریافت خواهید کرد."
        )
        return

    # enough info -> analyze now
    await _run_analysis(update, context, hits, hist)


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
    """Manually triggered analysis via /analyze (uses current conv state)."""
    st = context.chat_data.get("conv", {"issue": "", "doc": "", "history": []})
    issue = st.get("issue", "").strip()
    if not issue:
        await update.message.reply_text(
            "اول موضوع خود را بنویسید، سپس /analyze را بزنید.\n"
            "یا مستقیماً موضوع را بنویسید تا ایجنت خودش تحلیل کند."
        )
        return
    coll = ensure_collection()
    loop = asyncio.get_event_loop()
    hits = await loop.run_in_executor(None, query_collection, coll, issue, 6)
    await _run_analysis(update, context, hits, st.get("history", []))


async def _run_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE,
                        hits: list[dict], history: list[dict]):
    """Shared analysis routine: runs the LLM, renders deep output privately,
    posts the full report to the channel, updates stats."""
    uid = update.effective_user.id
    st = context.chat_data.get("conv", {"issue": "", "doc": "", "history": []})
    issue = st.get("issue", "").strip()

    await update.message.reply_text("⚖️ در حال تدوین تحلیل حقوقی جامع...")

    loop = asyncio.get_event_loop()
    try:
        opinion = await loop.run_in_executor(
            None, lambda: ask_lawyer(issue, hits, doc_text=st.get("doc") or None,
                                     openai_key=CFG.get("openai_key"),
                                     model=CFG.get("model", "gpt-4o-mini"),
                                     history=history)
        )
    except Exception as e:
        err = str(e)
        if "insufficient_quota" in err or "RateLimitError" in err or "429" in err:
            await update.message.reply_text(
                "⚠️ حساب OpenAI شارژ نشده یا سقف استفاده (quota) تمام شده.\nلطفاً شارژ کنید."
            )
        else:
            await update.message.reply_text(f"⚠️ خطا در فراخوانی مدل: {err[:300]}")
        return

    # private summary (truncated) + full report in channel
    summary = opinion.raw if len(opinion.raw) <= 4000 else opinion.raw[:4000] + "\n...(ادامه در کانال)"
    await update.message.reply_text(summary)

    # channel report with user info
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
    # reset conversation (but keep nothing; fresh start next time)
    context.chat_data["conv"] = {"issue": "", "doc": "", "history": [], "awaiting": False}
    # show the smart menu again
    await update.message.reply_text("📲 منوی سریع:", reply_markup=main_menu_keyboard())


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


async def draft_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate a ready-to-use legal document from the current case context.
    Usage: /draft [نوع سند]   (e.g. /draft دادخواست)
    Paid users / admins only (documents are a paid feature)."""
    uid = update.effective_user.id
    if not AP.has_access(uid, CFG.get("admin_ids")):
        await update.message.reply_text(
            "🔒 صدور اسناد حقوقی ویژه کاربران فعال (پرداخت‌شده) است.\n"
            "برای فعال‌سازی /buy را بزنید و پس از تایید ادمین استفاده کنید."
        )
        return

    st = context.chat_data.get("conv", {"issue": "", "doc": "", "history": []})
    issue = st.get("issue", "").strip()
    if not issue:
        await update.message.reply_text(
            "اول موضوع حقوقی خود را بنویسید (تا ایجنت تحلیل/اطلاعات کافی داشته باشد)، "
            "سپس /draft را بزنید.\nمثال: /draft دادخواست"
        )
        return

    doc_type = " ".join(context.args).strip() or DR.classify_doc_type(issue)
    await update.message.reply_text(f"📝 در حال تنظیم سند «{doc_type}»...")

    coll = ensure_collection()
    loop = asyncio.get_event_loop()
    try:
        hits = await loop.run_in_executor(None, query_collection, coll, issue, 6)
        draft = await loop.run_in_executor(
            None, lambda: DR.draft_document(doc_type, issue, hits,
                                            openai_key=CFG.get("openai_key"),
                                            model=CFG.get("model", "gpt-4o-mini"),
                                            history=st.get("history", []))
        )
    except Exception as e:
        err = str(e)
        if "insufficient_quota" in err or "RateLimitError" in err or "429" in err:
            await update.message.reply_text("⚠️ حساب OpenAI شارژ نشده یا سقف استفاده تمام شده. شارژ کنید.")
        else:
            await update.message.reply_text(f"⚠️ خطا در تنظیم سند: {err[:300]}")
        return

    # post full draft to channel too (with attribution)
    prof = get_profile(uid, context)
    name = prof.get("name", "—")
    handle = update.effective_user.username or "بدون یوزرنیم"
    report = (
        f"📄 سند حقوقی تولید شد\n━━━━━━━━━━━━━━━━\n"
        f"👤 کاربر: {name} | @{handle} | ID: {uid}\n"
        f"📑 نوع سند: {doc_type}\n━━━━━━━━━━━━━━━━\n{draft}"
    )
    await post_to_channel(report)
    AP.incr_analyses(uid)

    # send the user a PDF copy (RTL Persian) + fall back to text if unavailable
    pdf_path = os.path.join(tempfile.gettempdir(), f"draft_{uid}_{abs(hash(doc_type))}.pdf")
    try:
        if PDF.available():
            PDF.build_pdf(draft, f"{doc_type} — {name}", pdf_path)
            with open(pdf_path, "rb") as f:
                await update.message.reply_document(
                    document=f,
                    filename=f"{doc_type}.pdf",
                    caption="📄 نسخه PDF سند (با فرمت حقوقی).",
                )
            os.unlink(pdf_path)
            return
        else:
            await update.message.reply_text("ℹ️ کتابخانه PDF در دسترس نیست؛ متن سند ارسال شد.")
    except Exception as e:
        await update.message.reply_text(f"ℹ️ تولید PDF ناموفق بود ({str(e)[:150]})؛ متن سند ارسال شد.")

    # send the draft to the user (split if long)
    for chunk in _split(draft, 4000):
        await update.message.reply_text(chunk)



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
    """Build (or reuse) the chroma collection.

    If the persisted collection has fewer documents than the current corpus
    (e.g. new seed laws were added), it is force-rebuilt so the new articles
    become searchable without the user manually deleting data/chroma.

    The chroma path honors DATA_DIR (set to /data on Render) so the vector DB
    persists across restarts on the mounted disk.
    """
    global COLLECTION
    if COLLECTION is None:
        import chromadb
        data_dir = os.environ.get("DATA_DIR", os.path.join(PROJECT_ROOT, "data"))
        chroma_path = os.path.join(data_dir, "chroma")
        os.makedirs(chroma_path, exist_ok=True)
        client = chromadb.PersistentClient(path=chroma_path)
        recs = load_records()
        force = False
        try:
            existing = client.get_collection(name="iran_law")
            if existing.count() < len(recs):
                force = True  # corpus grew -> rebuild to include new laws
        except Exception:
            pass
        COLLECTION = build_collection(client, records=recs,
                                        openai_key=CFG.get("openai_key"),
                                        force_rebuild=force)
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

    # start a tiny health server so PaaS platforms (Render etc.) stay "healthy"
    try:
        import web_health
        web_health.start_health_server()
    except Exception as e:
        print(f"health server failed to start: {e}")

    _app = Application.builder().token(token).build()
    _app.add_handler(CommandHandler("start", start))
    _app.add_handler(CommandHandler("buy", buy))
    _app.add_handler(CommandHandler("analyze", analyze))
    _app.add_handler(CommandHandler("admin", admin_cmd))
    _app.add_handler(CommandHandler("stats", stats_cmd))
    _app.add_handler(CommandHandler("approve", approve_cmd))
    _app.add_handler(CommandHandler("revoke", revoke_cmd))
    _app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    _app.add_handler(CommandHandler("draft", draft_cmd))
    _app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    _app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, profile_input))
    _app.add_handler(CallbackQueryHandler(menu_callback))

    print("🤖 Bot started. Press Ctrl+C to stop.")
    _app.run_polling()


if __name__ == "__main__":
    main()
