from __future__ import annotations

import io
import json
import runpy
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import briefspec.cli as cli
from briefspec.errors import InstallConflict
from briefspec.models import HookDecision, Runtime, SessionState
from briefspec.state import save_session


def _clear_runtime_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(cli.os.environ):
        if (
            name.startswith("CLAUDE_CODE")
            or name.startswith("COPILOT")
            or name
            in {
                "CLAUDE_PLUGIN_ROOT",
                "CLAUDE_SESSION_ID",
                "CODEX_THREAD_ID",
                "KIMI_CODE_HOME",
                "GROK_HOME",
                "PI_CODING_AGENT_DIR",
                "OMP_PROFILE",
            }
        ):
            monkeypatch.delenv(name, raising=False)


def test_runtime_expansion() -> None:
    assert cli._runtimes("codex") == [Runtime.CODEX]
    assert cli._runtimes("all") == list(Runtime)


def test_runtime_detection_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_runtime_environment(monkeypatch)
    assert cli._detect_runtime({"provider": "copilot"}) is Runtime.COPILOT
    assert cli._detect_runtime({}) is Runtime.CODEX

    monkeypatch.setenv("CLAUDE_CODE_SESSION", "1")
    assert cli._detect_runtime({}) is Runtime.CLAUDE
    monkeypatch.delenv("CLAUDE_CODE_SESSION")

    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/plugin")
    assert cli._detect_runtime({}) is Runtime.CLAUDE
    monkeypatch.setenv("CODEX_THREAD_ID", "thread")
    assert cli._detect_runtime({}) is Runtime.CODEX

    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "1")
    assert cli._detect_runtime({}) is Runtime.CODEX
    assert cli._detect_runtime({"claude_session_id": "native"}) is Runtime.CODEX

    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT")
    monkeypatch.delenv("CODEX_THREAD_ID")
    monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT")
    monkeypatch.setenv("COPILOT_AGENT", "1")
    assert cli._detect_runtime({}) is Runtime.COPILOT


def test_read_text_supports_stdin_and_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("from stdin"))
    assert cli._read_text("-") == "from stdin"
    artifact = tmp_path / "brief.md"
    artifact.write_text("from file", encoding="utf-8")
    assert cli._read_text(str(artifact)) == "from file"


def test_human_printer_renders_all_result_shapes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli._print_result(
        [
            {
                "runtime": "codex",
                "scope": "user",
                "operations": [{"action": "write", "path": "/tmp/file", "detail": "asset"}],
                "warnings": ["warning"],
            },
            {
                "runtime": "claude",
                "status": "WARN",
                "checks": [
                    {
                        "status": "WARN",
                        "name": "host",
                        "detail": "missing",
                        "remediation": "Install it",
                    },
                    {
                        "status": "PASS",
                        "name": "core",
                        "detail": "ready",
                        "remediation": None,
                    },
                ],
            },
            "finished",
        ],
        False,
    )
    output = capsys.readouterr().out
    assert "write" in output and "warning" in output
    assert "Install it" in output and "core" in output
    assert "finished" in output


