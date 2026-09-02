const state = {
  incidents: [],
  selectedId: null,
  audit: [],
  runtime: null,
  control: null,
  loading: false,
  stream: null,
};

const elements = {
  form: document.querySelector("#analyze-form"),
  provider: document.querySelector("#provider"),
  analyzeButton: document.querySelector("#analyze-button"),
  trafficButton: document.querySelector("#traffic-button"),
  trafficRps: document.querySelector("#traffic-rps"),
  actionStatus: document.querySelector("#action-status"),
  providerDashboard: document.querySelector("#provider-dashboard-link"),
  incidentDashboard: document.querySelector("#incident-dashboard-link"),
  scenarioControls: document.querySelector(".scenario-controls"),
  refreshButton: document.querySelector("#refresh-button"),
  incidentList: document.querySelector("#incident-list"),
  incidentCount: document.querySelector("#incident-count"),
  incidentDetail: document.querySelector("#incident-detail"),
  runtimeLabel: document.querySelector("#runtime-label"),
  streamLabel: document.querySelector("#stream-label"),
  filterForm: document.querySelector("#filter-form"),
  filterProvider: document.querySelector("#filter-provider"),
  filterStatus: document.querySelector("#filter-status"),
  filterClassification: document.querySelector("#filter-classification"),
  toast: document.querySelector("#toast"),
};

const labels = {
  "atlas-pay": "AtlasPay",
  "nova-bank": "NovaBank",
  "orbit-wallet": "OrbitWallet",
  open: "Open",
  acknowledged: "Acknowledged",
  resolved: "Resolved",
  critical: "Critical",
  warning: "Warning",
  info: "Info",
  known: "Known catalog",
  unknown: "AI assessed",
  unavailable: "Needs review",
};

let toastTimer;

