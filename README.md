# Semley

```
      ┓
┏┏┓┏┳┓┃┏┓┓┏
┛┗ ┛┗┗┗┗ ┗┫
          ┛
```

Semley is an autonomous SRE investigation agent. A language model drives the
investigation; a state machine governs what it may do; and every read runs through a
typed, auditable tool surface the model cannot reach past. The thesis is "entrust,
don't trust": the model decides where the investigation goes, while authority over its
actions and verification of its conclusions stay outside it.

Control and verification live outside the model:

- A Burr state machine is the investigation graph. The model advances it only by
  calling one governed `step` tool with an action name and inputs. An unreachable
  action is refused with the valid next actions, so a wrong move is recoverable.
- The mount grounds every `conclude` and `refute`: it checks that the verdict cites a
  read that actually ran, and refuses it otherwise. The failing entity comes from the
  evidence, not from the code.
- The durable record is a persisted state store, not the transcript. Cross-incident
  memory is a compact findings digest rendered from that store, so context stays flat
  across incidents instead of re-sending every prior investigation's raw evidence.

Reads are performed by reflecting Ansible modules into typed tools (via rocannon),
annotated read-only from each module's own properties. The model never names a module.
On the node and control planes only read-annotated tools are reachable. The telemetry
plane is the documented exception (below): no Ansible module reads Prometheus, so its
read is a fixed GET template the model cannot alter rather than a read-annotated tool.

## Architecture

- **Burr** is the state-machine engine: the investigation graph, its actions, and
  the evidence-driven branching.
- **Theodosia** mounts that graph as a governed MCP server, exposing it through the
  single `step` tool and holding investigation state, with a SQLite state persister
  as the memory of record and a hash-chained trail as the audit ledger.
- **PydanticAI** is the agent framework: model inference over the mounted server,
  with the toolset filtered to exactly `step` and `reset_session`.
- **Rocannon** reflects the curated Ansible modules into typed, read-only tools,
  attached in-process as the upstream tool server.

The investigation is a hypothesis loop: `triage` fixes the target and scope and
elects the first hypothesis, `investigate` gathers evidence for it, and the model
then `conclude`s (confirmed), `refute`s (rule out, re-hypothesize), or `gather`s more.
The loop terminates at `exhausted` when the hypothesis space is used up, reporting
what was ruled out rather than inventing a fault. A read that cannot dispatch to the
target is recorded uninvestigable, never as a refutation.

## Prerequisites

The fast checks need nothing but uv. The live agent needs an API key. Each demo target
needs local infrastructure, which the bring-up scripts create if it is missing.

