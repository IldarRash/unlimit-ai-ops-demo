import json
import re
from pathlib import Path

import yaml

from apm_demo.common.contracts import ScenarioName


ROOT = Path(__file__).resolve().parents[1]


def test_prometheus_scrapes_all_application_services_and_routes_alerts() -> None:
    config = yaml.safe_load(
        (ROOT / "infra/prometheus/prometheus.yml").read_text(encoding="utf-8")
    )
    jobs = {job["job_name"]: job for job in config["scrape_configs"]}

    assert jobs["provider-emulator"]["static_configs"][0]["targets"] == [
        "provider-emulator:8000"
    ]
    assert jobs["traffic-generator"]["static_configs"][0]["targets"] == [
        "traffic-generator:8001"
    ]
    assert jobs["incident-api"]["static_configs"][0]["targets"] == [
        "incident-api:8002"
    ]
    assert config["rule_files"] == ["/etc/prometheus/alert.rules.yml"]
    assert config["alerting"]["alertmanagers"][0]["static_configs"][0][
        "targets"
    ] == ["alertmanager:9093"]


def test_grafana_dashboard_covers_required_observability_signals() -> None:
    dashboard = json.loads(
        (
            ROOT
            / "infra/grafana/dashboards/apm-provider-observability.json"
        ).read_text(encoding="utf-8")
    )
    titles = {panel["title"] for panel in dashboard["panels"]}
    expressions = "\n".join(
        target["expr"]
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
    )

    assert dashboard["uid"] == "apm-provider-observability"
    assert dashboard["version"] >= 2
    assert {
        "Success rate by provider",
        "Client vs provider p95 latency",
        "Declines, errors, and timeouts",
        "Provider health observed by client",
        "Traffic generator state",
        "Actual vs target traffic",
        "Business decline count by interval",
        "Technical failure count by interval",
    }.issubset(titles)
    assert titles.isdisjoint(
        {
            "Active provider scenario",
            "Applied provider scenarios",
            "Provider health mode history",
        }
    )
    assert "histogram_quantile(0.95" in expressions
    assert "apm_client_provider_health" in expressions
    assert "apm_provider_request_duration_seconds_bucket" in expressions
    assert "apm_generator_enabled" in expressions
    assert "apm_generator_target_requests_per_second" in expressions
    assert "soft-decline|hard-decline" in expressions
    assert "provider-error|timeout|transport-error" in expressions
    assert "apm_provider_configured_error_ratio" in expressions
    assert "apm_client_timeouts" not in expressions  # counted via bounded outcome label
    assert dashboard["refresh"] == "5s"


def test_grafana_datasource_and_dashboard_are_file_provisioned() -> None:
    datasource = yaml.safe_load(
        (
            ROOT
            / "infra/grafana/provisioning/datasources/prometheus.yml"
        ).read_text(encoding="utf-8")
    )
    provider = yaml.safe_load(
        (
            ROOT
            / "infra/grafana/provisioning/dashboards/dashboards.yml"
        ).read_text(encoding="utf-8")
    )

    configured = datasource["datasources"][0]
    assert configured["url"] == "${PROMETHEUS_URL}"
    assert configured["basicAuth"] is True
    assert configured["basicAuthUser"] == "${PROMETHEUS_WEB_USERNAME}"
    assert configured["secureJsonData"]["basicAuthPassword"] == "${PROMETHEUS_WEB_PASSWORD}"
    assert provider["providers"][0]["options"]["path"] == "/etc/grafana/dashboards"


def test_deployment_assets_use_variable_references_not_credentials() -> None:
    secret_markers = ("demo-admin", "change-me", "dev-", "replace_with")
    assets = (
        ROOT / "railway/services.json",
        ROOT / "infra/prometheus/railway.prometheus.yml.tmpl",
        ROOT / "infra/prometheus/web.yml.tmpl",
        ROOT / "infra/alertmanager/railway.alertmanager.yml.tmpl",
        ROOT / "infra/grafana/provisioning/datasources/prometheus.yml",
    )
    for asset in assets:
        content = asset.read_text(encoding="utf-8")
        assert not any(marker in content for marker in secret_markers), asset

    railway = json.loads((ROOT / "railway/services.json").read_text(encoding="utf-8"))
    services = {service["name"]: service for service in railway["services"]}
    assert services["prometheus"]["network"] == "public-authenticated"
    assert services["grafana"]["network"] == "public-authenticated"
    assert services["incident-api"]["network"] == "public-authenticated"
    assert services["alertmanager"]["network"] == "private"
    assert {
        "APM_INCIDENT_API_URL",
        "APM_PROVIDER_EVENT_TOKEN",
    } <= set(services["traffic-generator"]["requiredVariables"])
    assert {
        "APM_INCIDENT_EXTERNAL_SIGNAL_TOKEN",
        "APM_INCIDENT_PROMETHEUS_URL",
        "APM_INCIDENT_TRAFFIC_GENERATOR_URL",
        "APM_INCIDENT_PROVIDER_EMULATOR_URL",
        "APM_INCIDENT_GRAFANA_PUBLIC_URL",
        "APM_INCIDENT_OPENAI_MODEL",
        "APM_INCIDENT_OPENAI_REQUESTS_ENABLED",
        "OPENAI_API_KEY",
        "DATABASE_URL",
    } <= set(services["incident-api"]["requiredVariables"])


