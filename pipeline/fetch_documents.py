from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "docs"
DEFAULT_SYMBOLS = ["AXISBANK", "BAJFINANCE", "BHARTIARTL", "TRENT"]
BASE_URL = "https://www.screener.in"
USER_AGENT = "FINN research document collector/1.0 (educational project)"


def load_env_file():
    """Load only the Screener cookie from .env without requiring python-dotenv."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key == "SCREENER_COOKIE" and value and not os.getenv(key):
            os.environ[key] = value


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/pdf,*/*",
        "Referer": BASE_URL + "/",
    })
    cookie = os.getenv("SCREENER_COOKIE", "").strip()
    if cookie:
        # Accept either a raw Cookie header or a single sessionid value.
        if "=" in cookie:
            for part in cookie.split(";"):
                if "=" in part:
                    name, value = part.strip().split("=", 1)
                    session.cookies.set(name.strip(), value.strip(), domain=".screener.in")
        else:
            session.cookies.set("sessionid", cookie, domain=".screener.in")
    return session


def normalise_symbol(value: str) -> str:
    value = re.sub(r"[^A-Z0-9_-]", "", value.upper())
    if not value:
        raise ValueError("Empty/invalid symbol")
    return value


def discover_document_links(html: str, page_url: str, symbol: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    docs = soup.find(id="documents")
    if docs is None:
        # Some responses omit the id wrapper but still contain the Documents heading.
        heading = soup.find(lambda tag: tag.name in {"h2", "h3"} and "documents" in tag.get_text(" ", strip=True).lower())
        if heading:
            docs = heading.parent
    if docs is None:
        raise RuntimeError("Could not find Screener Documents section; page may be blocked or markup changed.")

    annual, concalls = [], []
    headings = docs.find_all(["h3", "h4"])
    for heading in headings:
        label = heading.get_text(" ", strip=True).lower()
        if "annual report" in label:
            kind = "annual_report"
        elif "concall" in label or "conference call" in label:
            kind = "concall"
        else:
            continue

        # Walk forward until the next heading at the same or higher level.
        for node in heading.next_elements:
            if getattr(node, "name", None) in {"h2", "h3", "h4"} and node is not heading:
                break
            if getattr(node, "name", None) != "a" or not node.get("href"):
                continue
            text = node.get_text(" ", strip=True)
            href = urljoin(page_url, node["href"])
            if kind == "annual_report":
                if not re.search(r"annual\s*report", text, re.I):
                    continue
                year_match = re.search(r"(20\d{2})", text)
                annual.append({
                    "type": kind, "title": text,
                    "year": int(year_match.group(1)) if year_match else None,
                    "url": href,
                })
            else:
                if "transcript" not in text.lower():
                    continue
                container_text = node.parent.get_text(" ", strip=True) if node.parent else text
                date_match = re.search(
                    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+20\d{2}\b",
                    container_text, re.I
                )
                concalls.append({
                    "type": kind, "title": text,
                    "period": date_match.group(0) if date_match else container_text[:80],
                    "url": href,
                })

    # Deduplicate by URL, preserving page order (newest first on Screener).
    def dedupe(items):
        seen, result = set(), []
        for item in items:
            if item["url"] not in seen:
                seen.add(item["url"])
                result.append(item)
        return result

    return {
        "symbol": symbol,
        "source_page": page_url,
        "annual_reports": dedupe(annual),
        "concall_transcripts": dedupe(concalls),
    }


def resolve_pdf_url(session: requests.Session, url: str) -> tuple[str, requests.Response]:
    response = session.get(url, timeout=45, allow_redirects=True)
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "").lower()
    if "pdf" in content_type or response.content[:5] == b"%PDF-":
        return response.url, response

    # Some transcript links lead to a company page that embeds/links the PDF.
    if "html" in content_type or response.content.lstrip().startswith(b"<"):
        soup = BeautifulSoup(response.text, "html.parser")
        candidates = []
        for anchor in soup.find_all("a", href=True):
            href = urljoin(response.url, anchor["href"])
            if ".pdf" in href.lower() or "pdf" in anchor.get_text(" ", strip=True).lower():
                candidates.append(href)
        for candidate in candidates:
            pdf = session.get(candidate, timeout=45, allow_redirects=True)
            if pdf.ok and (pdf.content[:5] == b"%PDF-" or "pdf" in pdf.headers.get("Content-Type", "").lower()):
                return pdf.url, pdf
    raise ValueError(f"Link did not resolve to a PDF (content-type={content_type or 'unknown'}): {url}")


def safe_filename(text: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._-")
    return (cleaned[:100] or fallback) + ".pdf"


def download_item(session, item, target_dir: Path, symbol: str, index: int) -> dict:
    result = dict(item)
    try:
        final_url, response = resolve_pdf_url(session, item["url"])
        digest = hashlib.sha256(response.content).hexdigest()
        if item["type"] == "annual_report":
            stem = f"AR_FY{item['year']}" if item.get("year") else f"AR_{index:02d}"
        else:
            period = re.sub(r"[^A-Za-z0-9]+", "_", item.get("period", "concall")).strip("_")
            stem = f"Concall_{period}"
        filename = safe_filename(stem, f"{symbol}_{index}")
        path = target_dir / filename

        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == digest:
            status = "cached"
        else:
            path.write_bytes(response.content)
            status = "downloaded"

        result.update({
            "download_status": status,
            "file": str(path.relative_to(ROOT)).replace("\\", "/"),
            "final_url": final_url,
            "sha256": digest,
            "bytes": len(response.content),
            "content_type": response.headers.get("Content-Type"),
        })
    except Exception as exc:
        result.update({"download_status": "failed", "error": str(exc)})
    return result


def collect_company(session, symbol: str, annual_limit: int, concall_limit: int, delay: float) -> dict:
    symbol = normalise_symbol(symbol)
    page_url = f"{BASE_URL}/company/{symbol}/consolidated/"
    print(f"\n[{symbol}] Fetching document links…", flush=True)
    response = session.get(page_url, timeout=45)
    if response.status_code in (401, 403):
        raise RuntimeError(f"Screener returned HTTP {response.status_code}. If login is required, set SCREENER_COOKIE in .env.")
    response.raise_for_status()

    discovered = discover_document_links(response.text, response.url, symbol)
    selected = (
        discovered["annual_reports"][:annual_limit]
        + discovered["concall_transcripts"][:concall_limit]
    )
    out_dir = DATA_DIR / symbol
    out_dir.mkdir(parents=True, exist_ok=True)

    records = []
    for index, item in enumerate(selected, 1):
        print(f"[{symbol}] Downloading {item['type']}: {item.get('title') or item.get('period')}…", flush=True)
        records.append(download_item(session, item, out_dir, symbol, index))
        time.sleep(delay)

    manifest = {
        "symbol": symbol,
        "source_page": page_url,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "requested": {"annual_reports": annual_limit, "concall_transcripts": concall_limit},
        "available": {
            "annual_reports": len(discovered["annual_reports"]),
            "concall_transcripts": len(discovered["concall_transcripts"]),
        },
        "documents": records,
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def main():
    load_env_file()
    parser = argparse.ArgumentParser(description="Download annual reports and concall transcripts from Screener.in.")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    parser.add_argument("--annual", type=int, default=2, help="Latest annual reports per company (default: 2)")
    parser.add_argument("--concalls", type=int, default=4, help="Latest concall transcripts per company (default: 4)")
    parser.add_argument("--delay", type=float, default=1.5, help="Seconds between document downloads")
    args = parser.parse_args()

    session = make_session()
    summaries = []
    for raw_symbol in args.symbols:
        try:
            summaries.append(collect_company(session, raw_symbol, args.annual, args.concalls, args.delay))
        except Exception as exc:
            print(f"[{raw_symbol}] ERROR: {exc}", file=sys.stderr)
            summaries.append({"symbol": raw_symbol, "error": str(exc)})

    summary_path = DATA_DIR / "collection_summary.json"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summaries, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n=== COLLECTION SUMMARY ===")
    for item in summaries:
        if "error" in item:
            print(f"{item['symbol']}: FAILED — {item['error']}")
            continue
        docs = item["documents"]
        ok = sum(d.get("download_status") in {"downloaded", "cached"} for d in docs)
        failed = sum(d.get("download_status") == "failed" for d in docs)
        print(f"{item['symbol']}: {ok} downloaded/cached, {failed} failed; "
              f"{item['available']['annual_reports']} annual reports and "
              f"{item['available']['concall_transcripts']} transcript links found.")
    print(f"\nManifest summary: {summary_path}")


if __name__ == "__main__":
    main()
