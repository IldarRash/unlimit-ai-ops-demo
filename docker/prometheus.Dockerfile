FROM prom/prometheus:v3.5.0

COPY infra/prometheus/prometheus.yml /etc/prometheus/prometheus.yml
COPY infra/prometheus/alert.rules.yml /etc/prometheus/alert.rules.yml
COPY infra/prometheus/railway.prometheus.yml.tmpl /etc/prometheus/railway.prometheus.yml.tmpl
COPY infra/prometheus/web.yml.tmpl /etc/prometheus/web.yml.tmpl
COPY docker/prometheus-entrypoint.sh /usr/local/bin/prometheus-entrypoint

ENTRYPOINT ["/bin/sh", "/usr/local/bin/prometheus-entrypoint"]
