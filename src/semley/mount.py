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
from .surfaces import REPO, Surface
from .tools import build_upstream

HOME = REPO / ".semley"
UPSTREAM = "ansible"


def _cited(inputs: dict[str, Any]) -> list[str]:
    c = inputs.get("cited_evidence") or []
    return [c] if isinstance(c, str) else list(c)


def _check_citations(
    state: dict[str, Any], inputs: dict[str, Any], *, want_dispatched: bool
) -> None:
    index = {e["id"]: e for e in state.get("evidence", [])}
    cited = _cited(inputs)
    if not cited:
        raise ValidationFailed(
            "cite the evidence ids you relied on (cited_evidence); none were given"
        )
    unknown = [c for c in cited if c not in index]
    if unknown:
        raise ValidationFailed(f"cited evidence {unknown} was never gathered")
    if not any(index[c]["dispatched"] == want_dispatched for c in cited):
        raise ValidationFailed(
            "a verdict must cite a read that actually dispatched to the target"
            if want_dispatched
            else "cite a read that did not dispatch"
        )


def _v_conclude(state: dict[str, Any], inputs: dict[str, Any]) -> None:
    _check_citations(state, inputs, want_dispatched=True)


def _v_refute(state: dict[str, Any], inputs: dict[str, Any]) -> None:
    _check_citations(state, inputs, want_dispatched=True)


def _v_recall(state: dict[str, Any], inputs: dict[str, Any]) -> None:
    eid = inputs.get("evidence_id")
    if not any(e["id"] == eid for e in state.get("evidence", [])):
        raise ValidationFailed(f"unknown evidence id {eid!r}")


VALIDATORS = {"conclude": _v_conclude, "refute": _v_refute, "recall": _v_recall}


def mount_surface(surface: Surface):
    """Return (mcp_server, upstream_server, persister) for a surface."""
    HOME.mkdir(exist_ok=True)
    upstream = build_upstream(surface)
    persister = SQLitePersister.from_values(
        str(HOME / "memory.db"), connect_kwargs={"check_same_thread": False}
    )
    persister.initialize()
    trail = theodosia.tracker(surface.name, str(HOME / "trail"))

    def factory():
        return (
            build_application(surface.plane, surface.hypotheses)
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
    return [s for s in states if s and s.get("phase") in {"concluded", "exhausted"}]
