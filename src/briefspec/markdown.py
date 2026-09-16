from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from briefspec.models import CheckpointMode, OutcomeStatus, ValidationResult
from briefspec.work_types import PROFILE_VERSION, type_profile, validate_explanation

OUTCOME_START = "<!-- briefspec:outcome:v1 -->"
CHECKPOINT_PATTERN = re.compile(
    r"<!--\s*briefspec:checkpoint:v1\s+mode=(orient|teach|spoken)\s*-->"
)
END_MARKER = "<!-- /briefspec -->"
TYPED_PATTERN = re.compile(
    r"<!--\s*brief-spec:typed:v1\s+"
    r"type=(?P<work_type>[a-z-]+)\s+"
    r"subject=(?P<subject>[a-z0-9-]+)\s+"
    r"confidence=(?P<confidence>high|medium|low)\s+"
    r"origin=(?P<origin>explicit|host|inferred|fallback|reported)\s+"
    r"classified_at=(?P<classified_at>\S+)\s+"
    r"profile=(?P<profile>\d+\.\d+)"
    r"(?:\s+decision_id=(?P<decision_id>[a-z0-9-]+))?\s*-->"
)
TYPED_END_MARKER = "<!-- /brief-spec -->"
_EVIDENCE_TAG = re.compile(
    r"^\[(?:direct|derived|reported)/(?:pass|fail|info)(?:\s+[^\]]+)?\]\s+",
    re.IGNORECASE,
)
_EVIDENCE_LOCATOR = re.compile(
    r"`[^`\n]+`"
    r"|\[[^\]\n]+\]\([^)]+\)"
    r"|https?://\S+"
    r"|\b(?:PR|issue)\s*#?\d+\b"
    r"|\b[0-9a-f]{7,40}\b",
    re.IGNORECASE,
)

_OUTCOME_SECTION_ORDER = (
    "Status",
    "Outcome",
    "Human action",
    "Proof",
    "Gaps",
    "Next",
    "Open",
)
# A DONE brief with nothing left for the human may omit these fields. They are read as the
# same explicit None values the full form carries, so the canonical object is identical.
_COMPACT_OPTIONAL_FIELDS = ("Human action", "Gaps", "Next", "Open")
_MISSING_FIELDS_PREFIX = "Missing required field(s)"

_CHECKPOINT_SECTIONS = {
    CheckpointMode.ORIENT: (
        "Headline",
        "Current state",
        "Completed",
        "Decisions",
        "Proof",
        "Next",
        "Open",
    ),
    CheckpointMode.TEACH: (
        "Headline",
        "Mental model",
        "Why it matters",
        "What changed",
        "Example",
        "Watch-outs",
        "Next",
        "Proof",
    ),
    CheckpointMode.SPOKEN: (
        "Headline",
        "Script",
        "Screen-only proof",
        "Next",
    ),
}


def _bounded_region(text: str, start: str | re.Pattern[str]) -> tuple[str | None, str | None]:
    if isinstance(start, str):
        index = text.find(start)
        marker = start
        mode = None
    else:
        match = start.search(text)
        if not match:
            return None, None
        index = match.start()
        marker = match.group(0)
        mode = match.group(1)
    if index < 0:
        return None, mode
    end = text.find(END_MARKER, index + len(marker))
    if end < 0:
        return None, mode
    return text[index + len(marker) : end].strip(), mode


def _read_sections(region: str, names: Iterable[str]) -> tuple[dict[str, Any], list[str]]:
    lines = region.splitlines()
    names = tuple(names)
    positions: list[tuple[int, str, str]] = []
    for index, line in enumerate(lines):
        stripped = line.strip().removeprefix("**").removesuffix("**").strip()
        for name in names:
            match = re.match(rf"^{re.escape(name)}\s*:\s*(.*)$", stripped, re.IGNORECASE)
            if match:
                positions.append((index, name, match.group(1).strip()))
                break

    errors: list[str] = []
    found_names = [name for _, name, _ in positions]
    missing = [name for name in names if name not in found_names]
    if missing:
        errors.append(f"Missing required field(s): {', '.join(missing)}")

    found_order = [name for name in found_names if name in names]
    expected_order = [name for name in names if name in found_order]
    if found_order != expected_order:
        errors.append("Fields are not in the required order")

    data: dict[str, Any] = {}
    for item_index, (line_index, name, inline) in enumerate(positions):
        next_index = positions[item_index + 1][0] if item_index + 1 < len(positions) else len(lines)
        body_lines = lines[line_index + 1 : next_index]
        if inline:
            data[name] = inline
            continue
        items = [
            line.strip()[2:].strip() for line in body_lines if line.strip().startswith(("- ", "* "))
        ]
        if items:
            data[name] = items
        else:
            data[name] = "\n".join(line.strip() for line in body_lines).strip()
    return data, errors


