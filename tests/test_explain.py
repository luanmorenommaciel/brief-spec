from __future__ import annotations

import json
import re
import socket
import time
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from explain_repo import commit, git, make_repo, name_only, write

import briefspec.explain as explain
from briefspec.adapters import normalize_event
from briefspec.cli import main
from briefspec.config import DEFAULT_CONFIG, load_config
from briefspec.hooks import process_event
from briefspec.models import HookDecision, Runtime

NOW = datetime(2026, 11, 9, 21, 0, tzinfo=UTC)

CALL_STACK = """\
#### Call stack
```text
stop_gate(path)
  -> load_config(path)
  -> StopGate.should_block(changed_files)
run(config, threshold)
  -> apply_threshold(config, threshold)
```

#### Pseudocode
```text
if apply_threshold(config, threshold): return the configured path
otherwise return nothing
```
"""


@pytest.fixture
def repo(tmp_path: Path, isolated_homes: dict[str, Path]) -> Path:
    return make_repo(tmp_path)


def frame(repo: Path, *args: str, capsys: pytest.CaptureFixture[str]) -> str:
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(repo)
        assert main(["explain", *args]) == 0
    return capsys.readouterr().out


def fill(text: str, *, extra_visuals: str = CALL_STACK) -> str:
    """Fill a frame the way an agent would: summary, one line per file, look-here notes."""
    text = re.sub(
        r"^(\*\*Summary:\*\*\n)?<!-- Exactly 3[^>]*-->$",
        lambda match: (
            (match.group(1) or "")
            + "Stop gate blocks once per large diff.\nrun() now takes a threshold.\n"
            "Watch the rename of the readme."
        ),
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(r"^(- `[^`]+`.*?) —\s*$", r"\1 — described", text, flags=re.MULTILINE)
    if extra_visuals:
        text = text.replace("### Files\n", extra_visuals + "\n### Files\n", 1)
    return text


def check(repo: Path, text: str, capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    path = repo.parent / "explain-back.md"
    path.write_text(text, encoding="utf-8")
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(repo)
        code = main(["explain", "--check", str(path), *args])
    return code, capsys.readouterr().out


def section(text: str, heading: str) -> list[str]:
    lines = text.splitlines()
    start = lines.index(heading) + 1
    out = []
    for line in lines[start:]:
        if line.startswith("#") or line.startswith("<!-- /"):
            break
        if line.startswith("- "):
            out.append(line)
    return out


# Done-when 1: the file list equals `git diff --name-only <base>...HEAD` exactly.


def test_file_list_equals_git_name_only_three_dot(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    expected = name_only(repo)
    assert "src/app/util.py" not in expected  # main moved on after the branch point
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(repo)
        assert main(["explain", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["files"] == expected
    listed = [
        re.match(r"- `([^`]+)`", line).group(1) for line in section(payload["frame"], "### Files")
    ]
    assert listed == expected


def test_base_override_and_quoted_paths_match_git_exactly(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.quotePath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    for base in ("main", "HEAD~1"):
        expected = name_only(repo, base)
        assert any(item.startswith('"') for item in expected)  # café.py is C-quoted
        with pytest.MonkeyPatch.context() as patch:
            patch.chdir(repo)
            assert main(["explain", "--json", "--base", base]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["files"] == expected
        assert payload["frame"].startswith(f"<!-- brief-spec:explain:v1 base={base} ")
    code, out = check(repo, fill(frame(repo, capsys=capsys)), capsys)
    assert (code, out.strip()) == (0, "VALID")


def test_frame_has_every_part_and_a_filled_frame_passes(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = frame(repo, capsys=capsys)
    for heading in (
        "### Summary",
        "### Visuals",
        "#### File tree",
        "#### Mermaid",
        "#### Signatures",
        "#### Before / after",
        "### Files",
        "### Look here first",
        "### Changed symbols",
    ):
        assert heading in text
    assert len(section(text, "### Look here first")) <= 3
    assert "- `src/app/core.py`: `run`, `apply_threshold`" in text
    code, out = check(repo, fill(text), capsys)
    assert (code, out.strip()) == (0, "VALID")


def test_phone_format_is_answer_first_without_mermaid_and_checks(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = frame(repo, "--format", "phone", capsys=capsys)
    assert "```mermaid" not in text and "|" not in text.replace("`", "")
    labels = re.findall(r"^\*\*(.+?):\*\*", text, re.MULTILINE)
    assert labels[:4] == ["Diff", "Summary", "Look here first", "Files"]
    code, out = check(repo, fill(text, extra_visuals=""), capsys)
    assert (code, out.strip()) == (0, "VALID")


def test_check_fails_when_a_diff_file_is_missing_from_the_file_list(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = fill(frame(repo, capsys=capsys)).replace("- `old.py` (+0 −2) — described\n", "")
    code, out = check(repo, text, capsys)
    assert code == 1 and "missing: old.py" in out


# Done-when 2 (offline half): Mermaid that does not parse fails --check. The real Mermaid
# parser runs over the same blocks in tests/test_explain_mermaid.py.


def test_check_fails_mermaid_that_does_not_parse(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = fill(frame(repo, capsys=capsys))
    broken = text.replace('d0 --> f0["logo.bin"]', 'd0 --> f0["logo.bin"\n  f0 -->')
    code, out = check(repo, broken, capsys)
    assert code == 1
    assert "Mermaid does not parse" in out


# Done-when 3: a file tree contains only files from the diff.


@pytest.mark.parametrize(
    ("tree", "valid", "message"),
    [
        (
            "fixture-repo/\n├── src/\n│   └── app/\n│       ├── core.py\n│       └── gate.py\n"
            "└── old.py",
            True,
            "",
        ),
        ("src/\n  app/\n    core.py\n    util.py", False, "'src/app/util.py', not in the diff"),
        ("lib/\n  core.py", False, "'lib/core.py', not in the diff"),
        ("src/\n  app/\n  tests/\n    core.py", False, "'src/tests/core.py'"),
    ],
)
def test_check_file_tree_lists_only_diff_files(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    tree: str,
    valid: bool,
    message: str,
) -> None:
    visual = f"#### File tree (focus)\n```text\n{tree}\n```\n"
    code, out = check(repo, fill(frame(repo, capsys=capsys), extra_visuals=visual), capsys)
    assert code == (0 if valid else 1), out
    assert message in out


# Done-when 4: every name in a call stack, signature view or pseudocode is real.


def test_symbol_from_a_touched_file_outside_the_hunks_is_accepted(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    skeleton = explain.build_skeleton(repo)
    assert "untouched_helper" not in skeleton.patch
    visual = "#### Call stack\n```text\nrun()\n  -> untouched_helper()\n```\n"
    code, out = check(repo, fill(frame(repo, capsys=capsys), extra_visuals=visual), capsys)
    assert (code, out.strip()) == (0, "VALID")


@pytest.mark.parametrize(
    ("visual", "name"),
    [
        (
            "#### Call stack\n```text\nstop_gate()\n  -> frobnicate_widget()\n```",
            "frobnicate_widget",
        ),
        ("#### Signatures\n```python\ndef stop_gate(path) -> GateVerdict:\n```", "GateVerdict"),
        ("#### Pseudocode\n```text\nfor each file: ghost_symbol.flush()\n```", "ghost_symbol"),
        ("```callstack\nrun() -> StopGate.explode_now()\n```", "explode_now"),
    ],
)
def test_check_fails_a_made_up_symbol(
    repo: Path, capsys: pytest.CaptureFixture[str], visual: str, name: str
) -> None:
    code, out = check(repo, fill(frame(repo, capsys=capsys), extra_visuals=visual + "\n"), capsys)
    assert code == 1
    assert f"names {name!r}, which is not in the diff or any touched file" in out


# Done-when 5: no prose block over 6 lines without a visual.


@pytest.mark.parametrize(("lines", "valid"), [(6, True), (7, False)])
def test_check_fails_a_seven_line_prose_block(
    repo: Path, capsys: pytest.CaptureFixture[str], lines: int, valid: bool
) -> None:
    prose = "### Notes\n" + "".join(f"Plain sentence number {n}.\n" for n in range(lines))
    text = fill(frame(repo, capsys=capsys)).replace(
        "### Changed symbols", prose + "\n### Changed symbols"
    )
    code, out = check(repo, text, capsys)
    assert code == (0 if valid else 1), out
    assert ("runs past 6 lines without a visual" in out) is not valid


def test_a_visual_resets_the_prose_count(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    prose = "".join(f"Sentence {n}.\n" for n in range(5))
    visual = f"### Notes\n{prose}\n```text\nrun()\n```\n{prose}\n"
    code, out = check(repo, fill(frame(repo, capsys=capsys), extra_visuals=visual), capsys)
    assert (code, out.strip()) == (0, "VALID")


# Done-when 6: at most 3 look-here entries, each path:line inside the diff; file summaries name
# files in the diff.


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        (
            ["src/app/core.py:9", "src/app/gate.py:1", "old.py:1", "src/app/core.py:10"],
            "look-here list has 4 entries; the limit is 3",
        ),
        (["src/app/util.py:1"], "look-here file 'src/app/util.py' is not in the diff"),
        (["src/app/core.py:99"], "src/app/core.py:99 is outside the diff hunks"),
        (["src/app/core.py"], "is not path:line"),
    ],
)
def test_check_look_here_rules(
    repo: Path, capsys: pytest.CaptureFixture[str], entries: list[str], message: str
) -> None:
    text = fill(frame(repo, capsys=capsys))
    current = section(text, "### Look here first")
    replacement = "\n".join(f"- `{entry}` — why" for entry in entries)
    text = text.replace("\n".join(current), replacement)
    code, out = check(repo, text, capsys)
    assert code == 1 and message in out, out


def test_look_here_on_a_deleted_file_uses_the_old_side(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = fill(frame(repo, capsys=capsys))
    current = section(text, "### Look here first")
    text = text.replace(
        "\n".join(current), "- `old.py:2` — removed\n- `src/app/core.py:9-13` — new"
    )
    code, out = check(repo, text, capsys)
    assert (code, out.strip()) == (0, "VALID")


def test_check_fails_a_file_summary_outside_the_diff(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = fill(frame(repo, capsys=capsys)).replace(
        "- `old.py` (+0 −2) — described", "- `old.py` (+0 −2) — described\n- `src/app/util.py` — x"
    )
    code, out = check(repo, text, capsys)
    assert code == 1 and "names 'src/app/util.py', which is not in the diff" in out


# Done-when 7: offline, no model call, read-only.


def test_explain_and_check_run_with_the_network_blocked(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("explain must not open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    status = git(repo, "status", "--porcelain")
    text = frame(repo, capsys=capsys)
    code, out = check(repo, fill(text), capsys)
    assert (code, out.strip()) == (0, "VALID")
    assert git(repo, "status", "--porcelain") == status


def test_explain_module_imports_no_network_or_model_client() -> None:
    source = Path(explain.__file__).read_text(encoding="utf-8")
    imports = set(re.findall(r"^(?:from|import) ([\w.]+)", source, re.MULTILINE))
    assert imports <= {
        "__future__",
        "hashlib",
        "json",
        "re",
        "subprocess",
        "time",
        "dataclasses",
        "datetime",
        "pathlib",
        "typing",
        "briefspec.config",
        "briefspec.delivery",
        "briefspec.errors",
        "briefspec.models",
        "briefspec.state",
    }


def test_explain_outside_git_fails_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["explain"]) == 1
    assert "brief-spec: git rev-parse" in capsys.readouterr().err


# Hook helpers.


def hook_config(**explain_settings: object) -> dict[str, dict[str, object]]:
    value = deepcopy(DEFAULT_CONFIG)
    value["explain"].update({"hook": True, **explain_settings})
    return value


def stop(
    repo: Path,
    seconds: int,
    config: dict[str, dict[str, object]] | None,
    *,
    runtime: Runtime = Runtime.CLAUDE,
    active: bool = False,
    session: str = "explain-session",
) -> HookDecision:
    payload = {
        "session_id": session,
        "timestamp": (NOW + timedelta(seconds=seconds)).isoformat(),
        "cwd": str(repo),
        "last_assistant_message": f"Finished step {seconds}.",
        "stop_hook_active": active,
    }
    name = "agentStop" if runtime is Runtime.COPILOT else "Stop"
    return process_event(normalize_event(runtime, payload, name), payload, config)


# Done-when 8: the hook ships off; one setting turns it on and off.


def test_hook_ships_off_and_one_setting_toggles_it(
    repo: Path, isolated_homes: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert DEFAULT_CONFIG["explain"]["hook"] is False
    calls: list[object] = []
    real_git = explain._Git

    def counting_git(*args: object, **kwargs: object) -> object:
        calls.append(args)
        return real_git(*args, **kwargs)

    monkeypatch.setattr(explain, "_Git", counting_git)
    config_file = isolated_homes["state"] / "config.toml"
    config_file.parent.mkdir(parents=True, exist_ok=True)

    off = stop(repo, 1, load_config(repo))
    assert off.action == "allow" and calls == []

    config_file.write_text('[explain]\nhook = "true"\n', encoding="utf-8")
    assert stop(repo, 2, load_config(repo)).action == "allow" and calls == []

    config_file.write_text("[explain]\nhook = true\n", encoding="utf-8")
    assert stop(repo, 3, load_config(repo)).action == "block"

    config_file.write_text("[explain]\nhook = false\n", encoding="utf-8")
    write(repo, "src/app/more.py", "def more():\n    return 1\n")
    commit(repo, "more")
    calls.clear()
    assert stop(repo, 4, load_config(repo)).action == "allow" and calls == []


def test_config_show_lists_the_explain_hook_off(
    isolated_homes: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["config", "show", "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["explain"] == {"hook": False, "hook_min_files": 3, "hook_min_lines": 100}


# Done-when 9: at most one block per diff; small, empty or unchanged diffs never block.


@pytest.mark.parametrize("runtime", [Runtime.CLAUDE, Runtime.CODEX])
def test_big_diff_blocks_once_with_the_frame_then_passes(repo: Path, runtime: Runtime) -> None:
    config = hook_config()
    first = stop(repo, 1, config, runtime=runtime)
    assert first.action == "block"
    assert "brief-spec explain" in first.reason
    assert explain.EXPLAIN_START in first.reason and "### Files" in first.reason
    for path in name_only(repo):
        assert f"- `{path}`" in first.reason
    second = stop(repo, 2, config, runtime=runtime)
    assert second.action == "allow" and second.reason is None
    third = stop(repo, 3, config, runtime=runtime, session="another-session")
    assert third.action == "allow"


def test_a_changed_diff_blocks_once_more(repo: Path) -> None:
    config = hook_config()
    assert stop(repo, 1, config).action == "block"
    assert stop(repo, 2, config).action == "allow"
    write(repo, "src/app/gate.py", (repo / "src/app/gate.py").read_text() + "\n# tweak\n")
    commit(repo, "tweak")
    assert stop(repo, 3, config).action == "block"
    assert stop(repo, 4, config).action == "allow"


def test_stop_hook_active_never_blocks(repo: Path) -> None:
    config = hook_config()
    assert stop(repo, 1, config, active=True).action == "allow"
    assert stop(repo, 2, config).action == "block"


def test_small_and_empty_diffs_never_block(tmp_path: Path, isolated_homes: dict[str, Path]) -> None:
    repo = tmp_path / "small"
    repo.mkdir()
    git(repo, "init", "-q")
    write(repo, "a.py", "x = 1\n")
    commit(repo, "base")
    config = hook_config()
    assert stop(repo, 1, config).action == "allow"  # empty diff: HEAD is main
    git(repo, "checkout", "-q", "-b", "feature")
    assert stop(repo, 2, config).action == "allow"  # empty diff on a branch
    write(repo, "a.py", "x = 1\ny = 2\n")
    write(repo, "b.py", "z = 3\n")
    commit(repo, "small")
    for seconds in range(3, 6):
        decision = stop(repo, seconds, config)
        assert decision.action == "allow"
        assert not decision.diagnostics


def test_line_threshold_and_custom_thresholds(
    tmp_path: Path, isolated_homes: dict[str, Path]
) -> None:
    repo = tmp_path / "lines"
    repo.mkdir()
    git(repo, "init", "-q")
    write(repo, "a.py", "x = 1\n")
    commit(repo, "base")
    git(repo, "checkout", "-q", "-b", "feature")
    write(repo, "a.py", "".join(f"value_{n} = {n}\n" for n in range(120)))
    commit(repo, "one big file")
    assert stop(repo, 1, hook_config(hook_min_lines=500)).action == "allow"
    assert stop(repo, 2, hook_config()).action == "block"


def test_running_explain_marks_the_diff_as_explained(
    repo: Path, isolated_homes: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    config_file = isolated_homes["state"] / "config.toml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text("[explain]\nhook = true\n", encoding="utf-8")
    frame(repo, capsys=capsys)
    assert stop(repo, 1, load_config(repo)).action == "allow"


@pytest.mark.parametrize("runtime", [Runtime.KIMI, Runtime.COPILOT, Runtime.GROK])
def test_other_runtimes_never_get_the_explain_block(repo: Path, runtime: Runtime) -> None:
    decision = stop(repo, 1, hook_config(), runtime=runtime)
    assert decision.action == "allow"


# Done-when 10: fail-open, under a second, no model call, only prompts `brief-spec explain`.


def test_injected_error_fails_open(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("injected")

    monkeypatch.setattr(explain, "stop_gate", boom)
    decision = stop(repo, 1, hook_config())
    assert decision.action == "allow"
    assert "explain hook fail-open: RuntimeError: injected" in decision.diagnostics


@pytest.mark.parametrize("failure", ["no-git", "budget", "not-a-repo", "bad-state"])
def test_hook_failures_pass_the_stop_through(
    repo: Path,
    tmp_path: Path,
    isolated_homes: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    target = repo
    if failure == "no-git":

        def missing(*_args: object, **_kwargs: object) -> None:
            raise FileNotFoundError("git")

        monkeypatch.setattr(explain.subprocess, "run", missing)
    elif failure == "budget":
        monkeypatch.setattr(explain, "HOOK_BUDGET_SECONDS", 0.0)
    elif failure == "not-a-repo":
        target = tmp_path / "plain"
        target.mkdir()
    else:
        state = explain._fingerprint_path(repo)
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text("{not json", encoding="utf-8")
    decision = stop(target, 1, hook_config())
    assert decision.action == "allow"
    assert any(item.startswith("explain hook fail-open") for item in decision.diagnostics)


def test_hook_is_fast_offline_and_writes_nothing_to_the_repo(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the hook must not open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    status = git(repo, "status", "--porcelain")
    started = time.perf_counter()
    decision = stop(repo, 1, hook_config())
    elapsed = time.perf_counter() - started
    assert decision.action == "block"
    assert elapsed < 1.0
    started = time.perf_counter()
    assert stop(repo, 2, hook_config()).action == "allow"
    assert time.perf_counter() - started < 1.0
    assert git(repo, "status", "--porcelain") == status


def test_cli_hook_blocks_once_then_passes(
    repo: Path,
    isolated_homes: dict[str, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_file = isolated_homes["state"] / "config.toml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text("[explain]\nhook = true\n", encoding="utf-8")
    outputs = []
    for seconds in (1, 2):
        payload = {
            "session_id": "cli-explain",
            "timestamp": (NOW + timedelta(seconds=seconds)).isoformat(),
            "cwd": str(repo),
            "last_assistant_message": f"Step {seconds}.",
        }
        args = ["hook", "--provider", "claude", "--event", "Stop", "--payload-json"]
        assert main([*args, json.dumps(payload)]) == 0
        outputs.append(json.loads(capsys.readouterr().out))
    assert outputs[0]["decision"] == "block"
    assert outputs[0]["reason"].startswith("Brief-Spec explain-back:")
    assert "decision" not in outputs[1]
