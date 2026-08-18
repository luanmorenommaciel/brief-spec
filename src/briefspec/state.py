from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from briefspec.config import briefspec_home, legacy_briefspec_home
from briefspec.models import Runtime, SessionState


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    with suppress(OSError):
        path.chmod(0o700)


def _atomic_write(path: Path, content: bytes, mode: int, *, private_parent: bool) -> None:
    if private_parent:
        _private_dir(path.parent)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(raw_temp)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if not hasattr(os, "fchmod"):
            with suppress(OSError):
                temp_path.chmod(mode)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def atomic_write(path: Path, content: bytes, mode: int = 0o600) -> None:
    _atomic_write(path, content, mode, private_parent=True)


def atomic_write_public(path: Path, content: bytes, mode: int = 0o644) -> None:
    """Atomically write an artifact without changing an existing parent directory's mode."""
    _atomic_write(path, content, mode, private_parent=False)


def atomic_write_many(files: list[tuple[Path, bytes, int]]) -> None:
    """Commit public files as one rollback-capable transaction."""
    snapshots: dict[Path, tuple[bytes, int] | None] = {}
    for path, _, _ in files:
        snapshots[path] = (
            (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None
        )
    written: list[Path] = []
    try:
        for path, content, mode in files:
            atomic_write_public(path, content, mode)
            written.append(path)
    except Exception as exc:
        rollback_errors: list[str] = []
        for path in reversed(written):
            snapshot = snapshots[path]
            try:
                if snapshot is None:
                    path.unlink(missing_ok=True)
                else:
                    content, mode = snapshot
                    atomic_write_public(path, content, mode)
            except OSError as rollback_exc:
                rollback_errors.append(f"{path}: {rollback_exc}")
        if rollback_errors:
            exc.add_note("Brief-Spec output rollback also failed: " + "; ".join(rollback_errors))
        raise


def _session_key(runtime: Runtime, session_id: str) -> str:
    raw = f"{runtime.value}\0{session_id}".encode()
    return hashlib.sha256(raw).hexdigest()


def session_path(runtime: Runtime, session_id: str) -> Path:
    return briefspec_home() / "sessions" / _session_key(runtime, session_id) / "state.json"


def _legacy_session_path(runtime: Runtime, session_id: str) -> Path:
    return legacy_briefspec_home() / "sessions" / _session_key(runtime, session_id) / "state.json"


@contextmanager
def session_lock(runtime: Runtime, session_id: str, timeout: float = 1.5) -> Iterator[None]:
    lock_dir = briefspec_home() / "locks"
    _private_dir(lock_dir)
    lock_path = lock_dir / f"{_session_key(runtime, session_id)}.lock"
    deadline = time.monotonic() + timeout
    while True:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(descriptor, str(os.getpid()).encode())
            os.close(descriptor)
            break
        except FileExistsError:
            try:
                if time.time() - lock_path.stat().st_mtime > 30:
                    lock_path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out acquiring session lock: {lock_path.name}") from None
            time.sleep(0.025)
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def load_session(runtime: Runtime, session_id: str, now: datetime) -> SessionState:
    path = session_path(runtime, session_id)
    if not path.exists():
        legacy = _legacy_session_path(runtime, session_id)
        if legacy != path and legacy.exists():
            path = legacy
    if not path.exists():
        return SessionState.new(runtime, session_id, now)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("state root is not an object")
        return SessionState.from_dict(value)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        quarantine = path.with_name(f"state.corrupt.{int(time.time())}.json")
        with suppress(OSError):
            path.replace(quarantine)
        return SessionState.new(runtime, session_id, now)


def save_session(state: SessionState) -> None:
    content = json.dumps(state.to_dict(), indent=2, sort_keys=True).encode() + b"\n"
    atomic_write(session_path(Runtime(state.runtime), state.session_id), content)


def list_sessions() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    roots = dict.fromkeys((briefspec_home() / "sessions", legacy_briefspec_home() / "sessions"))
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.glob("*/state.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(value, dict):
                    continue
                key = (str(value.get("runtime")), str(value.get("session_id")))
                if key not in seen:
                    results.append(value)
                    seen.add(key)
            except (OSError, json.JSONDecodeError):
                continue
    return results


def reset_session(runtime: Runtime, session_id: str) -> bool:
    removed = False
    for path in dict.fromkeys(
        (session_path(runtime, session_id), _legacy_session_path(runtime, session_id))
    ):
        if path.exists():
            path.unlink()
            removed = True
            with suppress(OSError):
                path.parent.rmdir()
    return removed


def prune_sessions(days: int, dry_run: bool = False, now: datetime | None = None) -> list[Path]:
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
    removed: list[Path] = []
    roots = dict.fromkeys((briefspec_home() / "sessions", legacy_briefspec_home() / "sessions"))
    for root in roots:
        if not root.exists():
            continue
        for path in root.glob("*/state.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                updated = datetime.fromisoformat(str(value["updated_at"]))
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                updated = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            if updated < cutoff:
                removed.append(path)
                if not dry_run:
                    path.unlink(missing_ok=True)
                    with suppress(OSError):
                        path.parent.rmdir()
    return removed
