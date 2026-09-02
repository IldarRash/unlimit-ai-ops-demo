# Sentinel — AI-assisted payment incident intelligence

Sentinel is a working Technical Operations demo for investigating degradation across synthetic Alternative Payment Method (APM) providers. It combines realistic baseline payment traffic, controllable provider failures, Prometheus/Grafana observability, deterministic known-error handling, OpenAI-assisted investigation of unknown incidents, and an operator-controlled incident console.

## What is implemented

- Three weighted providers — AtlasPay, NovaBank, and OrbitWallet — with different payment methods, latency profiles, decline rates, technical-error rates, and health behavior.
- Continuous healthy baseline traffic across merchants, regions, and payment methods. The rate can be changed from 1–100 RPS or stopped from the operator console.
- Explicit scenarios for latency, timeout, health-check failure, known technical errors, unknown technical errors, business declines, and recovery to the provider baseline.
- Prometheus metrics with bounded labels and two provisioned Grafana dashboards:
  - provider traffic, success rate, actual versus target RPS, client versus provider p95, business declines, technical failures, health, and active scenario;
  - Alertmanager delivery, incident classification, provider-event delivery, pipeline latency, feedback, and LLM circuit state.
- Alertmanager integration for p95 latency, technical-error rate, business-decline rate, and provider health.
- A catalog-first incident classifier:
  - a known AtlasPay `UPSTREAM_ERROR` is answered deterministically from a versioned catalog without an LLM call;
  - unknown technical errors and business anomalies are sent to OpenAI with normalized metrics and bounded provider events.
- OpenAI Responses API integration with strict JSON Schema, `store: false`, bounded retries, a circuit breaker, and validation that every cause refers only to supplied evidence.
- Structured causes that separate `business` from `technical` hypotheses and show: possible cause → why it is plausible → source metric/event.
- Human-controlled incident lifecycle, advisory-only remediation, feedback capture, full audit trail, correlation, replay protection, and deduplication.
- PostgreSQL persistence for the complete Compose/Railway runtime and SQLite as an explicit local/test fallback.
- Same-origin operator API: the browser never needs direct access to private provider or traffic-generator services.
- 66 automated tests covering domain contracts, OpenAI request/response validation with local fakes, the network-request gate, catalog bypass, orchestration, API access control, demo controls, observability assets, and persistence selection.

No production mock analyzer remains. Runtime configuration requires an `OPENAI_API_KEY`. A separate `APM_INCIDENT_OPENAI_REQUESTS_ENABLED` gate defaults to `false`, so a key can be configured without accidental external requests during local verification. Automated tests inject local fake analyzers and never call the external API.

## How the pipeline works

```text
weighted healthy traffic ──> synthetic providers ──> client/provider metrics ──> Prometheus ──> Grafana
          │                         │                                             │
          │                         └─ normalized non-success events              └─ alert rules
          │                                           │                                  │
          └─ operator scenarios                        ▼                                  ▼
                                              provider-event store <──────── Alertmanager webhook
                                                         │
                                                         ▼
                                                evidence bundle
                                           metrics + signals + events
                                                         │
                                                         ▼
                                               catalog-first classifier
                                              /                        \
                           known provider response                      unknown incident
                           deterministic runbook                         OpenAI reasoning
                                              \                        /
                                                         ▼
                                             PostgreSQL incident + audit
                                                         │
                                                         ▼
                                           operator console + live stream
```

The deterministic boundary is deliberate. Thresholds, alert routing, known response codes, deduplication, and lifecycle transitions are rules. OpenAI is used only when the normalized evidence does not match a reviewed catalog entry. The model proposes a structured explanation and reversible investigation steps; it cannot change routing, retry payments, or resolve an incident.

The incident bounded context is under `src/apm_demo/incidents`:

- `domain`: evidence, signals, cause hypotheses, incident, feedback, and audit contracts;
- `ports`: metrics, analyzer, repositories, catalog, and provider-event interfaces;
- `application`: anomaly detection, catalog-first classification, alert pipeline, and lifecycle use cases;
- `infrastructure`: Prometheus, OpenAI, SQLite, PostgreSQL, and observability adapters;
- `api`: configuration, dependency composition, protected integration routes, and same-origin demo controls;
- `web`: dependency-free operator console.

## Run locally with Docker Compose

Requirements: Docker Desktop with Compose and a usable OpenAI API key.

1. Copy `.env.example` to the ignored `.env` file and replace placeholders.
2. Create the four ignored secret files described in `secrets/README.md`.
3. Start the stack:

```powershell
docker compose up --build
```

Local endpoints:

