"""
brand.py
========
Brand identity + message templates for the legal-agent bot.

Single source of truth for the firm's identity and tone so every message the
bot sends stays consistent and on-brand.

Identity (from owner):
  - Firm:  موسسه حقوقی پدیدآوران عدالت
  - Founded: 1393/04/15
  - Advisor: a real, licensed attorney — وکیل پایه یک دادگستری AND chairman
    of the firm's board of directors (رئیس هیئت مدیره).
  - Tone: warm, plain-spoken, conversational (Persian colloquial), never stiff
    legalese. The bot speaks like a friendly, sharp lawyer you'd actually want
    to message at midnight — not like a statute book.
"""

from __future__ import annotations

FIRM_NAME = "موسسه حقوقی پدیدآوران عدالت"
FIRM_FOUNDED = "۱۳۹۳/۰۴/۱۵"
ADVISOR_NAME = "حجت فایقی"
ADVISOR_TITLE = "وکیل پایه یک دادگستری و رئیس هیئت مدیره " + FIRM_NAME
ADVISOR_PHONE = "09120849437"  # تماس مستقیم با مشاور جهت معرفی به وکیل / مشاوره تلفنی
ADVISOR_SIGNATURE = (
    f"⚖️ {ADVISOR_NAME} — {ADVISOR_TITLE}\n"
    f"🏛 {FIRM_NAME} (تأسیس {FIRM_FOUNDED})\n"
    f"📞 تماس: {ADVISOR_PHONE}"
)
# Short signature used inside analysis posts / channel reports.
ADVISOR_SHORT = f"{ADVISOR_NAME} | {FIRM_NAME}"

# Daily free allowance for the free tier (item 5): 3 questions/day.
FREE_DAILY_LIMIT = 3

# Exact liability disclaimer (item 5.4) — used to overwrite the model's version
# so the wording is always consistent and on-message.
DISCLAIMER_TEXT = (
    "این مشاوره صرفاً جنبه اطلاعاتی داره و به هیچ عنوان جایگزین مشاوره حقوقی "
    "تخصصی نیست. برای اقدامات قانونی، حتماً با وکلای موسسه ما مشورت کن."
)

# Subscription tiers (item 6). Each tier = months of access + a suggested price
# (toman). Owner can adjust prices; these are recommendations.
SUBSCRIPTION_TIERS = [
    {"id": "m3",  "label": "۳ ماهه",   "months": 3,  "price": 300_000, "emoji": "🥉"},
    {"id": "m6",  "label": "۶ ماهه",   "months": 6,  "price": 540_000, "emoji": "🥈"},
    {"id": "m12", "label": "۱۲ ماهه",  "months": 12, "price": 960_000, "emoji": "🥇"},
]


def subscription_menu_keyboard():
    """Inline keyboard rows for the three subscription tiers (item 6)."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    rows = []
    for t in SUBSCRIPTION_TIERS:
        btn = InlineKeyboardButton(
            f"{t['emoji']} اشتراک {t['label']} — {t['price']:,} تومان",
            callback_data=f"sub:{t['id']}",
        )
        rows.append([btn])
    rows.append([InlineKeyboardButton("💬 صحبت با ادمین", url="https://t.me/rezapilot")])
    return InlineKeyboardMarkup(rows)


def tier_by_id(tier_id: str) -> dict | None:
    for t in SUBSCRIPTION_TIERS:
        if t["id"] == tier_id:
            return t
    return None
