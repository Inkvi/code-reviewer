import os
from pathlib import Path

import pytest

from pr_reviewer.models import ProcessedState
from pr_reviewer.state import StateStore


def test_state_round_trip(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    store = StateStore(state_path)
    store.load()

    store.set(
        "org/repo#1",
        ProcessedState(
            last_reviewed_head_sha="abc",
            last_output_file="/tmp/out.md",
            last_status="generated",
            last_posted_at=None,
        ),
    )
    store.save()

    other = StateStore(state_path)
    other.load()
    result = other.get("org/repo#1")

    assert result.last_reviewed_head_sha == "abc"
    assert result.last_output_file == "/tmp/out.md"
    assert result.last_status == "generated"


def test_acquire_lock_removes_stale_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_path = tmp_path / "state.json"
    store = StateStore(state_path)
    store.lock_path.parent.mkdir(parents=True, exist_ok=True)
    store.lock_path.write_text("999999\n", encoding="utf-8")
    monkeypatch.setattr(store, "_is_pid_running", lambda _pid: False)

    store.acquire_lock()
    try:
        assert store.lock_path.read_text(encoding="utf-8").strip() == str(os.getpid())
    finally:
        store.release_lock()
    assert not store.lock_path.exists()


def test_acquire_lock_live_pid_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_path = tmp_path / "state.json"
    store = StateStore(state_path)
    store.lock_path.parent.mkdir(parents=True, exist_ok=True)
    store.lock_path.write_text("123\n", encoding="utf-8")
    monkeypatch.setattr(store, "_is_pid_running", lambda _pid: True)

    with pytest.raises(RuntimeError, match="pid 123"):
        store.acquire_lock()
