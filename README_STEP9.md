# FINN Step 9 — Management-change cards

## Purpose

Convert the `management_change` records in Step 7's `data/announcements_scored.json` into concise, source-traceable cards. Each card carries the company, filing date, priority/score, change action, named person (when explicit), role, effective date, filing evidence, BSE source URL and market context.

Where a short professional background is supported by a company filing or company leadership page, it is included with source links. If a reliable profile is not available, the card says `not_available`; it does not invent a biography. `management_overrides.json` is reserved for facts verified against the original filing when the feed headline is incomplete.

## Files

- `pipeline/build_management_cards.py` — card-generation pipeline.
- `data/management_profiles.json` — sourced professional backgrounds.
- `data/management_overrides.json` — corrections/enrichment for specific announcement IDs verified from filings/company sources.
- `data/management_cards.json` — generated output (20 cards for the current 335-announcement sample).
- `tests/test_management_cards.py` — unit tests.

## Run

From the project root, with `.venv` active:

```powershell
python pipeline\build_management_cards.py
```

Optional paths:

```powershell
python pipeline\build_management_cards.py `
  --input data\announcements_scored.json `
  --profiles data\management_profiles.json `
  --overrides data\management_overrides.json `
  --output data\management_cards.json
```

Run tests:

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

## Output and limitations

The current scored dataset contains 20 management-change announcements. The generated sample has 20 cards; 7 have sourced professional backgrounds and 13 do not. Missing biographies are intentionally left unavailable. Some announcements have generic exchange headlines; those cards retain the filing link and show an unknown person/role rather than guessing. The source PDFs remain the authoritative detail.

This stage does not scrape LinkedIn or depend on a paid enrichment API. It uses primary company disclosures/company leadership pages where possible and records source URLs. It is designed to be re-run whenever Step 7 produces a new `announcements_scored.json`.
