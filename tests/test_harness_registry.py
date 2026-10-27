from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
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
from briefspec.state import list_sessions, load_session


def test_registry_declares_evidence_based_harness_maturity() -> None:
    adapters = harness_adapters()
    assert [adapter.runtime for adapter in adapters] == list(Runtime)
    assert {adapter.name for adapter in adapters if adapter.maturity == "live-verified"} == {
        "codex",
        "claude",
        "omp",
        "grok",
        "kimi",
        "antigravity",
    }
    assert {adapter.name for adapter in adapters if adapter.maturity == "hold"} == set()
    assert {adapter.name for adapter in adapters if adapter.maturity == "experimental"} == {
        "copilot",
        "cursor",
        "goose",
    }
    for adapter in adapters:
        capabilities = adapter.capabilities()
        assert capabilities["harness"] == adapter.name
        assert capabilities["maturity"] in {"live-verified", "hold", "experimental"}
        assert capabilities["supported_scopes"] == ["user", "project"]
        assert "model_metadata" in capabilities
        assert "session_metadata" in capabilities

    grok = harness_adapter(Runtime.GROK).capabilities()
    assert any("passive hook stdout" in note for note in grok["notes"])
    assert any("read_file/search_replace" in note for note in grok["notes"])


@pytest.mark.parametrize(
    ("runtime", "native_event", "expected"),
    [
        (Runtime.OMP, "before_agent_start", EventType.USER_PROMPT),
        (Runtime.OMP, "tool_result", EventType.POST_TOOL),
        (Runtime.OMP, "session_before_compact", EventType.PRE_COMPACT),
        (Runtime.OMP, "agent_end", EventType.AGENT_STOP),
        (Runtime.OMP, "session_stop", EventType.UNKNOWN),
        (Runtime.GROK, "UserPromptSubmit", EventType.USER_PROMPT),
        (Runtime.GROK, "SubagentStart", EventType.SUBAGENT_START),
        (Runtime.KIMI, "Stop", EventType.AGENT_STOP),
        (Runtime.KIMI, "SubagentStop", EventType.SUBAGENT_STOP),
    ],
)
def test_native_events_normalize_through_registry(
    runtime: Runtime,
    native_event: str,
    expected: EventType,
) -> None:
    event = harness_adapter(runtime).normalize_event(
        {"session_id": "adapter-fixture", "prompt": "Review pull request #9."},
        native_event,
    )
    assert event.runtime is runtime
    assert event.type is expected


def test_omp_installs_native_extension_and_is_receipt_owned(
    isolated_homes: dict[str, Path],
) -> None:
    install_runtime(Runtime.OMP)
    skills, pyz, extension = _user_targets(Runtime.OMP)
    text = extension.read_text(encoding="utf-8")
    assert (skills / "brief-spec" / "SKILL.md").is_file()
    assert pyz.is_file()
    assert 'pi.on("session.compacting"' in text
    assert 'pi.on("session_stop"' in text
    assert "systemPrompt" in text
    assert '"Stop"' in text
    assert "crypto.randomUUID()" in text
    assert "payload?.sessionId ?? activeSessionId" in text
    assert '"--payload-json"' in text
    receipt = json.loads(receipt_path(Runtime.OMP, "user").read_text(encoding="utf-8"))
    assert receipt["lifecycle_automation"] is True
    assert any(
        item["path"] == str(extension) and item["kind"] == "owned" for item in receipt["files"]
    )
    uninstall_runtime(Runtime.OMP)
    assert not extension.exists()


