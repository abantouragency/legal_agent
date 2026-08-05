"""
seed_loader.py
==============
Loads the legal knowledge corpus (seed JSONL files + scraped corpus) into a
Chromadb collection so the agent can do semantic retrieval over Iranian law.

Each JSONL line must be a JSON object with at least:
    {
      "doc_id":   "civil-1",
      "law":      "قانون مدنی",
      "article":  190,            # or string like "باب قصاص"
      "title":    "ارکان صحت معامله",
      "text":     "قراردادها به اوضاع و احوال ..."
    }

We build a human-readable chunk string that embeds the citation so the LLM
can quote exact articles back to the user (anti-hallucination).
"""
from __future__ import annotations

import glob
import json
import os
from typing import Iterable

import chromadb
from chromadb.api.types import EmbeddingFunction
from chromadb.utils import embedding_functions

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED_DIR = os.path.join(PROJECT_ROOT, "data", "seed")
CORPUS_DIR = os.path.join(PROJECT_ROOT, "data", "corpus")

EMBED_DIM = 1536  # openai text-embedding-3-small default


def _iter_jsonl_files() -> list[str]:
    files: list[str] = []
    files += sorted(glob.glob(os.path.join(SEED_DIR, "*.jsonl")))
    files += sorted(glob.glob(os.path.join(CORPUS_DIR, "*.jsonl")))
    return files


def load_records() -> list[dict]:
    """Read every JSONL source into a flat list of dict records."""
    records: list[dict] = []
    _counter = {"n": 0}  # global counter -> guarantees unique doc_id across files
    for path in _iter_jsonl_files():
        with open(path, "r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "text" not in rec:
                    continue
                # ALWAYS assign a unique, stable id (filename#line). Do NOT trust
                # any pre-existing doc_id in the data — we saw duplicate doc_ids
                # in the seed files causing chromadb DuplicateIDError (the bot then
                # failed to build its collection on a cold Render start).
                rec["doc_id"] = f"{os.path.basename(path)}#{line_no}"
                rec.setdefault("law", "نامشخص")
                rec.setdefault("title", "")
                rec.setdefault("article", "")
                records.append(rec)
    return records


def make_chunk(rec: dict) -> str:
    """Render a record into a fully-cited chunk string for embedding + display."""
    law = rec.get("law", "")
    art = rec.get("article", "")
    title = rec.get("title", "")
    text = rec.get("text", "")
    citation = f"{law}"
    if art:
        citation += f" - ماده {art}"
    if title:
        citation += f" ({title})"
    return f"{citation}\n{text}"


def build_collection(chroma_client, collection_name: str = "iran_law",
                     records: Iterable[dict] | None = None,
                     openai_key: str | None = None,
                     local_embeddings: bool = False,
                     force_rebuild: bool = False):
    """
    (Re)create the collection and populate it from the corpus.
    If a collection already exists AND is non-empty, it is reused by default
    (force_rebuild=True to wipe and rebuild). This avoids re-embedding the
    entire corpus on every bot restart (which wastes OpenAI quota).

    Returns the chromadb Collection object.

    Embedding strategy:
      - openai_key set  -> OpenAI text-embedding-3-small (RECOMMENDED for prod)
      - local_embeddings -> sentence-transformers multilingual (needs internet
                            on first run to download the model; heavy)
      - else             -> deterministic hash fallback (TESTING ONLY; not real
                            semantic search, just proves the pipeline runs)
    """
    if openai_key:
        os.environ["OPENAI_API_KEY"] = openai_key
        emb_fn = embedding_functions.OpenAIEmbeddingFunction(
            api_key=openai_key,
            model_name="text-embedding-3-small",
        )
    elif local_embeddings:
        emb_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        )
    else:
        emb_fn = _HashFallbackEmbedding()

    # Reuse an existing populated collection unless force_rebuild is requested.
    try:
        existing = chroma_client.get_collection(name=collection_name,
                                                embedding_function=emb_fn)
        if not force_rebuild and existing.count() > 0:
            return existing
    except Exception:
        existing = None

    # wipe old collection for fresh rebuild
    try:
        chroma_client.delete_collection(collection_name)
    except Exception:
        pass

    coll = chroma_client.create_collection(
        name=collection_name,
        embedding_function=emb_fn,
        metadata={"hnsw:space": "cosine"},
    )

    if records is None:
        records = load_records()

    if not records:
        return coll

    ids, docs, metas = [], [], []
    for rec in records:
        ids.append(str(rec["doc_id"]))
        docs.append(make_chunk(rec))
        metas.append({
            "law": rec.get("law", ""),
            "article": str(rec.get("article", "")),
            "title": rec.get("title", ""),
        })

    # chromadb batches: insert in chunks to be safe on large corpora
    BATCH = 200
    for i in range(0, len(ids), BATCH):
        coll.add(
            ids=ids[i:i + BATCH],
            documents=docs[i:i + BATCH],
            metadatas=metas[i:i + BATCH],
        )
    return coll


class _HashFallbackEmbedding(EmbeddingFunction):
    """
    TEST-ONLY embedding: deterministic bag-of-chars vector so the pipeline runs
    without internet / API. It is NOT semantically meaningful — use OpenAI or
    sentence-transformers in production.
    """
    def __init__(self, dim: int = 256):
        self.dim = dim

    def __call__(self, input):
        import hashlib
        import numpy as np
        out = []
        for t in input:
            v = np.zeros(self.dim, dtype=np.float32)
            # character n-gram hashing into buckets
            for i in range(len(t)):
                h = hashlib.md5(t[i:i + 3].encode("utf-8")).digest()
                idx = int.from_bytes(h[:4], "big") % self.dim
                v[idx] += 1.0
            norm = np.linalg.norm(v) or 1.0
            out.append((v / norm).tolist())
        return out


def query_collection(coll, query: str, n_results: int = 6) -> list[dict]:
    """Semantic search over the legal corpus. Returns list of hit dicts."""
    res = coll.query(query_texts=query, n_results=n_results)
    out = []
    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    dists = res.get("distances", [[]])[0]
    for d, m, dist in zip(docs, metas, dists):
        out.append({
            "chunk": d,
            "law": m.get("law", ""),
            "article": m.get("article", ""),
            "title": m.get("title", ""),
            "score": round(1 - float(dist), 3),  # cosine similarity
        })
    return out


if __name__ == "__main__":
    import chromadb
    client = chromadb.PersistentClient(path=os.path.join(PROJECT_ROOT, "data", "chroma"))
    recs = load_records()
    print(f"Loaded {len(recs)} records from seed+corpus.")
    coll = build_collection(client, records=recs, openai_key=os.environ.get("OPENAI_API_KEY"))
    hits = query_collection(coll, "فسخ قرارداد به دلیل تدلیس و غبن", n_results=3)
    for h in hits:
        print(f"\n[{h['score']}] {h['law']} - ماده {h['article']} ({h['title']})")
        print(h["chunk"][:200])
