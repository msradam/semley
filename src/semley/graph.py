"""The investigation loop: a Burr state machine the model drives one action at a time.

The model owns judgment and the reads. It fixes a target with `triage`, states its own
hypothesis, then calls `read` with an Ansible module and its arguments, choosing what to
look at from the surface's read-only module set. It reads until it can `conclude`
(citing the evidence) or gives up honestly with `inconclusive`. The state machine
governs which action is legal; the mount checks a verdict cites a read that actually
ran. No fault, module, or argument is prescribed here: the action space is the surface's
modules, and the model fills in the calls.
"""

from __future__ import annotations

from typing import Any

from burr.core import (
    ApplicationBuilder,
    State,
    action,
    when,
)
from theodosia import safe_upstream

from .state import INITIAL, new_evidence_id

ITERATION_CAP = 12
UPSTREAM = "ansible"
SAFE_HTTP_METHODS = {"GET", "QUERY"}


async def _run_module(module: str, call_args: dict) -> tuple[Any | None, str | None]:
    """Dispatch one module through rocannon. Return (facts, None) if it produced a
    result, else (None, reason) for the uninvestigable record."""
    result = await safe_upstream(
        UPSTREAM, UPSTREAM, module.replace(".", "_"), call_args
    )
    payload = result.data if result.usable else None
    if payload and payload.get("status") == "successful" and payload.get("result"):
        return payload["result"], None
    if payload:
        detail = payload.get("stderr") or (payload.get("result") or {}).get("msg")
    else:
        detail = result.detail
    return None, (detail or "read did not dispatch").strip()[:200]


def _is_read_only(module: str, args: dict) -> bool:
    """A uri read is only allowed if its method is safe: GET (RFC 9110) or QUERY (the
    HTTP QUERY draft). Facts modules are read-only in themselves; uri is a general HTTP
    module, so the read stays a read by enforcing the method here, not by annotation.
    """
    if module.endswith(".uri"):
        return str(args.get("method", "GET")).upper() in SAFE_HTTP_METHODS
    return True


def _resolve_namespace(value: str, known: list[str]) -> str:
    """Snap a free-text namespace to a real one if a known namespace appears in it.
    The model is verbose ('shop namespace'); match against what actually exists."""
    tokens = set(str(value).replace("-", " ").split())
    return next((ns for ns in known if ns in tokens), str(value).strip())


@action(
    reads=["plane", "modules", "known_namespaces"],
    writes=["target", "scope", "hypothesis", "phase", "iteration"],
)
def triage(
    state: State, target: str, scope: str = "", hypothesis: str = ""
) -> tuple[dict, State]:
    """Fix what to investigate and record the model's own working hypothesis.

    On a host surface the target is an inventory host; on a cluster surface it is a
    namespace (snapped to one that exists).
    """
    if state["plane"] == "control":
        target = _resolve_namespace(target, state["known_namespaces"])
    new = state.update(
        target=target,
        scope=scope,
        hypothesis=hypothesis,
        phase="investigating",
        iteration=0,
    )
    return {
        "target": target,
        "scope": scope,
        "hypothesis": hypothesis,
        "modules": list(state["modules"]),
    }, new


@action(
    reads=["target", "plane", "evidence", "iteration", "modules", "known_namespaces"],
    writes=["evidence", "uninvestigable", "iteration", "capped", "has_evidence"],
)
async def read(
    state: State, module: str, args: dict[str, Any] | None = None
) -> tuple[dict, State]:
    """Dispatch one model-chosen read-only Ansible module and return the raw facts.

    The module comes from the surface's set (the mount refuses others); the args come
    from the model. Node reads run on the target host; control and observability reads
    run on the control host. A read that cannot dispatch is recorded uninvestigable.
    """
    args = dict(args or {})
    plane = state["plane"]
    host = "localhost" if plane in ("control", "observability") else state["target"]
    if plane == "control" and "namespace" in args:
        args["namespace"] = _resolve_namespace(
            args["namespace"], state["known_namespaces"]
        )
    short = module.split(".")[-1]
    entry = {"module": short, "args": args, "target": host}

    new = state
    gathered: list[dict[str, Any]] = []
    failed: list[str] = []
    if not _is_read_only(module, args):
        reason = "refused: non-GET/QUERY method on a read-only surface"
        new = new.append(uninvestigable={**entry, "reason": reason})
        failed.append(short)
    else:
        facts, reason = await _run_module(module, args | {"target": host})
        if facts is not None:
            eid = new_evidence_id(new)
            new = new.append(
                evidence={"id": eid, **entry, "dispatched": True, "facts": facts}
            )
            gathered.append(
                {
                    "id": eid,
                    "module": module,
                    "args": args,
                    "target": host,
                    "facts": facts,
                }
            )
        else:
            new = new.append(uninvestigable={**entry, "reason": reason})
            failed.append(short)

    iteration = state["iteration"] + 1
    has_evidence = any(e.get("dispatched") for e in new["evidence"])
    new = new.update(
        iteration=iteration,
        capped=iteration >= ITERATION_CAP,
        has_evidence=has_evidence,
    )
    return {
        "gathered": gathered,
        "uninvestigable": failed,
        "modules": list(state["modules"]),
        "note": "Read the facts and decide. Read another module to follow the evidence, "
        "call conclude with your finding and the evidence ids you relied on, or call "
        "inconclusive if you cannot confirm a fault.",
    }, new


@action(reads=["target"], writes=["conclusion", "outcome", "citations", "phase"])
def conclude(
    state: State, finding: str, cited_evidence: list[str]
) -> tuple[dict, State]:
    """Write the model's confirmed diagnosis. The mount validator checks the citations."""
    cited = list(cited_evidence)
    conclusion = f"confirmed on {state['target']}: {finding}"
    new = state.update(
        conclusion=conclusion, outcome="confirmed", citations=cited, phase="concluded"
    )
    return {"conclusion": conclusion, "outcome": "confirmed", "citations": cited}, new


@action(reads=["target"], writes=["conclusion", "outcome", "phase"])
def inconclusive(state: State, finding: str) -> tuple[dict, State]:
    """Terminate honestly when the model cannot confirm a fault: nothing wrong, or the
    reads it needed could not run. No fault is invented."""
    conclusion = f"inconclusive on {state['target']}: {finding}"
    new = state.update(conclusion=conclusion, outcome="inconclusive", phase="concluded")
    return {"conclusion": conclusion, "outcome": "inconclusive"}, new


@action(reads=["evidence"], writes=[])
def recall(state: State, evidence_id: str) -> tuple[dict, State]:
    """Pull one gathered evidence entry back up by id. Gated to a known id."""
    entry = next((e for e in state["evidence"] if e["id"] == evidence_id), None)
    return {"evidence": entry}, state


def build_application(
    plane: str, modules: list[str], namespaces: list[str] | None = None
) -> ApplicationBuilder:
    """Assemble the investigation graph for one surface's read-only module set."""
    b = (
        ApplicationBuilder()
        .with_actions(triage, read, conclude, inconclusive, recall)
        .with_transitions(
            ("triage", "read"),
            ("read", "read", when(capped=False)),
            ("read", "conclude", when(has_evidence=True)),
            ("read", "inconclusive"),
            ("conclude", "recall"),
            ("inconclusive", "recall"),
            ("recall", "recall"),
        )
        .with_state(
            **(
                INITIAL
                | {
                    "plane": plane,
                    "modules": list(modules),
                    "known_namespaces": list(namespaces or []),
                }
            )
        )
        .with_entrypoint("triage")
    )
    return b
