import importlib.util
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "research_retrieval_capex",
    ROOT / "pipeline" / "research_retrieval.py",
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_capex_expansion_contains_annual_report_accounting_language():
    expanded = module.expand_query("What are the bank's capital expenditure plans and capex guidance?")
    assert "capital expenditure for the year" in expanded.lower()
    assert "additions to fixed assets" in expanded.lower()
    assert "capital work in progress" in expanded.lower()


def test_capex_lexical_score_finds_annual_report_table_row():
    chunks = [
        {
            "text": "Capital Expenditure for the year 1,956.62 2,462.82 "
                    "Depreciation on fixed assets for the year 1,807.27 1,697.78"
        },
        {"text": "The bank completed a custody services contract with a mutual fund."},
        {"text": "Total capital qualifying for computation of capital adequacy ratio 197,057."},
    ]
    scores = module.lexical_scores(
        "What are the bank's capital expenditure plans and capex guidance?", chunks
    )
    assert scores[0] > scores[1]
    assert scores[0] > scores[2]


def test_generic_capital_adequacy_is_not_given_capex_table_bonus():
    chunks = [
        {"text": "Total capital qualifying for computation of capital adequacy ratio 197,057."},
        {"text": "Capital Expenditure for the year 1,956.62 2,462.82."},
    ]
    scores = module.lexical_scores("capex", chunks)
    assert scores[1] > scores[0]


def test_capex_expanded_terms_are_used_by_bm25():
    chunks = [
        {"text": "Additions to fixed assets during the year included premises and equipment."},
        {"text": "The bank expanded its custody and mutual fund business."},
    ]
    scores = module.lexical_scores("capex plans", chunks)
    assert scores[0] > scores[1]


def test_existing_intents_still_return_scores():
    chunks = [
        {"text": "The bank will add about 400 new branches this year."},
        {"text": "Management reiterated structural net interest margin guidance of 3.8%."},
    ]
    assert module.lexical_scores("branch expansion", chunks)[0] > 0
    assert module.lexical_scores("structural net interest margin target", chunks)[1] > 0


def test_hybrid_rank_keeps_component_scores():
    chunks = [
        {"chunk_id": "capex", "text": "Capital Expenditure for the year 1,956.62 2,462.82"},
        {"chunk_id": "other", "text": "Custody services contract"},
    ]
    vectors = np.array([[0.8, 0.6], [0.7, 0.3]], dtype=np.float32)
    ranked = module.hybrid_rank(
        "capex plans", chunks, vectors, np.array([1.0, 0.0]), top_k=2
    )
    assert len(ranked) == 2
    assert "hybrid_score" in ranked[0]["retrieval"]
