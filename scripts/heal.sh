#!/usr/bin/env bash
# Restore the healthy baseline: start nginx on web1.
set -euo pipefail

HOST="${SEMLEY_TARGET_SSH:-web1@orb}"
SERVICE="${SEMLEY_SERVICE:-nginx}"

ssh "$HOST" "sudo systemctl start ${SERVICE}"

echo "Healed ${HOST}: ${SERVICE} is $(ssh "$HOST" "systemctl is-active ${SERVICE}"), enabled=$(ssh "$HOST" "systemctl is-enabled ${SERVICE}")"
