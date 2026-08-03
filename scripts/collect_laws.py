"""
collect_laws.py  (RUN ON THE USER'S WINDOWS PC — needs internet)
==============================================================
Scrapes Iranian legal texts from public official/reference sources,
normalizes them into the JSONL schema used by seed_loader.py, and writes
them into data/corpus/ for the RAG pipeline.

This is deliberately modular: each source is a function returning a list of
records. Add more sources (e.g. rasmi.ir, iranjustice.ir, judicial rulings)
by appending to SOURCES.

Schema per record:
    {"doc_id","law","article","title","text"}

NOTE: scraping targets change often. If a source is down, the script skips it
and reports. You can also manually paste law text into data/corpus/*.jsonl.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS_DIR = os.path.join(PROJECT_ROOT, "data", "corpus")


def _req(url: str, timeout: int = 20) -> str | None:
    try:
        import requests
    except ImportError:
        print("install requests: uv add requests")
        return None
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        r.encoding = "utf-8"
        return r.text
    except Exception as e:
        print(f"  ! fetch failed {url}: {e}")
        return None


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Source 1: Madadgaar (madadgaar.ir) — plain-text Iranian law collection.
# It exposes per-law pages; we grab a curated set of the most-used laws.
# ---------------------------------------------------------------------------
LAW_PAGES = {
    "قانون مدنی": "https://madadgaar.ir/%D9%82%D8%A7%D9%86%D9%88%D9%86-%D9%85%D8%AF%D9%86%DB%8C",
    "قانون مجازات اسلامی": "https://madadgaar.ir/%D9%82%D8%A7%D9%86%D9%88%D9%86-%D9%85%D8%AC%D8%A7%D8%B2%D8%A7%D8%AA-%D8%A7%D8%B3%D9%84%D8%A7%D9%85%DB%8C",
    "قانون آیین دادرسی مدنی": "https://madadgaar.ir/%D9%82%D8%A7%D9%86%D9%88%D9%86-%D8%A2%DB%8C%DB%8C%D9%86-%D8%AF%D8%A7%D8%AF%D8%B1%D8%B3%DB%8C-%D9%85%D8%AF%D9%86%DB%8C",
    "قانون تجارت": "https://madadgaar.ir/%D9%82%D8%A7%D9%86%D9%88%D9%86-%D8%AA%D8%AC%D8%A7%D8%B1%D8%AA",
}


def parse_madadgaar(law_name: str, url: str) -> list[dict]:
    """Best-effort parse of madadgaar law page into article records."""
    html = _req(url)
    if not html:
        return []
    # madadgaar renders articles as 'ماده N' followed by the text.
    # Strip tags, then split on 'ماده [۰-۹]+'
    body = _clean(html)
    # find article markers
    pattern = re.compile(r"ماده\s+([۰-۹]+|\d+)\s*[-\.]?\s*(.*?)(?=ماده\s+[۰-۹]+|\Z)", re.S)
    recs = []
    for i, m in enumerate(pattern.finditer(body)):
        art = m.group(1)
        txt = m.group(2).strip()
        if len(txt) < 10:
            continue
        recs.append({
            "doc_id": f"{law_name}-{art}",
            "law": law_name,
            "article": art,
            "title": "",
            "text": txt[:1500],
        })
    return recs


def collect() -> list[dict]:
    all_recs: list[dict] = []
    for law, url in LAW_PAGES.items():
        print(f"→ {law} ...")
        recs = parse_madadgaar(law, url)
        print(f"   {len(recs)} ماده استخراج شد.")
        all_recs.extend(recs)
        time.sleep(1)  # be polite
    return all_recs


def write_corpus(recs: list[dict], name: str = "collected_laws.jsonl"):
    os.makedirs(CORPUS_DIR, exist_ok=True)
    out = os.path.join(CORPUS_DIR, name)
    with open(out, "w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"✓ نوشته شد: {out}  ({len(recs)} رکورد)")
    return out


if __name__ == "__main__":
    print("=== شروع جمع‌آوری قوانین (روی ویندوز با اینترنت اجرا شود) ===")
    recs = collect()
    if recs:
        write_corpus(recs)
    else:
        print("هیچ رکوردی استخراج نشد. احتمالاً ساختار سایت تغییر کرده؛")
        print("می‌توانید متن قوانین را دستی در data/corpus/ بچسبانید.")
    print("=== پایان ===")
