import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

from build_management_cards import build_cards, extract_action, extract_effective_date, extract_person, extract_role, validate_cards


class ManagementCardTests(unittest.TestCase):
    def test_person_extraction(self):
        self.assertEqual(extract_person("Appointment of Mr Vikram Janakiraman as Global CTO"), "Vikram Janakiraman")
        self.assertEqual(extract_person("Cessation of Girish Paranjpe as Independent Director"), "Girish Paranjpe")

    def test_action_extraction(self):
        self.assertEqual(extract_action("Re-appointment of Mr X as CFO"), "reappointment")
        self.assertEqual(extract_action("Retirement of Mr X as Chairman"), "retirement")
        self.assertEqual(extract_action("Cessation of Mr X as Director"), "cessation")

    def test_effective_date(self):
        self.assertEqual(extract_effective_date("with effect from 7th December, 2026"), "7th December, 2026")
        self.assertEqual(extract_effective_date("w.e.f. close of business hours November 1, 2026"), "November 1, 2026")

    def test_missing_fields_are_explicitly_flagged_for_filing_review(self):
        rows = [{"news_id":"4", "event_type":"management_change", "company":"Axis Bank", "nse_symbol":"AXISBANK", "headline":"Appointment of Independent Director", "subject":"Change in Management", "pdf_link":"https://example.com/filing.pdf"}]
        card = build_cards(rows, {})[0]
        self.assertTrue(card["data_quality"]["requires_filing_review"])
        self.assertFalse(card["data_quality"]["person_identified"])

    def test_transition_counterparty_is_in_summary(self):
        rows = [{"news_id":"5", "event_type":"management_change", "company":"HDFC Bank", "nse_symbol":"HDFCBANK", "headline":"Appointment of Mr Aditi Sharma as CEO", "subject":"Change in Management", "pdf_link":"https://example.com/filing.pdf"}]
        cards = build_cards(rows, {}, {"5": {"person_name":"Aditi Sharma", "counterparty_person":"Outgoing CEO Name", "role":"Chief Executive Officer"}})
        self.assertIn("Outgoing CEO Name", cards[0]["summary"])

    def test_role_extraction(self):
        role = extract_role("Appointment of Mr Vikram Janakiraman as the Global Chief Technical Operating Officer, designated as Senior Management Personnel")
        self.assertIn("Global Chief Technical Operating Officer", role)

    def test_builds_only_management_change_cards(self):
        rows = [
            {"news_id":"1", "event_type":"management_change", "company":"Cipla Ltd", "nse_symbol":"CIPLA", "headline":"Appointment of Mr Vikram Janakiraman as Global Chief Technical Operating Officer with effect from 7th December, 2026", "subject":"Change in Management", "pdf_link":"https://example.com/filing.pdf", "priority":"Medium", "priority_score":9.2},
            {"news_id":"2", "event_type":"results", "company":"Cipla Ltd", "nse_symbol":"CIPLA", "headline":"Quarterly results"},
        ]
        cards = build_cards(rows, {"vikram janakiraman":{"text":"30 years in pharma operations.","sources":[{"url":"https://example.com/profile"}]}})
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["person_name"], "Vikram Janakiraman")
        self.assertEqual(cards[0]["person_background"]["status"], "verified")
        self.assertEqual(validate_cards(cards), [])

    def test_missing_background_is_not_fabricated(self):
        rows = [{"news_id":"3", "event_type":"management_change", "company":"ONGC", "nse_symbol":"ONGC", "headline":"Change in Senior Management", "subject":"Change in Management", "pdf_link":"https://example.com/filing.pdf"}]
        card = build_cards(rows, {})[0]
        self.assertIsNone(card["person_name"])
        self.assertEqual(card["person_background"]["status"], "not_available")

    def test_current_sample_has_twenty_traceable_cards(self):
        with open(ROOT / "data" / "announcements_scored.json", encoding="utf-8") as f:
            data = json.load(f)
        with open(ROOT / "data" / "management_profiles.json", encoding="utf-8") as f:
            profiles = json.load(f)
        with open(ROOT / "data" / "management_overrides.json", encoding="utf-8") as f:
            overrides = json.load(f)
        cards = build_cards(data, profiles, overrides)
        self.assertEqual(len(cards), 20)
        self.assertEqual(validate_cards(cards), [])
        self.assertTrue(all(card.get("source_url") for card in cards))
        self.assertEqual(sum(c["person_background"]["status"] == "verified" for c in cards), 7)


if __name__ == "__main__":
    unittest.main()
