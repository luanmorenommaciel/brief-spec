from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from briefspec.artifacts import canonical_json_bytes, sha256_bytes
from briefspec.config import briefspec_home
from briefspec.events import prepare_event, validate_event
from briefspec.state import atomic_write

ZERO_HASH = "0" * 64


def chronicle_home() -> Path:
    return briefspec_home() / "chronicles"


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    with suppress(OSError):
        path.chmod(0o700)


def _registry_path() -> Path:
    return chronicle_home() / "registry.json"


def _load_registry() -> dict[str, Any]:
    try:
        value = json.loads(_registry_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": "brief-spec-chronicle-registry/1.0", "projects": []}
    if not isinstance(value, dict) or not isinstance(value.get("projects"), list):
        return {"schema_version": "brief-spec-chronicle-registry/1.0", "projects": []}
    return value


def _save_registry(registry: dict[str, Any]) -> None:
    atomic_write(
        _registry_path(),
        json.dumps(registry, indent=2, sort_keys=True).encode() + b"\n",
    )


def upsert_registry_record(record: dict[str, Any]) -> None:
    project_id = str(record["project_id"])
    private_root = str(record["private_root"])
    with project_lock("registry"):
        registry = _load_registry()
        for item in registry["projects"]:
            if item.get("project_id") == project_id and item.get("private_root") != private_root:
                raise ValueError(f"Chronicle project ID is already registered: {project_id}")
            if item.get("private_root") == private_root and item.get("project_id") != project_id:
                raise ValueError(f"Project root is already registered: {private_root}")
        projects = [item for item in registry["projects"] if item.get("project_id") != project_id]
        projects.append(
            {
                "project_id": project_id,
                "name": record["name"],
                "private_root": private_root,
                "root_sha256": record["root_sha256"],
            }
        )
        registry["projects"] = sorted(projects, key=lambda item: item["project_id"])
        _save_registry(registry)


def _canonical_root(project: Path) -> Path:
    root = project.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"Project is not a directory: {root}")
    return root


def _project_id(root: Path) -> str:
    return f"bscp-{sha256_bytes(str(root).encode())[:24]}"


def project_dir(project_id: str) -> Path:
    if not project_id.startswith("bscp-") or len(project_id) != 29:
        raise ValueError("Invalid Chronicle project ID")
    path = chronicle_home() / project_id
    if path.parent != chronicle_home():
        raise ValueError("Chronicle project path escaped its state root")
    return path


def init_project(
    project: Path, *, name: str | None = None, now: str | None = None
) -> dict[str, Any]:
    root = _canonical_root(project)
    registered = next(
        (item for item in _load_registry()["projects"] if item.get("private_root") == str(root)),
        None,
    )
    if registered is not None:
        existing_path = project_dir(str(registered["project_id"])) / "project.json"
        if not existing_path.is_file():
            raise ValueError("Chronicle registry points to missing project state; run doctor")
        return json.loads(existing_path.read_text(encoding="utf-8"))
    project_id = _project_id(root)
    created_at = now or datetime.now(UTC).isoformat()
    record = {
        "schema_version": "brief-spec-chronicle-project/1.0",
        "project_id": project_id,
        "name": name or root.name,
        "private_root": str(root),
        "root_sha256": sha256_bytes(str(root).encode()),
        "created_at": created_at,
        "retention": "project-lifetime",
        "capture": "explicit-project-init",
    }
    target = project_dir(project_id)
    _private_dir(target)
    project_path = target / "project.json"
    if project_path.exists():
        existing = json.loads(project_path.read_text(encoding="utf-8"))
        if existing.get("private_root") != str(root):
            raise ValueError("Chronicle project ID collision")
        return existing
    atomic_write(project_path, json.dumps(record, indent=2, sort_keys=True).encode() + b"\n")
    upsert_registry_record(record)
    return record


