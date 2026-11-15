import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("research_retrieval", ROOT / "pipeline" / "research_retrieval.py")
retrieval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retrieval)


def test_query_expansion_for_capex():
    expanded = retrieval.expand_query("What is the capex guidance?")
    assert "capital expenditure" in expanded.lower()


def test_lexical_retrieval_rewards_relevant_financial_terms():
    chunks = [
        {"text": "Management expects net interest margin to remain at 3.8 percent."},
        {"text": "The bank opened new branches across rural India."},
    ]
    scores = retrieval.lexical_scores("net interest margin guidance", chunks)
    assert scores[0] > scores[1]


def test_hybrid_rank_returns_retrieval_scores():
    import numpy as np
    chunks = [{"chunk_id": "a", "text": "capex capital expenditure investment"},
              {"chunk_id": "b", "text": "net interest margin"}]
    vectors = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    result = retrieval.hybrid_rank(
        "capex", chunks, vectors, np.array([1.0, 0.0]), top_k=2
    )
    assert result[0]["chunk_id"] == "a"
    assert "semantic_score" in result[0]["retrieval"]
    assert "lexical_score" in result[0]["retrieval"]
    assert "hybrid_score" in result[0]["retrieval"]


def test_extract_document_period():
    assert retrieval.extract_document_date("Concall_Jul_2026.pdf") == "Jul 2026"
    assert retrieval.extract_document_date("Annual_Report_FY2026.pdf") == "FY2026"


def test_chunk_index_mismatch_raises():
    import numpy as np
    try:
        retrieval.hybrid_rank("query", [{"text": "one"}], np.zeros((2, 3)), np.zeros(3))
    except ValueError as exc:
        assert "Chunk count" in str(exc)
    else:
        raise AssertionError("Expected mismatch validation error")
