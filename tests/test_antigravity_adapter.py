from __future__ import annotations

import json
from pathlib import Path

import pytest

from briefspec import cli
from briefspec.diagnostics import doctor_runtime
from briefspec.harnesses import harness_adapter, harness_adapters
from briefspec.installers import (
    _project_targets,
    _user_targets,
    install_runtime,
    receipt_path,
    uninstall_runtime,
)
from briefspec.models import EventType, Runtime


def test_antigravity_is_registered_in_harness_adapters() -> None:
    adapters = harness_adapters()
    assert Runtime.ANTIGRAVITY in [adapter.runtime for adapter in adapters]

    antigravity = harness_adapter(Runtime.ANTIGRAVITY)
    assert antigravity.name == "antigravity"
    assert antigravity.executables == ("agy", "antigravity")
    assert antigravity.user_scope is True
    assert antigravity.project_scope is True

    capabilities = antigravity.capabilities()
    assert capabilities["harness"] == "antigravity"
    assert capabilities["maturity"] == "live-verified"
    assert "user" in capabilities["supported_scopes"]
    assert "project" in capabilities["supported_scopes"]


@pytest.mark.parametrize(
    ("native_event", "expected"),
    [
        ("SessionStart", EventType.SESSION_START),
        ("UserPromptSubmit", EventType.USER_PROMPT),
        ("PostToolUse", EventType.POST_TOOL),
        ("PreCompact", EventType.PRE_COMPACT),
        ("Stop", EventType.AGENT_STOP),
        ("SubagentStart", EventType.SUBAGENT_START),
        ("SubagentStop", EventType.SUBAGENT_STOP),
    ],
)
def test_antigravity_events_normalize(native_event: str, expected: EventType) -> None:
    event = harness_adapter(Runtime.ANTIGRAVITY).normalize_event(
        {
            "conversation_id": "antigravity-session-123",
            "prompt": "Implement feature X in brief-spec.",
        },
        native_event,
    )
    assert event.runtime is Runtime.ANTIGRAVITY
    assert event.type is expected
    assert event.session_id == "antigravity-session-123"


def test_antigravity_user_and_project_installation(
    isolated_homes: dict[str, Path],
    tmp_path: Path,
) -> None:
    # 1. User scope setup
    install_runtime(Runtime.ANTIGRAVITY, scope="user")
    skills, pyz, hook = _user_targets(Runtime.ANTIGRAVITY)
    assert (skills / "brief-spec" / "SKILL.md").is_file()
    assert (skills / "outcome-brief" / "SKILL.md").is_file()
    assert (skills / "session-checkpoint" / "SKILL.md").is_file()
    assert pyz.is_file()
    assert hook.is_file()

    user_hook_json = json.loads(hook.read_text(encoding="utf-8"))
    assert "brief-spec.pyz" in json.dumps(user_hook_json)

    receipt = json.loads(receipt_path(Runtime.ANTIGRAVITY, "user").read_text(encoding="utf-8"))
    assert receipt["runtime"] == "antigravity"
    assert receipt["lifecycle_automation"] is True

    user_report = doctor_runtime(Runtime.ANTIGRAVITY, scope="user")
    user_checks = {item["name"]: item["status"] for item in user_report["checks"]}
    assert user_checks["skills"] == "PASS"
    assert user_checks["runtime bundle"] == "PASS"
    assert user_checks["hook configuration"] == "PASS"

    uninstall_runtime(Runtime.ANTIGRAVITY, scope="user")

    # 2. Project scope setup
    project = tmp_path / "test_project"
    project.mkdir()
    install_runtime(Runtime.ANTIGRAVITY, scope="project", project=project)
    p_skills, p_pyz, p_hook = _project_targets(Runtime.ANTIGRAVITY, project.resolve())

    assert (p_skills / "brief-spec" / "SKILL.md").is_file()
    assert p_pyz.is_file()
    assert p_hook.is_file()

    project_report = doctor_runtime(Runtime.ANTIGRAVITY, scope="project", project=project)
    p_checks = {item["name"]: item["status"] for item in project_report["checks"]}
    assert p_checks["skills"] == "PASS"
    assert p_checks["runtime bundle"] == "PASS"
    assert p_checks["hook configuration"] == "PASS"

    uninstall_runtime(Runtime.ANTIGRAVITY, scope="project", project=project)
    assert not p_pyz.exists()


def test_antigravity_runtime_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"antigravity_session_id": "test-agy-123"}
    assert cli._detect_runtime(payload) is Runtime.ANTIGRAVITY

    monkeypatch.setenv("AGY_HOME", "/tmp/fake-agy")
    assert cli._detect_runtime({}) is Runtime.ANTIGRAVITY
