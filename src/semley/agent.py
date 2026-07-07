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
from .surfaces import SURFACES, Surface

ALLOWED_TOOLS = {"step", "reset_session"}
DEFAULT_MODEL = "gpt-5.4"

INSTRUCTIONS = """\
You are Semley, an autonomous SRE investigation agent. You diagnose a fault on a
running system by driving a governed state machine through one tool: `step`.

You do not run commands or name modules. You call `step(action, inputs)`. The state
machine decides which reads run and returns the valid next actions after every call;
if you request an unreachable action it refuses and lists what is valid, so correct
course and continue.

The loop:
- `triage` (inputs: target, scope): fix what you investigate. The `target` MUST be one
  of the hosts in the inventory shown to you; `scope` is a short free-text note
  (a namespace for a cluster surface, otherwise the symptom). This elects the first
  hypothesis.
- `investigate`: gathers the current hypothesis's reads and returns the raw facts for
  each one, tagged with an evidence id. READ those facts and judge for yourself.
- Then, based on what the facts show:
  - `conclude` (inputs: finding, cited_evidence): when the facts confirm the current
    hypothesis. `finding` is your one-line diagnosis naming the specific failing entity
    you found in the facts; `cited_evidence` is the list of evidence ids you relied on.
  - `refute` (inputs: finding, cited_evidence): when the facts rule the current
    hypothesis out. This elects the next hypothesis (or exhausts the space).
  - `gather`: to collect more evidence for the same hypothesis.
- `exhausted` terminates honestly when no hypothesis remains: report what was ruled out.
- After a conclusion you may `recall(evidence_id)` to pull a specific reading back up.

You decide the verdict from the facts; the system does not judge them for you. It only
checks that a conclusion or refutation cites evidence that actually ran, so cite the ids
you used. Investigate honestly: diagnose from the evidence, not the incident wording;
name the specific failing entity the facts show; never report a fault the facts did not
show. A read that could not run is recorded uninvestigable, never a refutation.

Entry: given the operator's incident and the inventory, first state the single target
and scope you propose and ask the operator to confirm. Do not call `step` until they
confirm. Once confirmed, drive the loop to a conclusion, then give a one-paragraph
plain-English diagnosis citing the evidence.
"""


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


def build_agent(server) -> Agent:
    toolset = MCPToolset(server).filtered(_only_governed)
    return Agent(build_model(), instructions=INSTRUCTIONS, toolsets=[toolset])


def usage_limits() -> UsageLimits:
    return UsageLimits(request_limit=40, tool_calls_limit=30)


def investigate(surface_name: str, incident: str) -> dict[str, Any]:
    """Headless API: run one incident end to end, return the final investigation state."""
    surface: Surface = SURFACES[surface_name]
    server, _upstream, persister = mount_surface(surface)
    agent = build_agent(server)
    hosts = ", ".join(name for name, _ in surface.targets()) or "(none)"
    prompt = (
        f"Inventory hosts: {hosts}. Incident: {incident}\n"
        "Proceed without asking for confirmation: pick the target and scope yourself and "
        "drive the investigation to a conclusion."
    )
    agent.run_sync(prompt, usage_limits=usage_limits())
    return load_prior_state(persister, surface.name) or {}
