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
import re
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
import brand as brand
import receipt_verify as receipt_verify

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
        "🔒 سقف سوالات رایگان امروزت پر شد (۳ سوال در روز).\n\n"
        "برای ادامه، اشتراک ویژه بگیر — تحلیل نامحدود + صدور اسناد حقوقی 💎\n"
        "از منوی «💎 اشتراک ویژه» شروع کن، یا /buy رو بزن."
    )


# --------------------------------------------------------------------------
# user-facing handlers
# --------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    handle = update.effective_user.username or "—"
    AP.ensure_user(uid, handle=handle, admin_ids=CFG.get("admin_ids"))

    await update.message.reply_text(
        f"سلام دوست من 🌿 خوش اومدی.\n\n"
        f"من اینجام که **بارِ حقوقی‌ات** رو سبک کنم — بدون قضاوت، بدون پیچیدگی. "
        f"هر چی تو ذهنت هست (**اجاره**، **طلاق**، **قرارداد**، **شکایت**، **ارث**، **کار** و...) راحت بگو؛ "
        f"من می‌فهمم و راهش رو می‌گم.\n\n"
        f"یه نکته صادقانه: من زیر نظر یه تیم حقوقی واقعی کار می‌کنم. "
        f"پشت من چندین **وکیل پایه یک دادگستری** و {brand.FIRM_NAME} که در تاریخ "
        f"{brand.FIRM_FOUNDED} تأسیس شده نشسته.\n\n"
        f"بزن بریم؟ اولین موضوعت رو بنویس، یا از منوی پایین انتخاب کن 👇"
    )
    set_profile(uid, context, consented=True)

    # persistent smart menu (inline keyboard) — row/column layout, item 3
    await update.message.reply_text(
        "📲 منوی سریع:",
        reply_markup=main_menu_keyboard(),
    )


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Smart inline keyboard — row/column layout with relevant emojis (item 3)."""
    buttons = [
        [InlineKeyboardButton("⚖️ تحلیل موضوع حقوقی", callback_data="act:analyze")],
        [InlineKeyboardButton("📄 صدور سند حقوقی", callback_data="act:draft")],
        [InlineKeyboardButton("💎 اشتراک ویژه", callback_data="act:buy")],
        [InlineKeyboardButton("👤 درباره مشاور و موسسه", callback_data="act:about")],
    ]
    return InlineKeyboardMarkup(buttons)


def doc_type_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard to pick a legal document type (step 1 of the draft flow)."""
    from telegram import InlineKeyboardButton
    rows = []
    for dt in DR.DOC_TYPES:
        rows.append([InlineKeyboardButton(f"📑 {dt}", callback_data=f"doctype:{dt}")])
    rows.append([InlineKeyboardButton("🔙 بازگشت به منو", callback_data="act:menu")])
    return InlineKeyboardMarkup(rows)


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline keyboard button presses."""
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    if data == "act:analyze":
        await q.message.reply_text("✍️ موضوع حقوقی خود را بنویسید؛ من خودم تحلیل می‌کنم.")
    elif data == "act:draft":
        if not AP.has_access(update.effective_user.id, CFG.get("admin_ids")):
            await q.message.reply_text("🔒 صدور سند ویژه کاربران دارای اشتراک فعال است. اول اشتراک بگیر (از منوی «💎 اشتراک ویژه»).")
            return
        await q.message.reply_text(
            "📑 **چه نوع سندی می‌خوای؟**\nیه مورد رو انتخاب کن — بعد ازت چند تا سوال ساده می‌پرسم تا سند رو دقیق برات بنویسم 👇",
            reply_markup=doc_type_keyboard(),
        )
    elif data == "act:menu":
        await q.message.reply_text("📲 منوی سریع:", reply_markup=main_menu_keyboard())
    elif data == "act:buy":
        await buy(update, context)
    elif data == "act:about":
        await q.message.reply_text(
            f"👤 **درباره مشاور و موسسه**\n\n"
            f"⚖️ {brand.ADVISOR_NAME} — {brand.ADVISOR_TITLE}\n"
            f"📞 تماس مستقیم: {brand.ADVISOR_PHONE} (معرفی به وکیل / اخذ مشاوره تلفنی)\n\n"
            f"🏛 {brand.FIRM_NAME}\n📅 تأسیس: {brand.FIRM_FOUNDED}\n\n"
            f"ما یه موسسه حقوقی با‌سابقه هستیم که از سال ۹۳ داره به مردم عادی و کسب‌وکارها "
            f"خدمت می‌کنه. این بات، راه سریعیه برای اینکه بدون هزینه‌ی اولیه و بدون معطلی، "
            f"بفهمی حق قانونی‌ت چیه و قدم بعدی‌ت چیه.\n\n"
            f"سوال حقوقی‌ت رو بپرس — من همین‌جا جواب می‌دم 🌿"
        )
    elif data.startswith("doctype:"):
        await _draft_type_chosen(update, context, data[8:])


async def _doctype_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Wrapper so PTB's CallbackQueryHandler (which passes only update/context)
    can forward the chosen doc type to _draft_type_chosen."""
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    await _draft_type_chosen(update, context, data[8:])