def test_incident_dashboard_only_counts_unique_incidents() -> None:
    dashboard = json.loads(
        (ROOT / "infra/grafana/dashboards/incident-intelligence.json").read_text(
            encoding="utf-8"
        )
    )
    expressions = "\n".join(
        target["expr"]
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
    )
    panels = dashboard["panels"]
    titles = {panel["title"] for panel in panels}

    assert dashboard["uid"] == "incident-intelligence-pipeline"
    assert titles == {
        "Unique incidents in selected period",
        "Incidents by provider",
        "Incidents by error type",
        "Incident count by provider and error type",
    }
    assert all(
        panel["fieldConfig"]["defaults"]["unit"] == "short"
        and panel["fieldConfig"]["defaults"]["decimals"] == 0
        for panel in panels
    )
    assert expressions.count("incident_pipeline_incidents_total") == 4
    assert "[$__range]" in expressions
    assert "rate(" not in expressions
    assert "incident_pipeline_alerts_total" not in expressions
    assert "incident_pipeline_deliveries_total" not in expressions
    assert "incident_pipeline_processing_seconds" not in expressions


def test_business_decline_alert_separates_commercial_from_technical_failures() -> None:
    rules = yaml.safe_load(
        (ROOT / "infra/prometheus/alert.rules.yml").read_text(encoding="utf-8")
    )
    alerts = {rule["alert"]: rule for rule in rules["groups"][0]["rules"]}

    business_decline = alerts["ProviderBusinessDeclineHigh"]
    assert "soft-decline|hard-decline" in business_decline["expr"]
    assert "apm_client_requests_total" in business_decline["expr"]
    assert business_decline["for"] == "1m"
    assert business_decline["labels"]["severity"] == "warning"


def test_failure_dashboards_show_transaction_counts_not_rates() -> None:
    dashboard = json.loads(
        (ROOT / "infra/grafana/dashboards/apm-provider-observability.json").read_text(
            encoding="utf-8"
        )
    )
    panels = {panel["id"]: panel for panel in dashboard["panels"]}

    for panel_id in (13, 14):
        panel = panels[panel_id]
        assert panel["fieldConfig"]["defaults"]["unit"] == "short"
        assert panel["fieldConfig"]["defaults"]["decimals"] == 0
        assert "increase(" in panel["targets"][0]["expr"]
        assert "rate(" not in panel["targets"][0]["expr"]


def test_incident_console_exposes_verified_counts_and_response_provenance() -> None:
    app = (ROOT / "src/apm_demo/incidents/web/app.js").read_text(encoding="utf-8")

    assert 'metric("Attempts"' in app
    assert "Affected / all traffic" in app
    assert "Affected share by payment method" in app
    assert "Response-code evidence" in app
    assert "Database catalog" in app
    assert "recent provider event" in app
    assert "Investigation hypothesis" in app
    assert "Action required" in app
    assert "Monitoring required" in app
    assert "Investigation details" in app
    assert 'class="response-error"' in app
    assert "OpenAI assessment" not in app
    assert "Automated analysis" not in app
    assert "matched response event" in app


def test_incident_console_exposes_every_operator_scenario() -> None:
    page = (ROOT / "src/apm_demo/incidents/web/index.html").read_text(
        encoding="utf-8"
    )
    button_scenarios = set(re.findall(r'data-scenario="([^"]+)"', page))

    assert button_scenarios == {
        scenario.value for scenario in ScenarioName if scenario is not ScenarioName.NORMAL
    }
    assert "Analyze now" not in page
    assert "analyze-button" not in page
