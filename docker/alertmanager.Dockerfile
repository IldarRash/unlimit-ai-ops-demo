FROM prom/alertmanager:v0.28.1

COPY infra/alertmanager/alertmanager.yml /etc/alertmanager/alertmanager.yml
COPY infra/alertmanager/railway.alertmanager.yml.tmpl /etc/alertmanager/railway.alertmanager.yml.tmpl
COPY docker/alertmanager-entrypoint.sh /usr/local/bin/alertmanager-entrypoint

ENTRYPOINT ["/bin/sh", "/usr/local/bin/alertmanager-entrypoint"]
