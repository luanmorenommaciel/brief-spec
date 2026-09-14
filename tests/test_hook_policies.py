from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from briefspec.adapters import normalize_event
from briefspec.config import DEFAULT_CONFIG
from briefspec.hooks import (
    _checkpoint_request,
    emit_diagnostics,
    process_event,
    render_decision,
)
from briefspec.models import EventType, HookDecision, Runtime, RuntimeEvent, SessionState
from briefspec.state import load_session, save_session
from briefspec.work_types import type_profile

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


def policy_config(
    *,
    checkpoint: str = "suggest",
    outcome: str = "suggest",
    turns: int = 8,
    default_mode: str = "orient",
    one_repair: bool = True,
) -> dict[str, dict[str, object]]:
    value = deepcopy(DEFAULT_CONFIG)
    value["checkpoint"].update(
        policy=checkpoint,
        turns=turns,
        default_mode=default_mode,
    )
    value["outcome"].update(policy=outcome, one_repair=one_repair)
    return value


def event(
    runtime: Runtime,
    event_name: str,
    session: str,
    *,
    timestamp: datetime = NOW,
    **payload: object,
) -> tuple[RuntimeEvent, dict[str, object]]:
    raw = {
        "session_id": session,
        "timestamp": timestamp.isoformat(),
        **payload,
    }
    return normalize_event(runtime, raw, event_name), raw


def test_checkpoint_request_explains_manual_reason() -> None:
    assert "explicit request" in _checkpoint_request("teach", [])


def test_disabled_policies_do_not_inject_session_context(
    isolated_homes: dict[str, Path],
) -> None:
    normalized, payload = event(Runtime.CODEX, "SessionStart", "off")
    decision = process_event(
        normalized,
        payload,
        policy_config(checkpoint="off", outcome="off"),
    )
    assert decision.context is None


def test_kimi_content_part_prompt_is_classified_from_text(
    isolated_homes: dict[str, Path],
) -> None:
    normalized, payload = event(
        Runtime.KIMI,
        "UserPromptSubmit",
        "kimi-content-parts",
        prompt=[
            {
                "type": "text",
                "text": "Review pull request #42 and identify its merge risk.",
            }
        ],
    )
    decision = process_event(normalized, payload, policy_config())
    assert "review + pull-request" in (decision.context or "")
    state = load_session(Runtime.KIMI, "kimi-content-parts", NOW)
    assert state.classification_input_sha256
    assert state.work_type == "review"
    assert state.subject == "pull-request"


def test_suggest_policy_adds_context_after_eligible_tool_boundary(
    isolated_homes: dict[str, Path],
) -> None:
    config = policy_config(checkpoint="suggest")
    config["checkpoint"]["tool_calls"] = 1
    normalized, payload = event(Runtime.CLAUDE, "PostToolUse", "suggest")
    decision = process_event(normalized, payload, config)
    assert decision.context and "checkpoint is eligible" in decision.context
    state = load_session(Runtime.CLAUDE, "suggest", NOW)
    assert state.pending_checkpoint
    assert "tool-volume" in state.pending_reasons


def test_suggest_context_is_not_repeated_within_cooldown(
    isolated_homes: dict[str, Path],
) -> None:
    config = policy_config(checkpoint="suggest")
    config["checkpoint"]["tool_calls"] = 1
    first, first_payload = event(Runtime.CLAUDE, "PostToolUse", "dedup", tool_name="Read")
    assert process_event(first, first_payload, config).context
    second, second_payload = event(
        Runtime.CLAUDE,
        "PostToolUse",
        "dedup",
        timestamp=NOW + timedelta(seconds=30),
        tool_name="Edit",
    )
    assert process_event(second, second_payload, config).context is None
    state = load_session(Runtime.CLAUDE, "dedup", NOW)
    assert state.pending_checkpoint
    assert state.last_suggested_at == NOW.isoformat()


