# Railway deployment architecture

## Target

- Railway workspace: current authenticated personal workspace.
- Project: `unlimit-ai-ops-demo`.
- Environment: `demo`.
- Source: private GitHub repository `unlimit-ai-ops-demo`.
- Constraint: use the current Railway plan only; do not purchase or upgrade anything.

Railway deploys each component as an independent service. Internal calls use Railway private DNS in the form `<service>.railway.internal:8080`, matching Railway's injected runtime `PORT`; only the explicitly approved user-facing services receive public domains. The component-specific ports below remain the local Docker Compose ports.

## Service graph

| Service | Port | Exposure | Health check | Persistence |
| --- | ---: | --- | --- | --- |
| `provider-emulator` | 8000 | private | `/health` | ephemeral |
| `traffic-generator` | 8001 | private | `/health` | ephemeral |
| `prometheus` | 9090 | public after JIT approval | disabled; `/-/healthy` is auth-protected | ephemeral, six-hour retention |
| `alertmanager` | 9093 | private | `/-/healthy` | ephemeral |
| `grafana` | 3000 | public after JIT approval | `/api/health` | provisioned configuration |
| `incident-api` | 8002 | public after JIT approval | `/ready` | managed PostgreSQL |
| `postgres` | 5432 | private | Railway managed | durable volume |

The runtime flow is:

1. The traffic generator sends controllable good, slow, failing, and missing-response transactions to the provider emulator.
2. Prometheus scrapes emulator, generator, and incident-pipeline metrics.
3. Prometheus evaluates alert rules and sends firing/resolved alerts to the private Alertmanager service.
4. Alertmanager calls the Incident API through Railway private networking.
5. Authenticated adapters can add sanitized provider-status, support, Slack/email, merchant, and operations signals to the same provider-scoped evidence window.
6. The Incident API uses deterministic provider knowledge first; unknown failures receive bounded metric, provider-event, and external-signal context and can be analyzed by OpenAI.
7. Incidents, evidence, audit records, deliveries, and operator feedback are stored in PostgreSQL without automatic expiry.
8. Grafana reads Prometheus through private networking and provides the monitoring dashboard.

## Access boundary

- Incident UI and ordinary Incident API routes use HTTP Basic authentication.
- Alertmanager ingestion, provider-event ingestion, external-signal ingestion, catalog administration, and Prometheus scraping of Incident API metrics each use a separate bearer token in Railway.
- Railway does not publish a stable application-specific private-service CIDR. The deployment keeps source-network validation enabled and accepts only loopback plus standard private IPv4 and unique-local IPv6 ranges. Every ingestion route also requires its own high-entropy bearer token, so network location alone is never sufficient.
- Prometheus public access uses its native web configuration with a bcrypt password hash.
- Grafana anonymous access is disabled; its administrator account is configured from Railway secrets.
- Alertmanager, the provider emulator, traffic generator, and PostgreSQL remain private and receive no public domain.
- Health and readiness endpoints disclose only service state, not credentials or incident payloads.

## Protected variables

The following values are never committed and require just-in-time approval before upload to Railway:

- `DEMO_AUTH_USER`
- `DEMO_AUTH_PASSWORD`
- `PROMETHEUS_WEB_USERNAME`
- `PROMETHEUS_WEB_PASSWORD`
- `PROMETHEUS_WEB_PASSWORD_HASH`
- `GF_SECURITY_ADMIN_USER`
- `GF_SECURITY_ADMIN_PASSWORD`
- `GRAFANA_TESTER_USER`
- `GRAFANA_TESTER_PASSWORD`
- `APM_INCIDENT_METRICS_TOKEN`
- `APM_INCIDENT_ALERTMANAGER_TOKEN`
- `APM_INCIDENT_PROVIDER_EVENT_TOKEN`
- `APM_INCIDENT_EXTERNAL_SIGNAL_TOKEN`
- `APM_INCIDENT_CATALOG_ADMIN_TOKEN`
- `OPENAI_API_KEY`
- Grafana administrator credentials generated for this deployment

