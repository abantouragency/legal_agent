"""
test_logic.py
=============
Real, offline tests of the parts that DON'T need internet/API:
  - seed record loading
  - chunk formatting
  - LLM-output parsing (parse_opinion)
  - user-prompt assembly
These run in the sandbox so we prove the code works before deployment.
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))

from seed_loader import load_records, make_chunk
from lawyer_agent import parse_opinion, build_user_prompt


def test_load_records():
    recs = load_records()
    assert len(recs) > 30, f"expected >30 seed records, got {len(recs)}"
    # every record has required fields
    for r in recs:
        assert "text" in r and r["text"], r
        assert "law" in r
    print(f"[OK] load_records -> {len(recs)} records")
    return recs


def test_make_chunk(recs):
    chunk = make_chunk(recs[2])  # civil-3 (ارکان صحت معامله)
    assert "قانون مدنی" in chunk
    assert "ماده 190" in chunk or "190" in chunk
    assert "قصد و رضای طرفین" in chunk
    print("[OK] make_chunk -> citation embedded:")
    print("    " + chunk.replace("\n", " / ")[:120])


FAKE_LLM = """📌 خلاصه موضوع
مراجع مدعی است کارفرما ۴ ماه حقوق پرداخت نکرده و قرارداد کتبی ندارد.

⚖️ تحلیل حقوقی
با توجه به فقدان قرارداد کتبی، اثبات رابطه کارگری از طریق دلایل دیگر (شهود، فیش حقوقی، پیام‌ها) ممکن است.

📚 مواد قانونی مرتبط
قانون مدنی - ماده ۱۸۳ (تعریف عقد)
قانون مسئولیت مدنی - ماده ۱ (اتلاف و ضمان)

✅ راهکار(های) پیشنهادی
۱. تهیه اظهارنامه و ارسال به کارفرما
۲. مراجعه به اداره تعاون، کار و رفاه اجتماعی
۳. در صورت عدم پاسخ، شکایت در مراجع حل اختلاف

⚠️ هشدار مسئولیت
این تحلیل مشاوره اولیه مبتنی بر اطلاعات ارائه‌شده است و جایگزین مراجعه به وکیل دادگستری و بررسی پرونده اصلی نیست.
"""


def test_parse_opinion():
    op = parse_opinion(FAKE_LLM)
    assert op.summary.strip().startswith("مراجع"), repr(op.summary)
    assert "قانون مدنی" in op.raw
    assert len(op.recommendations) >= 1
    assert op.disclaimer.strip(), "disclaimer should be non-empty after parsing"
    print("[OK] parse_opinion -> sections parsed:")
    print(f"    summary: {op.summary[:40]}...")
    print(f"    recommendations count: {len(op.recommendations)}")
    print(f"    disclaimer present: {'⚠️' in op.disclaimer}")


def test_build_user_prompt(recs):
    hits = [{
        "chunk": make_chunk(recs[2]),
        "law": recs[2]["law"],
        "article": str(recs[2]["article"]),
        "title": recs[2]["title"],
        "score": 0.9,
    }]
    prompt = build_user_prompt("کارفرما حقوق نمی‌دهد", None, hits)
    assert "منابع حقوقی استخراج‌شده" in prompt
    assert "کارفرما حقوق نمی‌دهد" in prompt
    assert "ماده 190" in prompt
    print("[OK] build_user_prompt -> retrieval + issue assembled correctly")


if __name__ == "__main__":
    print("=== OFFLINE LOGIC TESTS ===")
    recs = test_load_records()
    test_make_chunk(recs)
    test_parse_opinion()
    test_build_user_prompt(recs)
    print("\n✅ ALL OFFLINE TESTS PASSED")
