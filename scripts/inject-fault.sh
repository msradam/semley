#!/usr/bin/env bash
# Inject the demo fault: stop nginx on web1 but leave it enabled,
# so it reads as enabled-but-stopped for the investigation.
set -euo pipefail

HOST="${SEMLEY_TARGET_SSH:-web1@orb}"
SERVICE="${SEMLEY_SERVICE:-nginx}"

ssh "$HOST" "sudo systemctl stop ${SERVICE}"

echo "Injected fault on ${HOST}: ${SERVICE} is $(ssh "$HOST" "systemctl is-active ${SERVICE}" || true), enabled=$(ssh "$HOST" "systemctl is-enabled ${SERVICE}")"
