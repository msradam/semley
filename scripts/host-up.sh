#!/usr/bin/env bash
# Bring up (or verify) the host demo target: an OrbStack Ubuntu machine named web1
# running nginx. Idempotent and loud, so it is safe to run before every demo.
set -euo pipefail

MACHINE="${SEMLEY_MACHINE:-web1}"
SSH="${MACHINE}@orb"

step() { printf '\n\033[1;36m▸ %s\033[0m\n' "$1"; }
ok()   { printf '  \033[1;32m✓\033[0m %s\n' "$1"; }
info() { printf '    \033[2m%s\033[0m\n' "$1"; }

printf '\033[1m━━━ Semley host target: %s ━━━\033[0m\n' "$MACHINE"

step "OrbStack machine"
if orb list 2>/dev/null | awk '{print $1}' | grep -qx "$MACHINE"; then
  state=$(orb list | awk -v m="$MACHINE" '$1==m {print $2}')
  if [ "$state" != "running" ]; then
    info "state=${state}; starting..."
    orb start "$MACHINE"
  fi
  ok "machine '${MACHINE}' is running"
else
  info "machine '${MACHINE}' not found; creating (ubuntu)..."
  orb create ubuntu "$MACHINE"
  ok "machine '${MACHINE}' created"
fi

step "SSH reachability"
ssh -o ConnectTimeout=6 -o BatchMode=yes "$SSH" true
ok "ssh ${SSH} reachable"

step "nginx installed and enabled"
if ! ssh "$SSH" "command -v nginx >/dev/null 2>&1"; then
  info "nginx not present; installing..."
  ssh "$SSH" "sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nginx"
fi
ssh "$SSH" "sudo systemctl enable --now nginx >/dev/null 2>&1 || true"
ver=$(ssh "$SSH" "nginx -v 2>&1")
enabled=$(ssh "$SSH" "systemctl is-enabled nginx")
ok "nginx ready (${ver}, ${enabled})"

printf '\n\033[1;32m✔ host target ready.\033[0m\n'
printf '  next:  \033[1mmake inject\033[0m           stop nginx (reads as enabled-but-stopped)\n'
printf '         \033[1muv run semley --surface host\033[0m\n\n'