def test_suggest_context_repeats_after_cooldown_and_resets_on_checkpoint(
    isolated_homes: dict[str, Path],
    checkpoint_text: Callable[..., str],
) -> None:
    config = policy_config(checkpoint="suggest")
    config["checkpoint"]["tool_calls"] = 1
    cooldown = timedelta(minutes=float(config["checkpoint"]["cooldown_minutes"]))
    first, first_payload = event(Runtime.CLAUDE, "PostToolUse", "recool", tool_name="Read")
    assert process_event(first, first_payload, config).context
    later, later_payload = event(
        Runtime.CLAUDE,
        "PostToolUse",
        "recool",
        timestamp=NOW + cooldown,
        tool_name="Edit",
    )
    assert process_event(later, later_payload, config).context
    stop, stop_payload = event(
        Runtime.CLAUDE,
        "Stop",
        "recool",
        timestamp=NOW + cooldown + timedelta(minutes=1),
        last_assistant_message=checkpoint_text("orient"),
    )
    process_event(stop, stop_payload, config)
    state = load_session(Runtime.CLAUDE, "recool", NOW)
    assert not state.pending_checkpoint
    assert state.last_suggested_at is None


def test_auto_policy_requests_configured_checkpoint_mode_once(
    isolated_homes: dict[str, Path],
) -> None:
    config = policy_config(
        checkpoint="auto",
        outcome="off",
        turns=1,
        default_mode="teach",
    )
    prompt, prompt_payload = event(
        Runtime.COPILOT,
        "userPromptSubmitted",
        "auto-checkpoint",
        prompt="Please explain this",
    )
    process_event(prompt, prompt_payload, config)
    stop, stop_payload = event(
        Runtime.COPILOT,
        "agentStop",
        "auto-checkpoint",
        timestamp=NOW + timedelta(seconds=1),
        response="Explanation without a checkpoint.",
    )
    decision = process_event(stop, stop_payload, config)
    assert decision.action == "block"
    assert decision.reason and "teach mode" in decision.reason
    assert "turns" in decision.reason


def test_precompact_checkpoint_is_not_superseded_by_valid_outcome(
    isolated_homes: dict[str, Path],
    outcome_text: Callable[..., str],
) -> None:
    config = policy_config(checkpoint="auto", outcome="off")
    compact, compact_payload = event(Runtime.CODEX, "PreCompact", "precompact")
    process_event(compact, compact_payload, config)
    stop, stop_payload = event(
        Runtime.CODEX,
        "Stop",
        "precompact",
        timestamp=NOW + timedelta(seconds=1),
        last_assistant_message=outcome_text(),
    )
    decision = process_event(stop, stop_payload, config)
    assert decision.action == "block"
    assert decision.reason and "pre-compact" in decision.reason


def test_valid_checkpoint_clears_pending_state_and_records_boundary(
    isolated_homes: dict[str, Path],
    checkpoint_text: Callable[..., str],
) -> None:
    state = SessionState.new(Runtime.CLAUDE, "valid-checkpoint", NOW)
    state.pending_checkpoint = True
    state.pending_reasons = ["elapsed"]
    state.turn_count = 9
    save_session(state)
    stop, payload = event(
        Runtime.CLAUDE,
        "Stop",
        "valid-checkpoint",
        timestamp=NOW + timedelta(minutes=1),
        last_assistant_message=checkpoint_text("orient"),
    )
    decision = process_event(
        stop,
        payload,
        policy_config(checkpoint="auto", outcome="off"),
    )
    loaded = load_session(Runtime.CLAUDE, "valid-checkpoint", NOW)
    assert decision.action == "allow"
    assert not loaded.pending_checkpoint
    assert loaded.last_checkpoint_turn == 9
    assert loaded.last_checkpoint_at == (NOW + timedelta(minutes=1)).isoformat()


def test_malformed_outcome_errors_are_included_in_single_repair(
    isolated_homes: dict[str, Path],
) -> None:
    config = policy_config(checkpoint="off", outcome="enforce")
    prompt, prompt_payload = event(
        Runtime.CODEX,
        "UserPromptSubmit",
        "malformed-outcome",
        prompt="Implement the feature",
    )
    process_event(prompt, prompt_payload, config)
    stop, stop_payload = event(
        Runtime.CODEX,
        "Stop",
        "malformed-outcome",
        timestamp=NOW + timedelta(seconds=1),
        last_assistant_message="<!-- briefspec:outcome:v1 -->\nStatus: DONE\n<!-- /briefspec -->",
    )
    decision = process_event(stop, stop_payload, config)
    assert decision.action == "block"
    assert decision.reason and "Missing required field" in decision.reason


