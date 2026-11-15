from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF

ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = ROOT / "data" / "docs"
CACHE_ROOT = ROOT / "data" / "cache" / "research"
DEFAULT_COMPANIES = ["AXISBANK", "BAJFINANCE", "BHARTIARTL", "TRENT"]
CHUNK_WORDS = 650
OVERLAP_WORDS = 100


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def clean_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[\u00ad]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_pdf_pages(pdf_path: Path) -> list[dict]:
    pages = []
    with fitz.open(pdf_path) as doc:
        for page_index, page in enumerate(doc):
            text = clean_text(page.get_text("text", sort=True))
            if text:
                pages.append({"page": page_index + 1, "text": text})
    return pages


def split_speaker_blocks(text: str) -> list[tuple[str | None, str]]:
    """Heuristic speaker detection; retain the full block even if no speaker is found."""
    lines = text.splitlines()
    blocks: list[tuple[str | None, list[str]]] = []
    current_speaker = None
    current_lines: list[str] = []

    speaker_re = re.compile(
        r"^\s*(?:[A-Z][A-Za-z.&'()/ -]{1,70})"
        r"(?:\s*[-–—,:]\s*(?:Moderator|Operator|Managing Director|"
        r"Chief Executive Officer|CEO|CFO|Analyst|Investor Relations|"
        r"Head of|President|Director|Executive Director|"
        r"Group Chief|Chief Financial Officer)\b.*)?\s*$"
    )
    role_re = re.compile(
        r"\b(Moderator|Operator|Managing Director|Chief Executive Officer|"
        r"\bCEO\b|\bCFO\b|Analyst|Investor Relations|Head of|President|"
        r"Director|Executive Director|Chief Financial Officer)\b", re.I
    )

    for line in lines:
        stripped = line.strip()
        is_heading = (
            2 <= len(stripped) <= 100
            and len(stripped.split()) <= 12
            and (role_re.search(stripped) or (stripped.isupper() and len(stripped.split()) <= 6))
            and not stripped.endswith((".", "?", "!"))
        )
        if is_heading and current_lines:
            blocks.append((current_speaker, current_lines))
            current_lines = []
            current_speaker = stripped
        elif is_heading and not current_lines:
            current_speaker = stripped
        else:
            current_lines.append(line)

    if current_lines:
        blocks.append((current_speaker, current_lines))

    result = []
    for speaker, block_lines in blocks:
        body = clean_text("\n".join(block_lines))
        if body:
            result.append((speaker, body))
    return result or [(None, clean_text(text))]


def words(text: str) -> list[str]:
    return re.findall(r"\S+", text)


def chunk_pages(pages: list[dict], document_name: str, doc_type: str,
                chunk_words: int = CHUNK_WORDS, overlap_words: int = OVERLAP_WORDS) -> list[dict]:
    """Chunk by word count while retaining page ranges and page-level provenance."""
    units = []
    for page in pages:
        text = page["text"]
        if doc_type == "concall":
            blocks = split_speaker_blocks(text)
        else:
            blocks = [(None, text)]
        for speaker, block in blocks:
            tokens = words(block)
            if tokens:
                units.append({
                    "page": page["page"],
                    "speaker": speaker,
                    "tokens": tokens,
                })

    chunks = []
    current_tokens: list[str] = []
    current_pages: list[int] = []
    current_speakers: list[str] = []

    def flush():
        nonlocal current_tokens, current_pages, current_speakers
        if not current_tokens:
            return
        text = " ".join(current_tokens).strip()
        chunks.append({
            "chunk_id": f"{Path(document_name).stem}_p{min(current_pages)}-{max(current_pages)}_c{len(chunks)+1:04d}",
            "document": document_name,
            "document_type": doc_type,
            "page_start": min(current_pages),
            "page_end": max(current_pages),
            "pages": sorted(set(current_pages)),
            "speaker": current_speakers[-1] if current_speakers and len(set(current_speakers)) == 1 else None,
            "word_count": len(current_tokens),
            "text": text,
        })
        current_tokens, current_pages, current_speakers = [], [], []

    for unit in units:
        tokens = unit["tokens"]
        # Preserve speaker boundaries where possible: flush before a new speaker.
        if current_tokens and unit["speaker"] and current_speakers and unit["speaker"] != current_speakers[-1]:
            flush()
        offset = 0
        while offset < len(tokens):
            room = chunk_words - len(current_tokens)
            take = min(room, len(tokens) - offset)
            current_tokens.extend(tokens[offset:offset + take])
            current_pages.extend([unit["page"]] * take)
            if unit["speaker"]:
                current_speakers.append(unit["speaker"])
            offset += take
            if len(current_tokens) >= chunk_words:
                previous_tokens = current_tokens[-overlap_words:] if overlap_words else []
                previous_pages = current_pages[-overlap_words:] if overlap_words else []
                previous_speakers = current_speakers[-1:] if current_speakers else []
                flush()
                current_tokens = list(previous_tokens)
                current_pages = list(previous_pages)
                current_speakers = list(previous_speakers)
    flush()
    return chunks


