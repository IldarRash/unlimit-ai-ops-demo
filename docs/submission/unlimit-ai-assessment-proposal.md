# Sentinel: AI-assisted payment incident intelligence

**AI Assessment Proposal — Technical Operations**  
**Candidate deliverable:** written proposal plus working code demonstration

## Executive proposal

Payment operations teams lose time not because monitoring data is absent, but because evidence is fragmented. An operator must compare transaction metrics, provider responses, status pages, support escalations, and merchant reports before deciding whether an issue is technical, commercial, isolated, or systemic. Sentinel shortens that investigation while keeping operational decisions under human control.

The working prototype generates realistic healthy payment traffic for three APM providers and introduces controlled degradations. Prometheus and Grafana expose the divergence; Alertmanager triggers an incident workflow; the Incident API collects bounded metrics, normalized provider responses, and sanitized external signals. Reviewed provider errors are answered immediately from a versioned catalog. Unknown incidents are eligible for OpenAI analysis using strict structured output. The result is stored as an advisory incident with evidence references, proposed causes, reversible checks, and a complete audit trail.

Success means faster and more consistent triage, not autonomous remediation. Sentinel never retries payments, changes routing, contacts a provider, or resolves an incident without an operator-owned process.

## Problem framing and operating boundary

The practical problem is the first 10–20 minutes of an incident: validate that the signal is real, identify the affected provider and failure class, gather relevant evidence, state likely impact, and route the issue to the correct owner. Today this often requires repetitive dashboard and message review.

Rules remain authoritative for thresholds, known response codes, deduplication, severity floors, access control, and incident lifecycle. AI is useful only where evidence must be summarized or several plausible explanations must be ranked. The model receives normalized provider-level data rather than raw transactions or customer messages. Every proposed cause must reference a supplied metric, alert, provider event, or external signal. Invalid, unavailable, or ungrounded model output fails closed to manual review.

Primary risks are false confidence, prompt injection through external text, missing or stale data, accidental disclosure of customer information, model latency/cost, and operators treating advice as an action. Controls include strict schemas, allowlisted fields, an explicit `contains_customer_data=false` contract, bounded evidence windows, evidence-reference validation, `store=false`, timeouts, a circuit breaker, a network enable gate, advisory-only actions, operator feedback, and audit records.

## Three AI-assisted use cases

| Use case | Inputs | AI action | Output and user | Operational value |
| --- | --- | --- | --- | --- |
| Incident triage and evidence synthesis | Prometheus snapshot, Alertmanager labels, normalized provider responses, sanitized provider status/support/merchant signals | Separate business and technical hypotheses; summarize impact; rank reversible checks | Structured incident for the on-call operator | Less dashboard switching; consistent first assessment; faster acknowledgement |
| Escalation clustering and routing | Sanitized support-ticket, Slack/email escalation, merchant complaint, provider status and operations-report signals | Correlate repeated symptoms by provider, region, time and outcome class; propose the responsible queue | Evidence-backed routing recommendation for Tech Ops, provider management, or commercial operations | Reduces duplicate investigation and wrong-team handoffs |
| Reviewed knowledge capture | Resolved incident, operator feedback, final cause and runbook outcome | Draft a catalog-rule or runbook update and highlight gaps in existing guidance | Human-reviewed change proposal for the operations owner | Converts repeated unknown incidents into faster deterministic handling |

The prototype implements the first use case end to end, the normalized input boundary for the second, and the feedback/audit foundation for the third. Automatic publication of catalog or runbook changes is intentionally out of scope.

## Detailed agent/workflow design

**Trigger.** A sustained Prometheus alert is delivered by Alertmanager, or an operator selects a provider and runs analysis. External operational signals can arrive before or during the metric anomaly and become bounded context for the same provider.