def test_grok_install_preserves_foreign_hook_entries(
    isolated_homes: dict[str, Path],
) -> None:
    _, _, hook = _user_targets(Runtime.GROK)
    hook.parent.mkdir(parents=True)
    hook.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {
                            "matcher": "",
                            "hooks": [{"type": "command", "command": "echo foreign"}],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    install_runtime(Runtime.GROK)
    installed = json.loads(hook.read_text(encoding="utf-8"))
    assert "echo foreign" in json.dumps(installed)
    assert "brief-spec.pyz" in json.dumps(installed)
    uninstall_runtime(Runtime.GROK)
    remaining = json.loads(hook.read_text(encoding="utf-8"))
    assert "echo foreign" in json.dumps(remaining)
    assert "brief-spec.pyz" not in json.dumps(remaining)


def test_kimi_user_plugin_preserves_foreign_plugins_and_project_is_skills_only(
    isolated_homes: dict[str, Path],
    tmp_path: Path,
) -> None:
    _, _, registry = _user_targets(Runtime.KIMI)
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": [
                    {
                        "id": "foreign-plugin",
                        "root": "/foreign",
                        "source": "local-path",
                        "enabled": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    install_runtime(Runtime.KIMI)
    installed = json.loads(registry.read_text(encoding="utf-8"))
    assert {item["id"] for item in installed["plugins"]} == {
        "foreign-plugin",
        "brief-spec",
    }
    user_report = doctor_runtime(Runtime.KIMI, scope="user")
    user_checks = {item["name"]: item["status"] for item in user_report["checks"]}
    assert user_checks["hook configuration"] == "PASS"

    project = tmp_path / "project"
    project.mkdir()
    install_runtime(Runtime.KIMI, scope="project", project=project)
    skills, pyz, capability = _project_targets(Runtime.KIMI, project.resolve())
    assert (skills / "brief-spec" / "SKILL.md").is_file()
    assert not pyz.exists()
    assert not capability.exists()
    receipt = json.loads(
        receipt_path(Runtime.KIMI, "project", project.resolve()).read_text(encoding="utf-8")
    )
    assert receipt["lifecycle_automation"] is False
    report = doctor_runtime(Runtime.KIMI, scope="project", project=project)
    checks = {item["name"]: item["status"] for item in report["checks"]}
    assert checks["runtime bundle"] == "PASS"
    assert checks["hook configuration"] == "PASS"

    uninstall_runtime(Runtime.KIMI)
    remaining = json.loads(registry.read_text(encoding="utf-8"))
    assert [item["id"] for item in remaining["plugins"]] == ["foreign-plugin"]


def test_setup_all_uses_detected_harnesses_only_and_reports_skips(
    isolated_homes: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "detected_harnesses", lambda: [Runtime.CODEX, Runtime.OMP])
    assert cli.main(["setup", "all", "--dry-run", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    by_runtime = {item["runtime"]: item for item in result}
    assert by_runtime["codex"]["operations"]
    assert by_runtime["omp"]["operations"]
    assert by_runtime["grok"]["operations"] == []
    assert "not detected" in by_runtime["grok"]["warnings"][0]


def test_setup_require_fails_before_any_write(
    isolated_homes: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "detected_harnesses", lambda: [Runtime.CODEX])
    assert (
        cli.main(
            [
                "setup",
                "all",
                "--require",
                "codex,claude,omp",
                "--json",
            ]
        )
        == 1
    )
    assert "claude, omp" in capsys.readouterr().err
    assert not isolated_homes["codex"].exists()


def test_doctor_fix_path_migrates_legacy_receipt_owned_state_transactionally(
    isolated_homes: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "canonical-state"
    legacy = tmp_path / "legacy-state"
    monkeypatch.setenv("BRIEF_SPEC_HOME", str(canonical))
    monkeypatch.setenv("BRIEFSPEC_HOME", str(legacy))
    old_bundle = isolated_homes["codex"] / "briefspec" / "briefspec.pyz"
    old_bundle.parent.mkdir(parents=True)
    old_bundle.write_bytes(b"legacy owned runtime")
    old_receipt = legacy / "receipts" / "codex-user.json"
    old_receipt.parent.mkdir(parents=True)
    old_receipt.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "briefspec_version": "0.4.0",
                "runtime": "codex",
                "scope": "user",
                "project": None,
                "files": [
                    {
                        "path": str(old_bundle),
                        "sha256": hashlib.sha256(old_bundle.read_bytes()).hexdigest(),
                        "kind": "owned",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    before = doctor_runtime(Runtime.CODEX, scope="user")
    assert {item["name"]: item["status"] for item in before["checks"]}["state migration"] == "FAIL"

    install_runtime(Runtime.CODEX)

    assert not old_bundle.exists()
    assert not old_receipt.exists()
    assert receipt_path(Runtime.CODEX, "user").is_file()
    after = doctor_runtime(Runtime.CODEX, scope="user")
    assert "state migration" not in {item["name"] for item in after["checks"]}


def test_legacy_sessions_remain_readable_without_copying_raw_task_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "canonical"
    legacy = tmp_path / "legacy"
    monkeypatch.setenv("BRIEF_SPEC_HOME", str(canonical))
    monkeypatch.setenv("BRIEFSPEC_HOME", str(legacy))
    session = "legacy-session"
    key = hashlib.sha256(f"codex\0{session}".encode()).hexdigest()
    state_path = legacy / "sessions" / key / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "runtime": "codex",
                "session_id": session,
                "started_at": "2026-08-12T12:00:00+00:00",
                "updated_at": "2026-08-12T12:00:00+00:00",
                "turn_count": 1,
            }
        ),
        encoding="utf-8",
    )
    loaded = load_session(Runtime.CODEX, session, datetime(2026, 8, 12, tzinfo=UTC))
    assert loaded.session_id == session
    assert [item["session_id"] for item in list_sessions()] == [session]
    assert not canonical.exists()
