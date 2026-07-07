"""Govern the graph: mount it via Theodosia with a state persister and the audit trail.

The validators are the external verification, and they check process, not content:
a verdict must cite evidence that was actually gathered and dispatched (conclude,
refute), and recall must name an id that exists. They never inspect a fact value or
judge whether the evidence supports the verdict; that judgment is the model's, and
adjudicating it here would rebuild the answer key.
"""

from __future__ import annotations

from typing import Any

import theodosia
from burr.core.persistence import SQLitePersister
from theodosia import ValidationFailed

from .graph import build_application
from .surfaces import REPO, Surface, cluster_namespaces
from .tools import build_upstream

HOME = REPO / ".semley"
UPSTREAM = "ansible"


def _cited(inputs: dict[str, Any]) -> list[str]:
    c = inputs.get("cited_evidence") or []
    return [c] if isinstance(c, str) else list(c)


def _v_read(state: dict[str, Any], inputs: dict[str, Any]) -> None:
    """A read may only call a module in the surface's set. This is the action-space
    boundary: the model picks the module and args, but not the set it picks from."""
    module = inputs.get("module")
    allowed = state.get("modules", [])
    if module not in allowed:
        raise ValidationFailed(
            f"module {module!r} is not on this surface; choose from {allowed}"
        )


def _v_verdict(state: dict[str, Any], inputs: dict[str, Any]) -> None:
    """A conclusion must cite a read that actually dispatched."""
    index = {e["id"]: e for e in state.get("evidence", [])}
    cited = _cited(inputs)
    if not cited:
        raise ValidationFailed(
            "cite the evidence ids you relied on (cited_evidence); none were given"
        )
    unknown = [c for c in cited if c not in index]
    if unknown:
        raise ValidationFailed(f"cited evidence {unknown} was never gathered")
    if not any(index[c]["dispatched"] for c in cited):
        raise ValidationFailed(
            "a verdict must cite a read that actually dispatched to the target"
        )


def _v_recall(state: dict[str, Any], inputs: dict[str, Any]) -> None:
    eid = inputs.get("evidence_id")
    if not any(e["id"] == eid for e in state.get("evidence", [])):
        raise ValidationFailed(f"unknown evidence id {eid!r}")


VALIDATORS = {"read": _v_read, "conclude": _v_verdict, "recall": _v_recall}


def mount_surface(surface: Surface):
    """Return (mcp_server, upstream_server, persister) for a surface."""
    HOME.mkdir(exist_ok=True)
    upstream = build_upstream(surface)
    persister = SQLitePersister.from_values(
        str(HOME / "memory.db"), connect_kwargs={"check_same_thread": False}
    )
    persister.initialize()
    trail = theodosia.tracker(surface.name, str(HOME / "trail"))
    namespaces = cluster_namespaces() if surface.plane == "control" else []

    def factory():
        return (
            build_application(surface.plane, surface.modules, namespaces)
            .with_tracker(trail)
            .with_state_persister(persister)
            .with_identifiers(partition_key=surface.name)
        )

    server = theodosia.mount(
        factory,
        name="semley",
        upstream={UPSTREAM: upstream},
        input_validators=VALIDATORS,
    )
    return server, upstream, persister


def _as_dict(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if record is None:
        return None
    state = record["state"]
    return dict(state.get_all()) if hasattr(state, "get_all") else dict(state)


def load_prior_state(
    persister: SQLitePersister, partition_key: str
) -> dict[str, Any] | None:
    """Final state of the most recent session (latest sequence of the newest app_id)."""
    ids = persister.list_app_ids(partition_key)
    if not ids:
        return None
    return _as_dict(persister.load(partition_key, ids[0]))


def load_incident_history(
    persister: SQLitePersister, partition_key: str
) -> list[dict[str, Any]]:
    """Final state of every concluded session in the partition, oldest first.

    This is the memory of record the cross-incident digest is rendered from.
    """
    states = [
        _as_dict(persister.load(partition_key, aid))
        for aid in reversed(persister.list_app_ids(partition_key))
    ]
    return [s for s in states if s and s.get("phase") == "concluded"]
