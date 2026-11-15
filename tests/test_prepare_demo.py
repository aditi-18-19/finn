import json
from pathlib import Path
import pipeline.prepare_demo as demo


def test_results_cards_resolves_canonical_nested_path(tmp_path, monkeypatch):
    data = tmp_path / "data"
    nested = data / "results" / "results_cards.json"
    nested.parent.mkdir(parents=True)
    nested.write_text(json.dumps({"results": []}), encoding="utf-8")
    monkeypatch.setattr(demo, "DATA", data)
    monkeypatch.setattr(
        demo, "ARTIFACT_PATHS",
        {"results_cards.json": [data / "results" / "results_cards.json",
                                data / "results_cards.json"]},
    )
    assert demo.resolve_artifact("results_cards.json") == nested


def test_results_cards_supports_legacy_root_path(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    legacy = data / "results_cards.json"
    legacy.write_text(json.dumps({"results": []}), encoding="utf-8")
    monkeypatch.setattr(demo, "DATA", data)
    monkeypatch.setattr(
        demo, "ARTIFACT_PATHS",
        {"results_cards.json": [data / "results" / "results_cards.json",
                                data / "results_cards.json"]},
    )
    assert demo.resolve_artifact("results_cards.json") == legacy


def test_missing_results_cards_reports_canonical_path(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(demo, "DATA", data)
    monkeypatch.setattr(
        demo, "ARTIFACT_PATHS",
        {"results_cards.json": [data / "results" / "results_cards.json",
                                data / "results_cards.json"]},
    )
    assert demo.resolve_artifact("results_cards.json") == data / "results" / "results_cards.json"
