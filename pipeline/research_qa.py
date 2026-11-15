from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CACHE_ROOT = ROOT / "data" / "cache" / "research"
OUTPUT_ROOT = ROOT / "data" / "research_answers"
MODEL_NAME = "BAAI/bge-small-en-v1.5"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
NOT_FOUND = "Not found in the documents"

def norm(text: str) -> str:
    text = (text or "").lower()
    text = text.replace("\u00ad", "")
    text = re.sub(r"\s+", " ", text)
    return re.sub(r"[^\w%₹.-]+", " ", text).strip()


def quote_is_verbatim(quote: str, source_text: str) -> bool:
    q = norm(quote)
    s = norm(source_text)
    return bool(q) and q in s


def validate_claims(payload: dict[str, Any], retrieved: list[dict]) -> dict[str, Any]:
    """Keep only claims with a valid retrieved chunk and verbatim source quote."""
    by_id = {str(c.get("chunk_id")): c for c in retrieved if c.get("chunk_id")}
    valid_claims = []
    rejected = []
    claims = payload.get("claims", [])
    if not isinstance(claims, list):
        claims = []

    for i, claim in enumerate(claims):
        if not isinstance(claim, dict):
            rejected.append({"index": i, "reason": "claim_not_object"})
            continue
        chunk_id = str(claim.get("chunk_id") or "")
        chunk = by_id.get(chunk_id)
        if not chunk:
            rejected.append({"index": i, "reason": "chunk_not_in_retrieved_context"})
            continue
        statement = str(claim.get("claim") or "").strip()
        quote = str(claim.get("quote") or "").strip()
        if not statement or not quote:
            rejected.append({"index": i, "reason": "missing_claim_or_quote"})
            continue
        if len(quote.split()) > 55:
            rejected.append({"index": i, "reason": "quote_exceeds_55_words"})
            continue
        if not quote_is_verbatim(quote, str(chunk.get("text") or "")):
            rejected.append({"index": i, "reason": "quote_not_verbatim_in_source_chunk"})
            continue

        valid_claims.append({
            "claim": statement,
            "quote": quote,
            "chunk_id": chunk_id,
            "document": str(chunk.get("document") or ""),
            "document_type": str(chunk.get("document_type") or ""),
            "page_start": chunk.get("page_start"),
            "page_end": chunk.get("page_end"),
            "speaker": chunk.get("speaker"),
        })

    answer = str(payload.get("answer") or "").strip()
    if not valid_claims:
        answer = NOT_FOUND
    elif answer == NOT_FOUND:
        # A model cannot suppress valid evidence by setting not_found.
        answer = " ".join(c["claim"] for c in valid_claims)

    return {
        "answer": answer,
        "claims": valid_claims,
        "not_found": not bool(valid_claims),
        "rejected_claims": rejected,
    }


def build_prompt(symbol: str, question: str, retrieved: list[dict]) -> str:
    context = []
    for i, chunk in enumerate(retrieved, 1):
        context.append({
            "chunk_id": chunk.get("chunk_id"),
            "document": chunk.get("document"),
            "document_type": chunk.get("document_type"),
            "page_start": chunk.get("page_start"),
            "page_end": chunk.get("page_end"),
            "speaker": chunk.get("speaker"),
            "text": chunk.get("text", ""),
        })

    return f"""You are FINN, an investment research assistant.
Answer the user's question about {symbol} using ONLY the primary-source excerpts below.
The excerpts come from annual reports and earnings-call transcripts.

Rules:
1. Do not use outside knowledge, assumptions, web information, or facts not present in these excerpts.
2. If the excerpts do not contain enough evidence to answer, return answer exactly as "{NOT_FOUND}", claims as [], and not_found as true.
3. Every factual sentence must be represented by one or more claims.
4. Each claim must cite one supplied chunk_id and include an exact verbatim quote copied from that chunk.
5. Never invent document names, page numbers, speakers, dates, amounts, units, or reporting scope.
6. Distinguish historical actuals, management guidance, targets, and forward-looking statements.
7. If sources conflict or differ by period/scope, state that explicitly and cite both.
8. Keep quotes concise (55 words maximum each).
9. Return ONLY valid JSON matching:
{{
  "answer": "concise answer or {NOT_FOUND}",
  "claims": [
    {{
      "claim": "one factual statement",
      "chunk_id": "exact supplied chunk_id",
      "quote": "exact source wording"
    }}
  ],
  "not_found": false
}}

Company: {symbol}
Question: {question}

Primary-source excerpts:
{json.dumps(context, ensure_ascii=False)}
"""


