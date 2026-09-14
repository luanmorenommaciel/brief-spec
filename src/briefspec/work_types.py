from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from briefspec.models import (
    ClassificationConfidence,
    ClassificationOrigin,
    WorkType,
)

PROFILE_VERSION = "1.0"
MAX_CLASSIFICATION_CHARS = 64 * 1024
CLASSIFIER_ADAPTER_VERSION = "1.1"
MIN_INFERRED_MARGIN = 1


@dataclass(frozen=True, slots=True)
class TypeSection:
    section_id: str
    label: str


@dataclass(frozen=True, slots=True)
class TypeProfile:
    work_type: str
    description: str
    sections: tuple[TypeSection, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "work_type": self.work_type,
            "profile_version": PROFILE_VERSION,
            "description": self.description,
            "sections": [asdict(section) for section in self.sections],
        }


@dataclass(frozen=True, slots=True)
class Classification:
    work_type: str
    subject: str
    confidence: str
    origin: str
    classified_at: str
    profile_version: str = PROFILE_VERSION
    rule_ids: tuple[str, ...] = ()
    decision_id: str = ""
    input_sha256: str = ""
    record_sha256: str = ""
    adapter_version: str = CLASSIFIER_ADAPTER_VERSION

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["rule_ids"] = list(self.rule_ids)
        return {key: item for key, item in value.items() if item != ""}


def _sections(*values: tuple[str, str]) -> tuple[TypeSection, ...]:
    return tuple(TypeSection(section_id, label) for section_id, label in values)


PROFILES: dict[str, TypeProfile] = {
    WorkType.GENERAL.value: TypeProfile(
        WorkType.GENERAL.value,
        "Give a direct answer, the reasoning needed to trust it, and the next useful move.",
        _sections(("answer", "Answer"), ("rationale", "Rationale"), ("next_action", "Next action")),
    ),
    WorkType.EXPLORATION.value: TypeProfile(
        WorkType.EXPLORATION.value,
        "Map an unfamiliar system without presenting investigation as implementation.",
        _sections(
            ("question", "Question"),
            ("system_map", "System map"),
            ("entry_points", "Entry points"),
            ("flow", "Flow"),
            ("unknowns", "Unknowns"),
            ("next_probe", "Next probe"),
        ),
    ),
    WorkType.REVIEW.value: TypeProfile(
        WorkType.REVIEW.value,
        "Lead with the verdict, then findings, risk, observed validation, and recommendation.",
        _sections(
            ("scope", "Scope"),
            ("verdict", "Verdict"),
            ("findings", "Findings"),
            ("risk", "Risk"),
            ("validation", "Validation"),
            ("recommendation", "Recommendation"),
        ),
    ),
    WorkType.IMPLEMENTATION.value: TypeProfile(
        WorkType.IMPLEMENTATION.value,
        "Explain what was intended, what changed, resulting behavior, and verification.",
        _sections(
            ("intent", "Intent"),
            ("changes", "Changes"),
            ("resulting_behavior", "Resulting behavior"),
            ("verification", "Verification"),
            ("tradeoffs", "Tradeoffs"),
        ),
    ),
    WorkType.DEBUGGING.value: TypeProfile(
        WorkType.DEBUGGING.value,
        "Separate the observed symptom from the proven cause, fix, and residual risk.",
        _sections(
            ("symptom", "Symptom"),
            ("root_cause", "Root cause"),
            ("fix", "Fix"),
            ("regression_protection", "Regression protection"),
            ("residual_risk", "Residual risk"),
        ),
    ),
    WorkType.PLANNING.value: TypeProfile(
        WorkType.PLANNING.value,
        "Turn intent into a decision-complete sequence with explicit release gates.",
        _sections(
            ("goal", "Goal"),
            ("decisions", "Decisions"),
            ("approach", "Approach"),
            ("sequence", "Sequence"),
            ("gates", "Gates"),
        ),
    ),
    WorkType.RESEARCH.value: TypeProfile(
        WorkType.RESEARCH.value,
        "Distinguish synthesis, evidence quality, current limits, and recommendation.",
        _sections(
            ("question", "Question"),
            ("synthesis", "Synthesis"),
            ("evidence_quality", "Evidence quality"),
            ("limitations", "Limitations"),
            ("recommendation", "Recommendation"),
        ),
    ),
    WorkType.OPERATIONS.value: TypeProfile(
        WorkType.OPERATIONS.value,
        "Make impact, current state, actions, recovery, and follow-up quickly scannable.",
        _sections(
            ("event", "Event"),
            ("impact", "Impact"),
            ("current_state", "Current state"),
            ("actions", "Actions"),
            ("recovery", "Recovery"),
            ("follow_up", "Follow-up"),
        ),
    ),
}