def _empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, list):
        return not value or all(_empty(item) for item in value)
    return str(value).strip().lower() in {"", "none", "n/a", "not applicable"}


def _evidence_items(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if not _empty(item))
    if _empty(value):
        return ()
    return (str(value).strip(),)


def _validate_evidence(
    value: Any,
    field_name: str,
    errors: list[str],
    warnings: list[str],
) -> None:
    for index, item in enumerate(_evidence_items(value), start=1):
        if not _EVIDENCE_LOCATOR.search(item):
            errors.append(
                f"{field_name} item {index} must contain an inspectable locator "
                "(for example a path, command, URL, issue, PR, or commit)"
            )
        if not _EVIDENCE_TAG.match(item):
            warnings.append(
                f"{field_name} item {index} should start with "
                "[direct|derived|reported]/[pass|fail|info]"
            )


def validate_outcome(text: str) -> ValidationResult:
    region, _ = _bounded_region(text, OUTCOME_START)
    if region is None:
        return ValidationResult(False, "outcome-brief", ("Missing bounded outcome marker",))
    data, errors = _read_sections(region, _OUTCOME_SECTION_ORDER)
    missing = [name for name in _OUTCOME_SECTION_ORDER if name not in data]
    compact = (
        bool(missing)
        and set(missing) <= set(_COMPACT_OPTIONAL_FIELDS)
        and str(data.get("Status", "")).strip().upper() == OutcomeStatus.DONE.value
    )
    if compact:
        errors = [error for error in errors if not error.startswith(_MISSING_FIELDS_PREFIX)]
        for name in missing:
            data[name] = "None" if name == "Human action" else ["None"]
        data["Form"] = "compact"

    try:
        status = OutcomeStatus(str(data.get("Status", "")).strip().upper())
        data["Status"] = status.value
    except ValueError:
        status = None
        errors.append("Status must be DONE, REVIEW, DECIDE, BLOCKED, or FAILED")

    if _empty(data.get("Outcome")):
        errors.append("Outcome must state what is now true")
    proof = data.get("Proof")
    if _empty(proof):
        errors.append("Proof must contain at least one inspectable reference")

    warnings: list[str] = []
    _validate_evidence(proof, "Proof", errors, warnings)

    human_action = data.get("Human action")
    gaps = data.get("Gaps")
    next_items = data.get("Next")
    open_items = data.get("Open")

    if status is OutcomeStatus.DONE:
        if not _empty(human_action):
            errors.append("DONE cannot require human action")
        if not _empty(gaps):
            errors.append("DONE cannot contain unresolved gaps")
    if status in {OutcomeStatus.REVIEW, OutcomeStatus.DECIDE, OutcomeStatus.BLOCKED} and _empty(
        human_action
    ):
        errors.append(f"{status.value} requires explicit human action")
    if status in {OutcomeStatus.BLOCKED, OutcomeStatus.FAILED}:
        if _empty(gaps):
            errors.append(f"{status.value} requires an explicit gap")
        if _empty(next_items):
            errors.append(f"{status.value} requires a next action")
    if status is OutcomeStatus.DECIDE and _empty(open_items):
        errors.append("DECIDE requires an open decision")

    for name, limit in (("Proof", 5), ("Next", 3), ("Open", 3)):
        value = data.get(name)
        if isinstance(value, list) and len(value) > limit:
            errors.append(f"{name} supports at most {limit} items")

    outcome = str(data.get("Outcome", ""))
    if len(outcome) > 500:
        warnings.append("Outcome is longer than 500 characters")
    return ValidationResult(not errors, "outcome-brief", tuple(errors), tuple(warnings), data)


