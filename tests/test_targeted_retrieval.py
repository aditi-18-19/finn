import importlib.util
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("research_retrieval", ROOT / "pipeline" / "research_retrieval.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_capex_query_does_not_expand_generic_guidance_terms():
    expanded = module.expand_query("What are the bank's capital expenditure plans and capex guidance?")
    assert "fixed asset additions" in expanded.lower()
    assert "through cycle" not in expanded.lower()


def test_capex_phrase_boosts_relevant_chunk():
    chunks = [
        {"text": "The bank purchased property plant and equipment during the year."},
        {"text": "The bank completed a custody services contract."},
    ]
    scores = module.lexical_scores("capital expenditure and capex plans", chunks)
    assert scores[0] > scores[1]


def test_technology_query_boosts_it_infrastructure_not_esg_lending():
    chunks = [
        {"text": "The bank invested in core banking systems and IT infrastructure."},
        {"text": "The bank increased lending to renewable energy and electric vehicles."},
    ]
    scores = module.lexical_scores("technology investment plans", chunks)
    assert scores[0] > scores[1]


def test_hybrid_rank_reports_intent_and_component_scores():
    chunks = [{"chunk_id": "a", "text": "capital expenditure fixed asset additions"},
              {"chunk_id": "b", "text": "custody services"}]
    vectors = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    ranked = module.hybrid_rank("capex plans", chunks, vectors, np.array([1.0, 0.0]), 2)
    assert ranked[0]["chunk_id"] == "a"
    assert ranked[0]["retrieval"]["intent"] == "capex"
    assert "lexical_score" in ranked[0]["retrieval"]


def test_document_date_parser_kept():
    assert module.extract_document_date("Concall_Jul_2026.pdf") == "Jul 2026"
    assert module.extract_document_date("Annual_Report_FY2026.pdf") == "FY2026"
