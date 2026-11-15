from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Any

ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = ROOT / "data" / "docs"
CACHE_ROOT = ROOT / "data" / "cache" / "research"
RESULT_ROOT = ROOT / "data" / "research"
DEFAULT_QUESTION = (
    "Prepare a concise, evidence-led research overview of this company using the supplied "
    "annual reports and earnings-call transcripts. Identify the business and operating "
    "drivers, reported financial trends, management guidance or targets, capital allocation "
    "and capex where disclosed, and material risks or uncertainties. Distinguish historical "
    "facts from management guidance. Include only points directly supported by the sources; "
    "if an area is not covered by the retrieved excerpts, do not infer it."
)

ProgressCallback = Callable[[dict[str, Any]], None]


class ResearchError(RuntimeError):
    pass


def normalise_symbol(symbol: str) -> str:
    raw = (symbol or "").upper().strip().replace("&", "_")
    value = re.sub(r"[^A-Z0-9_-]", "", raw)
    if not value or value != raw:
        raise ValueError("Invalid ticker. Use letters, numbers, hyphens or underscores only.")
    if len(value) > 20:
        raise ValueError("Ticker is too long (maximum 20 characters).")
    return value


def _emit(callback: ProgressCallback | None, stage: str, message: str, **extra: Any) -> None:
    event = {"stage": stage, "message": message, "timestamp": datetime.now(timezone.utc).isoformat(), **extra}
    if callback:
        callback(event)
    else:
        print(f"[{stage.upper()}] {message}", flush=True)


