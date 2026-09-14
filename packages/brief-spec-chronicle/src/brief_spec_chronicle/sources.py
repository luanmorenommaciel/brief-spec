from __future__ import annotations

from typing import Any

from briefspec.events import EVENT_KINDS

_EVIDENCE_ACCESS_ORDER = {"public": 0, "local": 1, "private": 2}


def _text(value: Any, *, fallback: str, limit: int = 500) -> str:
    candidate = str(value or "").strip()
    return (candidate or fallback)[:limit]


def _list(value: Any, *, limit: int = 16) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item)[:500] for item in value if str(item).strip()][:limit]


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _slug(value: Any, *, fallback: str) -> str:
    raw = str(value or fallback).strip().lower().replace("_", "-").replace(" ", "-")
    normalized = "".join(character for character in raw if character.isalnum() or character == "-")
    normalized = "-".join(part for part in normalized.split("-") if part)
    return normalized[:64] or fallback


def _evidence_locator(value: dict[str, Any]) -> str | None:
    locator = str(value.get("locator") or value.get("url") or "").strip()
    if not locator:
        return None
    kind = str(value.get("kind") or "").lower()
    if locator.startswith(("https://", "http://", "file:", "commit:")):
        return locator[:2048]
    if kind in {"file", "artifact"}:
        suffix = f"#sha256={value['sha256']}" if value.get("sha256") else ""
        return f"file:{locator}{suffix}"[:2048]
    if kind == "commit":
        return f"commit:{locator}"[:2048]
    return f"evidence:{locator}"[:2048]


def _delivery(value: dict[str, Any]) -> dict[str, Any]:
    brief = value.get("brief") if isinstance(value.get("brief"), dict) else {}
    source = value.get("source") if isinstance(value.get("source"), dict) else {}
    status = str(brief.get("status") or "").upper()
    kind = {
        "DONE": "TASK_COMPLETED",
        "REVIEW": "TASK_COMPLETED",
        "DECIDE": "DECISION_REQUESTED",
        "BLOCKED": "BLOCKER_RAISED",
        "FAILED": "TASK_FAILED",
    }.get(status, "HUMAN_NOTE")
    headline = _text(
        brief.get("outcome") or brief.get("headline"),
        fallback="Brief-Spec lifecycle boundary observed",
    )
    evidence = []
    evidence_details = []
    for item in [
        *_dicts(brief.get("proof")),
        *_dicts(value.get("provenance")),
        *_dicts(value.get("artifacts")),
    ]:
        locator = _evidence_locator(item)
        if not locator or (locator not in evidence and len(evidence) == 64):
            continue
        if locator not in evidence:
            evidence.append(locator)
        access = str(item.get("access") or "local")
        if access not in _EVIDENCE_ACCESS_ORDER:
            raise ValueError("Evidence access is invalid")
        evidence_details.append(
            {
                "evidence_id": locator,
                "access": access,
                "content_sha256": item.get("sha256") or item.get("content_sha256"),
                "expires_at": item.get("expires_at"),
                **{key: item[key] for key in ("basis", "result") if key in item},
            }
        )
    work_items = value.get("work_items") if isinstance(value.get("work_items"), list) else []
    task_ref = next(
        (
            str(item.get("work_id"))
            for item in work_items
            if isinstance(item, dict) and item.get("work_id")
        ),
        None,
    )
    details: dict[str, Any] = {
        "status": status or None,
        "next": _list(brief.get("next"), limit=3),
        "gaps": _list(brief.get("gaps"), limit=8),
        "human_action": brief.get("human_action"),
        "evidence": evidence_details,
        "classification": {
            key: value.get("classification", {}).get(key)
            for key in ("work_type", "subject", "confidence", "origin")
            if isinstance(value.get("classification"), dict)
            and value.get("classification", {}).get(key) is not None
        },
    }
    details = {key: item for key, item in details.items() if item not in (None, [], {})}
    return {
        "kind": kind,
        "importance": "blocking" if kind == "BLOCKER_RAISED" else "notable",
        "access": max(
            (item["access"] for item in evidence_details),
            key=_EVIDENCE_ACCESS_ORDER.__getitem__,
            default="local",
        ),
        "occurred_at": source.get("created_at"),
        "headline": headline,
        "source": {
            "opaque_ref": source.get("session_ref"),
            "source_revision": source.get("source_revision"),
            "provider_event_kind": "brief-spec-delivery",
            "harness": source.get("harness"),
            "adapter_version": source.get("adapter_version"),
        },
        "method_context": {
            "method": "general",
            **({"task_ref": task_ref} if task_ref else {}),
        },
        "entity_refs": [task_ref] if task_ref else [],
        "evidence_ids": evidence[:64],
        "details": details,
    }


def _task_spec(value: dict[str, Any]) -> dict[str, Any]:
    task_ref = _text(
        value.get("task_id") or value.get("id") or value.get("handoff_id"),
        fallback="task-spec:unidentified",
        limit=160,
    )
    intent_ref = value.get("intent_ref") or value.get("parent_intent")
    return {
        "kind": "TASK_CREATED",
        "importance": "notable",
        "access": str(value.get("access") or "local"),
        "occurred_at": value.get("created_at") or value.get("occurred_at"),
        "headline": _text(
            value.get("goal") or value.get("intent") or value.get("title"),
            fallback="Task-Spec task contract observed",
        ),
        "source": {
            "opaque_ref": value.get("handoff_id") or value.get("task_id"),
            "source_revision": value.get("source_revision"),
            "provider_event_kind": str(value.get("kind") or "task-spec"),
        },
        "method_context": {
            "method": "task-spec",
            "phase": _slug(value.get("phase"), fallback="declared"),
            "task_ref": task_ref,
            **({"intent_ref": str(intent_ref)[:160]} if intent_ref else {}),
        },
        "entity_refs": [task_ref],
        "evidence_ids": _list(value.get("evidence_ids"), limit=64),
        "details": {
            "constraints": _list(value.get("constraints"), limit=16),
            "acceptance": _list(value.get("acceptance"), limit=16),
        },
    }


