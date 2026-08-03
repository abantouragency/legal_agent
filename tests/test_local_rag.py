"""
test_local_rag.py
=================
Standalone test of the RAG pipeline against the seeded knowledge base.
Does NOT need an OpenAI key — it only verifies retrieval works.

Run:  python tests/test_local_rag.py
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))

from seed_loader import load_records, build_collection, query_collection
import chromadb


def main():
    print("Loading seed + corpus records...")
    recs = load_records()
    print(f"  total records: {len(recs)}")

    print("Building (local) chromadb collection with multilingual embeddings...")
    # Use the sentence-transformers fallback so no API key is needed.
    client = chromadb.PersistentClient(path=os.path.join(PROJECT_ROOT, "data", "chroma_test"))
    coll = build_collection(client, records=recs, openai_key=None)

    queries = [
        "فسخ قرارداد به دلیل تدلیس و غبن",
        "کارفرما حقوق کارگر را نمی‌دهد و قرارداد نداریم",
        "صدور چک بلامحل چه مجازاتی دارد",
        "ضرب و جرح عمدی و دیه",
        "مسئولیت پزشک در خطای پزشکی",
    ]
    for q in queries:
        print(f"\n=== پرسش: {q} ===")
        hits = query_collection(coll, q, n_results=3)
        for h in hits:
            print(f"  [{h['score']}] {h['law']} - ماده {h['article']} ({h['title']})")

    print("\n✓ RAG retrieval test completed.")


if __name__ == "__main__":
    main()
