"""The streaming REPL: a branded banner, natural-language infer-and-confirm entry,
and a live view of each governed step and each real Ansible read as it happens.

Memory is the state store, not the transcript. Within one investigation the model
conversation is threaded; at a new incident the conversation resets and a compact
findings digest rendered from persisted state is prepended, so context stays flat.
"""

from __future__ import annotations

import json
import os
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.messages import FunctionToolCallEvent, FunctionToolResultEvent
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .agent import build_agent, usage_limits
from .audit import commit_playbook
from .mount import load_incident_history, mount_surface
from .state import render_digest
from .surfaces import Surface

console = Console()

_LOGO = r"""      ┓
┏┏┓┏┳┓┃┏┓┓┏
┛┗ ┛┗┗┗┗ ┗┫
          ┛"""

_OUTCOME_STYLE = {"confirmed": "red", "all_clear": "green", "inconclusive": "yellow"}
_COMMANDS = ["/playbook", "/help", "/quit", "/exit"]


def _setup_completion() -> None:
    """Tab-completion for the REPL slash commands."""
    try:
        import readline
    except ImportError:
        return

    def completer(text: str, state: int):
        matches = [c for c in _COMMANDS if c.startswith(text)]
        return matches[state] if state < len(matches) else None

    readline.set_completer(completer)
    bind = (
        "bind ^I rl_complete"
        if "libedit" in (readline.__doc__ or "")
        else "tab: complete"
    )
    readline.parse_and_bind(bind)


def banner(surface: Surface) -> None:
    keyed = (
        "HMAC-keyed" if os.environ.get("THEODOSIA_LEDGER_KEY") else "unkeyed SHA-256"
    )
    body = (
        f"[bold]surface[/bold] {surface.name}  [dim]/[/dim]  [bold]plane[/bold] {surface.plane}\n"
        f"[green]{surface.invariant}[/green]\n"
        f"[dim]ledger: {keyed}; every step and refusal is hash-chained.[/dim]"
    )
    console.print(f"[bold cyan]{_LOGO}[/bold cyan]")
    console.print("[dim]autonomous SRE investigation agent[/dim]\n")
    console.print(body)
    if surface.plane == "control":
        _print_namespaces()
    else:
        _print_hosts(surface)
    console.print(
        "[dim]describe an incident · /help for commands · /quit to exit.[/dim]\n"
    )


def _print_hosts(surface: Surface) -> None:
    table = Table(show_header=True, header_style="bold cyan", box=None, pad_edge=False)
    table.add_column("host", style="bold")
    table.add_column("connection", style="dim")
    for name, detail in surface.targets():
        table.add_row(name, detail)
    console.print(
        f"\n[bold]inventory[/bold] [dim]{surface.inventory}[/dim] "
        "[dim]— name one of these hosts in your incident:[/dim]"
    )
    console.print(table)


def _print_namespaces() -> None:
    import subprocess

    names: list[str] = []
    try:
        out = subprocess.run(
            ["kubectl", "get", "namespaces", "-o", "name"],
            capture_output=True,
            text=True,
            timeout=6,
        )
        names = [ln.split("/", 1)[-1] for ln in out.stdout.splitlines() if ln.strip()]
    except Exception:
        pass
    if names:
        console.print(
            "\n[bold]namespaces[/bold] [dim](kubectl current-context) "
            "— name one in your incident:[/dim]"
        )
        console.print("  " + "  ".join(f"[cyan]{n}[/cyan]" for n in names))
    else:
        console.print(
            "\n[dim]namespaces: could not list; name one in your incident (e.g. 'shop').[/dim]"
        )


def _content_to_obj(content: Any) -> Any:
    if isinstance(content, str):
        try:
            return json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return content
    if isinstance(content, list):
        for block in content:
            text = getattr(block, "text", None)
            if text:
                return _content_to_obj(text)
    return content


def _render_step_call(part: Any) -> None:
    args = part.args
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (json.JSONDecodeError, TypeError):
            args = {}
    action = (args or {}).get("action", "?")
    inputs = (args or {}).get("inputs") or {}
    detail = (
        " ".join(f"{k}={v}" for k, v in inputs.items())
        if isinstance(inputs, dict)
        else ""
    )
    console.print(
        f"  [bold magenta]▸ step[/bold magenta] [cyan]{action}[/cyan] [dim]{detail}[/dim]"
    )


