
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Generator

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(os.getenv("FINN_PROJECT_ROOT", ROOT)).resolve()
DATA = PROJECT_ROOT / "data"
PIPELINE = PROJECT_ROOT / "pipeline"

app = FastAPI(
    title="FINN API",
    version="0.1.0",
    description="API for FINN's portfolio feed, alerts, company pages and grounded research.",
)

origins = [
    x.strip() for x in os.getenv(
        "FINN_CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000"
    ).split(",") if x.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def load_json(name: str, default: Any = None) -> Any:
    path = DATA / name
    if not path.exists():
        if default is not None:
            return default
        raise HTTPException(status_code=503, detail=f"Required data file is missing: data/{name}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=503, detail=f"Invalid JSON in data/{name}: {exc}") from exc


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def company_universe() -> dict[str, dict]:
    path = PROJECT_ROOT / "companies.json"
    if not path.exists():
        # Some project versions keep a copy under data/.
        path = DATA / "companies.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    result = {}
    for group in ("portfolio", "research"):
        for company in payload.get(group, []):
            symbol = company.get("nse_symbol")
            if symbol:
                result[symbol.upper()] = {**company, "holding_type": group}
    return result


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "finn-api",
        "data_directory_exists": DATA.exists(),
        "project_root": str(PROJECT_ROOT),
    }


@app.get("/feed")
def get_feed(hours: int = Query(default=24, ge=1, le=2160)) -> dict:
    payload = load_json("digest.json")
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    items = []
    for item in payload.get("items", []):
        dt = parse_dt(item.get("date_time"))
        if dt is None or dt >= cutoff:
            items.append(item)
    items.sort(
        key=lambda x: (parse_dt(x.get("date_time")) or datetime.min.replace(tzinfo=timezone.utc),
                       float(x.get("priority_score") or 0)),
        reverse=True,
    )
    return {
        "generated_at": payload.get("generated_at"),
        "window_hours": hours,
        "count": len(items),
        "summary": payload.get("summary", {}),
        "items": items,
    }


@app.get("/alerts")
def get_alerts(
    limit: int = Query(default=100, ge=1, le=500),
    alert_type: str | None = None,
    symbol: str | None = None,
) -> dict:
    payload = load_json("alerts.json")
    alerts = payload.get("alerts", [])
    if alert_type:
        alerts = [a for a in alerts if a.get("type") == alert_type]
    if symbol:
        alerts = [a for a in alerts if a.get("symbol", "").upper() == symbol.upper()]
    alerts = sorted(alerts, key=lambda a: a.get("timestamp", ""), reverse=True)
    return {
        "generated_at": payload.get("generated_at"),
        "config": payload.get("config", {}),
        "summary": payload.get("summary", {}),
        "count": len(alerts[:limit]),
        "alerts": alerts[:limit],
    }


@app.get("/company/{symbol}")
def get_company(symbol: str) -> dict:
    symbol = symbol.upper().strip()
    if not symbol or len(symbol) > 20:
        raise HTTPException(status_code=400, detail="Invalid ticker symbol.")
    universe = company_universe()
    prices = load_json("prices.json", {})
    price_entry = prices.get(symbol)
    if not price_entry and symbol not in universe:
        raise HTTPException(status_code=404, detail=f"{symbol} is not in FINN's configured company universe.")

    digest = load_json("digest.json", {"items": []})
    alerts = load_json("alerts.json", {"alerts": []})
    # Step 8 stores the canonical results card artifact under data/results/.
    # Retain support for the legacy data/results_cards.json location.
    results_path = DATA / "results" / "results_cards.json"
    if not results_path.is_file():
        results_path = DATA / "results_cards.json"
    if results_path.is_file():
        try:
            results_payload = json.loads(results_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=503, detail=f"Could not read results cards: {exc}") from exc
    else:
        results_payload = {"results": []}
    management_payload = load_json("management_cards.json", {"cards": []})

    announcements = [
        x for x in digest.get("items", [])
        if x.get("nse_symbol", "").upper() == symbol
    ]
    announcements.sort(key=lambda x: x.get("date_time", ""), reverse=True)
    company_alerts = [x for x in alerts.get("alerts", []) if x.get("symbol", "").upper() == symbol]
    results = [
        x for x in results_payload.get("results", [])
        if x.get("company", "").upper() == symbol
    ]
    management = [
        x for x in management_payload.get("cards", [])
        if x.get("nse_symbol", "").upper() == symbol
    ]
    management.sort(key=lambda x: x.get("date_time", ""), reverse=True)

    return {
        "company": universe.get(symbol, {
            "nse_symbol": symbol,
            "name": price_entry.get("name") if price_entry else symbol,
            "sector": price_entry.get("sector") if price_entry else None,
        }),
        "price": price_entry or {"name": None, "sector": None, "records": []},
        "latest_price": (price_entry or {}).get("records", [])[-1] if (price_entry or {}).get("records") else None,
        "announcements": announcements[:30],
        "alerts": company_alerts[:30],
        "results_cards": results,
        "management_cards": management[:20],
    }


class AskRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    question: str = Field(min_length=3, max_length=2000)
    top_k: int = Field(default=10, ge=1, le=20)


@app.post("/ask")
def ask_company(request: AskRequest) -> dict:
    symbol = request.symbol.upper().strip()
    script = PIPELINE / "research_qa.py"
    if not script.exists():
        raise HTTPException(status_code=503, detail="Grounded Q&A is unavailable: pipeline/research_qa.py is missing.")
    command = [
        sys.executable, str(script), "--symbol", symbol,
        "--question", request.question, "--top-k", str(request.top_k),
    ]
    answer_dir = DATA / "research_answers" / symbol
    before = set(answer_dir.glob("*.json")) if answer_dir.exists() else set()
    try:
        result = subprocess.run(
            command, cwd=PROJECT_ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=180,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="Research question timed out after 180 seconds.") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "No diagnostic output").strip()[-3000:]
        raise HTTPException(status_code=502, detail={
            "message": "Grounded analysis did not complete. No answer has been fabricated.",
            "diagnostic": detail,
        })
    after = set(answer_dir.glob("*.json")) if answer_dir.exists() else set()
    audit_files = sorted(after - before, key=lambda p: p.stat().st_mtime, reverse=True)
    if not audit_files:
        raise HTTPException(status_code=502, detail="Q&A returned successfully but did not create a new auditable answer file.")
    try:
        audit = json.loads(audit_files[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail=f"Could not read Q&A audit output: {exc}") from exc
    return {
        "symbol": symbol,
        "question": request.question,
        "answer": audit.get("answer", "Not found in the documents"),
        "claims": audit.get("claims", []),
        "rejected_claims": audit.get("rejected_claims", []),
        "retrieval": audit.get("retrieval", {}),
        "audit_file": audit.get("saved_to"),
    }


@app.get("/research/{symbol}")
def run_research(
    symbol: str,
    question: str | None = None,
    annual_reports: int = Query(default=2, ge=0, le=5),
    transcripts: int = Query(default=4, ge=0, le=10),
) -> StreamingResponse:
    try:
        from pipeline.research import research as run_universal_research
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Universal research pipeline is unavailable: {exc}") from exc

    def events() -> Generator[str, None, None]:
        import queue
        import threading

        q: queue.Queue = queue.Queue()
        finished = object()

        def worker():
            try:
                result = run_universal_research(
                    symbol, question, annual_reports=annual_reports,
                    transcripts=transcripts,
                    progress_callback=lambda event: q.put({"event": "progress", "data": event}),
                )
                q.put({"event": "result", "data": result})
            except Exception as exc:
                q.put({"event": "error", "data": {"message": str(exc)}})
            finally:
                q.put(finished)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        while True:
            item = q.get()
            if item is finished:
                break
            yield f"event: {item['event']}\ndata: {json.dumps(item['data'], ensure_ascii=False)}\n\n"
        thread.join(timeout=1)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- Step 18 additions: alert-rule configuration API ---
from pathlib import Path as _Path
from pydantic import BaseModel as _BaseModel, Field as _Field

RULES_PATH = PROJECT_ROOT / "config" / "rules.json"

class AlertRulesUpdate(_BaseModel):
    price_change_pct: float = _Field(ge=0.5, le=20)
    volume_ratio: float = _Field(ge=1.0, le=10)
    material_categories: list[str] = _Field(min_length=1)

@app.get("/alert-rules")
def get_alert_rules() -> dict:
    if not RULES_PATH.exists():
        raise HTTPException(status_code=503, detail="Alert rules file is missing: config/rules.json")
    try:
        return json.loads(RULES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail=f"Could not read alert rules: {exc}") from exc

@app.put("/alert-rules")
def update_alert_rules(payload: AlertRulesUpdate) -> dict:
    allowed = {
        "results", "M&A", "fund_raise", "management_change", "litigation",
        "insider_trading", "credit_rating", "pledging", "capex",
        "dividend", "bonus_split", "earnings_call", "investor_meetings",
        "press_release", "postal_ballot", "agm_egm"
    }
    unknown = sorted(set(payload.material_categories) - allowed)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown categories: {', '.join(unknown)}")
    RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    updated = {
        "price_change_pct": payload.price_change_pct,
        "volume_ratio": payload.volume_ratio,
        "material_categories": payload.material_categories,
    }
    temporary = RULES_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(updated, indent=2), encoding="utf-8")
    temporary.replace(RULES_PATH)
    return updated
