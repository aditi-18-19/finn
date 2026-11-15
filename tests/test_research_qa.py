import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("research_qa", ROOT / "pipeline" / "research_qa.py")
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)


def chunks():
    return [{
        "chunk_id": "AR_FY2026_p146-149_c0105",
        "document": "AR_FY2026.pdf",
        "document_type": "annual_report",
        "page_start": 146,
        "page_end": 149,
        "speaker": None,
        "text": "The Bank invested ₹4,000+ crores of OPEX in technology during FY 2025-26.",
    }]


def test_verbatim_quote_is_accepted():
    assert qa.quote_is_verbatim(
        "₹4,000+ crores of OPEX in technology",
        chunks()[0]["text"],
    )


def test_non_verbatim_quote_is_rejected():
    assert not qa.quote_is_verbatim(
        "₹5,000 crores of OPEX in technology",
        chunks()[0]["text"],
    )


def test_valid_claim_is_bound_to_actual_chunk_metadata():
    payload = {
        "answer": "The bank invested ₹4,000+ crores of OPEX in technology.",
        "claims": [{
            "claim": "The bank invested ₹4,000+ crores of OPEX in technology.",
            "chunk_id": "AR_FY2026_p146-149_c0105",
            "quote": "₹4,000+ crores of OPEX in technology",
        }],
    }
    result = qa.validate_claims(payload, chunks())
    assert result["not_found"] is False
    assert result["claims"][0]["document"] == "AR_FY2026.pdf"
    assert result["claims"][0]["page_start"] == 146


def test_fabricated_chunk_id_is_rejected():
    payload = {
        "answer": "Unsupported.",
        "claims": [{
            "claim": "Unsupported.",
            "chunk_id": "invented_chunk",
            "quote": "some quote",
        }],
    }
    result = qa.validate_claims(payload, chunks())
    assert result["answer"] == qa.NOT_FOUND
    assert result["not_found"] is True
    assert result["rejected_claims"][0]["reason"] == "chunk_not_in_retrieved_context"


def test_invented_quote_is_rejected():
    payload = {
        "answer": "The bank invested ₹5,000 crore.",
        "claims": [{
            "claim": "The bank invested ₹5,000 crore.",
            "chunk_id": "AR_FY2026_p146-149_c0105",
            "quote": "₹5,000 crore",
        }],
    }
    result = qa.validate_claims(payload, chunks())
    assert result["answer"] == qa.NOT_FOUND
    assert result["not_found"] is True


def test_empty_claims_return_exact_not_found_string():
    result = qa.validate_claims({"answer": "Maybe it plans to invest.", "claims": []}, chunks())
    assert result["answer"] == "Not found in the documents"
    assert result["not_found"] is True


def test_claim_cannot_cite_unretrieved_chunk_even_if_quote_exists():
    payload = {
        "answer": "Claim.",
        "claims": [{
            "claim": "Claim.",
            "chunk_id": "other_chunk",
            "quote": "The Bank invested ₹4,000+ crores of OPEX in technology",
        }],
    }
    result = qa.validate_claims(payload, chunks())
    assert result["not_found"] is True
