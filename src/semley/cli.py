"""Semley command line: pick a governed surface and open the streaming REPL."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import replace
from pathlib import Path

from .repl import console, run_repl
from .surfaces import SURFACES


def _quiet_libraries() -> None:
    """Keep library log noise off the Rich stream; notices print at launch only."""
    logging.basicConfig(level=logging.ERROR)
    for name in (
        "rocannon",
        "ansible_runner",
        "mcp",
        "httpx",
        "httpcore",
        "theodosia",
        "fastmcp",
        "burr",
    ):
        logging.getLogger(name).setLevel(logging.ERROR)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="semley", description="Autonomous SRE investigation agent."
    )
    parser.add_argument(
        "--surface",
        required=True,
        choices=sorted(SURFACES),
        help="the governed plane and its read-only module set (the action space)",
    )
    parser.add_argument(
        "-i",
        "--inventory",
        type=Path,
        help="Ansible inventory to bind (defaults to the surface's own)",
    )
    args = parser.parse_args(argv)

    _quiet_libraries()
    surface = SURFACES[args.surface]
    if args.inventory:
        surface = replace(surface, inventory=args.inventory)
    if not surface.inventory.exists():
        console.print(f"[red]inventory not found:[/red] {surface.inventory}")
        return 1
    try:
        asyncio.run(run_repl(surface))
    except KeyboardInterrupt:
        console.print("\n[dim]interrupted.[/dim]")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
