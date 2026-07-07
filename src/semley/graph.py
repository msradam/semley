"""The hypothesis loop: a Burr state machine the model drives one action at a time.

The model owns judgment. `investigate` gathers the current hypothesis's reads and
returns the raw facts; the model reads them and decides by which action it calls:
`conclude` (confirmed), `refute` (ruled out), or `gather` (more evidence). The
verdict is the model's, supplied as a finding and the evidence ids it relied on.
Verification is external but process-level (see mount.py): the mount refuses a
verdict that cites no read that actually ran. It never inspects a fact value or
judges whether the evidence supports the verdict, which would rebuild the answer
key. No service or fault name lives here.
"""

from __future__ import annotations

from typing import Any

from burr.core import (
    Application,
    ApplicationBuilder,
    State,
    action,
    default,
    when,
)
from theodosia import safe_upstream

from . import hypotheses as hyp
from .state import INITIAL, new_evidence_id

ITERATION_CAP = 8
UPSTREAM = "ansible"
SAFE_HTTP_METHODS = {"GET", "QUERY"}


def _is_read_only(module: str, args: dict) -> bool:
    """Reject a uri read whose method is not safe: GET (RFC 9110) or QUERY (the HTTP
    QUERY draft). The telemetry read is a fixed GET template the model cannot alter,
    so this guards a future hypothesis author, not the model: defense-in-depth on top
    of curation, not the primary read-only boundary. Facts modules are read-only in
    themselves and are not checked here.
    """
    if module.endswith(".uri"):
        return str(args.get("method", "GET")).upper() in SAFE_HTTP_METHODS
    return True


def _resolve_namespace(target: str, scope: str, known: list[str]) -> str:
    """Snap the model's free-text target/scope to a real namespace token.

    The model is verbose and unreliable at emitting a bare namespace ("the host that
    serves the control plane", "shop namespace"). Rather than trust it, match against
    the namespaces that actually exist and use the one it named.
    """
    tokens = set(f"{target} {scope}".replace("-", " ").split())
    return next((ns for ns in known if ns in tokens), (target or scope).strip())


@action(
    reads=["hypotheses", "plane", "known_namespaces"],
    writes=[
        "target",
        "scope",
        "current_hypothesis",
        "hypotheses",
        "phase",
        "iteration",
    ],
)
def triage(state: State, target: str, scope: str = "") -> tuple[dict, State]:
    """Fix the target and scope, and elect the first hypothesis to investigate."""
    if state["plane"] == "control":
        target = _resolve_namespace(target, scope, state["known_namespaces"])
        scope = target
    candidates = list(state["hypotheses"])
    first = candidates[0] if candidates else None
    new = state.update(
        target=target,
        scope=scope,
        current_hypothesis=first,
        hypotheses=candidates[1:],
        phase="investigating",
        iteration=0,
    )
    return {
        "target": target,
        "scope": scope,
        "current_hypothesis": first,
        "description": hyp.CATALOG[first].description if first else None,
        "remaining": new["hypotheses"],
    }, new


@action(
    reads=["current_hypothesis", "target", "scope", "plane", "evidence", "iteration"],
    writes=["evidence", "uninvestigable", "iteration", "capped", "has_evidence"],
)
async def investigate(state: State) -> tuple[dict, State]:
    """Gather the current hypothesis's reads and return the raw facts for the model.

    A read that cannot dispatch is recorded uninvestigable, never as a refutation.
    No verdict is computed here: the returned facts are what the model reads to
    make its own confirm/refute decision. Node reads execute on the remote target;
    control reads execute on the control host and reach the cluster API by
    kubeconfig, scoped by namespace.
    """
    hypothesis = state["current_hypothesis"]
    plane = state["plane"]
    host = "localhost" if plane in ("control", "observability") else state["target"]
    spec = hyp.CATALOG[hypothesis]

    new = state
    gathered: list[dict[str, Any]] = []
    failed: list[str] = []
    for read in spec.reads(state["target"], state["scope"]):
        short = read.module.split(".")[-1]
        args = read.args | {"target": host}
        if not _is_read_only(read.module, args):
            new = new.append(
                uninvestigable={
                    "hypothesis": hypothesis,
                    "module": short,
                    "target": host,
                    "reason": "refused: non-GET/QUERY method on a read-only surface",
                }
            )
            failed.append(short)
            continue
        result = await safe_upstream(UPSTREAM, UPSTREAM, read.tool, args)
        payload = result.data if result.usable else None
        dispatched = (
            bool(payload)
            and payload.get("status") == "successful"
            and payload.get("result")
        )
        if dispatched:
            eid = new_evidence_id(new)
            facts = payload["result"]
            new = new.append(
                evidence={
                    "id": eid,
                    "hypothesis": hypothesis,
                    "module": short,
                    "target": host,
                    "dispatched": True,
                    "facts": facts,
                }
            )
            gathered.append(
                {
                    "id": eid,
                    "module": read.module,
                    "args": dict(read.args),
                    "target": host,
                    "facts": facts,
                }
            )
        else:
            if payload:
                detail = payload.get("stderr") or (payload.get("result") or {}).get(
                    "msg"
                )
            else:
                detail = result.detail
            new = new.append(
                uninvestigable={
                    "hypothesis": hypothesis,
                    "module": short,
                    "target": host,
                    "reason": (detail or "read did not dispatch").strip()[:200],
                }
            )
            failed.append(short)

    iteration = state["iteration"] + 1
    has_evidence = any(
        e["hypothesis"] == hypothesis and e["dispatched"] for e in new["evidence"]
    )
    new = new.update(
        iteration=iteration,
        capped=iteration >= ITERATION_CAP,
        has_evidence=has_evidence,
    )
    return {
        "hypothesis": hypothesis,
        "gathered": gathered,
        "uninvestigable": failed,
        "note": "Read these facts and decide. If they confirm the hypothesis call "
        "conclude with your finding and the evidence ids you relied on; if they rule "
        "it out call refute; if inconclusive call gather.",
    }, new


