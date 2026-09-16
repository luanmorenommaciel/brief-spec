from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from briefspec.adapters import normalize_event
from briefspec.config import DEFAULT_CONFIG
from briefspec.continuity import detect_method_context
from briefspec.delivery import load_delivery
from briefspec.diagnostics import codex_hook_hash, codex_hook_trust, doctor_runtime
from briefspec.hooks import SESSION_CONTEXT, process_event, render_decision, user_authored_text
from briefspec.installers import _user_targets, install_runtime
from briefspec.markdown import validate_outcome
from briefspec.models import EventType, HookDecision, Runtime
from briefspec.state import load_session
from briefspec.work_types import classify_task, is_soft_pivot

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)

NOTIFICATION = """<task-notification>
<task-id>a1</task-id>
<status>completed</status>
<result>Instead, review the orient checkpoint. The Converge factory must debug this.</result>
</task-notification>"""


def config(**sections: dict[str, Any]) -> dict[str, dict[str, Any]]:
    value = deepcopy(DEFAULT_CONFIG)
    for name, changes in sections.items():
        value[name].update(changes)
    return value


class Session:
    def __init__(self, runtime: Runtime, name: str, settings: dict[str, Any] | None = None):
        self.runtime = runtime
        self.name = name
        self.settings = settings or config()
        self.clock = NOW
        self.counter = 0

    def send(self, event_name: str, **payload: Any) -> HookDecision:
        self.counter += 1
        self.clock += timedelta(seconds=30)
        raw = {
            "session_id": self.name,
            "timestamp": self.clock.isoformat(),
            "nonce": self.counter,
            **payload,
        }
        normalized = normalize_event(self.runtime, raw, event_name)
        return process_event(normalized, raw, self.settings)

    def prompt(self, text: str) -> HookDecision:
        return self.send("UserPromptSubmit", prompt=text)

    def stop(self, text: str, **extra: Any) -> HookDecision:
        return self.send("Stop", last_assistant_message=text, **extra)

    @property
    def state(self) -> Any:
        return load_session(self.runtime, self.name, NOW)


def test_host_wrappers_are_not_user_text() -> None:
    assert user_authored_text(NOTIFICATION) == ""
    assert user_authored_text("[SYSTEM NOTIFICATION - NOT USER INPUT]\nsomething") == ""
    assert user_authored_text("Stop hook feedback:\nBefore ending this turn, add") == ""
    slash = (
        "<command-name>/goal</command-name>\n<command-message>goal</command-message>\n"
        "<command-args>implement the parser fix</command-args>"
    )
    assert user_authored_text(slash) == "implement the parser fix"
    mixed = "<system-reminder>debug everything</system-reminder>\nReview pull request #7"
    assert user_authored_text(mixed) == "Review pull request #7"


def test_background_notification_does_not_reclassify_or_request_checkpoint(
    isolated_homes: dict[str, Path],
) -> None:
    session = Session(Runtime.CLAUDE, "notify")
    first = session.prompt("Explore this codebase and map its entry points.")
    assert "exploration + codebase" in (first.context or "")
    before = session.state
    decision = session.prompt(NOTIFICATION)
    after = session.state
    assert decision.context is None
    assert after.turn_count == before.turn_count
    assert after.classification_decision_id == before.classification_decision_id
    assert after.method_context == "general"
    assert "explicit-request" not in after.pending_reasons


def test_valid_outcome_closes_task_and_next_task_is_classified_fresh(
    isolated_homes: dict[str, Path],
    outcome_text: Callable[..., str],
) -> None:
    session = Session(Runtime.CODEX, "closing")
    session.prompt("Review pull request #42 for merge risk.")
    review_decision = session.state.classification_decision_id
    session.stop(outcome_text())
    closed = session.state
    assert closed.task_closed
    assert closed.work_type == "review"
    assert closed.classification_decision_id == review_decision
    assert session.prompt("thanks").context is None
    decision = session.prompt("Implement the retry feature in the uploader.")
    assert "implementation + feature" in (decision.context or "")
    assert not session.state.task_closed