async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `Request failed with status ${response.status}`);
  }
  return response.status === 204 ? null : response.json();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatTime(value) {
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatRatio(value) {
  return new Intl.NumberFormat(undefined, {
    style: "percent",
    maximumFractionDigits: 1,
  }).format(value);
}

function showToast(message, tone = "info") {
  window.clearTimeout(toastTimer);
  elements.toast.textContent = message;
  elements.toast.dataset.tone = tone;
  elements.toast.dataset.visible = "true";
  toastTimer = window.setTimeout(() => {
    elements.toast.dataset.visible = "false";
  }, 4200);
}

function setLoading(loading) {
  state.loading = loading;
  elements.analyzeButton.disabled = loading;
  elements.refreshButton.disabled = loading;
  elements.trafficButton.disabled = loading;
  elements.scenarioControls.querySelectorAll("button").forEach((button) => {
    button.disabled = loading;
  });
  elements.analyzeButton.querySelector(".button-label").textContent = loading
    ? "Analyzing…"
    : "Analyze now";
}

async function loadRuntime() {
  state.runtime = await request("/api/v1/runtime");
  const analyzer = state.runtime.openai_requests_enabled
    ? state.runtime.model
    : `${state.runtime.model} · requests gated`;
  elements.runtimeLabel.textContent = `${analyzer} · ${state.runtime.metrics_mode} metrics`;
  setDashboardLink(elements.providerDashboard, state.runtime.grafana_provider_dashboard_url);
  setDashboardLink(elements.incidentDashboard, state.runtime.grafana_incident_dashboard_url);
}

function setDashboardLink(element, url) {
  if (!url) return;
  element.href = url;
  element.hidden = false;
}

function setActionStatus(message, tone = "neutral") {
  elements.actionStatus.textContent = message;
  elements.actionStatus.dataset.tone = tone;
}

async function loadControl() {
  state.control = await request("/api/v1/demo/control");
  const generator = state.control.generator || {};
  elements.trafficRps.value = generator.requests_per_second ?? elements.trafficRps.value;
  renderControl();
}

function renderControl() {
  const generator = state.control?.generator || {};
  const enabled = Boolean(generator.enabled);
  elements.trafficButton.querySelector(".traffic-label").textContent = enabled ? "Stop traffic" : "Start traffic";
  elements.trafficButton.dataset.active = String(enabled);
  setActionStatus(enabled ? `Traffic running at ${generator.requests_per_second} RPS` : "Traffic stopped", enabled ? "success" : "neutral");
}

async function loadIncidents(preferredId = state.selectedId) {
  state.incidents = await request(`/api/v1/incidents${filterQuery()}`);
  if (preferredId && state.incidents.some((item) => item.incident_id === preferredId)) {
    state.selectedId = preferredId;
  } else {
    state.selectedId = state.incidents[0]?.incident_id || null;
  }
  await loadSelectedAudit();
  render();
}

function filterQuery() {
  const parameters = new URLSearchParams();
  if (elements.filterProvider.value) parameters.set("provider", elements.filterProvider.value);
  if (elements.filterStatus.value) parameters.set("status", elements.filterStatus.value);
  if (elements.filterClassification.value) {
    parameters.set("classification", elements.filterClassification.value);
  }
  const query = parameters.toString();
  return query ? `?${query}` : "";
}

function connectIncidentStream() {
  state.stream?.close();
  elements.streamLabel.textContent = "Connecting live feed…";
  const stream = new EventSource(`/api/v1/stream/incidents${filterQuery()}`);
  state.stream = stream;
  stream.addEventListener("ready", () => {
    elements.streamLabel.textContent = "Live feed connected";
    document.querySelector(".runtime-dot")?.setAttribute("data-state", "connected");
  });
  stream.addEventListener("incident", async (event) => {
    try {
      const incident = JSON.parse(event.data);
      await loadIncidents(state.selectedId || incident.incident_id);
    } catch (error) {
      showToast(`Live update failed: ${error.message}`, "error");
    }
  });
  stream.onerror = () => {
    elements.streamLabel.textContent = "Live feed reconnecting";
    document.querySelector(".runtime-dot")?.setAttribute("data-state", "reconnecting");
  };
}

async function loadSelectedAudit() {
  state.audit = state.selectedId
    ? await request(`/api/v1/incidents/${encodeURIComponent(state.selectedId)}/audit`)
    : [];
}

async function analyzeProvider(event) {
  event.preventDefault();
  setLoading(true);
  try {
    const result = await request("/api/v1/incidents/analyze", {
      method: "POST",
      body: JSON.stringify({ provider: elements.provider.value }),
    });
    if (!result.detected) {
      setActionStatus(`${labels[elements.provider.value]} is healthy — no anomaly in the current evidence window.`, "success");
      showToast(`${labels[elements.provider.value]} is within configured thresholds.`);
      return;
    }
    await loadIncidents(result.incident.incident_id);
    setActionStatus(`Analysis complete: ${result.incident.analysis.classification === "known" ? "catalog match" : "OpenAI assessment"}.`, "success");
    showToast(`Incident ${result.incident.occurrences > 1 ? "correlated" : "created"}.`);
  } catch (error) {
    setActionStatus(error.message, "error");
    showToast(error.message, "error");
  } finally {
    setLoading(false);
  }
}

async function toggleTraffic() {
  const requestedRps = Number(elements.trafficRps.value);
  if (!Number.isInteger(requestedRps) || requestedRps < 1 || requestedRps > 100) {
    setActionStatus("Choose a whole traffic rate from 1 to 100 RPS.", "error");
    elements.trafficRps.focus();
    return;
  }
  setLoading(true);
  try {
    const enabled = !state.control?.generator?.enabled;
    await request("/api/v1/demo/traffic", {
      method: "PATCH",
      body: JSON.stringify({ enabled, requests_per_second: requestedRps }),
    });
    await loadControl();
  } catch (error) {
    setActionStatus(error.message, "error");
    showToast(error.message, "error");
  } finally {
    setLoading(false);
  }
}

async function updateRunningTrafficRate() {
  if (!state.control?.generator?.enabled) return;
  const requestedRps = Number(elements.trafficRps.value);
  if (!Number.isInteger(requestedRps) || requestedRps < 1 || requestedRps > 100) {
    setActionStatus("Choose a whole traffic rate from 1 to 100 RPS.", "error");
    return;
  }
  setLoading(true);
  try {
    await request("/api/v1/demo/traffic", {
      method: "PATCH",
      body: JSON.stringify({ enabled: true, requests_per_second: requestedRps }),
    });
    await loadControl();
  } catch (error) {
    setActionStatus(error.message, "error");
  } finally {
    setLoading(false);
  }
}

async function runScenario(button) {
  const provider = button.dataset.scenarioProvider || elements.provider.value;
  elements.provider.value = provider;
  setLoading(true);
  setActionStatus(`Applying ${button.textContent.trim()}…`);
  try {
    await request("/api/v1/demo/scenarios", {
      method: "POST",
      body: JSON.stringify({ provider, scenario: button.dataset.scenario }),
    });
    await loadControl();
    setActionStatus(`${button.textContent.trim()} applied to ${labels[provider]}.`, "success");
  } catch (error) {
    setActionStatus(error.message, "error");
    showToast(error.message, "error");
  } finally {
    setLoading(false);
  }
}

async function selectIncident(incidentId) {
  state.selectedId = incidentId;
  renderList();
  renderDetailLoading();
  try {
    await loadSelectedAudit();
    renderDetail();
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function updateStatus(status) {
  if (!state.selectedId) return;
  setLoading(true);
  try {
    await request(`/api/v1/incidents/${encodeURIComponent(state.selectedId)}/status`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    });
    await loadIncidents(state.selectedId);
    showToast(`Incident marked ${labels[status].toLowerCase()}.`);
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setLoading(false);
  }
}

async function submitFeedback(verdict) {
  if (!state.selectedId) return;
  const note = document.querySelector("#feedback-note")?.value.trim() || null;
  try {
    await request(`/api/v1/incidents/${encodeURIComponent(state.selectedId)}/feedback`, {
      method: "POST",
      body: JSON.stringify({ verdict, note }),
    });
    showToast("Feedback recorded in the incident audit context.");
    const form = document.querySelector("#feedback-form");
    if (form) {
      form.innerHTML = `<p class="feedback-confirmation">Feedback saved. Thank you.</p>`;
    }
  } catch (error) {
    showToast(error.message, "error");
  }
}

function render() {
  renderList();
  renderDetail();
}

function renderList() {
  const count = state.incidents.length;
  elements.incidentCount.textContent = `${count} ${count === 1 ? "record" : "records"}`;
  if (!count) {
    elements.incidentList.innerHTML = `
      <div class="list-empty">
        <h3>No incidents yet</h3>
        <p>Run provider analysis to evaluate the current evidence window.</p>
      </div>`;
    return;
  }

  elements.incidentList.innerHTML = state.incidents
    .map(
      (incident) => `
        <button
          class="incident-item"
          type="button"
          data-incident-id="${escapeHtml(incident.incident_id)}"
          aria-current="${incident.incident_id === state.selectedId}"
        >
          <span class="item-meta">
            <span class="item-provider">${escapeHtml(labels[incident.provider] || incident.provider)}</span>
            <span class="severity-badge" data-severity="${escapeHtml(incident.severity)}">${escapeHtml(labels[incident.severity])}</span>
          </span>
          <h3>${escapeHtml(incident.analysis.headline)}</h3>
          <span class="classification-label" data-classification="${escapeHtml(incident.analysis.classification)}">${escapeHtml(labels[incident.analysis.classification])}</span>
          <span class="item-footer">
            <span>${escapeHtml(labels[incident.status])} · ${incident.occurrences}×</span>
            <time datetime="${escapeHtml(incident.last_seen_at)}">${escapeHtml(formatTime(incident.last_seen_at))}</time>
          </span>
        </button>`
    )
    .join("");
}

function renderDetailLoading() {
  elements.incidentDetail.innerHTML = `
    <div class="detail-empty">
      <span class="empty-symbol" aria-hidden="true">◌</span>
      <h2>Loading incident</h2>
      <p>Retrieving evidence and audit history.</p>
    </div>`;
}

function renderDetail() {
  const incident = state.incidents.find((item) => item.incident_id === state.selectedId);
  if (!incident) {
    elements.incidentDetail.innerHTML = `
      <div class="detail-empty">
        <span class="empty-symbol" aria-hidden="true">◎</span>
        <h2>No incident selected</h2>
        <p>Run AtlasPay or OrbitWallet analysis to create an evidence-backed incident.</p>
      </div>`;
    return;
  }

  const snapshot = incident.evidence.snapshot;
  const analysis = incident.analysis;
  elements.incidentDetail.innerHTML = `
    <header class="detail-header">
      <div class="detail-meta">
        <span class="severity-badge" data-severity="${escapeHtml(incident.severity)}">${escapeHtml(labels[incident.severity])}</span>
        <span class="status-badge">${escapeHtml(labels[incident.status])}</span>
        <span class="source-badge" data-classification="${escapeHtml(analysis.classification)}">${escapeHtml(labels[analysis.classification])} · ${escapeHtml(analysis.generated_by)}</span>
      </div>
      <h2>${escapeHtml(analysis.headline)}</h2>
      <div class="incident-id">${escapeHtml(incident.incident_id)} · ${escapeHtml(labels[incident.provider])} · ${incident.occurrences} occurrence${incident.occurrences === 1 ? "" : "s"}</div>
      <div class="status-actions" aria-label="Incident status actions">
        <button class="button button-secondary" type="button" data-status="acknowledged" ${incident.status === "acknowledged" ? "disabled" : ""}>Acknowledge</button>
        <button class="button button-danger" type="button" data-status="resolved" ${incident.status === "resolved" ? "disabled" : ""}>Resolve</button>
      </div>
    </header>

    <div class="detail-body">
      <section class="detail-section" aria-labelledby="evidence-title">
        <div class="section-heading">
          <h3 id="evidence-title">Measured evidence</h3>
          <p>${snapshot.available ? `${snapshot.window_seconds}s window` : "Metrics unavailable"} · ${escapeHtml(incident.evidence.source)}</p>
        </div>
        <dl class="metric-table">
          ${metric("p95 latency", `${Math.round(snapshot.p95_latency_ms)} ms`, snapshot.p95_latency_ms >= 800)}
          ${metric("Error rate", formatRatio(snapshot.error_rate), snapshot.error_rate >= 0.05)}
          ${metric("Timeout rate", formatRatio(snapshot.timeout_rate), snapshot.timeout_rate >= 0.03)}
          ${metric("Decline rate", formatRatio(Math.max(0, 1 - snapshot.success_rate - snapshot.error_rate - snapshot.timeout_rate)), (1 - snapshot.success_rate - snapshot.error_rate - snapshot.timeout_rate) >= 0.1)}
          ${metric("Success rate", formatRatio(snapshot.success_rate), false)}
          ${metric("Health", snapshot.health_up ? "Up" : "Down", !snapshot.health_up)}
        </dl>
        <ul class="signal-list" aria-label="Detected signals">
          ${incident.evidence.signals
            .map(
              (signal) => `<li class="signal" data-severity="${escapeHtml(signal.severity)}"><span class="signal-dot" aria-hidden="true"></span>${escapeHtml(signal.description)}</li>`
            )
            .join("")}
        </ul>
        ${renderProviderEvents(incident.evidence.provider_events)}
      </section>

      <section class="detail-section" aria-labelledby="analysis-title">
        <div class="section-heading">
          <h3 id="analysis-title">Investigation analysis</h3>
          <p>${Math.round(analysis.confidence * 100)}% confidence · ${escapeHtml(labels[analysis.classification])} · advisory only</p>
        </div>
        <p class="analysis-summary">${escapeHtml(analysis.summary)}</p>
        <p class="analysis-impact"><strong>Potential impact</strong>${escapeHtml(analysis.impact)}</p>
        <div class="analysis-columns">
          <div>
            <h4>Likely causes</h4>
            ${renderCauses(analysis)}
          </div>
          <div>
            <h4>Recommended operator checks</h4>
            <ol class="recommendation-list">
              ${analysis.recommended_actions
                .sort((a, b) => a.priority - b.priority)
                .map(
                  (action) => `<li><strong>${escapeHtml(action.title)}</strong><span>${escapeHtml(action.rationale)}</span></li>`
                )
                .join("")}
            </ol>
          </div>
        </div>
        ${analysis.runbook_url ? `<a class="runbook-link" href="${escapeHtml(analysis.runbook_url)}" target="_blank" rel="noreferrer">Open reviewed runbook <span aria-hidden="true">↗</span></a>` : ""}
      </section>

      <details class="detail-disclosure">
        <summary>Feedback and audit <span>${state.audit.length} audit event${state.audit.length === 1 ? "" : "s"}</span></summary>
        <div class="disclosure-content">
          <section aria-labelledby="feedback-title">
            <div class="section-heading"><h3 id="feedback-title">Analysis feedback</h3><p>Evaluation only; never changes production automatically</p></div>
            <form id="feedback-form" class="feedback-form">
              <label for="feedback-note">Optional operator note</label>
              <textarea id="feedback-note" maxlength="1000" rows="2" placeholder="What was useful or incorrect?"></textarea>
              <div class="feedback-actions" role="group" aria-label="Rate this analysis">
                <button class="button button-secondary" type="button" data-feedback="helpful">Helpful</button>
                <button class="button button-secondary" type="button" data-feedback="not-helpful">Not helpful</button>
                <button class="button button-secondary" type="button" data-feedback="incorrect">Incorrect</button>
              </div>
            </form>
          </section>
          <section aria-labelledby="audit-title">
            <div class="section-heading"><h3 id="audit-title">Audit trail</h3></div>
            <ol class="audit-list">${state.audit.map((event) => `<li><time datetime="${escapeHtml(event.occurred_at)}">${escapeHtml(formatTime(event.occurred_at))}</time><span><span class="audit-event">${escapeHtml(event.event_type.replaceAll("-", " "))}</span><span class="audit-detail">${escapeHtml(Object.entries(event.details).map(([key, value]) => `${key}: ${value}`).join(" · "))}</span></span></li>`).join("")}</ol>
          </section>
        </div>
      </details>
    </div>`;
}

function renderCauses(analysis) {
  const structuredCauses = analysis.causes || analysis.cause_hypotheses;
  const causes = Array.isArray(structuredCauses) && structuredCauses.length
    ? structuredCauses
    : (analysis.probable_causes || []).map((title) => ({ category: "technical", title, why: "Recorded analysis did not include structured cause evidence.", evidence_refs: [] }));
  const provenance = analysis.generated_by === "catalog" ? "Catalog" : analysis.generated_by === "openai" ? "OpenAI" : "Manual review";
  return `<ul class="cause-list">${causes.map((cause) => `
    <li class="cause-card" data-category="${escapeHtml(cause.category)}">
      <div class="cause-meta"><span>${escapeHtml(cause.category || "technical")}</span><span>${provenance}</span></div>
      <strong>${escapeHtml(cause.title)}</strong>
      <p><b>Why</b> ${escapeHtml(cause.why || "No explanation recorded.")}</p>
      <div class="evidence-refs">${(cause.evidence_refs || []).length ? cause.evidence_refs.map(evidenceLabel).join("") : "<span>Evidence source unavailable</span>"}</div>
    </li>`).join("")}</ul>`;
}

function evidenceLabel(ref) {
  const [kind, ...parts] = String(ref).split(":");
  const label = parts.join(":") || kind;
  const kindLabel = { metric: "Metric", event: "Event", alert: "Alert" }[kind] || "Evidence";
  return `<span title="${escapeHtml(ref)}">${escapeHtml(kindLabel)}: ${escapeHtml(label.replaceAll("_", " "))}</span>`;
}

function renderProviderEvents(events = []) {
  if (!events.length) {
    return `<p class="event-empty">No normalized provider responses were available for this evidence window.</p>`;
  }
  return `
    <div class="provider-events">
      <h4>Recent provider responses</h4>
      <div class="event-table-wrap">
        <table>
          <thead><tr><th>Code</th><th>Outcome</th><th>HTTP</th><th>Latency</th><th>Method</th></tr></thead>
          <tbody>${events.slice(0, 8).map((event) => `
            <tr>
              <td><code>${escapeHtml(event.response_code)}</code></td>
              <td>${escapeHtml(event.outcome)}</td>
              <td>${escapeHtml(event.http_status ?? "—")}</td>
              <td>${escapeHtml(event.processing_time_ms)} ms</td>
              <td>${escapeHtml(event.payment_method || "—")}</td>
            </tr>`).join("")}</tbody>
        </table>
      </div>
    </div>`;
}

function metric(label, value, bad) {
  return `<div class="metric"><dt>${escapeHtml(label)}</dt><dd data-state="${bad ? "bad" : "normal"}">${escapeHtml(value)}</dd></div>`;
}

elements.form.addEventListener("submit", analyzeProvider);
elements.trafficButton.addEventListener("click", toggleTraffic);
elements.trafficRps.addEventListener("change", updateRunningTrafficRate);
elements.scenarioControls.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-scenario]");
  if (button) runScenario(button);
});
elements.refreshButton.addEventListener("click", async () => {
  setLoading(true);
  try {
    await loadIncidents();
    showToast("Incident feed refreshed.");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setLoading(false);
  }
});

elements.incidentList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-incident-id]");
  if (button) selectIncident(button.dataset.incidentId);
});

elements.incidentDetail.addEventListener("click", (event) => {
  const button = event.target.closest("[data-status]");
  if (button) updateStatus(button.dataset.status);
  const feedback = event.target.closest("[data-feedback]");
  if (feedback) submitFeedback(feedback.dataset.feedback);
});

elements.filterForm.addEventListener("change", async () => {
  try {
    await loadIncidents(null);
    connectIncidentStream();
  } catch (error) {
    showToast(error.message, "error");
  }
});

Promise.all([loadRuntime(), loadControl(), loadIncidents()]).then(connectIncidentStream).catch((error) => {
  showToast(error.message, "error");
  elements.incidentDetail.innerHTML = `
    <div class="detail-error">
      <h2>Unable to load incident intelligence</h2>
      <p>${escapeHtml(error.message)}</p>
    </div>`;
});
