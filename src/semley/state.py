"""Investigation state: the object the whole investigation reads and writes.

Evidence entries keep the real read facts as the durable record. The model-facing
digest projects them to a compact form so cross-incident context stays flat.
"""

from __future__ import annotations

from typing import Any

from burr.core import State

INITIAL: dict[str, Any] = {
    "phase": "new",
    "incident": "",
    "target": "",
    "scope": "",
    "plane": "",
    "hypotheses": [],
    "current_hypothesis": None,
    "ruled_out": [],
    "uninvestigable": [],
    "evidence": [],
    "citations": [],
    "conclusion": None,
    "outcome": None,
    "iteration": 0,
    "capped": False,
    "has_evidence": False,
    "hypotheses_remain": True,
}


def evidence_for(state: State, hypothesis: str) -> list[dict[str, Any]]:
    """Evidence entries gathered for a given hypothesis."""
    return [e for e in state["evidence"] if e["hypothesis"] == hypothesis]


def find_evidence(state: State, evidence_id: str) -> dict[str, Any] | None:
    return next((e for e in state["evidence"] if e["id"] == evidence_id), None)


def new_evidence_id(state: State) -> str:
    return f"e{len(state['evidence']) + 1}"


def render_digest(incidents: list[dict[str, Any]]) -> str:
    """One compact line per prior incident: target, outcome, finding.

    This is the cross-incident memory, rendered from persisted state rather than
    the raw transcript, so context stays flat as incidents accumulate.
    """
    if not incidents:
        return ""
    lines = ["Prior incidents this session (memory of record, from persisted state):"]
    for n, inc in enumerate(incidents, 1):
        lines.append(f"  {n}. {inc['target']}: {inc['outcome']} - {inc['finding']}")
    return "\n".join(lines)