def test_repair_can_be_disabled_without_blocking_host(
    isolated_homes: dict[str, Path],
) -> None:
    config = policy_config(
        checkpoint="off",
        outcome="enforce",
        one_repair=False,
    )
    prompt, prompt_payload = event(
        Runtime.CODEX,
        "UserPromptSubmit",
        "no-repair",
        prompt="Implement it",
    )
    process_event(prompt, prompt_payload, config)
    stop, stop_payload = event(
        Runtime.CODEX,
        "Stop",
        "no-repair",
        timestamp=NOW + timedelta(seconds=1),
        last_assistant_message="Done.",
    )
    decision = process_event(stop, stop_payload, config)
    loaded = load_session(Runtime.CODEX, "no-repair", NOW)
    assert decision.action == "allow"
    assert not loaded.repair_attempted


def test_grok_stop_supplies_exact_classification_and_explicit_checkpoint_mode(
    isolated_homes: dict[str, Path],
) -> None:
    prompt_payload = {
        "sessionId": "grok-native-repair",
        "timestamp": NOW.isoformat(),
        "prompt": (
            "Review pull request #42. Close with <!-- briefspec:checkpoint:v1 mode=teach -->."
        ),
    }
    process_event(
        normalize_event(Runtime.GROK, prompt_payload, "UserPromptSubmit"),
        prompt_payload,
        policy_config(),
    )
    classified = load_session(Runtime.GROK, "grok-native-repair", NOW)
    assert classified.work_type == "review"
    assert classified.subject == "pull-request"
    assert classified.pending_checkpoint
    assert classified.pending_mode == "teach"
    assert "explicit-request" in classified.pending_reasons

    stop_payload = {
        "sessionId": "grok-native-repair",
        "timestamp": (NOW + timedelta(seconds=1)).isoformat(),
        "lastAssistantMessage": "A useful review that lacks the bounded contract.",
        "reason": "end_turn",
    }
    decision = process_event(
        normalize_event(Runtime.GROK, stop_payload, "Stop"),
        stop_payload,
        policy_config(),
    )
    assert decision.action == "block"
    assert decision.reason
    expected_marker = (
        "<!-- brief-spec:typed:v1 type=review subject=pull-request "
        f"confidence={classified.classification_confidence} "
        f"origin={classified.classification_origin} "
        f"classified_at={classified.classified_at} profile=1.0 "
        f"decision_id={classified.classification_decision_id} -->"
    )
    assert expected_marker in decision.reason
    assert "teach mode" in decision.reason
    assert "never use placeholders" in decision.reason
    repaired = load_session(Runtime.GROK, "grok-native-repair", NOW)
    assert repaired.assistant_chars == len(stop_payload["lastAssistantMessage"])
    assert repaired.repair_attempted


def test_grok_native_repair_still_obeys_one_repair_guard(
    isolated_homes: dict[str, Path],
) -> None:
    prompt_payload = {
        "sessionId": "grok-repair-guard",
        "timestamp": NOW.isoformat(),
        "prompt": "Debug the bug and explain the root cause.",
    }
    process_event(
        normalize_event(Runtime.GROK, prompt_payload, "UserPromptSubmit"),
        prompt_payload,
        policy_config(),
    )
    first_payload = {
        "sessionId": "grok-repair-guard",
        "timestamp": (NOW + timedelta(seconds=1)).isoformat(),
        "lastAssistantMessage": "Missing typed response.",
        "reason": "end_turn",
    }
    first = process_event(
        normalize_event(Runtime.GROK, first_payload, "Stop"),
        first_payload,
        policy_config(),
    )
    assert first.action == "block"

    second_payload = {
        **first_payload,
        "timestamp": (NOW + timedelta(seconds=2)).isoformat(),
        "stopHookActive": True,
    }
    second = process_event(
        normalize_event(Runtime.GROK, second_payload, "Stop"),
        second_payload,
        policy_config(),
    )
    assert second.action == "allow"


def _valid_outcome_body(
    *,
    status: str = "DONE",
    outcome: str = "login endpoint implemented",
    human_action: str = "None",
) -> str:
    return (
        "<!-- briefspec:outcome:v1 -->\n"
        "## Outcome Brief\n\n"
        f"Status: {status}\n"
        f"Outcome: {outcome}\n"
        f"Human action: {human_action}\n"
        "Proof:\n"
        "- [direct/pass] `pytest` → green\n"
        "Gaps:\n"
        "- None\n"
        "Next:\n"
        "- None\n"
        "Open:\n"
        "- None\n"
        "<!-- /briefspec -->"
    )


