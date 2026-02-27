from pathlib import Path

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
