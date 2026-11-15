import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "pipeline" / "answer_research.py"
spec = importlib.util.spec_from_file_location("answer_research", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def passage():
    return {
        "chunk_id": "doc_p1-1_c0001",
        "document": "Concall_Q1.pdf",
        "page_start": 1,
        "page_end": 1,
        "speaker": "CEO",
        "text": "We plan to open 100 new branches during FY2027. We will focus on deposits.",
    }


def test_quote_verification_whitespace_normalised():
    assert module.quote_exists("open 100 new branches", "We plan to open\n100 new branches during FY2027.")


def test_quote_verification_rejects_fabricated_quote():
    assert not module.quote_exists("We will open 500 branches", passage()["text"])


def test_verified_claim_uses_source_metadata_not_model_metadata():
    raw = {
        "status": "answered",
        "answer": "Management plans branch expansion.",
        "claims": [{
            "claim": "Management plans 100 new branches in FY2027.",
            "quote": "We plan to open 100 new branches during FY2027.",
            "document": "FAKE.pdf",
            "page_start": 999,
            "page_end": 999,
            "speaker": "Fake",
            "chunk_id": "doc_p1-1_c0001",
        }],
    }
    result = module.verify_response(raw, [passage()])
    assert result["status"] == "answered"
    claim = result["claims"][0]
    assert claim["document"] == "Concall_Q1.pdf"
    assert claim["page_start"] == 1
    assert claim["speaker"] == "CEO"


def test_unverifiable_claims_return_not_found():
    raw = {
        "status": "answered",
        "answer": "Made up.",
        "claims": [{
            "claim": "Made up claim",
            "quote": "There is no such quote.",
            "chunk_id": "doc_p1-1_c0001",
        }],
    }
    result = module.verify_response(raw, [passage()])
    assert result["status"] == "not_found"
    assert result["answer"] == module.NOT_FOUND
    assert not result["claims"]