async def _draft_type_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE,
                              doc_type: str):
    """Step 2 of the draft flow: store the chosen type, then ask for facts."""
    uid = update.effective_user.id
    conv = context.chat_data.setdefault("conv", {"issue": "", "doc": "", "history": []})
    conv["draft_type"] = doc_type
    conv["draft_step"] = "awaiting_facts"

    # If the user already described a case earlier in this chat, reuse it.
    prior = (conv.get("issue") or "").strip()
    if prior:
        await update.callback_query.message.reply_text(
            f"✅ نوع سند: **{doc_type}**\n\n"
            f"موضوع قبلی‌ات رو پیدا کردم — همون رو پایه قرار بدم یا می‌خوای توضیح جدیدی بدی؟\n\n"
            f"اگه همون اوکیه، فقط بنویس «همون» تا سند رو بنویسم.\n"
            f"اگه می‌خوای اصلاح کنی، موضوع/طرف مقابل/مبلغ رو اینجا بنویس."
        )
    else:
        await update.callback_query.message.reply_text(
            f"✅ نوع سند: **{doc_type}**\n\n"
            f"حالا چند تا نکته رو بگو تا دقیق بنویسم (همه رو یه جا بنویس کافیه):\n"
            f"• موضوع/واقعه چیه؟\n"
            f"• طرف مقابل (خوانده/مخاطب) کیه؟\n"
            f"• چی می‌خوای (مبلغ/درخواست مشخص)؟\n"
            f"• مدرکی داری؟ (قرارداد، رسید، پیام...)"
        )


async def _draft_facts_received(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                 facts: str):
    """Step 3: generate the document from the gathered facts + prior case."""
    uid = update.effective_user.id
    conv = context.chat_data.setdefault("conv", {"issue": "", "doc": "", "history": []})
    doc_type = conv.get("draft_type") or DR.classify_doc_type(facts)
    # Combine prior case issue (if any) with the new facts for richer context.
    base = (conv.get("issue") or "").strip()
    issue_text = (base + "\n" + facts).strip() if base else facts

    await update.message.reply_text("📝 در حال تنظیم سند حقوقی...")

    coll = ensure_collection()
    loop = asyncio.get_event_loop()
    try:
        hits = await loop.run_in_executor(None, query_collection, coll, issue_text, 10)
        draft = await loop.run_in_executor(
            None, lambda: DR.draft_document(doc_type, issue_text, hits,
                                            openai_key=CFG.get("openai_key"),
                                            model=CFG.get("model", "gpt-4o-mini"),
                                            history=conv.get("history", []))
        )
    except Exception as e:
        err = str(e)
        if "insufficient_quota" in err or "RateLimitError" in err or "429" in err:
            await update.message.reply_text("⚠️ حساب OpenAI شارژ نشده یا سقف استفاده تمام شده. شارژ کنید.")
        else:
            await update.message.reply_text(f"⚠️ خطا در تنظیم سند: {err[:300]}")
        return

    # forward to channel with attribution
    handle = update.effective_user.username or "بدون یوزرنیم"
    report = (
        f"📄 سند حقوقی تولید شد\n━━━━━━━━━━━━━━━━\n"
        f"👤 کاربر: @{handle} | ID: {uid}\n"
        f"📑 نوع سند: {doc_type}\n━━━━━━━━━━━━━━━━\n{draft}\n\n"
        f"━━━━━━━━━━━━━━━━\n{brand.ADVISOR_SIGNATURE}"
    )
    await post_to_channel(report)
    AP.incr_analyses(uid)

    # send text + PDF option
    await update.message.reply_text(draft)
    context.chat_data["conv"]["draft_last"] = draft
    context.chat_data["conv"]["draft_step"] = "done"

    # post-draft menu
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 دریافت PDF", callback_data="draft:pdf")],
        [InlineKeyboardButton("✏️ ویرایش / تکمیل", callback_data="draft:edit")],
        [InlineKeyboardButton("📑 سند دیگه", callback_data="act:draft")],
        [InlineKeyboardButton("📲 منو", callback_data="act:menu")],
    ])
    await update.message.reply_text("گزینه‌های بعدی:", reply_markup=kb)


