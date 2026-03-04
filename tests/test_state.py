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
            last_processed_at="2026-03-03T00:00:00+00:00",
            last_seen_rerequest_at="2026-03-02T00:00:00+00:00",
            trigger_mode="rerequest_only",
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
    assert result.last_processed_at == "2026-03-03T00:00:00+00:00"
    assert result.last_seen_rerequest_at == "2026-03-02T00:00:00+00:00"
    assert result.trigger_mode == "rerequest_only"
    assert result.last_output_file == "/tmp/out.md"
    assert result.last_status == "generated"


def test_state_loads_legacy_payload_without_new_keys(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(
        (
            "{\n"
            '  "org/repo#1": {\n'
            '    "last_reviewed_head_sha": "abc",\n'
            '    "last_output_file": "/tmp/out.md",\n'
            '    "last_status": "generated",\n'
            '    "last_posted_at": null\n'
            "  }\n"
            "}\n"
        ),
        encoding="utf-8",
    )

    store = StateStore(state_path)
    store.load()
    result = store.get("org/repo#1")

    assert result.last_reviewed_head_sha == "abc"
    assert result.last_processed_at is None
    assert result.last_seen_rerequest_at is None
    assert result.trigger_mode == "rerequest_only"


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