| Surface | URL |
| --- | --- |
| Incident console | `http://localhost:8002` |
| Provider observability dashboard | `http://localhost:3000/d/apm-provider-observability/apm-provider-observability` |
| Incident pipeline dashboard | `http://localhost:3000/d/incident-intelligence-pipeline/incident-intelligence-pipeline` |
| Prometheus | `http://localhost:9090` |
| Provider emulator API | `http://localhost:8000` |
| Traffic generator API | `http://localhost:8001` |

The OpenAI key remains server-side and is never returned by the runtime API or exposed to the browser. With `APM_INCIDENT_OPENAI_REQUESTS_ENABLED=true`, applying an unknown-error or business-decline scenario can cause a paid OpenAI request after the alert fires or when the operator runs analysis.

## Demo walkthrough: baseline → incident → recovery

1. Open the provider Grafana dashboard and the incident console side by side.
2. In the console, select each provider and use **Recover selected provider** if a previous scenario is active.
3. Keep traffic enabled and set it to 20 RPS. The generator produces mostly successful traffic using the weighted provider, method, merchant, and region distributions.
4. Observe the stable baseline in **Transaction rate by provider**, **Success rate by provider**, **Actual vs target traffic**, and **Active provider scenario**.
5. Apply one scenario:
   - **Known technical error (AtlasPay)** — deterministic catalog response, no OpenAI request;
   - **Unknown technical error (OpenAI)** — unmapped OrbitWallet response;
   - **Business decline (OpenAI)** — soft/hard declines rise while technical errors stay low;
   - latency, timeout, or health-down scenarios for their dedicated alerts.
6. The unaffected providers continue healthy traffic, so Grafana shows the degraded provider diverging from the baseline. The active-scenario and configured-behavior panels identify where the incident came from.
7. Alert rules evaluate every 5 seconds and require 15–60 seconds of sustained degradation. The incident then appears automatically through Alertmanager; **Analyze now** can also evaluate the current metrics window.
8. Inspect classification, evidence, cause category, rationale, source references, confidence, recommended checks, and audit history.
9. Select the affected provider and use **Recover selected provider**. Grafana shows recovery while Alertmanager closes the firing condition.

## Configuration

Use the ignored `.env` file for local values:

```dotenv
OPENAI_API_KEY=replace_with_your_real_key
APM_INCIDENT_OPENAI_MODEL=gpt-5.4-mini
APM_INCIDENT_OPENAI_REQUESTS_ENABLED=false
APM_REQUESTS_PER_SECOND=4
APM_INCIDENT_METRICS_MODE=prometheus
APM_INCIDENT_GRAFANA_PUBLIC_URL=http://localhost:3000
APM_INCIDENT_METRICS_TOKEN=replace_with_a_random_scrape_token
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=replace_with_a_random_admin_password
```

Operator Basic authentication is optional on localhost and required by the Railway design:

```dotenv
APM_INCIDENT_OPERATOR_AUTH_ENABLED=true
DEMO_AUTH_USER=demo-operator
DEMO_AUTH_PASSWORD=replace_with_a_random_operator_password
```

## HTTP API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Service liveness |
| `GET` | `/ready` | Persistence readiness without credential disclosure |
| `GET` | `/metrics` | Pipeline metrics; dedicated bearer token required |
| `GET` | `/api/v1/runtime` | Safe runtime metadata and Grafana links |
| `GET` | `/api/v1/demo/control` | Read generator and provider state through the operator facade |
| `PATCH` | `/api/v1/demo/traffic` | Start/stop traffic and change target RPS |
| `POST` | `/api/v1/demo/scenarios` | Apply or recover a provider scenario |
| `POST` | `/api/v1/incidents/analyze` | Run catalog-first analysis for one provider window |
| `GET` | `/api/v1/incidents` | List and filter incidents |
| `GET` | `/api/v1/incidents/{id}/audit` | Read the incident audit trail |
| `PATCH` | `/api/v1/incidents/{id}/status` | Acknowledge or resolve an incident |
| `POST` | `/api/v1/incidents/{id}/feedback` | Record operator feedback |
| `POST` | `/api/v1/integrations/alertmanager` | Receive authenticated Alertmanager webhooks |
| `POST` | `/api/v1/provider-events` | Store allowlisted, normalized provider evidence |
| `GET/POST/PUT/DELETE` | `/api/v1/catalog/...` | Manage versioned deterministic known-error rules |

OpenAPI documentation is at `http://localhost:8002/docs`.

## Assessment alignment

The source assignment asks for practical, controlled improvement of APM operational workflows, with emphasis on incident triage, signal validation, summarization, routing, runbook support, and process automation. This repository implements the working-code demonstration; the remaining written-submission work is tracked below rather than presented as complete.

