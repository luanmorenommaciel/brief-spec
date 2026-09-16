from __future__ import annotations

import json
import re
import sys
from datetime import UTC
from typing import Any

from briefspec.adapters.base import _content_text
from briefspec.config import load_config
from briefspec.continuity import detect_method_context, method_context
from briefspec.markdown import (
    CHECKPOINT_PATTERN,
    OUTCOME_START,
    parse_typed,
    validate_checkpoint,
    validate_outcome,
)
from briefspec.models import (
    CheckpointMode,
    ClassificationOrigin,
    EventType,
    HookDecision,
    Policy,
    Runtime,
    RuntimeEvent,
    SessionState,
)
from briefspec.state import load_session, save_session, session_lock
from briefspec.triggers import eligibility_reasons, reset_window, update_counters
from briefspec.work_types import (
    Classification,
    classify_task,
    explicit_type_requested,
    is_clear_pivot,
    is_soft_pivot,
    is_substantive,
    type_profile,
)

SESSION_CONTEXT = (
    "Brief-Spec is active. For substantive work, use the brief-spec router: pick one work type "
    "and subject and keep them until the task closes or the user clearly pivots. When the task "
    "ends, close with outcome-brief inside the typed wrapper; a DONE result with nothing left "
    "open may use the compact form (Status, Outcome, Proof). Use session-checkpoint only at a "
    "natural boundary. Preserve proof and explicit gaps; never infer success."
)

