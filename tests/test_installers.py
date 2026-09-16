from __future__ import annotations

import json
from pathlib import Path

import pytest

from briefspec.errors import InstallConflict
from briefspec.installers import (
    _project_targets,
    _user_targets,
    install_runtime,
    install_runtimes,
    receipt_path,
    uninstall_runtime,
)
from briefspec.models import Runtime


def _brief_spec_hook_entry_count(path: Path) -> int:
    value = json.loads(path.read_text(encoding="utf-8"))
    return sum(
        1
        for entries in value["hooks"].values()
        for entry in entries
        if "brief-spec.pyz" in json.dumps(entry)
    )


@pytest.mark.parametrize("runtime", list(Runtime))
def test_user_install_dry_run_writes_nothing(
    runtime: Runtime, isolated_homes: dict[str, Path]
) -> None:
    result = install_runtime(runtime, dry_run=True)
    skills, pyz, hook = _user_targets(runtime)
    assert result["dry_run"]
    assert result["operations"]
    assert not skills.exists()
    assert not pyz.exists()
    assert not hook.exists()
    assert not receipt_path(runtime, "user").exists()


@pytest.mark.parametrize("runtime", list(Runtime))
def test_user_install_is_idempotent_and_does_not_duplicate_hooks(
    runtime: Runtime, isolated_homes: dict[str, Path]
) -> None:
    first = install_runtime(runtime)
    second = install_runtime(runtime)
    skills, pyz, hook = _user_targets(runtime)
    assert first["operations"] and second["operations"]
    assert (skills / "outcome-brief" / "SKILL.md").is_file()
    assert (skills / "session-checkpoint" / "SKILL.md").is_file()
    assert (skills / "brief-spec" / "SKILL.md").is_file()
    assert pyz.is_file()
    assert hook.is_file()
    assert receipt_path(runtime, "user").is_file()
    if runtime is Runtime.OMP:
        assert hook.read_text(encoding="utf-8").count('pi.on("') == 5
    elif runtime is Runtime.KIMI:
        registry = json.loads(hook.read_text(encoding="utf-8"))
        assert [item["id"] for item in registry["plugins"]].count("brief-spec") == 1
        manifest = json.loads((skills.parent / "kimi.plugin.json").read_text(encoding="utf-8"))
        assert len(manifest["hooks"]) == 7
    elif runtime is Runtime.GOOSE:
        assert json.loads(hook.read_text(encoding="utf-8"))["lifecycle_automation"] is False
    else:
        expected_hooks = 8 if runtime is Runtime.GROK else 5
        assert _brief_spec_hook_entry_count(hook) == expected_hooks


def test_omp_extension_uses_system_prompt_and_session_stop_enforcement(
    isolated_homes: dict[str, Path],
) -> None:
    install_runtime(Runtime.OMP)
    _, _, hook = _user_targets(Runtime.OMP)
    content = hook.read_text(encoding="utf-8")
    assert content.count('pi.on("') == 5
    assert "systemPrompt" in content
    assert "sessionContext" in content
    assert "display: false" not in content
    assert '"Stop"' in content
    assert "SessionStop" not in content


def test_foreign_skill_file_is_never_overwritten(
    isolated_homes: dict[str, Path],
) -> None:
    skills, _, _ = _user_targets(Runtime.CODEX)
    foreign = skills / "outcome-brief" / "SKILL.md"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("foreign skill", encoding="utf-8")
    with pytest.raises(InstallConflict, match="foreign skill file"):
        install_runtime(Runtime.CODEX)
    assert foreign.read_text(encoding="utf-8") == "foreign skill"


def test_marker_bearing_skill_without_receipt_is_foreign(
    isolated_homes: dict[str, Path],
) -> None:
    skills, _, _ = _user_targets(Runtime.CODEX)
    foreign = skills / "outcome-brief" / "SKILL.md"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("<!-- brief-spec: marker only -->\nforeign\n", encoding="utf-8")
    with pytest.raises(InstallConflict, match="foreign skill file"):
        install_runtime(Runtime.CODEX)
    assert "foreign" in foreign.read_text(encoding="utf-8")


def test_malformed_existing_hook_file_is_a_conflict(
    isolated_homes: dict[str, Path],
) -> None:
    _, _, hook = _user_targets(Runtime.CLAUDE)
    hook.parent.mkdir(parents=True)
    hook.write_text("{bad-json", encoding="utf-8")
    with pytest.raises(InstallConflict, match="malformed JSON"):
        install_runtime(Runtime.CLAUDE)
    assert hook.read_text(encoding="utf-8") == "{bad-json"


def test_install_preserves_preexisting_hook_entries(
    isolated_homes: dict[str, Path],
) -> None:
    _, _, hook = _user_targets(Runtime.CODEX)
    hook.parent.mkdir(parents=True)
    existing = {
        "hooks": {
            "Stop": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "echo foreign-stop-hook",
                        }
                    ]
                }
            ]
        },
        "foreignSetting": True,
    }
    hook.write_text(json.dumps(existing), encoding="utf-8")
    install_runtime(Runtime.CODEX)
    installed = json.loads(hook.read_text(encoding="utf-8"))
    assert installed["foreignSetting"] is True
    assert "foreign-stop-hook" in json.dumps(installed["hooks"]["Stop"])
    assert "brief-spec.pyz" in json.dumps(installed["hooks"]["Stop"])


