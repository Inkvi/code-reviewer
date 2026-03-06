import asyncio
import subprocess
import time
from unittest.mock import patch

import pytest

from code_reviewer.shell import CommandError, run_command, run_command_async, run_json


def test_command_error_attributes() -> None:
    err = CommandError(["gh", "api"], 1, "out", "err")
    assert err.args_list == ["gh", "api"]
    assert err.code == 1
    assert err.stdout == "out"
    assert err.stderr == "err"
    assert "command failed (1)" in str(err)


def test_run_command_success() -> None:
    result = run_command(["echo", "hello"])
    assert result.returncode == 0
    assert result.stdout.strip() == "hello"


def test_run_command_failure_raises_command_error() -> None:
    with pytest.raises(CommandError) as exc_info:
        run_command(["false"])
    assert exc_info.value.code != 0


def test_run_command_check_false_returns_result() -> None:
    result = run_command(["false"], check=False)
    assert result.returncode != 0


def test_run_command_retries_on_failure() -> None:
    call_count = 0
    original_run = subprocess.run

    def counting_run(args, **kwargs):  # noqa: ANN001
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="fail")
        return original_run(args, **kwargs)

    with patch("code_reviewer.shell.subprocess.run", side_effect=counting_run):
        with patch("code_reviewer.shell.time.sleep"):
            result = run_command(["echo", "ok"], retries=3)

    assert result.returncode == 0
    assert call_count == 3


def test_run_command_max_retries_exhausted() -> None:
    def always_fail(args, **kwargs):  # noqa: ANN001
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="fail")

    with patch("code_reviewer.shell.subprocess.run", side_effect=always_fail):
        with patch("code_reviewer.shell.time.sleep"):
            with pytest.raises(CommandError):
                run_command(["gh", "api"], retries=2)


def test_run_json_success() -> None:
    with patch("code_reviewer.shell.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["echo"], returncode=0, stdout='{"key": "value"}', stderr=""
        )
        result = run_json(["echo"])
    assert result == {"key": "value"}


def test_run_json_malformed_raises_runtime_error() -> None:
    with patch("code_reviewer.shell.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["echo"], returncode=0, stdout="not json", stderr=""
        )
        with pytest.raises(RuntimeError, match="Invalid JSON"):
            run_json(["echo"])


def test_run_command_async_success() -> None:
    code, stdout, stderr = asyncio.run(run_command_async(["echo", "async_ok"]))
    assert code == 0
    assert "async_ok" in stdout


def test_run_command_async_timeout() -> None:
    with pytest.raises(TimeoutError):
        asyncio.run(run_command_async(["sleep", "10"], timeout=0.1))


def test_gh_throttle_enforces_minimum_interval() -> None:
    import code_reviewer.shell as shell_mod

    shell_mod._gh_last_call = 0.0

    with patch("code_reviewer.shell.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["gh"], returncode=0, stdout="", stderr=""
        )
        start = time.monotonic()
        run_command(["gh", "version"])
        run_command(["gh", "version"])
        elapsed = time.monotonic() - start

    assert elapsed >= shell_mod._GH_MIN_INTERVAL
