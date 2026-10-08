from __future__ import annotations

import hashlib
import html
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from briefspec import __version__
from briefspec.markdown import (
    END_MARKER,
    OUTCOME_START,
    detect_kind,
    extract_bounded,
    parse_typed,
    validate_checkpoint,
    validate_outcome,
)
from briefspec.models import AccessLevel, ValidationResult, WorkActivity
from briefspec.secret_scan import scan_value
from briefspec.state import atomic_write_many
from briefspec.work_types import (
    PROFILE_VERSION,
    PROFILES,
    Classification,
    type_profile,
    validate_explanation,
)

DELIVERY_SCHEMA_VERSION = "2.0"
DELIVERY_KIND = "brief-spec-delivery"
LEGACY_DELIVERY_KIND = "briefspec-delivery"
CORE_RENDERER_VERSION = "1.0"

_EVIDENCE = re.compile(
    r"^\[(?P<basis>direct|derived|reported)/(?P<result>pass|fail|info)"
    r"(?P<meta>(?:\s+[^\]]+)?)\]\s+(?P<label>.+)$",
    re.IGNORECASE,
)
_META = re.compile(r"(?P<key>[a-z_][a-z0-9_-]*)=(?P<value>[^\s]+)", re.IGNORECASE)
_MARKDOWN_LINK = re.compile(r"\[[^\]\n]+\]\((?P<locator>[^)]+)\)")
_BACKTICK = re.compile(r"`(?P<locator>[^`\n]+)`")
_URL = re.compile(r"https?://[^\s)]+")
_PR_ISSUE = re.compile(r"\b(?P<kind>PR|issue)\s*#?(?P<number>\d+)\b", re.IGNORECASE)
_COMMIT = re.compile(r"\b[0-9a-f]{7,40}\b", re.IGNORECASE)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SHELL_EXPRESSION = re.compile(r"\s|[|;&<>$()]|(?:^|\s)-{1,2}[a-z0-9]", re.IGNORECASE)

_FIELD_LABELS = {
    "status": "Status",
    "outcome": "Outcome",
    "human_action": "Human action",
    "proof": "Proof",
    "gaps": "Gaps",
    "next": "Next",
    "open": "Open",
    "headline": "Headline",
    "current_state": "Current state",
    "completed": "Completed",
    "decisions": "Decisions",
    "mental_model": "Mental model",
    "why_it_matters": "Why it matters",
    "what_changed": "What changed",
    "example": "Example",
    "watch_outs": "Watch-outs",
    "script": "Script",
}

_CHECKPOINT_FIELDS = {
    "orient": (
        "headline",
        "current_state",
        "completed",
        "decisions",
        "proof",
        "next",
        "open",
    ),
    "teach": (
        "headline",
        "mental_model",
        "why_it_matters",
        "what_changed",
        "example",
        "watch_outs",
        "next",
        "proof",
    ),
    "spoken": ("headline", "script", "proof", "next"),
}


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def canonical_json_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ": "),
        ).encode("utf-8")
        + b"\n"
    )