def test_soft_pivot_switches_only_to_a_confident_different_type(
    isolated_homes: dict[str, Path],
) -> None:
    session = Session(Runtime.CLAUDE, "soft")
    session.prompt("Explore this codebase and map its entry points.")
    stay = session.prompt("Review pull request #42 for merge risk.")
    assert "exploration + codebase" in (stay.context or "")
    vague = session.prompt("Now that we are here, let's keep going with the same thing please.")
    assert "exploration + codebase" in (vague.context or "")
    moved = session.prompt(
        "Now that the map is done, let's research how other tools summarize agent output."
    )
    assert "research" in (moved.context or "")
    assert session.state.work_type == "research"


def test_soft_pivot_cues() -> None:
    assert is_soft_pivot("Now that the audit is done, research the market")
    assert is_soft_pivot("Moving on, fix the parser")
    assert is_soft_pivot("Agora que terminamos, pesquise as ferramentas")
    assert not is_soft_pivot("Review pull request #42")
    assert not is_soft_pivot("Do not go on to the next task yet.")
    assert not is_soft_pivot("The log line says 'now that' and nothing else.")


def test_full_guidance_once_then_short_reminder_until_compaction(
    isolated_homes: dict[str, Path],
) -> None:
    session = Session(Runtime.CLAUDE, "guidance")
    first = session.prompt("Explore this codebase and map its entry points.").context or ""
    second = session.prompt("Keep mapping the hook module please, then continue.").context or ""
    assert first.startswith("Brief-Spec classified this task as exploration + codebase")
    assert "Method context" not in second and "method context" not in second
    assert second.startswith("Brief-Spec task: exploration + codebase")
    assert "decision_id=" in second
    assert len(second) < len(first) / 2
    session.send("PreCompact")
    third = session.prompt("Keep mapping the hook module please, then continue.").context or ""
    assert third.startswith("Brief-Spec classified this task as")


def test_omp_receives_full_guidance_every_turn(isolated_homes: dict[str, Path]) -> None:
    session = Session(Runtime.OMP, "omp-guidance")
    session.prompt("Explore this codebase and map its entry points.")
    again = session.prompt("Keep mapping the hook module please, then continue.").context or ""
    assert again.startswith("Brief-Spec classified this task as")


def test_session_context_has_no_mid_sentence_line_break() -> None:
    assert "\n" not in SESSION_CONTEXT
    assert "compact form" in SESSION_CONTEXT


def test_checkpoint_window_restarts_after_outcome(
    isolated_homes: dict[str, Path],
    outcome_text: Callable[..., str],
) -> None:
    settings = config(checkpoint={"turns": 3, "policy": "suggest"})
    session = Session(Runtime.CLAUDE, "window", settings)
    for _ in range(3):
        session.prompt("Implement the retry feature in the uploader.")
    assert session.state.pending_checkpoint
    session.stop(outcome_text())
    state = session.state
    assert not state.pending_checkpoint
    assert state.window_turn_base == state.turn_count
    session.prompt("Implement the backoff feature in the uploader.")
    assert not session.state.pending_checkpoint
    nudge = session.send("PostToolUse", tool_name="Read")
    assert nudge.context is None