### Assignment objective and scenario coverage

| Requirement from the task | Status | Evidence |
| --- | --- | --- |
| Reduce manual signal review and evidence collection | Done | Alerts, Prometheus queries, normalized provider events, evidence bundles |
| Improve consistency and decision speed | Done | Deterministic thresholds, catalog bypass, structured output, deduplication |
| Payment dashboards and transaction metrics | Done | Two Grafana dashboards and weighted baseline traffic |
| Provider/PSP status pages | Not yet | Graph node `N3` |
| Support tickets, Slack, email escalations | Not yet | Graph node `N3` |
| Merchant complaints and operational reports | Not yet | Graph node `N3` |
| Manual operations checks | Partial | Operator-triggered analysis and status controls exist; formal runbook checklist is in `N1` |
| Working practical demonstration | Done in code | Compose stack, controls, scenarios, alerts, incidents, Grafana, Postgres |
| Short written proposal with three AI use cases | Not yet | Graph node `N1` |

### Evaluation criteria

| Criterion | Current coverage | Remaining gap |
| --- | --- | --- |
| Operational thinking | Realistic provider-specific triage, business/technical separation, prioritization, recovery | Summarize handoffs and assumptions in the 2–4 page proposal (`N1`) |
| AI judgment | Rules handle known errors; OpenAI handles unknowns; failures close to manual review | Add explicit three-use-case comparison and limitations (`N1`) |
| Agent design | Trigger, normalized inputs, decision branch, advisory actions, human status control, guardrails, audit trail | Produce the concise submission diagram/narrative (`N1`) |
| Automation mindset | Metrics → alert → evidence → classification → incident → operator feedback is automated | Add external status/support/communication connectors (`N3`) |
| Communication | README, API docs, dashboards, and compact operator UI are implemented | Final 2–4 page decision-oriented submission and optional walkthrough (`N1`, `N7`) |

### Optional bonus coverage

| Bonus | Status |
| --- | --- |
| Prompt structure | Implemented as versioned system instructions plus strict JSON Schema |
| Confidence and severity | Implemented |
| Feedback loop | Feedback capture implemented; offline evaluation/improvement loop remains `N2` |
| Cost/latency trade-offs | Not yet; `N2` |
| KPI framework | Not yet; `N2` |

## Remaining graph nodes

```text
N0 Current working prototype
 ├──> N1 Written assessment package
 │     problem framing + 3 AI use cases + detailed agent design + human handoffs
 ├──> N2 Evaluation and KPI package
 │     golden incident set + accuracy/coverage + MTTA/MTTR + cost/latency budget
 ├──> N3 External signal integrations
 │     provider status + support/Slack/email + merchant/ops reports
 └──> N4 Enable gate and run live OpenAI smoke [requires explicit approval for paid request]
          │
          └──> N5 Complete end-to-end scenario verification
                    │
          N1 + N2 + N5 ──> N6 Railway deployment and public-access verification
                                      │
                                      └──> N7 Final submission bundle / optional video
```

Completion evidence for each node:

- `N1`: a self-contained 2–4 page proposal mapped to every mandatory deliverable.
- `N2`: repeatable eval command, measured results, KPI definitions, and model cost/latency decision.
- `N3`: at least one authenticated external-signal adapter plus normalized contract and failure tests.
- `N4`: enable the runtime gate for one approved real request proving structured output, request ID, token usage, and redaction boundaries.
- `N5`: baseline → known catalog incident → recovery and baseline → unknown OpenAI incident → recovery, with screenshots/log evidence.
- `N6`: healthy Railway services, protected public URLs, PostgreSQL persistence, and rollback evidence.
- `N7`: repository link, written proposal, demo URLs, and optional walkthrough packaged for submission.

## Railway deployment

Railway is designed as separate services plus managed PostgreSQL. The machine-readable inventory is `railway/services.json`; network boundaries, variables, rollout order, verification, and rollback are documented in `docs/architecture/railway-deployment.md`.

Only Grafana, Prometheus, and the Incident API/UI should receive public domains. Provider emulator, traffic generator, Alertmanager, and PostgreSQL remain on private networking. Public-domain creation, credential uploads, and the first paid OpenAI analysis are explicit rollout gates.

## Verification boundary

The repository has 66 passing automated tests. The post-change Compose build, healthy baseline, UI controls, Grafana provisioning, Prometheus targets, legacy-data migration, and the known catalog incident/recovery flow have been verified locally. These checks keep the OpenAI network gate disabled; the approved live OpenAI branch and its complete recovery capture remain `N4`–`N5`.
