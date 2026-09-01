#!/bin/sh
set -eu

# Railway provides PORT at runtime; Compose keeps Grafana's documented default.
export GF_SERVER_HTTP_PORT="${PORT:-3000}"

# A tester account is optional for local Compose, but the Railway manifest
# requires it. Grafana creates it as the default Viewer role after startup.
if [ -n "${GRAFANA_TESTER_USER:-}" ] || [ -n "${GRAFANA_TESTER_PASSWORD:-}" ]; then
  : "${GRAFANA_TESTER_USER:?GRAFANA_TESTER_USER must be configured}"
  : "${GRAFANA_TESTER_PASSWORD:?GRAFANA_TESTER_PASSWORD must be configured}"
  : "${GF_SECURITY_ADMIN_USER:?GF_SECURITY_ADMIN_USER must be configured}"
  : "${GF_SECURITY_ADMIN_PASSWORD:?GF_SECURITY_ADMIN_PASSWORD must be configured}"

  /run.sh &
  grafana_pid=$!
  trap 'kill "$grafana_pid" 2>/dev/null || true' EXIT INT TERM

  attempts=0
  until curl -fsS http://127.0.0.1:"$GF_SERVER_HTTP_PORT"/api/health >/dev/null; do
    attempts=$((attempts + 1))
    if [ "$attempts" -ge 30 ]; then
      echo "Grafana did not become ready to provision the tester account" >&2
      exit 1
    fi
    sleep 1
  done

  # Grafana returns 412 when this idempotent create sees an existing login.
  status_code=$(curl -sS -o /dev/null -w '%{http_code}' \
    -u "$GF_SECURITY_ADMIN_USER:$GF_SECURITY_ADMIN_PASSWORD" \
    -H 'Content-Type: application/json' \
    -X POST http://127.0.0.1:"$GF_SERVER_HTTP_PORT"/api/admin/users \
    --data "{\"name\":\"$GRAFANA_TESTER_USER\",\"email\":\"$GRAFANA_TESTER_USER@example.invalid\",\"login\":\"$GRAFANA_TESTER_USER\",\"password\":\"$GRAFANA_TESTER_PASSWORD\"}")
  case "$status_code" in
    200|201|412) ;;
    *)
      echo "Grafana tester account provisioning failed" >&2
      exit 1
      ;;
  esac

  wait "$grafana_pid"
  exit $?
fi

exec /run.sh