def test_stop_records_validation_and_claude_shows_invalid_notice(
    isolated_homes: dict[str, Path],
    outcome_text: Callable[..., str],
) -> None:
    claude = Session(Runtime.CLAUDE, "notice")
    claude.prompt("Implement the retry feature in the uploader.")
    invalid = outcome_text(status="DONE", human_action="Review it")
    decision = claude.stop(invalid)
    assert decision.action == "allow"
    assert decision.notice and "not valid" in decision.notice
    rendered = render_decision(Runtime.CLAUDE, EventType.AGENT_STOP, decision)
    assert rendered == {"systemMessage": decision.notice}
    state = claude.state
    assert state.last_brief_kind == "outcome"
    assert state.last_brief_valid is False
    assert state.briefs_invalid == 1
    assert any("human action" in error for error in state.last_brief_errors)

    claude.prompt("Implement the backoff feature in the uploader.")
    valid = claude.stop(outcome_text())
    assert valid.notice is None
    assert claude.state.last_brief_valid is True
    assert claude.state.last_brief_status == "DONE"
    assert claude.state.briefs_validated == 1

    codex = Session(Runtime.CODEX, "no-notice")
    codex.prompt("Implement the retry feature in the uploader.")
    silent = codex.stop(invalid)
    assert silent.notice is None
    assert codex.state.last_brief_valid is False
    assert render_decision(Runtime.CODEX, EventType.AGENT_STOP, silent) == {}


def test_plain_answer_without_marker_records_no_brief(isolated_homes: dict[str, Path]) -> None:
    session = Session(Runtime.CLAUDE, "plain")
    session.prompt("What does the checkpoint policy do in this repository?")
    decision = session.stop("It records eligibility and suggests a checkpoint later.")
    assert decision.notice is None
    assert session.state.last_brief_kind is None
    assert session.state.last_brief_valid is None


def test_compact_done_outcome_validates_and_matches_full_form(
    outcome_text: Callable[..., str],
) -> None:
    compact = """<!-- briefspec:outcome:v1 -->
## Outcome Brief

Status: DONE
Outcome: The parser now accepts empty input.
Proof: [direct/pass] `uv run pytest tests/test_parser.py` → 12 passed
<!-- /briefspec -->"""
    result = validate_outcome(compact)
    assert result.valid, result.errors
    assert result.data["Form"] == "compact"
    full = outcome_text(
        outcome="The parser now accepts empty input.",
        proof=("[direct/pass] `uv run pytest tests/test_parser.py` → 12 passed",),
    )
    compact_delivery, _ = load_delivery(compact, created_at="2026-09-16T12:00:00Z")
    full_delivery, _ = load_delivery(full, created_at="2026-09-16T12:00:00Z")
    assert compact_delivery["brief"] == full_delivery["brief"]


@pytest.mark.parametrize(
    "body",
    [
        "Status: REVIEW\nOutcome: Ready.\nProof: [direct/pass] `pytest` → ok",
        "Status: BLOCKED\nOutcome: Waiting.\nProof: [direct/pass] `pytest` → ok",
        "Status: DONE\nOutcome: Finished.",
        "Status: DONE\nOutcome: Finished.\nProof: [direct/pass] `pytest` → ok\nGaps:\n- One",
    ],
)
def test_compact_form_is_limited_to_honest_done(body: str) -> None:
    text = f"<!-- briefspec:outcome:v1 -->\n## Outcome Brief\n\n{body}\n<!-- /briefspec -->"
    assert not validate_outcome(text).valid


def test_enforce_does_not_demand_wrapper_for_compact_done(
    isolated_homes: dict[str, Path],
) -> None:
    settings = config(outcome={"policy": "enforce"}, checkpoint={"policy": "off"})
    session = Session(Runtime.CLAUDE, "compact-enforce", settings)
    session.prompt("Fix the typo in the parser error message.")
    compact = (
        "Done.\n\n<!-- briefspec:outcome:v1 -->\n## Outcome Brief\n\nStatus: DONE\n"
        "Outcome: The typo is fixed.\nProof: [direct/pass] `git diff --stat` → 1 file\n"
        "<!-- /briefspec -->"
    )
    assert session.stop(compact).action == "allow"