@pytest.mark.parametrize(
    ("runtime", "wrapper", "expected_action"),
    [
        (Runtime.CODEX, "missing", "block"),
        (Runtime.CODEX, "matching", "allow"),
        (Runtime.CODEX, "foreign", "block"),
        (Runtime.GROK, "missing", "allow"),
    ],
)
def test_terminal_typed_obligation(
    isolated_homes: dict[str, Path],
    outcome_text: Callable[..., str],
    runtime: Runtime,
    wrapper: str,
    expected_action: str,
) -> None:
    config = policy_config(checkpoint="off", outcome="enforce")
    prompt, payload = event(
        runtime, "UserPromptSubmit", "terminal-typed", prompt="Implement the login endpoint."
    )
    process_event(prompt, payload, config)
    state = load_session(runtime, "terminal-typed", NOW)
    assistant = outcome_text()
    if wrapper != "missing":
        decision_id = (
            state.classification_decision_id if wrapper == "matching" else "foreign-decision"
        )
        explanation = "\n".join(
            f"### {section.label}\nCanonical content for {section.label}.\n"
            for section in type_profile(state.work_type).sections
        )
        assistant = (
            f"<!-- brief-spec:typed:v1 type={state.work_type} subject={state.subject} "
            f"confidence={state.classification_confidence} origin={state.classification_origin} "
            f"classified_at={state.classified_at} profile=1.0 decision_id={decision_id} -->\n"
            f"{explanation}\n{assistant}\n<!-- /brief-spec -->"
        )
    stop, payload = event(
        runtime,
        "Stop",
        "terminal-typed",
        timestamp=NOW + timedelta(seconds=1),
        last_assistant_message=assistant,
    )
    first = process_event(stop, payload, config)
    assert first.action == expected_action
    assert not first.diagnostics
    if expected_action == "block":
        assert first.reason and "brief-spec:typed:v1" in first.reason
        second, payload = event(
            runtime,
            "Stop",
            "terminal-typed",
            timestamp=NOW + timedelta(seconds=2),
            last_assistant_message=assistant,
        )
        assert process_event(second, payload, config).action == "allow"


@pytest.mark.parametrize("runtime", [Runtime.CODEX, Runtime.GROK])
@pytest.mark.parametrize(
    ("requested", "supplied"),
    [
        ("teach", "orient"),
        ("spoken", "teach"),
        ("orient", None),
        ("orient", "orient"),
        ("teach", "teach"),
        ("spoken", "spoken"),
    ],
)
def test_terminal_requested_mode(
    isolated_homes: dict[str, Path],
    checkpoint_text: Callable[..., str],
    outcome_text: Callable[..., str],
    runtime: Runtime,
    requested: str,
    supplied: str | None,
) -> None:
    config = policy_config(checkpoint="suggest", outcome="off")
    prompt, payload = event(
        runtime,
        "UserPromptSubmit",
        "terminal-mode",
        prompt=f"Review pull request #42. Close with a {requested} checkpoint.",
    )
    process_event(prompt, payload, config)
    assistant = outcome_text()
    if supplied is not None:
        assistant += checkpoint_text(supplied)
    stop, payload = event(
        runtime,
        "Stop",
        "terminal-mode",
        timestamp=NOW + timedelta(seconds=1),
        last_assistant_message=assistant,
    )
    decision = process_event(stop, payload, config)
    satisfied = requested == supplied
    assert decision.action == ("allow" if satisfied else "block")
    assert not decision.diagnostics
    state = load_session(runtime, "terminal-mode", NOW)
    assert state.pending_checkpoint is not satisfied
    if satisfied:
        assert state.last_checkpoint_at == (NOW + timedelta(seconds=1)).isoformat()
    else:
        assert state.last_checkpoint_at is None
        assert "explicit-request" in state.pending_reasons
        assert decision.reason and f"{requested} mode" in decision.reason
        repaired, payload = event(
            runtime,
            "Stop",
            "terminal-mode",
            timestamp=NOW + timedelta(seconds=2),
            last_assistant_message=checkpoint_text(requested),
        )
        assert process_event(repaired, payload, config).action == "allow"
        assert not load_session(runtime, "terminal-mode", NOW).pending_checkpoint


