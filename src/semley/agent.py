"""One PydanticAI agent over the mounted server, its toolset filtered to the
governed surface. Plus the headless `investigate` API every other surface projects.
"""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import UsageLimits

from .mount import load_prior_state, mount_surface
from .surfaces import SURFACES, Surface, cluster_namespaces

ALLOWED_TOOLS = {"step", "reset_session"}
DEFAULT_MODEL = "gpt-5.4"

INSTRUCTIONS = """\
You are Semley, an autonomous SRE investigation agent. You diagnose a fault on a
running system by driving a governed state machine through one tool: `step`.

You investigate by reading real state with Ansible. You never change anything: you call
read-only modules and reason over what they return. Every call is `step(action, inputs)`.
The state machine returns the valid next actions after each call; if you request one that
is not reachable it refuses and lists what is valid, so correct course and continue.

The loop:
- `triage` (inputs: target, scope, hypothesis): fix what you investigate and state your
  own working hypothesis in plain words. On a host surface the `target` is an inventory
  host; on a cluster surface it is a namespace (for example `shop`). `scope` is the
  symptom in a few words; `hypothesis` is your first theory of the fault.
- `read` (inputs: module, args): call one Ansible module to gather evidence. Choose the
  module from the set listed below and fill in its arguments yourself. The facts come
  back tagged with an evidence id. Read as many times as you need, letting each result
  decide what to read next.
- `conclude` (inputs: finding, cited_evidence): when the evidence confirms a fault.
  `finding` names the specific failing entity you found; `cited_evidence` is the evidence
  ids you relied on. You MUST call this to record a verdict; a diagnosis written as text
  alone records nothing.
- `inconclusive` (inputs: finding): when you cannot confirm a fault, because nothing is
  wrong or because the reads you needed could not run. State what you checked. Never
  invent a fault the evidence does not show.
- After a verdict you may `recall(evidence_id)` to pull a specific reading back up.

You decide the verdict from the facts; the system does not judge them for you. It checks
only that a conclusion cites a read that actually ran. Diagnose from the evidence, not
from the incident wording; name the specific failing entity the facts show.

Entry: state the single target and scope you propose and ask the operator to confirm. Do
not call `step` until they confirm. When they confirm, `triage`, then `read` what you
need, and finish with `conclude` or `inconclusive`. After the verdict is recorded, give a
one-paragraph plain-English summary that cites the evidence.
"""


def _surface_note(surface: Surface) -> str:
    mods = "\n".join(f"  - {m}" for m in surface.modules)
    note = f"\n\nRead-only modules you may call on the {surface.name} surface:\n{mods}"
    if surface.guidance:
        note += f"\n{surface.guidance}"
    return note


def build_model() -> OpenAIChatModel:
    """Any OpenAI-compatible endpoint. OPENAI_API_KEY is required; OPENAI_BASE_URL
    (read by the OpenAI client) points at a non-OpenAI vendor; SEMLEY_MODEL picks
    the model. Defaults target OpenAI directly."""
    load_dotenv()
    provider = OpenAIProvider(api_key=os.environ["OPENAI_API_KEY"])
    return OpenAIChatModel(
        os.environ.get("SEMLEY_MODEL", DEFAULT_MODEL), provider=provider
    )


def _only_governed(_ctx: Any, tool_def: Any) -> bool:
    return tool_def.name in ALLOWED_TOOLS


def build_agent(server, surface: Surface) -> Agent:
    toolset = MCPToolset(server).filtered(_only_governed)
    return Agent(
        build_model(),
        instructions=INSTRUCTIONS + _surface_note(surface),
        toolsets=[toolset],
    )


def usage_limits() -> UsageLimits:
    return UsageLimits(request_limit=40, tool_calls_limit=30)


def investigate(surface_name: str, incident: str) -> dict[str, Any]:
    """Headless API: run one incident end to end, return the final investigation state."""
    surface: Surface = SURFACES[surface_name]
    server, _upstream, persister = mount_surface(surface)
    agent = build_agent(server, surface)
    if surface.plane == "control":
        names = cluster_namespaces()
        targetables = f"Namespaces (the target is one of these, exactly): {', '.join(names) or '(none)'}"
    else:
        hosts = ", ".join(name for name, _ in surface.targets()) or "(none)"
        targetables = f"Inventory hosts: {hosts}"
    prompt = (
        f"{targetables}. Incident: {incident}\n"
        "Proceed without asking for confirmation: pick the target and scope yourself and "
        "drive the investigation to a conclusion."
    )
    agent.run_sync(prompt, usage_limits=usage_limits())
    return load_prior_state(persister, surface.name) or {}
