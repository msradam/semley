"""Fast, deterministic checks on the heart of the product: the grounding validators
and the evidence-driven branch election. No model, no infra.

The verdict is the model's, so it is not tested here (there is nothing deterministic
to assert). What is deterministic, and load-bearing, is that the mount refuses a
verdict that does not cite a real dispatched read, and that the graph elects the next
step from accumulated evidence.
"""

from __future__ import annotations

import pytest
from burr.core import State

from semley.graph import build_application
from semley.mount import _v_conclude, _v_recall, _v_refute
from theodosia import ValidationFailed


def _state(*evidence):
    return {"evidence": list(evidence), "current_hypothesis": "service_down"}


def _ev(eid, dispatched=True):
    return {"id": eid, "hypothesis": "service_down", "dispatched": dispatched}


def test_verdict_refused_without_a_grounded_citation():
    # No citation, and a citation resting only on a read that failed to dispatch:
    # both are refused. A verdict must rest on a read that actually ran.
    with pytest.raises(ValidationFailed):
        _v_conclude(_state(_ev("e1")), {})
    with pytest.raises(ValidationFailed):
        _v_conclude(_state(_ev("e1", dispatched=False)), {"cited_evidence": ["e1"]})


def test_verdict_accepts_a_real_dispatched_citation():
    _v_conclude(_state(_ev("e1")), {"cited_evidence": ["e1"]})  # conclude: no raise
    _v_refute(_state(_ev("e1")), {"cited_evidence": ["e1"]})  # refute: same grounding


def test_recall_refuses_unknown_id():
    _v_recall({"evidence": [{"id": "e1"}]}, {"evidence_id": "e1"})
    with pytest.raises(ValidationFailed):
        _v_recall({"evidence": [{"id": "e1"}]}, {"evidence_id": "e9"})


def test_verdict_actions_require_gathered_evidence():
    """conclude and refute are unreachable from investigate until a read dispatched."""
    app = build_application("node", ["service_down", "resource_exhaustion"]).build()
    graph = app.graph

    none = State({"capped": False, "has_evidence": False})
    assert graph.get_next_node("investigate", none, "triage").name == "gather"

    got = State({"capped": False, "has_evidence": True})
    # With evidence, conclude/refute/gather are all offered; the model chooses.
    valid = {
        t.to.name
        for t in graph.transitions
        if t.from_.name == "investigate" and t.condition.run(got)["PROCEED"]
    }
    assert {"conclude", "refute", "gather"} <= valid


def test_refute_election_is_evidence_driven():
    app = build_application("node", ["service_down", "resource_exhaustion"]).build()
    graph = app.graph
    remain = State({"hypotheses_remain": True})
    assert graph.get_next_node("refute", remain, "investigate").name == "investigate"
    done = State({"hypotheses_remain": False})
    assert graph.get_next_node("refute", done, "investigate").name == "exhausted"


def test_capped_forces_exhausted():
    app = build_application("node", ["service_down"]).build()
    capped = State({"capped": True, "has_evidence": True})
    assert app.graph.get_next_node("investigate", capped, "triage").name == "exhausted"
