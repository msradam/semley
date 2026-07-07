#!/usr/bin/env bash
# Stop the Prometheus port-forward and remove the monitoring namespace.
set -euo pipefail
PF_PID="/tmp/semley-prom-portforward.pid"
[ -f "$PF_PID" ] && kill "$(cat "$PF_PID")" 2>/dev/null && rm -f "$PF_PID" && echo "port-forward stopped" || true
kubectl delete namespace monitoring --ignore-not-found >/dev/null 2>&1 && echo "monitoring namespace removed" || true