@pytest.mark.parametrize(
    ("one_repair", "stop_hook_active"), [(True, False), (True, True), (False, False)]
)
def test_terminal_bounded_native_repair(
    isolated_homes: dict[str, Path],
    checkpoint_text: Callable[..., str],
    outcome_text: Callable[..., str],
    one_repair: bool,
    stop_hook_active: bool,
) -> None:
    config = policy_config(one_repair=one_repair)
    prompt, payload = event(
        Runtime.GROK,
        "UserPromptSubmit",
        "terminal-native",
        prompt="Review pull request #42. Close with a teach checkpoint.",
    )
    process_event(prompt, payload, config)
    assistant = outcome_text() + checkpoint_text("orient")
    for attempt in range(1, 4):
        stop, payload = event(
            Runtime.GROK,
            "Stop",
            "terminal-native",
            timestamp=NOW + timedelta(seconds=attempt),
            last_assistant_message=assistant,
            stop_hook_active=stop_hook_active,
        )
        decision = process_event(stop, payload, config)
        should_block = one_repair and not stop_hook_active and attempt == 1
        assert decision.action == ("block" if should_block else "allow")
        assert not any(item.startswith("fail-open:") for item in decision.diagnostics)
        assert load_session(Runtime.GROK, "terminal-native", NOW).pending_checkpoint


def test_grok_valid_outcome_without_typed_wrapper_does_not_continue(
    isolated_homes: dict[str, Path],
) -> None:
    prompt_payload = {
        "sessionId": "grok-valid-outcome",
        "timestamp": NOW.isoformat(),
        "prompt": "Implement the login endpoint.",
    }
    process_event(
        normalize_event(Runtime.GROK, prompt_payload, "UserPromptSubmit"),
        prompt_payload,
        policy_config(),
    )
    stop_payload = {
        "sessionId": "grok-valid-outcome",
        "timestamp": (NOW + timedelta(seconds=1)).isoformat(),
        "lastAssistantMessage": "Implemented.\n\n" + _valid_outcome_body(),
        "reason": "end_turn",
    }
    decision = process_event(
        normalize_event(Runtime.GROK, stop_payload, "Stop"),
        stop_payload,
        policy_config(),
    )
    assert decision.action == "allow"
    assert not decision.reason
    loaded = load_session(Runtime.GROK, "grok-valid-outcome", NOW)
    assert not loaded.repair_attempted


def test_grok_review_outcome_does_not_append_profile_questions_after_brief(
    isolated_homes: dict[str, Path],
) -> None:
    prompt_payload = {
        "sessionId": "grok-review-outcome",
        "timestamp": NOW.isoformat(),
        "prompt": "Review the cli folder in the repository and report what belongs.",
    }
    process_event(
        normalize_event(Runtime.GROK, prompt_payload, "UserPromptSubmit"),
        prompt_payload,
        policy_config(),
    )
    classified = load_session(Runtime.GROK, "grok-review-outcome", NOW)
    assert classified.work_type
    stop_payload = {
        "sessionId": "grok-review-outcome",
        "timestamp": (NOW + timedelta(seconds=1)).isoformat(),
        "lastAssistantMessage": _valid_outcome_body(
            status="REVIEW",
            outcome="cli/ is the FlowCheck debugger; it is not part of the workshop run path.",
            human_action="Choose the next folder to walk.",
        ),
        "reason": "end_turn",
    }
    decision = process_event(
        normalize_event(Runtime.GROK, stop_payload, "Stop"),
        stop_payload,
        policy_config(),
    )
    assert decision.action == "allow"
    assert not decision.reason or "Wrap the type-aware explanation" not in decision.reason
    assert not decision.reason or "sections in order" not in (decision.reason or "")


