#!/usr/bin/env bash
# Bring up the control-plane demo target: a kind cluster with a workload that
# cannot pull its image (ImagePullBackOff) in the `shop` namespace, plus the
# kubernetes.core collection and client the k8s_info read needs. Idempotent and loud.
set -euo pipefail

CLUSTER="${SEMLEY_KIND_CLUSTER:-semley}"
NS="${SEMLEY_NAMESPACE:-shop}"
CTX="kind-${CLUSTER}"

step() { printf '\n\033[1;36m▸ %s\033[0m\n' "$1"; }
ok()   { printf '  \033[1;32m✓\033[0m %s\n' "$1"; }
info() { printf '    \033[2m%s\033[0m\n' "$1"; }

printf '\033[1m━━━ Semley cluster target: kind/%s ━━━\033[0m\n' "$CLUSTER"

step "kind cluster"
if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  ok "cluster '${CLUSTER}' already exists"
else
  info "creating cluster '${CLUSTER}' (this takes ~30s)..."
  kind create cluster --name "$CLUSTER" >/dev/null
  ok "cluster '${CLUSTER}' created"
fi
kubectl config use-context "$CTX" >/dev/null
ok "kubectl context set to ${CTX}"

step "kubernetes.core collection"
if ! uv run ansible-galaxy collection list 2>/dev/null | grep -qi "kubernetes.core"; then
  info "installing kubernetes.core collection..."
  uv run ansible-galaxy collection install kubernetes.core >/dev/null
fi
ok "k8s_info read path ready (the kubernetes client comes from uv sync)"

step "faulted workload in namespace '${NS}'"
kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n "$NS" apply -f - >/dev/null <<'YAML'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: checkout
spec:
  replicas: 1
  selector:
    matchLabels: {app: checkout}
  template:
    metadata:
      labels: {app: checkout}
    spec:
      containers:
        - name: checkout
          image: registry.local/checkout:v9-does-not-exist
YAML
info "waiting for the pod to reach ImagePullBackOff..."
for _ in $(seq 1 30); do
  reason=$(kubectl -n "$NS" get pods -l app=checkout \
    -o jsonpath='{.items[0].status.containerStatuses[0].state.waiting.reason}' 2>/dev/null || true)
  case "$reason" in ImagePullBackOff|ErrImagePull) break ;; esac
  sleep 2
done
ok "workload 'checkout' is unhealthy (${reason:-pending}) in namespace '${NS}'"

printf '\n\033[1;32m✔ cluster target ready.\033[0m\n'
printf '  drive:  \033[1muv run semley --surface cluster\033[0m\n'
printf '          then describe: "the checkout workload in the %s namespace will not start"\n' "$NS"
printf '  down:   \033[1mmake cluster-down\033[0m\n\n'
