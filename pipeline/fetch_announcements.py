"""
FINN - Step 5: corporate announcements (BSE primary).

Three ways to get the raw BSE data (pick with --source):

  file        Read data/raw/bse_export.json, which you capture from your OWN
              browser with bse_console_export.js. Most reliable. DEFAULT.
  playwright  A real Chromium window opens the official BSE page and calls the
              same endpoint the page itself calls. Low volume, no tricks.
  http        Plain `requests`. Makes one probe and STOPS on the first 403.
              Kept so you can see for yourself whether BSE allows it.

Usage (run from the finn/ folder, venv active):
  python pipeline/fetch_announcements.py --only RELIANCE            # test run
  python pipeline/fetch_announcements.py                            # all 22
  python pipeline/fetch_announcements.py --source playwright --headed
  python pipeline/fetch_announcements.py --source http --only RELIANCE

Output schema is unchanged: a list of announcement dicts in
data/announcements.json. A per-company status report is written to
data/announcements_meta.json.
"""

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import requests

# ------------------------------------------------------------------
# Paths and constants
# ------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
COMPANIES_FILE = BASE_DIR / "companies.json"
DATA_DIR = BASE_DIR / "data"
OUTPUT_FILE = DATA_DIR / "announcements.json"
TEST_OUTPUT_FILE = DATA_DIR / "announcements_test.json"
META_FILE = DATA_DIR / "announcements_meta.json"
RAW_EXPORT = DATA_DIR / "raw" / "bse_export.json"

BSE_API = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
ANN_PAGE = "https://www.bseindia.com/corporates/ann.html"
PDF_BASE_URL = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/"

WINDOW_DAYS = 30
MAX_PAGES = 20          # 50 rows/page, so 1000 announcements per company is plenty
PAUSE_SECONDS = 1.0     # be polite to BSE


