"""FINN Step 10: build a windowed portfolio digest and threshold alerts.

Inputs: data/announcements_scored.json, data/prices.json, optional result and
management cards. No network calls and no model calls are made in this step.

Examples (PowerShell, from project root):
  python pipeline/build_digest.py
  python pipeline/build_digest.py --hours 168
  python pipeline/build_digest.py --hours 720 --include-routine
  python pipeline/build_digest.py --hours 720 --price-change-pct 3 --volume-ratio 2
"""
from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_ANNOUNCEMENTS = DATA / "announcements_scored.json"
DEFAULT_PRICES = DATA / "prices.json"
DEFAULT_RESULTS = DATA / "results" / "results_cards.json"
DEFAULT_MANAGEMENT = DATA / "management_cards.json"
DEFAULT_DIGEST = DATA / "digest.json"
DEFAULT_ALERTS = DATA / "alerts.json"
MATERIAL_CATEGORIES = {
    "results", "M&A", "fund_raise", "management_change", "litigation",
    "insider_trading", "credit_rating", "pledging", "capex",
}
TIER_ORDER = {"high": 0, "medium": 1, "routine": 2, "unknown": 3}


def read_json(path: Path, required: bool = True) -> Any:
    if not path.exists():
        if required:
            raise FileNotFoundError(f"Required input not found: {path}")
        return None
    return json.loads(path.read_text(encoding="utf-8-sig"))


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        # BSE/Yahoo data are exchange-local naive timestamps. Keep comparisons
        # consistent by treating naive values as local-naive, not UTC.
        return dt.replace(tzinfo=None) if dt.tzinfo else dt
    except (TypeError, ValueError):
        return None


def finite_number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def first(row: dict, *keys: str, default=None):
    for key in keys:
        if row.get(key) is not None:
            return row[key]
    return default


def tier_for(row: dict) -> str:
    value = str(first(row, "priority_tier", "priority", "tier", "priority_level", default="unknown")).lower()
    if value in {"high", "medium", "routine"}:
        return value
    score = finite_number(first(row, "priority_score", "score"))
    if score is not None:
        return "high" if score >= 13 else "medium" if score >= 7 else "routine"
    return "unknown"


def score_for(row: dict) -> float:
    return finite_number(first(row, "priority_score", "score")) or 0.0


def load_cards(path: Path | None) -> dict[str, dict]:
    """Load enrichment cards using their actual schema identifiers.

    Management cards use card_id (the source announcement UUID). Results cards
    are company-level records whose source_pdf may encode the source UUID in
    its filename. Generic results filenames are retained for a company-level
    fallback, resolved to one matching results announcement in make_digest.
    """
    if not path or not path.exists():
        return {}
    payload = read_json(path, required=False)
    if isinstance(payload, dict):
        rows = payload.get("cards", payload.get("results", []))
    else:
        rows = payload or []

    indexed: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        card_id = row.get("card_id") or row.get("news_id")
        if card_id:
            indexed[f"id:{card_id}"] = row
        source_pdf = str(row.get("source_pdf") or "")
        match = re.search(r"_([0-9a-fA-F]{8})\.pdf$", source_pdf)
        if match:
            indexed[f"prefix:{match.group(1).lower()}"] = row
        elif row.get("company"):
            indexed[f"company:{row['company']}"] = row
    return indexed


def _fallback_result_targets(announcements: list[dict], results_cards: dict[str, dict],
                             selected_ids: set[str]) -> dict[str, str]:
    """Choose one best matching result announcement per company-level card."""
    targets: dict[str, str] = {}
    for key, card in results_cards.items():
        if not key.startswith("company:"):
            continue
        symbol = key.split(":", 1)[1]
        candidates = [
            row for row in announcements
            if isinstance(row, dict)
            and first(row, "nse_symbol", "symbol") == symbol
            and str(first(row, "event_type", "category_label", default="")).lower() == "results"
            and str(row.get("news_id", "")) in selected_ids
        ]
        if not candidates:
            continue

        def relevance(row: dict) -> tuple[int, str]:
            text = " ".join(str(row.get(k) or "") for k in ("subject", "headline", "description")).lower()
            score = 0
            if "unaudited financial results" in text:
                score += 8
            elif "financial results" in text:
                score += 6
            elif "financial result" in text:
                score += 5
            if "quarter" in text and ("ended" in text or "ending" in text):
                score += 2
            if "investor presentation" in text or "earnings presentation" in text:
                score -= 1
            if "press release" in text or "media release" in text:
                score -= 1
            return score, str(first(row, "date_time", "timestamp", default=""))

        best = max(candidates, key=relevance)
        targets[str(best.get("news_id", ""))] = symbol
    return targets


