#!/usr/bin/env python3
"""Snapshot only Brief-Spec-owned global integration paths before dogfooding."""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import shutil
import subprocess
import tomllib
from datetime import UTC, datetime
from pathlib import Path

from briefspec import __version__
from briefspec.config import briefspec_home, legacy_briefspec_home

ROOT = Path(__file__).resolve().parents[1]


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _allowed_restore_target(path: Path, project_roots: tuple[Path, ...] = ()) -> bool:
    home = Path.home().resolve()
    allowed_roots = {
        (home / ".codex").resolve(),
        (home / ".claude").resolve(),
        (home / ".omp" / "agent").resolve(),
        (home / ".grok").resolve(),
        (home / ".kimi-code").resolve(),
        (home / ".copilot").resolve(),
        (home / ".cursor").resolve(),
        (home / ".config" / "goose").resolve(),
        briefspec_home().resolve(),
        legacy_briefspec_home().resolve(),
    }
    resolved = path.resolve(strict=False)
    roots = (*allowed_roots, *project_roots)
    return any(resolved != root and resolved.is_relative_to(root) for root in roots)


def _restore(manifest_path: Path) -> int:
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    project_roots = tuple(Path(path).resolve() for path in value.get("project_roots", []))
    for record in value.get("records", []):
        target = Path(record["path"])
        if not _allowed_restore_target(target, project_roots):
            raise SystemExit(f"Refusing unsafe restore target: {target}")
        backup = Path(record["backup"]) if record.get("backup") else None
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        if record["kind"] == "directory" and backup is not None:
            shutil.copytree(backup, target, copy_function=shutil.copy2)
        elif record["kind"] == "file" and backup is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, target)
    print(f"Restored {manifest_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restore", type=Path)
    parser.add_argument(
        "--tool-artifact-dir",
        type=Path,
        help="Directory containing exact core and optional wheels used for rollback",
    )
    parser.add_argument(
        "--project-receipt",
        action="append",
        default=[],
        type=Path,
        help="Also snapshot every receipt-owned path for one project installation",
    )
    args = parser.parse_args()
    if args.restore:
        return _restore(args.restore.resolve())
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = briefspec_home() / "backups" / stamp
    destination.mkdir(parents=True, mode=0o700)
    home = Path.home()
    paths = [
        home / ".codex" / "hooks.json",
        home / ".codex" / "brief-spec",
        home / ".codex" / "briefspec",
        home / ".codex" / "skills" / "brief-spec",
        home / ".codex" / "skills" / "outcome-brief",
        home / ".codex" / "skills" / "session-checkpoint",
        home / ".claude" / "settings.json",
        home / ".claude" / "brief-spec",
        home / ".claude" / "briefspec",
        home / ".claude" / "skills" / "brief-spec",
        home / ".claude" / "skills" / "outcome-brief",
        home / ".claude" / "skills" / "session-checkpoint",
        home / ".omp" / "agent" / "brief-spec",
        home / ".omp" / "agent" / "extensions" / "brief-spec.ts",
        home / ".omp" / "agent" / "skills" / "brief-spec",
        home / ".omp" / "agent" / "skills" / "outcome-brief",
        home / ".omp" / "agent" / "skills" / "session-checkpoint",
        home / ".grok" / "brief-spec",
        home / ".grok" / "hooks" / "brief-spec.json",
        home / ".grok" / "skills" / "brief-spec",
        home / ".grok" / "skills" / "outcome-brief",
        home / ".grok" / "skills" / "session-checkpoint",
        home / ".kimi-code" / "plugins" / "installed.json",
        home / ".kimi-code" / "plugins" / "managed" / "brief-spec",
        home / ".copilot" / "brief-spec",
        home / ".copilot" / "hooks" / "brief-spec.json",
        home / ".copilot" / "skills" / "brief-spec",
        home / ".copilot" / "skills" / "outcome-brief",
        home / ".copilot" / "skills" / "session-checkpoint",
        home / ".cursor" / "brief-spec",
        home / ".cursor" / "hooks.json",
        home / ".cursor" / "skills" / "brief-spec",
        home / ".cursor" / "skills" / "outcome-brief",
        home / ".cursor" / "skills" / "session-checkpoint",
        home / ".config" / "goose" / "brief-spec",
        home / ".config" / "goose" / "skills" / "brief-spec",
        home / ".config" / "goose" / "skills" / "outcome-brief",
        home / ".config" / "goose" / "skills" / "session-checkpoint",
        *[
            state_root / "receipts" / f"{runtime}-user.json"
            for state_root in dict.fromkeys((briefspec_home(), legacy_briefspec_home()))
            for runtime in (
                "codex",
                "claude",
                "omp",
                "grok",
                "kimi",
                "copilot",
                "cursor",
                "goose",
                "antigravity",
            )
        ],
    ]
    project_roots: list[Path] = []
    project_installs: list[tuple[str, Path]] = []
    for receipt_source in args.project_receipt:
        receipt_source = receipt_source.resolve()
        receipt = json.loads(receipt_source.read_text(encoding="utf-8"))
        if receipt.get("scope") != "project" or not receipt.get("project"):
            raise SystemExit(f"Not a project receipt: {receipt_source}")
        project_root = Path(receipt["project"]).resolve()
        runtime = str(receipt.get("runtime") or receipt.get("harness") or "")
        if not runtime:
            raise SystemExit(f"Project receipt has no runtime: {receipt_source}")
        project_roots.append(project_root)
        project_installs.append((runtime, project_root))
        paths.append(receipt_source)
        for file_record in receipt.get("files", []):
            source = Path(file_record["path"])
            resolved = source.resolve(strict=False)
            if resolved == project_root or not resolved.is_relative_to(project_root):
                raise SystemExit(f"Project receipt escapes {project_root}: {source}")
            paths.append(source)

    records = []
    for source in dict.fromkeys(paths):
        relative = Path(str(source).removeprefix(str(home)).lstrip("/"))
        target = destination / "files" / relative
        if source.is_dir():
            shutil.copytree(source, target, copy_function=shutil.copy2)
            records.append({"path": str(source), "kind": "directory", "backup": str(target)})
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            records.append(
                {
                    "path": str(source),
                    "kind": "file",
                    "backup": str(target),
                    "mode": source.stat().st_mode & 0o777,
                    "sha256": _hash(source),
                }
            )
        else:
            records.append({"path": str(source), "kind": "absent", "backup": None})
    commands = {}
    for name, command in {
        "brief_spec_version": ["brief-spec", "--version"],
        "uv_tools": ["uv", "tool", "list"],
    }.items():
        result = subprocess.run(command, text=True, capture_output=True, timeout=30, check=False)
        commands[name] = {
            "command": command,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    artifact_dir = args.tool_artifact_dir.resolve() if args.tool_artifact_dir else None
    restore_tool = None
    if artifact_dir is not None:
        required_wheels = [
            artifact_dir / f"brief_spec-{__version__}-py3-none-any.whl",
            artifact_dir / f"brief_spec_renderer_pdf-{__version__}-py3-none-any.whl",
            artifact_dir / f"brief_spec_renderer_audio-{__version__}-py3-none-any.whl",
        ]
        missing = [str(path) for path in required_wheels if not path.is_file()]
        if missing:
            raise SystemExit("Missing rollback wheel(s): " + ", ".join(missing))
        optional_projects = (
            ("brief_spec_chronicle", Path("packages/brief-spec-chronicle/pyproject.toml")),
            (
                "brief_spec_renderer_video",
                Path("packages/brief-spec-renderer-video/pyproject.toml"),
            ),
        )
        optional_wheels: list[Path] = []
        for filename, project_path in optional_projects:
            with (ROOT / project_path).open("rb") as handle:
                version = tomllib.load(handle)["project"]["version"]
            candidate = artifact_dir / f"{filename}-{version}-py3-none-any.whl"
            if candidate.is_file():
                optional_wheels.append(candidate)
        restore_arguments = [
            "uv",
            "tool",
            "install",
            "--force",
            "--reinstall",
            str(required_wheels[0]),
        ]
        for wheel in [*required_wheels[1:], *optional_wheels]:
            restore_arguments.extend(["--with", str(wheel)])
        chronicle = next(
            (path for path in optional_wheels if path.name.startswith("brief_spec_chronicle-")),
            None,
        )
        if chronicle is not None:
            restore_arguments.extend(["--with-executables-from", "brief-spec-chronicle"])
        restore_tool = shlex.join(restore_arguments)
    rollback = [
        f"brief-spec uninstall {runtime} --scope user"
        for runtime in (
            "codex",
            "claude",
            "omp",
            "grok",
            "kimi",
            "copilot",
            "cursor",
            "goose",
            "antigravity",
        )
    ]
    rollback.extend(
        shlex.join(
            ["brief-spec", "uninstall", runtime, "--scope", "project", "--project", str(project)]
        )
        for runtime, project in project_installs
    )
    if restore_tool:
        rollback.append(restore_tool)
    rollback.append(
        f"uv run python scripts/snapshot-installation.py --restore {destination / 'manifest.json'}"
    )
    manifest = {
        "schema_version": 1,
        "brief_spec_version": __version__,
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "tool_artifact_dir": str(artifact_dir) if artifact_dir else None,
        "project_roots": [str(path) for path in project_roots],
        "records": records,
        "commands": commands,
        "rollback": rollback,
    }
    manifest_path = destination / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    manifest_path.chmod(0o600)
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