def registered_project(project: Path) -> dict[str, Any]:
    root = _canonical_root(project)
    registered = next(
        (item for item in _load_registry()["projects"] if item.get("private_root") == str(root)),
        None,
    )
    project_id = str(registered["project_id"]) if registered else _project_id(root)
    path = project_dir(project_id) / "project.json"
    if not path.is_file():
        raise ValueError(f"Chronicle is not initialized for {root}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("private_root") != str(root):
        raise ValueError("Chronicle registration does not match the project root")
    return value


@contextmanager
def project_lock(project_id: str, timeout: float = 3.0) -> Iterator[None]:
    locks = chronicle_home() / "locks"
    _private_dir(locks)
    lock = locks / f"{project_id}.lock"
    deadline = time.monotonic() + timeout
    while True:
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(descriptor, str(os.getpid()).encode())
            os.close(descriptor)
            break
        except FileExistsError:
            with suppress(OSError):
                if time.time() - lock.stat().st_mtime > 30:
                    lock.unlink(missing_ok=True)
                    continue
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out acquiring Chronicle lock: {project_id}") from None
            time.sleep(0.025)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def order_events(loaded: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recover ingest order from a valid single-linked event hash chain."""
    by_previous: dict[str, list[dict[str, Any]]] = {}
    for event in loaded:
        by_previous.setdefault(str(event["previous_event_hash"]), []).append(event)
    ordered: list[dict[str, Any]] = []
    previous = ZERO_HASH
    while len(ordered) < len(loaded):
        candidates = by_previous.get(previous, [])
        if len(candidates) != 1:
            break
        event = candidates[0]
        if event in ordered:
            break
        ordered.append(event)
        previous = str(event["event_hash"])
    # Preserve inspectability on corruption: verification will report the malformed raw order.
    return ordered if len(ordered) == len(loaded) else loaded


def iter_events(project_id: str) -> Iterator[dict[str, Any]]:
    root = project_dir(project_id) / "events"
    if not root.exists():
        return
    loaded: list[dict[str, Any]] = []
    for segment in sorted(root.glob("*.ndjson")):
        if segment.is_symlink():
            raise ValueError(f"Chronicle event segment must not be a symlink: {segment.name}")
        with segment.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Corrupt Chronicle segment {segment.name}:{line_number}"
                    ) from exc
                errors = validate_event(value)
                if errors:
                    raise ValueError(
                        f"Invalid Chronicle event {segment.name}:{line_number}: {'; '.join(errors)}"
                    )
                loaded.append(value)
    yield from order_events(loaded)


def verify_chain(events: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    previous = ZERO_HASH
    seen: set[str] = set()
    for index, event in enumerate(events):
        event_id = str(event.get("event_id", ""))
        if event_id in seen:
            errors.append(f"Duplicate event ID at position {index}: {event_id}")
        seen.add(event_id)
        if event.get("previous_event_hash") != previous:
            errors.append(f"Event chain mismatch at position {index}: {event_id}")
        expected = dict(event)
        actual = expected.pop("event_hash", None)
        calculated = sha256_bytes(canonical_json_bytes(expected))
        if actual != calculated:
            errors.append(f"Event hash mismatch at position {index}: {event_id}")
        previous = str(actual)
    return errors


def _connect(project_id: str) -> sqlite3.Connection:
    root = project_dir(project_id)
    _private_dir(root)
    index = root / "index.sqlite3"
    connection = sqlite3.connect(index)
    with suppress(OSError):
        index.chmod(0o600)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS events (
          event_id TEXT PRIMARY KEY,
          occurred_at TEXT NOT NULL,
          kind TEXT NOT NULL,
          headline TEXT NOT NULL,
          event_hash TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS relations (
          relation_id TEXT PRIMARY KEY,
          source_ref TEXT NOT NULL,
          relation TEXT NOT NULL,
          target_ref TEXT NOT NULL,
          event_id TEXT NOT NULL,
          confidence TEXT NOT NULL,
          rule_id TEXT NOT NULL
        );
        """
    )
    for sidecar in (index.with_name(index.name + "-wal"), index.with_name(index.name + "-shm")):
        with suppress(OSError):
            sidecar.chmod(0o600)
    return connection


def _relations(event: dict[str, Any]) -> list[dict[str, str]]:
    details = event.get("details", {})
    explicit = details.get("relations", []) if isinstance(details, dict) else []
    relations: list[dict[str, str]] = []
    for value in explicit if isinstance(explicit, list) else []:
        if not isinstance(value, dict):
            continue
        relation = str(value.get("relation", ""))
        if relation not in {
            "implements",
            "depends_on",
            "supersedes",
            "blocked_by",
            "contradicts",
            "verifies",
            "caused_by",
            "accepted_by",
            "learned_from",
        }:
            continue
        source_ref = str(value.get("source_ref", ""))
        target_ref = str(value.get("target_ref", ""))
        if source_ref and target_ref:
            relations.append(
                {
                    "source_ref": source_ref,
                    "relation": relation,
                    "target_ref": target_ref,
                    "confidence": "high",
                    "rule_id": "explicit.relation",
                }
            )
    method = event.get("method_context", {})
    task_ref = method.get("task_ref") if isinstance(method, dict) else None
    intent_ref = method.get("intent_ref") if isinstance(method, dict) else None
    if event.get("kind") == "TASK_STARTED" and task_ref and intent_ref:
        relations.append(
            {
                "source_ref": str(task_ref),
                "relation": "implements",
                "target_ref": str(intent_ref),
                "confidence": "high",
                "rule_id": "event.task-started-intent",
            }
        )
    if event.get("kind") == "TASK_ACCEPTED" and task_ref:
        relations.append(
            {
                "source_ref": str(task_ref),
                "relation": "accepted_by",
                "target_ref": str(event["event_id"]),
                "confidence": "high",
                "rule_id": "event.task-accepted",
            }
        )
    return relations


def rebuild_index(project_id: str) -> dict[str, int]:
    events = list(iter_events(project_id))
    errors = verify_chain(events)
    if errors:
        raise ValueError("; ".join(errors))
    connection = _connect(project_id)
    try:
        with connection:
            connection.execute("DELETE FROM relations")
            connection.execute("DELETE FROM events")
            for event in events:
                connection.execute(
                    "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                    (
                        event["event_id"],
                        event["occurred_at"],
                        event["kind"],
                        event["headline"],
                        event["event_hash"],
                    ),
                )
                for relation in _relations(event):
                    relation_id = (
                        "bsr-"
                        + sha256_bytes(
                            canonical_json_bytes({"event_id": event["event_id"], **relation})
                        )[:24]
                    )
                    connection.execute(
                        "INSERT OR IGNORE INTO relations VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            relation_id,
                            relation["source_ref"],
                            relation["relation"],
                            relation["target_ref"],
                            event["event_id"],
                            relation["confidence"],
                            relation["rule_id"],
                        ),
                    )
    finally:
        connection.close()
    return {"events": len(events), "relations": sum(len(_relations(item)) for item in events)}


