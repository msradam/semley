#!/usr/bin/env bash
# Tear down the kind cluster demo target.
set -euo pipefail
CLUSTER="${SEMLEY_KIND_CLUSTER:-semley}"
kind delete cluster --name "$CLUSTER"
echo "cluster '${CLUSTER}' deleted"