def test_grok_receives_classification_once_on_first_tool_call(
    isolated_homes: dict[str, Path],
) -> None:
    session = Session(Runtime.GROK, "grok-late")
    session.prompt("Review pull request #42 for merge risk.")
    first = session.send("PreToolUse", toolName="read_file")
    assert first.action == "allow"
    assert "review + pull-request" in (first.context or "")
    rendered = render_decision(Runtime.GROK, EventType.PRE_TOOL, first)
    assert rendered == {
        "hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": first.context}
    }
    assert "decision" not in rendered and "permissionDecision" not in json.dumps(rendered)
    assert session.send("PreToolUse", toolName="read_file").context is None


def test_grok_stop_repair_counts_as_delivery(isolated_homes: dict[str, Path]) -> None:
    session = Session(Runtime.GROK, "grok-repair")
    session.prompt("Review pull request #42 for merge risk.")
    repair = session.stop("BRIEF_SPEC_METADATA_PENDING", reason="end_turn")
    assert repair.action == "block"
    assert "review + pull-request" in (repair.reason or "")
    assert session.send("PreToolUse", toolName="read_file").context is None


def test_grok_session_end_stop_is_observe_only(isolated_homes: dict[str, Path]) -> None:
    session = Session(Runtime.GROK, "grok-end")
    session.prompt("Review pull request #42 for merge risk.")
    before = session.state.updated_at
    closing = session.stop("", reason="channel_closed")
    assert closing == HookDecision()
    assert session.state.updated_at == before
    other = session.stop("partial", reason="max_turns")
    assert other.action == "allow"


def test_copilot_receives_classification_on_first_post_tool(
    isolated_homes: dict[str, Path],
) -> None:
    session = Session(Runtime.COPILOT, "copilot-late")
    session.prompt("Review pull request #42 for merge risk.")
    late = session.send("postToolUse", toolName="view")
    assert "review + pull-request" in (late.context or "")
    assert session.send("postToolUse", toolName="view").context is None


def test_grok_manifest_registers_pre_tool_use(isolated_homes: dict[str, Path]) -> None:
    install_runtime(Runtime.GROK)
    _, _, hook = _user_targets(Runtime.GROK)
    value = json.loads(hook.read_text(encoding="utf-8"))
    assert "PreToolUse" in value["hooks"]
    command = value["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert "--event PreToolUse" in command


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("the results converge after two runs, fix it", "general"),
        ("Converge the two lists and fix it", "general"),
        ("Run the dock task through Converge", "converge"),
        ("converge settle this run", "converge"),
        ("Seal it with task-spec first", "task-spec"),
    ],
)
def test_method_context_needs_a_product_reference(text: str, expected: str) -> None:
    assert detect_method_context(text)[0] == expected


