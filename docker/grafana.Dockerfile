FROM grafana/grafana:12.1.0

ENV GF_AUTH_ANONYMOUS_ENABLED=false \
    GF_USERS_ALLOW_SIGN_UP=false \
    GF_USERS_AUTO_ASSIGN_ORG_ROLE=Viewer

COPY infra/grafana/provisioning /etc/grafana/provisioning
COPY infra/grafana/dashboards /etc/grafana/dashboards
COPY docker/grafana-entrypoint.sh /usr/local/bin/grafana-entrypoint

ENTRYPOINT ["/bin/sh", "/usr/local/bin/grafana-entrypoint"]