async def draft_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle post-draft menu (PDF / edit / new)."""
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    conv = context.chat_data.setdefault("conv", {})
    if data == "draft:pdf":
        draft = conv.get("draft_last", "")
        if not draft:
            await q.message.reply_text("⚠️ سندی برای PDF ندارم. اول سند رو بساز.")
            return
        pdf_path = os.path.join(tempfile.gettempdir(), f"draft_{update.effective_user.id}.pdf")
        try:
            if PDF.available():
                PDF.build_pdf(draft, f"سند حقوقی — {conv.get('draft_type','')}", pdf_path)
                with open(pdf_path, "rb") as f:
                    await q.message.reply_document(document=f, filename="سند_حقوقی.pdf",
                                                    caption="📄 نسخه PDF سند (با فرمت حقوقی).")
                os.unlink(pdf_path)
            else:
                await q.message.reply_text("ℹ️ کتابخانه PDF در دسترس نیست؛ متن سند همون بالاست.")
        except Exception as e:
            await q.message.reply_text(f"ℹ️ تولید PDF ناموفق بود ({str(e)[:150]}).")
    elif data == "draft:edit":
        await q.message.reply_text(
            "✏️ بگو چی رو اصلاح/اضافه کنم (مثلاً «مبلغ رو ۵۰ میلیون بنویس» یا «نام طرف رو اضافه کن») "
            "تا نسخهٔ جدید برات بزنم."
        )
        conv["draft_step"] = "editing"
    # note: "act:draft" (new document) is handled by menu_callback, not here



async def profile_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Text handler. Two modes:
       - If awaiting clarification answers (conv['awaiting']), fold the reply
         into the issue and run the analysis.
       - Otherwise hand the message straight to handle_text (the smart
         workflow). Name/phone capture was removed so the bot analyzes the
         user's first message immediately instead of mis-storing it as a name.
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
        hits = await loop.run_in_executor(None, query_collection, coll, st["issue"], 10)
        await _run_analysis(update, context, hits, st.get("history", []))
        return

    # Mode A2: user is in the document-draft flow -> gather facts / edit
    draft_step = st.get("draft_step")
    if draft_step == "awaiting_facts":
        # "همون" means reuse the prior case issue without new facts
        if text.strip() == "همون":
            facts = (st.get("issue") or "").strip()
        else:
            facts = text
        st["draft_step"] = None
        await _draft_facts_received(update, context, facts)
        return
    if draft_step == "editing":
        # Append the edit instruction to the last draft and regenerate
        st["draft_step"] = None
        prior = st.get("draft_last", "")
        extra = f"\n[ویرایش درخواستی کاربر]: {text}"
        await _draft_facts_received(update, context, (st.get("issue") or "") + extra)
        return

    # Mode A3: user is filling the subscription-buyer info form (item 7)
    if st.get("awaiting_buyer_info"):
        st["awaiting_buyer_info"] = False
        parts = [p.strip() for p in text.split("|")]
        # pad to 4 fields; national_id optional (defaults to "-")
        while len(parts) < 4:
            parts.append("-")
        full_name, last_name, mobile, national_id = parts[0], parts[1], parts[2], parts[3]
        AP.save_buyer_info(uid, full_name=full_name, last_name=last_name,
                           mobile=mobile, national_id=(national_id if national_id not in ("-", "") else ""))
        tier_id = st.get("pending_tier")
        tier = brand.tier_by_id(tier_id) if tier_id else None
        card = os.environ.get("BANK_CARD_NUMBER", "—— (کارت بانکی توسط ادمین ست می‌شود) ——")
        card_holder = os.environ.get("BANK_CARD_HOLDER", "موسسه حقوقی پدیدآوران عدالت")
        tier_label = tier["label"] if tier else "اشتراک"
        tier_months = tier["months"] if tier else 1
        await update.message.reply_text(
            f"✅ مشخصاتت ثبت شد. حالا برو پرداخت:\n\n"
            f"۱. مبلغ **{tier['price']:,} تومان** رو به کارت زیر واریز کن:\n"
            f"`{card}`\n👤 به نام: {card_holder}\n\n"
            f"۲. **عکس رسید رو دقیقاً همین‌جا (توی این چت) بفرست** — من هوشمندانه چک می‌کنم "
            f"و اگه مبلغ درست باشه، اشتراک {tier_label} ({tier_months} ماه) رو خودکار "
            f"برات فعال می‌کنم 🤖✨\n\n"
            f"اگه عکس کار نکرد، به @rezapilot پیام بده تا دستی فعال کنه."
        )
        return

    # Mode B: any other free text is the legal issue -> analyze it
    await handle_text(update, context)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User sent free text. Run the smart workflow: clarify -> (ask | analyze)."""
    uid = update.effective_user.id
    text = update.message.text.strip()

    # ensure the user record exists even if they never pressed /start
    AP.ensure_user(uid, handle=update.effective_user.username or "—",
                   admin_ids=CFG.get("admin_ids"))

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

    # Filter out trivial greetings / non-legal chatter so the bot doesn't
    # hang calling the LLM on "سلام". Require a minimum of real content.
    if len(text.strip()) < 4 or text.strip() in (
        "سلام", "درود", "خوبی", "سلام علیکم", "هاي", "hi", "hello", "سلام👋"
    ):
        await update.message.reply_text(
            "👋 سلام. من دستیار حقوقی موسسه پدیدآوران عدالت هستم.\n"
            "لطفاً **موضوع حقوقی** خود را بنویسید (مثلاً: «کارفرما ۴ ماه حقوقم "
            "را نپرداخته و قرارداد کتبی ندارم») تا تحلیلش کنم."
        )
        return

    await update.message.reply_text("🔎 در حال بررسی موضوع و جستجوی مواد قانونی...")

    coll = ensure_collection()
    loop = asyncio.get_event_loop()
    try:
        hits = await loop.run_in_executor(None, query_collection, coll, text, 10)
    except Exception as e:
        if "insufficient_quota" in str(e) or "RateLimitError" in str(e) or "429" in str(e):
            await update.message.reply_text(
                "⚠️ حساب OpenAI شارژ نشده یا سقف استفاده (quota) تمام شده.\nلطفاً در "
                "https://platform.openai.com روی Billing شارژ کنید، سپس دوباره پیام دهید."
            )
            return
        await update.message.reply_text(f"⚠️ خطا در جستجوی مواد: {str(e)[:200]}")
        return

    # Analyze directly (clarify step removed to avoid hangs; the deep analysis
    # prompt already asks for a complete, actionable opinion).
    try:
        await _run_analysis(update, context, hits, hist)
    except Exception as e:
        err = str(e)
        if "insufficient_quota" in err or "RateLimitError" in err or "429" in err:
            await update.message.reply_text(
                "⚠️ حساب OpenAI شارژ نشده یا سقف استفاده (quota) تمام شده.\nلطفاً شارژ کنید."
            )
            return
        await update.message.reply_text(f"⚠️ خطا در تحلیل: {err[:300]}")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc:
        return
    uid = update.effective_user.id

    # If the user previously picked a subscription tier and is now sending a
    # receipt image, verify it intelligently and (if valid) auto-activate.
    st = context.chat_data.setdefault("conv", {"issue": "", "doc": ""})
    pending_tier = st.get("pending_tier")
    if pending_tier:
        await _handle_receipt(update, context, doc, pending_tier)
        return

    # Otherwise: treat the document as a legal case file to analyze.
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


