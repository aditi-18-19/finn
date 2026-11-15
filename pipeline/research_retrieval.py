from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter
from pathlib import Path

import numpy as np

MODEL_NAME = "BAAI/bge-small-en-v1.5"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

# Domain expansions are deliberately specific. Generic terms such as "guidance",
# "investment" and "plans" are not expanded because they create broad false matches.
DOMAIN_EXPANSIONS = {
    "nim": ["net interest margin", "interest margin"],
    "net interest margin": ["nim"],
    "capex": [
        "capital expenditure", "capital expenditures", "capital expenditure for the year",
        "capital spending", "additions to fixed assets", "fixed asset additions",
        "purchase of fixed assets", "purchase of property plant and equipment",
        "property plant and equipment", "capital work in progress",
    ],
    "capital expenditure": [
        "capex", "capital expenditures", "capital expenditure for the year",
        "capital spending", "additions to fixed assets", "fixed asset additions",
        "purchase of fixed assets", "purchase of property plant and equipment",
        "capital work in progress",
    ],
    "technology investment": [
        "technology expenditure", "technology spend", "IT infrastructure",
        "core banking systems", "digital platforms", "software investment",
        "data centre", "cybersecurity infrastructure",
    ],
    "technology spend": [
        "technology expenditure", "IT infrastructure", "core banking systems",
        "digital platforms", "software investment", "data centre",
    ],
    "branch expansion": [
        "new branches", "branch additions", "branch network",
        "opening of branches", "banking outlets",
    ],
    "deposit growth": [
        "deposit mobilisation", "CASA deposits", "term deposits",
        "total deposits", "retail deposits",
    ],
    "loan growth": ["advances growth", "credit growth"],
    "asset quality": ["slippages", "gross NPA", "net NPA"],
}

INTENT_PHRASES = {
    "capex": [
        "capital expenditure", "capital expenditures", "capital expenditure for the year",
        "capital spending", "additions to fixed assets", "fixed asset additions",
        "purchase of fixed assets", "purchase of property plant and equipment",
        "property plant and equipment", "capital work in progress",
    ],
    "technology": [
        "technology expenditure", "technology spend", "IT infrastructure",
        "core banking", "digital platforms", "data centre", "cybersecurity",
        "software expenditure",
    ],
    "branch": [
        "new branches", "branch additions", "branch network",
        "opening of branches", "banking outlets",
    ],
    "nim": ["net interest margin", "structural NIM", "through cycle NIM"],
    "deposit": ["deposit growth", "total deposits", "CASA deposits", "term deposits"],
}

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "by", "for",
    "from", "has", "have", "how", "in", "is", "it", "its", "of", "on", "or",
    "our", "the", "their", "this", "to", "was", "what", "when", "where",
    "which", "who", "will", "with", "would", "bank", "company", "management",
    "plan", "plans", "planned", "guidance", "provided", "provide",
}


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = text.replace("\u00ad", "")
    text = text.replace("&", " and ")
    return re.sub(r"\s+", " ", text).strip()


def tokenize(text: str) -> list[str]:
    return [
        t for t in re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", normalise(text))
        if t not in STOPWORDS and len(t) > 1
    ]


def expand_query(query: str) -> str:
    q = normalise(query)
    additions = []
    for phrase, terms in DOMAIN_EXPANSIONS.items():
        if phrase in q:
            additions.extend(terms)
    return query + " " + " ".join(dict.fromkeys(additions))


def _chunk_text(chunk: dict) -> str:
    return str(chunk.get("text") or chunk.get("content") or "")


def _intent_for_query(query: str) -> str | None:
    q = normalise(query)
    # Specific phrases first to avoid generic technology / capital overlap.
    if "capex" in q or "capital expenditure" in q or "capital spending" in q:
        return "capex"
    if "technology" in q or "it investment" in q or "digital investment" in q:
        return "technology"
    if "branch" in q:
        return "branch"
    if "nim" in q or "net interest margin" in q:
        return "nim"
    if "deposit" in q:
        return "deposit"
    return None


def _bm25_scores(query: str, chunks: list[dict]) -> list[float]:
    """BM25 lexical score over original query terms, normalized to 0..1."""
    q_terms = list(dict.fromkeys(tokenize(query)))
    if not q_terms or not chunks:
        return [0.0] * len(chunks)

    docs = [tokenize(_chunk_text(c)) for c in chunks]
    doc_sets = [set(d) for d in docs]
    n = len(docs)
    avgdl = sum(len(d) for d in docs) / max(n, 1)
    df = Counter(term for d in doc_sets for term in d)
    lengths = [len(d) for d in docs]
    k1, b = 1.5, 0.75

    raw_scores = []
    for doc, length in zip(docs, lengths):
        tf = Counter(doc)
        score = 0.0
        for term in q_terms:
            freq = tf.get(term, 0)
            if not freq:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            denom = freq + k1 * (1 - b + b * length / max(avgdl, 1))
            score += idf * (freq * (k1 + 1)) / denom
        raw_scores.append(score)

    maximum = max(raw_scores, default=0.0)
    if maximum <= 0:
        return [0.0] * len(chunks)
    return [min(1.0, s / maximum) for s in raw_scores]