def canonical_sha256(value: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def _created_at(source_path: Path | None, explicit: str | None) -> str:
    if explicit:
        parsed = datetime.fromisoformat(explicit.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch:
        return datetime.fromtimestamp(int(epoch), tz=UTC).isoformat().replace("+00:00", "Z")
    if source_path is not None and source_path.exists():
        return (
            datetime.fromtimestamp(source_path.stat().st_mtime, tz=UTC)
            .isoformat()
            .replace("+00:00", "Z")
        )
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _items(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    if text.lower() in {"", "none", "n/a", "not applicable"}:
        return []
    return [text]


def _text(value: Any) -> str | None:
    if isinstance(value, list):
        value = "\n".join(str(item) for item in value)
    text = str(value or "").strip()
    return None if text.lower() in {"", "none", "n/a", "not applicable"} else text


def _locator(label: str) -> tuple[str, str]:
    match = _MARKDOWN_LINK.search(label)
    if match:
        value = match.group("locator")
        return "url" if value.startswith(("http://", "https://")) else "file", value
    match = _URL.search(label)
    if match:
        return "url", match.group(0).rstrip(".,")
    match = _PR_ISSUE.search(label)
    if match:
        return match.group("kind").lower(), match.group(0)
    match = _COMMIT.search(label)
    if match:
        return "commit", match.group(0)
    match = _BACKTICK.search(label)
    if match:
        value = match.group("locator")
        if _SHELL_EXPRESSION.search(value):
            return "observation", value
        path_like = "/" in value or "\\" in value or Path(value).suffix != ""
        return ("file" if path_like else "observation"), value
    return "observation", label


def parse_evidence(item: str) -> dict[str, Any]:
    match = _EVIDENCE.match(item.strip())
    if match:
        metadata = {
            found.group("key").lower(): found.group("value")
            for found in _META.finditer(match.group("meta"))
        }
        label = match.group("label").strip()
        inferred_kind, locator = _locator(label)
        return {
            "kind": metadata.get("kind", inferred_kind),
            "label": label,
            "locator": locator,
            "basis": match.group("basis").lower(),
            "result": match.group("result").lower(),
        }
    inferred_kind, locator = _locator(item)
    return {
        "kind": inferred_kind,
        "label": item.strip(),
        "locator": locator,
        "basis": "reported",
        "result": "info",
    }


def render_evidence(evidence: dict[str, Any]) -> str:
    basis = str(evidence.get("basis", "reported"))
    result = str(evidence.get("result", "info"))
    kind = str(evidence.get("kind", "observation"))
    label = str(evidence.get("label") or evidence.get("locator") or "unresolved evidence")
    return f"[{basis}/{result} kind={kind}] {label}"


def _brief_from_markdown(text: str) -> tuple[dict[str, Any], list[str]]:
    kind = detect_kind(text)
    if kind == "outcome":
        result = validate_outcome(text)
        raw = result.data
        brief = {
            "schema_version": "1.0",
            "kind": "outcome-brief",
            "status": str(raw.get("Status", "")),
            "outcome": _text(raw.get("Outcome")) or "",
            "human_action": _text(raw.get("Human action")),
            "proof": [parse_evidence(item) for item in _items(raw.get("Proof"))],
            "gaps": _items(raw.get("Gaps")),
            "next": _items(raw.get("Next")),
            "open": _items(raw.get("Open")),
        }
    elif kind == "checkpoint":
        result = validate_checkpoint(text)
        raw = result.data
        mode = str(raw.get("Mode", ""))
        brief = {
            "schema_version": "1.0",
            "kind": "session-checkpoint",
            "mode": mode,
        }
        for field in _CHECKPOINT_FIELDS.get(mode, ()):
            source_name = (
                "Screen-only proof"
                if field == "proof" and mode == "spoken"
                else _FIELD_LABELS[field]
            )
            value = raw.get(source_name)
            if field == "proof":
                brief[field] = [parse_evidence(item) for item in _items(value)]
            elif field in {"completed", "decisions", "what_changed", "watch_outs", "next", "open"}:
                brief[field] = _items(value)
            else:
                brief[field] = _text(value)
    else:
        raise ValueError("No Brief-Spec marker found")
    if not result.valid:
        raise ValueError("; ".join(result.errors))
    warnings = list(result.warnings)
    if any(
        evidence.get("kind") == "observation"
        and (match := _BACKTICK.search(str(evidence.get("label", "")))) is not None
        and _SHELL_EXPRESSION.search(match.group("locator"))
        for evidence in brief.get("proof", [])
    ):
        warnings.append(
            "Ambiguous backtick evidence was retained as an unresolved observation; no command "
            "was executed."
        )
    return brief, warnings


def _default_explanation(brief: dict[str, Any]) -> dict[str, Any]:
    profile = type_profile("general")
    if brief.get("kind") == "outcome-brief":
        content = {
            "answer": str(brief.get("outcome") or "Outcome brief supplied."),
            "rationale": (
                "The source used the legacy Brief-Spec contract without a typed explanation."
            ),
            "next_action": "; ".join(_items(brief.get("next"))) or "None",
        }
    else:
        content = {
            "answer": str(brief.get("headline") or "Session checkpoint supplied."),
            "rationale": (
                "The source used the legacy Brief-Spec contract without a typed explanation."
            ),
            "next_action": "; ".join(_items(brief.get("next"))) or "None",
        }
    return {
        "profile_version": PROFILE_VERSION,
        "sections": [
            {"id": item.section_id, "label": item.label, "content": content[item.section_id]}
            for item in profile.sections
        ],
    }


def _legacy_classification(created_at: str) -> dict[str, Any]:
    return Classification(
        work_type="general",
        subject="general",
        confidence="low",
        origin="fallback",
        classified_at=created_at,
        rule_ids=("compat.legacy-untyped",),
    ).to_dict()


def new_delivery(
    brief: dict[str, Any],
    *,
    source_path: Path | None = None,
    runtime: str = "unknown",
    harness: str | None = None,
    session_ref: str | None = None,
    host_version: str | None = None,
    adapter_version: str | None = None,
    source_revision: str | None = None,
    model_provider: str | None = None,
    model: str | None = None,
    created_at: str | None = None,
    classification: dict[str, Any] | None = None,
    explanation: dict[str, Any] | None = None,
    provenance: list[dict[str, Any]] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    work_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    canonical_created_at = _created_at(source_path, created_at)
    source: dict[str, Any] = {
        "harness": harness or runtime,
        "brief_spec_version": __version__,
        "created_at": canonical_created_at,
    }
    optional = {
        "session_ref": session_ref,
        "host_version": host_version,
        "adapter_version": adapter_version,
        "source_revision": source_revision,
        "model_provider": model_provider,
        "model": model,
    }
    source.update({key: value for key, value in optional.items() if value is not None})
    return {
        "schema_version": DELIVERY_SCHEMA_VERSION,
        "kind": DELIVERY_KIND,
        "source": source,
        "classification": classification or _legacy_classification(canonical_created_at),
        "explanation": explanation or _default_explanation(brief),
        "brief": brief,
        "provenance": provenance or [],
        "artifacts": artifacts or [],
        "work_items": work_items or [],
    }


def load_delivery(
    text: str,
    *,
    source_path: Path | None = None,
    runtime: str = "unknown",
    harness: str | None = None,
    session_ref: str | None = None,
    host_version: str | None = None,
    adapter_version: str | None = None,
    source_revision: str | None = None,
    model_provider: str | None = None,
    model: str | None = None,
    created_at: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = None
    if isinstance(value, dict):
        if value.get("kind") == DELIVERY_KIND:
            result = validate_delivery(value)
            if not result.valid:
                raise ValueError("; ".join(result.errors))
            return value, list(result.warnings)
        if value.get("kind") == LEGACY_DELIVERY_KIND:
            legacy_source = value.get("source") if isinstance(value.get("source"), dict) else {}
            delivery = new_delivery(
                value.get("brief", {}),
                runtime=str(legacy_source.get("runtime") or runtime),
                harness=str(legacy_source.get("runtime") or harness or runtime),
                session_ref=legacy_source.get("session_ref"),
                host_version=legacy_source.get("host_version"),
                source_revision=legacy_source.get("source_revision"),
                model=legacy_source.get("model"),
                created_at=legacy_source.get("created_at") or created_at,
                provenance=(
                    value.get("provenance") if isinstance(value.get("provenance"), list) else []
                ),
                artifacts=(
                    value.get("artifacts") if isinstance(value.get("artifacts"), list) else []
                ),
                work_items=(
                    value.get("work_items") if isinstance(value.get("work_items"), list) else []
                ),
            )
            result = validate_delivery(delivery)
            if not result.valid:
                raise ValueError("; ".join(result.errors))
            return delivery, [
                "Legacy briefspec-delivery/1.0 was migrated in memory",
                *result.warnings,
            ]
        if value.get("kind") in {"outcome-brief", "session-checkpoint"}:
            delivery = new_delivery(
                value,
                source_path=source_path,
                runtime=runtime,
                harness=harness,
                session_ref=session_ref,
                host_version=host_version,
                adapter_version=adapter_version,
                source_revision=source_revision,
                model_provider=model_provider,
                model=model,
                created_at=created_at,
            )
            result = validate_delivery(delivery)
            if not result.valid:
                raise ValueError("; ".join(result.errors))
            return delivery, list(result.warnings)
        raise ValueError("JSON input is not a Brief-Spec delivery or brief")
    bounded = extract_bounded(text)
    typed = parse_typed(bounded)
    brief, warnings = _brief_from_markdown(bounded)
    canonical_created_at = _created_at(source_path, created_at)
    classification, explanation = typed or (
        _legacy_classification(canonical_created_at),
        _default_explanation(brief),
    )
    if typed is None:
        warnings.append("Legacy untyped brief loaded as general + general")
    delivery = new_delivery(
        brief,
        source_path=source_path,
        runtime=runtime,
        harness=harness,
        session_ref=session_ref,
        host_version=host_version,
        adapter_version=adapter_version,
        source_revision=source_revision,
        model_provider=model_provider,
        model=model,
        created_at=created_at,
        classification=classification,
        explanation=explanation,
    )
    result = validate_delivery(delivery)
    if not result.valid:
        raise ValueError("; ".join(result.errors))
    return delivery, list(dict.fromkeys([*warnings, *result.warnings]))


_V06_DONE_RULES = (
    "DONE requires at least one [direct/pass]",
    "DONE cannot include failing proof",
)


def _older_than(source: Any, version: tuple[int, int]) -> bool:
    raw = str(source.get("brief_spec_version", "")) if isinstance(source, dict) else ""
    parts = raw.split(".")
    try:
        return (int(parts[0]), int(parts[1])) < version
    except (IndexError, ValueError):
        return False


def validate_delivery(value: dict[str, Any]) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    allowed_delivery = {
        "schema_version",
        "kind",
        "source",
        "classification",
        "explanation",
        "brief",
        "provenance",
        "artifacts",
        "work_items",
    }
    unexpected = sorted(set(value) - allowed_delivery)
    if unexpected:
        errors.append(f"Delivery has unsupported field(s): {', '.join(unexpected)}")
    if value.get("schema_version") != DELIVERY_SCHEMA_VERSION:
        errors.append("Delivery schema_version must be 2.0")
    if value.get("kind") != DELIVERY_KIND:
        errors.append("Delivery kind must be brief-spec-delivery")
    source = value.get("source")
    if not isinstance(source, dict):
        errors.append("Delivery source must be an object")
    else:
        allowed_source = {
            "harness",
            "session_ref",
            "brief_spec_version",
            "host_version",
            "adapter_version",
            "source_revision",
            "model_provider",
            "created_at",
            "model",
        }
        unexpected = sorted(set(source) - allowed_source)
        if unexpected:
            errors.append(f"Delivery source has unsupported field(s): {', '.join(unexpected)}")
        for name in ("harness", "brief_spec_version", "created_at"):
            if not str(source.get(name, "")).strip():
                errors.append(f"Delivery source.{name} is required")
        try:
            datetime.fromisoformat(str(source.get("created_at", "")).replace("Z", "+00:00"))
        except ValueError:
            errors.append("Delivery source.created_at must be an ISO 8601 timestamp")
    classification = value.get("classification")
    explanation = value.get("explanation")
    if not isinstance(classification, dict):
        errors.append("Delivery classification must be an object")
    else:
        allowed_classification = {
            "work_type",
            "subject",
            "confidence",
            "origin",
            "classified_at",
            "profile_version",
            "rule_ids",
            "decision_id",
            "input_sha256",
            "record_sha256",
            "adapter_version",
        }
        unexpected = sorted(set(classification) - allowed_classification)
        if unexpected:
            errors.append(
                f"Delivery classification has unsupported field(s): {', '.join(unexpected)}"
            )
        if classification.get("work_type") not in PROFILES:
            errors.append("Delivery classification.work_type is invalid")
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", str(classification.get("subject", ""))):
            errors.append("Delivery classification.subject must be a normalized slug")
        if classification.get("confidence") not in {"high", "medium", "low"}:
            errors.append("Delivery classification.confidence is invalid")
        if classification.get("origin") not in {
            "explicit",
            "host",
            "inferred",
            "fallback",
            "reported",
        }:
            errors.append("Delivery classification.origin is invalid")
        if classification.get("profile_version") != PROFILE_VERSION:
            errors.append(f"Delivery classification.profile_version must be {PROFILE_VERSION}")
        rule_ids = classification.get("rule_ids")
        if not isinstance(rule_ids, list) or not all(isinstance(item, str) for item in rule_ids):
            errors.append("Delivery classification.rule_ids must be an array of strings")
        for name in ("input_sha256", "record_sha256"):
            if name in classification and not re.fullmatch(
                r"[0-9a-f]{64}", str(classification.get(name, ""))
            ):
                errors.append(f"Delivery classification.{name} must be a SHA-256 digest")
        if "decision_id" in classification and not re.fullmatch(
            r"bsd-[0-9a-f]{24}", str(classification.get("decision_id", ""))
        ):
            errors.append("Delivery classification.decision_id is invalid")
        if classification.get("record_sha256"):
            record = {
                "adapter_version": classification.get("adapter_version"),
                "classified_at": classification.get("classified_at"),
                "confidence": classification.get("confidence"),
                "input_sha256": classification.get("input_sha256"),
                "origin": classification.get("origin"),
                "profile_version": classification.get("profile_version"),
                "rule_ids": classification.get("rule_ids"),
                "subject": classification.get("subject"),
                "work_type": classification.get("work_type"),
            }
            actual_record_sha256 = hashlib.sha256(
                json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            if actual_record_sha256 != classification.get("record_sha256"):
                errors.append("Delivery classification record hash does not match its fields")
            if classification.get("decision_id") != f"bsd-{actual_record_sha256[:24]}":
                errors.append("Delivery classification decision_id does not match its record hash")
        try:
            datetime.fromisoformat(
                str(classification.get("classified_at", "")).replace("Z", "+00:00")
            )
        except ValueError:
            errors.append("Delivery classification.classified_at must be an ISO 8601 timestamp")
    if not isinstance(explanation, dict):
        errors.append("Delivery explanation must be an object")
    elif isinstance(classification, dict):
        errors.extend(validate_explanation(classification, explanation))
    brief = value.get("brief")
    if not isinstance(brief, dict):
        errors.append("Delivery brief must be an object")
    else:
        try:
            markdown = render_markdown(value)
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"Delivery brief cannot be rendered: {exc}")
        else:
            result = (
                validate_outcome(markdown)
                if brief.get("kind") == "outcome-brief"
                else validate_checkpoint(markdown)
            )
            legacy = _older_than(value.get("source", {}), (0, 6))
            for error in result.errors:
                # v0.6 made DONE require a direct pass. Deliveries made by earlier versions stay
                # verifiable; the stricter rule is reported as a warning for them.
                if legacy and error.startswith(_V06_DONE_RULES):
                    warnings.append(f"{error} (made by Brief-Spec < 0.6; accepted)")
                else:
                    errors.append(error)
            warnings.extend(result.warnings)

        proof = brief.get("proof", [])
        if not isinstance(proof, list):
            errors.append("Delivery brief.proof must be an array")
        else:
            allowed_kinds = {
                "file",
                "command",
                "test",
                "commit",
                "url",
                "pr",
                "issue",
                "artifact",
                "observation",
            }
            for index, evidence in enumerate(proof):
                if not isinstance(evidence, dict):
                    errors.append(f"brief.proof[{index}] must be an object")
                    continue
                if evidence.get("kind") not in allowed_kinds:
                    errors.append(f"brief.proof[{index}].kind is invalid")
                if evidence.get("basis") not in {"direct", "derived", "reported"}:
                    errors.append(f"brief.proof[{index}].basis is invalid")
                if evidence.get("result") not in {"pass", "fail", "info"}:
                    errors.append(f"brief.proof[{index}].result is invalid")
                for name in ("label", "locator"):
                    if not str(evidence.get(name, "")).strip():
                        errors.append(f"brief.proof[{index}].{name} is required")

    for path, kind in scan_value(
        {name: value.get(name) for name in ("brief", "explanation", "provenance", "artifacts")}
    ):
        errors.append(f"{path} looks like it contains a {kind}; remove it before sharing")

    provenance = value.get("provenance", [])
    if not isinstance(provenance, list):
        errors.append("Delivery provenance must be an array")
        provenance = []
    for index, item in enumerate(provenance):
        if not isinstance(item, dict):
            errors.append(f"provenance[{index}] must be an object")
            continue
        for name in ("provider", "locator", "retrieved_at", "basis", "access"):
            if not str(item.get(name, "")).strip():
                errors.append(f"provenance[{index}].{name} is required")
        if item.get("access") not in {level.value for level in AccessLevel}:
            errors.append(f"provenance[{index}].access is invalid")
        if item.get("basis") not in {"direct", "derived", "reported"}:
            errors.append(f"provenance[{index}].basis is invalid")
        try:
            datetime.fromisoformat(str(item.get("retrieved_at", "")).replace("Z", "+00:00"))
        except ValueError:
            errors.append(f"provenance[{index}].retrieved_at must be an ISO 8601 timestamp")
        digest = item.get("content_sha256")
        if digest is not None and not _SHA256.fullmatch(str(digest)):
            errors.append(f"provenance[{index}].content_sha256 must be SHA-256")

    artifacts = value.get("artifacts", [])
    if not isinstance(artifacts, list):
        errors.append("Delivery artifacts must be an array")
        artifacts = []
    for index, item in enumerate(artifacts):
        if not isinstance(item, dict):
            errors.append(f"artifacts[{index}] must be an object")
            continue
        for name in ("artifact_id", "role", "locator", "media_type"):
            if not str(item.get(name, "")).strip():
                errors.append(f"artifacts[{index}].{name} is required")
        if item.get("access") not in {level.value for level in AccessLevel}:
            errors.append(f"artifacts[{index}].access is invalid")
        size = item.get("size_bytes")
        if size is not None and (not isinstance(size, int) or isinstance(size, bool) or size < 0):
            errors.append(f"artifacts[{index}].size_bytes must be a non-negative integer")
        digest = item.get("sha256")
        if digest is not None and not _SHA256.fullmatch(str(digest)):
            errors.append(f"artifacts[{index}].sha256 must be SHA-256")
        for name in ("observed_at", "expires_at"):
            stamp = item.get(name)
            if stamp is not None:
                try:
                    datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                except ValueError:
                    errors.append(f"artifacts[{index}].{name} must be an ISO 8601 timestamp")

    work_items = value.get("work_items", [])
    if not isinstance(work_items, list):
        errors.append("Delivery work_items must be an array")
        work_items = []
    for index, item in enumerate(work_items):
        if not isinstance(item, dict):
            errors.append(f"work_items[{index}] must be an object")
            continue
        for name in ("work_id", "activity", "headline", "last_updated"):
            if not str(item.get(name, "")).strip():
                errors.append(f"work_items[{index}].{name} is required")
        if item.get("activity") not in {activity.value for activity in WorkActivity}:
            errors.append(f"work_items[{index}].activity is invalid")
        try:
            datetime.fromisoformat(str(item.get("last_updated", "")).replace("Z", "+00:00"))
        except ValueError:
            errors.append(f"work_items[{index}].last_updated must be an ISO 8601 timestamp")
        if (
            item.get("activity") == WorkActivity.COMPLETED.value
            and not str(item.get("result_ref", "")).strip()
        ):
            warnings.append(f"work_items[{index}] is COMPLETED without a result_ref")
    if any(
        isinstance(item, dict) and item.get("activity") == WorkActivity.RUNNING.value
        for item in work_items
    ) and not _items(brief.get("next") if isinstance(brief, dict) else None):
        errors.append("Active work requires a concrete brief.next action")
    return ValidationResult(
        not errors,
        DELIVERY_KIND,
        tuple(errors),
        tuple(dict.fromkeys(warnings)),
        value,
    )


def _render_field(label: str, value: Any) -> list[str]:
    if isinstance(value, list):
        if not value:
            return [f"{label}: None"]
        return [f"{label}:", *(f"- {item}" for item in value)]
    if value is None or str(value).strip() == "":
        return [f"{label}: None"]
    text = str(value).strip()
    if "\n" in text:
        return [f"{label}:", text]
    return [f"{label}: {text}"]


def render_markdown(delivery: dict[str, Any]) -> str:
    brief = delivery["brief"]
    classification = delivery["classification"]
    explanation = delivery["explanation"]
    kind = brief.get("kind")
    if kind == "outcome-brief":
        marker = OUTCOME_START
        fields = ("status", "outcome", "human_action", "proof", "gaps", "next", "open")
        lead_fields = ("status", "outcome", "human_action")
        mode = None
    elif kind == "session-checkpoint":
        mode = str(brief.get("mode", ""))
        if mode not in _CHECKPOINT_FIELDS:
            raise ValueError(f"Unsupported checkpoint mode: {mode}")
        marker = f"<!-- briefspec:checkpoint:v1 mode={mode} -->"
        fields = _CHECKPOINT_FIELDS[mode]
        lead_fields = ("headline", "current_state") if mode == "orient" else ("headline",)
    else:
        raise ValueError(f"Unsupported brief kind: {kind}")
    lines = [
        "<!-- brief-spec:typed:v1 "
        f"type={classification['work_type']} "
        f"subject={classification['subject']} "
        f"confidence={classification['confidence']} "
        f"origin={classification['origin']} "
        f"classified_at={classification['classified_at']} "
        f"profile={classification['profile_version']}"
        + (
            f" decision_id={classification['decision_id']}"
            if classification.get("decision_id")
            else ""
        )
        + " -->",
    ]
    for field in lead_fields:
        lines.extend(_render_field(_FIELD_LABELS[field], brief.get(field)))
    lines.extend(
        [
            "",
            "Type: "
            f"{classification['work_type']} + {classification['subject']} "
            f"({classification['confidence']}, {classification['origin']})",
            "",
        ]
    )
    for section in explanation["sections"]:
        lines.extend([f"### {section['label']}", "", str(section["content"]), ""])
    lines.append(marker)
    for field in fields:
        label = (
            "Screen-only proof" if field == "proof" and mode == "spoken" else _FIELD_LABELS[field]
        )
        value = brief.get(field)
        if field == "proof" and isinstance(value, list):
            value = [
                render_evidence(item) if isinstance(item, dict) else str(item) for item in value
            ]
        rendered = _render_field(label, value)
        if field in lead_fields:
            lines.extend(["<!-- legacy-compatible duplicate", *rendered, "-->"])
        else:
            lines.extend(rendered)
    lines.append(END_MARKER)
    lines.append("<!-- /brief-spec -->")
    context: list[str] = []
    source = delivery.get("source", {})
    if isinstance(source, dict):
        context.extend(
            [
                "",
                "## Delivery context",
                "",
                f"- Harness: {source.get('harness', 'unknown')}",
                f"- Brief-Spec: {source.get('brief_spec_version', 'unknown')}",
                f"- Created: {source.get('created_at', 'unknown')}",
            ]
        )
        for label, name in (
            ("Host", "host_version"),
            ("Adapter", "adapter_version"),
            ("Source revision", "source_revision"),
            ("Session", "session_ref"),
            ("Model provider", "model_provider"),
            ("Model", "model"),
        ):
            if source.get(name):
                context.append(f"- {label}: {source[name]}")
    collections = (
        ("Provenance", delivery.get("provenance", [])),
        ("Artifacts", delivery.get("artifacts", [])),
        ("Work items", delivery.get("work_items", [])),
    )
    for title, items in collections:
        if not isinstance(items, list) or not items:
            continue
        context.extend(["", f"### {title}", ""])
        for item in items:
            if not isinstance(item, dict):
                continue
            primary = (
                item.get("provider") or item.get("artifact_id") or item.get("work_id") or "item"
            )
            attributes = [
                f"{name}={value}"
                for name in (
                    "basis",
                    "access",
                    "activity",
                    "media_type",
                    "retrieved_at",
                    "observed_at",
                    "expires_at",
                )
                if (value := item.get(name)) is not None
            ]
            locator = item.get("locator") or item.get("result_ref") or ""
            suffix = f" — {locator}" if locator else ""
            metadata = f" ({', '.join(attributes)})" if attributes else ""
            context.append(f"- {primary}{metadata}{suffix}")
    lines.extend(context)
    return "\n".join(lines) + "\n"


def _html_value(value: Any) -> str:
    if isinstance(value, list):
        if not value:
            return '<p class="empty">None</p>'
        items = []
        for item in value:
            if isinstance(item, dict):
                label = render_evidence(item)
                locator = str(item.get("locator", ""))
                access = str(item.get("access", ""))
                suffix = f" · {access}" if access else ""
                locator_html = html.escape(locator)
                if locator.startswith(("https://", "http://")):
                    safe_url = html.escape(locator, quote=True)
                    locator_html = (
                        f'<a href="{safe_url}" rel="noopener noreferrer">{locator_html}</a>'
                    )
                items.append(
                    f"<li><code>{html.escape(label)}</code>"
                    f'<span class="meta">{html.escape(suffix)}</span>'
                    f'<span class="locator">{locator_html}</span></li>'
                )
            else:
                items.append(f"<li>{html.escape(str(item))}</li>")
        return "<ul>" + "".join(items) + "</ul>"
    if value is None or str(value).strip() == "":
        return '<p class="empty">None</p>'
    return "".join(f"<p>{html.escape(line)}</p>" for line in str(value).splitlines())


def _html_context_value(name: str, value: Any) -> str:
    rendered = html.escape(str(value))
    if name == "locator" and str(value).startswith(("https://", "http://")):
        href = html.escape(str(value), quote=True)
        return f'<a href="{href}" rel="noopener noreferrer">{rendered}</a>'
    return rendered


def render_html(delivery: dict[str, Any]) -> str:
    brief = delivery["brief"]
    classification = delivery["classification"]
    explanation = delivery["explanation"]
    digest = canonical_sha256(delivery)
    title = str(brief.get("headline") or brief.get("outcome") or "Brief-Spec delivery")
    if brief.get("kind") == "outcome-brief":
        fields = ("status", "outcome", "human_action", "proof", "gaps", "next", "open")
        lead_fields = ("status", "outcome", "human_action")
    else:
        fields = _CHECKPOINT_FIELDS[str(brief["mode"])]
        lead_fields = (
            ("headline", "current_state") if brief.get("mode") == "orient" else ("headline",)
        )
    sections: list[str] = []
    for field in lead_fields:
        sections.append(
            f'<section class="decision-signal" aria-labelledby="field-{field}">'
            f'<h2 id="field-{field}">{html.escape(_FIELD_LABELS[field])}</h2>'
            f"{_html_value(brief.get(field))}</section>"
        )
    sections.append(
        '<section class="classification" aria-labelledby="classification">'
        '<h2 id="classification">Type and subject</h2><p>'
        f"{html.escape(str(classification['work_type']))} + "
        f"{html.escape(str(classification['subject']))} "
        f"({html.escape(str(classification['confidence']))}, "
        f"{html.escape(str(classification['origin']))})</p></section>"
    )
    sections.extend(
        f'<section class="typed" aria-labelledby="typed-{html.escape(str(item["id"]))}">'
        f'<h2 id="typed-{html.escape(str(item["id"]))}">'
        f"{html.escape(str(item['label']))}</h2>{_html_value(item['content'])}</section>"
        for item in explanation["sections"]
    )
    for field in fields:
        if field in lead_fields:
            continue
        label = (
            "Screen-only proof"
            if field == "proof" and brief.get("mode") == "spoken"
            else _FIELD_LABELS[field]
        )
        content = _html_value(brief.get(field))
        if field in {"proof", "gaps", "watch_outs"}:
            content = (
                f'<details open class="evidence-detail"><summary>Show or hide '
                f"{html.escape(label)}</summary>"
                f"{content}</details>"
            )
        sections.append(
            f'<section aria-labelledby="field-{field}"><h2 id="field-{field}">'
            f"{html.escape(label)}</h2>{content}</section>"
        )
    source = delivery.get("source", {})
    metadata = " · ".join(
        html.escape(str(value))
        for value in (
            source.get("harness"),
            source.get("host_version"),
            source.get("source_revision"),
            source.get("created_at"),
        )
        if value
    )
    canonical = html.escape(canonical_json_bytes(delivery).decode("utf-8"))
    context_sections = []
    for context_title, key in (
        ("Provenance", "provenance"),
        ("Artifacts", "artifacts"),
        ("Work items", "work_items"),
    ):
        items = delivery.get(key, [])
        if not isinstance(items, list) or not items:
            continue
        rows = []
        for item in items:
            if not isinstance(item, dict):
                continue
            rows.append(
                "<li>"
                + " · ".join(
                    f"<strong>{html.escape(str(name))}</strong> "
                    f"{_html_context_value(str(name), value)}"
                    for name, value in item.items()
                    if value is not None
                )
                + "</li>"
            )
        context_sections.append(
            f'<section aria-labelledby="context-{key}"><h2 id="context-{key}">'
            f"{context_title}</h2><ul>{''.join(rows)}</ul></section>"
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy"
 content="default-src 'none'; style-src 'unsafe-inline'; img-src data:;
 base-uri 'none'; form-action 'none'">
<meta name="brief-spec-sha256" content="{digest}">
<meta name="briefspec-sha256" content="{digest}">
<title>{html.escape(title)}</title>
<style>
:root{{--ink:#171717;--muted:#666;--paper:#f7f5ef;--line:#d7d2c6;--accent:#204f3b}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 ui-sans-serif,
-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:900px;margin:auto;padding:64px 28px 96px}}
header{{border-bottom:2px solid var(--ink);padding-bottom:24px;margin-bottom:36px}}
.eyebrow{{font:700 12px/1.2 ui-monospace,SFMono-Regular,monospace;letter-spacing:.12em;
text-transform:uppercase;color:var(--accent)}}
h1{{font:700 clamp(32px,6vw,64px)/1.02 ui-serif,Georgia,serif;margin:12px 0;
letter-spacing:-.03em}}
h2{{font-size:13px;letter-spacing:.08em;text-transform:uppercase;margin:0 0 10px}}
section{{padding:22px 0;border-bottom:1px solid var(--line)}}
p,ul{{margin:0}} ul{{padding-left:22px}} li+li{{margin-top:8px}}
code{{font:13px/1.5 ui-monospace,SFMono-Regular,monospace;white-space:normal}}
.meta,.locator{{display:block;color:var(--muted);font-size:12px}}
.empty{{color:var(--muted)}} details{{margin-top:40px}}
summary{{cursor:pointer;font-weight:700}}
pre{{overflow:auto;padding:16px;background:#fff;border:1px solid var(--line)}}
:focus-visible{{outline:3px solid var(--accent);outline-offset:3px}}
@media print{{body{{background:white}} main{{max-width:none;padding:0}}
details.integrity{{display:none}} details.evidence-detail summary{{display:none}}
section{{break-inside:avoid}}}}
</style></head><body><main data-brief-spec-sha256="{digest}" data-briefspec-sha256="{digest}">
<header><div class="eyebrow">Verified Brief-Spec delivery · \
{html.escape(str(classification["work_type"]))} / \
{html.escape(str(classification["subject"]))}</div>
<h1>{html.escape(title)}</h1><p>{metadata}</p></header>
{"".join(sections)}
<aside aria-label="Delivery provenance and work state">{"".join(context_sections)}</aside>
<details class="integrity"><summary>Canonical JSON and integrity hash</summary>
<p><code>sha256:{digest}</code></p><pre>{canonical}</pre></details>
</main></body></html>
"""


def render_spoken_text(delivery: dict[str, Any]) -> str:
    brief = delivery.get("brief", {})
    if brief.get("kind") != "session-checkpoint" or brief.get("mode") != "spoken":
        raise ValueError("spoken-text and ssml require a Spoken Checkpoint")
    script = str(brief.get("script") or "").strip()
    if not script:
        raise ValueError("Spoken Checkpoint has no Script")
    return script + "\n"


def render_ssml(delivery: dict[str, Any]) -> str:
    script = render_spoken_text(delivery).strip()
    paragraphs = "".join(f"<p>{html.escape(part.strip())}</p>" for part in script.split("\n\n"))
    return f"<speak>{paragraphs}</speak>\n"


def render_core(delivery: dict[str, Any], output_format: str) -> bytes:
    if output_format == "markdown":
        return render_markdown(delivery).encode("utf-8")
    if output_format == "json":
        return canonical_json_bytes(delivery)
    if output_format == "html":
        return render_html(delivery).encode("utf-8")
    if output_format == "spoken-text":
        return render_spoken_text(delivery).encode("utf-8")
    if output_format == "ssml":
        return render_ssml(delivery).encode("utf-8")
    raise ValueError(f"Unsupported core format: {output_format}")


CORE_FORMATS = {
    "markdown": ("brief.md", "text/markdown"),
    "json": ("brief.json", "application/json"),
    "html": ("brief.html", "text/html"),
    "spoken-text": ("spoken.txt", "text/plain"),
    "ssml": ("spoken.ssml", "application/ssml+xml"),
}


def export_core(
    delivery: dict[str, Any],
    formats: list[str],
    output_dir: Path,
    *,
    force: bool = False,
) -> list[dict[str, Any]]:
    unknown = [name for name in formats if name not in CORE_FORMATS]
    if unknown:
        raise ValueError(f"Unsupported core format(s): {', '.join(unknown)}")
    if len(formats) != len(set(formats)):
        raise ValueError("Duplicate output formats are not allowed")
    rendered = {name: render_core(delivery, name) for name in formats}
    paths = {name: output_dir / CORE_FORMATS[name][0] for name in formats}
    conflicts = [path for path in paths.values() if path.exists() and not force]
    if conflicts:
        raise FileExistsError(f"Refusing to overwrite existing output: {conflicts[0]}")
    atomic_write_many([(paths[name], rendered[name], 0o644) for name in formats])
    records: list[dict[str, Any]] = []
    for name in formats:
        path = paths[name]
        content = rendered[name]
        records.append(
            {
                "format": name,
                "path": str(path),
                "media_type": CORE_FORMATS[name][1],
                "size_bytes": len(content),
                "sha256": sha256_bytes(content),
                "renderer_version": CORE_RENDERER_VERSION,
            }
        )
    return records