SUBJECTS = (
    "pull-request",
    "codebase",
    "change-set",
    "issue",
    "bug",
    "feature",
    "refactor",
    "test",
    "release",
    "architecture",
    "document",
    "data",
    "incident",
    "dependency",
    "security",
    "general",
)

_TYPE_RULES: dict[str, tuple[tuple[str, str], ...]] = {
    WorkType.DEBUGGING.value: (
        ("debug.explicit", r"\b(?:debug|diagnos(?:e|is)|root cause|stack trace|traceback)\b"),
        ("debug.failure", r"\b(?:failing|failure|broken|crash(?:es|ed)?|exception|error)\b"),
        ("debug.why", r"\bwhy (?:is|does|did|won't|doesn't|isn't)\b"),
    ),
    WorkType.REVIEW.value: (
        ("review.explicit", r"\b(?:review|audit|critique|inspect)\b"),
        ("review.pr", r"\b(?:pull request|merge request|code review|pr\s*#?\d+)\b"),
        ("review.diff", r"\b(?:diff|change set|changeset)\b.*\b(?:risk|quality|correct|issue)\b"),
    ),
    WorkType.OPERATIONS.value: (
        ("operations.incident", r"\b(?:incident|outage|degradation|on-call|sev[0-9])\b"),
        ("operations.release", r"\b(?:deploy|deployment|rollback|rollout|release|publish)\b"),
        ("operations.observe", r"\b(?:monitor|alert|recovery|restore|production)\b"),
    ),
    WorkType.RESEARCH.value: (
        ("research.explicit", r"\b(?:research|investigate the market|literature review)\b"),
        ("research.current", r"\b(?:latest|current market|recent changes|state of the art)\b"),
        (
            "research.compare",
            r"\b(?:compare|evaluate|benchmark|recommend)\b.*"
            r"\b(?:tools?|products?|vendors?|models?)\b",
        ),
        ("research.web", r"\b(?:browse|search the web|look up|sources?|exa|tavily|firecrawl)\b"),
    ),
    WorkType.PLANNING.value: (
        ("planning.explicit", r"\b(?:plan|roadmap|strategy|proposal|implementation plan)\b"),
        ("planning.design", r"\b(?:design|architect|architecture|specification|spec)\b"),
        ("planning.sequence", r"\b(?:milestones?|phases?|acceptance criteria|release gates?)\b"),
    ),
    WorkType.EXPLORATION.value: (
        ("exploration.explicit", r"\b(?:explore|map|trace|orient|understand)\b"),
        (
            "exploration.codebase",
            r"\b(?:codebase|repository|repo)\b.*"
            r"\b(?:works?|structured|flow|entry point)\b",
        ),
        ("exploration.where", r"\b(?:where is|how does|walk me through|explain how)\b"),
    ),
    WorkType.IMPLEMENTATION.value: (
        ("implementation.explicit", r"\b(?:implement|build|create|write|add|remove|refactor)\b"),
        ("implementation.change", r"\b(?:change|update|modify|patch|migrate|configure|install)\b"),
        ("implementation.fix", r"\bfix\b"),
        ("implementation.test", r"\b(?:add|write|implement)\b.*\btests?\b"),
    ),
}

_SUBJECT_RULES: tuple[tuple[str, str, str], ...] = (
    ("pull-request", "subject.pull-request", r"\b(?:pull request|merge request|pr\s*#?\d+)\b"),
    ("codebase", "subject.codebase", r"\b(?:codebase|repository|repo)\b"),
    ("change-set", "subject.change-set", r"\b(?:diff|change set|changeset)\b"),
    ("incident", "subject.incident", r"\b(?:incident|outage|sev[0-9]|degradation)\b"),
    ("security", "subject.security", r"\b(?:security|vulnerability|cve|threat)\b"),
    ("dependency", "subject.dependency", r"\b(?:dependency|dependencies|package upgrade)\b"),
    ("architecture", "subject.architecture", r"\b(?:architecture|architectural|system design)\b"),
    ("release", "subject.release", r"\b(?:release|deploy|deployment|publish|rollout)\b"),
    ("refactor", "subject.refactor", r"\brefactor(?:ing)?\b"),
    ("test", "subject.test", r"\btests?|testing|pytest|unit test|integration test\b"),
    ("document", "subject.document", r"\b(?:document|documentation|readme|guide|pdf)\b"),
    ("data", "subject.data", r"\b(?:data|database|schema|table|dataset|sql)\b"),
    ("bug", "subject.bug", r"\b(?:bug|defect|broken|failure|error)\b"),
    ("feature", "subject.feature", r"\b(?:feature|capability)\b"),
    ("issue", "subject.issue", r"\bissue\s*#?\d+\b"),
)

