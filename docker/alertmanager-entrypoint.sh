#!/bin/sh
set -eu

port="${PORT:-9093}"
config=/etc/alertmanager/alertmanager.yml

if [ "${APM_DEPLOYMENT_TARGET:-compose}" = "railway" ]; then
  : "${APM_INCIDENT_ALERTMANAGER_TOKEN:?APM_INCIDENT_ALERTMANAGER_TOKEN must be configured}"
  awk -v token="$APM_INCIDENT_ALERTMANAGER_TOKEN" '
    function replace(text, needle, value, position) {
      position = index(text, needle)
      return position ? substr(text, 1, position - 1) value substr(text, position + length(needle)) : text
    }
    { print replace($0, "__APM_INCIDENT_ALERTMANAGER_TOKEN__", token) }
  ' /etc/alertmanager/railway.alertmanager.yml.tmpl > /tmp/alertmanager.yml
  config=/tmp/alertmanager.yml
fi

exec /bin/alertmanager \
  "--config.file=$config" \
  "--storage.path=/alertmanager" \
  "--web.listen-address=:$port"