def test_uninstall_removes_only_briefspec_hooks_and_preserves_foreign_entries(
    isolated_homes: dict[str, Path],
) -> None:
    _, _, hook = _user_targets(Runtime.CLAUDE)
    hook.parent.mkdir(parents=True)
    hook.write_text(
        json.dumps(
            {
                "hooks": {
                    "FutureEvent": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "echo foreign-hook",
                                }
                            ]
                        }
                    ]
                },
                "foreignSetting": "keep-me",
            }
        ),
        encoding="utf-8",
    )
    install_runtime(Runtime.CLAUDE)
    uninstall_runtime(Runtime.CLAUDE)
    remaining = json.loads(hook.read_text(encoding="utf-8"))
    assert remaining["foreignSetting"] == "keep-me"
    assert "foreign-hook" in json.dumps(remaining)
    assert "brief-spec.pyz" not in json.dumps(remaining)


def test_uninstall_preserves_modified_owned_file(
    isolated_homes: dict[str, Path],
) -> None:
    install_runtime(Runtime.CODEX)
    skills, _, _ = _user_targets(Runtime.CODEX)
    skill = skills / "outcome-brief" / "SKILL.md"
    skill.write_text(skill.read_text(encoding="utf-8") + "\nuser change\n", encoding="utf-8")
    result = uninstall_runtime(Runtime.CODEX)
    assert skill.exists()
    assert any("Preserved modified file" in warning for warning in result["warnings"])
    assert not receipt_path(Runtime.CODEX, "user").exists()


def test_claude_project_install_uses_native_skill_and_project_dir_anchor(
    isolated_homes: dict[str, Path], tmp_path: Path
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    install_runtime(Runtime.CLAUDE, scope="project", project=project)
    skills, pyz, hook = _project_targets(Runtime.CLAUDE, project.resolve())
    assert skills == project.resolve() / ".claude" / "skills"
    assert (skills / "outcome-brief" / "SKILL.md").is_file()
    assert (skills / "session-checkpoint" / "SKILL.md").is_file()
    assert pyz.is_file()
    settings = json.loads(hook.read_text(encoding="utf-8"))
    commands = [
        child["command"]
        for entries in settings["hooks"].values()
        for entry in entries
        for child in entry.get("hooks", [])
        if "brief-spec.pyz" in child.get("command", "")
    ]
    assert commands
    assert all(
        '"$CLAUDE_PROJECT_DIR/.claude/brief-spec/brief-spec.pyz"' in command for command in commands
    )
    assert not (project / ".agents").exists()


def test_copilot_project_install_contains_complete_offline_cloud_bridge(
    isolated_homes: dict[str, Path], tmp_path: Path
) -> None:
    project = tmp_path / "project with spaces"
    project.mkdir()
    result = install_runtime(
        Runtime.COPILOT,
        scope="project",
        project=project,
    )
    skills, pyz, hook = _project_targets(Runtime.COPILOT, project.resolve())
    instruction = project / ".github" / "instructions" / "brief-spec.instructions.md"
    assert result["scope"] == "project"
    assert (skills / "outcome-brief" / "SKILL.md").is_file()
    assert (skills / "session-checkpoint" / "SKILL.md").is_file()
    assert pyz.is_file()
    assert hook.is_file()
    assert instruction.is_file()
    hook_text = hook.read_text(encoding="utf-8")
    assert ".github/brief-spec/brief-spec.pyz" in hook_text
    assert "python" in hook_text
    assert receipt_path(Runtime.COPILOT, "project", project.resolve()).is_file()


def test_project_uninstall_preserves_modified_instruction(
    isolated_homes: dict[str, Path], tmp_path: Path
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    install_runtime(Runtime.COPILOT, scope="project", project=project)
    instruction = project / ".github" / "instructions" / "brief-spec.instructions.md"
    instruction.write_text(
        instruction.read_text(encoding="utf-8") + "\nLocal policy.\n",
        encoding="utf-8",
    )
    result = uninstall_runtime(Runtime.COPILOT, scope="project", project=project)
    assert instruction.exists()
    assert any("Preserved modified file" in warning for warning in result["warnings"])


def test_uninstall_removes_hook_file_created_by_briefspec(
    isolated_homes: dict[str, Path],
) -> None:
    _, _, hook = _user_targets(Runtime.COPILOT)
    install_runtime(Runtime.COPILOT)
    install_runtime(Runtime.COPILOT)
    assert hook.is_file()

    uninstall_runtime(Runtime.COPILOT)

    assert not hook.exists()


def test_multi_runtime_setup_rolls_back_every_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_target = tmp_path / "codex.txt"
    claude_target = tmp_path / "claude.txt"
    codex_target.write_text("before-codex", encoding="utf-8")
    claude_target.write_text("before-claude", encoding="utf-8")

    def managed(runtime: Runtime, **kwargs: object) -> list[Path]:
        return [codex_target if runtime is Runtime.CODEX else claude_target]

    def install(runtime: Runtime, *, dry_run: bool, **kwargs: object) -> dict[str, object]:
        if dry_run:
            return {"runtime": runtime.value, "operations": []}
        target = codex_target if runtime is Runtime.CODEX else claude_target
        target.write_text(f"changed-{runtime.value}", encoding="utf-8")
        if runtime is Runtime.CLAUDE:
            raise OSError("second runtime failed")
        return {"runtime": runtime.value, "operations": []}

    monkeypatch.setattr("briefspec.installers._managed_paths_for_runtime", managed)
    monkeypatch.setattr("briefspec.installers.install_runtime", install)
    with pytest.raises(OSError, match="second runtime"):
        install_runtimes([Runtime.CODEX, Runtime.CLAUDE])
    assert codex_target.read_text(encoding="utf-8") == "before-codex"
    assert claude_target.read_text(encoding="utf-8") == "before-claude"