def call_gemini(prompt: str, api_key: str, model: str) -> dict[str, Any]:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
            "maxOutputTokens": 2500,
        },
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"Gemini HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Gemini connection error: {exc}") from exc

    data = json.loads(raw)
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Gemini returned no candidates: {raw[:1500]}")
    parts = candidates[0].get("content", {}).get("parts", [])
    text = "".join(str(p.get("text", "")) for p in parts)
    try:
        result = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Gemini did not return valid JSON: {text[:1500]}") from exc
    if not isinstance(result, dict):
        raise RuntimeError("Gemini JSON response was not an object.")
    return result


def run_question(symbol: str, question: str, top_k: int = 10,
                 model_name: str | None = None) -> dict[str, Any]:
    symbol = symbol.upper()
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        # Environment variables may still be supplied by the shell/host.
        pass
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing from the local .env file.")

    try:
        from fastembed import TextEmbedding
    except ImportError as exc:
        raise RuntimeError("fastembed is missing. Install the Step 13 requirements before running research Q&A.") from exc

    # Import project retrieval only at execution time so unit tests for the
    # evidence validator do not need the user's local project tree.
    try:
        from research_retrieval import load_index, hybrid_rank, expand_query
    except ImportError:
        from pipeline.research_retrieval import load_index, hybrid_rank, expand_query

    chunks, vectors, metadata = load_index(symbol, CACHE_ROOT)
    embedder = TextEmbedding(model_name=MODEL_NAME)
    query_vector = np.asarray(
        list(embedder.query_embed(QUERY_PREFIX + expand_query(question)))[0],
        dtype=np.float32,
    )
    query_vector /= max(float(np.linalg.norm(query_vector)), 1e-12)
    retrieved = hybrid_rank(question, chunks, vectors, query_vector, top_k=top_k)

    if not retrieved:
        verified = {
            "answer": NOT_FOUND, "claims": [], "not_found": True,
            "rejected_claims": [],
        }
        model_used = None
    else:
        model_used = model_name or os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        prompt = build_prompt(symbol, question, retrieved)
        raw = call_gemini(prompt, api_key, model_used)
        verified = validate_claims(raw, retrieved)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    question_hash = hashlib.sha256(question.encode("utf-8")).hexdigest()[:10]
    out_dir = OUTPUT_ROOT / symbol
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / f"{timestamp}_{question_hash}.json"

    audit = {
        "symbol": symbol,
        "question": question,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": model_used,
        "retrieval": {
            "method": "step15_hybrid_semantic_bm25",
            "top_k": top_k,
            "index_model": metadata.get("model"),
            "retrieved_chunks": [
                {
                    "chunk_id": c.get("chunk_id"),
                    "document": c.get("document"),
                    "page_start": c.get("page_start"),
                    "page_end": c.get("page_end"),
                    "semantic_score": (c.get("retrieval") or {}).get("semantic_score"),
                    "lexical_score": (c.get("retrieval") or {}).get("lexical_score"),
                    "hybrid_score": (c.get("retrieval") or {}).get("hybrid_score"),
                }
                for c in retrieved
            ],
        },
        **verified,
    }
    output_path.write_text(
        json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    audit["saved_to"] = str(output_path.relative_to(ROOT)).replace("\\", "/")
    return audit


def main():
    parser = argparse.ArgumentParser(description="Answer a company research question from retrieved primary sources.")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--model", default=None)
    args = parser.parse_args()
    if args.top_k < 1 or args.top_k > 20:
        parser.error("--top-k must be between 1 and 20.")
    try:
        result = run_question(args.symbol, args.question, args.top_k, args.model)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print("\n" + "=" * 88)
    print(result["answer"])
    print("\nEvidence:")
    if not result["claims"]:
        print("- No verifiable evidence found in the retrieved source excerpts.")
    for claim in result["claims"]:
        pages = str(claim.get("page_start"))
        if claim.get("page_end") != claim.get("page_start"):
            pages += f"-{claim.get('page_end')}"
        speaker = f" | Speaker: {claim['speaker']}" if claim.get("speaker") else ""
        print(f"\n- {claim['claim']}")
        print(f'  Quote: "{claim["quote"]}"')
        print(f"  Source: {claim['document']} | PDF pages {pages}{speaker}")
        print(f"  Chunk: {claim['chunk_id']}")
    if result.get("rejected_claims"):
        print(f"\nRejected unsupported claims: {len(result['rejected_claims'])}")
    print(f"\nAudit saved: {result['saved_to']}")


if __name__ == "__main__":
    main()