def lexical_scores(query: str, chunks: list[dict]) -> list[float]:
    """BM25 over original + targeted expansion, with table-aware capex boosts."""
    intent = _intent_for_query(query)
    expanded = expand_query(query)
    # The previous implementation expanded the embedding query but not BM25.
    # This meant annual-report wording such as "capital expenditure for the year"
    # could not receive a lexical match when the user typed only "capex".
    base_original = _bm25_scores(query, chunks)
    base_expanded = _bm25_scores(expanded, chunks)
    phrases = INTENT_PHRASES.get(intent, []) if intent else []
    q_norm = normalise(query)
    scores = []

    capex_table_markers = (
        "capital expenditure for the year",
        "additions to fixed assets",
        "purchase of fixed assets",
        "capital work in progress",
        "property plant and equipment",
        "depreciation on fixed assets",
    )

    for chunk, original, expanded_score in zip(chunks, base_original, base_expanded):
        text = normalise(_chunk_text(chunk))
        phrase_hits = sum(1 for phrase in phrases if normalise(phrase) in text)
        phrase_bonus = min(0.55, 0.18 * phrase_hits)
        exact_query_bonus = 0.12 if len(q_norm) > 8 and q_norm in text else 0.0

        table_bonus = 0.0
        if intent == "capex":
            marker_hits = sum(1 for marker in capex_table_markers if marker in text)
            # Annual-report fixed-asset tables are strong capex evidence.
            # Require a specific accounting phrase; generic "capital" is not enough.
            if "capital expenditure for the year" in text:
                table_bonus += 0.40
            if "additions to fixed assets" in text or "purchase of fixed assets" in text:
                table_bonus += 0.22
            if marker_hits >= 2 and re.search(r"\\d[\\d,]*(?:\\.\\d+)?", text):
                table_bonus += 0.12
            table_bonus = min(0.55, table_bonus)

        # Keep the original query relevant while allowing the specific synonyms
        # to retrieve accounting-table language absent from the user's wording.
        lexical = max(original, 0.72 * expanded_score)
        scores.append(min(1.0, lexical + phrase_bonus + exact_query_bonus + table_bonus))
    return scores


def hybrid_rank(query: str, chunks: list[dict], vectors: np.ndarray,
                query_vector: np.ndarray, top_k: int = 8) -> list[dict]:
    if len(chunks) != vectors.shape[0]:
        raise ValueError("Chunk count does not match embedding index.")
    if not chunks:
        return []
    qv = np.asarray(query_vector, dtype=np.float32)
    qv = qv / max(float(np.linalg.norm(qv)), 1e-12)
    semantic_raw = np.asarray(vectors, dtype=np.float32) @ qv
    semantic = np.clip((semantic_raw + 1.0) / 2.0, 0.0, 1.0)
    lexical = np.asarray(lexical_scores(query, chunks), dtype=np.float32)

    # Semantic remains useful for paraphrases, while phrase-aware BM25 can
    # sharply improve specific financial concepts.
    combined = 0.55 * semantic + 0.45 * lexical
    order = np.argsort(combined)[::-1][:top_k]
    results = []
    for rank, idx in enumerate(order, 1):
        item = dict(chunks[int(idx)])
        item["retrieval"] = {
            "rank": rank,
            "semantic_score": round(float(semantic[int(idx)]), 4),
            "lexical_score": round(float(lexical[int(idx)]), 4),
            "hybrid_score": round(float(combined[int(idx)]), 4),
            "intent": _intent_for_query(query),
        }
        results.append(item)
    return results


def load_index(symbol: str, cache_root: Path):
    directory = cache_root / symbol.upper()
    chunks_path = directory / "chunks.json"
    vectors_path = directory / "embeddings.npy"
    metadata_path = directory / "index_metadata.json"
    for path in (chunks_path, vectors_path, metadata_path):
        if not path.exists():
            raise FileNotFoundError(f"Missing Step 13 index file: {path}")
    payload = json.loads(chunks_path.read_text(encoding="utf-8"))
    chunks = payload.get("chunks", payload) if isinstance(payload, dict) else payload
    if not isinstance(chunks, list):
        raise ValueError(f"Unexpected chunk format in {chunks_path}")
    vectors = np.load(vectors_path)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("model") != MODEL_NAME:
        raise RuntimeError(f"Embedding model mismatch for {symbol}; rebuild the index.")
    if vectors.shape[0] != len(chunks):
        raise RuntimeError(f"Chunk/index size mismatch for {symbol}; rebuild the index.")
    return chunks, vectors, metadata


def retrieve_hybrid(symbol: str, query: str, cache_root: Path,
                    embedder, top_k: int = 8) -> list[dict]:
    chunks, vectors, _ = load_index(symbol, cache_root)
    query_vector = np.asarray(
        list(embedder.query_embed(QUERY_PREFIX + expand_query(query)))[0],
        dtype=np.float32,
    )
    return hybrid_rank(query, chunks, vectors, query_vector, top_k=top_k)


def extract_document_date(document: str) -> str | None:
    name = (document or "").replace("-", "_")
    month = re.search(
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[_ ](20\d{2})",
        name, re.I,
    )
    if month:
        return f"{month.group(1).title()} {month.group(2)}"
    fy = re.search(r"FY[_ -]?(20\d{2})", name, re.I)
    if fy:
        return f"FY{fy.group(1)}"
    return None
