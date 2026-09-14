from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime
from typing import Any

from briefspec.adapters.base import _content_text
from briefspec.config import load_config
from briefspec.continuity import detect_method_context, method_context
from briefspec.markdown import parse_typed, validate_checkpoint, validate_outcome
from briefspec.models import (
    CheckpointMode,
    EventType,
    HookDecision,
    Policy,
    Runtime,
    RuntimeEvent,
    SessionState,
)
from briefspec.state import load_session, save_session, session_lock
from briefspec.triggers import eligibility_reasons, update_counters
from briefspec.work_types import (
    classify_task,
    explicit_type_requested,
    is_clear_pivot,
    is_substantive,
    type_profile,
)

SESSION_CONTEXT = """\
Brief-Spec is active. Use the brief-spec router for substantive work, adapt the full explanation
to one primary work type and subject, and keep that selection stable for the task. When a bounded
method context is available, explain the current phase in plain language without turning it into a
new work type. When substantive
work ends, use outcome-brief inside the typed wrapper. For long or overloaded work, use
session-checkpoint at a natural boundary. Preserve proof and explicit gaps; never infer success."""


def _typed_marker(state: SessionState) -> str:
    return (
        "<!-- brief-spec:typed:v1 "
        f"type={state.work_type} subject={state.subject} "
        f"confidence={state.classification_confidence} origin={state.classification_origin} "
        f"classified_at={state.classified_at} profile=1.0 "
        f"decision_id={state.classification_decision_id} -->"
    )


def _classification_context(state: SessionState) -> str:
    profile = type_profile(state.work_type or "general")
    sections = ", ".join(section.label for section in profile.sections)
    method = method_context(state.method_context, phase=state.method_phase)
    phase = f" in phase {method['phase']}" if method["phase"] else ""
    return (
        "Brief-Spec classified this task as "
        f"{state.work_type} + {state.subject} ({state.classification_confidence}, "
        f"{state.classification_origin}). Use the brief-spec skill and explain it with these "
        f"sections in order: {sections}. Keep this type stable unless the user clearly pivots. "
        f"The method context is {method['method']}{phase} ({state.method_context_origin}). "
        "Use it as a lightweight Human Frame: state what is happening, why it matters, and the "
        "next human-relevant action; use a diagram only when it clarifies real relationships. "
        "At a terminal Outcome or Checkpoint, wrap the explanation and unchanged legacy brief "
        "inside brief-spec:typed:v1. The authoritative opening marker is exactly `"
        f"{_typed_marker(state)}`. Copy it character-for-character; never use placeholders."
    )


def _explicit_checkpoint_mode(prompt: str) -> str | None:
    value = prompt.lower()
    marker = re.search(r"briefspec:checkpoint:v1\s+mode=(orient|teach|spoken)", value)
    if marker:
        return marker.group(1)
    for mode in ("orient", "teach", "spoken"):
        if re.search(rf"\b(?:{mode}\s+checkpoint|checkpoint\s+(?:in\s+)?{mode}\s+mode)\b", value):
            return mode
    return None


def _checkpoint_request(mode: str, reasons: list[str]) -> str:
    because = ", ".join(reasons) if reasons else "an explicit request"
    return (
        f"Before ending this turn, add one valid Brief-Spec session checkpoint in {mode} mode. "
        f"It is due because of: {because}. Use the session-checkpoint skill, retain inspectable "
        "proof, and do not replace the requested task result."
    )


def _suggestion_due(state: SessionState, config: dict[str, Any], now: datetime) -> bool:
    """A pending checkpoint is suggested once, then again only after the cooldown."""
    if not state.last_suggested_at:
        return True
    try:
        previous = datetime.fromisoformat(state.last_suggested_at)
    except ValueError:
        return True
    cooldown = float(config["checkpoint"]["cooldown_minutes"])
    return (now - previous).total_seconds() / 60 >= cooldown