**Inputs.** The workflow collects a five-minute provider snapshot (volume, success, technical-error, timeout, decline, p95 latency and health), detected threshold breaches, alert metadata, up to 20 normalized provider responses, and up to 12 sanitized external signals. Raw transaction bodies, cardholder data, merchant free text, credentials, and customer identifiers are excluded.

**Decision logic.** First, deterministic rules validate volume and severity. If no threshold is crossed, no incident is created. Next, provider responses are matched against active versioned catalog rules, with ambiguity treated as an error. A known match produces the reviewed response without an LLM call. Otherwise, and only when the runtime gate is enabled, OpenAI receives the evidence bundle and must return the strict incident schema. Output references are checked against the bundle. Failures return an explicit “analysis unavailable” result.

**Actions.** Sentinel creates or correlates an incident, records the evidence and classification source, suggests likely causes and investigation checks, publishes the update to the operator UI, and records feedback and lifecycle changes. Suggested remediation is always marked unsafe to automate.

**Approvals and handoffs.** Operators acknowledge and resolve incidents. Routing changes, payment retries, provider communication, catalog promotion, and runbook publication require the appropriate human owner. A critical technical failure routes to Tech Ops/provider engineering; business declines route to payment operations or merchant/commercial owners; low-confidence or unavailable analysis stays with manual triage.

**Auditability.** Every incident stores prompt/model metadata where applicable, request ID and token usage when returned, evidence, classification source, occurrence count, status transitions and feedback. Known responses include catalog rule/version. External signal IDs and source references remain visible, while ingestion is authenticated and idempotent.

## End-to-end automation and human control

```text
healthy + fault traffic -> provider metrics -> Prometheus/Grafana -> Alertmanager
                                      |                    |
normalized provider responses --------+                    v
sanitized external signals ----------------------> evidence bundle
                                                      |
                                          catalog match? -- yes --> reviewed runbook
                                                      |
                                                      no
                                                      v
                                             OpenAI structured analysis
                                                      |
                                                      v
                                    Postgres incident + audit -> operator UI
```

The automation stops after preparation and recommendation. This is deliberate: an incorrect summary is recoverable, while an incorrect routing or payment action can create financial and customer harm. The operator can inspect exactly where each cause came from before acting.

## Measurement, cost and rollout

The offline golden set currently passes 6/6 cases for detection, severity, routing, and business/technical categorization. This validates rules and contracts with a local analyzer; it does not claim real-model accuracy. The next quality gate is a labelled incident set reviewed by operations.

Initial targets are: at least 95% scenario recall, below 1% false positives on healthy windows, 100% catalog bypass for known errors, 100% valid evidence references, at least 80% helpful operator feedback, alert-to-incident p95 below 60 seconds, unknown-analysis p95 below eight seconds, zero raw customer identifiers sent to the model, and mean unknown-analysis cost below $0.01.

At the official GPT-5.4 mini rates available on 2 September 2026 ($0.75 per 1M input tokens and $4.50 per 1M output tokens), a 2,000-input/600-output analysis is approximately $0.0042. Known errors and healthy windows have no model cost. Actual latency, token use and groundedness must be captured during an approved live smoke before rollout.

Recommended rollout: shadow mode on synthetic/replayed incidents; limited advisory pilot with one provider; weekly review of false positives, feedback, latency and cost; then gradual provider coverage. The fallback is always deterministic monitoring plus manual investigation. No automated remediation is required for the value proposition.

## Practical demonstration

The repository runs seven local services with Docker Compose: provider emulator, traffic generator, Prometheus, Grafana, Alertmanager, Incident API/UI, and PostgreSQL. The operator can observe a stable weighted baseline, introduce a known technical error, an unknown technical error, business declines, latency, timeout, or health failure, inspect the resulting evidence and causes, and recover the provider while unaffected traffic continues.

The demo directly shows the operational decision boundary: dashboards detect and quantify the problem; rules handle known facts; AI proposes a grounded explanation for unknowns; the operator owns the decision.
