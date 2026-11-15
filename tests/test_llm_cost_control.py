import json
from pathlib import Path
from pipeline.llm_cost_control import cache_key, model_for, cost_report

def test_model_routing():
    assert model_for("classification") == "gemini-2.5-flash"
    assert model_for("promised_vs_delivered") == "gemini-2.5-pro"

def test_hash_is_stable_for_object_key_order():
    assert cache_key("classification", "m", "p", {"a": 1, "b": 2}) == cache_key(
        "classification", "m", "p", {"b": 2, "a": 1}
    )

def test_different_inputs_have_different_hashes():
    assert cache_key("x", "m", "p", {"v": 1}) != cache_key("x", "m", "p", {"v": 2})

def test_cost_report_empty_shape():
    result = cost_report()
    assert "estimated_spend_inr" in result
    assert "paid_calls" in result
