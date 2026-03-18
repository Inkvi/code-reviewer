from __future__ import annotations

import asyncio
import json
import subprocess
import threading
import time
from pathlib import Path

# Minimum seconds between consecutive gh CLI calls to avoid GitHub rate limits.
_GH_MIN_INTERVAL = 0.2
_gh_last_call: float = 0.0
_gh_lock = threading.Lock()


def _gh_throttle() -> None:
    global _gh_last_call
    with _gh_lock:
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
    retries: int = 0,
) -> subprocess.CompletedProcess[str]:
    last_proc: subprocess.CompletedProcess[str] | None = None
    for attempt in range(1 + retries):
        if args and args[0] == "gh":
            _gh_throttle()
        last_proc = subprocess.run(
            args,
            cwd=cwd,
            timeout=timeout,
            check=False,
            capture_output=True,
            text=True,
        )
        if last_proc.returncode == 0:
            return last_proc
        if attempt < retries:
            time.sleep(2**attempt)
    assert last_proc is not None
    if check:
        raise CommandError(args, last_proc.returncode, last_proc.stdout, last_proc.stderr)
    return last_proc


def run_json(args: list[str], *, cwd: Path | None = None, timeout: int | None = None) -> object:
    proc = run_command(args, cwd=cwd, timeout=timeout)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON from command: {' '.join(args)}") from exc


async def run_command_async(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: int | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    if args and args[0] == "gh":
        _gh_throttle()
    merged_env = None
    if env is not None:
        import os

        merged_env = {**os.environ, **env}
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=merged_env,
    )

    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    except BaseException:
        # Kill subprocess on task cancellation or any other exception.
        proc.kill()
        await proc.wait()
        raise

    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    return proc.returncode, stdout, stderr
