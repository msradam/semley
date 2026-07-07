"""Surfaces: per-plane bindings of an inventory, a curated module set, and hypotheses.

The module sets are disjoint across planes, so a session bound to one surface
structurally cannot call another plane's modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INVENTORY_DIR = REPO / "inventory"

NODE_MODULES = [
    "ansible.builtin.service_facts",
    "ansible.builtin.listen_ports_facts",
    "ansible.builtin.setup",
]
CONTROL_MODULES = ["kubernetes.core.k8s_info"]


@dataclass(frozen=True)
class Surface:
    name: str
    plane: str
    hypotheses: list[str]
    modules: list[str]
    inventory: Path
    invariant: str
    scopes: list[str] = field(default_factory=list)

    def targets(self) -> list[tuple[str, str]]:
        """(name, detail) rows for the banner: hosts for node, namespaces for control."""
        if self.plane == "control":
            return [(ns, "namespace") for ns in self.scopes] or [("(namespace set at entry)", "")]
        return _parse_ini_hosts(self.inventory)


def _parse_ini_hosts(path: Path) -> list[tuple[str, str]]:
    if not path.exists():
        return []
    rows: list[tuple[str, str]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith(("[", "#", ";")):
            continue
        name, _, rest = line.partition(" ")
        detail = next((tok.split("=", 1)[1] for tok in rest.split()
                       if tok.startswith("ansible_host=")), "")
        rows.append((name, f"ansible_host={detail}" if detail else "via ssh"))
    return rows


HOST = Surface(
    name="host",
    plane="node",
    hypotheses=["service_down", "resource_exhaustion"],
    modules=NODE_MODULES,
    inventory=INVENTORY_DIR / "hosts.ini",
    invariant="read-only: only read-annotated modules are reachable; the model supplies no module.",
)

LOCALHOST = Surface(
    name="localhost",
    plane="node",
    hypotheses=["service_down", "resource_exhaustion"],
    modules=NODE_MODULES,
    inventory=INVENTORY_DIR / "localhost.ini",
    invariant="read-only: only read-annotated modules are reachable; the model supplies no module.",
)

CLUSTER = Surface(
    name="cluster",
    plane="control",
    hypotheses=["workload_unhealthy"],
    modules=CONTROL_MODULES,
    inventory=INVENTORY_DIR / "localhost.ini",
    invariant="read-only: k8s_info is a facts read; execution is local, scoped by namespace.",
)

SURFACES = {s.name: s for s in (HOST, LOCALHOST, CLUSTER)}