| To run | Requires |
|---|---|
| `make check` (fast deterministic tests) | [uv](https://docs.astral.sh/uv/) and Python 3.12+ |
| the agent on any surface | the above, plus an API key for an OpenAI-compatible endpoint |
| the `host` demo | [OrbStack](https://orbstack.dev/) (it provides the `web1` Linux machine over SSH) |
| the `cluster` and `telemetry` demos | Docker, [kind](https://kind.sigs.k8s.io/), and `kubectl` |

Ansible and the collections the reads use (`kubernetes.core`, the `kubernetes` client)
are installed into the project virtualenv by `uv sync` and the bring-up scripts. You do
not install them separately.

## Install

```
uv sync
```

Put an API key in a `.env` file at the repo root:

```
OPENAI_API_KEY=sk-...             # the default model is gpt-5.4
```

The agent is vendor-agnostic: it speaks the OpenAI chat-completions protocol, so any
compatible endpoint works. `OPENAI_API_KEY` is the only required variable. For a
different vendor, also set `OPENAI_BASE_URL` (for example `https://openrouter.ai/api/v1`)
and `SEMLEY_MODEL` (for example `anthropic/claude-sonnet-5`).

## Quick start

The fastest check needs no model and no infrastructure:

```
make check
```

To see a real end-to-end investigation without standing up any target, read the
committed recording at `recordings/host-investigation.txt`. To run one live, pick a
surface below.

## Driving the agent

```
uv run semley --surface host
```

The banner lists the inventory hosts (or, on the cluster surface, the live namespaces)
you can name. Describe an incident in plain language. The agent proposes a target and
scope, waits for you to confirm, then drives the investigation to a conclusion.

```
semley › the web service on web1 is not responding
  (proposes target=web1, scope="web service not responding", asks to confirm)
semley › yes
  ▸ step triage target=web1 scope=web service not responding
  ▸ step investigate
    read e1 ansible.builtin.service_facts on web1
    read e2 ansible.builtin.listen_ports_facts on web1
  ▸ step conclude ...
```

Reading the stream:

- `▸ step <action>` is one governed transition of the state machine.
- `read <id> <module> on <target>` is a real Ansible read that dispatched. The id
  (`e1`, `e2`, ...) tags the evidence the model reads and later cites.
- `refused <reason>; valid: ...` is the state machine rejecting an unreachable or
  ungrounded action and listing what is valid instead.
- The closing panel is the model's grounded verdict: `confirmed`, `all clear`, or
  `inconclusive`. The evidence table lists every read, with a `*` on the ones the
  verdict cited.

After a conclusion, `/playbook` writes the recorded Ansible calls as a standard
playbook, `/quit` exits, and Tab completes the commands.

## Demos

Each demo target has a bring-up script that provisions it and prints clear status, so
the demo runs end to end from a clean machine:

```
make demo-host      # host-up + inject a fault + investigate + restore the baseline
make demo-cluster   # kind cluster + a faulted workload + investigate it
make demo-telemetry # Prometheus on the cluster + investigate a failing scrape target
make demo-localhost # investigate this control host
make check          # fast deterministic checks (no model, no infrastructure)
```

- **host** (primary, reliable): `scripts/host-up.sh` ensures an OrbStack systemd
  machine (`web1`) running nginx; `inject-fault.sh` stops it (enabled-but-stopped),
  `heal.sh` restores it. Inventory: `inventory/hosts.ini`.
- **cluster** (control plane): `scripts/cluster-up.sh` stands up a kind cluster, the
  `kubernetes.core` collection and client, and a workload stuck in `ImagePullBackOff`
  in the `shop` namespace; `make cluster-down` removes it.
- **telemetry** (observability plane): `scripts/telemetry-up.sh` deploys Prometheus in
  the kind cluster, scraping itself and a `checkout` job whose target is down
  (`up == 0`), and port-forwards it to `localhost:9090`; `make telemetry-down` stops it.
  Ansible has no read-only module that queries Prometheus (the observability
  collections are all deploy roles and CRUD management), so this surface reads over
  `ansible.builtin.uri`, a general HTTP module. Because `uri` is not read-annotated,
  the boundary here is curation, not annotation: the read is a fixed GET template the
  model cannot alter (it supplies no module and no method), and the action phase also
  rejects any non-GET/QUERY method as defense-in-depth. This is a deliberate workaround
  for the missing telemetry facts module. The surface detects a failing scrape target
  (`up == 0`); deeper root-cause diagnosis and cross-plane correlation are out of scope.
- **localhost**: this machine as a local target (no setup). On a non-systemd control
  host the node reads cannot dispatch, so it returns `inconclusive`, not a false
  all-clear.

## Surfaces

Each surface binds one inventory and a curated, disjoint set of Ansible modules, so a
session bound to one plane structurally cannot call another plane's modules.

| Surface | Plane | Investigates |
|---|---|---|
| `host` | node | service and resource health on a remote systemd host (the primary demo) |
| `localhost` | node | this machine, as a legitimate local target |
| `cluster` | control | a Kubernetes workload fault, scoped by namespace |
| `telemetry` | observability | Prometheus scrape health, read over a GET-only `uri` (see below) |

## Configuration

- `--surface {host,localhost,cluster,telemetry}` selects the governed surface (required).
- `OPENAI_API_KEY` (required) authenticates the model endpoint.
- `OPENAI_BASE_URL` (optional) points at a non-OpenAI vendor; defaults to OpenAI.
- `SEMLEY_MODEL` (optional) selects the model slug; defaults to `gpt-5.4`.
- `THEODOSIA_LEDGER_KEY` (hex) switches the audit ledger from unkeyed SHA-256 to an
  HMAC-keyed chain.

## Auditability

Every investigation produces two artifacts. The governed trail (under `.semley/`) is a
hash-chained log of each transition and refusal: what happened, provably. The recorded
playbook (under `.rocannon/playbooks/`) is a deterministic transcription of the real
Ansible calls, tasks matching the calls in order, credentials redacted at recording:
what was done, faithfully. The trail and the state persister are separate stores, and
neither is the model's.

## Limitations

The hypothesis set is bounded and pre-enumerated; faults outside it are out of scope by
design, and the loop terminates honestly at `exhausted` rather than guessing. A session
is bound to one inventory and one tool package at launch. Recorded playbooks are audit
records, not directly re-runnable: credentials are redacted and must be restored to
replay. The host surface is the most reliable; the cluster surface works but has more
moving parts (a kind cluster and the kubernetes client), so it needs `make cluster-up`
first.

## Naming

Semley, Rocannon, and the ansible all come from Ursula K. Le Guin's *Rocannon's World*
(1966). The ansible, her name for a device that communicates instantly across any
distance, gave Red Hat's Ansible its name. Rocannon is the novel's protagonist, and the
library that reflects Ansible modules into tools takes his name. Semley is the
noblewoman whose journey opens the book.

## License

MIT