def document_type_from_name(name: str) -> str:
    return "concall" if name.lower().startswith("concall") else "annual_report"


def load_manifest_docs(symbol: str) -> list[dict]:
    manifest_path = DOCS_ROOT / symbol / "manifest.json"
    if not manifest_path.exists():
        return []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return [
        item for item in manifest.get("documents", [])
        if item.get("download_status") in {"downloaded", "cached"}
        and item.get("file")
    ]


def process_company(symbol: str, chunk_words: int, overlap_words: int) -> dict:
    symbol = symbol.upper()
    manifest_docs = load_manifest_docs(symbol)
    cache_dir = CACHE_ROOT / symbol
    cache_dir.mkdir(parents=True, exist_ok=True)
    all_chunks = []
    document_records = []

    if not manifest_docs:
        return {"symbol": symbol, "status": "no_documents", "documents": 0, "chunks": 0}

    for item in manifest_docs:
        pdf_path = ROOT / item["file"]
        if not pdf_path.exists():
            document_records.append({"file": item["file"], "status": "missing_pdf"})
            continue

        digest = sha256_file(pdf_path)
        cache_key = hashlib.sha256(
            f"{digest}|{chunk_words}|{overlap_words}|v1".encode()
        ).hexdigest()
        cache_file = cache_dir / f"{cache_key}.json"

        if cache_file.exists():
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            chunks = cached["chunks"]
            status = "cached"
        else:
            print(f"[{symbol}] Extracting {pdf_path.name}…", flush=True)
            try:
                pages = extract_pdf_pages(pdf_path)
                chunks = chunk_pages(
                    pages, pdf_path.name, document_type_from_name(pdf_path.name),
                    chunk_words, overlap_words
                )
                cache_file.write_text(json.dumps({
                    "source_file": item["file"],
                    "sha256": digest,
                    "page_count_with_text": len(pages),
                    "chunks": chunks,
                }, ensure_ascii=False), encoding="utf-8")
                status = "extracted"
            except Exception as exc:
                document_records.append({"file": item["file"], "status": "error", "error": str(exc)})
                continue

        all_chunks.extend(chunks)
        document_records.append({
            "file": item["file"],
            "sha256": digest,
            "status": status,
            "chunks": len(chunks),
        })

    out_file = cache_dir / "chunks.json"
    out_file.write_text(json.dumps({
        "symbol": symbol,
        "chunk_words": chunk_words,
        "overlap_words": overlap_words,
        "documents": document_records,
        "chunks": all_chunks,
    }, ensure_ascii=False), encoding="utf-8")

    return {
        "symbol": symbol,
        "status": "ok",
        "documents": len(document_records),
        "chunks": len(all_chunks),
        "extracted": sum(x.get("status") == "extracted" for x in document_records),
        "cached": sum(x.get("status") == "cached" for x in document_records),
        "errors": [x for x in document_records if x.get("status") in {"error", "missing_pdf"}],
        "chunks_file": str(out_file.relative_to(ROOT)).replace("\\", "/"),
    }


def main():
    parser = argparse.ArgumentParser(description="Extract PDFs and create provenance-preserving chunks.")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_COMPANIES)
    parser.add_argument("--chunk-words", type=int, default=CHUNK_WORDS)
    parser.add_argument("--overlap-words", type=int, default=OVERLAP_WORDS)
    args = parser.parse_args()
    if args.chunk_words < 100 or args.overlap_words < 0 or args.overlap_words >= args.chunk_words:
        parser.error("Require chunk-words >= 100 and 0 <= overlap-words < chunk-words.")

    results = []
    for symbol in args.symbols:
        print(f"\n[{symbol}] Reading document manifest…", flush=True)
        results.append(process_company(symbol, args.chunk_words, args.overlap_words))

    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    summary = CACHE_ROOT / "extraction_summary.json"
    summary.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n=== EXTRACTION SUMMARY ===")
    for result in results:
        print(f"{result['symbol']}: {result.get('documents', 0)} documents, "
              f"{result.get('chunks', 0)} chunks, status={result['status']}")
        for error in result.get("errors", []):
            print(f"  {error}")
    print(f"Saved: {summary}")


if __name__ == "__main__":
    main()
