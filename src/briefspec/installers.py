from __future__ import annotations

import hashlib
import json
import os
import shlex
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from briefspec import __version__
from briefspec.bundle import build_zipapp
from briefspec.config import briefspec_home, legacy_briefspec_home
from briefspec.errors import InstallConflict
from briefspec.models import Runtime
from briefspec.resources import resource_root
from briefspec.state import atomic_write


@dataclass(frozen=True, slots=True)
class InstallOperation:
    action: str
    path: str
    detail: str


@dataclass(frozen=True, slots=True)
class _FileSnapshot:
    path: Path
    content: bytes | None
    mode: int


def _hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _hash_file(path: Path) -> str:
    return _hash_bytes(path.read_bytes())


def _owned_marker(content: bytes) -> bool:
    return any(
        marker in content
        for marker in (
            b"Managed by Brief-Spec",
            b"Managed by Brief-Spec",
            b"brief-spec:",
            b"briefspec:",
        )
    )


def _safe_write(
    path: Path,
    content: bytes,
    operations: list[InstallOperation],
    written: list[dict[str, Any]],
    dry_run: bool,
) -> None:
    if path.exists():
        existing = path.read_bytes()
        if existing == content:
            operations.append(InstallOperation("unchanged", str(path), "already current"))
            written.append({"path": str(path), "sha256": _hash_bytes(content), "kind": "owned"})
            return
        if not _owned_marker(existing):
            raise InstallConflict(f"Refusing to overwrite foreign file: {path}")
    operations.append(InstallOperation("write", str(path), f"{len(content)} bytes"))
    if not dry_run:
        atomic_write(path, content)
    written.append({"path": str(path), "sha256": _hash_bytes(content), "kind": "owned"})


