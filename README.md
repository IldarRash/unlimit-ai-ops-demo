# Sentinel — AI-assisted payment incident intelligence

Sentinel is a working Technical Operations demo for detecting degradation across synthetic Alternative Payment Method providers. It combines provider emulation, generated traffic, Prometheus/Grafana observability, deterministic anomaly detection, and an operator-controlled LLM investigation pipeline.

## What is implemented

- Three payment providers with configurable latency, outcomes, timeouts, and health behavior.
- Traffic generation with bounded Prometheus labels and client-observed metrics.
- A provisioned Grafana dashboard for success rate, p95 latency, errors, timeouts, and health.
- An incident pipeline with normalized evidence, threshold detection, severity, correlation, and deduplication.
- Interchangeable deterministic and OpenAI analyzers behind one application port.
- Strict structured output, bounded retries, `store=false`, and advisory-only remediation actions.
- Incident lifecycle, repository ports, SQLite for local development, and PostgreSQL for Railway persistence.
- Durable incidents, normalized evidence, provider events, feedback, catalog versions, audit events, and webhook delivery state.
- Separate operator, scrape, Alertmanager, provider-event, and catalog-administration credentials.
- A versioned FastAPI surface and responsive incident console served by the same service.

## Architecture

```text
provider emulator ──> traffic generator ──> Prometheus ──> Grafana
                                                │
                                                ▼
                                       metrics source port
                                                │
                                                ▼
detector ──> evidence bundle ──> incident orchestrator ──> analyzer port
                                                │              ├─ mock
                                                │              └─ OpenAI
                                                ▼
                                   PostgreSQL + audit log
                                                │
                                                ▼
                                        FastAPI + web console
```

The incident bounded context lives under `src/apm_demo/incidents`:

- `domain`: provider evidence, signals, analysis, incident, and audit contracts.
- `ports`: metrics, analyzer, repository, and audit interfaces.
- `application`: deterministic detection and orchestration use cases.
- `infrastructure`: Prometheus, OpenAI, mock, SQLite, and PostgreSQL adapters.
- `api`: configuration, dependency composition, HTTP delivery, and logging.
- `web`: dependency-free same-origin operator console.

## Run the incident console without Docker

Create and activate a Python 3.12 environment, install the project, then start the API:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\uvicorn.exe apm_demo.incidents.api.app:app --app-dir src --host 127.0.0.1 --port 8002
```

Open `http://127.0.0.1:8002`. The safe default is:

```dotenv
APM_INCIDENT_METRICS_MODE=demo
APM_INCIDENT_ANALYZER_MODE=mock
```

`/metrics` is always protected. Set a local token before scraping it:

```dotenv
APM_INCIDENT_METRICS_TOKEN=replace_with_a_random_scrape_token
```

Operator Basic authentication is optional for localhost and mandatory in the Railway manifest:

```dotenv
APM_INCIDENT_OPERATOR_AUTH_ENABLED=true
DEMO_AUTH_USER=demo-operator
DEMO_AUTH_PASSWORD=replace_with_a_random_operator_password
```

AtlasPay and OrbitWallet produce demo incidents; NovaBank demonstrates the healthy/no-incident path.

## Enable OpenAI analysis

Put the real key only in the ignored `.env` file and switch the adapter:

```dotenv
OPENAI_API_KEY=replace_with_your_real_key
APM_INCIDENT_ANALYZER_MODE=openai
APM_INCIDENT_OPENAI_MODEL=gpt-5.4-mini
```

The key is never returned by the API or exposed to the browser. Live calls incur API usage and are intentionally not part of the current automated verification phase.

## HTTP API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Service liveness |
| `GET` | `/ready` | Persistence readiness without credential disclosure |
| `GET` | `/metrics` | Pipeline metrics; dedicated bearer token required |
| `GET` | `/api/v1/runtime` | Safe runtime mode metadata |
| `POST` | `/api/v1/incidents/analyze` | Analyze one provider evidence window |
| `GET` | `/api/v1/incidents` | List recent incidents |
| `GET` | `/api/v1/incidents/{id}` | Read one incident |
| `GET` | `/api/v1/incidents/{id}/audit` | Read its audit trail |
| `PATCH` | `/api/v1/incidents/{id}/status` | Acknowledge or resolve |
| `POST` | `/api/v1/integrations/alertmanager` | Receive authenticated Alertmanager webhooks |
| `POST` | `/api/v1/provider-events` | Store bounded normalized provider evidence |
| `GET/POST/PUT/DELETE` | `/api/v1/catalog/...` | Manage versioned deterministic known-error rules |

OpenAPI documentation is available at `http://127.0.0.1:8002/docs`.

## Docker Compose

When Docker is available:

```powershell
docker compose up --build
```

Copy `.env.example` to the ignored `.env`, replace its placeholder credentials, and create the three ignored token files described in `secrets/README.md` before starting Compose.

- Incident console: `http://localhost:8002`
- Grafana: `http://localhost:3000`
- Prometheus: `http://localhost:9090`
- Provider emulator: `http://localhost:8000`
- Traffic generator: `http://localhost:8001`

The incident service uses Prometheus in Compose and mock analysis unless `APM_INCIDENT_ANALYZER_MODE=openai` is explicitly configured.

## Railway deployment

Railway runs the six containers as separate services plus managed PostgreSQL. The machine-readable service inventory is `railway/services.json`; the deployment architecture, access boundaries, protected variables, rollout order, verification, and rollback contract are in `docs/architecture/railway-deployment.md`.

Only Grafana, Prometheus, and the Incident API/UI are intended to receive public domains. Grafana login, Prometheus Basic authentication, and Incident operator Basic authentication protect those surfaces. Provider emulator, traffic generator, Alertmanager, and PostgreSQL remain on Railway private networking.

Railway injects `DATABASE_URL`. When that value is present, the Incident service opens a bounded asynchronous PostgreSQL pool and applies its idempotent schema migration under a transaction-level advisory lock. Without it, local development explicitly falls back to SQLite.

No secrets belong in Git. Uploads of integration credentials, operator/Grafana credentials, and `OPENAI_API_KEY`, creation of public domains, and the first paid OpenAI call are deliberate deployment gates rather than repository setup steps.

## Verification boundary

The repository currently has 54 passing domain, adapter, orchestration, API, access-control, configuration, and static UI tests. Docker Compose configuration and shell syntax are validated without starting containers. Full image/runtime and deployed end-to-end verification remains a Railway rollout gate because the host Docker/WSL2 runtime is unavailable.