_EXPLICIT_TYPE = re.compile(
    r"\b(?:brief-spec\s+)?(?:work\s+)?type\s*[:=]?\s*"
    r"(general|exploration|review|implementation|debugging|planning|research|operations)\b",
    re.IGNORECASE,
)
_PIVOT = re.compile(
    r"\b(?:new task|switch(?:ing)? to|instead(?:,|\s)|different task|now (?:please )?(?:review|"
    r"explore|implement|debug|plan|research|deploy))\b",
    re.IGNORECASE,
)
_NEGATED_SPAN = re.compile(
    r"\b(?:do\s+not|don't|never|avoid|without|must\s+not|should\s+not|"
    r"shouldn't|cannot|can't)\b"
    r".*?(?=(?:[.;!?\n]|\b(?:but|however|instead)\b|$))",
    re.IGNORECASE | re.DOTALL,
)
_QUOTED_SPAN = re.compile(r"(?P<quote>['\"`])(?P<body>.*?)(?P=quote)", re.DOTALL)
_BRAND_SPAN = re.compile(r"\bbrief-?spec\b", re.IGNORECASE)


def normalize_subject(value: str | None) -> str:
    if not value:
        return "general"
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not normalized or len(normalized) > 64:
        raise ValueError("Subject must normalize to a non-empty slug of at most 64 characters")
    return normalized


def _timestamp(now: datetime | None) -> str:
    if now is None and (epoch := os.environ.get("SOURCE_DATE_EPOCH")):
        now = datetime.fromtimestamp(int(epoch), tz=UTC)
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(UTC).isoformat().replace("+00:00", "Z")


def is_clear_pivot(text: str) -> bool:
    return bool(_PIVOT.search(_affirmative_text(text[:MAX_CLASSIFICATION_CHARS])))


def explicit_type_requested(text: str) -> bool:
    return bool(_EXPLICIT_TYPE.search(_affirmative_text(text[:MAX_CLASSIFICATION_CHARS])))


def is_substantive(text: str) -> bool:
    value = text[:MAX_CLASSIFICATION_CHARS].strip()
    if not value:
        return False
    if re.search(r"\bbrief-spec\b", value, re.IGNORECASE):
        return True
    if len(value.split()) < 4:
        return False
    return any(
        re.search(pattern, _affirmative_text(value), re.IGNORECASE)
        for rules in _TYPE_RULES.values()
        for _, pattern in rules
    )


def _affirmative_text(text: str) -> str:
    """Mask bounded prohibitions and quoted prohibition examples before rule matching."""

    def mask(match: re.Match[str]) -> str:
        return " " * len(match.group(0))

    value = _BRAND_SPAN.sub(mask, text)
    value = _QUOTED_SPAN.sub(lambda match: " " * len(match.group(0)), value)
    return _NEGATED_SPAN.sub(mask, value)


def _finalize_classification(
    *,
    work_type: str,
    subject: str,
    confidence: str,
    origin: str,
    classified_at: str,
    rule_ids: tuple[str, ...],
    bounded: str,
) -> Classification:
    input_sha256 = hashlib.sha256(bounded.encode("utf-8")).hexdigest()
    record = {
        "adapter_version": CLASSIFIER_ADAPTER_VERSION,
        "classified_at": classified_at,
        "confidence": confidence,
        "input_sha256": input_sha256,
        "origin": origin,
        "profile_version": PROFILE_VERSION,
        "rule_ids": list(rule_ids),
        "subject": subject,
        "work_type": work_type,
    }
    record_sha256 = hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return Classification(
        work_type=work_type,
        subject=subject,
        confidence=confidence,
        origin=origin,
        classified_at=classified_at,
        rule_ids=rule_ids,
        decision_id=f"bsd-{record_sha256[:24]}",
        input_sha256=input_sha256,
        record_sha256=record_sha256,
    )


