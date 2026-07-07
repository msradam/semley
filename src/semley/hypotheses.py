"""The pre-enumerated hypothesis catalog: a diagnostic menu, not an answer key.

Each hypothesis names a fault class and the reads that probe it. It carries no
evaluator: the model reads the raw facts those reads return and decides confirm,
refute, or inconclusive itself. No service, host, or fault name is written here,
and no fact value is ever adjudicated in code.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Read:
    """One reflected Ansible read: a module FQCN plus static args.

    The target host is injected at dispatch from investigation state, never here.
    """

    module: str
    args: dict[str, Any]

    @property
    def tool(self) -> str:
        return self.module.replace(".", "_")


@dataclass(frozen=True)
class Hypothesis:
    name: str
    plane: str
    description: str
    reads: Callable[[str, str], list[Read]]  # (target, scope) -> reads


def _node_reads_service(target: str, scope: str) -> list[Read]:
    return [
        Read("ansible.builtin.service_facts", {}),
        Read("ansible.builtin.listen_ports_facts", {}),
    ]


def _node_reads_setup(target: str, scope: str) -> list[Read]:
    return [Read("ansible.builtin.setup", {})]


def _control_reads_pods(target: str, scope: str) -> list[Read]:
    return [Read("kubernetes.core.k8s_info", {"kind": "Pod", "namespace": scope})]


PROMETHEUS_URL = "http://localhost:9090"


def _telemetry_reads(target: str, scope: str) -> list[Read]:
    # Ansible has no read-only module that queries Prometheus, so this uses uri, a
    # general HTTP module. The action phase enforces GET/QUERY to keep it a read.
    return [
        Read(
            "ansible.builtin.uri",
            {
                "url": f"{PROMETHEUS_URL}/api/v1/query?query=up",
                "method": "GET",
                "return_content": True,
            },
        )
    ]


SERVICE_DOWN = Hypothesis(
    name="service_down",
    plane="node",
    description="a service that should be running has stopped",
    reads=_node_reads_service,
)

RESOURCE_EXHAUSTION = Hypothesis(
    name="resource_exhaustion",
    plane="node",
    description="the host is out of disk or memory headroom",
    reads=_node_reads_setup,
)

WORKLOAD_UNHEALTHY = Hypothesis(
    name="workload_unhealthy",
    plane="control",
    description="a workload will not become ready (image pull or crash loop)",
    reads=_control_reads_pods,
)

TARGET_DOWN = Hypothesis(
    name="target_down",
    plane="observability",
    description="a monitored target is failing its scrape (Prometheus up == 0)",
    reads=_telemetry_reads,
)

CATALOG: dict[str, Hypothesis] = {
    h.name: h
    for h in (SERVICE_DOWN, RESOURCE_EXHAUSTION, WORKLOAD_UNHEALTHY, TARGET_DOWN)
}
