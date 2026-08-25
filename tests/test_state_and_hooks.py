from __future__ import annotations

import os
import stat
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from briefspec.adapters import normalize_event
from briefspec.config import DEFAULT_CONFIG
from briefspec.hooks import process_event, render_decision
from briefspec.models import EventType, Runtime, SessionState
from briefspec.state import (
    list_sessions,
    load_session,
    prune_sessions,
    reset_session,
    save_session,
    session_lock,
    session_path,
)

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


def enforced_config() -> dict[str, dict[str, object]]:
    return {
        "checkpoint": {
            **DEFAULT_CONFIG["checkpoint"],
            "policy": "off",
        },
        "outcome": {
            "policy": "enforce",
            "one_repair": True,
        },
        "state": dict(DEFAULT_CONFIG["state"]),
    }


def test_session_filename_hashes_the_raw_session_id(
    isolated_homes: dict[str, Path],
) -> None:
    path = session_path(Runtime.CODEX, "../../sensitive-session")
    assert "sensitive-session" not in str(path)
    assert path.name == "state.json"
    assert len(path.parent.name) == 64


def test_state_files_are_private_and_round_trip(
    isolated_homes: dict[str, Path],
) -> None:
    state = SessionState.new(Runtime.CLAUDE, "private", NOW)
    state.turn_count = 4
    save_session(state)
    path = session_path(Runtime.CLAUDE, "private")
    loaded = load_session(Runtime.CLAUDE, "private", NOW)
    assert loaded.turn_count == 4
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_corrupt_state_is_quarantined_and_reinitialized(
    isolated_homes: dict[str, Path],
) -> None:
    path = session_path(Runtime.COPILOT, "corrupt")
    path.parent.mkdir(parents=True)
    path.write_text("{not-json", encoding="utf-8")
    state = load_session(Runtime.COPILOT, "corrupt", NOW)
    assert state.session_id == "corrupt"
    assert not path.exists()
    assert len(list(path.parent.glob("state.corrupt.*.json"))) == 1


def test_non_object_state_is_also_quarantined(
    isolated_homes: dict[str, Path],
) -> None:
    path = session_path(Runtime.COPILOT, "non-object")
    path.parent.mkdir(parents=True)
    path.write_text("[]", encoding="utf-8")
    state = load_session(Runtime.COPILOT, "non-object", NOW)
    assert state.session_id == "non-object"
    assert not path.exists()
    assert list(path.parent.glob("state.corrupt.*.json"))


def test_prompt_content_is_not_persisted(
    isolated_homes: dict[str, Path],
) -> None:
    secret = "TOP-SECRET-PROMPT-CONTENT"
    payload = {
        "session_id": "privacy",
        "timestamp": NOW.isoformat(),
        "prompt": f"Please build this using {secret}",
    }
    event = normalize_event(Runtime.CODEX, payload, "UserPromptSubmit")
    process_event(event, payload, enforced_config())
    persisted = session_path(Runtime.CODEX, "privacy").read_text(encoding="utf-8")
    assert secret not in persisted
    assert "Please build this" not in persisted


def test_duplicate_hook_event_is_idempotent(
    isolated_homes: dict[str, Path],
) -> None:
    payload = {
        "session_id": "duplicate",
        "timestamp": NOW.isoformat(),
        "prompt": "Please implement it",
    }
    event = normalize_event(Runtime.CODEX, payload, "UserPromptSubmit")
    first = process_event(event, payload, enforced_config())
    second = process_event(event, payload, enforced_config())
    state = load_session(Runtime.CODEX, "duplicate", NOW)
    assert first.action == "allow"
    assert second.diagnostics == ("duplicate event ignored",)
    assert state.turn_count == 1
    assert len(state.recent_event_hashes) == 1


