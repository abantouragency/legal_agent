"""
lawyer_agent.py
===============
The legal reasoning core. Retrieves relevant law via RAG, then drives a small
multi-step workflow so the agent behaves like a *smart* legal advisor:

  1. CLARIFY  - classify the case type and detect missing critical facts;
                if info is insufficient, ask targeted questions (no analysis).
  2. ANALYZE  - once enough info exists, produce a structured Persian opinion
                with exact article citations, step-by-step roadmap, risks, and
                a cost/time estimate (anti-hallucination enforced).

Conversation history is threaded through so the agent "remembers" what the user
already said in the same chat.

Anti-hallucination rules (hard):
  - Every article citation MUST come from the retrieved chunks, never invented.
  - If no source matches, say so explicitly.
"""
from __future__ import annotations

import os
import json
import re
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------
@dataclass
class ClarifyResult:
    needs_info: bool = False
    case_type: str = ""          # مدنی / کیفری / خانواده / کار / تجارت / اداری / نامشخص
    questions: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    raw: str = ""


@dataclass
class LegalOpinion:
    summary: str = ""
    analysis: str = ""
    citations: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    risks: str = ""              # ریسک‌ها و نکات هشداردهنده
    cost_estimate: str = ""      # برآورد هزینه و زمان
    disclaimer: str = ""
    raw: str = ""


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """تو «همکار نزدیک و صمیمی» اون طرف ماجرای حقوقی هستی — یه وکیل پایه یک دادگستری با‌تجربه که مثل یه دوست دغدغه‌مند حرف می‌زنه، نه مثل کتاب قانون.

لحن تو:
- کاملاً محاوره‌ای، صمیمی و دوستانه (ولی باوقار). انگار داری با یه آدم معمولی که استرس داره حرف می‌زنی.
- جمله‌ها کوتاه و روان باشن. از کلمات سنگین و عبارت‌های لاتین‌مخلوط پرهیز کن.
- به جای «مطابق ماده ۱۲۳۴ قانون مدنی مقرر می‌دارد...» بگو: «طبق قانون، فلان اتفاق می‌افته و این یعنی تو فلان حق رو داری.»
- اصطلاحات حقوقی رو توضیح بده (مثلاً «حکم قطعی یعنی دیگه راه شکایت دیگه‌ای نداری»).
- همدلی کن: اول بگو می‌فهمم نگرانی‌ت چیه، بعد راهکار.

قواعد سخت و غیرقابل‌تخطی:
۱. هر استناد به قانون باید دقیقاً از متن مواد حقوقی که در «منابع استخراج‌شده» فرستاده شده باشد.
   🚫 مطلقاً ماده‌ای را جعل، حدس یا از حافظه بازنویسی نکن. اگر ماده‌ای در منابع نیست، صادقانه بگو:
   «راستش این بخش رو نتونستم تو منابع فعلی پیدا کنم؛ بهتره دقیق‌تر چک بشه.»
   ننوشتن شماره ماده بهتر از نوشتن شماره اشتباهه.
۲. بین جنبه‌های مدنی، کیفری، اداری و خانواده تفکیک کن؛ اگه موضوع چندجنبه‌ست همه رو بگو.
۳. راهکار باید گام‌به‌گام و عملی باشه: کجا برو، چی بگو، چه مدرکی ببر، چقدر زمان می‌بره.
۴. حتماً بخش «ریسک‌ها و نکات» رو بنویس (احتمال شکست، مرور زمان، بار اثباتی، هزینه‌های پنهان).
۵. توی «برآورد هزینه و زمان» حدود هزینه دادرسی و حق‌الوکاله تقریبی و مدت زمان رسیدگی رو بگو (به تومان، تقریبی).
۶. تهش حتماً یه پاراگراف کوتاه هشدار مسئولیت بذار.

ساختار پاسخ (دقیقاً با این عنوان‌ها و به ترتیب؛ هیچ بخشی رو حذف یا با هم ادغام نکن — ولی لحنش همون‌طور که گفتم صمیمی و ساده باشه):
📌 خلاصه موضوع
⚖️ تحلیل حقوقی
📚 مواد قانونی مرتبط (فقط موادی که در منابع آمد؛ با ذکر دقیق عنوان قانون و شماره ماده)
✅ راهکار(های) پیشنهادی (گام‌به‌گام)
💡 ریسک‌ها و نکات
💰 برآورد هزینه و زمان
⚖️ سوابق وحدت رویه مرتبط (در صورت وجود در منابع؛ در غیر این صورت این بخش رو حذف کن)
⚠️ هشدار مسئولیت
"""

