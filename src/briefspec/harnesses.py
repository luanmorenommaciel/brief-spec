from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from briefspec import __version__
from briefspec.continuity import human_frame_delivery_tier
from briefspec.models import Runtime


@dataclass(frozen=True, slots=True)
class HarnessAdapter:
    runtime: Runtime
    maturity: str
    executables: tuple[str, ...]
    user_scope: bool
    project_scope: bool
    skill_discovery: bool
    lifecycle_hooks: bool
    final_output: bool
    pre_compact: bool
    artifact_links: bool
    background_sessions: bool
    subagents: bool
    session_metadata: bool
    model_metadata: bool
    hook_events: tuple[str, ...]
    notes: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return self.runtime.value

    def executable(self) -> str | None:
        for candidate in self.executables:
            if found := shutil.which(candidate):
                return found
        return None

    def detect(self) -> dict[str, Any]:
        executable = self.executable()
        return {
            "harness": self.name,
            "detected": executable is not None,
            "executable": executable,
        }

    def installation_plan(
        self,
        *,
        scope: str = "user",
        project: Path | None = None,
    ) -> dict[str, Any]:
        from briefspec.installers import install_runtime

        return install_runtime(self.runtime, scope=scope, project=project, dry_run=True)

    def setup(
        self,
        *,
        scope: str = "user",
        project: Path | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        from briefspec.installers import install_runtime

        return install_runtime(self.runtime, scope=scope, project=project, dry_run=dry_run)

    def normalize_event(
        self,
        payload: dict[str, Any],
        event_name: str | None = None,
    ) -> Any:
        from briefspec.adapters.registry import normalize_event

        return normalize_event(self.runtime, payload, event_name)

    def material_event(
        self,
        event: Any,
        *,
        method: str = "general",
        phase: str | None = None,
    ) -> dict[str, Any] | None:
        """Project a material boundary for an optional consumer such as Chronicle."""
        from briefspec.events import material_event_candidate

        if event.runtime is not self.runtime:
            raise ValueError(
                f"{self.name} adapter cannot project a {event.runtime.value} runtime event"
            )
        return material_event_candidate(event, method=method, phase=phase)

    def probe(
        self,
        *,
        scope: str = "auto",
        project: Path | None = None,
    ) -> dict[str, Any]:
        from briefspec.diagnostics import doctor_runtime

        return doctor_runtime(self.runtime, scope=scope, project=project, probe=True)

    def uninstall(
        self,
        *,
        scope: str = "user",
        project: Path | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        from briefspec.installers import uninstall_runtime

        return uninstall_runtime(self.runtime, scope=scope, project=project, dry_run=dry_run)

    def capabilities(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("runtime")
        value["harness"] = self.name
        value["brief_spec_version"] = __version__
        value["host_executable"] = self.executable()
        value["compatibility"] = "best-effort-fail-open"
        value["human_frame_delivery"] = human_frame_delivery_tier(
            final_output=self.final_output,
            lifecycle_hooks=self.lifecycle_hooks,
        )
        value["method_contexts"] = ["general", "seamwise", "task-spec", "converge"]
        value["supported_scopes"] = [
            scope
            for scope, enabled in (("user", self.user_scope), ("project", self.project_scope))
            if enabled
        ]
        value["hook_events"] = list(self.hook_events)
        value["notes"] = list(self.notes)
        value.pop("executables")
        value.pop("user_scope")
        value.pop("project_scope")
        return value


_STANDARD_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PostToolUse",
    "PreCompact",
    "Stop",
)

_ADAPTERS = {
    Runtime.CODEX: HarnessAdapter(
        Runtime.CODEX,
        "live-verified",
        ("codex",),
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        False,
        True,
        True,
        True,
        _STANDARD_EVENTS,
    ),
    Runtime.CLAUDE: HarnessAdapter(
        Runtime.CLAUDE,
        "live-verified",
        ("claude",),
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        _STANDARD_EVENTS,
    ),
    Runtime.OMP: HarnessAdapter(
        Runtime.OMP,
        "live-verified",
        ("omp",),
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        (
            "session_start",
            "before_agent_start",
            "tool_result",
            "session.compacting",
            "session_stop",
        ),
    ),
    Runtime.GROK: HarnessAdapter(
        Runtime.GROK,
        "live-verified",
        ("grok",),
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        (*_STANDARD_EVENTS, "SubagentStart", "SubagentStop", "PreToolUse"),
        (
            "Grok records passive lifecycle events, but its passive hook stdout is not model "
            "context; automatic routing is provided by the installed native skill. The first "
            "PreToolUse hook of a classified task delivers the decision after that tool call. "
            "A Stop that is not a normal turn end is observe-only. The Stop "
            "hook supplies one bounded repair only when the turn still lacks a valid Outcome "
            "Brief or Session Checkpoint, never a wrap-only continuation after a completed brief.",
            "The live implementation gate uses Grok's native read_file/search_replace allowlist "
            "inside a disposable repository; shell, web, memory, and subagents remain disabled.",
        ),
    ),
    Runtime.KIMI: HarnessAdapter(
        Runtime.KIMI,
        "live-verified",
        ("kimi",),
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        (*_STANDARD_EVENTS, "SubagentStart", "SubagentStop"),
        ("Project scope installs skills; lifecycle hooks require the user plugin.",),
    ),
    Runtime.COPILOT: HarnessAdapter(
        Runtime.COPILOT,
        "experimental",
        ("copilot",),
        True,
        True,
        True,
        True,
        False,
        True,
        True,
        False,
        False,
        True,
        True,
        ("sessionStart", "userPromptSubmitted", "postToolUse", "preCompact", "agentStop"),
    ),
    Runtime.CURSOR: HarnessAdapter(
        Runtime.CURSOR,
        "experimental",
        ("cursor-agent", "agent"),
        True,
        True,
        True,
        True,
        False,
        True,
        True,
        False,
        True,
        True,
        True,
        _STANDARD_EVENTS,
    ),
    Runtime.GOOSE: HarnessAdapter(
        Runtime.GOOSE,
        "experimental",
        ("goose",),
        True,
        True,
        True,
        False,
        False,
        False,
        True,
        True,
        True,
        False,
        True,
        (),
        ("Lifecycle automation is not claimed until a native live gate is available.",),
    ),
}


def harness_adapter(runtime: Runtime | str) -> HarnessAdapter:
    return _ADAPTERS[Runtime(runtime)]


def harness_adapters() -> tuple[HarnessAdapter, ...]:
    return tuple(_ADAPTERS[runtime] for runtime in Runtime)


def detected_harnesses() -> list[Runtime]:
    return [adapter.runtime for adapter in harness_adapters() if adapter.executable()]
