"""FINN Step 9: turn prioritised management-change filings into traceable cards.

This stage is deliberately conservative: it extracts only what is present in
announcement metadata and merges optional, explicitly sourced biographies.
It never invents a person's identity, role, effective date, or background.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "data" / "announcements_scored.json"
DEFAULT_PROFILES = ROOT / "data" / "management_profiles.json"
DEFAULT_OVERRIDES = ROOT / "data" / "management_overrides.json"
DEFAULT_OUTPUT = ROOT / "data" / "management_cards.json"

NAME_PATTERNS = [
    # Honorific + Indian / international personal name; stop at role/action clauses.
    re.compile(r"\b(?:Mr\.?|Ms\.?|Mrs\.?|Dr\.?|Shri|Smt\.?|Prof\.?)\s+([A-Z][A-Za-z.'’-]*(?:\s+[A-Z][A-Za-z.'’-]*){1,4})", re.I),
    re.compile(r"\b(?:appointment|re-appointment|resignation|retirement|cessation)\s+of\s+([A-Z][A-Za-z.'’-]*(?:\s+[A-Z][A-Za-z.'’-]*){1,3})", re.I),
]

ACTION_PATTERNS = [
    ("reappointment", re.compile(r"\bre[- ]appointment\b|\breappointment\b", re.I)),
    ("appointment", re.compile(r"\bappointment\b|\bappointed\b", re.I)),
    ("resignation", re.compile(r"\bresign(?:ation|ed)?\b", re.I)),
    ("retirement", re.compile(r"\bretir(?:e|ement|ed)\b", re.I)),
    ("cessation", re.compile(r"\bcessation\b|\bceases?\b", re.I)),
    ("change", re.compile(r"\bchange in (?:senior )?management\b|\bchange in directors?\b", re.I)),
]

DATE_PATTERNS = [
    # Exchange filings commonly insert "close of business hours" after w.e.f.
    re.compile(r"(?:w\.?e\.?f\.?|with effect from|effective(?:\s+from)?)\s*(?:close of business hours\s*)?[, :]?\s*((?:\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+,?\s*\d{4})|(?:[A-Za-z]+\s+\d{1,2},?\s*\d{4})|(?:\d{1,2}[./-]\d{1,2}[./-]\d{4}))", re.I),
    re.compile(r"(?:from|with effect from)\s+((?:\d{1,2}(?:st|nd|rd|th)?\s+)?[A-Za-z]+\s+\d{4})", re.I),
]

ROLE_PATTERNS = [
    re.compile(r"\bas\s+(?:the\s+)?([^\n]+?)(?:,?\s+(?:designated|with effect|w\.?e\.?f\.?|effective)|\s+of\s+(?:the\s+)?(?:company|bank)|[.;]|$)", re.I),
    re.compile(r"\bto the post of\s+(.+?)(?:\s+w\.?e\.?f\.?|,|[.;]|$)", re.I),
]


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def combined_text(row: dict) -> str:
    # Prefer explicit headline/description; subject is useful for generic filings.
    return "\n".join(str(row.get(k) or "") for k in ("headline", "description", "subject"))


def extract_action(text: str) -> str:
    for action, pattern in ACTION_PATTERNS:
        if pattern.search(text):
            return action
    return "unspecified_change"


def extract_person(text: str) -> str | None:
    for pattern in NAME_PATTERNS:
        match = pattern.search(text)
        if match:
            name = re.sub(r"\s+", " ", match.group(1)).strip(" ,.;:-")
            # The source headline often continues directly into the role clause.
            name = re.split(r"\s+(?:as|to the post of|to the|for a period|with effect|w\.?e\.?f\.?|effective)\b", name, maxsplit=1, flags=re.I)[0]
            name = name.strip(" ,.;:-")
            bad_starts = {"director", "senior", "management", "personnel", "chairman", "appointment", "disclosure"}
            # Reject role labels and truncated generic filing text rather than inventing a person.
            if len(name.split()) >= 2 and name.split()[0].casefold() not in bad_starts and not re.search(r"\b(senior management personnel|independent director)\b", name, re.I):
                return name
    return None


def extract_role(text: str) -> str | None:
    for pattern in ROLE_PATTERNS:
        match = pattern.search(text)
        if match:
            role = re.sub(r"\s+", " ", match.group(1)).strip(" ,.;:-")
            if 2 <= len(role) <= 120:
                return role
    # Fallback: role explicitly stated in common headline patterns.
    match = re.search(r"as\s+(?:an?\s+)?((?:independent|non-executive|executive|whole[- ]time|managing|chief|global|additional)[^,.;]{2,90})", text, re.I)
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else None


def extract_effective_date(text: str) -> str | None:
    for pattern in DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip(" ,.")
    return None


def build_summary(company: str, action: str, person: str | None, role: str | None, effective: str | None, counterparty: str | None = None) -> str:
    who = person or "an individual not named in the available announcement text"
    if action in {"change", "unspecified_change"}:
        verb = "reports a management/directorate change involving"
    else:
        verb = f"reports {action} involving"
    parts = [f"{company}: {verb} {who}."]
    if counterparty:
        parts.append(f"The filing also identifies {counterparty} in connection with this transition.")
    if role:
        parts.append(f"Role: {role}.")
    if effective:
        parts.append(f"Effective date: {effective}.")
    return " ".join(parts)


def build_cards(announcements: list[dict], profiles: dict[str, dict], overrides: dict[str, dict] | None = None) -> list[dict]:
    cards = []
    for row in announcements:
        if row.get("event_type") != "management_change":
            continue
        text = combined_text(row)
        symbol = row.get("nse_symbol") or "UNKNOWN"
        override = (overrides or {}).get(str(row.get("news_id")), {})
        person = override.get("person_name") or next((extract_person(str(row.get(k) or "")) for k in ("headline", "subject", "description") if extract_person(str(row.get(k) or ""))), None)
        action = override.get("action") or extract_action(text)
        role = override.get("role") or next((extract_role(str(row.get(k) or "")) for k in ("headline", "subject", "description") if extract_role(str(row.get(k) or ""))), None)
        effective = override.get("effective_date") or extract_effective_date(text)
        profile = profiles.get(person.casefold(), {}) if person else {}
        evidence = {
            "headline": row.get("headline"),
            "subject": row.get("subject"),
            "description": row.get("description"),
        }
        cards.append({
            "card_id": str(row.get("news_id") or f"{symbol}-{row.get('date_time','unknown')}"),
            "company": row.get("company"),
            "nse_symbol": symbol,
            "date_time": row.get("date_time"),
            "priority": row.get("priority", "Routine"),
            "priority_score": row.get("priority_score"),
            "action": action,
            "person_name": person,
            "counterparty_person": override.get("counterparty_person"),
            "role": role,
            "effective_date": effective,
            "summary": build_summary(row.get("company") or symbol, action, person, role, effective, override.get("counterparty_person")),
            "person_background": {
                "text": profile.get("text"),
                "sources": profile.get("sources", []),
                "status": "verified" if profile.get("text") and profile.get("sources") else "not_available",
            },
            "evidence": evidence,
            "source": row.get("source"),
            "source_url": row.get("pdf_link"),
            "classification_confidence": row.get("classification_confidence"),
            "needs_manual_review": bool(row.get("needs_manual_review")),
            "data_quality": {
                "person_identified": bool(person),
                "role_identified": bool(role),
                "effective_date_identified": bool(effective),
                "requires_filing_review": not bool(person and role and effective),
            },
            "market_context": {
                "signal_date": (row.get("priority_details") or {}).get("market_signal_date"),
                "daily_change_pct": (row.get("priority_details") or {}).get("daily_change_pct"),
                "volume_ratio": (row.get("priority_details") or {}).get("volume_ratio"),
            },
        })
    cards.sort(key=lambda x: (x.get("priority_score") or 0, x.get("date_time") or ""), reverse=True)
    return cards


def validate_cards(cards: list[dict]) -> list[str]:
    issues = []
    ids = [c["card_id"] for c in cards]
    if len(ids) != len(set(ids)):
        issues.append("duplicate_card_ids")
    for card in cards:
        if not card.get("source_url"):
            issues.append(f"missing_source_url:{card['card_id']}")
        if not card.get("evidence", {}).get("headline") and not card.get("evidence", {}).get("subject"):
            issues.append(f"missing_announcement_evidence:{card['card_id']}")
        if card.get("person_background", {}).get("status") == "verified" and not card["person_background"].get("sources"):
            issues.append(f"untraceable_background:{card['card_id']}")
    return issues


def main() -> None:
    parser = argparse.ArgumentParser(description="Build FINN management-change cards.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILES)
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    announcements = load_json(args.input)
    profiles = load_json(args.profiles, default={})
    overrides = load_json(args.overrides, default={})
    if not isinstance(announcements, list):
        raise ValueError("Input must be a JSON list.")
    if not isinstance(profiles, dict):
        raise ValueError("Profiles must be a JSON object keyed by lower-case person name.")
    if not isinstance(overrides, dict):
        raise ValueError("Overrides must be a JSON object keyed by announcement news_id.")

    cards = build_cards(announcements, profiles, overrides)
    issues = validate_cards(cards)
    incomplete = [c for c in cards if c["data_quality"]["requires_filing_review"]]
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_file": str(args.input.name),
        "total_cards": len(cards),
        "verified_backgrounds": sum(c["person_background"]["status"] == "verified" for c in cards),
        "backgrounds_unavailable": sum(c["person_background"]["status"] != "verified" for c in cards),
        "cards_requiring_filing_review": len(incomplete),
        "completeness": {
            "person_identified": sum(c["data_quality"]["person_identified"] for c in cards),
            "role_identified": sum(c["data_quality"]["role_identified"] for c in cards),
            "effective_date_identified": sum(c["data_quality"]["effective_date_identified"] for c in cards),
        },
        "validation": {"status": "passed" if not issues else "failed", "issues": issues},
        "cards": cards,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"Management-change cards: {len(cards)}")
    print(f"Verified backgrounds: {payload['verified_backgrounds']}")
    print(f"Background unavailable: {payload['backgrounds_unavailable']}")
    print(f"Cards requiring filing review: {payload['cards_requiring_filing_review']}")
    print("Completeness: " + ", ".join(f"{k}={v}/{len(cards)}" for k, v in payload["completeness"].items()))
    print(f"Structural validation: {payload['validation']['status']} ({len(issues)} issues)")
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