def _seamwise(value: dict[str, Any]) -> dict[str, Any]:
    intent_ref = _text(
        value.get("intent_id") or value.get("id"), fallback="seamwise:unidentified", limit=160
    )
    has_intent = bool(value.get("intent") or value.get("goal"))
    return {
        "kind": "INTENT_DECLARED" if has_intent else "PLAN_CREATED",
        "importance": "notable",
        "access": str(value.get("access") or "local"),
        "occurred_at": value.get("created_at") or value.get("occurred_at"),
        "headline": _text(
            value.get("intent") or value.get("goal") or value.get("title"),
            fallback="Seamwise plan observed",
        ),
        "source": {
            "opaque_ref": value.get("intent_id") or value.get("id"),
            "source_revision": value.get("source_revision"),
            "provider_event_kind": str(value.get("kind") or "seamwise"),
        },
        "method_context": {
            "method": "seamwise",
            "phase": _slug(value.get("phase"), fallback="intent"),
            "intent_ref": intent_ref,
        },
        "entity_refs": [intent_ref],
        "evidence_ids": _list(value.get("evidence_ids"), limit=64),
        "details": {
            "seam_refs": _list(value.get("seams"), limit=32),
            "next": _list(value.get("next"), limit=3),
        },
    }


def _converge(value: dict[str, Any]) -> dict[str, Any]:
    provider_kind = str(value.get("event_kind") or value.get("kind") or "converge")
    normalized_kind = provider_kind.upper().replace("-", "_").replace(" ", "_")
    kind = normalized_kind if normalized_kind in EVENT_KINDS else "HUMAN_NOTE"
    task_ref = value.get("task_ref") or value.get("task_id")
    return {
        "kind": kind,
        "importance": str(value.get("importance") or "notable"),
        "access": str(value.get("access") or "local"),
        "occurred_at": value.get("occurred_at") or value.get("created_at"),
        "headline": _text(
            value.get("headline") or value.get("outcome") or value.get("event"),
            fallback="Converge lifecycle event observed",
        ),
        "source": {
            "opaque_ref": value.get("receipt_id") or value.get("event_id"),
            "source_revision": value.get("source_revision"),
            "provider_event_kind": provider_kind,
        },
        "method_context": {
            "method": "converge",
            "phase": _slug(value.get("phase"), fallback="execution"),
            **({"task_ref": str(task_ref)[:160]} if task_ref else {}),
            **({"intent_ref": str(value["intent_ref"])[:160]} if value.get("intent_ref") else {}),
        },
        "entity_refs": [str(task_ref)[:160]] if task_ref else [],
        "evidence_ids": _list(value.get("evidence_ids"), limit=64),
        "details": {
            "authorization_ref": value.get("authorization_ref"),
            "settlement_ref": value.get("settlement_ref"),
            "human_action": value.get("human_action"),
            "next": _list(value.get("next"), limit=3),
        },
    }


def _provenance(value: dict[str, Any], provider: str) -> dict[str, Any]:
    locator = _text(
        value.get("locator") or value.get("url"), fallback=f"{provider}:unavailable", limit=2048
    )
    return {
        "kind": "EVIDENCE_ADDED",
        "importance": "informational",
        "access": str(value.get("access") or "local"),
        "occurred_at": value.get("retrieved_at") or value.get("observed_at"),
        "headline": _text(
            value.get("headline") or value.get("title"),
            fallback=f"Evidence normalized from {provider}",
        ),
        "source": {
            "opaque_ref": value.get("source_id") or value.get("id"),
            "provider_event_kind": str(value.get("kind") or "provenance"),
        },
        "method_context": {"method": "general"},
        "entity_refs": _list(value.get("entity_refs"), limit=64),
        "evidence_ids": [
            locator
            if locator.startswith(("https://", "http://", "file:", "commit:"))
            else f"evidence:{locator}"
        ],
        "details": {
            "provider": provider,
            "basis": str(value.get("basis") or "reported"),
            "content_sha256": value.get("content_sha256"),
            "expires_at": value.get("expires_at"),
        },
    }


def normalize_source_event(value: dict[str, Any], *, source_system: str) -> dict[str, Any]:
    """Normalize one bounded native record without retaining its original payload."""
    if value.get("kind") in EVENT_KINDS:
        return dict(value)
    kind = str(value.get("kind") or "").lower()
    schema = str(value.get("schema_version") or value.get("schema") or "").lower()
    source = source_system.strip().lower().replace("_", "-")
    if kind in {"brief-spec-delivery", "briefspec-delivery"} and schema in {"1.0", "2.0"}:
        return _delivery(value)
    if source in {"task-spec", "taskspec"} or "task-spec" in schema or "task-handoff" in kind:
        return _task_spec(value)
    if source == "seamwise" or "seamwise" in schema or "seamwise" in kind:
        return _seamwise(value)
    if source == "converge" or "converge" in schema:
        return _converge(value)
    if source in {"exa", "tavily", "firecrawl", "raft", "raft-tools"}:
        return _provenance(value, source)
    raise ValueError(
        f"Input is not a canonical event or a supported bounded source record for {source_system}"
    )