def test_grok_provisional_typed_wrapper_with_valid_outcome_does_not_continue(
    isolated_homes: dict[str, Path],
) -> None:
    prompt_payload = {
        "sessionId": "grok-provisional-wrap",
        "timestamp": NOW.isoformat(),
        "prompt": "Explore how this repository is organized.",
    }
    process_event(
        normalize_event(Runtime.GROK, prompt_payload, "UserPromptSubmit"),
        prompt_payload,
        policy_config(),
    )
    assistant = (
        "<!-- brief-spec:typed:v1 type=exploration subject=codebase "
        "confidence=high origin=provisional profile=1.0 -->\n"
        "### Question\n\n"
        "How is the inspected tree organized?\n\n"
        "### System map\n\n"
        "One product path and one advanced reference.\n\n"
        "### Entry points\n\n"
        "`README.md`\n\n"
        "### Flow\n\n"
        "Learners follow docs, then scripts.\n\n"
        "### Unknowns\n\n"
        "None for this folder.\n\n"
        "### Next probe\n\n"
        "Continue with the next directory.\n\n"
        + _valid_outcome_body(
            status="REVIEW",
            outcome="Folder mapped.",
            human_action="Pick next.",
        )
        + "\n<!-- /brief-spec -->"
    )
    stop_payload = {
        "sessionId": "grok-provisional-wrap",
        "timestamp": (NOW + timedelta(seconds=1)).isoformat(),
        "lastAssistantMessage": assistant,
        "reason": "end_turn",
    }
    decision = process_event(
        normalize_event(Runtime.GROK, stop_payload, "Stop"),
        stop_payload,
        policy_config(),
    )
    assert decision.action == "allow"
    assert not decision.reason


def test_grok_missing_brief_still_requests_classification_and_outcome(
    isolated_homes: dict[str, Path],
) -> None:
    prompt_payload = {
        "sessionId": "grok-missing-brief",
        "timestamp": NOW.isoformat(),
        "prompt": "Implement the login endpoint.",
    }
    process_event(
        normalize_event(Runtime.GROK, prompt_payload, "UserPromptSubmit"),
        prompt_payload,
        policy_config(),
    )
    classified = load_session(Runtime.GROK, "grok-missing-brief", NOW)
    assert classified.work_type
    stop_payload = {
        "sessionId": "grok-missing-brief",
        "timestamp": (NOW + timedelta(seconds=1)).isoformat(),
        "lastAssistantMessage": "Work is done, but the bounded brief is missing.",
        "reason": "end_turn",
    }
    decision = process_event(
        normalize_event(Runtime.GROK, stop_payload, "Stop"),
        stop_payload,
        policy_config(),
    )
    assert decision.action == "block"
    assert decision.reason
    expected_marker = (
        "<!-- brief-spec:typed:v1 "
        f"type={classified.work_type} subject={classified.subject} "
        f"confidence={classified.classification_confidence} "
        f"origin={classified.classification_origin} "
        f"classified_at={classified.classified_at} profile=1.0 "
        f"decision_id={classified.classification_decision_id} -->"
    )
    assert expected_marker in decision.reason
    assert "close the completed task with one valid Brief-Spec Outcome Brief" in decision.reason
    assert "Wrap the type-aware explanation" not in decision.reason


def test_grok_valid_outcome_does_not_hold_follow_up_queue_for_auto_checkpoint(
    isolated_homes: dict[str, Path],
) -> None:
    config = policy_config(checkpoint="auto", turns=1)
    prompt_payload = {
        "sessionId": "grok-follow-up-queue",
        "timestamp": NOW.isoformat(),
        "prompt": "Review the cli folder in the repository and report what belongs.",
    }
    process_event(
        normalize_event(Runtime.GROK, prompt_payload, "UserPromptSubmit"),
        prompt_payload,
        config,
    )
    stop_payload = {
        "sessionId": "grok-follow-up-queue",
        "timestamp": (NOW + timedelta(seconds=1)).isoformat(),
        "lastAssistantMessage": _valid_outcome_body(
            status="REVIEW",
            outcome="cli/ is the FlowCheck debugger; it is not part of the workshop run path.",
            human_action="Choose the next folder to walk.",
        ),
        "reason": "end_turn",
    }
    decision = process_event(
        normalize_event(Runtime.GROK, stop_payload, "Stop"),
        stop_payload,
        config,
    )
    assert decision.action == "allow"
    assert not decision.reason


