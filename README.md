# Payment Provider Incident Intelligence

A working payment-operations demo: realistic provider traffic is observed by Prometheus and Grafana, alerts are converted into evidence-backed incidents, known incidents are handled by database rules, and OpenAI is used once only when the system cannot classify the incident.

## How it works

```text
Traffic generator
      │
      ├──> Provider emulators ──> normalized provider responses ──> PostgreSQL
      │                                  (no customer payloads)
      │
      └──> Prometheus metrics ──> alert rules ──> Alertmanager
                                          │
Manual Analyze now ────────────────────────┤
                                          ▼
                                  Evidence collection
                         metrics + alerts + provider events
                              + external operational signals
                                          │
                                          ▼
                          PostgreSQL response-code catalog
                                          │
                                          ▼
                         PostgreSQL known-incident rules
                              /                       \
                   rule matched                  no rule matched
                        │                              │
              deterministic report             one OpenAI request
                  no LLM call                 for the complete report
                              \                       /
                                          ▼
                         verified quantitative conclusion
                                          │
                                          ▼
                         PostgreSQL incident + audit trail
                                          │
                                          ▼
                                  Operator console
```

The boundary is intentionally simple: there is one LLM decision point and no separate model call for an individual response code.

### Database catalogs and rules

PostgreSQL is the runtime source of truth. SQLite implements the same ports for local tests and a single-process fallback.

`response_code_definitions` is a versioned dictionary of reviewed response meanings:

- `definition_id` and `version` identify history;
- `provider = NULL` defines a global meaning;
- a provider-specific definition overrides a global definition for the same code;
- `name` and `description` are shown as database-catalog evidence;
- only active definitions participate in analysis.

The application seeds reviewed demo definitions for `APPROVED`, `DO_NOT_HONOR`, `INVALID_ACCOUNT`, `PROVIDER_TIMEOUT`, `TRANSPORT_ERROR`, and the AtlasPay-specific `UPSTREAM_ERROR`. These are demo operational definitions, not claims copied from a real provider.

`known_error_rules` contains versioned deterministic incident rules. A rule can match:

- provider and response code;
- optionally outcome, payment method, and region;
- the most specific active rule wins;
- two equally specific overlapping rules are rejected as ambiguous.

Each known-incident rule stores the reviewed headline, summary, impact, probable causes, operator decision, operator checks, confidence, and an optional reference URL. Catalog definitions explain response codes; known-error rules decide whether the incident itself is understood.

