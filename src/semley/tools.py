"""The tool surface: reflect one surface's curated Ansible modules via rocannon."""

from __future__ import annotations

from rocannon.config import Config
from rocannon.server import create_server

from .surfaces import Surface


def build_upstream(surface: Surface):
    """A rocannon FastMCP server exposing only this surface's modules, read-only."""
    cfg = Config(
        inventories=[surface.inventory],
        modules=surface.modules,
        discovery="static",
    )
    return create_server(cfg)
