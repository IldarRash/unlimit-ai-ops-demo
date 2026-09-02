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
request is made. The approved live smoke below validates the integration and grounding
boundary; accuracy still requires a human-reviewed labelled incident set.

## Approved live OpenAI smoke

On 2 September 2026, a local synthetic OrbitWallet degradation completed the full
Prometheus → Alertmanager → evidence collection → OpenAI → incident → recovery path.
The `gpt-5.4-mini` response used prompt `incident-v4`, passed the strict evidence-reference
validator, and produced two grounded technical causes plus four advisory actions. Request
ID presence was verified but the value was not persisted in the evidence artifact.

| Measurement | Observed |
| --- | ---: |
| Scenario start to stored analysis | ~57.4 s |
| Evidence collection to OpenAI response | ~7.0 s |
| Input tokens | 3,464 |
| Output tokens | 807 |
| Estimated request cost | $0.0062295 |
| Automatic incident recovery | Verified |

This single latency observation is inside the 8-second per-request target, but it is not
enough to claim a p95. The sanitized machine-readable record is
`docs/evaluation/live-openai-smoke.json`.

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
approximately **$0.00765**, below the $0.01 operating ceiling. The approved live request
used 3,464 input and 807 output tokens, costing approximately **$0.0062295**, so the
per-unknown-incident budget is validated for this observed case.

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
