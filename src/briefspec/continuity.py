from __future__ import annotations

import re
from typing import Any

METHOD_CONTEXTS: dict[str, dict[str, Any]] = {
    "general": {
        "label": "General",
        "purpose": "Explain the current work without claiming a governing method.",
        "sections": ("plain-language", "current-state", "next-action"),
    },
    "seamwise": {
        "label": "Seamwise",
        "purpose": "Explain delivery intent, seams, capability legs, and task ordering.",
        "sections": ("intent", "system-map", "current-phase", "changes", "next-action"),
    },
    "task-spec": {
        "label": "Task-Spec",
        "purpose": "Explain the bounded task, constraints, acceptance, and remaining action.",
        "sections": ("intent", "task-contract", "current-phase", "evidence", "acceptance"),
    },
    "converge": {
        "label": "Converge",
        "purpose": "Explain authorization, execution, evidence, settlement, and intervention.",
        "sections": ("intent", "authorization", "execution", "evidence", "settlement"),
    },
}

_METHOD_PATTERNS = {
    "seamwise": re.compile(r"(?i)\bseamwise\b"),
    "task-spec": re.compile(r"(?i)\b(?:task[ -]spec|taskspec|taskmesh)\b"),
    # "converge" is also an ordinary English verb ("the results converge"). Only a product
    # reference counts: the capitalized name inside a sentence, a CLI-style use, or a
    # Converge-only term.
    "converge": re.compile(
        r"(?<![.!?]\s)(?<![.!?]\n)(?<!^)(?<=\S\s)\bConverge\b"
        r"|(?i:\bconverge\s+(?:run|settle|settled|settlement|dock|factory|compose|register|"
        r"loop|status|mission|cli|repo|project)\b)"
        r"|(?i:\bconverge[/.]\w)"
        r"|\b(?:LOCAL_SETTLED|SETTLED loop)\b"
    ),
}


def method_context(name: str | None, *, phase: str | None = None) -> dict[str, Any]:
    normalized = (name or "general").strip().lower().replace("_", "-")
    if normalized not in METHOD_CONTEXTS:
        raise ValueError(f"Unknown method context: {normalized}")
    if phase is not None and not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", phase):
        raise ValueError("Method phase must be a normalized slug")
    profile = METHOD_CONTEXTS[normalized]
    return {
        "method": normalized,
        "phase": phase,
        "label": profile["label"],
        "purpose": profile["purpose"],
        "sections": list(profile["sections"]),
    }


def human_frame_delivery_tier(*, final_output: bool, lifecycle_hooks: bool) -> str:
    if final_output and lifecycle_hooks:
        return "pre-final-context"
    if final_output:
        return "companion-annotation"
    return "terminal-only"


def detect_method_context(
    text: str,
    *,
    host_context: dict[str, Any] | None = None,
) -> tuple[str, str | None, str]:
    """Select bounded method metadata without retaining the task text."""
    supplied = host_context or {}
    explicit = supplied.get("method_context")
    phase = supplied.get("method_phase")
    if isinstance(explicit, str):
        normalized = explicit.strip().lower().replace("_", "-")
        if normalized in METHOD_CONTEXTS:
            normalized_phase = None
            if isinstance(phase, str) and phase:
                candidate = phase.strip().lower().replace("_", "-").replace(" ", "-")
                if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", candidate):
                    normalized_phase = candidate
            value = method_context(
                normalized,
                phase=normalized_phase,
            )
            return value["method"], value["phase"], "host"
    matched = [name for name, pattern in _METHOD_PATTERNS.items() if pattern.search(text[:32_000])]
    if len(matched) == 1:
        return matched[0], None, "inferred"
    return "general", None, "fallback"
