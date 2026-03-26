from __future__ import annotations

import json
import os
import platform
import threading
from pathlib import Path

from code_reviewer.models import ProcessedState


class StateStore:
    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path
        self.lock_path = Path(f"{state_path}.lock")
        self._data: dict[str, dict[str, str | None]] = {}
        self._owns_lock = False
        self._mutex = threading.Lock()

    def _read_lock_info(self) -> tuple[int | None, str | None]:
        """Return (pid, hostname) from lock file, or (None, None) if unreadable."""
        if not self.lock_path.exists():
            return None, None
        raw = self.lock_path.read_text(encoding="utf-8").strip()
        if not raw:
            return None, None
        # Format: "pid\nhostname" or legacy "pid"
        parts = raw.split("\n", 1)
        try:
            pid = int(parts[0])
        except ValueError:
            return None, None
        hostname = parts[1].strip() if len(parts) > 1 else None
        return pid, hostname

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

    def _is_lock_holder_alive(self, pid: int, hostname: str | None) -> bool:
        """Check if the lock holder is still alive on this host."""
        if hostname is not None and hostname != platform.node():
            return False
        return self._is_pid_running(pid)

    def acquire_lock(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        for _ in range(2):
            try:
                fd = os.open(self.lock_path, flags)
            except FileExistsError as exc:
                lock_pid, lock_host = self._read_lock_info()
                if lock_pid is not None and self._is_lock_holder_alive(lock_pid, lock_host):
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
                handle.write(f"{os.getpid()}\n{platform.node()}\n")
            self._owns_lock = True
            return
        raise RuntimeError(f"Unable to acquire lock: {self.lock_path}")

    def release_lock(self) -> None:
        if not self._owns_lock:
            return
        lock_pid, _ = self._read_lock_info()
        if lock_pid is None or lock_pid == os.getpid():
            self.lock_path.unlink(missing_ok=True)
        self._owns_lock = False

    def load(self) -> None:
        with self._mutex:
            if not self.state_path.exists():
                self._data = {}
                return
            with self.state_path.open("r", encoding="utf-8") as handle:
                parsed = json.load(handle)
            self._data = parsed if isinstance(parsed, dict) else {}

    def save(self) -> None:
        with self._mutex:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.state_path.with_suffix(f".tmp.{os.getpid()}.{threading.get_ident()}")
            with tmp_path.open("w", encoding="utf-8") as handle:
                json.dump(self._data, handle, indent=2, sort_keys=True)
                handle.write("\n")
            tmp_path.replace(self.state_path)

    def get(self, key: str) -> ProcessedState:
        with self._mutex:
            item = self._data.get(key, {})
        _raw_cmd_id = item.get("last_slash_command_id")
        return ProcessedState(
            last_reviewed_head_sha=item.get("last_reviewed_head_sha"),
            last_processed_at=item.get("last_processed_at"),
            last_seen_rerequest_at=item.get("last_seen_rerequest_at"),
            trigger_mode=item.get("trigger_mode") or "rerequest_only",
            last_output_file=item.get("last_output_file"),
            last_status=item.get("last_status"),
            last_posted_at=item.get("last_posted_at"),
            last_slash_command_id=int(_raw_cmd_id) if _raw_cmd_id is not None else None,
        )

    def set(self, key: str, state: ProcessedState) -> None:
        with self._mutex:
            self._data[key] = {
                "last_reviewed_head_sha": state.last_reviewed_head_sha,
                "last_processed_at": state.last_processed_at,
                "last_seen_rerequest_at": state.last_seen_rerequest_at,
                "trigger_mode": state.trigger_mode,
                "last_output_file": state.last_output_file,
                "last_status": state.last_status,
                "last_posted_at": state.last_posted_at,
                "last_slash_command_id": state.last_slash_command_id,
            }