async def _handle_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE,
                          doc, pending_tier: str):
    """User sent a receipt image after picking a tier. Verify via vision."""
    tier = brand.tier_by_id(pending_tier)
    if not tier:
        await update.message.reply_text("⚠️ تیر انتخابی نامعتبر؛ لطفاً دوباره /buy را بزنید.")
        return
    uid = update.effective_user.id
    await update.message.reply_text("🔎 در حال بررسی عکس رسید با هوش مصنوعی...")

    # download to a temp file
    suffix = os.path.splitext(doc.file_name or ".jpg")[1] or ".jpg"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    adl = await context.bot.get_file(doc.file_id)
    await adl.download_to_drive(tmp.name)

    loop = asyncio.get_event_loop()
    try:
        verdict = await loop.run_in_executor(
            None, lambda: receipt_verify.verify_receipt_image(
                tmp.name, tier["price"],
                openai_key=CFG.get("openai_key"),
                model=CFG.get("model", "gpt-4o-mini"))
        )
    except Exception as e:
        os.unlink(tmp.name)
        await update.message.reply_text(
            f"⚠️ بررسی رسید با خطا مواجه شد: {str(e)[:200]}\n"
            "لطفاً رسید را دوباره بفرستید یا به @rezapilot اطلاع دهید."
        )
        return

    # forward the receipt to the channel/admin for oversight regardless of verdict
    try:
        with open(tmp.name, "rb") as f:
            await context.bot.send_document(
                chat_id=CFG.get("channel_id") or update.effective_user.id,
                document=f,
                caption=f"🧾 رسید اشتراک {tier['label']} از کاربر {uid}\n"
                        f"تشخیص مبلغ: {verdict.get('amount')}\n"
                        f"نتیجه: {'✅ تایید خودکار' if verdict['ok'] else '⏳ نیاز به تایید ادمین'}"
            )
    except Exception:
        pass
    os.unlink(tmp.name)

    if verdict["ok"]:
        AP.approve_purchase(uid, months=tier["months"])
        context.chat_data["conv"].pop("pending_tier", None)
        await update.message.reply_text(
            f"🎉 تایید شد! اشتراک {tier['label']} ({tier['months']} ماه) برایت فعال شد.\n"
            "حالا می‌تونی تحلیل نامحدود داشته باشی و سند حقوقی تنظیم کنی 💎"
        )
    else:
        # mark pending so admin can /approve later
        AP.request_purchase(uid, tier_id=tier["id"], months=tier["months"])
        await update.message.reply_text(
            f"⏳ مبلغ از عکس بخوبی تایید نشد ({verdict.get('reason')}).\n"
            "رسیدت برای ادمین ارسال شد و پس از تایید دستی، اشتراکت فعال می‌شه.\n"
            "اگر اشتباه بود، دوباره عکس واضح‌تری بفرست."
        )