class Blocked(Exception):
    """BSE refused the request (401/403). Stop; do not retry or escalate."""


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def load_companies():
    with open(COMPANIES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["portfolio"] + data["research"]


def build_params(code, start, end, page):
    return {
        "pageno": page,
        "strCat": -1,
        "strPrevDate": start.strftime("%Y%m%d"),
        "strScrip": str(code),
        "strSearch": "P",
        "strToDate": end.strftime("%Y%m%d"),
        "strType": "C",
        "subcategory": -1,
    }


def normalise(row, company):
    """Turn one raw BSE row into our schema. Never invents a PDF link."""
    attachment = (row.get("ATTACHMENTNAME") or "").strip()
    return {
        "news_id": row.get("NEWSID"),
        "company": row.get("SLONGNAME") or company["name"],
        "nse_symbol": company["nse_symbol"],
        "bse_code": str(company["bse_scrip_code"]),
        "date_time": row.get("DT_TM"),
        "subject": row.get("NEWSSUB"),
        "description": row.get("MORE"),
        "headline": row.get("HEADLINE"),
        "category": row.get("CATEGORYNAME"),
        "subcategory": row.get("SUBCATNAME"),
        "pdf_link": (PDF_BASE_URL + attachment) if attachment else None,
        "source": "BSE",
    }


def paginate(get_page):
    """
    Call get_page(1), get_page(2), ... until a page is empty or repeats.
    get_page returns a list of raw rows. Duplicate NEWSIDs inside one company
    are dropped here.
    """
    collected, seen = [], set()
    for page in range(1, MAX_PAGES + 1):
        rows = get_page(page)
        if not rows:
            break
        fresh = [r for r in rows if r.get("NEWSID") not in seen]
        if not fresh:       # server sent the same page again, so we are done
            break
        seen.update(r.get("NEWSID") for r in fresh)
        collected.extend(fresh)
        time.sleep(PAUSE_SECONDS)
    return collected


# ------------------------------------------------------------------
# Source 1: file exported from your own browser
# ------------------------------------------------------------------

def fetch_from_file(companies, start, end):
    if not RAW_EXPORT.exists():
        sys.exit(
            f"Missing {RAW_EXPORT}.\n"
            "Run bse_console_export.js in your browser on the BSE announcements "
            "page, then move the downloaded bse_export.json to that path."
        )
    with open(RAW_EXPORT, "r", encoding="utf-8") as f:
        export = json.load(f)

    raw, status = {}, {}
    for c in companies:
        code = str(c["bse_scrip_code"])
        if code not in export:
            status[c["nse_symbol"]] = {"ok": False, "error": "not in export file"}
            continue
        raw[c["nse_symbol"]] = export[code]
        status[c["nse_symbol"]] = {"ok": True}
    return raw, status


# ------------------------------------------------------------------
# Source 2: real browser (Playwright)
# ------------------------------------------------------------------

def fetch_with_playwright(companies, start, end, headed):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("Install first:  pip install playwright && playwright install chromium")

    raw, status = {}, {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
        page = browser.new_page()
        page.goto(ANN_PAGE, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)

        js = """async (url) => {
            const r = await fetch(url);
            return { status: r.status, text: await r.text() };
        }"""

        for c in companies:
            sym, code = c["nse_symbol"], str(c["bse_scrip_code"])

            def get_page(n, code=code):
                url = BSE_API + "?" + urlencode(build_params(code, start, end, n))
                res = page.evaluate(js, url)
                if res["status"] in (401, 403):
                    raise Blocked(f"HTTP {res['status']} from BSE")
                if res["status"] != 200:
                    raise RuntimeError(f"HTTP {res['status']}")
                return json.loads(res["text"]).get("Table") or []

            try:
                print(f"Fetching {sym} ({code}) ...")
                raw[sym] = paginate(get_page)
                status[sym] = {"ok": True}
            except Blocked as e:
                print(f"  BLOCKED: {e}. Stopping. Use --source file instead.")
                status[sym] = {"ok": False, "error": str(e)}
                break
            except Exception as e:
                print(f"  FAILED: {e}")
                status[sym] = {"ok": False, "error": str(e)}
        browser.close()
    return raw, status


# ------------------------------------------------------------------
# Source 3: plain HTTP (one probe, stops on 403)
# ------------------------------------------------------------------

def fetch_with_http(companies, start, end):
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Referer": ANN_PAGE,
        "Origin": "https://www.bseindia.com",
    })

    raw, status = {}, {}
    for c in companies:
        sym, code = c["nse_symbol"], str(c["bse_scrip_code"])

        def get_page(n, code=code):
            r = session.get(BSE_API, params=build_params(code, start, end, n), timeout=30)
            if r.status_code in (401, 403):
                raise Blocked(f"HTTP {r.status_code} (access denied by BSE)")
            r.raise_for_status()
            return r.json().get("Table") or []

        try:
            print(f"Fetching {sym} ({code}) ...")
            raw[sym] = paginate(get_page)
            status[sym] = {"ok": True}
        except Blocked as e:
            print(f"  BLOCKED: {e}. Stopping. Use --source file instead.")
            status[sym] = {"ok": False, "error": str(e)}
            break
        except (requests.RequestException, ValueError) as e:
            print(f"  FAILED: {e}")
            status[sym] = {"ok": False, "error": str(e)}
    session.close()
    return raw, status


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["file", "playwright", "http"], default="file")
    ap.add_argument("--only", help="NSE symbol to fetch alone, e.g. RELIANCE (writes announcements_test.json)")
    ap.add_argument("--headed", action="store_true", help="show the browser window (playwright)")
    args = ap.parse_args()

    companies = load_companies()
    if args.only:
        companies = [c for c in companies if c["nse_symbol"].upper() == args.only.upper()]
        if not companies:
            sys.exit(f"{args.only} not found in companies.json")

    end = datetime.now().date()
    start = end - timedelta(days=WINDOW_DAYS)

    print("=" * 50)
    print("FINN CORPORATE ANNOUNCEMENT PIPELINE")
    print("=" * 50)
    print(f"Source: {args.source} | Companies: {len(companies)} | {start} to {end}")

    if args.source == "file":
        raw, status = fetch_from_file(companies, start, end)
    elif args.source == "playwright":
        raw, status = fetch_with_playwright(companies, start, end, args.headed)
    else:
        raw, status = fetch_with_http(companies, start, end)

    # Normalise, filter to the window, and de-duplicate across companies
    by_symbol = {c["nse_symbol"]: c for c in companies}
    cutoff = datetime.combine(start, datetime.min.time()).isoformat()
    records, seen = [], set()
    for sym, rows in raw.items():
        count = missing_pdf = 0
        for row in rows:
            rec = normalise(row, by_symbol[sym])
            key = rec["news_id"] or (rec["bse_code"], rec["date_time"], rec["subject"])
            if key in seen:
                continue
            if rec["date_time"] and rec["date_time"] < cutoff:
                continue
            seen.add(key)
            records.append(rec)
            count += 1
            missing_pdf += rec["pdf_link"] is None
        status[sym].update({"announcements": count, "without_pdf": missing_pdf})

    records.sort(key=lambda r: r.get("date_time") or "", reverse=True)

    # Report
    ok = [s for s, v in status.items() if v.get("ok")]
    failed = [s for s, v in status.items() if not v.get("ok")]
    not_attempted = [c["nse_symbol"] for c in companies if c["nse_symbol"] not in status]

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(META_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "source": args.source,
            "window": [str(start), str(end)],
            "companies": status,
            "not_attempted": not_attempted,
        }, f, indent=2)

    print("\n" + "=" * 50)
    print(f"OK: {len(ok)} | Failed: {len(failed)} | Not attempted: {len(not_attempted)}")
    for s in failed:
        print(f"  x {s}: {status[s].get('error')}")
    print(f"Total announcements: {len(records)}")

    # Never overwrite good data with an empty result
    if not records:
        print("\nNo announcements collected. Existing data file was NOT touched.")
        sys.exit(1)

    out = TEST_OUTPUT_FILE if args.only else OUTPUT_FILE
    with open(out, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
    print(f"Saved to: {out}")

    print("\nLATEST 5")
    for r in records[:5]:
        print(f"  {r['company']} | {r['date_time']} | {r['subject']}")


if __name__ == "__main__":
    main()
