from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from fastembed import TextEmbedding

ROOT = Path(__file__).resolve().parents[1]
CACHE_ROOT = ROOT / "data" / "cache" / "research"
MODEL_NAME = "BAAI/bge-small-en-v1.5"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def load_chunks(symbol: str) -> list[dict]:
    path = CACHE_ROOT / symbol.upper() / "chunks.json"
    if not path.exists():
        raise FileNotFoundError(f"No chunks for {symbol}. Run extract_research_chunks.py first.")
    return json.loads(path.read_text(encoding="utf-8"))["chunks"]


def build_index(symbol: str, model: TextEmbedding, batch_size: int = 32) -> dict:
    symbol = symbol.upper()
    chunks = load_chunks(symbol)
    index_dir = CACHE_ROOT / symbol
    index_dir.mkdir(parents=True, exist_ok=True)
    vectors_path = index_dir / "embeddings.npy"
    metadata_path = index_dir / "index_metadata.json"

    if not chunks:
        return {"symbol": symbol, "status": "empty", "chunks": 0}

    # Reuse embeddings if chunk IDs and texts are unchanged.
    fingerprint = __import__("hashlib").sha256(
        "\n".join(c["chunk_id"] + c["text"] for c in chunks).encode("utf-8")
    ).hexdigest()
    if vectors_path.exists() and metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("fingerprint") == fingerprint:
            vectors = np.load(vectors_path)
            if vectors.shape[0] == len(chunks):
                return {"symbol": symbol, "status": "cached", "chunks": len(chunks),
                        "dimension": int(vectors.shape[1])}

    texts = [c["text"] for c in chunks]
    vectors = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start + batch_size]
        vectors.extend(list(model.embed(batch)))
        print(f"[{symbol}] Embedded {min(start + len(batch), len(texts))}/{len(texts)} chunks…", flush=True)

    matrix = np.asarray(vectors, dtype=np.float32)
    # BGE vectors are L2-normalised here for cosine similarity via dot product.
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = matrix / np.maximum(norms, 1e-12)
    np.save(vectors_path, matrix)
    metadata_path.write_text(json.dumps({
        "symbol": symbol,
        "model": MODEL_NAME,
        "fingerprint": fingerprint,
        "chunks": len(chunks),
        "dimension": int(matrix.shape[1]),
        "metric": "cosine_similarity",
    }, indent=2), encoding="utf-8")
    return {"symbol": symbol, "status": "built", "chunks": len(chunks),
            "dimension": int(matrix.shape[1])}


def search_company(symbol: str, query: str, top_k: int = 5) -> list[dict]:
    symbol = symbol.upper()
    chunks = load_chunks(symbol)
    index_dir = CACHE_ROOT / symbol
    vectors_path = index_dir / "embeddings.npy"
    metadata_path = index_dir / "index_metadata.json"
    if not vectors_path.exists() or not metadata_path.exists():
        raise FileNotFoundError(f"No vector index for {symbol}. Run build_research_index.py first.")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("model") != MODEL_NAME:
        raise RuntimeError(f"Index model mismatch for {symbol}; rebuild the index.")
    model = TextEmbedding(model_name=MODEL_NAME)
    query_vector = np.asarray(
        list(model.query_embed(QUERY_PREFIX + query))[0], dtype=np.float32
    )
    query_vector /= max(float(np.linalg.norm(query_vector)), 1e-12)
    matrix = np.load(vectors_path)
    scores = matrix @ query_vector
    top_indices = np.argsort(scores)[::-1][:top_k]

    results = []
    for idx in top_indices:
        item = dict(chunks[int(idx)])
        item["similarity"] = round(float(scores[int(idx)]), 4)
        results.append(item)
    return results


def main():
    parser = argparse.ArgumentParser(description="Build/search company-specific embedding indexes.")
    parser.add_argument("--symbols", nargs="+", default=["AXISBANK"])
    parser.add_argument("--query", default=None)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    print(f"Loading embedding model {MODEL_NAME} (first run downloads the small model)…", flush=True)
    model = TextEmbedding(model_name=MODEL_NAME)
    if args.query:
        for symbol in args.symbols:
            print(f"\n[{symbol}] Searching: {args.query}")
            try:
                results = search_company_with_model(symbol, args.query, args.top_k, model)
            except Exception as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                continue
            for rank, item in enumerate(results, 1):
                print(f"\n{rank}. score={item['similarity']} | {item['document']} | "
                      f"pages {item['page_start']}-{item['page_end']}")
                if item.get("speaker"):
                    print(f"Speaker: {item['speaker']}")
                print(item["text"][:1400])
    else:
        for symbol in args.symbols:
            try:
                result = build_index(symbol, model)
                print(f"{symbol}: {result['status']} — {result['chunks']} chunks, "
                      f"dimension={result.get('dimension')}")
            except Exception as exc:
                print(f"{symbol}: ERROR — {exc}", file=sys.stderr)


def search_company_with_model(symbol: str, query: str, top_k: int, model: TextEmbedding):
    symbol = symbol.upper()
    chunks = load_chunks(symbol)
    index_dir = CACHE_ROOT / symbol
    vectors = np.load(index_dir / "embeddings.npy")
    query_vector = np.asarray(
        list(model.query_embed(QUERY_PREFIX + query))[0], dtype=np.float32
    )
    query_vector /= max(float(np.linalg.norm(query_vector)), 1e-12)
    scores = vectors @ query_vector
    results = []
    for idx in np.argsort(scores)[::-1][:top_k]:
        item = dict(chunks[int(idx)])
        item["similarity"] = round(float(scores[int(idx)]), 4)
        results.append(item)
    return results


if __name__ == "__main__":
    main()
