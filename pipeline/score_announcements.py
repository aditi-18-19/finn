"""
FINN Step 7: deterministic announcement priority scoring.

Inputs:
  data/announcements_classified.json
  companies.json
  data/prices.json

Output:
  data/announcements_scored.json

No LLM/API calls are made by this script.
"""
from __future__ import annotations

import argparse
import json
from bisect import bisect_left
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

# Importance points on a 0-10 scale. Categories not intended for the main
# feed remain deliberately low and cannot receive a market-reaction bonus.
CATEGORY_BASE = {
    "results": 10.0,
    "M&A": 9.5,
    "management_change": 9.0,
    "litigation": 9.0,
    "fund_raise": 8.0,
    "credit_rating": 7.5,
    "capex": 7.0,
    "dividend": 6.5,
    "bonus_split": 6.0,
    "insider_trading": 6.0,
    "pledging": 5.5,
    "earnings_call": 5.0,
    "press_release": 4.0,
    "investor_meetings": 2.0,
    "postal_ballot": 1.5,
    "agm_egm": 1.5,
    "unclassified": 0.5,
}
MANUAL_REVIEW_BASE = 1.0
MARKET_BONUS_ELIGIBLE = {
    "results", "M&A", "management_change", "litigation", "fund_raise",
    "credit_rating", "capex", "dividend", "bonus_split",
    "insider_trading", "pledging", "earnings_call", "press_release",
}

# Price-change and volume-ratio bonuses are additive and capped at 4 points.
def market_reaction_bonus(change_pct: float | None,
                          volume_ratio: float | None) -> tuple[float, list[str]]:
    bonus = 0.0
    reasons: list[str] = []

    if change_pct is not None:
        magnitude = abs(change_pct)
        if magnitude >= 5.0:
            bonus += 2.0
            reasons.append(f"price move {change_pct:+.2f}% (>=5%)")
        elif magnitude >= 3.0:
            bonus += 1.5
            reasons.append(f"price move {change_pct:+.2f}% (>=3%)")
        elif magnitude >= 2.0:
            bonus += 1.0
            reasons.append(f"price move {change_pct:+.2f}% (>=2%)")

    if volume_ratio is not None:
        if volume_ratio >= 3.0:
            bonus += 2.0
            reasons.append(f"volume {volume_ratio:.2f}x (>=3x)")
        elif volume_ratio >= 2.0:
            bonus += 1.5
            reasons.append(f"volume {volume_ratio:.2f}x (>=2x)")
        elif volume_ratio >= 1.5:
            bonus += 1.0
            reasons.append(f"volume {volume_ratio:.2f}x (>=1.5x)")

    return min(bonus, 4.0), reasons


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_price_index(prices: dict) -> dict:
    index = {}
    for symbol, company_data in prices.items():
        rows = company_data.get("records", [])
        by_date = {row["date"]: row for row in rows if row.get("date")}
        dates = sorted(by_date)
        index[symbol] = {"dates": dates, "by_date": by_date}
    return index


