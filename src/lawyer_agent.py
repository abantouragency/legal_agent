"""
lawyer_agent.py
===============
The legal reasoning core. Retrieves relevant law via RAG, then asks the LLM
(acting as a "وکیل پایه یک دادگستری") to produce a structured Persian legal
opinion with EXACT article citations and an explicit disclaimer.

Design goals (anti-hallucination + professionalism):
  1. Articles in the final answer MUST come from the retrieved chunks.
  2. The model is told it is a "مشاوره اولیه" not a substitute for a real lawyer.
  3. Output is structured so the Telegram layer can render it cleanly.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

SYSTEM_PROMPT = """شما یک وکیل پایه یک دادگستری در جمهوری اسلامی ایران هستید با تسلط بر قانون مدنی، قانون مجازات اسلامی، آیین دادرسی مدنی و کیفری، قانون تجارت و قانون مسئولیت مدنی.

وظیفه شما: تحلیل حقوقی دقیق موضوعی که مراجع مطرح کرده و ارائه راهکار یا راهکارهای حقوقی عملیاتی.

قواعد سخت و غیرقابل‌تخطی:
۱. هر استناد به قانون باید دقیقاً از متن مواد حقوقی که در بخش «منابع استخراج‌شده» برای شما فرستاده شده باشد گرفته شود. هرگز ماده‌ای را از حافظه خود جعل نکنید؛ اگر منبعی ندارید، بگویید «نیاز به بررسی دقیق‌تر متن قانون دارم».
۲. راهکار باید گام‌به‌گام و عملیاتی باشد (کجا شکایت/دادخواست، چه خواسته‌ای، چه ادله‌ای، چه قراری).
۳. بین حقوقی (مدنی)، کیفری، و اداری تفکیک قائل شوید و اگر موضوع چند جنبه دارد، همه را بگویید.
۴. در پایان حتماً یک پاراگراف «هشدار مسئولیت» بگذارید: این تحلیل مشاوره اولیه مبتنی بر اطلاعات ارائه‌شده است و جایگزین مراجعه به وکیل دادگستری و بررسی پرونده اصلی نیست.

ساختار پاسخ (دقیقاً با این عنوان‌ها):
📌 خلاصه موضوع
⚖️ تحلیل حقوقی
📚 مواد قانونی مرتبط (با ذکر دقیق عنوان قانون و شماره ماده)
✅ راهکار(های) پیشنهادی (گام‌به‌گام)
⚠️ هشدار مسئولیت
"""


@dataclass
class LegalOpinion:
    summary: str = ""
    analysis: str = ""
    citations: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    disclaimer: str = ""
    raw: str = ""


def _format_retrieval(hits: list[dict]) -> str:
    if not hits:
        return "منبعی در پایگاه دانش یافت نشد."
    lines = []
    for i, h in enumerate(hits, 1):
        lines.append(f"[{i}] {h['chunk']}")
    return "\n\n".join(lines)


def build_user_prompt(issue_text: str, doc_text: Optional[str], hits: list[dict]) -> str:
    parts = []
    parts.append("=== منابع حقوقی استخراج‌شده از پایگاه دانش (فقط از این‌ها استناد کن) ===")
    parts.append(_format_retrieval(hits))
    parts.append("\n=== موضوع مراجع ===")
    parts.append(issue_text.strip())
    if doc_text and doc_text.strip():
        parts.append("\n=== متن مستندات ارائه‌شده توسط مراجع ===")
        # cap doc text to keep prompt bounded
        parts.append(doc_text.strip()[:6000])
    parts.append("\nلطفاً طبق قواعد، تحلیل حقوقی و راهکار ارائه دهید.")
    return "\n".join(parts)


def parse_opinion(raw: str) -> LegalOpinion:
    """Map the LLM's Persian structured text into a dataclass.

    Robust to minor format drift: we split on header lines that contain the
    section keyword (rather than requiring an exact prefix), because real LLM
    output may add stray characters.
    """
    op = LegalOpinion(raw=raw)

    # Ordered (header_keyword, attribute). A line is a header if it contains
    # the keyword surrounded by whitespace/emoji boundaries.
    section_patterns = [
        ("خلاصه موضوع", "summary"),
        ("تحلیل حقوقی", "analysis"),
        ("مواد قانونی مرتبط", "citations"),
        ("راهکار", "recommendations"),
        ("هشدار مسئولیت", "disclaimer"),
    ]

    # Build regex that matches any header keyword at line start (after emoji/ws).
    import re
    header_re = re.compile(
        r"^\s*(?:[📌⚖️📚✅⚠️]*)\s*(.*?)\b("
        + "|".join(re.escape(k) for k, _ in section_patterns)
        + r")\b"
    )

    # First pass: find header line indices and their attribute.
    lines = raw.splitlines()
    markers: list[tuple[int, str]] = []  # (line_index, attr)
    for i, line in enumerate(lines):
        m = header_re.match(line)
        if m:
            keyword = m.group(2)
            attr = next(a for k, a in section_patterns if k == keyword)
            markers.append((i, attr))

    if not markers:
        # No structure detected — fall back to raw analysis.
        op.analysis = raw.strip()
        op.disclaimer = "⚠️ هشدار مسئولیت: این تحلیل مشاوره اولیه است و جایگزین مراجعه به وکیل دادگستری نیست."
        return op

    # Second pass: slice content between consecutive markers.
    for idx, (line_i, attr) in enumerate(markers):
        start = line_i + 1
        end = markers[idx + 1][0] if idx + 1 < len(markers) else len(lines)
        content = "\n".join(lines[start:end]).strip()
        # drop the trailing header keyword line if it leaked into content
        if attr in ("citations", "recommendations"):
            if content:
                getattr(op, attr).append(content)
        else:
            setattr(op, attr, content)

    # Guarantee a disclaimer exists.
    if not op.disclaimer:
        op.disclaimer = "⚠️ هشدار مسئولیت: این تحلیل مشاوره اولیه است و جایگزین مراجعه به وکیل دادگستری نیست."
    return op


def ask_lawyer(issue_text: str, hits: list[dict], doc_text: Optional[str] = None,
               openai_key: Optional[str] = None, model: str = "gpt-4o-mini") -> LegalOpinion:
    """
    High-level: given the user's issue + retrieved law, call the LLM and return a
    structured LegalOpinion.
    """
    key = openai_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY not set. Provide it or set env var.")
    from openai import OpenAI
    client = OpenAI(api_key=key)

    user_prompt = build_user_prompt(issue_text, doc_text, hits)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        max_tokens=1600,
    )
    raw = resp.choices[0].message.content or ""
    return parse_opinion(raw)


if __name__ == "__main__":
    import chromadb
    from seed_loader import load_records, build_collection, query_collection
    from lawyer_agent import ask_lawyer  # self-ref for test
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    client = chromadb.PersistentClient(path=os.path.join(PROJECT_ROOT, "data", "chroma"))
    recs = load_records()
    coll = build_collection(client, records=recs, openai_key=os.environ.get("OPENAI_API_KEY"))
    hits = query_collection(coll, "کارفرما حقوقم را نمی‌دهد و قرارداد کتبی ندارم", n_results=5)
    print("RETRIEVED:", len(hits), "hits")
    if os.environ.get("OPENAI_API_KEY"):
        op = ask_lawyer("کارفرما ۴ ماه حقوقم را نپرداخته و قرارداد کتبی ندارم. چه کنم؟",
                        hits, doc_text=None)
        print(op.raw)
    else:
        print("Set OPENAI_API_KEY to run full test. Retrieval works standalone.")