Railway reference variables are used for service-to-service URLs and managed PostgreSQL's `DATABASE_URL` so that derived infrastructure values are not copied as secrets.

## Required reference and control variables

| Service | Variable | Railway value/source |
| --- | --- | --- |
| `traffic-generator` | `APM_PROVIDER_BASE_URL` | `http://provider-emulator.railway.internal:8080` |
| `traffic-generator` | `APM_INCIDENT_API_URL` | `http://incident-api.railway.internal:8080` |
| `traffic-generator` | `APM_PROVIDER_EVENT_TOKEN` | same secret value as Incident API provider-event token |
| `grafana` | `PROMETHEUS_URL` | `http://prometheus.railway.internal:8080` |
| `incident-api` | `APM_INCIDENT_METRICS_MODE` | `prometheus` |
| `incident-api` | `APM_INCIDENT_PROMETHEUS_URL` | `http://prometheus.railway.internal:8080` |
| `incident-api` | `APM_INCIDENT_TRAFFIC_GENERATOR_URL` | `http://traffic-generator.railway.internal:8080` |
| `incident-api` | `APM_INCIDENT_PROVIDER_EMULATOR_URL` | `http://provider-emulator.railway.internal:8080` |
| `incident-api` | `APM_INCIDENT_GRAFANA_PUBLIC_URL` | approved Grafana public URL after domain creation |
| `incident-api` | `APM_INCIDENT_ENFORCE_INGRESS_NETWORKS` | `true` |
| `incident-api` | `APM_INCIDENT_TRUSTED_INGRESS_NETWORKS` | loopback, RFC 1918 IPv4, and unique-local IPv6 ranges; bearer tokens remain mandatory |
| `incident-api` | `APM_INCIDENT_OPENAI_MODEL` | `gpt-5.4-mini` |
| `incident-api` | `APM_INCIDENT_OPENAI_REQUESTS_ENABLED` | `false` until the separately approved live smoke |
| `incident-api` | `DATABASE_URL` | managed PostgreSQL reference variable |

## Deployment sequence

1. Validate the local tree, migrations, authentication boundaries, tests, and deployment configuration.
2. Create the private GitHub repository, make the initial neutral commit, and push `main`.
3. Create the Railway project and `demo` environment.
4. Provision managed PostgreSQL and the six application services.
5. Connect each service to the correct Dockerfile and configure non-secret reference variables.
6. Ask for just-in-time approval and upload each secret without printing its value.
7. Deploy private services first, then Prometheus, Grafana, and Incident API.
8. Ask for separate just-in-time approval before creating each public domain.
9. Verify health, authentication, private connectivity, alert ingestion, PostgreSQL persistence, and browser surfaces.
10. With the author's paid-request approval, enable the runtime gate and verify exactly one unknown-error incident end to end. Recover the provider immediately after the check so continuous healthy traffic does not create repeated paid analyses.

## Verification and rollback

Acceptance evidence includes successful tests, authenticated `401`/successful request pairs, healthy Railway deployments, a firing-to-resolved incident flow, a stored incident surviving an Incident API restart, populated Grafana panels, and an unknown incident carrying structured model provenance.

Rollback is performed by redeploying the preceding known-good deployment or disabling a public domain. PostgreSQL data is retained indefinitely until the author explicitly requests deletion. No project, environment, service, domain, repository, or database is deleted automatically.

## Railway references

- [Deploying Docker Compose services](https://docs.railway.com/guides/docker-compose)
- [Private networking](https://docs.railway.com/networking/private-networking)
- [Dockerfile paths](https://docs.railway.com/builds/dockerfiles)
- [PostgreSQL and `DATABASE_URL`](https://docs.railway.com/databases/postgresql)
- [Reference variables](https://docs.railway.com/variables/reference)
- [Public domains](https://docs.railway.com/networking/domains/working-with-domains)
- [Deployment health checks](https://docs.railway.com/deployments/healthchecks)
