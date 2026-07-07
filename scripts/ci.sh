#!/usr/bin/env bash
# Simple, real quality pass. Gates (must pass): format, lint, tests.
# Advisory (reported, never fails the run): modernization, dead code, complexity.
# Run: make ci   (or: scripts/ci.sh)
set -euo pipefail
cd "$(dirname "$0")/.."

hr() { printf '\n\033[1m── %s ──\033[0m\n' "$1"; }

hr "format (ruff)"
uv run ruff format --check src tests scripts

hr "lint (ruff)"
uv run ruff check src tests scripts

hr "tests (pytest)"
uv run pytest tests/ -q

# --- advisory: informative, not gates ---
# FURB123 (list(x) -> x.copy()) is intentionally ignored: inputs may arrive as a
# tuple from tool-call coercion, and list() is safe where .copy() is not.
hr "modernize (refurb, advisory)"
uvx refurb src --ignore FURB123 || true

hr "dead code (vulture, advisory)"
uvx vulture src --min-confidence 80 || true

hr "complexity + maintainability (radon, advisory)"
uvx radon cc src -a -s 2>/dev/null | tail -1          # whole-codebase average
uvx radon cc src -s -n C || true                      # any function ranked C or worse
uvx radon mi src || true                              # maintainability index per file

printf '\n\033[1;32m✔ gates passed.\033[0m\n'