async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    try:
        if AP.has_access(uid, CFG.get("admin_ids")):
            await update.message.reply_text("✅ شما در حال حاضر اشتراک فعال دارید.")
            return

        card = os.environ.get("BANK_CARD_NUMBER", "—— (کارت بانکی توسط ادمین ست می‌شود) ——")
        card_holder = os.environ.get("BANK_CARD_HOLDER", "موسسه حقوقی پدیدآوران عدالت")
        await update.message.reply_text(
            "💎 **اشتراک ویژه — تحلیل نامحدود + صدور اسناد**\n\n"
            "با اشتراک، بی‌نهایت تحلیل حقوقی + تنظیم سند (دادخواست/لایحه/قرارداد) داری.\n"
            "۳ سطح دسترسی داریم — هر چی طولانی‌تر، به‌صرفه‌تر 👇\n\n"
            "💡 پیشنهاد من: اشتراک ۶ ماهه! چون قیمتش از ۲ تا ۳ ماهه منصفانه‌تره و "
            "برای پرونده‌هایی که زمان می‌برن (مثل طلاق یا مطالبه طولانی) کاملاً می‌ارزه.\n\n"
            f"🏦 **واریز به کارت:**\n`{card}`\n👤 به نام: {card_holder}\n\n"
            "بعد از انتخاب سطح و کارت‌به‌کارت، **عکس رسید رو همین‌جا بفرست** — من "
            "هوشمندانه چک می‌کنم و اشتراکت رو خودکار فعال می‌کنم 🤖✨",
            reply_markup=brand.subscription_menu_keyboard(),
        )
    except Exception as e:
        print(f"[buy error] {e}")
        await update.message.reply_text(
            "⚠️ در باز کردن بخش اشتراک خطایی رخ داد. لطفاً چند لحظه بعد دوباره /buy را بزنید "
            "یا به @rezapilot پیام دهید."
        )