Both catalogs are durable database data. The protected APIs are:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET/POST` | `/api/v1/response-code-catalog` | Read definitions or create a new version |
| `GET/POST/PUT/DELETE` | `/api/v1/catalog/...` | Manage known-incident rule versions |
| `GET` | `/api/v1/catalog/audit` | Read the known-rule audit trail |

They require `X-Catalog-Admin-Token`. Runtime code contains only idempotent bootstrap seed data so a new database can start; classification always reads active records from persistence.

### The single LLM rule

The detector and classifier evaluate the following decision after evidence and database definitions have been loaded:

| Condition | Report path | OpenAI |
| --- | --- | --- |
| Metrics remain inside configured thresholds | Treat individual failures as background traffic; do not create an incident | Not called |
| An active `known_error_rules` entry matches a provider event | Build the full report from the stored rule | Not called |
| No known-incident rule matches | Ask OpenAI for one complete structured incident report | Called once, only if the runtime gate is enabled |
| No rule matches, but the OpenAI gate/key/provider is unavailable | Create a manual-review incident with concrete evidence checks | Not called or failed closed |
| Catalog lookup is ambiguous | Stop automated reasoning and require manual review | Not called |

An unknown response code does **not** independently trigger OpenAI. If the incident is unknown, the same single report request also receives:

- reviewed database definitions as authoritative context;
- the exact list of response codes missing from the database;
- normalized provider events supporting each code.

The same single response contains both the incident conclusion and exactly one bounded explanation for every missing code. The backend rejects missing, duplicate, extra, or unreferenced explanations. It also rejects `monitor-only` when any measured signal is critical.

Every stored report contains one operator disposition:

- `action-required` — investigate or intervene now using the listed safe checks;
- `monitor-only` — likely background noise; no immediate change, but watch the stated escalation condition;
- `manual-review` — the cause could not be classified safely, so a human must inspect the measured evidence before changing routing or retry behaviour.

The operator console uses these operational labels and does not expose model or prompt terminology.

### Full incident path

1. The traffic generator continuously sends a weighted healthy baseline to AtlasPay, NovaBank, and OrbitWallet. A selected scenario changes one provider while the other traffic remains healthy.
2. Prometheus collects request counts, outcome counts, latency histograms, health, and payment-method breakdowns. Only throughput is expressed as RPS; incident impact uses integer request counts.
3. Non-success provider responses are normalized into allowlisted `ProviderEvent` records. Raw transactions, credentials, and customer data are not stored in the incident evidence.
4. An Alertmanager webhook or the operator's **Analyze now** action starts the same pipeline.
5. The backend collects a bounded evidence bundle: one Prometheus window, detected signals, up to 20 response events matching those signals and that exact window, alert metadata, and sanitized external operational signals. Unrelated declines or errors are not presented as causes of another incident.
6. Active `response_code_definitions` are resolved from the database. Provider-specific definitions take precedence over global definitions.
7. Active `known_error_rules` are matched from most specific to least specific.
8. If a rule matches, the application builds the narrative from reviewed database data and skips OpenAI. If no rule matches, one `incident-v7` OpenAI Responses request produces a concise conclusion, operator disposition, causes, evidence references, recommended checks, and explanations for uncatalogued codes.
9. Model output is advisory. The backend validates the JSON schema, evidence references, response-code coverage, and non-automatable recommendations.
10. The backend—not the model—calculates the exact window, affected outcomes, affected/all traffic, percentage, and payment-method shares from Prometheus counts. Domain validation rejects inconsistent arithmetic.
11. The incident is fingerprinted by provider and detected signal types. Manual analysis and a later Alertmanager notification reuse the same active incident, preventing a second paid analysis for the same degradation. The record is persisted with its evidence and audit event, streamed to the UI, and later resolved by Alertmanager recovery or an explicit operator action.

The report therefore combines three clearly labelled sources:

- **Database catalog** — reviewed code meanings and known-incident narratives;
- **Investigation hypothesis** — generated by OpenAI only as part of a complete unknown incident report;
- **Verified calculation** — time window, counts, shares, and payment-method impact calculated by the backend.

OpenAI cannot change routing, retry payments, execute remediation, acknowledge, or resolve an incident. `APM_INCIDENT_OPENAI_REQUESTS_ENABLED` defaults to `false`, so configuring a key does not by itself authorize paid requests.

## Deployed Railway demo

| Surface | URL | Login |
| --- | --- | --- |
| Incident console | [incident-api-demo.up.railway.app](https://incident-api-demo.up.railway.app) | `demo-operator`; password is the Railway `incident-api` variable `DEMO_AUTH_PASSWORD` |
| Grafana | [grafana-demo-0349.up.railway.app](https://grafana-demo-0349.up.railway.app) | `demo-viewer`; password is the Railway `grafana` variable `GRAFANA_TESTER_PASSWORD` |
| Prometheus | [prometheus-demo-8480.up.railway.app](https://prometheus-demo-8480.up.railway.app) | `metrics-reader`; password is the Railway `grafana` variable `PROMETHEUS_WEB_PASSWORD` |

Healthy weighted traffic can be started at 20 requests per second. The Railway environment has the paid-analysis gate enabled: **Unknown OrbitWallet error** makes a real OpenAI request when a new uncatalogued incident crosses a threshold. Use **Recover provider** after the demonstration. Known AtlasPay errors and background traffic do not call OpenAI.

## Run locally

Requirements: Docker Desktop with Docker Compose.

1. Copy `.env.example` to `.env` and set local values:

```dotenv
OPENAI_API_KEY=replace_with_your_real_key
APM_INCIDENT_OPENAI_MODEL=gpt-5.4-mini
APM_INCIDENT_OPENAI_REQUESTS_ENABLED=false
APM_INCIDENT_LLM_TIMEOUT_SECONDS=30
APM_REQUESTS_PER_SECOND=20
APM_INCIDENT_METRICS_MODE=prometheus
APM_INCIDENT_GRAFANA_PUBLIC_URL=http://localhost:3000
APM_INCIDENT_PROMETHEUS_USERNAME=
APM_INCIDENT_PROMETHEUS_PASSWORD=
```

2. Create the ignored secret files described in `secrets/README.md`: PostgreSQL password, provider-event token, catalog-admin token, and Alertmanager webhook token.

3. Build and start the stack:

```powershell
docker compose up --build
```

4. Wait for readiness:

```powershell
docker compose ps
```

Local surfaces:

| Surface | URL |
| --- | --- |
| Incident console | `http://localhost:8002` |
| Provider Grafana dashboard | `http://localhost:3000/d/apm-provider-observability/apm-provider-observability` |
| Incident pipeline dashboard | `http://localhost:3000/d/incident-intelligence-pipeline/incident-intelligence-pipeline` |
| Prometheus | `http://localhost:9090` |
| Incident OpenAPI | `http://localhost:8002/docs` |

### Demo flow

1. Open Grafana and the incident console.
2. Start traffic at 20 RPS.
3. Open **Test scenarios**.
4. Run **Known AtlasPay error** to demonstrate the database-rule path without OpenAI.
5. Recover AtlasPay.
6. Set `APM_INCIDENT_OPENAI_REQUESTS_ENABLED=true` only when a paid call is intended, rebuild the incident API, and run **Unknown OrbitWallet error** to demonstrate the single unknown-incident report request.
7. Inspect the conclusion, verified traffic impact, causes, operator checks, response-code provenance, evidence details, and audit trail.

### Controls and scenarios

| Control | What it does | Expected incident path |
| --- | --- | --- |
| **Start/Stop traffic** | Starts or pauses weighted traffic; the RPS field changes its target rate | Healthy baseline creates metrics but no incident |
| **Analyze now** | Evaluates the selected provider immediately using the same pipeline as an alert | Creates or updates an incident only when a threshold is crossed |
| **Recover provider** | Restores the selected provider baseline | Prometheus resolves the active alert after recovery is observed |
| **Known AtlasPay error** | Raises AtlasPay `UPSTREAM_ERROR` responses | Database rule, no OpenAI request |
| **Unknown OrbitWallet error** | Raises OrbitWallet `UNMAPPED_PROVIDER_FAILURE` responses | One OpenAI report for a new active incident when paid requests are enabled |
| **Business declines** | Raises issuer-style soft and hard declines for OrbitWallet | Business decline incident, separate from technical failures |
| **Latency** | Raises payment processing latency for the selected provider | p95 latency incident |
| **Timeout** | Makes most selected-provider payment requests exceed the client timeout | Timeout incident; latency/error alerts may corroborate it |
| **Health down** | Returns an unhealthy provider health check | Health incident without unrelated payment response codes |
| **Health timeout** | Makes the provider health endpoint stop responding in time | Health incident representing a non-responsive check |

Stop the stack with:

```powershell
docker compose down
```

Run the automated suite without external OpenAI requests:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```
