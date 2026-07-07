# AGENTS.md

Guidance for coding agents (and people) working in this repo. Semley is an autonomous
SRE investigation agent: a language model drives an investigation through one governed
`step` tool, a Burr state machine gates which transitions are legal, and conclusions are
verified against real evidence in code the model cannot reach. `README.md` has the full
design; this file is the fast path to running it.

## Setup

```bash
uv sync                                   # needs uv and Python 3.12+
printf 'OPENAI_API_KEY=sk-...\n' > .env   # any OpenAI-compatible key; default model gpt-5.4
```

The agent is vendor-agnostic (it speaks the OpenAI chat-completions protocol). For a
non-OpenAI vendor, also set `OPENAI_BASE_URL` (e.g. `https://openrouter.ai/api/v1`) and
`SEMLEY_MODEL` (e.g. `anthropic/claude-sonnet-5`).

## Verify it works (no key, no infrastructure)

```bash
make check                                # 7 fast deterministic tests; no model, no infra
```

To see a real end-to-end investigation without a key or any infrastructure, read
`recordings/host-investigation.txt`.

## Run the agent

```bash
uv run semley --surface host              # needs a key; the host demo also needs OrbStack
```

Describe an incident in plain language, confirm the proposed target and scope, and it
drives the investigation to a grounded conclusion. `/playbook` saves the recorded
Ansible reads as a standard playbook, `/quit` exits, Tab completes the commands.

## Demo targets

Each command below stands up its own target (printing clear status) and then runs the
agent. **These create real local infrastructure**, so run them deliberately, not as a
quick check:

| Command | What it does | Requires |
|---|---|---|
| `make demo-host` | inject a service fault on `web1`, investigate, restore | OrbStack |
| `make demo-cluster` | kind cluster + a workload in ImagePullBackOff, investigate | Docker, kind, kubectl |
| `make demo-telemetry` | Prometheus + a down scrape target, investigate | Docker, kind, kubectl |
| `make demo-localhost` | investigate this machine | nothing extra |

`make cluster-down` and `make telemetry-down` tear the cluster pieces back down.
`make inject` and `make heal` toggle the host fault on its own, for driving the agent
separately from the bundled demo. After a run, `uv run theodosia verify --home
.semley/trail` recomputes the session's hash chain.

## Tests and checks

```bash
make check     # fast tests: deterministic, no model, no infrastructure. Run this while iterating.
make ci        # format + lint + tests (gates), plus advisory refurb / vulture / radon
```

The live agent and the demo targets need a key and infrastructure and are deliberately
kept out of the fast suite.

## Code style

- Python, formatted and linted with ruff: `uv run ruff format` and `uv run ruff check`.
- `make ci` is the gate; format, lint, and tests must pass before a commit.
- Comment on why, not what. Keep functions small and named for what they do.

## The one invariant to preserve

The model decides the verdict; code never does. `investigate` returns raw facts and
computes no conclusion. The mount validators in `src/semley/mount.py` check only that a
verdict cites a read that actually dispatched, never whether the facts support it.
Adjudicating fact values in code would rebuild the answer-key oracle this design exists
to avoid. Do not add fault-specific names, thresholds, or verdict logic to the graph or
the hypothesis catalog.

## Project layout

`src/semley/`:

- `graph.py`: the Burr state machine and its actions (`triage`, `investigate`, `conclude`, `refute`, `gather`, `exhausted`, `recall`).
- `mount.py`: the Theodosia mount, the grounding validators, the state persister, the audit trail.
- `surfaces.py`: the four surfaces (`host`, `localhost`, `cluster`, `telemetry`) and their disjoint module sets.
- `tools.py`: rocannon reflection of each surface's curated Ansible modules.
- `hypotheses.py`: the pre-enumerated hypothesis catalog and the reads each one probes with.
- `agent.py`: the PydanticAI agent, filtered to the governed tools, plus the headless `investigate` API.
- `repl.py`: the streaming REPL and its rendering.
- `state.py`, `audit.py`, `cli.py`: state helpers, playbook recording, the CLI entry point.
