from __future__ import annotations

import asyncio
import json
import subprocess
import time
from pathlib import Path

# Minimum seconds between consecutive gh CLI calls to avoid GitHub rate limits.
_GH_MIN_INTERVAL = 0.2
_gh_last_call: float = 0.0


def _gh_throttle() -> None:
    global _gh_last_call
    now = time.monotonic()
    wait = _GH_MIN_INTERVAL - (now - _gh_last_call)
    if wait > 0:
        time.sleep(wait)
    _gh_last_call = time.monotonic()


class CommandError(RuntimeError):
    def __init__(self, args: list[str], code: int, stdout: str, stderr: str) -> None:
        self.args_list = args
        self.code = code
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(
            f"command failed ({code}): {' '.join(args)}\nstdout:\n{stdout}\nstderr:\n{stderr}"
        )


def run_command(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: int | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    if args and args[0] == "gh":
        _gh_throttle()
    proc = subprocess.run(
        args,
        cwd=cwd,
        timeout=timeout,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and proc.returncode != 0:
        raise CommandError(args, proc.returncode, proc.stdout, proc.stderr)
    return proc


def run_json(args: list[str], *, cwd: Path | None = None, timeout: int | None = None) -> object:
    proc = run_command(args, cwd=cwd, timeout=timeout)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON from command: {' '.join(args)}") from exc


async def run_command_async(
    args: list[str], *, cwd: Path | None = None, timeout: int | None = None
) -> tuple[int, str, str]:
    if args and args[0] == "gh":
        _gh_throttle()
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise

    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    return proc.returncode, stdout, stderr
