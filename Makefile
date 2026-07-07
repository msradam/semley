.PHONY: install check host-up inject heal demo-host demo-localhost \
        cluster-up cluster-down demo-cluster clean

install:
	uv sync

ci:
	scripts/ci.sh

check:
	uv run pytest tests/ -q

# --- Node plane: host service/health on a managed systemd target (web1) ---

host-up:
	scripts/host-up.sh

inject:
	scripts/inject-fault.sh

heal:
	scripts/heal.sh

# Bring the target and fault up, investigate, restore the healthy baseline.
demo-host: host-up inject
	uv run semley --surface host
	$(MAKE) heal

# The control host itself as a legitimate local target.
demo-localhost:
	uv run semley --surface localhost

# --- Control plane: workload health in a kind cluster namespace ---

cluster-up:
	scripts/cluster-up.sh

cluster-down:
	scripts/cluster-down.sh

# Bring the cluster and faulted workload up, then investigate.
demo-cluster: cluster-up
	uv run semley --surface cluster

clean:
	rm -rf .semley .rocannon/playbooks .rocannon/runs
