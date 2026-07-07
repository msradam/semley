"""Auditability: the deterministic playbook of the real Ansible calls an
investigation made. Rocannon records every reflected-module call; committing the
session filters to successful reads and writes a standard playbook, credentials
redacted at recording. The same session yields a byte-identical playbook.
"""

from __future__ import annotations

from typing import Any

from fastmcp import Client


async def commit_playbook(upstream_server, name: str, description: str = "") -> dict[str, Any]:
    """Write the recorded reads as an Ansible playbook; return rocannon's report."""
    async with Client(upstream_server) as client:
        res = await client.call_tool(
            "commit_session",
            {"name": name, "description": description, "overwrite": True},
        )
        return res.structured_content or res.data