# Text the host inserts into the prompt stream on the user's behalf: background task
# notifications, system reminders, slash-command echoes, and hook feedback. None of it is the
# user's request, so it must not classify work, request checkpoints, or pick a method.
_HOST_BLOCK = re.compile(
    r"<(system-reminder|task-notification|local-command-caveat|local-command-stdout|"
    r"local-command-stderr|command-name|command-message|bash-input|bash-stdout|bash-stderr|"
    r"user-prompt-submit-hook)\b[^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_COMMAND_ARGS_TAG = re.compile(r"</?command-args\s*>", re.IGNORECASE)
_HOST_PREFIXES = (
    "[system notification",
    "stop hook feedback",
    "subagentstop hook feedback",
    "caveat: the messages below were generated",
    "<task-notification",
)

# Hosts that rebuild the system prompt on every turn, or drop prompt-hook output, need the
# full guidance each time rather than the short reminder.
_FULL_CONTEXT_EVERY_TURN = frozenset({Runtime.OMP, Runtime.GROK})
# Hosts whose prompt hook cannot reach the model receive the decision on the first tool event.
_LATE_CONTEXT_EVENT = {
    Runtime.GROK: EventType.PRE_TOOL,
    Runtime.COPILOT: EventType.POST_TOOL,
}
# Hosts that display a hook's systemMessage to the person.
_NOTICE_RUNTIMES = frozenset({Runtime.CLAUDE})

CHECKPOINT_NUDGE = (
    "A Brief-Spec checkpoint is eligible. At the next natural boundary, offer or include an "
    "orient checkpoint without interrupting active work."
)


def user_authored_text(prompt: str) -> str:
    """Return the part of a prompt the user wrote, without host-inserted wrappers."""
    value = _HOST_BLOCK.sub(" ", prompt)
    value = _COMMAND_ARGS_TAG.sub(" ", value).strip()
    if value.lower().startswith(_HOST_PREFIXES):
        return ""
    return value


def _typed_marker(state: SessionState) -> str:
    return (
        "<!-- brief-spec:typed:v1 "
        f"type={state.work_type} subject={state.subject} "
        f"confidence={state.classification_confidence} origin={state.classification_origin} "
        f"classified_at={state.classified_at} profile=1.0 "
        f"decision_id={state.classification_decision_id} -->"
    )


def _section_labels(state: SessionState) -> str:
    profile = type_profile(state.work_type or "general")
    return ", ".join(section.label for section in profile.sections)


def _classification_context(state: SessionState) -> str:
    method = method_context(state.method_context, phase=state.method_phase)
    phase = f" in phase {method['phase']}" if method["phase"] else ""
    return (
        "Brief-Spec classified this task as "
        f"{state.work_type} + {state.subject} ({state.classification_confidence}, "
        f"{state.classification_origin}). Use the brief-spec skill and explain it with these "
        f"sections in order: {_section_labels(state)}. Keep this type until the task closes "
        "or the user clearly pivots. "
        f"The method context is {method['method']}{phase} ({state.method_context_origin}); "
        "say what is happening, why it matters, and the next human action. "
        "At a terminal Outcome or Checkpoint, wrap the explanation and unchanged legacy brief "
        "inside brief-spec:typed:v1. The authoritative opening marker is exactly `"
        f"{_typed_marker(state)}`. Copy it character-for-character; never use placeholders. "
        "A short DONE answer with nothing left open may skip the sections and the wrapper and "
        "use the compact Outcome Brief (Status, Outcome, Proof)."
    )


def _classification_reminder(state: SessionState) -> str:
    return (
        f"Brief-Spec task: {state.work_type} + {state.subject}; sections: "
        f"{_section_labels(state)}. Typed wrapper opener: `{_typed_marker(state)}`."
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


def _outcome_request(errors: tuple[str, ...] = ()) -> str:
    detail = f" Fix: {'; '.join(errors)}." if errors else ""
    return (
        "Before ending this turn, close the completed task with one valid Brief-Spec Outcome "
        "Brief. Use the outcome-brief skill. Keep Status, Outcome, Human action, Proof, Gaps, "
        f"Next, and Open in that order.{detail}"
    )


def _apply_classification(state: SessionState, classified: Classification) -> None:
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
    state.task_closed = False
    state.context_delivered_decision_id = None


def _record_terminal_message(
    state: SessionState,
    event: RuntimeEvent,
    assistant: str,
    *,
    outcome_valid: bool,
    outcome_errors: tuple[str, ...],
    outcome_status: str | None,
    checkpoint_valid: bool,
    checkpoint_errors: tuple[str, ...],
) -> str | None:
    """Record whether the last terminal message carried a valid brief; return a notice."""
    has_outcome_marker = OUTCOME_START in assistant
    has_checkpoint_marker = CHECKPOINT_PATTERN.search(assistant) is not None
    state.last_brief_at = event.occurred_at.astimezone(UTC).isoformat()
    if has_outcome_marker:
        state.last_brief_kind = "outcome"
        state.last_brief_valid = outcome_valid
        state.last_brief_status = outcome_status if outcome_valid else None
        state.last_brief_errors = [] if outcome_valid else list(outcome_errors[:5])
    elif has_checkpoint_marker:
        state.last_brief_kind = "checkpoint"
        state.last_brief_valid = checkpoint_valid
        state.last_brief_status = None
        state.last_brief_errors = [] if checkpoint_valid else list(checkpoint_errors[:5])
    else:
        state.last_brief_kind = None
        state.last_brief_valid = None
        state.last_brief_status = None
        state.last_brief_errors = []
        return None
    if state.last_brief_valid:
        state.briefs_validated += 1
        return None
    state.briefs_invalid += 1
    label = "Outcome Brief" if state.last_brief_kind == "outcome" else "Session Checkpoint"
    detail = "; ".join(state.last_brief_errors[:2]) or "the contract did not validate"
    return f"Brief-Spec: this {label} is not valid: {detail}."


def process_event(
    event: RuntimeEvent,
    payload: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> HookDecision:
    if event.type is EventType.SESSION_END:
        # Observe-only boundary: the host ignores the output and no work state changes.
        return HookDecision()
    effective = config or load_config(event.cwd)
    diagnostics: list[str] = []
    raw_prompt = _content_text(payload.get("prompt")) or ""
    prompt = user_authored_text(raw_prompt)
    host_generated = event.type is EventType.USER_PROMPT and not prompt and bool(raw_prompt.strip())
    try:
        with session_lock(event.runtime, event.session_id):
            state = load_session(event.runtime, event.session_id, event.occurred_at)
            if event.payload_hash in state.recent_event_hashes:
                return HookDecision(diagnostics=("duplicate event ignored",))
            state.recent_event_hashes.append(event.payload_hash)
            state.recent_event_hashes = state.recent_event_hashes[-32:]
            if host_generated:
                # A background notification or hook echo is not a user turn.
                state.updated_at = event.occurred_at.astimezone(UTC).isoformat()
                save_session(state)
                return HookDecision()
            update_counters(state, event, prompt)
            if event.type is EventType.USER_PROMPT:
                state.last_prompt_substantive = is_substantive(prompt)
            if event.type in {EventType.SESSION_START, EventType.PRE_COMPACT}:
                state.guidance_delivered = False

            checkpoint_policy = Policy(str(effective["checkpoint"]["policy"]))
            outcome_policy = Policy(str(effective["outcome"]["policy"]))
            typing = effective.get("typing", {})
            typing_enabled = bool(typing.get("enabled", True))
            typing_activation = str(typing.get("activation", "substantive"))
            host_context = (
                payload.get("brief_spec") if isinstance(payload.get("brief_spec"), dict) else None
            )
            classifiable = (
                event.type is EventType.USER_PROMPT
                and typing_enabled
                and (
                    (typing_activation == "explicit" and "brief-spec" in prompt.lower())
                    or (typing_activation != "explicit" and is_substantive(prompt))
                )
            )
            classification_changed = False
            if classifiable:
                reclassify = (
                    not state.work_type
                    or state.task_closed
                    or not bool(typing.get("sticky", True))
                    or is_clear_pivot(prompt)
                    or explicit_type_requested(prompt)
                    or bool(host_context and host_context.get("work_type"))
                )

                def classify() -> Classification:
                    return classify_task(
                        prompt,
                        host_context=host_context,
                        default_type=str(typing.get("default_type", "general")),
                        now=event.occurred_at,
                    )

                if reclassify:
                    _apply_classification(state, classify())
                    classification_changed = True
                elif is_soft_pivot(prompt):
                    candidate = classify()
                    if (
                        candidate.origin != ClassificationOrigin.FALLBACK.value
                        and candidate.work_type != state.work_type
                    ):
                        _apply_classification(state, candidate)
                        classification_changed = True
            method_due = (
                event.type is EventType.USER_PROMPT
                and bool(prompt)
                and (
                    state.method_context == "general"
                    or classification_changed
                    or bool(host_context and host_context.get("method_context"))
                )
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

            task_open = bool(state.work_type) and not state.task_closed
            contexts: list[str] = []
            if event.type is EventType.SESSION_START and (
                checkpoint_policy is not Policy.OFF or outcome_policy is not Policy.OFF
            ):
                contexts.append(SESSION_CONTEXT)
            elif event.type is EventType.USER_PROMPT and task_open:
                full = not state.guidance_delivered or event.runtime in _FULL_CONTEXT_EVERY_TURN
                contexts.append(
                    _classification_context(state) if full else _classification_reminder(state)
                )
                state.guidance_delivered = True

            late_event = _LATE_CONTEXT_EVENT.get(event.runtime)
            if (
                event.type is late_event
                and task_open
                and state.classification_decision_id
                and state.context_delivered_decision_id != state.classification_decision_id
                and (state.last_prompt_substantive or state.outcome_expected)
            ):
                contexts.append(_classification_context(state))
                state.context_delivered_decision_id = state.classification_decision_id

            if (
                event.type is EventType.POST_TOOL
                and state.pending_checkpoint
                and checkpoint_policy is Policy.SUGGEST
                and state.last_suggested_at is None
            ):
                # One suggestion per eligibility window; the window restarts at the next
                # valid checkpoint or Outcome Brief.
                state.last_suggested_at = event.occurred_at.astimezone(UTC).isoformat()
                contexts.append(CHECKPOINT_NUDGE)

            decision = HookDecision(context=" ".join(contexts) if contexts else None)

            if event.type is EventType.AGENT_STOP:
                assistant = event.assistant_text or ""
                outcome_expected = state.outcome_expected
                outcome_result = validate_outcome(assistant)
                has_outcome = outcome_result.valid
                compact_outcome = has_outcome and outcome_result.data.get("Form") == "compact"
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
                notice = _record_terminal_message(
                    state,
                    event,
                    assistant,
                    outcome_valid=has_outcome,
                    outcome_errors=outcome_result.errors,
                    outcome_status=(
                        str(outcome_result.data.get("Status")) if has_outcome else None
                    ),
                    checkpoint_valid=has_checkpoint,
                    checkpoint_errors=checkpoint_result.errors,
                )
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
                    state.task_closed = True
                    if (
                        state.pending_checkpoint
                        and state.pending_mode == CheckpointMode.ORIENT.value
                        and "pre-compact" not in state.pending_reasons
                        and explicit_checkpoint_mode is None
                    ):
                        state.pending_checkpoint = False
                        state.pending_reasons = []
                    reset_window(state, event.occurred_at)
                if has_checkpoint:
                    state.pending_checkpoint = False
                    state.pending_reasons = []
                    state.last_checkpoint_at = event.occurred_at.astimezone(UTC).isoformat()
                    state.last_checkpoint_turn = state.turn_count
                    reset_window(state, event.occurred_at)

                requests: list[str] = []
                grok_legacy_complete = event.runtime is Runtime.GROK and (
                    has_outcome or has_checkpoint
                )
                if outcome_expected and outcome_policy is Policy.ENFORCE and not has_outcome:
                    errors = outcome_result.errors if OUTCOME_START in assistant else ()
                    requests.append(_outcome_request(errors))
                elif (
                    outcome_expected
                    and outcome_policy is Policy.ENFORCE
                    and typing_enabled
                    and state.work_type
                    and has_outcome
                    and not compact_outcome
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
                        errors = outcome_result.errors if OUTCOME_START in assistant else ()
                        boundary = _outcome_request(errors)
                    if boundary is not None:
                        grok_request = f"{_classification_context(state)} {boundary}"
                        if grok_request not in requests:
                            requests.append(grok_request)
                            state.context_delivered_decision_id = state.classification_decision_id

                # A completed Grok brief already released the TUI: suggested-question
                # chips and queued follow-ups are live. Blocking Stop holds that queue,
                # then the continuation wipes the chips and can fire the wrong prompt.
                if grok_legacy_complete and not (explicit_checkpoint_mode and not has_checkpoint):
                    requests.clear()
                # Grok parses but ignores decisions from a Stop that is not a normal turn end.
                if event.runtime is Runtime.GROK and event.stop_reason not in {None, "end_turn"}:
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
                decision = HookDecision(notice=notice)

            save_session(state)
            return HookDecision(
                action=decision.action,
                reason=decision.reason,
                context=decision.context,
                diagnostics=tuple(diagnostics) or decision.diagnostics,
                notice=decision.notice if event.runtime in _NOTICE_RUNTIMES else None,
            )
    except Exception as exc:  # Hooks must fail open.
        return HookDecision(diagnostics=(f"fail-open: {type(exc).__name__}: {exc}",))


_CONTEXT_EVENT_NAMES = {
    EventType.SESSION_START: "SessionStart",
    EventType.PRE_TOOL: "PreToolUse",
    EventType.POST_TOOL: "PostToolUse",
    EventType.USER_PROMPT: "UserPromptSubmit",
}


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
    result = {}
    if decision.context:
        event_name = _CONTEXT_EVENT_NAMES.get(event, "SessionStart")
        if runtime is Runtime.COPILOT:
            result = {"additionalContext": decision.context}
            if output_profile == "vscode":
                result["hookSpecificOutput"] = {
                    "hookEventName": event_name,
                    "additionalContext": decision.context,
                }
        else:
            result = {
                "hookSpecificOutput": {
                    "hookEventName": event_name,
                    "additionalContext": decision.context,
                }
            }
    if decision.notice and runtime in _NOTICE_RUNTIMES:
        result["systemMessage"] = decision.notice
    return result


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
