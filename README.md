# Semley

```
      ┓
┏┏┓┏┳┓┃┏┓┓┏
┛┗ ┛┗┗┗┗ ┗┫
          ┛
```

Semley is an autonomous SRE investigation agent. A language model drives an
investigation; a state machine governs what is allowed; every read runs through a
typed, auditable tool surface; and the model cannot reach past the action boundary
into the tool layer. The thesis is "entrust, don't trust": give the model agency over
where an investigation goes, and keep authority over what it may do, and verification
of what it concludes, outside the model.

Control and verification live outside the model:

- A Burr state machine is the investigation graph. The model advances it only by
  calling one governed `step` tool with an action name and inputs. An unreachable
  action is refused with the valid next actions, so a wrong move is recoverable.
- Conclusions are verified externally. `conclude` and `refute` are grounded against
  the real evidence before they are written: a verdict the facts do not support is
  refused. The faulty entity is discovered from evidence, not named in the code.
- The durable record is a persisted state store, not the transcript. Cross-incident
  memory is a compact findings digest rendered from that store, so context stays flat
  across incidents instead of re-sending every prior investigation's raw evidence.

Reads are performed by reflecting Ansible modules into typed tools (via rocannon),
annotated read-only from each module's own properties. The surface is read-only by
default: only read-annotated tools are reachable, and the model never names a module.

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

## Install

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), and an API key for any
OpenAI-compatible endpoint.

```
uv sync
```

Put the key in a `.env` file:

```
OPENAI_API_KEY=sk-...             # an OpenAI key; the default model is gpt-5.4
```

The agent is vendor-agnostic: it talks the OpenAI chat-completions protocol, so any
compatible endpoint works. `OPENAI_API_KEY` is the only required variable. To use a
different vendor, set `OPENAI_BASE_URL` (for example `https://openrouter.ai/api/v1`)
and `SEMLEY_MODEL` (for example `anthropic/claude-sonnet-5`).

## Usage

```
uv run semley --surface host
```

Describe an incident in plain language. The agent grounds a target and scope against
the real inventory, proposes them, and acts only once you confirm. It then streams
each governed step and each real Ansible read, and closes with a grounded diagnosis.
After a conclusion, `/playbook` writes the recorded Ansible calls as a standard
playbook.

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
  `ansible.builtin.uri`, a general HTTP module. The read-only guarantee is therefore
  enforced in the action phase, which allows only GET or QUERY, rather than by module
  annotation. This is a deliberate workaround for the missing telemetry facts module.
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

## License

MIT