def make_digest(announcements: list[dict], prices: dict, hours: int,
                as_of: datetime, include_routine: bool,
                results_cards: dict, management_cards: dict,
                portfolio_symbols: set[str] | None = None) -> dict:
    start = as_of - timedelta(hours=hours)
    portfolio = set(portfolio_symbols) if portfolio_symbols is not None else set(prices.keys())
    selected, collapsed = [], 0
    # First identify rows in the requested window so company-level result
    # cards can be attached to the single most relevant result filing.
    window_rows = [
        row for row in announcements
        if isinstance(row, dict)
        and (not portfolio or first(row, "nse_symbol", "symbol") in portfolio)
        and (parse_dt(first(row, "date_time", "timestamp", "announced_at")) is not None)
        and start <= parse_dt(first(row, "date_time", "timestamp", "announced_at")) <= as_of
        and (include_routine or tier_for(row) != "routine")
    ]
    fallback_targets = _fallback_result_targets(
        announcements, results_cards, {str(r.get("news_id", "")) for r in window_rows}
    )
    for row in announcements:
        if not isinstance(row, dict):
            continue
        symbol = first(row, "nse_symbol", "symbol")
        # The portfolio digest is deliberately limited to the configured
        # portfolio, not the ad-hoc research universe.
        if portfolio and symbol not in portfolio:
            continue
        dt = parse_dt(first(row, "date_time", "timestamp", "announced_at"))
        if dt is None or not (start <= dt <= as_of):
            continue
        tier = tier_for(row)
        if tier == "routine" and not include_routine:
            collapsed += 1
            continue
        item = dict(row)
        item["priority_tier"] = tier
        item["priority_score"] = score_for(row)
        item["digest_enrichment"] = None
        news_id = str(row.get("news_id", ""))
        symbol_key = first(row, "nse_symbol", "symbol")
        result_card = results_cards.get(f"id:{news_id}") or results_cards.get(
            f"prefix:{news_id[:8].lower()}" if news_id else ""
        )
        if result_card is None and fallback_targets.get(news_id):
            result_card = results_cards.get(f"company:{fallback_targets[news_id]}")
        management_card = management_cards.get(f"id:{news_id}")
        if result_card is not None:
            item["digest_enrichment"] = {"type": "quarterly_results", "card": result_card}
        elif management_card is not None:
            item["digest_enrichment"] = {"type": "management_change", "card": management_card}
        selected.append(item)
    # Within each priority tier, highest score first, then newest first.
    selected.sort(key=lambda r: (TIER_ORDER.get(r["priority_tier"], 3),
                                 -r["priority_score"],
                                 -(parse_dt(first(r, "date_time", "timestamp", "announced_at")) or datetime.min).timestamp()),)
    counts = {tier: sum(r["priority_tier"] == tier for r in selected) for tier in ("high", "medium", "routine", "unknown")}
    return {
        "schema_version": "1.0",
        "generated_at": as_of.isoformat(timespec="seconds"),
        "window": {"hours": hours, "start": start.isoformat(timespec="seconds"), "end": as_of.isoformat(timespec="seconds")},
        "portfolio_symbols": sorted(portfolio),
        "summary": {"shown": len(selected), "collapsed_routine": collapsed, "by_priority": counts},
        "items": selected,
    }