def market_row_for_announcement(item: dict, price_index: dict,
                                market_close: time = time(15, 30)):
    """Use same session for pre-close filings; next trading session after close."""
    symbol = item.get("nse_symbol")
    company_prices = price_index.get(symbol)
    if not company_prices:
        return None, None

    raw_dt = item.get("date_time")
    if not raw_dt:
        return None, None

    try:
        announced_at = datetime.fromisoformat(raw_dt.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None, None

    # BSE timestamps in this dataset are naive local timestamps (IST).
    # If an offset exists, preserve its local calendar/time representation.
    target_day = announced_at.date()
    if announced_at.timetz().replace(tzinfo=None) >= market_close:
        target_day += timedelta(days=1)

    dates = company_prices["dates"]
    pos = bisect_left(dates, target_day.isoformat())
    if pos >= len(dates):
        return None, None

    market_date = dates[pos]
    return market_date, company_prices["by_date"][market_date]


def score_announcement(item: dict, portfolio_weights: dict,
                       research_symbols: set, price_index: dict) -> dict:
    event_type = item.get("event_type")
    needs_review = bool(item.get("needs_manual_review")) or not event_type

    if needs_review:
        category_base = MANUAL_REVIEW_BASE
        category_reason = "Unresolved classification; conservative manual-review score."
    else:
        category_base = CATEGORY_BASE.get(event_type, 1.0)
        category_reason = f"{event_type} category base = {category_base:.1f}/10."

    symbol = item.get("nse_symbol")
    portfolio_weight = portfolio_weights.get(symbol)

    # Portfolio weights are percentages summing to 100. A 5% holding is the
    # neutral reference; research-only names use 1.0 rather than zero.
    if portfolio_weight is None:
        portfolio_multiplier = 1.0
        holding_type = "research" if symbol in research_symbols else "unmapped"
    else:
        portfolio_multiplier = portfolio_weight / 5.0
        holding_type = "portfolio"

    market_date, market_row = market_row_for_announcement(item, price_index)
    change_pct = market_row.get("daily_change_pct") if market_row else None
    volume_ratio = market_row.get("volume_ratio") if market_row else None

    bonus, bonus_reasons = (0.0, [])
    if (not needs_review) and event_type in MARKET_BONUS_ELIGIBLE:
        bonus, bonus_reasons = market_reaction_bonus(change_pct, volume_ratio)

    base_adjusted = category_base * portfolio_multiplier
    raw_score = base_adjusted + bonus
    score = round(min(raw_score, 100.0), 2)

    if needs_review:
        priority = "Routine"
    elif score >= 13.0:
        priority = "High"
    elif score >= 7.0:
        priority = "Medium"
    else:
        priority = "Routine"

    reasons = [category_reason]
    if portfolio_weight is not None:
        reasons.append(
            f"Portfolio weight {portfolio_weight:.1f}% "
            f"(multiplier {portfolio_multiplier:.2f}x)."
        )
    else:
        reasons.append("Research-only/unweighted company; neutral 1.00x multiplier.")
    if market_date:
        reasons.append(f"Market signal uses {market_date}.")
    else:
        reasons.append("No matching trading-session data; no market bonus.")
    reasons.extend(bonus_reasons)
    if needs_review:
        reasons.append("Manual-review items are kept Routine until classification is resolved.")

    scored = dict(item)
    scored.update({
        "priority_score": score,
        "priority": priority,
        "priority_details": {
            "category_base": category_base,
            "portfolio_weight_pct": portfolio_weight,
            "portfolio_multiplier": round(portfolio_multiplier, 3),
            "adjusted_category_score": round(base_adjusted, 2),
            "market_signal_date": market_date,
            "daily_change_pct": change_pct,
            "volume_ratio": volume_ratio,
            "market_bonus": bonus,
            "holding_type": holding_type,
            "needs_manual_review": needs_review,
            "explanation": reasons,
        },
    })
    return scored


def main():
    parser = argparse.ArgumentParser(description="Score FINN corporate announcements.")
    parser.add_argument("--announcements", type=Path, default=None)
    parser.add_argument("--companies", type=Path, default=None)
    parser.add_argument("--prices", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    announcements_path = args.announcements or project_root / "data" / "announcements_classified.json"
    companies_path = args.companies or project_root / "companies.json"
    prices_path = args.prices or project_root / "data" / "prices.json"
    output_path = args.output or project_root / "data" / "announcements_scored.json"

    announcements = load_json(announcements_path)
    companies = load_json(companies_path)
    prices = load_json(prices_path)

    portfolio_weights = {
        company["nse_symbol"]: float(company["weight"])
        for company in companies.get("portfolio", [])
    }
    research_symbols = {
        company["nse_symbol"] for company in companies.get("research", [])
    }
    price_index = build_price_index(prices)

    scored = [
        score_announcement(item, portfolio_weights, research_symbols, price_index)
        for item in announcements
    ]
    scored.sort(
        key=lambda row: (
            {"High": 0, "Medium": 1, "Routine": 2}[row["priority"]],
            -row["priority_score"],
            row.get("date_time", ""),
        )
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(scored, f, ensure_ascii=False, indent=2)

    counts = {}
    for row in scored:
        counts[row["priority"]] = counts.get(row["priority"], 0) + 1

    print(f"Scored {len(scored)} announcements.")
    print(f"Priority counts: {counts}")
    print(f"Output: {output_path}")
    print("\nTop 10:")
    for i, row in enumerate(scored[:10], start=1):
        print(
            f"{i:>2}. {row['priority']:<7} {row['priority_score']:>5.2f} "
            f"{row.get('nse_symbol', 'UNKNOWN'):<12} "
            f"{str(row.get('event_type') or 'manual_review'):<18} "
            f"{row.get('date_time', '')[:10]}  "
            f"{row.get('subject', row.get('headline', ''))[:95]}"
        )

    # Acceptance/integrity checks.
    ids = [row.get("news_id") for row in scored]
    assert len(scored) == len(announcements), "Announcement count changed."
    assert len(ids) == len(set(ids)), "Duplicate news_id after scoring."
    assert all(row["priority"] in {"High", "Medium", "Routine"} for row in scored)
    assert all(0 <= row["priority_score"] <= 100 for row in scored)
    assert sum(counts.values()) == len(announcements)
    print("\nValidation passed: row count, unique IDs, score bounds, and labels.")


if __name__ == "__main__":
    main()
