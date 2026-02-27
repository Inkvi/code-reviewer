from __future__ import annotations

import json
import os
from pathlib import Path

from pr_reviewer.models import ProcessedState


class StateStore:
    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path
        self.lock_path = Path(f"{state_path}.lock")
        self._data: dict[str, dict[str, str | None]] = {}
        self._owns_lock = False

    def _read_lock_pid(self) -> int | None:
        if not self.lock_path.exists():
            return None
        raw = self.lock_path.read_text(encoding="utf-8").strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    def _is_pid_running(self, pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def acquire_lock(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        for _ in range(2):
            try:
                fd = os.open(self.lock_path, flags)
            except FileExistsError as exc:
                lock_pid = self._read_lock_pid()
                if lock_pid is not None and self._is_pid_running(lock_pid):
                    raise RuntimeError(
                        f"Another daemon appears active (pid {lock_pid}): {self.lock_path}"
                    ) from exc
                try:
                    self.lock_path.unlink(missing_ok=True)
                except OSError as unlink_exc:
                    raise RuntimeError(
                        f"Unable to clear stale lock file: {self.lock_path}"
                    ) from unlink_exc
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(f"{os.getpid()}\n")
            self._owns_lock = True
            return
        raise RuntimeError(f"Unable to acquire lock: {self.lock_path}")

    def release_lock(self) -> None:
        if not self._owns_lock:
            return
        lock_pid = self._read_lock_pid()
        if lock_pid is None or lock_pid == os.getpid():
            self.lock_path.unlink(missing_ok=True)
        self._owns_lock = False

    def load(self) -> None:
        if not self.state_path.exists():
            self._data = {}
            return
        with self.state_path.open("r", encoding="utf-8") as handle:
            parsed = json.load(handle)
        self._data = parsed if isinstance(parsed, dict) else {}

    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(self._data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        tmp_path.replace(self.state_path)

    def get(self, key: str) -> ProcessedState:
        item = self._data.get(key, {})
        return ProcessedState(
            last_reviewed_head_sha=item.get("last_reviewed_head_sha"),
            last_output_file=item.get("last_output_file"),
            last_status=item.get("last_status"),
            last_posted_at=item.get("last_posted_at"),
        )

    def set(self, key: str, state: ProcessedState) -> None:
        self._data[key] = {
            "last_reviewed_head_sha": state.last_reviewed_head_sha,
            "last_output_file": state.last_output_file,
            "last_status": state.last_status,
            "last_posted_at": state.last_posted_at,
        }
