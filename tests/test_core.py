"""Fast, deterministic checks on the heart of the product: the action-space boundary,
the grounding validators, and the read-driven transitions. No model, no infra.

The verdict is the model's, so it is not tested here. What is deterministic, and
load-bearing, is that the mount refuses a read outside the surface's module set, refuses
a verdict that cites no dispatched read, and that the graph lets the model read, then
conclude or give up.
"""

from __future__ import annotations

import pytest
from burr.core import State

from semley.graph import _is_read_only, _resolve_namespace, build_application
from semley.mount import _v_read, _v_recall, _v_verdict
from semley.surfaces import CONTROL_MODULES, NODE_MODULES
from theodosia import ValidationFailed


def _state(*evidence, modules=NODE_MODULES):
    return {"evidence": list(evidence), "modules": list(modules)}


def _ev(eid, dispatched=True):
    return {"id": eid, "dispatched": dispatched}


# --- the action-space boundary: the model picks the module, not the set ---


def test_read_refuses_a_module_off_the_surface():
    _v_read(_state(), {"module": "ansible.builtin.service_facts"})  # on the host set
    with pytest.raises(ValidationFailed):
        _v_read(_state(), {"module": "ansible.builtin.command"})  # not on the set
    with pytest.raises(ValidationFailed):
        _v_read(_state(modules=CONTROL_MODULES), {"module": "ansible.builtin.setup"})


def test_uri_reads_are_get_or_query_only():
    assert _is_read_only("ansible.builtin.uri", {})  # defaults to GET
    assert _is_read_only("ansible.builtin.uri", {"method": "QUERY"})
    assert not _is_read_only("ansible.builtin.uri", {"method": "POST"})
    assert _is_read_only("ansible.builtin.setup", {"method": "POST"})  # not uri


# --- grounding: a verdict must cite a read that actually ran ---


def test_verdict_refused_without_a_grounded_citation():
    with pytest.raises(ValidationFailed):
        _v_verdict(_state(_ev("e1")), {})  # no citation
    with pytest.raises(ValidationFailed):
        _v_verdict(_state(_ev("e1")), {"cited_evidence": ["e9"]})  # unknown id
    with pytest.raises(ValidationFailed):
        _v_verdict(_state(_ev("e1", dispatched=False)), {"cited_evidence": ["e1"]})


def test_verdict_accepts_a_real_dispatched_citation():
    _v_verdict(_state(_ev("e1")), {"cited_evidence": ["e1"]})  # must not raise


def test_recall_refuses_unknown_id():
    _v_recall({"evidence": [{"id": "e1"}]}, {"evidence_id": "e1"})
    with pytest.raises(ValidationFailed):
        _v_recall({"evidence": [{"id": "e1"}]}, {"evidence_id": "e9"})


def test_namespace_snaps_to_a_real_one():
    known = ["default", "shop", "monitoring"]
    assert _resolve_namespace("shop namespace", known) == "shop"
    assert _resolve_namespace("the shop workload is down", known) == "shop"
    assert _resolve_namespace("nope", known) == "nope"


# --- transitions: the model reads, then concludes or gives up ---


def test_read_loop_and_termination():
    graph = build_application("node", NODE_MODULES).build().graph

    def valid_from(node, **flags):
        st = {"capped": False, "has_evidence": False, **flags}
        return {
            t.to.name
            for t in graph.transitions
            if t.from_.name == node and t.condition.run(State(st))["PROCEED"]
        }

    assert graph.get_next_node("triage", State({}), "triage").name == "read"
    # after a dispatched read the model may read again, conclude, or give up
    assert {"read", "conclude", "inconclusive"} <= valid_from("read", has_evidence=True)
    # capped closes the read loop but conclude and inconclusive remain
    capped = valid_from("read", capped=True, has_evidence=True)
    assert "read" not in capped and {"conclude", "inconclusive"} <= capped
    # with no evidence yet, conclude is unreachable; the honest move is to give up
    none_yet = valid_from("read", has_evidence=False)
    assert "conclude" not in none_yet and {"read", "inconclusive"} <= none_yet