async def sub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User picked a subscription tier (sub:<id>)."""
    q = update.callback_query
    await q.answer()
    tier = brand.tier_by_id((q.data or "")[4:])
    if not tier:
        await q.message.reply_text("⚠️ گزینه نامعتبر.")
        return
    uid = update.effective_user.id
    # Flag pending purchase with the chosen tier so admin can activate + track.
    AP.request_purchase(uid, tier_id=tier["id"], months=tier["months"])
    # Remember the chosen tier in chat_data so the next receipt photo is verified.
    conv = context.chat_data.setdefault("conv", {})
    conv["pending_tier"] = tier["id"]
    # Now ask for the buyer's identity (item 7) before taking the receipt.
    conv["awaiting_buyer_info"] = True
    card = os.environ.get("BANK_CARD_NUMBER", "—— (کارت بانکی توسط ادمین ست می‌شود) ——")
    await q.message.reply_text(
        f"🛒 **اشتراک {tier['label']} — {tier['price']:,} تومان** انتخاب شد.\n\n"
        f"برای ثبت اشتراک و صدور فاکتور، لطفاً مشخصات خودت رو به این فرمت بفرست:\n\n"
        f"`نام | نام‌خانوادگی | شماره موبایل | کد ملی`\n\n"
        f"مثال:\n`علی | رضایی | 09123456789 | 0012345678`\n\n"
        f"(کد ملی اختیاریه — اگه نمی‌خوای بفرستی، خط تیره بزن: `علی | رضایی | 09123456789 | -`)\n\n"
        f"بعدش راهنمای پرداخت و ارسال رسید رو برات می‌فرستم 🤖✨"
    )


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
    hits = await loop.run_in_executor(None, query_collection, coll, issue, 10)
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
    # prepend the firm identity header (item 1 + 5.1: keep FIRM, drop advisor name)
    header = (
        f"⚖️ {brand.FIRM_NAME} | تأسیس {brand.FIRM_FOUNDED}\n"
        f"━━━━━━━━━━━━━━━━\n\n"
    )
    # Force the exact liability disclaimer (item 5.4) instead of the model's wording.
    final_text = _replace_disclaimer(summary, brand.DISCLAIMER_TEXT)
    await update.message.reply_text(header + final_text)

    # channel report with user info + advisor attribution
    prof = get_profile(uid, context)
    name = prof.get("name", "—")
    phone = prof.get("phone", "—")
    handle = update.effective_user.username or "بدون یوزرنیم"
    report = (
        f"📋 گزارش جدید از ایجنت حقوقی\n━━━━━━━━━━━━━━━━\n"
        f"👤 کاربر: {name} | 📞 {phone}\n🔗 @{handle} | ID: {uid}\n"
        f"━━━━━━━━━━━━━━━━\n📝 موضوع:\n{issue[:1500]}\n\n━━━━━━━━━━━━━━━━\n{opinion.raw}\n\n"
        f"━━━━━━━━━━━━━━━━\n{brand.ADVISOR_SIGNATURE}"
    )
    await post_to_channel(report)
    AP.incr_analyses(uid)

    # Show the daily free-question counter for trial users (and anyone not on
    # a paid plan) so they always know how many questions are left today.
    if not AP.has_access(uid, CFG.get("admin_ids")) or not AP.is_paid(uid):
        remaining = AP.trial_remaining(uid)
        if remaining > 0:
            await update.message.reply_text(
                f"✅ تحلیل رایگانت انجام شد. امروز {remaining} سوال رایگان دیگه داری 🌿"
            )
        else:
            await update.message.reply_text(
                "✅ تحلیل رایگان امروزت تموم شد. برای ادامه اشتراک بگیر (💎 اشتراک ویژه) — "
                "تحلیل نامحدود + صدور اسناد."
            )
    # reset conversation (but keep nothing; fresh start next time)
    context.chat_data["conv"] = {"issue": "", "doc": "", "history": [], "awaiting": False}
    # show the smart menu again
    await update.message.reply_text("📲 منوی سریع:", reply_markup=main_menu_keyboard())


_DISCLAIMER_RE = re.compile(
    r"⚠️\s*هشدار مسئولیت.*$",
    re.DOTALL | re.MULTILINE,
)


def _replace_disclaimer(text: str, disclaimer: str) -> str:
    """Strip any model-generated '⚠️ هشدار مسئولیت' block and append the exact,
    on-message disclaimer (item 5.4). Keeps output wording consistent."""
    cleaned = _DISCLAIMER_RE.sub("", text).strip()
    if cleaned.endswith("━━━━━━━━━━━━━━━━"):
        cleaned = cleaned[: -len("━━━━━━━━━━━━━━━━")].strip()
    return cleaned + "\n\n⚠️ " + disclaimer


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
        await update.message.reply_text(f"✅ اشتراک کاربر {uid} فعال شد.")
        # notify the user if possible
        try:
            u = AP.get_user(uid) or {}
            months = u.get("tier_months") or 1
            await _app.bot.send_message(chat_id=uid,
                text=f"🎉 اشتراک {months} ماهه شما فعال شد! حالا می‌توانید تحلیل‌های نامحدود داشته باشید و سند حقوقی تنظیم کنید 💎")
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


async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: export the subscription-buyers list as Excel (CSV) + PDF."""
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text("⏳ در حال تهیه گزارش خریداران...")
    buyers = AP.list_buyers()
    if not buyers:
        await update.message.reply_text("هنوز هیچ درخواست خریدی ثبت نشده.")
        return

    # Excel (CSV with BOM so Persian opens correctly in Excel)
    import csv
    csv_path = os.path.join(tempfile.gettempdir(), "buyers_export.csv")
    try:
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ردیف", "یوزرنیم", "آیدی", "نام", "نام خانوادگی", "موبایل",
                         "کد ملی", "وضعیت", "تیر", "تاریخ پرداخت", "اعتبار تا"])
            for i, b in enumerate(buyers, 1):
                w.writerow([i, b["handle"], b["user_id"], b["full_name"], b["last_name"],
                            b["mobile"], b["national_id"], b["access"], b["tier"],
                            b["paid_at"] or "—", b["paid_until"] or "—"])
        with open(csv_path, "rb") as f:
            await update.message.reply_document(document=f, filename="گزارش_خریداران.csv",
                                                caption="📊 خروجی اکسل (CSV) لیست خریداران اشتراک")
        os.unlink(csv_path)
    except Exception as e:
        await update.message.reply_text(f"⚠️ تولید اکسل ناموفق: {str(e)[:150]}")

    # PDF
    pdf_path = os.path.join(tempfile.gettempdir(), "buyers_export.pdf")
    try:
        if PDF.available():
            PDF.build_pdf(AP.export_buyers_text(), "گزارش خریداران اشتراک", pdf_path)
            with open(pdf_path, "rb") as f:
                await update.message.reply_document(document=f, filename="گزارش_خریداران.pdf",
                                                    caption="📄 خروجی PDF لیست خریداران اشتراک")
            os.unlink(pdf_path)
        else:
            await update.message.reply_text(AP.export_buyers_text())
    except Exception as e:
        await update.message.reply_text(f"⚠️ تولید PDF ناموفق: {str(e)[:150]}")


