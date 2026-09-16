from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from briefspec.models import EventType, Runtime, RuntimeEvent

MAX_TRANSCRIPT_BYTES = 256 * 1024
# Grok fires an observe-only Stop when the session closes; its output is ignored by the host.
SESSION_END_STOP_REASONS = frozenset({"channel_closed", "shutdown"})

EVENT_ALIASES = {
    "sessionstart": EventType.SESSION_START,
    "session_start": EventType.SESSION_START,
    "sessionend": EventType.SESSION_END,
    "session_end": EventType.SESSION_END,
    "session_shutdown": EventType.SESSION_END,
    "pretooluse": EventType.PRE_TOOL,
    "pre_tool_use": EventType.PRE_TOOL,
    "userpromptsubmit": EventType.USER_PROMPT,
    "userpromptsubmitted": EventType.USER_PROMPT,
    "user_prompt": EventType.USER_PROMPT,
    "before_agent_start": EventType.USER_PROMPT,
    "turn_start": EventType.USER_PROMPT,
    "posttooluse": EventType.POST_TOOL,
    "post_tool_use": EventType.POST_TOOL,
    "tool_result": EventType.POST_TOOL,
    "precompact": EventType.PRE_COMPACT,
    "pre_compact": EventType.PRE_COMPACT,
    "session_before_compact": EventType.PRE_COMPACT,
    "session.compacting": EventType.PRE_COMPACT,
    "stop": EventType.AGENT_STOP,
    "agentstop": EventType.AGENT_STOP,
    "agent_stop": EventType.AGENT_STOP,
    "agent_end": EventType.AGENT_STOP,
    "subagentstart": EventType.SUBAGENT_START,
    "subagent_start": EventType.SUBAGENT_START,
    "subagentstop": EventType.SUBAGENT_STOP,
    "subagent_stop": EventType.SUBAGENT_STOP,
    "erroroccurred": EventType.ERROR,
    "error": EventType.ERROR,
}


def canonical_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def parse_time(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        if value > 10_000_000_000:
            value = value / 1000
        return datetime.fromtimestamp(value, tz=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.now(UTC)


def event_type(payload: dict[str, Any], explicit: str | None = None) -> EventType:
    raw = (
        explicit
        or payload.get("hook_event_name")
        or payload.get("hookEventName")
        or payload.get("eventName")
    )
    if not raw:
        raw = payload.get("event") or payload.get("type")
    normalized = str(raw or "").replace("-", "").replace(" ", "").lower()
    return EVENT_ALIASES.get(normalized, EventType.UNKNOWN)


def _content_text(content: Any) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: list[str] = []
        for item in content:
            if isinstance(item, str):
                pieces.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                pieces.append(item["text"])
        return "\n".join(pieces) or None
    if isinstance(content, dict):
        for key in ("text", "content", "message"):
            result = _content_text(content.get(key))
            if result:
                return result
    return None


def read_last_assistant_message(path: Path) -> str | None:
    """Read a bounded transcript tail without following a symlink."""
    try:
        if path.is_symlink() or not path.is_file():
            return None
        size = path.stat().st_size
        with path.open("rb") as handle:
            handle.seek(max(0, size - MAX_TRANSCRIPT_BYTES))
            raw = handle.read(MAX_TRANSCRIPT_BYTES)
    except OSError:
        return None
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if size > MAX_TRANSCRIPT_BYTES and lines:
        lines = lines[1:]
    for line in reversed(lines):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        role = value.get("role") or value.get("type")
        message = value.get("message") if isinstance(value.get("message"), dict) else value
        message_role = message.get("role") if isinstance(message, dict) else None
        if str(role or message_role).lower() not in {"assistant", "assistant_message"}:
            continue
        content = message.get("content") if isinstance(message, dict) else None
        result = _content_text(content)
        if result:
            return result
    return None


def normalize_common(
    runtime: Runtime,
    payload: dict[str, Any],
    explicit_event: str | None = None,
) -> RuntimeEvent:
    kind = event_type(payload, explicit_event)
    raw_reason = payload.get("reason") if kind is EventType.AGENT_STOP else None
    stop_reason = raw_reason if isinstance(raw_reason, str) and raw_reason else None
    if stop_reason in SESSION_END_STOP_REASONS:
        kind = EventType.SESSION_END
    session_id = str(
        payload.get("session_id")
        or payload.get("sessionId")
        or payload.get("conversation_id")
        or payload.get("conversationId")
        or "anonymous"
    )
    transcript_raw = payload.get("transcript_path") or payload.get("transcriptPath")
    transcript = Path(str(transcript_raw)).expanduser() if transcript_raw else None
    assistant = (
        _content_text(payload.get("last_assistant_message"))
        or _content_text(payload.get("lastAssistantMessage"))
        or _content_text(payload.get("response"))
        or _content_text(payload.get("assistant_text"))
        or _content_text(payload.get("assistantText"))
    )
    if not assistant and transcript and kind is EventType.AGENT_STOP:
        assistant = read_last_assistant_message(transcript)
    prompt = _content_text(payload.get("prompt")) or ""
    cwd_raw = payload.get("cwd")
    cwd = Path(str(cwd_raw)).expanduser() if cwd_raw else None
    return RuntimeEvent(
        runtime=runtime,
        type=kind,
        session_id=session_id,
        turn_id=str(payload.get("turn_id") or payload.get("turnId") or "") or None,
        occurred_at=parse_time(payload.get("timestamp") or payload.get("occurred_at")),
        cwd=cwd,
        assistant_text=assistant,
        transcript_path=transcript,
        stop_hook_active=bool(
            payload.get("stop_hook_active")
            or payload.get("stopHookActive")
            or os.environ.get("BRIEFSPEC_STOP_HOOK_ACTIVE") == "1"
        ),
        payload_hash=canonical_hash(payload),
        prompt_chars=len(prompt),
        assistant_chars=len(assistant or ""),
        tool_calls=1 if kind is EventType.POST_TOOL else 0,
        stop_reason=stop_reason,
    )
