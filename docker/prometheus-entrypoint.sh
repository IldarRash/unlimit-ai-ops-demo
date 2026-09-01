#!/bin/sh
set -eu

port="${PORT:-9090}"
config=/etc/prometheus/prometheus.yml
web_config=

: "${APM_INCIDENT_METRICS_TOKEN:?APM_INCIDENT_METRICS_TOKEN must be configured}"

if [ "${APM_DEPLOYMENT_TARGET:-compose}" = "railway" ]; then
  : "${PROMETHEUS_WEB_USERNAME:?PROMETHEUS_WEB_USERNAME must be configured}"
  : "${PROMETHEUS_WEB_PASSWORD_HASH:?PROMETHEUS_WEB_PASSWORD_HASH must be configured}"
  config=/etc/prometheus/railway.prometheus.yml.tmpl

  awk -v username="$PROMETHEUS_WEB_USERNAME" \
      -v password_hash="$PROMETHEUS_WEB_PASSWORD_HASH" '
    function replace(text, needle, value, position) {
      position = index(text, needle)
      return position ? substr(text, 1, position - 1) value substr(text, position + length(needle)) : text
    }
    {
      line = replace($0, "__PROMETHEUS_WEB_USERNAME__", username)
      print replace(line, "__PROMETHEUS_WEB_PASSWORD_HASH__", password_hash)
    }
  ' /etc/prometheus/web.yml.tmpl > /tmp/web.yml
  web_config="--web.config.file=/tmp/web.yml"
fi

awk -v metrics_token="$APM_INCIDENT_METRICS_TOKEN" '
  function replace(text, needle, value, position) {
    position = index(text, needle)
    return position ? substr(text, 1, position - 1) value substr(text, position + length(needle)) : text
  }
  { print replace($0, "__APM_INCIDENT_METRICS_TOKEN__", metrics_token) }
' "$config" > /tmp/prometheus.yml

exec /bin/prometheus \
  "--config.file=/tmp/prometheus.yml" \
  "--storage.tsdb.path=/prometheus" \
  "--storage.tsdb.retention.time=6h" \
  "--web.listen-address=:$port" \
  "--web.enable-lifecycle" \
  $web_config