async def draft_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate a ready-to-use legal document from the current case context.
    Usage: /draft [نوع سند]   (e.g. /draft دادخواست)
    Paid users / admins only (documents are a paid feature)."""
    uid = update.effective_user.id
    if not AP.has_access(uid, CFG.get("admin_ids")):
        await update.message.reply_text(
            "🔒 صدور اسناد حقوقی ویژه کاربران دارای اشتراک فعال است.\n"
            "برای فعال‌سازی، از منوی «💎 اشتراک ویژه» یا /buy اقدام کن."
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
        hits = await loop.run_in_executor(None, query_collection, coll, issue, 10)
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
        # On Render's free plan there is no mounted disk; if the configured
        # DATA_DIR is not writable (e.g. it points at /data) fall back to a
        # writable temp dir so the bot does not crash with PermissionError.
        candidates = [data_dir, "/tmp/legal_agent", os.path.join(PROJECT_ROOT, "data")]
        for cdir in candidates:
            try:
                os.makedirs(cdir, exist_ok=True)
                # probe writability
                probe = os.path.join(cdir, ".write_test")
                with open(probe, "w") as f:
                    f.write("ok")
                os.remove(probe)
                data_dir = cdir
                break
            except (PermissionError, OSError):
                continue
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

    # Webhook mode: Telegram POSTs updates to /webhook and web_health enqueues
    # them. We do NOT call delete_webhook here (it would wipe the webhook we set
    # below). The app sets the webhook on startup via _tg.set_webhook.
    from telegram import Bot as _TG_Bot
    _tg = _TG_Bot(token)
    _app = Application.builder().token(token).build()
    _app.add_handler(CommandHandler("start", start))
    _app.add_handler(CommandHandler("buy", buy))
    _app.add_handler(CommandHandler("analyze", analyze))
    _app.add_handler(CommandHandler("admin", admin_cmd))
    _app.add_handler(CommandHandler("stats", stats_cmd))
    _app.add_handler(CommandHandler("approve", approve_cmd))
    _app.add_handler(CommandHandler("revoke", revoke_cmd))
    _app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    _app.add_handler(CommandHandler("export", export_cmd))
    _app.add_handler(CommandHandler("draft", draft_cmd))
    _app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    _app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, profile_input))
    _app.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^act:"))
    _app.add_handler(CallbackQueryHandler(sub_callback, pattern=r"^sub:"))
    _app.add_handler(CallbackQueryHandler(draft_callback, pattern=r"^draft:"))
    _app.add_handler(CallbackQueryHandler(_doctype_callback, pattern=r"^doctype:"))

    # Global error handler so the bot never silently hangs/crashes on an
    # unhandled exception (e.g. a slow OpenAI call) — the user always gets a
    # message instead of being stuck on "در حال بررسی...".
    async def _global_error(update: Update, context: ContextTypes.DEFAULT_TYPE):
        err = context.error
        # Surface the real cause so the user (and admin) can diagnose instead of
        # getting a generic "try again" that hides the root cause.
        detail = ""
        if err is not None:
            msg = str(err)
            if "OPENAI_API_KEY" in msg:
                detail = "\nعلت: کلید OpenAI روی سرور ست نشده (OPENAI_API_KEY در تنظیمات رندر خالی است)."
            elif "insufficient_quota" in msg or "RateLimitError" in msg or "429" in msg:
                detail = "\nعلت: حساب OpenAI شارژ نشده یا سقف استفاده تمام شده."
            elif "CHANNEL_ID" in msg:
                detail = "\nعلت: شناسه کانال ست نشده."
            else:
                detail = f"\nجزئیات: {msg[:200]}"
        try:
            if update and update.effective_message:
                await update.effective_message.reply_text(
                    "⚠️ متأسفانه خطایی پیش آمد." + detail +
                    "\nلطفاً چند لحظه صبر کنید و دوباره موضوع را بنویسید."
                )
        except Exception:
            pass
        print(f"[global error] {err}")

    _app.add_error_handler(_global_error)

    # In webhook mode the HTTP server (web_health) receives Telegram POSTs on
    # /webhook and enqueues them into the Application's update_queue. We do NOT
    # call run_polling (which caused 409 Conflict on Render) nor run_webhook
    # (which collides on the same port). Instead web_health owns the port and the
    # Application just processes the queue.
    # Use PTB's own run_webhook — the officially supported webhook server.
    # It binds $PORT, serves /webhook, parses updates, runs handlers, and
    # sets the webhook on Telegram itself during its bootstrap (no manual
    # set_webhook needed — calling it here causes flood-control errors).
    # No getUpdates polling -> no 409 Conflict on Render.
    public_url = os.environ.get("PUBLIC_URL", "").rstrip("/")
    if not public_url:
        print("⚠️ PUBLIC_URL not set; webhook not registered. Set PUBLIC_URL to "
              "https://<your-app>.onrender.com so Telegram can reach /webhook.")

    print("🤖 Bot started in WEBHOOK mode. Press Ctrl+C to stop.")
    _app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
        url_path="/webhook",
        webhook_url=f"{public_url}/webhook" if public_url else None,
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
