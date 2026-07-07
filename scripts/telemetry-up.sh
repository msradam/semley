#!/usr/bin/env bash
# Bring up the observability demo target: Prometheus in the kind cluster, scraping
# itself (up) and a 'checkout' job whose target is down (up == 0), plus a port-forward
# so the local uri read can query it at localhost:9090. Idempotent and loud.
set -euo pipefail

CLUSTER="${SEMLEY_KIND_CLUSTER:-semley}"
CTX="kind-${CLUSTER}"
PF_PID="/tmp/semley-prom-portforward.pid"

step() { printf '\n\033[1;36m▸ %s\033[0m\n' "$1"; }
ok()   { printf '  \033[1;32m✓\033[0m %s\n' "$1"; }
info() { printf '    \033[2m%s\033[0m\n' "$1"; }

printf '\033[1m━━━ Semley telemetry target: Prometheus on kind/%s ━━━\033[0m\n' "$CLUSTER"

step "kind cluster"
if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  info "creating cluster '${CLUSTER}'..."
  kind create cluster --name "$CLUSTER" >/dev/null
fi
kubectl config use-context "$CTX" >/dev/null
ok "cluster '${CLUSTER}' ready"

step "Prometheus deployment"
kubectl apply -f - >/dev/null <<'YAML'
apiVersion: v1
kind: Namespace
metadata: {name: monitoring}
---
apiVersion: v1
kind: ConfigMap
metadata: {name: prometheus-config, namespace: monitoring}
data:
  prometheus.yml: |
    global: {scrape_interval: 5s}
    scrape_configs:
      - job_name: prometheus
        static_configs: [{targets: ['localhost:9090']}]
      - job_name: checkout
        static_configs: [{targets: ['checkout.shop.svc.cluster.local:8080']}]
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: prometheus, namespace: monitoring}
spec:
  replicas: 1
  selector: {matchLabels: {app: prometheus}}
  template:
    metadata: {labels: {app: prometheus}}
    spec:
      containers:
        - name: prometheus
          image: prom/prometheus:v2.53.1
          args: ['--config.file=/etc/prometheus/prometheus.yml']
          ports: [{containerPort: 9090}]
          volumeMounts: [{name: config, mountPath: /etc/prometheus}]
      volumes:
        - name: config
          configMap: {name: prometheus-config}
---
apiVersion: v1
kind: Service
metadata: {name: prometheus, namespace: monitoring}
spec:
  selector: {app: prometheus}
  ports: [{port: 9090, targetPort: 9090}]
YAML
info "waiting for Prometheus to become ready..."
kubectl -n monitoring rollout status deploy/prometheus --timeout=120s >/dev/null
ok "Prometheus running"

step "port-forward localhost:9090"
if [ -f "$PF_PID" ] && kill -0 "$(cat "$PF_PID")" 2>/dev/null; then
  ok "port-forward already up (pid $(cat "$PF_PID"))"
else
  kubectl -n monitoring port-forward svc/prometheus 9090:9090 >/dev/null 2>&1 &
  echo $! > "$PF_PID"
  sleep 2
  ok "port-forward up (pid $(cat "$PF_PID"))"
fi
until curl -sf localhost:9090/-/ready >/dev/null 2>&1; do sleep 1; done
reason=$(curl -sf 'localhost:9090/api/v1/query?query=up{job="checkout"}' | grep -o '"value":\[[^]]*\]' | tail -1)
ok "Prometheus answering; checkout scrape ${reason:-pending} (0 == down)"

printf '\n\033[1;32m✔ telemetry target ready.\033[0m\n'
printf '  drive:  \033[1muv run semley --surface telemetry\033[0m\n'
printf '          then describe: "a monitored target is down, check the metrics"\n'
printf '  down:   \033[1mmake telemetry-down\033[0m\n\n'
