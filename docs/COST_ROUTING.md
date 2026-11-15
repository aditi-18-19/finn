# FINN cost-routing policy

## Routing policy
- Deterministic rules first; no model call for rule-resolvable classifications.
- Gemini Flash for announcement classification, one-line summaries and results extraction.
- Gemini Pro only for genuinely multi-document reasoning: promised-vs-delivered and bull/bear analysis.
- Cache each model output by SHA-256 over task, model, prompt and canonical input JSON.
- Batch classification inputs into bounded batches (recommended 10–20 filings per request), parse one structured JSON response, and retry only failed items.
- Record provider-reported input/output token counts and estimated INR cost in `data/cost_log.jsonl`.

## Additive wrapper
`pipeline/llm_cost_control.py` provides `model_for`, `cached_generate`, `cached_generate_batch` (bounded batches, default 15), and `cost_report`.
It is not automatically wired into the existing classification/results scripts. Replace their direct Gemini calls with `cached_generate(...)` after checking each call site's expected prompt and response schema. Do not mass-replace without testing.

Example:
```python
from pipeline.llm_cost_control import cached_generate
result = cached_generate(
    "classification",
    "Classify each filing into the supplied taxonomy. Return JSON.",
    [{"id": "123", "subject": "Board approves ..."}]
)
```

## Cost reporting
Run:
```powershell
python -c "from pipeline.llm_cost_control import cost_report; import json; print(json.dumps(cost_report(), indent=2))"
```
The estimate uses provider-reported token counts and editable prices in `config/model_routing.json`. It is not a billing statement. Existing Gemini calls made before this logger was installed cannot be reconstructed from local code; report them as historical spend unknown unless provider billing data is available. Never claim ₹0 unless billing/usage data confirms it.
