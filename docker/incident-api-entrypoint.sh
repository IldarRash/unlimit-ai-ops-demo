#!/bin/sh
set -eu

if [ -z "${DATABASE_URL:-}" ] && [ -n "${DATABASE_PASSWORD_FILE:-}" ]; then
  : "${DATABASE_HOST:?DATABASE_HOST must be configured}"
  : "${DATABASE_PORT:?DATABASE_PORT must be configured}"
  : "${DATABASE_NAME:?DATABASE_NAME must be configured}"
  : "${DATABASE_USER:?DATABASE_USER must be configured}"

  database_password=$(tr -d '\r\n' < "$DATABASE_PASSWORD_FILE")
  if [ -z "$database_password" ]; then
    echo "Database password file is empty" >&2
    exit 1
  fi
  encoded_password=$(printf '%s' "$database_password" | python -c 'import sys; from urllib.parse import quote; print(quote(sys.stdin.read(), safe=""))')
  export DATABASE_URL="postgresql://${DATABASE_USER}:${encoded_password}@${DATABASE_HOST}:${DATABASE_PORT}/${DATABASE_NAME}"
  unset database_password encoded_password
fi

exec python -m apm_demo.incidents.api
