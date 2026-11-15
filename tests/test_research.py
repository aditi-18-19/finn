import json
from pathlib import Path

import pytest

from pipeline.research import normalise_symbol, _document_gaps, _source_fingerprint


def test_symbol_normalisation():
    assert normalise_symbol("axisbank") == "AXISBANK"
    assert normalise_symbol("M&M") == "M_M"


@pytest.mark.parametrize("symbol", ["", "   ", "ABC/DEF", "A B", "A" * 21])
def test_rejects_invalid_symbols(symbol):
    with pytest.raises(ValueError):
        normalise_symbol(symbol)


def test_reports_exact_missing_document_types(tmp_path, monkeypatch):
    import pipeline.research as mod
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    (tmp_path / "data/docs/TEST").mkdir(parents=True)
    (tmp_path / "data/docs/TEST/annual.pdf").write_bytes(b"%PDF")
    manifest = {
        "available": {"annual_reports": 1, "concall_transcripts": 0},
        "documents": [{"type": "annual_report", "download_status": "downloaded", "file": "data/docs/TEST/annual.pdf"}],
    }
    gaps = _document_gaps(manifest, 2, 4)
    assert any("Annual reports: requested latest 2, found 1" in item for item in gaps)
    assert any("Earnings-call transcripts: requested latest 4, found 0" in item for item in gaps)


def test_fingerprint_uses_actual_pdf_bytes(tmp_path, monkeypatch):
    import pipeline.research as mod
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "DOCS_ROOT", tmp_path / "data/docs")
    folder = tmp_path / "data/docs/TEST"
    folder.mkdir(parents=True)
    pdf = folder / "a.pdf"
    pdf.write_bytes(b"first")
    (folder / "manifest.json").write_text(json.dumps({"documents": [{
        "type": "annual_report", "title": "AR", "download_status": "downloaded",
        "file": "data/docs/TEST/a.pdf"
    }]}))
    first = _source_fingerprint("TEST")
    pdf.write_bytes(b"second")
    second = _source_fingerprint("TEST")
    assert first != second
