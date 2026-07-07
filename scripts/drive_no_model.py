"""Drive the mounted host surface directly, no model, against the real target.

Verifies governance: the model-driven `read` (module + args) through rocannon, the
action-space boundary, grounded conclusions, recall, and refusals. Standing in for the
model, this script chooses which modules to read, then supplies its own finding and
citations; the mount checks the module and the citations, never the finding. Run with
the fault injected (scripts/inject-fault.sh).
"""

from __future__ import annotations

import asyncio

from fastmcp import Client

from semley.mount import load_prior_state, mount_surface
from semley.surfaces import SURFACES


def _payload(result):
    return result.structured_content or result.data


async def step(client, action, **inputs):
    res = await client.call_tool("step", {"action": action, "inputs": inputs})
    return _payload(res)


async def main():
    surface = SURFACES["host"]
    server, _upstream, persister = mount_surface(surface)

    async with Client(server) as client:
        tools = sorted(t.name for t in await client.list_tools())
        print("mounted tools:", tools)

        r = await step(
            client,
            "triage",
            target="web1",
            scope="web service not responding",
            hypothesis="the web service unit has stopped",
        )
        print("triage ->", r["result"]["target"], "| modules:", r["result"]["modules"])

        # The model chooses which module to read and its arguments.
        ids = []
        for module in (
            "ansible.builtin.service_facts",
            "ansible.builtin.listen_ports_facts",
        ):
            r = await step(client, "read", module=module, args={})
            g = r["result"]["gathered"]
            ids += [x["id"] for x in g]
            print("read ->", [f"{x['id']}:{x['module']}" for x in g])

        # Action-space boundary: a module off the surface is refused, not executed.
        bad_mod = await step(client, "read", module="ansible.builtin.command", args={})
        assert "error" in bad_mod, bad_mod
        print("refuse read off-surface ->", bad_mod["error"])

        # Grounding: a verdict with no citation is refused before it is written.
        no_cite = await step(client, "conclude", finding="the service is down")
        assert "error" in no_cite, no_cite
        print("refuse conclude without citation ->", no_cite["error"])

        # A grounded conclusion (the script read the facts and decided) is written.
        r = await step(
            client,
            "conclude",
            finding="the web service unit is stopped",
            cited_evidence=ids,
        )
        assert r["result"]["outcome"] == "confirmed", r
        print("conclude ->", r["result"]["conclusion"])

        rc = await step(client, "recall", evidence_id=ids[0])
        assert rc["result"]["evidence"]["id"] == ids[0]
        print("recall ->", rc["result"]["evidence"]["module"])
        bad_recall = await step(client, "recall", evidence_id="e99")
        assert "error" in bad_recall
        print("refuse recall e99 ->", bad_recall["error"])

    state = load_prior_state(persister, surface.name)
    print(
        "persisted phase:",
        state["phase"],
        "| outcome:",
        state["outcome"],
        "| evidence:",
        len(state["evidence"]),
    )


if __name__ == "__main__":
    asyncio.run(main())