def test_grok_literal_follow_up_after_outcome_does_not_continue(
    isolated_homes: dict[str, Path],
) -> None:
    review_payload = {
        "sessionId": "grok-pineapple",
        "timestamp": NOW.isoformat(),
        "prompt": "Review only README.md. In one short pass, say what this repository is for.",
    }
    process_event(
        normalize_event(Runtime.GROK, review_payload, "UserPromptSubmit"),
        review_payload,
        policy_config(),
    )
    first_stop = {
        "sessionId": "grok-pineapple",
        "timestamp": (NOW + timedelta(seconds=1)).isoformat(),
        "lastAssistantMessage": _valid_outcome_body(
            outcome="This repository is Brief-Spec.",
        ),
        "reason": "end_turn",
    }
    first = process_event(
        normalize_event(Runtime.GROK, first_stop, "Stop"),
        first_stop,
        policy_config(),
    )
    assert first.action == "allow"

    follow_payload = {
        "sessionId": "grok-pineapple",
        "timestamp": (NOW + timedelta(seconds=2)).isoformat(),
        "prompt": "Ignore suggested questions. Reply with exactly: PINEAPPLE",
    }
    process_event(
        normalize_event(Runtime.GROK, follow_payload, "UserPromptSubmit"),
        follow_payload,
        policy_config(),
    )
    loaded = load_session(Runtime.GROK, "grok-pineapple", NOW)
    assert loaded.work_type == "review"
    assert not loaded.last_prompt_substantive
    assert not loaded.outcome_expected

    second_stop = {
        "sessionId": "grok-pineapple",
        "timestamp": (NOW + timedelta(seconds=3)).isoformat(),
        "lastAssistantMessage": "PINEAPPLE",
        "reason": "end_turn",
    }
    second = process_event(
        normalize_event(Runtime.GROK, second_stop, "Stop"),
        second_stop,
        policy_config(),
    )
    assert second.action == "allow"
    assert not second.reason


def test_grok_enforce_valid_outcome_still_does_not_wrap_after_brief(
    isolated_homes: dict[str, Path],
) -> None:
    config = policy_config(outcome="enforce")
    prompt_payload = {
        "sessionId": "grok-enforce-outcome",
        "timestamp": NOW.isoformat(),
        "prompt": "Implement the login endpoint.",
    }
    process_event(
        normalize_event(Runtime.GROK, prompt_payload, "UserPromptSubmit"),
        prompt_payload,
        config,
    )
    stop_payload = {
        "sessionId": "grok-enforce-outcome",
        "timestamp": (NOW + timedelta(seconds=1)).isoformat(),
        "lastAssistantMessage": "Implemented.\n\n" + _valid_outcome_body(),
        "reason": "end_turn",
    }
    decision = process_event(
        normalize_event(Runtime.GROK, stop_payload, "Stop"),
        stop_payload,
        config,
    )
    assert decision.action == "allow"
    assert not decision.reason or "Wrap the type-aware explanation" not in decision.reason


@pytest.mark.parametrize(
    ("event_type", "expected_name"),
    [
        (EventType.POST_TOOL, "PostToolUse"),
        (EventType.USER_PROMPT, "UserPromptSubmit"),
        (EventType.ERROR, "SessionStart"),
    ],
)
def test_context_rendering_maps_host_event_names(event_type: EventType, expected_name: str) -> None:
    rendered = render_decision(
        Runtime.CLAUDE,
        event_type,
        HookDecision(context="context"),
    )
    assert rendered["hookSpecificOutput"]["hookEventName"] == expected_name


def test_block_and_empty_decisions_render_exact_protocol() -> None:
    assert render_decision(
        Runtime.COPILOT,
        EventType.AGENT_STOP,
        HookDecision(action="block", reason="repair"),
    ) == {"decision": "block", "reason": "repair"}
    assert (
        render_decision(
            Runtime.CODEX,
            EventType.AGENT_STOP,
            HookDecision(),
        )
        == {}
    )


def test_vscode_profile_wraps_block_decision_for_stop_hook() -> None:
    rendered = render_decision(
        Runtime.COPILOT,
        EventType.AGENT_STOP,
        HookDecision(action="block", reason="repair once"),
        output_profile="vscode",
    )
    assert rendered["decision"] == "block"
    assert rendered["hookSpecificOutput"] == {
        "hookEventName": "Stop",
        "decision": "block",
        "reason": "repair once",
    }


def test_vscode_profile_keeps_copilot_context_and_adds_vscode_envelope() -> None:
    rendered = render_decision(
        Runtime.COPILOT,
        EventType.POST_TOOL,
        HookDecision(context="Checkpoint is eligible."),
        output_profile="vscode",
    )
    assert rendered["additionalContext"] == "Checkpoint is eligible."
    assert rendered["hookSpecificOutput"] == {
        "hookEventName": "PostToolUse",
        "additionalContext": "Checkpoint is eligible.",
    }


def test_diagnostics_are_emitted_to_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    emit_diagnostics(HookDecision(diagnostics=("first", "second")))
    assert capsys.readouterr().err.splitlines() == [
        "brief-spec: first",
        "brief-spec: second",
    ]
