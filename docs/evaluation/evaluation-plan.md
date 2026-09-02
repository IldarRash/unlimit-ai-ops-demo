# Incident pipeline evaluation and operating budget

## What is measured now

Run the deterministic regression set with:

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_incident_pipeline.py `
  --output docs\evaluation\offline-eval-results.json
```

The current set contains six reviewed cases: healthy baseline, known provider error,
unknown technical error with provider-status corroboration, business declines with
merchant-report corroboration, latency plus timeout, and low-volume health failure.

Current offline result: **6/6 cases passed**. Detection signal set, severity,
rules-versus-OpenAI route, and business-versus-technical cause category all matched.
The known error used the catalog and did not increment the offline analyzer call count.

This result validates deterministic routing and schema contracts. It is **not** a claim
about model accuracy: OpenAI is replaced by a local fixture analyzer and no network
request is made. Model-quality evidence begins only after the approved live smoke and
a human-reviewed labelled incident set.

## Production KPI scorecard

| Area | Metric | Initial target | Measurement |
| --- | --- | ---: | --- |
| Detection | Scenario recall | >= 95% | Labelled synthetic and replayed incidents |
| Detection | Healthy-window false positive rate | < 1% | Rolling baseline windows |
| Routing | Known-error catalog bypass | 100% | Catalog match / OpenAI calls |
| Grounding | Valid evidence references | 100% | Output-schema and reference validator |
| Usefulness | Operator helpful rating | >= 80% | Incident feedback endpoint |
| Speed | Alert-to-incident p95 | < 60 s | Alert start to stored incident |
| LLM latency | Unknown-analysis p95 | < 8 s | Responses API request timer |
| Reliability | Safe fallback on LLM failure | 100% | Fault injection and circuit-breaker tests |
| Efficiency | Mean investigation preparation time | -50% | Before/after operator study |
| Outcome | MTTA / MTTR | -30% / -20% | Incident system timestamps |
| Privacy | Raw customer identifiers sent to LLM | 0 | Contract tests and payload audit |
| Cost | Unknown analysis cost | <= $0.01 | Recorded input/output token usage |

## Cost and latency decision

The configured model is `gpt-5.4-mini`. Official OpenAI pricing on 2 September 2026
lists **$0.75 per 1M input tokens** and **$4.50 per 1M output tokens**:
<https://developers.openai.com/api/docs/models/gpt-5.4-mini>.

The cost formula is:

```text
cost = input_tokens / 1,000,000 * 0.75
     + output_tokens / 1,000,000 * 4.50
```

At 2,000 input and 600 output tokens, the estimated request cost is **$0.0042**.
The current `max_output_tokens=1200` safety cap keeps a 3,000-input-token request at
approximately **$0.00765**, below the $0.01 operating ceiling. Actual reasoning and
output usage must be recorded from the approved smoke before calling this budget
validated.

The model is used only for unknown incidents. Healthy windows produce no incident and
known provider responses use the catalog, so most traffic creates no model cost.
If p95 exceeds 8 seconds or the failure threshold opens the circuit, the pipeline
returns an explicit manual-review result and performs no automated mitigation.

## Improvement loop

1. Add anonymized, operator-labelled incidents to the golden set.
2. Evaluate routing, category, evidence grounding, action safety, and usefulness.
3. Review every incorrect or unhelpful result; never auto-promote model output.
4. Update prompt or catalog rules through versioned changes and rerun the full set.
5. Promote a response code to the deterministic catalog only after operations review.
6. Track cost, latency, feedback, and fallback rate per prompt/model version.
