"""
drafter.py
=========
Generates ready-to-use Persian legal documents (دادخواست / لایحه دفاعیه /
اظهارنامه / شکوائیه / قرارداد) from the analyzed case facts + cited articles.

The agent first classifies the document type, then produces a well-structured
Persian draft using the retrieved legal articles as the citation backbone.

Anti-hallucination: all article references come from the retrieved hits, never
invented. Party names/amounts are taken verbatim from what the user provided.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

DRAFT_SYSTEM_PROMPT = """تو یک وکیل پایه یک دادگستری با‌تجربه‌ای که عین خودِ کاربر، ساده و روان حرف می‌زنه، ولی توی تنظیم سند سنگ تموم می‌ذاره.

وظیفه‌ت: بر اساس اطلاعاتی که مراجع داد و مواد قانونی که توی «منابع استخراج‌شده» اومده، یه سند حقوقی رسمی و آماده‌ی تسلیم به مرجع قضایی/ثبتی بنویس.

قواعد سخت (غیرقابل تخطی):
۱. همه استنادها فقط و فقط از «منابع استخراج‌شده» باشه. 🚫 هیچ ماده‌ای رو جعل یا از حافظه ننویس. اگه ماده‌ای توی منابع نبود، بگو «این بخش رو توی منابع فعلی پیدا نکردم، بهتره دقیق‌تر چک بشه» — ولی ساختار سند رو کامل نگه دار.
۲. ساختار سند (بسته به نوعش) دقیقاً همین بخش‌ها رو داشته باش:
   📄 نوع سند / مرجع صالح
   👤 مشخصات طرفین (خواهان/خوانده یا مطرح‌کننده/طرف مقابل)
   📝 خواسته (دقیق و عددی، جایی که عدد داره)
   📋 شرح مختصر دعوا / واقعه
   ⚖️ مستندات و دلایل (فهرست مدارک + استناد به مواد قانونی)
   🔚 تنظیم و امضاء
۳. لحن سند رسمی و بی‌طرفانه باشه (انگار داری به دادگاه تسلیم می‌کنی) — ولی جمله‌ها روان و بدون پیچیدگی بی‌خود.
۴. جاهایی که اطلاعات کافی نیست رو با [ــــــ] مشخص کن تا کاربر خودش پر کنه.
۵. تهش یه خط کوتاه بنویس: «این پیش‌نویس جهت راهنمایی اولیه است و پیش از تسلیم باید توسط وکیل بررسی شود.»

خروجی فقط متن سند باشه (بدون مقدمه و حاشیه اضافه)."""

DOC_TYPES = ["دادخواست", "لایحه دفاعیه", "اظهارنامه", "شکوائیه", "قرارداد", "درخواست تأمین دلیل"]


def _format_retrieval(hits: list[dict]) -> str:
    if not hits:
        return "منبعی در پایگاه دانش یافت نشد."
    return "\n\n".join(f"[{i}] {h['chunk']}" for i, h in enumerate(hits, 1))


def build_draft_prompt(doc_type: str, issue_text: str, hits: list[dict],
                        history: Optional[list[dict]] = None) -> str:
    parts = []
    if history:
        parts.append("=== گفت‌وگوی قبلی ===")
        for m in history:
            role = "مراجع" if m["role"] == "user" else "مشاور"
            parts.append(f"{role}: {m['content']}")
    parts.append(f"=== نوع سند درخواستی: {doc_type} ===")
    parts.append("=== منابع حقوقی استخراج‌شده (فقط از این‌ها استناد کن) ===")
    parts.append(_format_retrieval(hits))
    parts.append("\n=== شرح موضوع/واقعه از زبان مراجع ===")
    parts.append(issue_text.strip())
    parts.append(f"\nلطفاً سند «{doc_type}» را با رعایت قواعد تنظیم کن.")
    return "\n".join(parts)


def classify_doc_type(text: str) -> str:
    """Heuristic: pick the most likely doc type from free text, else دادخواست."""
    t = text.lower()
    for kw, dt in [
        ("لایحه", "لایحه دفاعیه"),
        ("دفاع", "لایحه دفاعیه"),
        ("اظهارنامه", "اظهارنامه"),
        ("شکایت", "شکوائیه"),
        ("شکوائیه", "شکوائیه"),
        ("قرارداد", "قرارداد"),
        ("تأمین دلیل", "درخواست تأمین دلیل"),
        ("دادخواست", "دادخواست"),
    ]:
        if kw in t:
            return dt
    return "دادخواست"


def draft_document(doc_type: str, issue_text: str, hits: list[dict],
                   openai_key: Optional[str] = None, model: str = "gpt-4o-mini",
                   history: Optional[list[dict]] = None) -> str:
    """Call the LLM and return the drafted document text."""
    key = openai_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY not set. Provide it or set env var.")
    from openai import OpenAI
    client = OpenAI(api_key=key)

    prompt = build_draft_prompt(doc_type, issue_text, hits, history)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": DRAFT_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        max_tokens=2400,
    )
    return resp.choices[0].message.content or ""


if __name__ == "__main__":
    import chromadb
    from seed_loader import load_records, build_collection, query_collection
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    client = chromadb.PersistentClient(path=os.path.join(PROJECT_ROOT, "data", "chroma"))
    recs = load_records()
    coll = build_collection(client, records=recs, openai_key=os.environ.get("OPENAI_API_KEY"))
    hits = query_collection(coll, "کارفرما ۴ ماه حقوق نداده", n_results=5)
    if os.environ.get("OPENAI_API_KEY"):
        print(draft_document("دادخواست", "کارفرما ۴ ماه حقوقم را نپرداخته", hits))
    else:
        print("Set OPENAI_API_KEY to run.")