def _render_step_result(obj: Any) -> None:
    if not isinstance(obj, dict):
        return
    if "error" in obj:
        valid = ", ".join(obj.get("valid_next_actions") or [])
        console.print(
            f"    [yellow]refused[/yellow] [dim]{obj['error']}; valid: {valid}[/dim]"
        )
        return
    result = obj.get("result") or {}
    for read in result.get("gathered", []):
        if not isinstance(read, dict):
            console.print(f"    [green]read[/green] [dim]{read}[/dim]")
            continue
        args = read.get("args") or {}
        argstr = " ".join(f"{k}={v}" for k, v in args.items())
        console.print(
            f"    [green]read[/green] [cyan]{read.get('id')}[/cyan] "
            f"[bold]{read.get('module')}[/bold]"
            + (f" [magenta]{argstr}[/magenta]" if argstr else "")
            + f" [dim]on {read.get('target')}[/dim]"
        )
    for miss in result.get("uninvestigable", []):
        console.print(
            f"    [yellow]uninvestigable[/yellow] [dim]{miss} (read did not dispatch)[/dim]"
        )
    if result.get("conclusion"):
        console.print(f"    [bold]conclusion:[/bold] {result['conclusion']}")
    if result.get("ruled_out"):
        console.print(
            f"    [dim]ruled out {result['ruled_out']}: {result.get('finding', '')}[/dim]"
        )
    if "evidence" in result and isinstance(result["evidence"], dict):
        console.print(
            f"    [green]recall[/green] [dim]{result['evidence'].get('module')} "
            f"on {result['evidence'].get('target')}[/dim]"
        )


async def _stream_turn(agent: Agent, prompt: str, history: list | None):
    """Stream one model turn; return (result, final_state) where final_state is the
    state from this turn's last successful step, or None if no step ran this turn."""
    text_buf: list[str] = []
    final_state: dict[str, Any] | None = None
    async with agent.iter(
        prompt, message_history=history, usage_limits=usage_limits()
    ) as run:
        async for node in run:
            if Agent.is_model_request_node(node):
                async with node.stream(run.ctx) as stream:
                    async for ev in stream:
                        delta = getattr(
                            getattr(ev, "delta", None), "content_delta", None
                        )
                        if isinstance(delta, str):
                            text_buf.append(delta)
            elif Agent.is_call_tools_node(node):
                async with node.stream(run.ctx) as stream:
                    async for ev in stream:
                        if isinstance(ev, FunctionToolCallEvent):
                            _render_step_call(ev.part)
                        elif isinstance(ev, FunctionToolResultEvent):
                            obj = _content_to_obj(ev.part.content)
                            _render_step_result(obj)
                            if (
                                isinstance(obj, dict)
                                and "error" not in obj
                                and obj.get("state")
                            ):
                                final_state = obj["state"]
    if text_buf:
        console.print(f"[white]{''.join(text_buf).strip()}[/white]")
    return run.result, final_state


def _show_conclusion(state: dict[str, Any]) -> None:
    outcome = state.get("outcome") or "inconclusive"
    style = _OUTCOME_STYLE.get(outcome, "yellow")
    console.print(
        Panel(
            state.get("conclusion") or "(no conclusion)",
            title=f"[bold]{outcome}[/bold]",
            border_style=style,
        )
    )
    evidence = state.get("evidence") or []
    if evidence:
        table = Table(title="evidence", header_style="bold cyan", title_justify="left")
        table.add_column("id")
        table.add_column("read")
        table.add_column("target")
        table.add_column("cited", justify="center")
        cited = set(state.get("citations") or [])
        for e in evidence:
            table.add_row(
                e["id"], e["module"], e["target"], "*" if e["id"] in cited else ""
            )
        console.print(table)
    console.print()


async def run_repl(surface: Surface) -> None:
    server, upstream, persister = mount_surface(surface)
    agent = build_agent(server)
    banner(surface)

    history: list | None = None
    digest = render_digest(_digest_incidents(persister, surface.name))
    _setup_completion()

    while True:
        try:
            user = console.input("[bold cyan]semley[/bold cyan] [dim]›[/dim] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye.[/dim]")
            return
        if not user:
            continue
        if user in {"/quit", "/exit", "quit", "exit", ":q"}:
            console.print("[dim]bye.[/dim]")
            return
        if user in {"/help", "help", "?"}:
            console.print(
                "[dim]describe an incident in plain language to investigate it · "
                "/playbook saves the recorded Ansible playbook · /quit exits[/dim]\n"
            )
            continue
        if user == "/playbook":
            report = await commit_playbook(upstream, f"{surface.name}_investigation")
            if report.get("ok"):
                console.print(
                    f"  [green]playbook saved[/green] [dim]{report['path']} "
                    f"({report['steps']} tasks, credentials redacted)[/dim]\n"
                )
            else:
                console.print(
                    f"  [yellow]no playbook: {report.get('error')}[/yellow]\n"
                )
            continue

        prompt = f"{digest}\n\n{user}" if (history is None and digest) else user
        result, state = await _stream_turn(agent, prompt, history)
        history = result.all_messages()

        if state and state.get("phase") in {"concluded", "exhausted"}:
            _show_conclusion(state)
            console.print(
                "[dim]/playbook saves the recorded Ansible playbook · "
                "describe the next incident (prior findings carry forward)[/dim]\n"
            )
            history = None
            digest = render_digest(_digest_incidents(persister, surface.name))


def _digest_incidents(persister, partition_key: str) -> list[dict[str, Any]]:
    return [
        {
            "target": s.get("target", "?"),
            "outcome": s.get("outcome", "?"),
            "finding": (s.get("conclusion") or "").split(": ", 1)[-1],
        }
        for s in load_incident_history(persister, partition_key)
    ]