def _run(script: str, symbol: str, callback: ProgressCallback | None, stage: str,
         extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
    path = ROOT / "pipeline" / script
    if not path.exists():
        raise ResearchError(f"Required pipeline file is missing: pipeline/{script}. Install the earlier FINN Steps 12–14 packages first.")
    command = [sys.executable, str(path), "--symbols", symbol]
    if extra_args:
        command.extend(extra_args)
    _emit(callback, stage, f"Running {script} for {symbol}…")
    # Stream child-process output as it arrives so the UI/terminal visibly advances
    # while downloads, PDF extraction, and embedding batches are running.
    process = subprocess.Popen(
        command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    output_lines: list[str] = []
    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.rstrip()
        output_lines.append(line)
        if line:
            _emit(callback, stage, line)
    return_code = process.wait()
    output = "\n".join(output_lines)
    result = subprocess.CompletedProcess(command, return_code, output, "")
    if return_code != 0:
        detail = output.strip()[-2500:] or "No error output"
        raise ResearchError(f"{script} failed (exit {return_code}): {detail}")
    return result


def _load_manifest(symbol: str) -> dict | None:
    path = DOCS_ROOT / symbol / "manifest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _source_fingerprint(symbol: str) -> str | None:
    manifest = _load_manifest(symbol)
    if not manifest:
        return None
    items = []
    for doc in manifest.get("documents", []):
        if doc.get("download_status") not in {"downloaded", "cached"} or not doc.get("file"):
            continue
        path = ROOT / doc["file"]
        if not path.is_file():
            continue
        digest = doc.get("sha256")
        if not digest:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        items.append((doc.get("type", ""), doc.get("title", ""), str(path.name), digest))
    if not items:
        return None
    payload = json.dumps(sorted(items), separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _document_gaps(manifest: dict | None, annual_target: int, concall_target: int) -> list[str]:
    if not manifest:
        return ["No Screener document manifest was created or found."]
    docs = manifest.get("documents", [])
    successful = [d for d in docs if d.get("download_status") in {"downloaded", "cached"}
                  and d.get("file") and (ROOT / d["file"]).is_file()]
    annual = [d for d in successful if d.get("type") == "annual_report"]
    concalls = [d for d in successful if d.get("type") == "concall"]
    available = manifest.get("available", {})
    gaps = []
    if len(annual) < annual_target:
        gaps.append(f"Annual reports: requested latest {annual_target}, found {len(annual)} downloadable PDF(s); Screener lists {available.get('annual_reports', 0)} link(s).")
    if len(concalls) < concall_target:
        gaps.append(f"Earnings-call transcripts: requested latest {concall_target}, found {len(concalls)} downloadable PDF(s); Screener lists {available.get('concall_transcripts', 0)} transcript link(s).")
    for doc in docs:
        if doc.get("download_status") == "failed":
            gaps.append(f"Could not download {doc.get('title') or doc.get('url') or 'a discovered document'}: {doc.get('error', 'unknown download error')}")
    return gaps


def _valid_research_cache(symbol: str, fingerprint: str | None, question: str) -> dict | None:
    path = RESULT_ROOT / symbol / "research.json"
    if not fingerprint or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("source_fingerprint") == fingerprint and payload.get("question") == question and payload.get("status") == "complete":
        payload["cache_hit"] = True
        return payload
    return None


def research(symbol: str, question: str | None = None, *, annual_reports: int = 2,
             transcripts: int = 4, top_k: int = 10,
             progress_callback: ProgressCallback | None = None) -> dict[str, Any]:
    """Fetch, chunk, index and analyse primary-source documents for any Screener ticker.

    Progress callback receives dictionaries with stage/message/timestamp fields, suitable
    for forwarding as server-sent events by the API layer.
    """
    symbol = normalise_symbol(symbol)
    question = (question or DEFAULT_QUESTION).strip()
    if not question:
        raise ValueError("Research question cannot be empty.")
    if annual_reports < 0 or transcripts < 0 or annual_reports + transcripts == 0:
        raise ValueError("Request at least one annual report or transcript.")
    if top_k < 1 or top_k > 20:
        raise ValueError("top_k must be between 1 and 20.")

    fingerprint = _source_fingerprint(symbol)
    cached = _valid_research_cache(symbol, fingerprint, question)
    if cached:
        _emit(progress_callback, "cache", f"Using cached research for {symbol}.", cache_hit=True)
        return cached

    _emit(progress_callback, "fetch", f"Fetching annual reports and earnings-call transcripts for {symbol}…")
    fetch_error = None
    try:
        _run("fetch_documents.py", symbol, progress_callback, "fetch",
             ["--annual", str(annual_reports), "--concalls", str(transcripts)])
    except ResearchError as exc:
        fetch_error = str(exc)
        _emit(progress_callback, "fetch_warning", f"Screener refresh failed; checking previously downloaded documents. {fetch_error}")

    manifest = _load_manifest(symbol)
    gaps = _document_gaps(manifest, annual_reports, transcripts)
    fingerprint = _source_fingerprint(symbol)
    if not fingerprint:
        return {
            "symbol": symbol, "status": "missing_documents", "cache_hit": False,
            "answer": "Research could not start because no downloadable primary-source PDFs are available.",
            "claims": [], "missing_documents": gaps,
            "error": fetch_error or "No annual reports or earnings-call transcripts were found.",
        }

    _emit(progress_callback, "read", f"Reading PDFs and extracting page-level text for {symbol}…")
    _run("extract_research_chunks.py", symbol, progress_callback, "read")
    chunks_path = CACHE_ROOT / symbol / "chunks.json"
    if not chunks_path.exists():
        raise ResearchError(f"Chunk extraction completed without creating {chunks_path.relative_to(ROOT)}.")
    chunks_payload = json.loads(chunks_path.read_text(encoding="utf-8"))
    chunk_count = len(chunks_payload.get("chunks", []))
    if chunk_count == 0:
        return {
            "symbol": symbol, "status": "no_extractable_text", "cache_hit": False,
            "answer": "PDFs were found, but no extractable text was produced. Scanned/image-only PDFs may require OCR.",
            "claims": [], "missing_documents": gaps,
            "documents": chunks_payload.get("documents", []),
        }

    _emit(progress_callback, "index", f"Building or reusing the search index for {chunk_count} text chunks…")
    _run("build_research_index.py", symbol, progress_callback, "index")
    index_dir = CACHE_ROOT / symbol
    if not (index_dir / "embeddings.npy").exists() or not (index_dir / "index_metadata.json").exists():
        raise ResearchError("Search index was not created. Check the fastembed installation and index-stage output.")

    _emit(progress_callback, "analyse", f"Analysing {symbol} against retrieved primary-source passages…")
    qa_script = ROOT / "pipeline" / "research_qa.py"
    if not qa_script.exists():
        raise ResearchError("pipeline/research_qa.py is missing. Install the Step 14 package first.")
    before = set((ROOT / "data" / "research_answers" / symbol).glob("*.json")) if (ROOT / "data" / "research_answers" / symbol).exists() else set()
    result = subprocess.run(
        [sys.executable, str(qa_script), "--symbol", symbol, "--question", question, "--top-k", str(top_k)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "No error output").strip()[-2500:]
        return {
            "symbol": symbol, "status": "analysis_failed", "cache_hit": False,
            "answer": "Documents were collected and indexed, but the analysis stage did not complete.",
            "claims": [], "missing_documents": gaps, "error": detail,
            "likely_action": "Check GEMINI_API_KEY and the model quota in the local .env/provider account; do not paste the key into chat.",
        }

    audit_dir = ROOT / "data" / "research_answers" / symbol
    after = set(audit_dir.glob("*.json")) if audit_dir.exists() else set()
    created = sorted(after - before, key=lambda p: p.stat().st_mtime, reverse=True)
    if not created:
        raise ResearchError("The Q&A command succeeded but did not create an auditable answer JSON.")
    audit = json.loads(created[0].read_text(encoding="utf-8"))
    payload = {
        "symbol": symbol,
        "status": "complete" if audit.get("claims") else "insufficient_evidence",
        "cache_hit": False,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_fingerprint": fingerprint,
        "question": question,
        "answer": audit.get("answer", "Not found in the documents"),
        "claims": audit.get("claims", []),
        "rejected_claims": audit.get("rejected_claims", []),
        "retrieval": audit.get("retrieval", {}),
        "audit_file": audit.get("saved_to"),
        "missing_documents": gaps,
        "document_count": len([d for d in chunks_payload.get("documents", []) if d.get("status") in {"cached", "extracted"}]),
        "chunk_count": chunk_count,
    }
    out_dir = RESULT_ROOT / symbol
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "research.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    _emit(progress_callback, "complete", f"Research finished for {symbol}: {len(payload['claims'])} evidence-backed claim(s).", status=payload["status"])
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="End-to-end FINN primary-source research for any ticker.")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--question", default=None, help="Optional research question; defaults to a concise company overview.")
    parser.add_argument("--annual", type=int, default=2)
    parser.add_argument("--transcripts", type=int, default=4)
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()
    try:
        result = research(args.symbol, args.question, annual_reports=args.annual,
                          transcripts=args.transcripts, top_k=args.top_k)
    except Exception as exc:
        print(f"RESEARCH ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print("\n" + "=" * 76)
    print(f"{result['symbol']} — {result['status']}")
    print(result.get("answer", ""))
    for claim in result.get("claims", []):
        pages = str(claim.get("page_start"))
        if claim.get("page_end") != claim.get("page_start"):
            pages += f"–{claim.get('page_end')}"
        print(f"\n• {claim.get('claim')}\n  Source: {claim.get('document')} | PDF pages {pages}\n  Quote: {claim.get('quote')}")
    if result.get("missing_documents"):
        print("\nDOCUMENT COVERAGE GAPS:")
        for gap in result["missing_documents"]:
            print(f"- {gap}")
    if result.get("error"):
        print(f"\nERROR: {result['error']}")
    print(f"\nCache hit: {result.get('cache_hit', False)}")


if __name__ == "__main__":
    main()