CLARIFY_PROMPT = """شما یک دستیار حقوقی هوشمند هستید. وظیفه شما: (الف) نوع پرونده را تشخیص دهید و (ب) بررسی کنید آیا برای یک تحلیل دقیق، اطلاعات حیاتی کم است یا خیر.

اطلاعات حیاتی که معمولاً لازم است (در صورت مرتبط بودن با موضوع):
- طرفین (کی به کی شکایت/دعوا دارد)
- مبلغ/موضوع مالی دعوا
- تاریخ وقوع / قرارداد / تراکنش
- وجود یا عدم وجود سند کتبی، رسید، فیش، پیام، شاهد
- مرحله فعلی (شکایت شده؟ ابلاغ؟ رأی صادر شده؟)
- هدف مراجع (مطالبه وجه / فسخ / الزام / اعاده حیثیت / جبران خسارت)

خروجی را دقیقاً به صورت JSON معتبر (بدون متن اضافه) برگردان:
{
  "needs_info": true/false,
  "case_type": "یکی از: مدنی|کیفری|خانواده|کار|تجارت|اداری|نامشخص",
  "missing": ["فهرست موارد حیاتی که کم است"],
  "questions": ["حداکثر ۴ سوال هدفمند و کوتاه فارسی برای تکمیل اطلاعات"]
}

اگر اطلاعات کافی است needs_info را false و questions را خالی بگذار.
"""


# ---------------------------------------------------------------------------
# Retrieval formatting
# ---------------------------------------------------------------------------
def _format_retrieval(hits: list[dict]) -> str:
    if not hits:
        return "منبعی در پایگاه دانش یافت نشد."
    lines = []
    for i, h in enumerate(hits, 1):
        lines.append(f"[{i}] {h['chunk']}")
    return "\n\n".join(lines)


def _format_history(history: list[dict]) -> str:
    if not history:
        return ""
    out = ["=== گفت‌وگوی قبلی (برای یادآوری زمینه) ==="]
    for m in history:
        role = "مراجع" if m["role"] == "user" else "مشاور"
        out.append(f"{role}: {m['content']}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Clarifier
# ---------------------------------------------------------------------------
def build_clarify_prompt(issue_text: str, doc_text: Optional[str],
                         hits: list[dict], history: list[dict]) -> str:
    parts = []
    if history:
        parts.append(_format_history(history))
    parts.append("=== منابع حقوقی استخراج‌شده (فقط از این‌ها استناد کن) ===")
    parts.append(_format_retrieval(hits))
    parts.append("\n=== موضوع فعلی مراجع ===")
    parts.append(issue_text.strip())
    if doc_text and doc_text.strip():
        parts.append("\n=== متن مستندات ارائه‌شده ===")
        parts.append(doc_text.strip()[:6000])
    parts.append("\nلطفاً خروجی را فقط به صورت JSON معتبر برگردان.")
    return "\n".join(parts)


def clarify(issue_text: str, hits: list[dict], doc_text: Optional[str] = None,
            history: Optional[list[dict]] = None,
            openai_key: Optional[str] = None,
            model: str = "gpt-4o-mini") -> ClarifyResult:
    """Decide whether we have enough to analyze, or need to ask questions."""
    key = openai_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY not set. Provide it or set env var.")
    from openai import OpenAI
    client = OpenAI(api_key=key, timeout=60, max_retries=2)

    prompt = build_clarify_prompt(issue_text, doc_text, hits, history or [])
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": CLARIFY_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=700,
    )
    raw = resp.choices[0].message.content or ""
    return _parse_clarify(raw)


def _parse_clarify(raw: str) -> ClarifyResult:
    res = ClarifyResult(raw=raw)
    # extract the first JSON object from the response
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        # No JSON -> assume ready with no questions
        res.needs_info = False
        return res
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        res.needs_info = False
        return res
    res.needs_info = bool(data.get("needs_info", False))
    res.case_type = str(data.get("case_type", ""))
    res.missing = [str(x) for x in data.get("missing", [])]
    res.questions = [str(x) for x in data.get("questions", [])][:4]
    return res


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
def build_user_prompt(issue_text: str, doc_text: Optional[str], hits: list[dict],
                       history: Optional[list[dict]] = None) -> str:
    parts = []
    if history:
        parts.append(_format_history(history))
    parts.append("=== منابع حقوقی استخراج‌شده از پایگاه دانش (فقط و فقط از این متون استناد کن؛ "
                 "اگر ماده‌ای در اینجا نیست، ادعای شماره ماده نکن و بگو در منابع نیافتم) ===")
    parts.append(_format_retrieval(hits))
    parts.append("\n=== موضوع مراجع ===")
    parts.append(issue_text.strip())
    if doc_text and doc_text.strip():
        parts.append("\n=== متن مستندات ارائه‌شده توسط مراجع ===")
        parts.append(doc_text.strip()[:6000])
    parts.append("\nلطفاً طبق قواعد سیستم، تحلیل حقوقی و راهکار ارائه دهید. "
                 "در بخش «مواد قانونی مرتبط» فقط موادی را بنویس که در منابع بالا آمده‌اند.")
    return "\n".join(parts)