def _copy_tree(
    source: Path,
    destination: Path,
    operations: list[InstallOperation],
    written: list[dict[str, Any]],
    dry_run: bool,
    receipt_hashes: dict[Path, str],
    replace_modified: bool = False,
) -> None:
    for path in sorted(source.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        target = destination / path.relative_to(source)
        content = path.read_bytes()
        existing = target.read_bytes() if target.exists() else None
        receipt_hash = receipt_hashes.get(target)
        receipt_owned = (
            existing is not None
            and receipt_hash is not None
            and _hash_bytes(existing) == receipt_hash
        )
        if existing is not None and existing != content and receipt_hash is None:
            raise InstallConflict(f"Refusing to overwrite foreign skill file: {target}")
        if (
            existing is not None
            and existing != content
            and receipt_hash is not None
            and not receipt_owned
            and not replace_modified
        ):
            candidate = target.with_name(f"{target.name}.brief-spec-new")
            candidate_existing = candidate.read_bytes() if candidate.exists() else None
            candidate_receipt_hash = receipt_hashes.get(candidate)
            if (
                candidate_existing is not None
                and candidate_existing != content
                and (
                    candidate_receipt_hash is None
                    or _hash_bytes(candidate_existing) != candidate_receipt_hash
                )
            ):
                raise InstallConflict(
                    f"Refusing to overwrite foreign conflict candidate: {candidate}"
                )
            operations.append(
                InstallOperation(
                    "conflict",
                    str(target),
                    f"locally modified; candidate staged at {candidate}",
                )
            )
            operations.append(InstallOperation("write", str(candidate), "upgrade candidate"))
            if not dry_run:
                atomic_write(candidate, content)
            written.append({"path": str(target), "sha256": receipt_hash, "kind": "owned"})
            written.append(
                {"path": str(candidate), "sha256": _hash_bytes(content), "kind": "owned"}
            )
            continue
        candidate = target.with_name(f"{target.name}.brief-spec-new")
        if replace_modified and candidate.exists():
            candidate_hash = receipt_hashes.get(candidate)
            if candidate.read_bytes() != content and (
                candidate_hash is None or _hash_file(candidate) != candidate_hash
            ):
                raise InstallConflict(
                    f"Refusing to remove modified conflict candidate: {candidate}"
                )
            operations.append(
                InstallOperation("remove", str(candidate), "resolved upgrade candidate")
            )
            if not dry_run:
                candidate.unlink(missing_ok=True)
        operations.append(InstallOperation("write", str(target), "skill asset"))
        if not dry_run:
            atomic_write(target, content)
        written.append({"path": str(target), "sha256": _hash_bytes(content), "kind": "owned"})


def _ensure_copy_tree_safe(
    source: Path,
    destination: Path,
    receipt_hashes: dict[Path, str],
    replace_modified: bool = False,
) -> None:
    for path in sorted(source.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        target = destination / path.relative_to(source)
        if not target.exists() or target.read_bytes() == path.read_bytes():
            continue
        expected = receipt_hashes.get(target)
        if expected is not None:
            if replace_modified:
                continue
            candidate = target.with_name(f"{target.name}.brief-spec-new")
            if candidate.exists() and candidate.read_bytes() != path.read_bytes():
                candidate_expected = receipt_hashes.get(candidate)
                if candidate_expected is None or _hash_file(candidate) != candidate_expected:
                    raise InstallConflict(
                        f"Refusing to overwrite foreign conflict candidate: {candidate}"
                    )
            continue
        raise InstallConflict(f"Refusing to overwrite foreign skill file: {target}")


def _managed_skill_paths(source: Path, destination: Path) -> list[Path]:
    return [
        destination / path.relative_to(source)
        for path in sorted(source.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    ]


def _snapshot_files(paths: list[Path]) -> list[_FileSnapshot]:
    snapshots: list[_FileSnapshot] = []
    for path in dict.fromkeys(paths):
        if path.exists():
            snapshots.append(
                _FileSnapshot(
                    path=path,
                    content=path.read_bytes(),
                    mode=path.stat().st_mode & 0o777,
                )
            )
        else:
            snapshots.append(_FileSnapshot(path=path, content=None, mode=0o600))
    return snapshots


def _restore_files(snapshots: list[_FileSnapshot]) -> None:
    for snapshot in reversed(snapshots):
        if snapshot.content is None:
            snapshot.path.unlink(missing_ok=True)
            continue
        atomic_write(snapshot.path, snapshot.content, mode=snapshot.mode)


def _runtime_home(runtime: Runtime) -> Path:
    if runtime is Runtime.CODEX:
        return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    if runtime is Runtime.CLAUDE:
        return Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")).expanduser()
    defaults = {
        Runtime.COPILOT: ("COPILOT_HOME", Path.home() / ".copilot"),
        Runtime.OMP: ("OMP_HOME", Path.home() / ".omp" / "agent"),
        Runtime.GROK: ("GROK_HOME", Path.home() / ".grok"),
        Runtime.KIMI: ("KIMI_CODE_HOME", Path.home() / ".kimi-code"),
        Runtime.CURSOR: ("CURSOR_HOME", Path.home() / ".cursor"),
        Runtime.GOOSE: ("GOOSE_HOME", Path.home() / ".config" / "goose"),
        Runtime.ANTIGRAVITY: (
            "ANTIGRAVITY_HOME" if "ANTIGRAVITY_HOME" in os.environ else "AGY_HOME",
            Path.home() / ".gemini" / "antigravity",
        ),
    }
    variable, default = defaults[runtime]
    return Path(os.environ.get(variable, default)).expanduser()


def _user_targets(runtime: Runtime) -> tuple[Path, Path, Path]:
    root = _runtime_home(runtime)
    if runtime is Runtime.KIMI:
        plugin = root / "plugins" / "managed" / "brief-spec"
        return (
            plugin / "skills",
            plugin / "brief-spec" / "brief-spec.pyz",
            root / "plugins" / "installed.json",
        )
    skills = root / "skills"
    pyz = root / "brief-spec" / "brief-spec.pyz"
    if runtime is Runtime.CODEX:
        hook = root / "hooks.json"
    elif runtime is Runtime.CLAUDE:
        hook = root / "settings.json"
    elif runtime is Runtime.OMP:
        hook = root / "extensions" / "brief-spec.ts"
    elif runtime in {Runtime.COPILOT, Runtime.GROK}:
        hook = root / "hooks" / "brief-spec.json"
    elif runtime in {Runtime.CURSOR, Runtime.ANTIGRAVITY}:
        hook = root / "hooks.json"
    else:
        hook = root / "brief-spec" / "capabilities.json"
    return skills, pyz, hook


def _project_targets(runtime: Runtime, project: Path) -> tuple[Path, Path, Path]:
    skills = project / ".agents" / "skills"
    if runtime is Runtime.CODEX:
        return (
            skills,
            project / ".codex" / "brief-spec" / "brief-spec.pyz",
            project / ".codex" / "hooks.json",
        )
    if runtime is Runtime.CLAUDE:
        # Claude Code discovers project skills natively only under .claude/skills.
        return (
            project / ".claude" / "skills",
            project / ".claude" / "brief-spec" / "brief-spec.pyz",
            project / ".claude" / "settings.json",
        )
    targets = {
        Runtime.COPILOT: (
            skills,
            project / ".github" / "brief-spec" / "brief-spec.pyz",
            project / ".github" / "hooks" / "brief-spec.json",
        ),
        Runtime.OMP: (
            project / ".omp" / "skills",
            project / ".omp" / "brief-spec" / "brief-spec.pyz",
            project / ".omp" / "extensions" / "brief-spec.ts",
        ),
        Runtime.GROK: (
            project / ".grok" / "skills",
            project / ".grok" / "brief-spec" / "brief-spec.pyz",
            project / ".grok" / "hooks" / "brief-spec.json",
        ),
        Runtime.KIMI: (
            project / ".kimi-code" / "skills",
            project / ".kimi-code" / "brief-spec" / "brief-spec.pyz",
            project / ".kimi-code" / "brief-spec" / "project-capabilities.json",
        ),
        Runtime.CURSOR: (
            project / ".cursor" / "skills",
            project / ".cursor" / "brief-spec" / "brief-spec.pyz",
            project / ".cursor" / "hooks.json",
        ),
        Runtime.GOOSE: (
            project / ".agents" / "skills",
            project / ".goose" / "brief-spec" / "brief-spec.pyz",
            project / ".goose" / "brief-spec" / "capabilities.json",
        ),
        Runtime.ANTIGRAVITY: (
            project / ".agents" / "skills",
            project / ".gemini" / "antigravity" / "brief-spec" / "brief-spec.pyz",
            project / ".gemini" / "antigravity" / "hooks.json",
        ),
    }
    return targets[runtime]


def _command(
    pyz: Path,
    runtime: Runtime,
    event: str,
    project: Path | None = None,
    output_profile: str = "native",
) -> str:
    if project:
        executable = "python3"
        relative = pyz.relative_to(project).as_posix()
        if runtime is Runtime.CLAUDE:
            # Claude Code exports CLAUDE_PROJECT_DIR to hook commands; anchoring on it
            # keeps the hook working when the session cwd is not the project root.
            quoted_path = f'"$CLAUDE_PROJECT_DIR/{relative}"'
        elif runtime is Runtime.CODEX:
            # Codex runs hooks with the session cwd. Resolve repository-local assets
            # from Git instead of assuming the session started at the project root.
            quoted_path = f'"$(git rev-parse --show-toplevel)/{relative}"'
        else:
            quoted_path = shlex.quote(relative)
    else:
        quoted_path = shlex.quote(str(pyz))
        executable = sys.executable
    command = (
        f"{shlex.quote(executable)} {quoted_path} hook --provider {runtime.value} --event {event}"
    )
    if output_profile != "native":
        command += f" --output-profile {shlex.quote(output_profile)}"
    return command


def _codex_project_powershell_command(
    pyz: Path,
    event: str,
    project: Path,
    output_profile: str = "native",
) -> str:
    relative = pyz.relative_to(project).as_posix()
    script = (
        "$briefspecRoot = git rev-parse --show-toplevel; "
        "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; "
        f"& python (Join-Path $briefspecRoot '{relative}') "
        f"hook --provider codex --event {event}"
    )
    if output_profile != "native":
        script += f" --output-profile {output_profile}"
    return f'powershell.exe -NoLogo -NoProfile -NonInteractive -Command "& {{ {script} }}"'


def _powershell_command(
    pyz: Path,
    runtime: Runtime,
    event: str,
    project: Path | None = None,
    output_profile: str = "native",
) -> str:
    if project:
        executable = "python"
        runtime_path = pyz.relative_to(project).as_posix()
    else:
        executable = str(Path(sys.executable))
        runtime_path = str(pyz)

    def quote(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    command = (
        f"& {quote(executable)} {quote(runtime_path)} "
        f"hook --provider {runtime.value} --event {event}"
    )
    if output_profile != "native":
        command += f" --output-profile {quote(output_profile)}"
    return command


_EVENTS = (
    ("SessionStart", "sessionStart"),
    ("UserPromptSubmit", "userPromptSubmitted"),
    ("PostToolUse", "postToolUse"),
    ("PreCompact", "preCompact"),
    ("Stop", "agentStop"),
)

_SUBAGENT_EVENTS = (
    ("SubagentStart", "subagentStart"),
    ("SubagentStop", "subagentStop"),
)


def _events_for_runtime(runtime: Runtime) -> tuple[tuple[str, str], ...]:
    if runtime in {Runtime.GROK, Runtime.KIMI, Runtime.ANTIGRAVITY}:
        return (*_EVENTS, *_SUBAGENT_EVENTS)
    return _EVENTS


def _nested_hook_block(
    runtime: Runtime,
    pyz: Path,
    project: Path | None = None,
) -> dict[str, list[dict[str, Any]]]:
    hooks: dict[str, list[dict[str, Any]]] = {}
    for pascal, _ in _events_for_runtime(runtime):
        handler: dict[str, Any] = {
            "type": "command",
            "command": _command(pyz, runtime, pascal, project),
            "timeout": 10,
        }
        if runtime is Runtime.CODEX and project is not None:
            handler["commandWindows"] = _codex_project_powershell_command(
                pyz,
                pascal,
                project,
            )
        hooks[pascal] = [
            {
                "matcher": "",
                "hooks": [handler],
            }
        ]
    return hooks


def _copilot_hook_block(pyz: Path, project: Path | None = None) -> dict[str, Any]:
    hooks: dict[str, list[dict[str, Any]]] = {}
    for pascal, camel in _EVENTS:
        command = _command(pyz, Runtime.COPILOT, pascal, project)
        hooks[camel] = [
            {
                "type": "command",
                "bash": command,
                "powershell": _powershell_command(
                    pyz,
                    Runtime.COPILOT,
                    pascal,
                    project,
                ),
                "timeoutSec": 10,
                "cwd": ".",
            }
        ]
    return {"version": 1, "hooks": hooks}


def _copilot_project_hook_block(pyz: Path, project: Path) -> dict[str, Any]:
    hooks: dict[str, list[dict[str, Any]]] = {}
    for pascal, _ in _EVENTS:
        hooks[pascal] = [
            {
                "type": "command",
                "bash": _command(
                    pyz,
                    Runtime.COPILOT,
                    pascal,
                    project,
                    output_profile="vscode",
                ),
                "powershell": _powershell_command(
                    pyz,
                    Runtime.COPILOT,
                    pascal,
                    project,
                    output_profile="vscode",
                ),
                "timeoutSec": 10,
                "cwd": ".",
            }
        ]
    return {"version": 1, "hooks": hooks}


def _command_from_item(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    if "hooks" in item and isinstance(item["hooks"], list):
        return "\n".join(_command_from_item(child) for child in item["hooks"])
    return str(item.get("command") or item.get("bash") or "")


def _remove_briefspec_entries(value: dict[str, Any]) -> dict[str, Any]:
    hooks = value.get("hooks")
    if not isinstance(hooks, dict):
        return value
    for event, entries in list(hooks.items()):
        if not isinstance(entries, list):
            continue
        hooks[event] = [
            entry
            for entry in entries
            if not any(
                marker in _command_from_item(entry)
                for marker in ("briefspec.pyz", "brief-spec.pyz")
            )
        ]
        if not hooks[event]:
            hooks.pop(event, None)
    return value


def _empty_hook_file(value: dict[str, Any]) -> bool:
    allowed = {"hooks", "version"}
    return (
        not (set(value) - allowed)
        and value.get("hooks") in ({}, None)
        and value.get("version") in (1, None)
    )


def _merge_hook_file(
    runtime: Runtime,
    path: Path,
    pyz: Path,
    project: Path | None = None,
) -> bytes:
    try:
        existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except json.JSONDecodeError as exc:
        raise InstallConflict(f"Cannot merge malformed JSON: {path}: {exc}") from exc
    if not isinstance(existing, dict):
        raise InstallConflict(f"Cannot merge non-object JSON: {path}")
    existing = _remove_briefspec_entries(existing)
    if runtime is Runtime.COPILOT:
        block = (
            _copilot_project_hook_block(pyz, project)
            if project is not None
            else _copilot_hook_block(pyz)
        )
        existing["version"] = 1
        hooks = existing.setdefault("hooks", {})
        for event, entries in block["hooks"].items():
            hooks.setdefault(event, []).extend(entries)
    else:
        hooks = existing.setdefault("hooks", {})
        for event, entries in _nested_hook_block(runtime, pyz, project).items():
            hooks.setdefault(event, []).extend(entries)
    return json.dumps(existing, indent=2, sort_keys=True).encode() + b"\n"


def _omp_extension_content(pyz: Path, project: Path | None) -> bytes:
    executable = "python3" if project else sys.executable
    runtime_expression = (
        'new URL("../brief-spec/brief-spec.pyz", import.meta.url).pathname'
        if project
        else json.dumps(str(pyz))
    )
    content = f"""\
// Managed by Brief-Spec. Compatibility markers: briefspec: briefspec.pyz
const PYTHON = {json.dumps(executable)};
const BRIEF_SPEC = {runtime_expression};

function contentText(value) {{
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value
    .map((item) => typeof item === "string" ? item : String(item?.text ?? ""))
    .filter(Boolean).join("\\n");
  return String(value?.text ?? value?.content ?? "");
}}

export default function briefSpecExtension(pi) {{
  pi.setLabel("Brief-Spec verified delivery");
  let activeSessionId = `omp-${{crypto.randomUUID()}}`;
  let sessionContext = "";

  async function run(eventName, payload, ctx) {{
    const value = {{
      ...payload,
      cwd: ctx?.cwd,
      session_id: payload?.session_id ?? payload?.sessionId ?? activeSessionId,
      timestamp: new Date().toISOString(),
    }};
    try {{
      const result = await pi.exec(PYTHON, [
        BRIEF_SPEC, "hook", "--provider", "omp", "--event", eventName,
        "--payload-json", JSON.stringify(value),
      ], {{ cwd: ctx?.cwd, timeout: 10000 }});
      if (result.code !== 0) {{
        pi.logger.warn(`Brief-Spec hook ${{eventName}} exited ${{result.code}}`);
        return {{}};
      }}
      return JSON.parse(result.stdout || "{{}}");
    }} catch (error) {{
      pi.logger.warn(`Brief-Spec hook ${{eventName}} failed: ${{String(error)}}`);
      return {{}};
    }}
  }}

  function contextFrom(value) {{
    return value?.additionalContext ?? value?.hookSpecificOutput?.additionalContext;
  }}

  pi.on("session_start", async (event, ctx) => {{
    activeSessionId = event.session_id ?? event.sessionId ?? activeSessionId;
    const result = await run("SessionStart", event, ctx);
    sessionContext = contextFrom(result) || "";
  }});

  pi.on("before_agent_start", async (event, ctx) => {{
    const result = await run("UserPromptSubmit", {{ prompt: event.prompt }}, ctx);
    const context = contextFrom(result) || sessionContext;
    if (!context) return;
    const base = Array.isArray(event?.systemPrompt) ? event.systemPrompt : [];
    return {{ systemPrompt: [...base, context] }};
  }});

  pi.on("tool_result", async (event, ctx) => {{
    await run("PostToolUse", {{ tool_name: event.toolName, is_error: event.isError }}, ctx);
  }});

  pi.on("session.compacting", async (event, ctx) => {{
    const result = await run("PreCompact", {{ session_id: event.sessionId }}, ctx);
    const context = contextFrom(result);
    return context ? {{ context: [context] }} : undefined;
  }});

  pi.on("session_stop", async (event, ctx) => {{
    const messages = Array.isArray(event.messages) ? event.messages : [];
    const lastMessage = messages.length ? messages[messages.length - 1] : undefined;
    const result = await run("Stop", {{
      session_id: event.session_id ?? activeSessionId,
      turn_id: String(event.turn_id ?? ""),
      stop_hook_active: event.stop_hook_active,
      last_assistant_message: contentText(
        event.last_assistant_message?.content ?? lastMessage?.content
      ),
    }}, ctx);
    if (result?.decision === "block" && result?.reason) {{
      return {{ decision: "block", reason: result.reason }};
    }}
  }});
}}
"""
    return content.encode()


def _capability_marker(runtime: Runtime, scope: str) -> bytes:
    value = {
        "schema_version": 1,
        "kind": "brief-spec-harness-capabilities",
        "brief_spec_version": __version__,
        "harness": runtime.value,
        "scope": scope,
        "lifecycle_automation": runtime is not Runtime.GOOSE,
    }
    return json.dumps(value, indent=2, sort_keys=True).encode() + b"\n"


def _kimi_plugin_manifest(pyz: Path) -> bytes:
    hooks = []
    for event, _ in _events_for_runtime(Runtime.KIMI):
        hooks.append(
            {
                "event": event,
                "matcher": "",
                "command": _command(pyz, Runtime.KIMI, event),
                "timeout": 10,
            }
        )
    value = {
        "name": "brief-spec",
        "version": __version__,
        "description": "Managed by Brief-Spec: type routing and verified delivery lifecycle",
        "skills": "./skills",
        "sessionStart": {"skill": "brief-spec"},
        "hooks": hooks,
    }
    return json.dumps(value, indent=2, sort_keys=True).encode() + b"\n"


def _merge_kimi_plugin_registry(path: Path, plugin_root: Path) -> bytes:
    try:
        existing = (
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"plugins": []}
        )
    except json.JSONDecodeError as exc:
        raise InstallConflict(f"Cannot merge malformed JSON: {path}: {exc}") from exc
    if not isinstance(existing, dict) or not isinstance(existing.get("plugins", []), list):
        raise InstallConflict(f"Cannot merge non-object Kimi plugin registry: {path}")
    plugins = [
        value
        for value in existing.get("plugins", [])
        if not (isinstance(value, dict) and value.get("id") == "brief-spec")
    ]
    plugins.append(
        {
            "id": "brief-spec",
            "root": str(plugin_root),
            "source": "local-path",
            "enabled": True,
            "version": __version__,
        }
    )
    existing["plugins"] = plugins
    return json.dumps(existing, indent=2, sort_keys=True).encode() + b"\n"


def _instruction_content() -> bytes:
    return b"""\
---
applyTo: "**"
---

<!-- Managed by Brief-Spec. Compatibility marker: briefspec: -->

Use the `brief-spec` skill to select one work-type explanation profile.
Use the `outcome-brief` skill when substantive work reaches a terminal handoff.
Use `session-checkpoint` when the user asks to orient, teach, or receive a spoken recap.
Keep claims attached to inspectable proof, state unverified gaps explicitly, and never
treat the brief as more authoritative than the underlying repository or runtime evidence.
"""


def _receipt_name(runtime: Runtime, scope: str, project: Path | None) -> str:
    suffix = ""
    if project:
        suffix = "-" + hashlib.sha256(str(project.resolve()).encode()).hexdigest()[:12]
    return f"{runtime.value}-{scope}{suffix}.json"


def receipt_path(runtime: Runtime, scope: str, project: Path | None = None) -> Path:
    return briefspec_home() / "receipts" / _receipt_name(runtime, scope, project)


def _receipt_created_path(receipt: Path, path: Path) -> bool:
    try:
        value = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return any(
        Path(entry.get("path", "")) == path and bool(entry.get("created"))
        for entry in value.get("files", [])
        if isinstance(entry, dict)
    )


def _receipt_owned_hashes(receipt: Path) -> dict[Path, str]:
    try:
        value = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        Path(entry["path"]): str(entry["sha256"])
        for entry in value.get("files", [])
        if isinstance(entry, dict)
        and entry.get("kind") == "owned"
        and entry.get("sha256") != "dry-run"
        and isinstance(entry.get("path"), str)
        and isinstance(entry.get("sha256"), str)
    }


def _legacy_receipt_path(runtime: Runtime, scope: str, project: Path | None = None) -> Path:
    return legacy_briefspec_home() / "receipts" / _receipt_name(runtime, scope, project)


def _combined_receipt_hashes(
    runtime: Runtime,
    scope: str,
    project: Path | None,
) -> dict[Path, str]:
    values = _receipt_owned_hashes(_legacy_receipt_path(runtime, scope, project))
    values.update(_receipt_owned_hashes(receipt_path(runtime, scope, project)))
    return values


def _legacy_owned_paths(
    runtime: Runtime,
    scope: str,
    project: Path | None,
    retained: set[Path],
) -> tuple[Path, list[Path]]:
    receipt = _legacy_receipt_path(runtime, scope, project)
    if receipt == receipt_path(runtime, scope, project) or not receipt.is_file():
        return receipt, []
    try:
        value = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return receipt, []
    removable: list[Path] = []
    for entry in value.get("files", []):
        if not isinstance(entry, dict) or entry.get("kind") != "owned":
            continue
        path = Path(str(entry.get("path", "")))
        expected = entry.get("sha256")
        if path in retained or not path.is_file() or expected in {None, "dry-run"}:
            continue
        if _hash_file(path) == expected:
            removable.append(path)
    return receipt, removable


def _ensure_owned_target_safe(path: Path, content: bytes) -> None:
    if not path.exists() or path.read_bytes() == content or _owned_marker(path.read_bytes()):
        return
    raise InstallConflict(f"Refusing to overwrite foreign file: {path}")


def install_runtime(
    runtime: Runtime,
    *,
    scope: str = "user",
    project: Path | None = None,
    dry_run: bool = False,
    replace_modified: bool = False,
) -> dict[str, Any]:
    if scope not in {"user", "project"}:
        raise ValueError("scope must be user or project")
    project = (project or Path.cwd()).resolve() if scope == "project" else None
    skills_target, pyz_target, hook_target = (
        _project_targets(runtime, project) if project else _user_targets(runtime)
    )
    operations: list[InstallOperation] = []
    written: list[dict[str, Any]] = []
    resources = resource_root()
    target_receipt = receipt_path(runtime, scope, project)
    receipt_hashes = _combined_receipt_hashes(runtime, scope, project)
    _ensure_copy_tree_safe(
        resources / "skills",
        skills_target,
        receipt_hashes,
        replace_modified=replace_modified,
    )
    skills_only = runtime is Runtime.KIMI and project is not None
    hook_created = not hook_target.exists() or _receipt_created_path(target_receipt, hook_target)
    hook_kind = "merged"
    plugin_manifest: Path | None = None
    plugin_content: bytes | None = None
    if skills_only:
        hook_content = None
    elif runtime is Runtime.OMP:
        hook_content = _omp_extension_content(pyz_target, project)
        hook_kind = "owned"
        _ensure_owned_target_safe(hook_target, hook_content)
    elif runtime is Runtime.KIMI:
        plugin_root = skills_target.parent
        plugin_manifest = plugin_root / "kimi.plugin.json"
        plugin_content = _kimi_plugin_manifest(pyz_target)
        _ensure_owned_target_safe(plugin_manifest, plugin_content)
        hook_content = _merge_kimi_plugin_registry(hook_target, plugin_root)
        hook_kind = "kimi-registry"
    elif runtime is Runtime.GOOSE:
        hook_content = _capability_marker(runtime, scope)
        hook_kind = "owned"
        _ensure_owned_target_safe(hook_target, hook_content)
    else:
        hook_content = _merge_hook_file(runtime, hook_target, pyz_target, project)
    if runtime is Runtime.COPILOT and project:
        instruction = project / ".github" / "instructions" / "brief-spec.instructions.md"
        if instruction.exists():
            existing_instruction = instruction.read_bytes()
            if existing_instruction != _instruction_content() and not _owned_marker(
                existing_instruction
            ):
                raise InstallConflict(f"Refusing to overwrite foreign file: {instruction}")

    managed_paths = _managed_skill_paths(resources / "skills", skills_target)
    managed_paths.extend(
        path.with_name(f"{path.name}.brief-spec-new") for path in tuple(managed_paths)
    )
    if not skills_only:
        managed_paths.extend((pyz_target, hook_target))
    if plugin_manifest is not None:
        managed_paths.append(plugin_manifest)
    managed_paths.append(target_receipt)
    if runtime is Runtime.COPILOT and project:
        managed_paths.append(instruction)
    legacy_receipt, legacy_owned = _legacy_owned_paths(
        runtime,
        scope,
        project,
        set(managed_paths),
    )
    if legacy_receipt != target_receipt and legacy_receipt.exists():
        managed_paths.extend((legacy_receipt, *legacy_owned))
    snapshots = [] if dry_run else _snapshot_files(managed_paths)

    try:
        _copy_tree(
            resources / "skills",
            skills_target,
            operations,
            written,
            dry_run,
            receipt_hashes,
            replace_modified,
        )
        if not skills_only:
            operations.append(InstallOperation("write", str(pyz_target), "self-contained runtime"))
            if not dry_run:
                build_zipapp(pyz_target)
                written.append(
                    {
                        "path": str(pyz_target),
                        "sha256": _hash_file(pyz_target),
                        "kind": "owned",
                    }
                )
            else:
                written.append({"path": str(pyz_target), "sha256": "dry-run", "kind": "owned"})

            assert hook_content is not None
            action = "merge" if hook_kind in {"merged", "kimi-registry"} else "write"
            operations.append(InstallOperation(action, str(hook_target), "lifecycle hooks"))
            if not dry_run:
                atomic_write(hook_target, hook_content)
            written.append(
                {
                    "path": str(hook_target),
                    "sha256": _hash_bytes(hook_content),
                    "kind": hook_kind,
                    "created": hook_created,
                }
            )

        if plugin_manifest is not None and plugin_content is not None:
            _safe_write(plugin_manifest, plugin_content, operations, written, dry_run)

        if runtime is Runtime.COPILOT and project:
            _safe_write(instruction, _instruction_content(), operations, written, dry_run)

        receipt = {
            "schema_version": 2,
            "kind": "brief-spec-installation-receipt",
            "brief_spec_version": __version__,
            "briefspec_version": __version__,
            "harness": runtime.value,
            "runtime": runtime.value,
            "scope": scope,
            "project": str(project) if project else None,
            "lifecycle_automation": not skills_only and runtime is not Runtime.GOOSE,
            "files": written,
        }
        operations.append(InstallOperation("write", str(target_receipt), "installation receipt"))
        if not dry_run:
            atomic_write(
                target_receipt,
                json.dumps(receipt, indent=2, sort_keys=True).encode() + b"\n",
            )
        if legacy_receipt != target_receipt and legacy_receipt.exists():
            for path in legacy_owned:
                operations.append(
                    InstallOperation("remove", str(path), "legacy receipt-owned file")
                )
                if not dry_run:
                    path.unlink(missing_ok=True)
            operations.append(
                InstallOperation("remove", str(legacy_receipt), "migrated legacy receipt")
            )
            if not dry_run:
                legacy_receipt.unlink(missing_ok=True)
    except Exception as exc:
        if snapshots:
            try:
                _restore_files(snapshots)
            except Exception as rollback_error:
                exc.add_note(f"Brief-Spec rollback also failed: {rollback_error}")
        raise
    return {
        "runtime": runtime.value,
        "scope": scope,
        "project": str(project) if project else None,
        "dry_run": dry_run,
        "operations": [asdict(item) for item in operations],
        "receipt": str(target_receipt),
    }


def _managed_paths_for_runtime(
    runtime: Runtime,
    *,
    scope: str,
    project: Path | None,
) -> list[Path]:
    resolved_project = (project or Path.cwd()).resolve() if scope == "project" else None
    skills, pyz, hook = (
        _project_targets(runtime, resolved_project)
        if resolved_project is not None
        else _user_targets(runtime)
    )
    paths = _managed_skill_paths(resource_root() / "skills", skills)
    paths.extend(path.with_name(f"{path.name}.brief-spec-new") for path in tuple(paths))
    skills_only = runtime is Runtime.KIMI and resolved_project is not None
    if not skills_only:
        paths.extend((pyz, hook))
    if runtime is Runtime.KIMI and resolved_project is None:
        paths.append(skills.parent / "kimi.plugin.json")
    paths.append(receipt_path(runtime, scope, resolved_project))
    if runtime is Runtime.COPILOT and resolved_project is not None:
        paths.append(resolved_project / ".github" / "instructions" / "brief-spec.instructions.md")
    return paths


def install_runtimes(
    runtimes: list[Runtime],
    *,
    scope: str = "user",
    project: Path | None = None,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Install multiple runtimes as one preflighted, rollback-safe transaction."""
    if not runtimes:
        return []
    preflight = [
        install_runtime(runtime, scope=scope, project=project, dry_run=True) for runtime in runtimes
    ]
    if dry_run:
        return preflight
    paths: list[Path] = []
    for runtime in runtimes:
        paths.extend(_managed_paths_for_runtime(runtime, scope=scope, project=project))
    snapshots = _snapshot_files(paths)
    try:
        return [
            install_runtime(runtime, scope=scope, project=project, dry_run=False)
            for runtime in runtimes
        ]
    except Exception as exc:
        try:
            _restore_files(snapshots)
        except Exception as rollback_error:
            exc.add_note(f"Brief-Spec multi-runtime rollback also failed: {rollback_error}")
        raise


def _remove_kimi_plugin_registry(value: dict[str, Any]) -> dict[str, Any]:
    plugins = value.get("plugins")
    if not isinstance(plugins, list):
        return value
    value["plugins"] = [
        item for item in plugins if not (isinstance(item, dict) and item.get("id") == "brief-spec")
    ]
    return value


def uninstall_runtime(
    runtime: Runtime,
    *,
    scope: str = "user",
    project: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    project = (project or Path.cwd()).resolve() if scope == "project" else None
    target_receipt = receipt_path(runtime, scope, project)
    if not target_receipt.exists():
        return {
            "runtime": runtime.value,
            "scope": scope,
            "dry_run": dry_run,
            "operations": [],
            "warnings": ["No installation receipt found"],
        }
    receipt = json.loads(target_receipt.read_text(encoding="utf-8"))
    operations: list[InstallOperation] = []
    warnings: list[str] = []
    for entry in receipt.get("files", []):
        path = Path(entry["path"])
        if entry.get("kind") == "kimi-registry":
            if not path.exists():
                continue
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                cleaned = _remove_kimi_plugin_registry(value)
                if entry.get("created") and cleaned.get("plugins") == []:
                    operations.append(
                        InstallOperation("remove", str(path), "Brief-Spec-created registry")
                    )
                    if not dry_run:
                        path.unlink(missing_ok=True)
                else:
                    content = json.dumps(cleaned, indent=2, sort_keys=True).encode() + b"\n"
                    operations.append(
                        InstallOperation("merge-remove", str(path), "Brief-Spec Kimi plugin")
                    )
                    if not dry_run:
                        atomic_write(path, content)
            except (OSError, json.JSONDecodeError):
                warnings.append(f"Preserved unreadable Kimi registry: {path}")
            continue
        if entry.get("kind") == "merged":
            if not path.exists():
                continue
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                cleaned = _remove_briefspec_entries(value)
                if entry.get("created") and _empty_hook_file(cleaned):
                    operations.append(
                        InstallOperation("remove", str(path), "Brief-Spec-created hook file")
                    )
                    if not dry_run:
                        path.unlink(missing_ok=True)
                else:
                    content = json.dumps(cleaned, indent=2, sort_keys=True).encode() + b"\n"
                    operations.append(
                        InstallOperation("merge-remove", str(path), "Brief-Spec hooks")
                    )
                    if not dry_run:
                        atomic_write(path, content)
            except (OSError, json.JSONDecodeError):
                warnings.append(f"Preserved unreadable merged file: {path}")
            continue
        if not path.exists():
            continue
        expected = entry.get("sha256")
        if expected != "dry-run" and _hash_file(path) != expected:
            warnings.append(f"Preserved modified file: {path}")
            continue
        if _referenced_by_other_receipt(path, target_receipt):
            warnings.append(f"Preserved shared file still used by another install: {path}")
            continue
        operations.append(InstallOperation("remove", str(path), "receipt-owned file"))
        if not dry_run:
            path.unlink()
    operations.append(InstallOperation("remove", str(target_receipt), "installation receipt"))
    if not dry_run:
        target_receipt.unlink(missing_ok=True)
        _prune_empty_parents(receipt)
    return {
        "runtime": runtime.value,
        "scope": scope,
        "dry_run": dry_run,
        "operations": [asdict(item) for item in operations],
        "warnings": warnings,
    }


def _referenced_by_other_receipt(path: Path, current_receipt: Path) -> bool:
    roots = dict.fromkeys((briefspec_home() / "receipts", legacy_briefspec_home() / "receipts"))
    for root in roots:
        if not root.exists():
            continue
        for receipt in root.glob("*.json"):
            if receipt == current_receipt:
                continue
            try:
                value = json.loads(receipt.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if any(Path(item.get("path", "")) == path for item in value.get("files", [])):
                return True
    return False


def _prune_empty_parents(receipt: dict[str, Any]) -> None:
    for entry in receipt.get("files", []):
        path = Path(entry["path"]).parent
        for _ in range(4):
            try:
                path.rmdir()
            except OSError:
                break
            path = path.parent


def host_executable(runtime: Runtime) -> str | None:
    from briefspec.harnesses import harness_adapter

    return harness_adapter(runtime).executable()
