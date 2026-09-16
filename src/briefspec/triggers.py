from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from briefspec.models import EventType, RuntimeEvent, SessionState

_ACTION_PATTERNS = (
    r"\b(?:implement|build|create|write|fix|repair|change|update|add|remove|refactor|"
    r"migrate|install|configure|test|ship|deploy|publish|generate)\b",
    r"\b(?:go ahead|go and|take care of|finish this|do the work|make it work)\b",
)


def looks_like_action_request(prompt: str) -> bool:
    value = prompt.strip().lower()
    if not value:
        return False
    return any(re.search(pattern, value) for pattern in _ACTION_PATTERNS)


def elapsed_minutes(state: SessionState, now: datetime) -> float:
    """Minutes since the current checkpoint window opened (session start by default)."""
    try:
        started = datetime.fromisoformat(state.window_started_at or state.started_at)
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        return max(0.0, (now - started).total_seconds() / 60)
    except ValueError:
        return 0.0


def reset_window(state: SessionState, now: datetime) -> None:
    """Start a new eligibility window after a valid checkpoint or Outcome Brief."""
    state.window_started_at = now.astimezone(UTC).isoformat()
    state.window_turn_base = state.turn_count
    state.window_tool_base = state.tool_count
    state.window_chars_base = state.assistant_chars
    state.last_suggested_at = None


def eligibility_reasons(
    state: SessionState,
    config: dict[str, Any],
    now: datetime,
) -> list[str]:
    checkpoint = config["checkpoint"]
    reasons: list[str] = []
    if elapsed_minutes(state, now) >= float(checkpoint["elapsed_minutes"]):
        reasons.append("elapsed")
    if state.turn_count - state.window_turn_base >= int(checkpoint["turns"]):
        reasons.append("turns")
    if state.assistant_chars - state.window_chars_base >= int(checkpoint["assistant_chars"]):
        reasons.append("assistant-volume")
    if state.tool_count - state.window_tool_base >= int(checkpoint["tool_calls"]):
        reasons.append("tool-volume")
    if state.last_checkpoint_turn:
        minimum = int(checkpoint["minimum_turns_after_checkpoint"])
        if state.turn_count - state.last_checkpoint_turn < minimum:
            return []
    if state.last_checkpoint_at:
        try:
            previous = datetime.fromisoformat(state.last_checkpoint_at)
            cooldown = float(checkpoint["cooldown_minutes"])
            if max(0.0, (now - previous).total_seconds() / 60) < cooldown:
                return []
        except ValueError:
            pass
    return reasons


def update_counters(
    state: SessionState,
    event: RuntimeEvent,
    prompt_text: str = "",
) -> None:
    state.updated_at = event.occurred_at.astimezone(UTC).isoformat()
    if event.type is EventType.USER_PROMPT:
        state.turn_count += 1
        state.prompt_chars += event.prompt_chars
        state.repair_attempted = False
        if looks_like_action_request(prompt_text):
            state.outcome_expected = True
    if event.type is EventType.POST_TOOL:
        state.tool_count += max(1, event.tool_calls)
    if event.type is EventType.AGENT_STOP:
        state.assistant_chars += event.assistant_chars
    if event.type is EventType.PRE_COMPACT:
        state.pending_checkpoint = True
        if "pre-compact" not in state.pending_reasons:
            state.pending_reasons.append("pre-compact")
