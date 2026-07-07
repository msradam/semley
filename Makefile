.PHONY: install check demo-host demo-localhost inject heal clean

install:
	uv sync

check:
	uv run pytest tests/ -q

# Primary surface: host service/health investigation on a managed systemd target.
# Bring the fault up, run the investigation, restore the healthy baseline.
demo-host: inject
	uv run semley --surface host
	$(MAKE) heal

demo-localhost:
	uv run semley --surface localhost

inject:
	scripts/inject-fault.sh

heal:
	scripts/heal.sh

clean:
	rm -rf .semley .rocannon/playbooks .rocannon/runs
