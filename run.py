"""
run.py  —  one-command local launcher (Windows)
Runs the RAG retrieval test + (optionally) the bot.

Usage:
  python run.py --test        # run offline RAG retrieval test
  python run.py --bot         # start the Telegram bot (needs .env BOT_TOKEN)
"""
from __future__ import annotations
import argparse
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))


def run_test():
    from seed_loader import load_records, build_collection, query_collection
    import chromadb
    print("Loading seed + corpus records...")
    recs = load_records()
    print(f"  total: {len(recs)} records")
    print("Building chromadb collection (multilingual local embeddings)...")
    client = chromadb.PersistentClient(path=os.path.join(PROJECT_ROOT, "data", "chroma"))
    coll = build_collection(client, records=recs, openai_key=None)
    queries = [
        "فسخ قرارداد به دلیل تدلیس و غبن",
        "کارفرما حقوق کارگر را نمی‌دهد و قرارداد نداریم",
        "صدور چک بلامحل چه مجازاتی دارد",
        "ضرب و جرح عمدی و دیه",
        "مسئولیت پزشک در خطای پزشکی",
    ]
    for q in queries:
        print(f"\n=== {q} ===")
        for h in query_collection(coll, q, n_results=3):
            print(f"  [{h['score']}] {h['law']} - ماده {h['article']} ({h['title']})")
    print("\n✓ RAG test done.")


def run_bot():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    if not os.environ.get("BOT_TOKEN"):
        print("⚠️ BOT_TOKEN not set in .env")
        sys.exit(1)
    os.environ.setdefault("MODEL", "gpt-4o-mini")
    from src.bot import main
    main()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--bot", action="store_true")
    args = ap.parse_args()
    if args.bot:
        run_bot()
    else:
        run_test()
