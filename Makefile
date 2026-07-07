.PHONY: install check ci host-up inject heal demo-host demo-localhost \
        cluster-up cluster-down demo-cluster \
        telemetry-up telemetry-down demo-telemetry clean

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

# --- Observability plane: Prometheus scrape health, read over GET-only uri ---

telemetry-up:
	scripts/telemetry-up.sh

telemetry-down:
	scripts/telemetry-down.sh

demo-telemetry: telemetry-up
	uv run semley --surface telemetry

clean:
	rm -rf .semley .rocannon/playbooks .rocannon/runs