@action(
    reads=["current_hypothesis", "target"],
    writes=["conclusion", "outcome", "citations", "phase"],
)
def conclude(
    state: State, finding: str, cited_evidence: list[str]
) -> tuple[dict, State]:
    """Write the model's confirmed diagnosis. The mount validator checks the citations."""
    cited = list(cited_evidence)
    conclusion = (
        f"{state['current_hypothesis']} confirmed on {state['target']}: {finding}"
    )
    new = state.update(
        conclusion=conclusion, outcome="confirmed", citations=cited, phase="concluded"
    )
    return {"conclusion": conclusion, "outcome": "confirmed", "citations": cited}, new


@action(
    reads=["current_hypothesis", "hypotheses", "ruled_out"],
    writes=[
        "current_hypothesis",
        "hypotheses",
        "ruled_out",
        "hypotheses_remain",
        "has_evidence",
    ],
)
def refute(state: State, finding: str, cited_evidence: list[str]) -> tuple[dict, State]:
    """Rule the current hypothesis out on the model's reading, and elect the next."""
    hypothesis = state["current_hypothesis"]
    remaining = list(state["hypotheses"])
    nxt = remaining[0] if remaining else None
    new = state.append(
        ruled_out={
            "hypothesis": hypothesis,
            "finding": finding,
            "citations": list(cited_evidence),
        }
    ).update(
        current_hypothesis=nxt,
        hypotheses=remaining[1:],
        hypotheses_remain=nxt is not None,
        has_evidence=False,
    )
    return {"ruled_out": hypothesis, "finding": finding, "next_hypothesis": nxt}, new


@action(reads=["current_hypothesis"], writes=[])
def gather(state: State) -> tuple[dict, State]:
    """Signal intent to gather more evidence for the same hypothesis."""
    return {"gather": state["current_hypothesis"]}, state


@action(
    reads=["ruled_out", "uninvestigable", "target"],
    writes=["conclusion", "outcome", "phase"],
)
def exhausted(state: State) -> tuple[dict, State]:
    """Terminate honestly when the hypothesis space is used up."""
    ruled = [r["hypothesis"] for r in state["ruled_out"]]
    uninv = {u["hypothesis"] for u in state["uninvestigable"]}
    if uninv:
        outcome = "inconclusive"
        conclusion = (
            f"Inconclusive on {state['target']}: ruled out {', '.join(ruled) or 'nothing'}; "
            f"could not investigate {', '.join(sorted(uninv))}."
        )
    else:
        outcome = "all_clear"
        conclusion = (
            f"All clear on {state['target']}: ruled out {', '.join(ruled) or 'nothing'}, "
            "no fault found in the hypothesis set."
        )
    new = state.update(conclusion=conclusion, outcome=outcome, phase="exhausted")
    return {"conclusion": conclusion, "outcome": outcome}, new


@action(reads=["evidence"], writes=[])
def recall(state: State, evidence_id: str) -> tuple[dict, State]:
    """Pull one gathered evidence entry back up by id. Gated to a known id."""
    entry = next((e for e in state["evidence"] if e["id"] == evidence_id), None)
    return {"evidence": entry}, state


def build_application(
    plane: str, hypothesis_names: list[str], namespaces: list[str] | None = None
) -> Application:
    """Assemble the investigation graph for one plane's hypothesis set."""
    b = (
        ApplicationBuilder()
        .with_actions(triage, investigate, conclude, refute, gather, exhausted, recall)
        .with_transitions(
            ("triage", "investigate"),
            ("investigate", "exhausted", when(capped=True)),
            ("investigate", "conclude", when(capped=False, has_evidence=True)),
            ("investigate", "refute", when(capped=False, has_evidence=True)),
            ("investigate", "gather", when(capped=False)),
            ("gather", "investigate"),
            ("refute", "investigate", when(hypotheses_remain=True)),
            ("refute", "exhausted", default),
            ("conclude", "recall"),
            ("exhausted", "recall"),
            ("recall", "recall"),
        )
        .with_state(
            **(
                INITIAL
                | {
                    "plane": plane,
                    "hypotheses": list(hypothesis_names),
                    "known_namespaces": list(namespaces or []),
                }
            )
        )
        .with_entrypoint("triage")
    )
    return b