def classify_task(
    text: str,
    *,
    explicit_type: str | None = None,
    subject: str | None = None,
    host_context: dict[str, Any] | None = None,
    default_type: str = WorkType.GENERAL.value,
    now: datetime | None = None,
) -> Classification:
    bounded = text[:MAX_CLASSIFICATION_CHARS]
    affirmative = _affirmative_text(bounded)
    host_context = host_context or {}
    explicit_match = _EXPLICIT_TYPE.search(affirmative)
    requested_type = explicit_type or (explicit_match.group(1) if explicit_match else None)
    host_subject_hint: str | None = None
    if requested_type:
        work_type = WorkType(requested_type.lower()).value
        origin = ClassificationOrigin.EXPLICIT.value
        confidence = ClassificationConfidence.HIGH.value
        rule_ids = ("explicit.work-type",)
    else:
        host_type = str(host_context.get("work_type") or "").lower()
        if host_type not in PROFILES and any(
            host_context.get(key)
            for key in ("pull_request", "pull_request_url", "pr_number", "review_command")
        ):
            host_type = WorkType.REVIEW.value
            host_subject_hint = "pull-request"
        if host_type in PROFILES:
            work_type = host_type
            origin = ClassificationOrigin.HOST.value
            confidence = ClassificationConfidence.HIGH.value
            rule_ids = ("host.work-type",)
        else:
            matches: dict[str, list[str]] = {}
            for candidate, rules in _TYPE_RULES.items():
                found = [
                    rule_id for rule_id, pattern in rules if re.search(pattern, affirmative, re.I)
                ]
                if found:
                    matches[candidate] = found
            if not matches:
                work_type = WorkType(default_type).value
                origin = ClassificationOrigin.FALLBACK.value
                confidence = ClassificationConfidence.LOW.value
                rule_ids = ("fallback.general",)
            else:
                top_score = max(len(found) for found in matches.values())
                winners = [
                    candidate for candidate, found in matches.items() if len(found) == top_score
                ]
                runner_up = max(
                    (
                        len(found)
                        for candidate, found in matches.items()
                        if candidate not in winners
                    ),
                    default=0,
                )
                if len(winners) != 1 or top_score - runner_up < MIN_INFERRED_MARGIN:
                    work_type = WorkType(default_type).value
                    origin = ClassificationOrigin.FALLBACK.value
                    confidence = ClassificationConfidence.LOW.value
                    rule_ids = tuple(
                        sorted(rule for candidate in winners for rule in matches[candidate])
                    )
                else:
                    work_type = winners[0]
                    origin = ClassificationOrigin.INFERRED.value
                    confidence = ClassificationConfidence.MEDIUM.value
                    rule_ids = tuple(matches[work_type])

    host_subject = str(host_context.get("subject") or "") or host_subject_hint
    resolved_subject = subject or host_subject
    subject_rule: str | None = None
    if resolved_subject is None and origin != ClassificationOrigin.FALLBACK.value:
        for candidate, rule_id, pattern in _SUBJECT_RULES:
            if re.search(pattern, affirmative, re.IGNORECASE):
                resolved_subject = candidate
                subject_rule = rule_id
                break
    normalized_subject = normalize_subject(resolved_subject)
    if subject_rule:
        rule_ids = (*rule_ids, subject_rule)
    elif subject or host_subject:
        rule_ids = (*rule_ids, "explicit.subject" if subject else "host.subject")

    deduplicated_rules = tuple(dict.fromkeys(rule_ids))
    classified_at = _timestamp(now)
    return _finalize_classification(
        work_type=work_type,
        subject=normalized_subject,
        confidence=confidence,
        origin=origin,
        classified_at=classified_at,
        rule_ids=deduplicated_rules,
        bounded=bounded,
    )


def type_profile(work_type: str) -> TypeProfile:
    try:
        return PROFILES[WorkType(work_type).value]
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Unknown Brief-Spec work type: {work_type}") from exc


def types_document() -> dict[str, Any]:
    return {
        "profile_version": PROFILE_VERSION,
        "types": [PROFILES[item.value].to_dict() for item in WorkType],
        "subjects": list(SUBJECTS),
        "custom_primary_types": False,
    }


def validate_explanation(
    classification: dict[str, Any], explanation: dict[str, Any]
) -> tuple[str, ...]:
    errors: list[str] = []
    try:
        profile = type_profile(str(classification.get("work_type", "")))
    except ValueError as exc:
        return (str(exc),)
    if explanation.get("profile_version") != PROFILE_VERSION:
        errors.append(f"Explanation profile_version must be {PROFILE_VERSION}")
    sections = explanation.get("sections")
    if not isinstance(sections, list):
        return (*errors, "Explanation sections must be an array")
    expected = [section.section_id for section in profile.sections]
    observed: list[str] = []
    for index, section in enumerate(sections):
        if not isinstance(section, dict):
            errors.append(f"explanation.sections[{index}] must be an object")
            continue
        section_id = str(section.get("id", ""))
        observed.append(section_id)
        if not str(section.get("label", "")).strip():
            errors.append(f"explanation.sections[{index}].label is required")
        if not str(section.get("content", "")).strip():
            errors.append(f"explanation.sections[{index}].content is required")
    if observed != expected:
        errors.append("Explanation sections do not match the selected type profile order")
    return tuple(errors)