def parse_opinion(raw: str) -> LegalOpinion:
    """Map the LLM's Persian structured text into a dataclass.

    Robust to minor format drift: split on header lines containing the section
    keyword (rather than requiring an exact prefix), because real LLM output
    may add stray characters.
    """
    op = LegalOpinion(raw=raw)

    section_patterns = [
        ("خلاصه موضوع", "summary"),
        ("تحلیل حقوقی", "analysis"),
        ("مواد قانونی مرتبط", "citations"),
        ("راهکار", "recommendations"),
        ("ریسک", "risks"),
        ("هزینه", "cost_estimate"),
        ("هشدار مسئولیت", "disclaimer"),
    ]

    import re
    header_re = re.compile(
        r"^\s*(?:[📌⚖️📚✅💡💰⚠️]*)?\s*(.*?)\b("
        + "|".join(re.escape(k) for k, _ in section_patterns)
        + r")\b"
    )

    lines = raw.splitlines()
    markers: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = header_re.match(line)
        if m:
            keyword = m.group(2)
            attr = next(a for k, a in section_patterns if k == keyword)
            markers.append((i, attr))

    if not markers:
        op.analysis = raw.strip()
        op.disclaimer = _default_disclaimer()
        return op

    for idx, (line_i, attr) in enumerate(markers):
        start = line_i + 1
        end = markers[idx + 1][0] if idx + 1 < len(markers) else len(lines)
        content = "\n".join(lines[start:end]).strip()
        if attr in ("citations", "recommendations"):
            if content:
                getattr(op, attr).append(content)
        else:
            setattr(op, attr, content)

    if not op.disclaimer:
        op.disclaimer = _default_disclaimer()
    return op


def _default_disclaimer() -> str:
    return ("⚠️ هشدار مسئولیت: این تحلیل مشاوره اولیه مبتنی بر اطلاعات ارائه‌شده است "
            "و جایگزین مراجعه به وکیل دادگستری و بررسی پرونده اصلی نیست.")


def ask_lawyer(issue_text: str, hits: list[dict], doc_text: Optional[str] = None,
               openai_key: Optional[str] = None, model: str = "gpt-4o-mini",
               history: Optional[list[dict]] = None) -> LegalOpinion:
    """High-level: given the user's issue + retrieved law (+ optional history),
    call the LLM and return a structured LegalOpinion."""
    key = openai_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY not set. Provide it or set env var.")
    from openai import OpenAI
    client = OpenAI(api_key=key, timeout=90, max_retries=2)

    user_prompt = build_user_prompt(issue_text, doc_text, hits, history)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        max_tokens=4500,
    )
    raw = resp.choices[0].message.content or ""
    return parse_opinion(raw)


if __name__ == "__main__":
    import chromadb
    from seed_loader import load_records, build_collection, query_collection
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    client = chromadb.PersistentClient(path=os.path.join(PROJECT_ROOT, "data", "chroma"))
    recs = load_records()
    coll = build_collection(client, records=recs, openai_key=os.environ.get("OPENAI_API_KEY"))
    hits = query_collection(coll, "کارفرما حقوقم را نمی‌دهد و قرارداد کتبی ندارم", n_results=5)
    print("RETRIEVED:", len(hits), "hits")
    if os.environ.get("OPENAI_API_KEY"):
        c = clarify("کارفرما ۴ ماه حقوقم را نپرداخته و قرارداد کتبی ندارم.", hits,
                    history=[], openai_key=os.environ.get("OPENAI_API_KEY"))
        print("CLARIFY:", c.needs_info, c.case_type, c.questions)
        op = ask_lawyer("کارفرما ۴ ماه حقوقم را نپرداخته و قرارداد کتبی ندارم.",
                        hits, doc_text=None, history=[])
        print(op.raw)
    else:
        print("Set OPENAI_API_KEY to run full test. Retrieval works standalone.")