def test_one_repair_guard_blocks_once_then_fails_open(
    isolated_homes: dict[str, Path],
) -> None:
    prompt_payload = {
        "session_id": "repair",
        "timestamp": NOW.isoformat(),
        "prompt": "Please implement and test the feature",
    }
    prompt = normalize_event(Runtime.CLAUDE, prompt_payload, "UserPromptSubmit")
    process_event(prompt, prompt_payload, enforced_config())

    first_payload = {
        "session_id": "repair",
        "timestamp": (NOW + timedelta(seconds=1)).isoformat(),
        "last_assistant_message": "Implemented.",
    }
    first_event = normalize_event(Runtime.CLAUDE, first_payload, "Stop")
    first = process_event(first_event, first_payload, enforced_config())
    assert first.action == "block"
    assert first.reason and "Outcome Brief" in first.reason

    second_payload = {
        **first_payload,
        "timestamp": (NOW + timedelta(seconds=2)).isoformat(),
        "last_assistant_message": "Still missing the contract.",
    }
    second_event = normalize_event(Runtime.CLAUDE, second_payload, "Stop")
    second = process_event(second_event, second_payload, enforced_config())
    assert second.action == "allow"
    assert "repair guard allowed a still-invalid second stop" in second.diagnostics


def test_native_stop_hook_active_also_prevents_repair_loop(
    isolated_homes: dict[str, Path],
) -> None:
    payload = {
        "session_id": "native-guard",
        "timestamp": NOW.isoformat(),
        "prompt": "Build this",
    }
    process_event(
        normalize_event(Runtime.COPILOT, payload, "userPromptSubmitted"),
        payload,
        enforced_config(),
    )
    stop = {
        "session_id": "native-guard",
        "timestamp": (NOW + timedelta(seconds=1)).isoformat(),
        "response": "No brief",
        "stop_hook_active": True,
    }
    decision = process_event(
        normalize_event(Runtime.COPILOT, stop, "agentStop"),
        stop,
        enforced_config(),
    )
    assert decision.action == "allow"
    assert decision.diagnostics


def test_grok_camel_case_stop_payload_preserves_assistant_text() -> None:
    payload = {
        "sessionId": "grok-camel",
        "timestamp": NOW.isoformat(),
        "lastAssistantMessage": "The native Grok response.",
        "stopHookActive": True,
    }
    normalized = normalize_event(Runtime.GROK, payload, "Stop")
    assert normalized.session_id == "grok-camel"
    assert normalized.assistant_text == "The native Grok response."
    assert normalized.assistant_chars == len("The native Grok response.")
    assert normalized.stop_hook_active


def test_valid_outcome_clears_expected_state(
    isolated_homes: dict[str, Path], outcome_text: Callable[..., str]
) -> None:
    prompt_payload = {
        "session_id": "valid-outcome",
        "timestamp": NOW.isoformat(),
        "prompt": "Fix the feature",
    }
    process_event(
        normalize_event(Runtime.CODEX, prompt_payload, "UserPromptSubmit"),
        prompt_payload,
        enforced_config(),
    )
    stop_payload = {
        "session_id": "valid-outcome",
        "timestamp": (NOW + timedelta(seconds=1)).isoformat(),
        "last_assistant_message": outcome_text(),
    }
    decision = process_event(
        normalize_event(Runtime.CODEX, stop_payload, "Stop"),
        stop_payload,
        enforced_config(),
    )
    state = load_session(Runtime.CODEX, "valid-outcome", NOW)
    assert decision.action == "allow"
    assert not state.outcome_expected
    assert not state.repair_attempted


def test_unknown_or_invalid_policy_fails_open(
    isolated_homes: dict[str, Path],
) -> None:
    config = enforced_config()
    config["outcome"]["policy"] = "not-a-policy"
    payload = {"session_id": "bad-config", "timestamp": NOW.isoformat()}
    decision = process_event(
        normalize_event(Runtime.CODEX, payload, "SessionStart"),
        payload,
        config,
    )
    assert decision.action == "allow"
    assert decision.diagnostics
    assert decision.diagnostics[0].startswith("fail-open:")


