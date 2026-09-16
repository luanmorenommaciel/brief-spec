from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from briefspec import __version__
from briefspec.installers import (
    _legacy_receipt_path,
    _project_targets,
    _user_targets,
    host_executable,
    receipt_path,
)
from briefspec.models import Runtime


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    status: str
    detail: str
    remediation: str | None = None


def _contains_hook(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
        return any(marker in text for marker in ("brief-spec.pyz", "briefspec.pyz"))
    except OSError:
        return False


_CODEX_EVENT_LABELS = {
    "PreToolUse": "pre_tool_use",
    "PermissionRequest": "permission_request",
    "PostToolUse": "post_tool_use",
    "PreCompact": "pre_compact",
    "PostCompact": "post_compact",
    "SessionStart": "session_start",
    "SessionEnd": "session_end",
    "UserPromptSubmit": "user_prompt_submit",
    "SubagentStart": "subagent_start",
    "SubagentStop": "subagent_stop",
    "Stop": "stop",
    "Interrupt": "interrupt",
}
_CODEX_UNMATCHED_EVENTS = frozenset({"UserPromptSubmit", "Stop", "Interrupt"})
_CODEX_CONTEXT_EVENTS = frozenset(
    {"PreToolUse", "PostToolUse", "SessionStart", "UserPromptSubmit", "SubagentStart"}
)
_CODEX_DEFAULT_CONTEXT_LIMIT = 2500


def codex_hook_hash(event: str, group: dict[str, Any], handler: dict[str, Any]) -> str:
    """Reproduce the identity hash Codex stores as `hooks.state.<key>.trusted_hash`."""
    timeout = handler.get("timeout")
    if event in {"SessionEnd", "Interrupt"}:
        timeout = max(1, min(timeout if timeout is not None else 1, 3))
    else:
        timeout = max(1, timeout if timeout is not None else 600)
    identity_handler: dict[str, Any] = {
        "type": "command",
        "command": handler.get("command", ""),
        "timeout": timeout,
        "async": bool(handler.get("async", False)),
    }
    if handler.get("statusMessage") is not None:
        identity_handler["statusMessage"] = handler["statusMessage"]
    limit = handler.get("additionalContextLimit")
    if event in _CODEX_CONTEXT_EVENTS and limit not in {None, _CODEX_DEFAULT_CONTEXT_LIMIT}:
        identity_handler["additionalContextLimit"] = limit
    identity: dict[str, Any] = {
        "event_name": _CODEX_EVENT_LABELS[event],
        "hooks": [identity_handler],
    }
    matcher = None if event in _CODEX_UNMATCHED_EVENTS else group.get("matcher")
    if matcher is not None:
        identity["matcher"] = matcher
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def codex_hook_trust(hook_file: Path, config_file: Path) -> dict[str, list[str]]:
    """Classify Brief-Spec Codex hooks as trusted, untrusted, or modified since review."""
    result: dict[str, list[str]] = {"trusted": [], "untrusted": [], "modified": []}
    try:
        hooks = json.loads(hook_file.read_text(encoding="utf-8")).get("hooks", {})
    except (OSError, json.JSONDecodeError, AttributeError):
        return result
    try:
        with config_file.open("rb") as handle:
            config = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        config = {}
    hooks_config = config.get("hooks", {}) if isinstance(config, dict) else {}
    trust_state = hooks_config.get("state", {}) if isinstance(hooks_config, dict) else {}
    paths = dict.fromkeys((str(hook_file), str(hook_file.resolve(strict=False))))
    for event, groups in hooks.items() if isinstance(hooks, dict) else ():
        if event not in _CODEX_EVENT_LABELS or not isinstance(groups, list):
            continue
        for group_index, group in enumerate(groups):
            handlers = group.get("hooks", []) if isinstance(group, dict) else []
            for handler_index, handler in enumerate(handlers):
                if not isinstance(handler, dict) or not _is_brief_spec_command(
                    str(handler.get("command", ""))
                ):
                    continue
                suffix = f"{_CODEX_EVENT_LABELS[event]}:{group_index}:{handler_index}"
                stored = next(
                    (
                        trust_state[f"{path}:{suffix}"].get("trusted_hash")
                        for path in paths
                        if isinstance(trust_state.get(f"{path}:{suffix}"), dict)
                    ),
                    None,
                )
                if stored is None:
                    result["untrusted"].append(event)
                elif stored == codex_hook_hash(event, group, handler):
                    result["trusted"].append(event)
                else:
                    result["modified"].append(event)
    return result


def _is_brief_spec_command(command: str) -> bool:
    return any(marker in command for marker in ("brief-spec.pyz", "briefspec.pyz"))


def _codex_trust_check(hook_file: Path) -> Check | None:
    from briefspec.installers import _runtime_home

    if not hook_file.is_file():
        return None
    trust = codex_hook_trust(hook_file, _runtime_home(Runtime.CODEX) / "config.toml")
    pending = trust["untrusted"] + trust["modified"]
    if not pending and not trust["trusted"]:
        return None
    if not pending:
        return Check("hook trust", "PASS", f"{len(trust['trusted'])} Brief-Spec hooks trusted")
    details = []
    if trust["untrusted"]:
        details.append(f"not yet reviewed: {', '.join(trust['untrusted'])}")
    if trust["modified"]:
        details.append(f"changed since review: {', '.join(trust['modified'])}")
    return Check(
        "hook trust",
        "WARN",
        "; ".join(details),
        "Open Codex and approve the Brief-Spec hooks in /hooks; Codex skips untrusted hooks "
        "without an error, and `codex exec` never shows the review screen",
    )


def _kimi_plugin_registered(registry: Path, plugin_root: Path) -> bool:
    try:
        value = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    plugins = value.get("plugins") if isinstance(value, dict) else None
    if not isinstance(plugins, list):
        return False
    registered = any(
        isinstance(item, dict)
        and item.get("id") == "brief-spec"
        and item.get("enabled") is not False
        and Path(str(item.get("root", ""))).resolve(strict=False)
        == plugin_root.resolve(strict=False)
        for item in plugins
    )
    return registered and _contains_hook(plugin_root / "kimi.plugin.json")


def _resolve_scope(runtime: Runtime, scope: str, project: Path | None) -> tuple[str, Path | None]:
    if scope != "auto":
        return scope, project
    candidate = (project or Path.cwd()).resolve()
    if (
        receipt_path(runtime, "project", candidate).is_file()
        or _legacy_receipt_path(runtime, "project", candidate).is_file()
    ):
        return "project", candidate
    return "user", None


def doctor_runtime(
    runtime: Runtime,
    *,
    scope: str = "auto",
    project: Path | None = None,
    probe: bool = False,
    optional_when_absent: bool = False,
) -> dict[str, Any]:
    scope, project = _resolve_scope(runtime, scope, project)
    project = (project or Path.cwd()).resolve() if scope == "project" else None
    skills, pyz, hook = _project_targets(runtime, project) if project else _user_targets(runtime)
    checks: list[Check] = []
    checks.append(
        Check("core", "PASS", f"Python {sys.version_info.major}.{sys.version_info.minor}")
    )
    canonical_receipt = receipt_path(runtime, scope, project)
    legacy_receipt = _legacy_receipt_path(runtime, scope, project)
    receipt = canonical_receipt if canonical_receipt.is_file() else legacy_receipt
    receipt_value: dict[str, Any] = {}
    if receipt.is_file():
        try:
            loaded = json.loads(receipt.read_text(encoding="utf-8"))
            receipt_value = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            receipt_value = {}
    checks.append(
        Check(
            "receipt",
            "PASS" if receipt.is_file() else "FAIL",
            str(receipt),
            (
                None
                if receipt.is_file()
                else f"Run: brief-spec setup {runtime.value} --scope {scope}"
            ),
        )
    )
    if legacy_receipt != canonical_receipt and legacy_receipt.is_file():
        checks.append(
            Check(
                "state migration",
                "FAIL",
                f"legacy receipt at {legacy_receipt}",
                f"Run: brief-spec doctor {runtime.value} --scope {scope} --fix",
            )
        )
    installed_version = str(
        receipt_value.get("brief_spec_version") or receipt_value.get("briefspec_version") or ""
    )
    version_ok = installed_version == __version__
    checks.append(
        Check(
            "version alignment",
            "PASS" if version_ok else "FAIL",
            f"installed {installed_version or 'unknown'}; running {__version__}",
            None if version_ok else f"Run: brief-spec setup {runtime.value} --scope {scope}",
        )
    )
    modified_owned: list[str] = []
    missing_owned: list[str] = []
    for entry in receipt_value.get("files", []):
        if not isinstance(entry, dict) or entry.get("kind") != "owned":
            continue
        raw_path = entry.get("path")
        expected = entry.get("sha256")
        if not isinstance(raw_path, str) or not isinstance(expected, str) or expected == "dry-run":
            continue
        owned_path = Path(raw_path)
        if not owned_path.is_file():
            missing_owned.append(raw_path)
            continue
        actual = hashlib.sha256(owned_path.read_bytes()).hexdigest()
        if actual != expected:
            modified_owned.append(raw_path)
    if modified_owned:
        checks.append(
            Check(
                "managed file drift",
                "WARN",
                "; ".join(modified_owned),
                "Review .brief-spec-new candidates; use doctor --fix --replace-modified only "
                "after approving replacement",
            )
        )
    if missing_owned:
        checks.append(
            Check(
                "managed file missing",
                "FAIL",
                "; ".join(missing_owned),
                "Run doctor --fix to restore receipt-owned files",
            )
        )
    installed_skills = all(
        (skills / name / "SKILL.md").is_file()
        for name in ("brief-spec", "outcome-brief", "session-checkpoint")
    )
    checks.append(
        Check(
            "skills",
            "PASS" if installed_skills else "FAIL",
            str(skills),
            None if installed_skills else "Re-run the installer",
        )
    )
    skills_only = runtime is Runtime.KIMI and scope == "project"
    if skills_only:
        user_skills, user_pyz, user_hook = _user_targets(Runtime.KIMI)
        user_lifecycle = (
            receipt_path(Runtime.KIMI, "user").is_file()
            and user_pyz.is_file()
            and user_hook.is_file()
            and _contains_hook(user_skills.parent / "kimi.plugin.json")
        )
        checks.append(
            Check(
                "runtime bundle",
                "PASS" if user_lifecycle else "WARN",
                str(user_pyz) if user_lifecycle else "project scope installs skills only",
                None
                if user_lifecycle
                else "Run: brief-spec setup kimi --scope user for lifecycle automation",
            )
        )
        checks.append(
            Check(
                "hook configuration",
                "PASS" if user_lifecycle else "WARN",
                str(user_hook) if user_lifecycle else "Kimi plugins are user-wide",
                None if user_lifecycle else "Install the Brief-Spec Kimi user plugin",
            )
        )
        if user_lifecycle:
            pyz = user_pyz
    else:
        checks.append(
            Check(
                "runtime bundle",
                "PASS" if pyz.is_file() else "FAIL",
                str(pyz),
                None if pyz.is_file() else "Re-run the installer",
            )
        )
        hook_ok = hook.is_file() and (
            (
                _kimi_plugin_registered(hook, skills.parent)
                if runtime is Runtime.KIMI
                else _contains_hook(hook)
            )
            or runtime is Runtime.GOOSE
            and "brief-spec-harness-capabilities" in hook.read_text(encoding="utf-8")
        )
        hook_status = (
            "WARN" if runtime is Runtime.GOOSE and hook_ok else ("PASS" if hook_ok else "FAIL")
        )
        checks.append(
            Check(
                "hook configuration",
                hook_status,
                str(hook),
                (
                    "No native Goose lifecycle is claimed"
                    if runtime is Runtime.GOOSE and hook_ok
                    else None
                    if hook_ok
                    else "Re-run the installer after resolving config conflicts"
                ),
            )
        )
        if runtime is Runtime.CODEX and hook_ok and (trust := _codex_trust_check(hook)):
            checks.append(trust)
    executable = host_executable(runtime)
    host_detail = executable or f"{runtime.value} is not on PATH"
    if executable:
        try:
            version_result = subprocess.run(
                [executable, "--version"],
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
            version_text = version_result.stdout.strip() or version_result.stderr.strip()
            if version_text:
                host_detail = f"{executable} ({version_text.splitlines()[0]})"
        except (OSError, subprocess.SubprocessError):
            host_detail = executable
    checks.append(
        Check(
            "host executable",
            "PASS" if executable else "WARN",
            host_detail,
            None if executable else f"Install/authenticate {runtime.value} for live host testing",
        )
    )

    if optional_when_absent and not receipt.is_file() and executable is None:
        optional_names = {
            "receipt",
            "version alignment",
            "skills",
            "runtime bundle",
            "hook configuration",
        }
        checks = [
            (
                Check(
                    item.name,
                    "WARN",
                    item.detail,
                    f"Install {runtime.value} and run Brief-Spec setup when this host is enabled",
                )
                if item.name in optional_names and item.status == "FAIL"
                else item
            )
            for item in checks
        ]

    if probe and pyz.is_file():
        payload = {
            "hook_event_name": "SessionStart",
            "session_id": "briefspec-doctor",
            "timestamp": "2026-01-01T00:00:00Z",
            "cwd": str(project or Path.cwd()),
        }
        with tempfile.TemporaryDirectory() as temporary:
            env = dict(os.environ)
            env["BRIEF_SPEC_HOME"] = temporary
            result = subprocess.run(
                [
                    sys.executable,
                    str(pyz),
                    "hook",
                    "--provider",
                    runtime.value,
                    "--event",
                    "SessionStart",
                ],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                env=env,
                timeout=15,
                check=False,
            )
        probe_ok = result.returncode == 0
        checks.append(
            Check(
                "synthetic hook",
                "PASS" if probe_ok else "FAIL",
                result.stdout.strip() or result.stderr.strip() or f"exit {result.returncode}",
                None if probe_ok else "Run the hook command directly for diagnostics",
            )
        )
    status = (
        "FAIL"
        if any(item.status == "FAIL" for item in checks)
        else ("WARN" if any(item.status == "WARN" for item in checks) else "PASS")
    )
    return {
        "runtime": runtime.value,
        "scope": scope,
        "project": str(project) if project else None,
        "status": status,
        "checks": [asdict(item) for item in checks],
    }


def doctor_all_scopes(
    runtime: Runtime,
    *,
    project: Path | None = None,
    probe: bool = False,
    optional_when_absent: bool = False,
) -> list[dict[str, Any]]:
    """Report user scope and every receipt-backed project scope for one runtime."""
    targets: list[tuple[str, Path | None]] = [("user", None)]
    receipt_roots = dict.fromkeys(
        (
            receipt_path(runtime, "user").parent,
            _legacy_receipt_path(runtime, "user").parent,
        )
    )
    for receipts in receipt_roots:
        if not receipts.is_dir():
            continue
        for path in sorted(receipts.glob(f"{runtime.value}-project-*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            raw_project = value.get("project") if isinstance(value, dict) else None
            if isinstance(raw_project, str):
                candidate = Path(raw_project).resolve()
                if ("project", candidate) not in targets:
                    targets.append(("project", candidate))
    if project is not None:
        candidate = project.resolve()
        if ("project", candidate) not in targets:
            targets.append(("project", candidate))
    return [
        doctor_runtime(
            runtime,
            scope=scope,
            project=target,
            probe=probe,
            optional_when_absent=optional_when_absent,
        )
        for scope, target in targets
    ]