def _codex_config(path: Path, hooks_file: Path, trusted: dict[str, str]) -> None:
    lines = ["[hooks.state]"]
    for key, digest in trusted.items():
        # JSON string escaping is valid TOML and keeps Windows backslashes literal.
        lines.append(f"[hooks.state.{json.dumps(f'{hooks_file}:{key}')}]")
        lines.append(f'trusted_hash = "{digest}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_codex_trust_is_reported_per_hook(isolated_homes: dict[str, Path]) -> None:
    install_runtime(Runtime.CODEX)
    _, _, hooks_file = _user_targets(Runtime.CODEX)
    hooks = json.loads(hooks_file.read_text(encoding="utf-8"))["hooks"]
    labels = {
        "SessionStart": "session_start",
        "UserPromptSubmit": "user_prompt_submit",
        "PostToolUse": "post_tool_use",
        "PreCompact": "pre_compact",
        "Stop": "stop",
    }
    trusted = {
        f"{labels[event]}:0:0": codex_hook_hash(event, hooks[event][0], hooks[event][0]["hooks"][0])
        for event in ("SessionStart", "UserPromptSubmit", "PostToolUse")
    }
    trusted["stop:0:0"] = "sha256:" + "0" * 64
    config_file = isolated_homes["codex"] / "config.toml"
    _codex_config(config_file, hooks_file, trusted)
    trust = codex_hook_trust(hooks_file, config_file)
    assert sorted(trust["trusted"]) == ["PostToolUse", "SessionStart", "UserPromptSubmit"]
    assert trust["untrusted"] == ["PreCompact"]
    assert trust["modified"] == ["Stop"]
    report = doctor_runtime(Runtime.CODEX, scope="user")
    check = next(item for item in report["checks"] if item["name"] == "hook trust")
    assert check["status"] == "WARN"
    assert "PreCompact" in check["detail"] and "Stop" in check["detail"]
    assert "/hooks" in check["remediation"]


def test_codex_hash_matches_known_codex_identity() -> None:
    group = {"matcher": "", "hooks": []}
    handler = {"type": "command", "command": "echo hi", "timeout": 10}
    expected_payload = json.dumps(
        {
            "event_name": "post_tool_use",
            "hooks": [{"async": False, "command": "echo hi", "timeout": 10, "type": "command"}],
            "matcher": "",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    import hashlib

    assert codex_hook_hash("PostToolUse", group, handler) == (
        "sha256:" + hashlib.sha256(expected_payload.encode()).hexdigest()
    )
    stop = codex_hook_hash("Stop", group, handler)
    assert stop != codex_hook_hash("PostToolUse", group, handler)
    unmatched = json.dumps(
        {
            "event_name": "stop",
            "hooks": [{"async": False, "command": "echo hi", "timeout": 10, "type": "command"}],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    assert stop == "sha256:" + hashlib.sha256(unmatched.encode()).hexdigest()


CORPUS = [
    ("The login endpoint returns 500 after the last deploy, find the root cause", "debugging"),
    ("Fix the bug in the release script before we publish", "implementation", "bug"),
    ("Implement the login feature and write tests for it", "implementation", "feature"),
    (
        "Come up with a table of new features to be implemented and fixes, using deep research",
        "research",
        "feature",
    ),
    (
        "go ahead with the plan, implement, test and deploy the latest version after the tests",
        "implementation",
        "release",
    ),
    ("Audit the authentication module for security issues", "review", "security"),
    ("Why does the CI pipeline fail on Windows only?", "debugging"),
    ("Debug the crash in the parser when the input is empty", "debugging", "bug"),
    ("Walk me through how the hook pipeline works in this repository", "exploration", "codebase"),
    ("Create a roadmap for the next two quarters", "planning"),
    ("We have an outage in production, the API is down, start recovery", "operations", "incident"),
    ("Deploy the new build to staging and monitor the alerts", "operations", "release"),
    ("Update the README with the new install commands", "implementation", "document"),
    ("Upgrade the dependency on playwright and fix the failing tests", "implementation"),
    ("Investigate why the database migration is slow", "debugging", "data"),
    ("Now that the audit is done, let's research how other tools summarize output", "research"),
    ("Revise o pull request #12 e aponte os riscos", "review", "pull-request"),
    ("Corrija o bug no instalador do Codex", "implementation", "bug"),
    ("Por que o teste está falhando no CI?", "debugging", "test"),
    (
        "Implemente a nova funcionalidade de checkpoint e escreva testes",
        "implementation",
        "feature",
    ),
    ("Pesquise as ferramentas mais recentes de hooks para agentes", "research"),
    ("Planeje a próxima release com as etapas e os gates", "planning", "release"),
    ("Explore o repositório e mapeie os pontos de entrada", "exploration", "codebase"),
    ("Review and research this item for me.", "general", "general"),
    ("Revise the README wording for clarity and update the install section", "implementation"),
]


@pytest.mark.parametrize("case", CORPUS, ids=[case[0][:40] for case in CORPUS])
def test_everyday_prompt_corpus(case: tuple[str, ...]) -> None:
    prompt, expected_type, *rest = case
    result = classify_task(prompt)
    assert result.work_type == expected_type, result.rule_ids
    if rest:
        assert result.subject == rest[0], result.rule_ids
    assert not (result.origin == "inferred" and result.confidence == "high")