def test_runtime_specific_context_rendering() -> None:
    from briefspec.models import HookDecision

    decision = HookDecision(context="Brief-Spec is active")
    copilot = render_decision(Runtime.COPILOT, EventType.SESSION_START, decision)
    claude = render_decision(Runtime.CLAUDE, EventType.SESSION_START, decision)
    assert copilot == {"additionalContext": "Brief-Spec is active"}
    assert claude["hookSpecificOutput"]["hookEventName"] == "SessionStart"


def test_list_reset_and_prune_session_state(
    isolated_homes: dict[str, Path],
) -> None:
    old = SessionState.new(Runtime.CODEX, "old", NOW - timedelta(days=20))
    recent = SessionState.new(Runtime.CLAUDE, "recent", NOW)
    save_session(old)
    save_session(recent)
    assert {item["session_id"] for item in list_sessions()} == {"old", "recent"}
    preview = prune_sessions(14, dry_run=True, now=NOW)
    assert preview == [session_path(Runtime.CODEX, "old")]
    assert session_path(Runtime.CODEX, "old").exists()
    prune_sessions(14, now=NOW)
    assert not session_path(Runtime.CODEX, "old").exists()
    assert reset_session(Runtime.CLAUDE, "recent")
    assert not reset_session(Runtime.CLAUDE, "recent")


def test_empty_state_queries_are_safe(
    isolated_homes: dict[str, Path],
) -> None:
    assert list_sessions() == []
    assert prune_sessions(14, now=NOW) == []


def test_session_listing_ignores_malformed_and_non_object_files(
    isolated_homes: dict[str, Path],
) -> None:
    root = isolated_homes["state"] / "sessions"
    malformed = root / "malformed" / "state.json"
    non_object = root / "list" / "state.json"
    malformed.parent.mkdir(parents=True)
    non_object.parent.mkdir(parents=True)
    malformed.write_text("{bad", encoding="utf-8")
    non_object.write_text("[]", encoding="utf-8")
    assert list_sessions() == []


def test_prune_falls_back_to_file_mtime_for_malformed_state(
    isolated_homes: dict[str, Path],
) -> None:
    path = isolated_homes["state"] / "sessions" / "malformed" / "state.json"
    path.parent.mkdir(parents=True)
    path.write_text("{bad", encoding="utf-8")
    old = (NOW - timedelta(days=30)).timestamp()
    os.utime(path, (old, old))
    assert prune_sessions(14, dry_run=True, now=NOW) == [path]


def test_session_lock_reclaims_stale_lock_and_times_out_on_live_lock(
    isolated_homes: dict[str, Path],
) -> None:
    key = session_path(Runtime.CODEX, "locked").parent.name
    lock = isolated_homes["state"] / "locks" / f"{key}.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("stale", encoding="utf-8")
    os.utime(lock, (0, 0))
    with session_lock(Runtime.CODEX, "locked", timeout=0):
        assert lock.exists()
    assert not lock.exists()

    lock.write_text("live", encoding="utf-8")
    with (
        pytest.raises(TimeoutError, match="Timed out acquiring session lock"),
        session_lock(Runtime.CODEX, "locked", timeout=0),
    ):
        pass


def test_state_write_survives_a_platform_without_fchmod(
    isolated_homes: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """os.fchmod is Unix-only. Windows has no such attribute, so the atomic write
    must not depend on it, and must leave no temp file behind."""
    monkeypatch.delattr(os, "fchmod", raising=False)
    state = SessionState.new(Runtime.CLAUDE, "no-fchmod", NOW)
    state.turn_count = 7
    save_session(state)
    path = session_path(Runtime.CLAUDE, "no-fchmod")
    assert load_session(Runtime.CLAUDE, "no-fchmod", NOW).turn_count == 7
    assert list(path.parent.glob(".state.json.*")) == []
