# Architecture

How Semley is put together and why. `README.md` covers install and usage; this
document covers the design.

## Context and goals

Autonomous SRE agents fail in a characteristic way: they decide they are done without
external evidence. The agent investigates, forms a theory, declares it confirmed, and
nothing outside the model ever checks the claim. Semley is built against that failure
mode. The thesis is "entrust, don't trust": give the model real agency over where the
investigation goes and what it reads, and keep authority over what it may do, and
verification of what it concludes, outside the model.

The goals, in order:

1. The model owns judgment. It forms the hypothesis, chooses the reads, and decides
   the verdict. No fault name, threshold, or verdict logic lives in code.
2. The system owns permission. A state machine gates which action is legal at each
   moment, and validators refuse ungrounded verdicts and out-of-bounds reads.
3. Every step is auditable after the fact, including every refusal.
4. Reads are real and read-only. The agent inspects running infrastructure through
   Ansible; it changes nothing.

## High-level design

```
  ┌────────────────────────────┐
  │      PydanticAI agent      │   the model: forms hypotheses, picks
  │                            │   modules and args, decides verdicts
  └────────────────────────────┘
                 │  step(action, inputs)      ▲ refused + valid next actions
                 ▼
  ╔════════════════════════════╗
  ║     Theodosia (mount)      ║   one MCP tool: `step`. Hash-chained
  ║     + Burr (graph)         ║   ledger. Input validators. Burr gates
  ║                            ║   which transition is reachable.
  ╚════════════════════════════╝
                 │  upstream call
                 ▼
  ┌────────────────────────────┐
  │   Rocannon (tool surface)  │   the surface's Ansible modules,
  │                            │   reflected as typed tools
  └────────────────────────────┘
                 │  Ansible read
                 ▼
  ┌────────────────────────────┐
  │        real target         │   OrbStack host / kind cluster /
  │                            │   Prometheus
  └────────────────────────────┘
```

Theodosia is the only MCP server the model connects to, and the agent's toolset is
filtered to exactly `step` and `reset_session`. Rocannon is not a second tool menu:
it is mounted in-process as an upstream that the graph's `read` action calls. The
model chooses the module and arguments, but the choice travels through the governed
`step` call, never through a direct tool.

## The investigation loop

The Burr graph (`src/semley/graph.py`) has five actions:

- `triage(target, scope, hypothesis)`: fix what to investigate. The model states its
  own working hypothesis in plain words. On the control plane the target is snapped
  to a namespace that actually exists; on the observability plane it is set to
  `prometheus` deterministically.
- `read(module, args)`: dispatch one model-chosen Ansible module. Raw facts come back
  tagged with an evidence id. Each result steers the model's next read. The loop is
  capped (`ITERATION_CAP`), and PydanticAI usage limits bound the session.
- `conclude(finding, cited_evidence)`: the model's confirmed diagnosis, with the
  evidence ids it relied on.
- `inconclusive(finding)`: honest termination when no fault is confirmed.
- `recall(evidence_id)`: pull one gathered reading back up after a verdict.

Transitions enforce the shape: `read` loops until capped, `conclude` is unreachable
until at least one read dispatched, and `inconclusive` is always available, so the
honest exit is never blocked.

## Key decisions and trade-offs

**The action space is the surface's module set.** A surface
(`src/semley/surfaces.py`) binds one inventory and a curated list of read-only
Ansible modules; the sets are disjoint across planes. The model reads anything it
wants within the set, with arguments it writes itself. The mount validator refuses a
module outside the set. Widening the action space means adding a module to a surface,
one line. Trade-off: a fault that needs a module the surface does not reflect ends at
`inconclusive`.

**Grounding is process-level, not semantic.** The validators
(`src/semley/mount.py`) check that a verdict cites evidence ids that exist and that
at least one cited read actually dispatched. They never inspect a fact value or judge
whether the evidence supports the verdict. Judging fact values in code would rebuild
the answer key the design exists to avoid: the moment code knows what "confirmed"
looks like, the model is no longer doing the diagnosis. Trade-off, held deliberately:
a finding that contradicts its own citation passes the validator. The citation makes
the claim checkable by a human; the system proves provenance, not truth.

**Read-only is enforced where it can be.** Facts modules are read-only by nature.
The telemetry surface needs `ansible.builtin.uri` because no read-only Ansible module
queries Prometheus, so the `read` action rejects any HTTP method other than GET or
QUERY before dispatch. The model writes its own PromQL; it cannot write a mutation.

**Failure is recorded, never inferred from.** A read that cannot dispatch is recorded
`uninvestigable` with the module's own error message. It never counts as evidence of
health or of fault. An investigation whose reads all fail ends `inconclusive`, not
`all clear`.

**Memory is the state store, not the transcript.** Investigation state persists in
SQLite (`.semley/memory.db`, partitioned by surface). At a new incident the model
conversation resets and a compact digest of prior findings, rendered from persisted
state, is prepended. Context stays flat as incidents accumulate. Trade-off: within a
single incident, raw facts do ride the conversation; a compact evidence view with
`recall` for the raw record is the known next step.

**Untrusted text is grounded deterministically.** The model is verbose and unreliable
at emitting exact tokens, so triage snaps its free-text namespace to one that exists
in the cluster, and the observability target is set in code. These fixes name where
the investigation runs; they say nothing about the fault.

## Audit trail

Two artifacts, in separate stores, neither writable by the model:

- The governed trail (`.semley/trail`): a hash-chained ledger of every transition and
  every refusal, written by Theodosia. `theodosia verify` recomputes the chain.
- The recorded playbook (`.rocannon/playbooks/`, written on `/playbook`): rocannon's
  deterministic transcription of the real Ansible calls, credentials redacted.

## Data flow of one read

1. The model calls `step("read", {module, args})`.
2. Theodosia's input validator checks the module is on the surface's set; Burr checks
   `read` is reachable from the current state. Either failure returns a refusal with
   the valid next actions, and the refusal is ledgered.
3. The `read` action applies the read-only method check, resolves the execution host
   (the target host on the node plane, the control host elsewhere), and calls the
   reflected module through rocannon.
4. Ansible runs the module against the real target. Rocannon records the call.
5. The raw facts are appended to state as evidence with a fresh id and returned to
   the model, which decides the next step.

## Libraries

| Piece | Role |
|---|---|
| [Burr](https://github.com/apache/burr) | the investigation state machine and its persistence |
| [PydanticAI](https://ai.pydantic.dev/) | the agent: model inference, MCP client |
| [Theodosia](https://pypi.org/project/theodosia/) | the governed `step` mount, validators, hash-chained ledger |
| [Rocannon](https://pypi.org/project/rocannon/) | Ansible modules reflected into typed, read-annotated tools |

Theodosia and Rocannon are the author's published libraries; Semley composes them
against a problem they were built for.