def ingest_event(
    project: Path,
    value: dict[str, Any],
    *,
    source_system: str,
    observed_at: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    registration = registered_project(project)
    project_id = str(registration["project_id"])
    with project_lock(project_id):
        events = list(iter_events(project_id))
        chain_errors = verify_chain(events)
        if chain_errors:
            raise ValueError("; ".join(chain_errors))
        previous = events[-1]["event_hash"] if events else ZERO_HASH
        event = prepare_event(
            value,
            project_id=project_id,
            source_system=source_system,
            previous_event_hash=previous,
            observed_at=observed_at,
        )
        duplicate = next((item for item in events if item["event_id"] == event["event_id"]), None)
        receipts = project_dir(project_id) / "receipts"
        receipt = {
            "schema_version": "brief-spec-chronicle-ingest-receipt/1.0",
            "project_id": project_id,
            "event_id": event["event_id"],
            "event_hash": event["event_hash"],
            "ledger_head_hash": event["event_hash"],
            "observed_at": event["observed_at"],
        }
        receipt_path = receipts / f"ingest-{event['event_id']}.json"
        if duplicate is not None:
            if not dry_run:
                rebuild_index(project_id)
                if not receipt_path.is_file():
                    _private_dir(receipts)
                    duplicate_receipt = {
                        **receipt,
                        "event_hash": duplicate["event_hash"],
                        "ledger_head_hash": events[-1]["event_hash"],
                        "observed_at": duplicate["observed_at"],
                    }
                    atomic_write(
                        receipt_path,
                        json.dumps(duplicate_receipt, indent=2, sort_keys=True).encode() + b"\n",
                    )
            return {
                "status": "DUPLICATE",
                "event": duplicate,
                "ledger_head_hash": events[-1]["event_hash"],
            }
        if dry_run:
            return {"status": "DRY-RUN", "event": event, "ledger_head_hash": previous}
        timestamp = datetime.fromisoformat(event["observed_at"].replace("Z", "+00:00"))
        segment_dir = project_dir(project_id) / "events"
        _private_dir(segment_dir)
        segment = segment_dir / f"{timestamp:%Y-%m}.ndjson"
        previous_size = segment.stat().st_size if segment.exists() else 0
        descriptor = os.open(segment, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, canonical_json_bytes(event) + b"\n")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            rebuild_index(project_id)
            _private_dir(receipts)
            atomic_write(
                receipt_path,
                json.dumps(receipt, indent=2, sort_keys=True).encode() + b"\n",
            )
        except Exception:
            with segment.open("r+b") as handle:
                handle.truncate(previous_size)
                handle.flush()
                os.fsync(handle.fileno())
            if previous_size == 0:
                segment.unlink(missing_ok=True)
            with suppress(Exception):
                rebuild_index(project_id)
            receipt_path.unlink(missing_ok=True)
            raise
        return {"status": "INGESTED", "event": event, "receipt": receipt}


def delete_project(project_id: str, confirmation: str) -> dict[str, Any]:
    if confirmation != project_id:
        raise ValueError("Deletion confirmation must exactly match the project ID")
    root = project_dir(project_id)
    if not root.is_dir():
        raise ValueError(f"Chronicle project does not exist: {project_id}")
    resolved = root.resolve()
    expected_parent = chronicle_home().resolve()
    if resolved.parent != expected_parent:
        raise ValueError("Refusing to delete outside the Chronicle state root")
    with project_lock(project_id):
        shutil.rmtree(resolved)
        with project_lock("registry"):
            registry = _load_registry()
            registry["projects"] = [
                item for item in registry["projects"] if item.get("project_id") != project_id
            ]
            _save_registry(registry)
    return {"status": "DELETED", "project_id": project_id, "recoverable": False}


def atomic_external_write(path: Path, content: bytes, *, force: bool = False) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(temporary)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o644)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if not hasattr(os, "fchmod"):
            with suppress(OSError):
                temp.chmod(0o644)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