def validate_checkpoint(text: str, expected_mode: CheckpointMode | None = None) -> ValidationResult:
    region, raw_mode = _bounded_region(text, CHECKPOINT_PATTERN)
    if region is None or raw_mode is None:
        return ValidationResult(False, "session-checkpoint", ("Missing bounded checkpoint marker",))
    mode = CheckpointMode(raw_mode)
    errors: list[str] = []
    if expected_mode is not None and mode is not expected_mode:
        errors.append(f"Expected {expected_mode.value} mode, found {mode.value}")

    data, section_errors = _read_sections(region, _CHECKPOINT_SECTIONS[mode])
    errors.extend(section_errors)
    data["Mode"] = mode.value

    if _empty(data.get("Headline")):
        errors.append("Headline must orient the reader")
    if _empty(data.get("Next")):
        errors.append("Next must identify the next useful move")
    proof_field = "Proof" if mode is not CheckpointMode.SPOKEN else "Screen-only proof"
    proof = data.get(proof_field)
    if _empty(proof):
        errors.append("Checkpoint must retain at least one evidence reference")

    warnings: list[str] = []
    _validate_evidence(proof, proof_field, errors, warnings)
    if mode is CheckpointMode.SPOKEN:
        script = str(data.get("Script", ""))
        words = len(script.split())
        if words < 80:
            warnings.append("Spoken script is shorter than 80 words")
        if words > 240:
            errors.append("Spoken script must not exceed 240 words")
        if "|" in script or "```" in script:
            errors.append("Spoken script cannot contain tables or code fences")
    return ValidationResult(not errors, "session-checkpoint", tuple(errors), tuple(warnings), data)


def detect_kind(text: str) -> str | None:
    if OUTCOME_START in text:
        return "outcome"
    if CHECKPOINT_PATTERN.search(text):
        return "checkpoint"
    return None


def parse_typed(text: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Parse the optional type-aware wrapper without changing the legacy inner contract."""
    match = TYPED_PATTERN.search(text)
    if match is None:
        return None
    end = text.find(TYPED_END_MARKER, match.end())
    if end < 0:
        raise ValueError("Missing Brief-Spec typed end marker")
    region = text[match.end() : end]
    checkpoint = CHECKPOINT_PATTERN.search(region)
    inner_indexes = [
        index
        for index in (
            region.find(OUTCOME_START),
            checkpoint.start() if checkpoint else -1,
        )
        if index >= 0
    ]
    if not inner_indexes:
        raise ValueError("Typed wrapper must contain a legacy-compatible brief")
    inner_start = min(inner_indexes)
    inner_end = region.find(END_MARKER, inner_start)
    if inner_end < 0:
        raise ValueError("Typed wrapper contains an incomplete legacy brief")
    explanation_text = (region[:inner_start] + "\n" + region[inner_end + len(END_MARKER) :]).strip()
    profile = type_profile(match.group("work_type"))
    heading = re.compile(r"^###\s+(.+?)\s*$", re.MULTILINE)
    headings = list(heading.finditer(explanation_text))
    sections: list[dict[str, str]] = []
    for index, found in enumerate(headings):
        content_start = found.end()
        content_end = (
            headings[index + 1].start() if index + 1 < len(headings) else len(explanation_text)
        )
        label = found.group(1).strip()
        section_id = next(
            (item.section_id for item in profile.sections if item.label == label),
            label.lower().replace(" ", "-"),
        )
        sections.append(
            {
                "id": section_id,
                "label": label,
                "content": explanation_text[content_start:content_end].strip(),
            }
        )
    classification = {
        "work_type": match.group("work_type"),
        "subject": match.group("subject"),
        # A model-written marker is a report, not an authoritative classifier decision.
        "confidence": "low",
        "origin": "reported",
        "classified_at": match.group("classified_at"),
        "profile_version": match.group("profile"),
        "rule_ids": ["reported.typed-marker"],
    }
    if match.group("decision_id"):
        classification["decision_id"] = match.group("decision_id")
    explanation = {"profile_version": match.group("profile"), "sections": sections}
    errors = validate_explanation(classification, explanation)
    if errors:
        raise ValueError("; ".join(errors))
    if match.group("profile") != PROFILE_VERSION:
        raise ValueError(f"Typed profile must be {PROFILE_VERSION}")
    return classification, explanation


def extract_bounded(text: str) -> str:
    """Return exactly one complete Brief-Spec region, excluding surrounding host text."""
    typed = TYPED_PATTERN.search(text)
    if typed is not None:
        end = text.find(TYPED_END_MARKER, typed.end())
        if end < 0:
            raise ValueError("Missing Brief-Spec typed end marker")
        return text[typed.start() : end + len(TYPED_END_MARKER)].strip() + "\n"
    kind = detect_kind(text)
    if kind == "outcome":
        start = text.find(OUTCOME_START)
        marker = OUTCOME_START
    elif kind == "checkpoint":
        match = CHECKPOINT_PATTERN.search(text)
        if match is None:  # pragma: no cover - guarded by detect_kind
            raise ValueError("Missing bounded checkpoint marker")
        start = match.start()
        marker = match.group(0)
    else:
        raise ValueError("No Brief-Spec marker found")
    end = text.find(END_MARKER, start + len(marker))
    if end < 0:
        raise ValueError("Missing Brief-Spec end marker")
    return text[start : end + len(END_MARKER)].strip() + "\n"
