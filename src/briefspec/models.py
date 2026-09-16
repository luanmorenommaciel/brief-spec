from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any


class Runtime(StrEnum):
    CODEX = "codex"
    CLAUDE = "claude"
    COPILOT = "copilot"
    OMP = "omp"
    GROK = "grok"
    KIMI = "kimi"
    CURSOR = "cursor"
    GOOSE = "goose"
    ANTIGRAVITY = "antigravity"


class WorkType(StrEnum):
    GENERAL = "general"
    EXPLORATION = "exploration"
    REVIEW = "review"
    IMPLEMENTATION = "implementation"
    DEBUGGING = "debugging"
    PLANNING = "planning"
    RESEARCH = "research"
    OPERATIONS = "operations"


class ClassificationConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ClassificationOrigin(StrEnum):
    EXPLICIT = "explicit"
    HOST = "host"
    INFERRED = "inferred"
    FALLBACK = "fallback"
    REPORTED = "reported"


class EventType(StrEnum):
    SESSION_START = "session_start"
    USER_PROMPT = "user_prompt"
    POST_TOOL = "post_tool"
    PRE_COMPACT = "pre_compact"
    AGENT_STOP = "agent_stop"
    SUBAGENT_START = "subagent_start"
    SUBAGENT_STOP = "subagent_stop"
    ERROR = "error"
    UNKNOWN = "unknown"


class OutcomeStatus(StrEnum):
    DONE = "DONE"
    REVIEW = "REVIEW"
    DECIDE = "DECIDE"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class CheckpointMode(StrEnum):
    ORIENT = "orient"
    TEACH = "teach"
    SPOKEN = "spoken"


class Policy(StrEnum):
    OFF = "off"
    MANUAL = "manual"
    SUGGEST = "suggest"
    AUTO = "auto"
    ENFORCE = "enforce"


class AccessLevel(StrEnum):
    LOCAL = "local"
    PRIVATE = "private"
    PUBLIC = "public"


class WorkActivity(StrEnum):
    RUNNING = "RUNNING"
    NEEDS_INPUT = "NEEDS_INPUT"
    WAITING = "WAITING"
    PAUSED = "PAUSED"
    STALE = "STALE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class VerificationLevel(StrEnum):
    STRUCTURAL = "structural"
    RESOLVED = "resolved"
    RENDERED = "rendered"
    DELIVERED = "delivered"


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    kind: str
    label: str
    locator: str
    basis: str = "direct"
    result: str = "info"
    revision: str | None = None
    observed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True, slots=True)
class SourceMetadata:
    harness: str = "unknown"
    session_ref: str | None = None
    brief_spec_version: str = ""
    host_version: str | None = None
    adapter_version: str | None = None
    source_revision: str | None = None
    created_at: str = ""
    model_provider: str | None = None
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True, slots=True)
class ProvenanceRef:
    provider: str
    locator: str
    retrieved_at: str
    basis: str = "direct"
    access: str = AccessLevel.PUBLIC.value
    content_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    artifact_id: str
    role: str
    locator: str
    media_type: str
    access: str = AccessLevel.LOCAL.value
    size_bytes: int | None = None
    sha256: str | None = None
    source_revision: str | None = None
    observed_at: str | None = None
    expires_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True, slots=True)
class WorkItem:
    work_id: str
    activity: str
    headline: str
    last_updated: str
    parent_id: str | None = None
    runtime: str | None = None
    agent_ref: str | None = None
    human_action: str | None = None
    result_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    runtime: Runtime
    type: EventType
    session_id: str
    occurred_at: datetime
    payload_hash: str
    cwd: Path | None = None
    turn_id: str | None = None
    assistant_text: str | None = None
    transcript_path: Path | None = None
    stop_hook_active: bool = False
    prompt_chars: int = 0
    assistant_chars: int = 0
    tool_calls: int = 0


@dataclass(slots=True)
class SessionState:
    runtime: str
    session_id: str
    started_at: str
    updated_at: str
    turn_count: int = 0
    tool_count: int = 0
    prompt_chars: int = 0
    assistant_chars: int = 0
    pending_checkpoint: bool = False
    pending_mode: str = CheckpointMode.ORIENT.value
    pending_reasons: list[str] = field(default_factory=list)
    outcome_expected: bool = False
    last_prompt_substantive: bool = False
    repair_attempted: bool = False
    last_suggested_at: str | None = None
    last_checkpoint_at: str | None = None
    last_checkpoint_turn: int = 0
    recent_event_hashes: list[str] = field(default_factory=list)
    work_type: str | None = None
    subject: str | None = None
    classification_confidence: str | None = None
    classification_origin: str | None = None
    classification_rule_ids: list[str] = field(default_factory=list)
    classified_at: str | None = None
    classification_decision_id: str | None = None
    classification_input_sha256: str | None = None
    classification_record_sha256: str | None = None
    classification_adapter_version: str | None = None
    method_context: str = "general"
    method_phase: str | None = None
    method_context_origin: str = "fallback"

    @classmethod
    def new(cls, runtime: Runtime, session_id: str, now: datetime) -> SessionState:
        stamp = now.astimezone(UTC).isoformat()
        return cls(
            runtime=runtime.value,
            session_id=session_id,
            started_at=stamp,
            updated_at=stamp,
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SessionState:
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        return cls(**{key: item for key, item in value.items() if key in allowed})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ValidationResult:
    valid: bool
    kind: str
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "kind": self.kind,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "data": self.data,
        }


@dataclass(frozen=True, slots=True)
class HookDecision:
    action: str = "allow"
    reason: str | None = None
    context: str | None = None
    diagnostics: tuple[str, ...] = ()