def make_alerts(announcements: list[dict], prices: dict, config: dict,
                as_of: datetime, portfolio_symbols: set[str] | None = None,
                announcement_window_hours: int = 24) -> dict:
    categories = set(config.get("material_categories", MATERIAL_CATEGORIES))
    price_threshold = float(config.get("price_change_pct", 3.0))
    volume_threshold = float(config.get("volume_ratio", 2.0))
    alert_rows: list[dict] = []
    portfolio = set(portfolio_symbols) if portfolio_symbols is not None else set(prices.keys())
    announcement_cutoff = as_of - timedelta(hours=announcement_window_hours)
    for row in announcements:
        if not isinstance(row, dict):
            continue
        symbol = first(row, "nse_symbol", "symbol")
        category = first(row, "event_type", "category_label")
        announced_at = parse_dt(first(row, "date_time", "timestamp"))
        if (symbol in portfolio and category in categories and announced_at is not None
                and announcement_cutoff <= announced_at <= as_of):
            alert_rows.append({
                "alert_id": f"announcement:{row.get('news_id') or symbol + ':' + str(row.get('date_time'))}",
                "type": "material_announcement", "symbol": symbol,
                "company": row.get("company"), "category": category,
                "priority_tier": tier_for(row), "priority_score": score_for(row),
                "timestamp": first(row, "date_time", "timestamp"),
                "headline": first(row, "headline", "subject"),
                "reason": f"Material announcement category: {category}",
                "source_url": row.get("pdf_link"), "source": row.get("source", "BSE"),
            })
    for symbol, company in prices.items():
        if symbol not in portfolio:
            continue
        records = company.get("records", []) if isinstance(company, dict) else []
        valid = [r for r in records if parse_dt(r.get("date")) and parse_dt(r.get("date")) <= as_of]
        if not valid:
            continue
        latest = max(valid, key=lambda r: parse_dt(r.get("date")))
        change = finite_number(latest.get("daily_change_pct"))
        volume_ratio = finite_number(latest.get("volume_ratio"))
        triggered = []
        if change is not None and abs(change) >= price_threshold:
            triggered.append({"metric": "daily_change_pct", "value": change, "threshold": price_threshold})
        if volume_ratio is not None and volume_ratio >= volume_threshold:
            triggered.append({"metric": "volume_ratio", "value": volume_ratio, "threshold": volume_threshold})
        if triggered:
            alert_rows.append({
                "alert_id": f"market:{symbol}:{latest['date']}", "type": "market_anomaly",
                "symbol": symbol, "company": company.get("name", symbol),
                "timestamp": latest.get("date"), "close": latest.get("close"),
                "daily_change_pct": change, "volume_ratio": volume_ratio,
                "triggers": triggered,
                "reason": "; ".join(f"{t['metric']}={t['value']} crossed {t['threshold']}" for t in triggered),
                "source": "Yahoo Finance EOD data",
            })
    alert_rows.sort(key=lambda a: parse_dt(a.get("timestamp")) or datetime.min, reverse=True)
    return {
        "schema_version": "1.0", "generated_at": as_of.isoformat(timespec="seconds"),
        "config": {"material_categories": sorted(categories), "price_change_pct": price_threshold,
                   "volume_ratio": volume_threshold},
        "summary": {"total": len(alert_rows),
                    "material_announcements": sum(a["type"] == "material_announcement" for a in alert_rows),
                    "market_anomalies": sum(a["type"] == "market_anomaly" for a in alert_rows)},
        "alerts": alert_rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Build FINN portfolio digest and threshold alerts")
    ap.add_argument("--announcements", type=Path, default=DEFAULT_ANNOUNCEMENTS)
    ap.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    ap.add_argument("--results-cards", type=Path, default=DEFAULT_RESULTS)
    ap.add_argument("--management-cards", type=Path, default=DEFAULT_MANAGEMENT)
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--include-routine", action="store_true")
    ap.add_argument("--price-change-pct", type=float, default=3.0)
    ap.add_argument("--volume-ratio", type=float, default=2.0)
    ap.add_argument("--alert-hours", type=int, default=24, help="Announcement alert lookback window")
    ap.add_argument("--as-of", help="Optional ISO timestamp for reproducible runs")
    ap.add_argument("--digest-out", type=Path, default=DEFAULT_DIGEST)
    ap.add_argument("--alerts-out", type=Path, default=DEFAULT_ALERTS)
    args = ap.parse_args()
    if args.hours <= 0 or args.alert_hours <= 0 or args.price_change_pct <= 0 or args.volume_ratio <= 0:
        ap.error("hours and alert thresholds must be positive")
    announcements = read_json(args.announcements)
    prices = read_json(args.prices)
    if not isinstance(announcements, list) or not isinstance(prices, dict):
        ap.error("Unexpected input schema: announcements must be a list and prices a company-keyed object")
    as_of = parse_dt(args.as_of) if args.as_of else datetime.now()
    if as_of is None:
        ap.error("--as-of must be a valid ISO timestamp")
    results_cards = load_cards(args.results_cards)
    management_cards = load_cards(args.management_cards)
    companies_path = ROOT / "companies.json"
    companies_payload = read_json(companies_path, required=False)
    portfolio_symbols = ({c["nse_symbol"] for c in companies_payload.get("portfolio", [])}
                         if isinstance(companies_payload, dict) and companies_payload.get("portfolio")
                         else set(prices.keys()))
    digest = make_digest(announcements, prices, args.hours, as_of, args.include_routine,
                         results_cards, management_cards, portfolio_symbols)
    alerts = make_alerts(announcements, prices,
                         {"price_change_pct": args.price_change_pct, "volume_ratio": args.volume_ratio}, as_of, portfolio_symbols, args.alert_hours)
    args.digest_out.parent.mkdir(parents=True, exist_ok=True)
    args.alerts_out.parent.mkdir(parents=True, exist_ok=True)
    args.digest_out.write_text(json.dumps(digest, indent=2, ensure_ascii=False), encoding="utf-8")
    args.alerts_out.write_text(json.dumps(alerts, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Digest: {digest['summary']['shown']} shown | {digest['summary']['collapsed_routine']} routine collapsed")
    print("Priority:", digest["summary"]["by_priority"])
    print(f"Alerts: {alerts['summary']['total']} ({alerts['summary']['material_announcements']} material in {args.alert_hours}h, {alerts['summary']['market_anomalies']} latest-EOD market)")
    print(f"Saved: {args.digest_out}")
    print(f"Saved: {args.alerts_out}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
