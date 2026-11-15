import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "pipeline" / "extract_research_chunks.py"
spec = importlib.util.spec_from_file_location("extract_research_chunks", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_chunk_preserves_page_provenance():
    pages = [
        {"page": 2, "text": " ".join(["alpha"] * 120)},
        {"page": 3, "text": " ".join(["beta"] * 120)},
    ]
    chunks = module.chunk_pages(pages, "sample.pdf", "annual_report", 150, 20)
    assert chunks
    assert all(c["document"] == "sample.pdf" for c in chunks)
    assert all(c["page_start"] <= c["page_end"] for c in chunks)
    assert all(c["pages"] for c in chunks)


def test_chunk_word_limit_and_overlap():
    pages = [{"page": 1, "text": " ".join(f"w{i}" for i in range(1000))}]
    chunks = module.chunk_pages(pages, "sample.pdf", "annual_report", 200, 30)
    assert len(chunks) > 1
    assert all(c["word_count"] <= 200 for c in chunks)
    assert chunks[0]["word_count"] == 200


def test_transcript_speaker_boundary_detected():
    text = "OPERATOR\nGood morning everyone.\n\nJOHN DOE - CHIEF EXECUTIVE OFFICER\nThank you. We delivered strong growth."
    blocks = module.split_speaker_blocks(text)
    assert blocks
    assert any("Good morning" in body for _, body in blocks)
    assert any("delivered strong growth" in body for _, body in blocks)


def test_clean_text_removes_noise():
    assert module.clean_text("hello\x00  world\n\n\nnext") == "hello world\n\nnext"
