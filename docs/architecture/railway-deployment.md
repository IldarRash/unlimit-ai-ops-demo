# Railway deployment architecture

## Target

- Railway workspace: current authenticated personal workspace.
- Project: `unlimit-ai-ops-demo`.
- Environment: `demo`.
- Source: private GitHub repository `unlimit-ai-ops-demo`.
- Constraint: use the current Railway plan only; do not purchase or upgrade anything.

Railway deploys each component as an independent service. Internal calls use Railway private DNS in the form `<service>.railway.internal`; only the explicitly approved user-facing services receive public domains.

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
5. The Incident API uses deterministic provider knowledge first; unknown failures receive bounded metric and event context and can be analyzed by OpenAI.
6. Incidents, evidence, audit records, deliveries, and operator feedback are stored in PostgreSQL without automatic expiry.
7. Grafana reads Prometheus through private networking and provides the monitoring dashboard.

## Access boundary

- Incident UI and ordinary Incident API routes use HTTP Basic authentication.
- Alertmanager ingestion, provider-event ingestion, catalog administration, and Prometheus scraping of Incident API metrics each use a separate bearer token.
- Railway does not publish a stable private-service CIDR for application allowlists. The Railway deployment therefore disables the optional source-IP guard and treats each route's dedicated high-entropy bearer token as the authoritative control. Local/Compose operation retains source-network validation by default.
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
- `APM_INCIDENT_CATALOG_ADMIN_TOKEN`
- `OPENAI_API_KEY`
- Grafana administrator credentials generated for this deployment

Railway reference variables are used for service-to-service URLs and managed PostgreSQL's `DATABASE_URL` so that derived infrastructure values are not copied as secrets.

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
10. Ask for separate just-in-time approval immediately before the first paid OpenAI request, then verify one unknown-error incident end to end.

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