def test_json_printer_serializes_non_json_native_values(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli._print_result({"path": Path("/tmp/value")}, True)
    assert json.loads(capsys.readouterr().out) == {"path": str(Path("/tmp/value"))}


@pytest.mark.parametrize("command", ["install", "uninstall"])
def test_cli_dispatches_all_runtime_installation_commands(
    command: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[Runtime, str, bool]] = []

    def handler(
        runtime: Runtime,
        *,
        scope: str,
        project: Path | None,
        dry_run: bool,
    ) -> dict[str, Any]:
        assert project is None
        calls.append((runtime, scope, dry_run))
        return {
            "runtime": runtime.value,
            "scope": scope,
            "operations": [],
            "warnings": [],
        }

    attribute = "install_runtime" if command == "install" else "uninstall_runtime"
    monkeypatch.setattr(cli, attribute, handler)
    result = cli.main([command, "all", "--dry-run", "--json"])
    assert result == 0
    assert calls == [(runtime, "user", True) for runtime in Runtime]
    assert len(json.loads(capsys.readouterr().out)) == len(Runtime)


def test_cli_install_conflict_has_dedicated_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def conflict(*args: object, **kwargs: object) -> dict[str, Any]:
        raise InstallConflict("foreign file")

    monkeypatch.setattr(cli, "install_runtime", conflict)
    assert cli.main(["install", "codex"]) == 3
    assert "Installation conflict" in capsys.readouterr().err


def test_cli_expected_operational_error_returns_one(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def failure(*args: object, **kwargs: object) -> dict[str, Any]:
        raise OSError("disk unavailable")

    monkeypatch.setattr(cli, "install_runtime", failure)
    assert cli.main(["install", "codex"]) == 1
    assert "disk unavailable" in capsys.readouterr().err


@pytest.mark.parametrize(("status", "expected"), [("PASS", 0), ("WARN", 0), ("FAIL", 1)])
def test_cli_doctor_exit_status_tracks_health(
    status: str,
    expected: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "doctor_runtime",
        lambda *args, **kwargs: {
            "runtime": args[0].value,
            "scope": "user",
            "status": status,
            "checks": [],
        },
    )
    assert cli.main(["doctor", "codex", "--json"]) == expected
    assert json.loads(capsys.readouterr().out)["status"] == status


def test_cli_validate_checkpoint_and_invalid_outcome(
    checkpoint_text: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(checkpoint_text("teach")))
    assert cli.main(["validate", "checkpoint", "-", "--mode", "teach"]) == 0
    assert "VALID" in capsys.readouterr().out

    monkeypatch.setattr(sys, "stdin", io.StringIO("ordinary prose"))
    assert cli.main(["validate", "outcome", "-"]) == 1
    output = capsys.readouterr().out
    assert "INVALID" in output
    assert "Missing bounded outcome marker" in output


def test_cli_validate_auto_rejects_unmarked_text(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("ordinary prose"))
    assert cli.main(["validate", "auto", "-"]) == 1
    assert "No Brief-Spec marker found" in capsys.readouterr().err


def test_cli_validate_prints_warnings(
    outcome_text: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(outcome_text(outcome="x" * 501)))
    assert cli.main(["validate", "outcome", "-"]) == 0
    assert "WARN" in capsys.readouterr().out


def test_cli_auto_hook_uses_payload_runtime_and_emits_json(
    isolated_homes: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = {
        "provider": "copilot",
        "session_id": "auto-provider",
        "timestamp": "2026-07-31T12:00:00Z",
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert cli.main(["hook", "--provider", "auto", "--event", "sessionStart"]) == 0
    assert "additionalContext" in json.loads(capsys.readouterr().out)


def test_kimi_hook_uses_native_stdout_context_and_exit_two_blocking(
    isolated_homes: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = {"session_id": "kimi-native", "timestamp": "2026-07-31T12:00:00Z"}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert cli.main(["hook", "--provider", "kimi", "--event", "SessionStart"]) == 0
    assert capsys.readouterr().out.startswith("Brief-Spec is active")

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(
        cli,
        "process_event",
        lambda *_args, **_kwargs: HookDecision(action="block", reason="repair the handoff"),
    )
    assert cli.main(["hook", "--provider", "kimi", "--event", "Stop"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "repair the handoff" in captured.err


def test_cli_vscode_profile_emits_nested_host_output(
    isolated_homes: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = {
        "provider": "copilot",
        "session_id": "vscode-provider",
        "timestamp": "2026-07-31T12:00:00Z",
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert (
        cli.main(
            [
                "hook",
                "--provider",
                "copilot",
                "--event",
                "SessionStart",
                "--output-profile",
                "vscode",
            ]
        )
        == 0
    )
    rendered = json.loads(capsys.readouterr().out)
    assert rendered["additionalContext"]
    assert rendered["hookSpecificOutput"]["hookEventName"] == "SessionStart"


def test_cli_hook_parser_failure_is_fail_open(
    isolated_homes: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("{not-json"))
    assert cli.main(["hook", "--provider", "codex", "--event", "SessionStart"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {}
    assert "fail-open" in captured.err


def test_cli_config_show_init_conflict_and_force(
    isolated_homes: dict[str, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["config", "show", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["checkpoint"]["policy"] == "suggest"

    project = tmp_path / "project"
    project.mkdir()
    args = ["config", "init", "--scope", "project", "--project", str(project)]
    assert cli.main(args) == 0
    config_path = project / ".brief-spec.toml"
    assert config_path.is_file()
    capsys.readouterr()
    assert cli.main(args) == 3
    assert "Refusing to overwrite" in capsys.readouterr().err
    assert cli.main([*args, "--force"]) == 0


def test_cli_user_config_init_uses_private_state_home(
    isolated_homes: dict[str, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["config", "init"]) == 0
    assert (isolated_homes["state"] / "config.toml").is_file()
    assert str(isolated_homes["state"]) in capsys.readouterr().out


def test_cli_state_list_prune_defaults_and_reset(
    isolated_homes: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = SessionState.new(
        Runtime.CODEX,
        "cli-state",
        datetime(2026, 1, 1, tzinfo=UTC),
    )
    save_session(state)
    assert cli.main(["state", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["session_id"] == "cli-state"

    observed: list[tuple[int, bool]] = []
    monkeypatch.setattr(
        cli,
        "prune_sessions",
        lambda days, dry_run: observed.append((days, dry_run)) or [Path("/old/state.json")],
    )
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda: {"state": {"retention_days": 21}},
    )
    assert cli.main(["state", "prune", "--dry-run"]) == 0
    assert observed == [(21, True)]
    assert str(Path("/old/state.json")) in capsys.readouterr().out

    monkeypatch.setattr(cli, "reset_session", lambda runtime, session: False)
    assert cli.main(["state", "reset", "--runtime", "codex", "--session", "missing"]) == 1
    monkeypatch.setattr(cli, "reset_session", lambda runtime, session: True)
    assert cli.main(["state", "reset", "--runtime", "codex", "--session", "found"]) == 0


def test_cli_state_prune_explicit_days_bypasses_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[int] = []
    monkeypatch.setattr(
        cli,
        "prune_sessions",
        lambda days, dry_run: observed.append(days) or [],
    )
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda: pytest.fail("explicit retention must not load config"),
    )
    assert cli.main(["state", "prune", "--older-than", "3"]) == 0
    assert observed == [3]


def test_python_module_entrypoint_delegates_to_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "main", lambda: 17)
    with pytest.raises(SystemExit) as raised:
        runpy.run_module("briefspec.__main__", run_name="__main__")
    assert raised.value.code == 17


def test_cli_prints_non_ascii_on_a_cp1252_console(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A Windows console defaults to cp1252 and cannot encode the em dashes and
    arrows that briefs legitimately contain. The CLI must ask for UTF-8 instead of
    dying with UnicodeEncodeError."""
    brief = tmp_path / "outcome.md"
    brief.write_text(
        "<!-- briefspec:outcome:v1 -->\n"
        "## Outcome Brief\n\n"
        "Status: DONE\n"
        "Outcome: The gate is green — every check passed.\n"
        "Human action: None\n\n"
        "Proof:\n"
        "- [direct/pass] `pytest` → 0 failures\n\n"
        "Gaps:\n- None\n\n"
        "Next:\n- None\n\n"
        "Open:\n- None\n"
        "<!-- /briefspec -->\n",
        encoding="utf-8",
    )
    cp1252 = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", cp1252)
    monkeypatch.setattr(sys, "stderr", io.TextIOWrapper(io.BytesIO(), encoding="cp1252"))

    assert cli.main(["validate", "outcome", str(brief)]) == 0

    sys.stdout.flush()
    written = sys.stdout.buffer.getvalue().decode("utf-8")
    assert "VALID" in written
    assert "—" in written
