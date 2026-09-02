FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY docker/incident-api-entrypoint.sh /usr/local/bin/incident-api-entrypoint

RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data \
    && chown appuser:appuser /data
USER appuser

EXPOSE 8002

ENTRYPOINT ["/bin/sh", "/usr/local/bin/incident-api-entrypoint"]