def _outcome_request(errors: tuple[str, ...] = ()) -> str:
    detail = f" Fix: {'; '.join(errors)}." if errors else ""
    return (
        "Before ending this turn, close the completed task with one valid Brief-Spec Outcome "
        "Brief. Use the outcome-brief skill. Keep Status, Outcome, Human action, Proof, Gaps, "
        f"Next, and Open in that order.{detail}"
    )


def process_event(
    event: RuntimeEvent,
    payload: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> HookDecision:
    effective = config or load_config(event.cwd)
    diagnostics: list[str] = []
    prompt = _content_text(payload.get("prompt")) or ""
    try:
        with session_lock(event.runtime, event.session_id):
            state = load_session(event.runtime, event.session_id, event.occurred_at)
            if event.payload_hash in state.recent_event_hashes:
                return HookDecision(diagnostics=("duplicate event ignored",))
            state.recent_event_hashes.append(event.payload_hash)
            state.recent_event_hashes = state.recent_event_hashes[-32:]
            update_counters(state, event, prompt)
            if event.type is EventType.USER_PROMPT:
                state.last_prompt_substantive = is_substantive(prompt)

            checkpoint_policy = Policy(str(effective["checkpoint"]["policy"]))
            outcome_policy = Policy(str(effective["outcome"]["policy"]))
            typing = effective.get("typing", {})
            typing_enabled = bool(typing.get("enabled", True))
            typing_activation = str(typing.get("activation", "substantive"))
            host_context = (
                payload.get("brief_spec") if isinstance(payload.get("brief_spec"), dict) else None
            )
            classification_due = (
                event.type is EventType.USER_PROMPT
                and typing_enabled
                and (
                    (typing_activation == "explicit" and "brief-spec" in prompt.lower())
                    or (typing_activation != "explicit" and is_substantive(prompt))
                )
                and (
                    not state.work_type
                    or not bool(typing.get("sticky", True))
                    or is_clear_pivot(prompt)
                    or explicit_type_requested(prompt)
                )
            )
            if classification_due:
                classified = classify_task(
                    prompt,
                    host_context=host_context,
                    default_type=str(typing.get("default_type", "general")),
                    now=event.occurred_at,
                )
                state.work_type = classified.work_type
                state.subject = classified.subject
                state.classification_confidence = classified.confidence
                state.classification_origin = classified.origin
                state.classification_rule_ids = list(classified.rule_ids)
                state.classified_at = classified.classified_at
                state.classification_decision_id = classified.decision_id
                state.classification_input_sha256 = classified.input_sha256
                state.classification_record_sha256 = classified.record_sha256
                state.classification_adapter_version = classified.adapter_version
            method_due = event.type is EventType.USER_PROMPT and (
                state.method_context == "general"
                or is_clear_pivot(prompt)
                or bool(host_context and host_context.get("method_context"))
            )
            if method_due:
                selected_method, selected_phase, method_origin = detect_method_context(
                    prompt,
                    host_context=host_context,
                )
                state.method_context = selected_method
                state.method_phase = selected_phase
                state.method_context_origin = method_origin
            requested_mode = (
                _explicit_checkpoint_mode(prompt) if event.type is EventType.USER_PROMPT else None
            )
            if requested_mode:
                state.pending_checkpoint = True
                state.pending_mode = requested_mode
                if "explicit-request" not in state.pending_reasons:
                    state.pending_reasons.append("explicit-request")
            if not state.pending_checkpoint:
                state.pending_mode = CheckpointMode(
                    str(effective["checkpoint"]["default_mode"])
                ).value
            if event.type not in {EventType.PRE_COMPACT, EventType.AGENT_STOP}:
                reasons = eligibility_reasons(state, effective, event.occurred_at)
                if reasons and checkpoint_policy not in {Policy.OFF, Policy.MANUAL}:
                    state.pending_checkpoint = True
                    state.pending_reasons = list(dict.fromkeys(state.pending_reasons + reasons))

            decision = HookDecision()
            if event.type is EventType.SESSION_START and (
                checkpoint_policy is not Policy.OFF or outcome_policy is not Policy.OFF
            ):
                decision = HookDecision(context=SESSION_CONTEXT)
            elif event.type is EventType.USER_PROMPT and state.work_type:
                decision = HookDecision(context=_classification_context(state))

            if (
                event.type is EventType.POST_TOOL
                and state.pending_checkpoint
                and checkpoint_policy is Policy.SUGGEST
                and _suggestion_due(state, effective, event.occurred_at)
            ):
                state.last_suggested_at = event.occurred_at.astimezone(UTC).isoformat()
                decision = HookDecision(
                    context=(
                        "A Brief-Spec checkpoint is eligible. At the next natural boundary, "
                        "offer or include an orient checkpoint without interrupting active "
                        "work."
                    )
                )

            if event.type is EventType.AGENT_STOP:
                assistant = event.assistant_text or ""
                outcome_expected = state.outcome_expected
                outcome_result = validate_outcome(assistant)
                has_outcome = outcome_result.valid
                explicit_checkpoint_mode = (
                    state.pending_mode
                    if state.pending_checkpoint and "explicit-request" in state.pending_reasons
                    else None
                )
                checkpoint_result = validate_checkpoint(
                    assistant,
                    CheckpointMode(explicit_checkpoint_mode) if explicit_checkpoint_mode else None,
                )
                has_checkpoint = checkpoint_result.valid
                typed_valid = False
                if state.work_type and (has_outcome or has_checkpoint):
                    try:
                        typed = parse_typed(assistant)
                    except ValueError:
                        typed = None
                    typed_valid = bool(
                        typed is not None
                        and typed[0].get("work_type") == state.work_type
                        and typed[0].get("subject") == state.subject
                        and typed[0].get("decision_id") == state.classification_decision_id
                    )

                if has_outcome:
                    state.outcome_expected = False
                    if (
                        state.pending_checkpoint
                        and state.pending_mode == CheckpointMode.ORIENT.value
                        and "pre-compact" not in state.pending_reasons
                        and explicit_checkpoint_mode is None
                    ):
                        state.pending_checkpoint = False
                        state.pending_reasons = []
                        state.last_suggested_at = None
                if has_checkpoint:
                    state.pending_checkpoint = False
                    state.pending_reasons = []
                    state.last_suggested_at = None
                    state.last_checkpoint_at = event.occurred_at.astimezone(UTC).isoformat()
                    state.last_checkpoint_turn = state.turn_count

                requests: list[str] = []
                grok_legacy_complete = event.runtime is Runtime.GROK and (
                    has_outcome or has_checkpoint
                )
                if outcome_expected and outcome_policy is Policy.ENFORCE and not has_outcome:
                    errors = (
                        outcome_result.errors
                        if "<!-- briefspec:outcome:v1 -->" in assistant
                        else ()
                    )
                    requests.append(_outcome_request(errors))
                elif (
                    outcome_expected
                    and outcome_policy is Policy.ENFORCE
                    and typing_enabled
                    and state.work_type
                    and has_outcome
                    and not typed_valid
                    and event.runtime is not Runtime.GROK
                ):
                    requests.append(
                        "Wrap the type-aware explanation and valid legacy Outcome Brief in one "
                        f"brief-spec:typed:v1 region for {state.work_type} + {state.subject}."
                    )
                if (
                    state.pending_checkpoint
                    and (checkpoint_policy is Policy.AUTO or explicit_checkpoint_mode is not None)
                    and not has_checkpoint
                    and not (
                        has_outcome
                        and state.pending_mode == CheckpointMode.ORIENT.value
                        and "pre-compact" not in state.pending_reasons
                        and explicit_checkpoint_mode is None
                    )
                ):
                    requests.append(_checkpoint_request(state.pending_mode, state.pending_reasons))

                # Grok ignores stdout from passive SessionStart/UserPromptSubmit hooks, so
                # classification cannot reach the model before the first visible message.
                # Grok also paints that message before Stop runs. Continue only when this
                # turn still needs a brief: a missing Outcome on a substantive or
                # action-requested prompt, or an explicit missing checkpoint. A literal
                # follow-up such as "reply with PINEAPPLE" is not a brief-due turn even
                # when a sticky work type remains from earlier work.
                if event.runtime is Runtime.GROK and state.work_type and not typed_valid:
                    brief_due = state.outcome_expected or state.last_prompt_substantive
                    if explicit_checkpoint_mode and not has_checkpoint:
                        boundary = _checkpoint_request(
                            explicit_checkpoint_mode,
                            ["explicit request"],
                        )
                    elif grok_legacy_complete or not brief_due:
                        boundary = None
                    else:
                        errors = (
                            outcome_result.errors
                            if "<!-- briefspec:outcome:v1 -->" in assistant
                            else ()
                        )
                        boundary = _outcome_request(errors)
                    if boundary is not None:
                        grok_request = f"{_classification_context(state)} {boundary}"
                        if grok_request not in requests:
                            requests.append(grok_request)

                # A completed Grok brief already released the TUI: suggested-question
                # chips and queued follow-ups are live. Blocking Stop holds that queue,
                # then the continuation wipes the chips and can fire the wrong prompt.
                if grok_legacy_complete and not (explicit_checkpoint_mode and not has_checkpoint):
                    requests.clear()

                already_active = event.stop_hook_active or state.repair_attempted
                if requests and bool(effective["outcome"]["one_repair"]) and not already_active:
                    state.repair_attempted = True
                    save_session(state)
                    return HookDecision(
                        action="block",
                        reason="\n\n".join(requests),
                        diagnostics=tuple(diagnostics),
                    )
                if requests and already_active:
                    diagnostics.append("repair guard allowed a still-invalid second stop")

            save_session(state)
            return HookDecision(
                action=decision.action,
                reason=decision.reason,
                context=decision.context,
                diagnostics=tuple(diagnostics) or decision.diagnostics,
            )
    except Exception as exc:  # Hooks must fail open.
        return HookDecision(diagnostics=(f"fail-open: {type(exc).__name__}: {exc}",))


def render_decision(
    runtime: Runtime,
    event: EventType,
    decision: HookDecision,
    output_profile: str = "native",
) -> dict[str, Any]:
    if decision.action == "block" and decision.reason:
        result: dict[str, Any] = {"decision": "block", "reason": decision.reason}
        if output_profile == "vscode":
            result["hookSpecificOutput"] = {
                "hookEventName": "Stop",
                "decision": "block",
                "reason": decision.reason,
            }
        return result
    if decision.context:
        if runtime is Runtime.COPILOT:
            result = {"additionalContext": decision.context}
            if output_profile == "vscode":
                result["hookSpecificOutput"] = {
                    "hookEventName": {
                        EventType.SESSION_START: "SessionStart",
                        EventType.POST_TOOL: "PostToolUse",
                        EventType.USER_PROMPT: "UserPromptSubmit",
                    }.get(event, "SessionStart"),
                    "additionalContext": decision.context,
                }
            return result
        event_name = {
            EventType.SESSION_START: "SessionStart",
            EventType.POST_TOOL: "PostToolUse",
            EventType.USER_PROMPT: "UserPromptSubmit",
        }.get(event, "SessionStart")
        return {
            "hookSpecificOutput": {
                "hookEventName": event_name,
                "additionalContext": decision.context,
            }
        }
    return {}


def emit_diagnostics(decision: HookDecision) -> None:
    for diagnostic in decision.diagnostics:
        print(f"brief-spec: {diagnostic}", file=sys.stderr)


def read_hook_payload(stream: Any, limit: int = 1024 * 1024) -> dict[str, Any]:
    raw = stream.buffer.read(limit + 1) if hasattr(stream, "buffer") else stream.read(limit + 1)
    if isinstance(raw, str):
        raw = raw.encode()
    if len(raw) > limit:
        raise ValueError("hook payload exceeds 1 MiB")
    value = json.loads(raw or b"{}")
    if not isinstance(value, dict):
        raise ValueError("hook payload must be a JSON object")
    return value
