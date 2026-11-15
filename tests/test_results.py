import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
import extract_results as er


class ResultsExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_pdf_dir = er.PDF_DIR
        er.PDF_DIR = ROOT / "data" / "raw" / "results_pdfs"

    @classmethod
    def tearDownClass(cls):
        er.PDF_DIR = cls.original_pdf_dir

    def test_all_five_sample_pdfs_exist_and_extract(self):
        for symbol, cfg in er.SAMPLES.items():
            with self.subTest(symbol=symbol):
                self.assertTrue((er.PDF_DIR / cfg["pdf"]).exists())
                result = er.process_one(symbol)
                self.assertEqual(result["company"], symbol)
                self.assertEqual(result["validation"]["status"], "validated", result["validation"]["issues"])

    def test_hdfc_ocr_zero_normalisation_and_bank_profile(self):
        payload = er.extract_hdfc(er.read_pages(er.PDF_DIR / er.SAMPLES["HDFCBANK"]["pdf"], [9])[0]["sorted_text"])
        self.assertEqual(payload["metrics"]["revenue"]["values"]["Q1_FY27"], 133110.36)
        self.assertEqual(payload["metrics"]["pat"]["values"]["Q2_FY27"], 20700.03)
        self.assertIsNone(payload["metrics"]["depreciation"]["values"]["Q2_FY27"])

    def test_icici_consolidated_bank_mapping(self):
        payload = er.extract_icici(er.read_pages(er.PDF_DIR / er.SAMPLES["ICICIBANK"]["pdf"], [13])[0]["sorted_text"])
        self.assertEqual(payload["metrics"]["revenue"]["values"]["Q2_FY27"], 88161.18)
        self.assertEqual(payload["metrics"]["pbt"]["values"]["Q2_FY27"], 23191.75)
        self.assertEqual(payload["metrics"]["pat"]["values"]["Q2_FY27"], 16333.18)

    def test_industrial_ebitda_formula(self):
        result = er.process_one("RELIANCE")
        self.assertEqual(result["ebitda"]["Q2_FY27"], 56616.0)
        self.assertEqual(result["ebitda"]["Q1_FY27"], 47517.0)

    def test_tata_steel_ebitda_formula(self):
        result = er.process_one("TATASTEEL")
        self.assertEqual(result["ebitda"]["Q2_FY27"], 10994.02)

    def test_banks_have_no_ebitda(self):
        for symbol in ("HDFCBANK", "ICICIBANK"):
            with self.subTest(symbol=symbol):
                result = er.process_one(symbol)
                self.assertIsNone(result["ebitda"])

    def test_qoq_and_yoy_calculations(self):
        self.assertEqual(er.pct_change(110, 100), 10.0)
        self.assertEqual(er.pct_change(90, 100), -10.0)
        self.assertIsNone(er.pct_change(100, 0))
        self.assertIsNone(er.pct_change(None, 100))

    def test_source_number_check_handles_ocr_zero(self):
        self.assertTrue(er.number_present("13311O.36", 133110.36))
        self.assertFalse(er.number_present("133110.36", 133110.35))

    def test_pat_does_not_exceed_revenue_for_all_samples(self):
        for symbol in er.SAMPLES:
            result = er.process_one(symbol)
            for period in er.PERIODS:
                rev = result["metrics"]["revenue"]["values"][period]
                pat = result["metrics"]["pat"]["values"][period]
                self.assertLessEqual(pat, rev, (symbol, period))

    def test_deterministic_metadata_does_not_claim_llm(self):
        for symbol in er.SAMPLES:
            result = er.process_one(symbol)
            self.assertEqual(result["extraction_method"], "pymupdf_deterministic")
            self.assertIsNone(result["model"])

    def test_expected_values_match_pdf_tables(self):
        expected = {
            "HDFCBANK": {"revenue": [129358.69, 133110.36, 118560.63], "pbt": [28662.54, 27193.16, 26658.89], "pat": [20700.03, 19244.71, 19610.67]},
            "ICICIBANK": {"revenue": [88161.18, 79689.22, 76146.59], "pbt": [23191.75, 21373.61, 19126.33], "pat": [16333.18, 15440.06, 13357.06]},
            "INFY": {"revenue": [49244, 48211, 44490], "pbt": [11063, 11028, 10229], "pat": [7838, 7775, 7375]},
            "RELIANCE": {"revenue": [332861, 311850, 258898], "pbt": [38613, 30630, 29124], "pat": [29250, 23001, 22146]},
            "TATASTEEL": {"revenue": [65436.24, 60794.29, 58689.29], "pbt": [5455.15, 3837.53, 4222.47], "pat": [3699.59, 2385.24, 3183.09]},
        }
        for symbol, metrics in expected.items():
            result = er.process_one(symbol)
            for metric_name, expected_values in metrics.items():
                actual = [result["metrics"][metric_name]["values"][p] for p in er.PERIODS]
                self.assertEqual(actual, expected_values, (symbol, metric_name))

    def test_all_cards_have_qoq_and_yoy_for_disclosed_metrics(self):
        for symbol in er.SAMPLES:
            result = er.process_one(symbol)
            for metric_name in ("revenue", "finance_cost", "other_income", "pbt", "pat"):
                item = result["metrics"][metric_name]
                self.assertIsInstance(item["qoq_pct"], float, (symbol, metric_name, "qoq"))
                self.assertIsInstance(item["yoy_pct"], float, (symbol, metric_name, "yoy"))

    def test_missing_source_row_is_rejected(self):
        with self.assertRaises(ValueError):
            er.extract_hdfc("UNAUDITED CONSOLIDATED FINANCIAL RESULTS\nTotal Income 100 90 80")

    def test_null_bank_depreciation_does_not_fail_validation(self):
        result = er.process_one("HDFCBANK")
        self.assertIsNone(result["metrics"]["depreciation"]["values"]["Q2_FY27"])
        self.assertEqual(result["validation"]["issues"], [])

    def test_output_schema_contains_source_traceability(self):
        for symbol in er.SAMPLES:
            result = er.process_one(symbol)
            self.assertTrue(result["source_pdf"])
            self.assertTrue(result["source_pages"])
            self.assertTrue(result["source_sha256"])
            self.assertEqual(result["statement_type"], "consolidated")
            for metric_name in ("revenue", "finance_cost", "other_income", "pbt", "pat"):
                self.assertTrue(result["metrics"][metric_name]["row_label"])
                self.assertTrue(result["metrics"][metric_name]["evidence"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
