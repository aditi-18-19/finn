import importlib.util
import unittest
from datetime import datetime
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "pipeline" / "build_digest.py"
spec = importlib.util.spec_from_file_location("build_digest", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class DigestTests(unittest.TestCase):
    def setUp(self):
        self.as_of = datetime.fromisoformat("2026-11-15T12:00:00")
        self.announcements = [
            {"news_id": "a", "nse_symbol": "ABC", "company": "ABC Ltd", "date_time": "2026-11-15T10:00:00", "event_type": "results", "priority_tier": "High", "priority_score": 15, "headline": "Q2 results", "pdf_link": "https://example.com/a.pdf"},
            {"news_id": "b", "nse_symbol": "ABC", "date_time": "2026-11-15T09:00:00", "event_type": "investor_meetings", "priority_tier": "Routine", "priority_score": 2, "headline": "Investor meeting"},
            {"news_id": "c", "nse_symbol": "XYZ", "date_time": "2026-11-15T09:00:00", "event_type": "M&A", "priority_tier": "Medium", "priority_score": 9, "headline": "Acquisition"},
            {"news_id": "old", "nse_symbol": "ABC", "date_time": "2026-11-01T09:00:00", "event_type": "results", "priority_tier": "High", "priority_score": 15},
        ]
        self.prices = {"ABC": {"name": "ABC Ltd", "records": [
            {"date": "2026-11-14", "daily_change_pct": 3.5, "volume_ratio": 2.2, "close": 101},
            {"date": "2026-11-15", "daily_change_pct": 1.0, "volume_ratio": 1.1, "close": 102},
        ]}}

    def test_digest_filters_portfolio_and_window(self):
        result = mod.make_digest(self.announcements, self.prices, 24, self.as_of, False, {}, {}, {"ABC"})
        self.assertEqual([r["news_id"] for r in result["items"]], ["a"])
        self.assertEqual(result["summary"]["collapsed_routine"], 1)

    def test_include_routine(self):
        result = mod.make_digest(self.announcements, self.prices, 24, self.as_of, True, {}, {}, {"ABC"})
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(result["items"][0]["priority_tier"], "high")

    def test_material_announcement_alert(self):
        result = mod.make_alerts(self.announcements, self.prices, {}, self.as_of, {"ABC"}, 24)
        self.assertTrue(any(a["type"] == "material_announcement" and a["symbol"] == "ABC" for a in result["alerts"]))

    def test_market_alert_uses_latest_record_only(self):
        prices = {"ABC": {"records": [
            {"date": "2026-11-14", "daily_change_pct": 4.0, "volume_ratio": 3.0},
            {"date": "2026-11-15", "daily_change_pct": 1.0, "volume_ratio": 1.0},
        ]}}
        result = mod.make_alerts([], prices, {}, self.as_of, {"ABC"}, 24)
        self.assertEqual(result["summary"]["market_anomalies"], 0)

    def test_market_alert_thresholds(self):
        result = mod.make_alerts([], {"ABC": {"records": [
            {"date": "2026-11-15", "daily_change_pct": -3.2, "volume_ratio": 2.4}
        ]}}, {"price_change_pct": 3.0, "volume_ratio": 2.0}, self.as_of, {"ABC"}, 24)
        self.assertEqual(result["summary"]["market_anomalies"], 1)
        self.assertEqual(len(result["alerts"][0]["triggers"]), 2)

    def test_announcement_alerts_respect_lookback(self):
        result = mod.make_alerts(self.announcements, {}, {}, self.as_of, {"ABC"}, 1)
        self.assertEqual(result["summary"]["material_announcements"], 0)

    def test_unparseable_dates_are_excluded(self):
        rows = [{"news_id": "x", "nse_symbol": "ABC", "date_time": "not-a-date", "event_type": "results", "priority_tier": "High"}]
        result = mod.make_digest(rows, self.prices, 24, self.as_of, True, {}, {}, {"ABC"})
        self.assertEqual(result["items"], [])

    def test_load_cards_uses_real_management_card_id(self):
        import json, tempfile
        from pathlib import Path
        payload = {"cards": [{"card_id": "abc-123", "nse_symbol": "ABC"}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "management.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            cards = mod.load_cards(path)
        self.assertIn("id:abc-123", cards)

    def test_load_results_cards_indexes_source_pdf_id_prefix(self):
        import json, tempfile
        from pathlib import Path
        payload = {"results": [{"company": "ABC", "source_pdf": "ABC_1234abcd.pdf"}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "results.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            cards = mod.load_cards(path)
        self.assertIn("prefix:1234abcd", cards)

    def test_digest_attaches_result_card_by_source_id_prefix(self):
        card = {"company": "ABC", "source_pdf": "ABC_a.pdf", "metrics": {"pat": 10}}
        cards = {"prefix:a": card}
        result = mod.make_digest(self.announcements, self.prices, 24, self.as_of, False, cards, {}, {"ABC"})
        self.assertEqual(result["items"][0]["digest_enrichment"]["type"], "quarterly_results")
        self.assertEqual(result["items"][0]["digest_enrichment"]["card"], card)

    def test_digest_attaches_management_card_by_card_id(self):
        card = {"card_id": "a", "person_name": "A Person"}
        result = mod.make_digest(self.announcements, self.prices, 24, self.as_of, False, {}, {"id:a": card}, {"ABC"})
        self.assertEqual(result["items"][0]["digest_enrichment"]["type"], "management_change")
        self.assertEqual(result["items"][0]["digest_enrichment"]["card"], card)

    def test_company_level_results_card_attaches_to_best_result_filing_once(self):
        announcements = [
            {"news_id": "press01", "nse_symbol": "ABC", "date_time": "2026-11-15T10:00:00",
             "event_type": "results", "priority_tier": "High", "priority_score": 15,
             "subject": "Press release / media release"},
            {"news_id": "result1", "nse_symbol": "ABC", "date_time": "2026-11-15T09:00:00",
             "event_type": "results", "priority_tier": "Medium", "priority_score": 9,
             "subject": "Unaudited financial results for the quarter ended September 30, 2026"},
        ]
        card = {"company": "ABC", "source_pdf": "ABC_results.pdf"}
        result = mod.make_digest(announcements, self.prices, 24, self.as_of, False,
                                 {"company:ABC": card}, {}, {"ABC"})
        enriched = [x for x in result["items"] if x["digest_enrichment"]]
        self.assertEqual(len(enriched), 1)
        self.assertEqual(enriched[0]["news_id"], "result1")


    def test_default_alert_rules(self):
        rules = mod.validate_alert_rules({})
        self.assertEqual(rules["price_change_pct"], 5.0)
        self.assertEqual(rules["volume_ratio"], 2.0)
        self.assertIn("results", rules["material_categories"])

    def test_custom_price_threshold_changes_alert_list(self):
        prices = {"ABC": {"records": [{"date": "2026-11-15", "daily_change_pct": 4.0, "volume_ratio": 1.0}]}}
        default_result = mod.make_alerts([], prices, {}, self.as_of, {"ABC"}, 24)
        custom_result = mod.make_alerts([], prices, {"price_change_pct": 3.0}, self.as_of, {"ABC"}, 24)
        self.assertEqual(default_result["summary"]["market_anomalies"], 0)
        self.assertEqual(custom_result["summary"]["market_anomalies"], 1)

    def test_custom_volume_threshold_changes_alert_list(self):
        prices = {"ABC": {"records": [{"date": "2026-11-15", "daily_change_pct": 1.0, "volume_ratio": 1.7}]}}
        default_result = mod.make_alerts([], prices, {}, self.as_of, {"ABC"}, 24)
        custom_result = mod.make_alerts([], prices, {"volume_ratio": 1.5}, self.as_of, {"ABC"}, 24)
        self.assertEqual(default_result["summary"]["market_anomalies"], 0)
        self.assertEqual(custom_result["summary"]["market_anomalies"], 1)

    def test_watched_categories_filter_material_announcements(self):
        result = mod.make_alerts(self.announcements, {}, {"material_categories": ["M&A"]}, self.as_of, {"ABC"}, 24)
        self.assertEqual(result["summary"]["material_announcements"], 0)
        result = mod.make_alerts(self.announcements, {}, {"material_categories": ["results"]}, self.as_of, {"ABC"}, 24)
        self.assertEqual(result["summary"]["material_announcements"], 1)

    def test_invalid_alert_rules_are_rejected(self):
        for rules in (
            {"price_change_pct": 0},
            {"volume_ratio": -1},
            {"material_categories": "results"},
            {"material_categories": ["not_a_category"]},
        ):
            with self.subTest(rules=rules):
                with self.assertRaises(ValueError):
                    mod.validate_alert_rules(rules)

    def test_load_alert_rules_from_json(self):
        import json, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rules.json"
            path.write_text(json.dumps({"price_change_pct": 4.5, "volume_ratio": 1.8, "material_categories": ["results"]}), encoding="utf-8")
            rules = mod.load_alert_rules(path)
        self.assertEqual(rules["price_change_pct"], 4.5)
        self.assertEqual(rules["volume_ratio"], 1.8)
        self.assertEqual(rules["material_categories"], ["results"])


if __name__ == "__main__":
    unittest.main()
