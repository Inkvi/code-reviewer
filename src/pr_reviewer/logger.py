from __future__ import annotations

from datetime import UTC, datetime

from rich.console import Console

console = Console()


def redirect_to_stderr() -> None:
    """Switch all log output to stderr, keeping stdout clean for structured output."""
    global console  # noqa: PLW0603
    console = Console(stderr=True)


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ")


def info(message: str) -> None:
    console.print(f"[{_stamp()}] [cyan]INFO[/cyan] {message}")


def warn(message: str) -> None:
    console.print(f"[{_stamp()}] [yellow]WARN[/yellow] {message}")


def error(message: str) -> None:
    console.print(f"[{_stamp()}] [red]ERROR[/red] {message}")
