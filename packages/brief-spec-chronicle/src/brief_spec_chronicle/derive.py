from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from briefspec.artifacts import canonical_json_bytes, sha256_bytes

from brief_spec_chronicle import __version__
from brief_spec_chronicle.sources import _EVIDENCE_ACCESS_ORDER
from brief_spec_chronicle.storage import iter_events, registered_project, verify_chain


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _within(event: dict[str, Any], since: str | None, until: str | None) -> bool:
    occurred = _timestamp(str(event["occurred_at"]))
    return not (
        (since is not None and occurred < _timestamp(since))
        or (until is not None and occurred > _timestamp(until))
    )


def _task_ref(event: dict[str, Any]) -> str | None:
    method = event.get("method_context", {})
    if not isinstance(method, dict):
        return None
    value = method.get("task_ref")
    return str(value) if value else None


def _intent_ref(event: dict[str, Any]) -> str | None:
    method = event.get("method_context", {})
    if not isinstance(method, dict):
        return None
    value = method.get("intent_ref")
    return str(value) if value else None


def derive_relations(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    allowed = {
        "implements",
        "depends_on",
        "supersedes",
        "blocked_by",
        "contradicts",
        "verifies",
        "caused_by",
        "accepted_by",
        "learned_from",
    }

    def add(
        source_ref: str, relation: str, target_ref: str, event: dict[str, Any], rule: str
    ) -> None:
        key = (source_ref, relation, target_ref, str(event["event_id"]))
        if not source_ref or not target_ref or relation not in allowed or key in seen:
            return
        seen.add(key)
        record = {
            "source_ref": source_ref,
            "relation": relation,
            "target_ref": target_ref,
            "source_event_ids": [event["event_id"]],
            "confidence": "high",
            "basis": "direct" if rule == "explicit.relation" else "derived",
            "rule_id": rule,
            "review_state": "observed" if rule == "explicit.relation" else "derived",
        }
        record["relation_id"] = "bsr-" + sha256_bytes(canonical_json_bytes(record))[:24]
        values.append(record)

    for event in events:
        details = event.get("details", {})
        explicit = details.get("relations", []) if isinstance(details, dict) else []
        for relation in explicit if isinstance(explicit, list) else []:
            if isinstance(relation, dict):
                add(
                    str(relation.get("source_ref", "")),
                    str(relation.get("relation", "")),
                    str(relation.get("target_ref", "")),
                    event,
                    "explicit.relation",
                )
        task_ref = _task_ref(event)
        intent_ref = _intent_ref(event)
        if event["kind"] == "TASK_STARTED" and task_ref and intent_ref:
            add(task_ref, "implements", intent_ref, event, "event.task-started-intent")
        if event["kind"] == "TASK_ACCEPTED" and task_ref:
            add(task_ref, "accepted_by", event["event_id"], event, "event.task-accepted")
        if event["kind"] == "EVIDENCE_ADDED":
            for entity in event.get("entity_refs", []):
                for evidence in event.get("evidence_ids", []):
                    add(str(entity), "verifies", str(evidence), event, "event.evidence-added")
        if event["kind"] == "INTENT_REVISED":
            previous = details.get("supersedes") if isinstance(details, dict) else None
            current = intent_ref or (event.get("entity_refs") or [None])[0]
            if current and previous:
                add(str(current), "supersedes", str(previous), event, "event.intent-revised")
    return sorted(values, key=lambda item: item["relation_id"])


def derive_drift(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    drifts: list[dict[str, Any]] = []
    active_blockers: dict[str, dict[str, Any]] = {}
    active_tasks: dict[str, dict[str, Any]] = {}
    completed_tasks: dict[str, dict[str, Any]] = {}
    accepted_tasks: set[str] = set()
    recorded_decisions = {
        str(event.get("details", {}).get("decision_id"))
        for event in events
        if event["kind"] == "DECISION_RECORDED" and event.get("details", {}).get("decision_id")
    }
    revisions = [
        event
        for event in events
        if event["kind"] == "INTENT_REVISED"
        and str(event.get("details", {}).get("decision_id")) in recorded_decisions
    ]

    def add(
        event: dict[str, Any],
        category: str,
        observed: str,
        expected: str,
        severity: str,
        rule_id: str,
    ) -> None:
        record = {
            "category": category,
            "severity": severity,
            "expected": expected,
            "observed": observed,
            "basis": "derived",
            "source_event_ids": [event["event_id"]],
            "rule_id": rule_id,
            "human_action": event.get("details", {}).get("human_action"),
            "disposition": "open",
        }
        record["drift_id"] = "bsdft-" + sha256_bytes(canonical_json_bytes(record))[:24]
        drifts.append(record)

    for event in events:
        kind = event["kind"]
        task_ref = _task_ref(event)
        details = event.get("details", {})
        method = event.get("method_context", {})
        if details.get("scope_status") == "exceeds-accepted" or details.get("scope_exceeds_ref"):
            add(
                event,
                "scope",
                "Observed scope exceeds an accepted intent or task constraint",
                "Execution remains in scope or records a human-approved pivot",
                "needs-attention",
                "drift.scope-exceeds-accepted",
            )
        expected_phase = details.get("expected_phase")
        if expected_phase and method.get("phase") != expected_phase:
            add(
                event,
                "phase",
                f"Source phase is {method.get('phase') or 'unavailable'}",
                f"Source phase is {expected_phase}",
                "needs-attention",
                "drift.method-phase-conflict",
            )
        expected_revision = details.get("expected_source_revision")
        observed_revision = event.get("source", {}).get("source_revision")
        if expected_revision and observed_revision != expected_revision:
            add(
                event,
                "source-revision",
                f"Observed source revision is {observed_revision or 'unavailable'}",
                f"Source revision remains {expected_revision}",
                "needs-attention",
                "drift.source-revision-mismatch",
            )
        expected_hash = details.get("expected_sha256")
        observed_hash = details.get("observed_sha256")
        if details.get("hash_status") == "mismatch" or (
            expected_hash and observed_hash and expected_hash != observed_hash
        ):
            add(
                event,
                "artifact-integrity",
                "Observed artifact hash does not match the declared source hash",
                "Artifact hash remains equal to its accepted source hash",
                "blocking",
                "drift.artifact-hash-mismatch",
            )
        if kind == "INTENT_REVISED" and str(details.get("decision_id")) not in recorded_decisions:
            add(
                event,
                "decision",
                "Intent revision has no matching human decision receipt",
                "A pivot carries a recorded human decision identifier",
                "blocking",
                "drift.pivot-without-decision",
            )
        if kind == "BLOCKER_RAISED":
            for entity in event.get("entity_refs", []) or ([task_ref] if task_ref else []):
                active_blockers[str(entity)] = event
        elif kind == "BLOCKER_CLEARED":
            for entity in event.get("entity_refs", []) or ([task_ref] if task_ref else []):
                active_blockers.pop(str(entity), None)
        elif kind == "TASK_STARTED" and task_ref:
            active_tasks[task_ref] = event
            if method.get("method") == "converge" and not details.get("authorization_ref"):
                add(
                    event,
                    "authority",
                    "Converge task started without an authorization reference",
                    "Execution carries a bounded authorization reference",
                    "needs-attention",
                    "drift.converge-authorization",
                )
        elif kind in {"TASK_COMPLETED", "TASK_FAILED", "TASK_REJECTED"} and task_ref:
            active_tasks.pop(task_ref, None)
            if kind == "TASK_COMPLETED":
                completed_tasks[task_ref] = event
            if kind == "TASK_COMPLETED" and not event.get("evidence_ids"):
                add(
                    event,
                    "evidence",
                    "Task completion has no supporting evidence IDs",
                    "Completion links material proof",
                    "needs-attention",
                    "drift.completion-without-evidence",
                )
            if kind == "TASK_COMPLETED" and task_ref in active_blockers:
                add(
                    event,
                    "sequence",
                    "Task completed while a blocker remained active",
                    "Blocker is cleared or explicitly superseded before completion",
                    "blocking",
                    "drift.bypassed-blocker",
                )
        elif kind == "TASK_ACCEPTED" and task_ref:
            accepted_tasks.add(task_ref)
        elif kind == "DRIFT_DETECTED":
            record = {
                "drift_id": (event.get("entity_refs") or [event["event_id"]])[0],
                "category": details.get("category", "reported"),
                "severity": details.get("severity", event["importance"]),
                "expected": details.get("expected", "Declared project intent"),
                "observed": event["headline"],
                "basis": "direct",
                "source_event_ids": [event["event_id"]],
                "rule_id": "event.drift-detected",
                "human_action": details.get("human_action"),
                "disposition": "open",
            }
            drifts.append(record)
        elif kind == "DRIFT_RESOLVED":
            targets = set(event.get("entity_refs", []))
            for drift in drifts:
                if drift["drift_id"] in targets:
                    drift["disposition"] = "resolved"
                    drift.setdefault("source_event_ids", []).append(event["event_id"])

    for task_ref, event in completed_tasks.items():
        if task_ref not in accepted_tasks:
            add(
                event,
                "acceptance",
                "Task completion has no independent acceptance event",
                "Completed work is accepted or remains visibly awaiting acceptance",
                "needs-attention",
                "drift.completion-without-acceptance",
            )
    for task_ref, event in active_tasks.items():
        parent_ref = event.get("method_context", {}).get("parent_ref")
        if parent_ref and parent_ref in completed_tasks:
            add(
                event,
                "sequence",
                f"Task {task_ref} remains active after parent {parent_ref} completed",
                "Child work settles before or with its parent",
                "needs-attention",
                "drift.active-child-after-parent",
            )

    if revisions:
        latest_revision = revisions[-1]
        superseded = latest_revision.get("details", {}).get("resolves_drift_ids", [])
        for drift in drifts:
            if drift["drift_id"] in superseded:
                drift["disposition"] = "superseded-by-pivot"
                drift["source_event_ids"].append(latest_revision["event_id"])
    return sorted(drifts, key=lambda item: item["drift_id"])


def _decisions(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    requested: dict[str, dict[str, Any]] = {}
    recorded: list[dict[str, Any]] = []
    for event in events:
        details = event.get("details", {})
        refs = event.get("entity_refs", [])
        decision_id = str(details.get("decision_id") or (refs[0] if refs else event["event_id"]))
        if event["kind"] == "DECISION_REQUESTED":
            requested[decision_id] = {
                "decision_id": decision_id,
                "question": details.get("question", event["headline"]),
                "alternatives": details.get("alternatives", []),
                "state": "requested",
                "source_event_ids": [event["event_id"]],
            }
        elif event["kind"] == "DECISION_RECORDED":
            value = requested.pop(decision_id, {"decision_id": decision_id, "question": None})
            value.update(
                {
                    "choice": details.get("choice"),
                    "owner": details.get("owner"),
                    "rationale": details.get("rationale"),
                    "consequences": details.get("consequences", []),
                    "state": "recorded",
                    "source_event_ids": [*value.get("source_event_ids", []), event["event_id"]],
                }
            )
            recorded.append(value)
    return [*recorded, *requested.values()]


def _lessons(events: list[dict[str, Any]], decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lessons: dict[str, dict[str, Any]] = {}
    for event in events:
        if event["kind"] != "LESSON_PROPOSED":
            continue
        details = event.get("details", {})
        lesson_id = str(
            details.get("lesson_id") or (event.get("entity_refs") or [event["event_id"]])[0]
        )
        lessons[lesson_id] = {
            "lesson_id": lesson_id,
            "observation": details.get("observation", event["headline"]),
            "applicability": details.get("applicability", "project"),
            "confidence": details.get("confidence", "low"),
            "destination": details.get("destination"),
            "review_state": "proposed",
            "evidence_ids": event.get("evidence_ids", []),
            "source_event_ids": [event["event_id"]],
        }
    for decision in decisions:
        decision_id = str(decision.get("decision_id", ""))
        if not decision_id.startswith("lesson-review:"):
            continue
        lesson_id = decision_id.split(":", 1)[1]
        if lesson_id in lessons:
            lessons[lesson_id]["review_state"] = str(decision.get("choice", "recorded"))
            lessons[lesson_id]["source_event_ids"].extend(decision.get("source_event_ids", []))
    return list(lessons.values())


def build_snapshot(
    project: Path,
    *,
    since: str | None = None,
    until: str | None = None,
    created_at: str | None = None,
    ledger_cutoff: str | None = None,
) -> dict[str, Any]:
    registration = registered_project(project)
    all_events = list(iter_events(registration["project_id"]))
    chain_errors = verify_chain(all_events)
    if chain_errors:
        raise ValueError("; ".join(chain_errors))
    observed_events = all_events
    if ledger_cutoff is not None:
        cutoff_index = next(
            (index for index, event in enumerate(all_events) if event["event_id"] == ledger_cutoff),
            None,
        )
        if cutoff_index is None:
            raise ValueError(f"Unknown Chronicle ledger cutoff event: {ledger_cutoff}")
        observed_events = all_events[: cutoff_index + 1]
    selected = sorted(
        (event for event in observed_events if _within(event, since, until)),
        key=lambda event: (event["occurred_at"], event["event_id"]),
    )
    if not selected:
        raise ValueError("No Chronicle events fall within the requested window")
    canonical_created_at = created_at or observed_events[-1]["observed_at"]
    intents = [
        {
            "event_id": event["event_id"],
            "intent_ref": _intent_ref(event) or (event.get("entity_refs") or [None])[0],
            "headline": event["headline"],
            "kind": event["kind"],
            "evidence_ids": event.get("evidence_ids", []),
            "source_event_ids": [event["event_id"]],
        }
        for event in selected
        if event["kind"] in {"INTENT_DECLARED", "INTENT_REVISED"}
    ]
    phases = [
        event["method_context"].get("phase")
        for event in selected
        if event.get("method_context", {}).get("phase")
    ]
    decisions = _decisions(selected)
    lessons = _lessons(selected, decisions)
    blockers: dict[str, dict[str, Any]] = {}
    for event in selected:
        if event["kind"] == "BLOCKER_RAISED":
            blocker_id = str((event.get("entity_refs") or [event["event_id"]])[0])
            blockers[blocker_id] = {
                "blocker_id": blocker_id,
                "headline": event["headline"],
                "human_action": event.get("details", {}).get("human_action"),
                "source_event_ids": [event["event_id"]],
            }
        elif event["kind"] == "BLOCKER_CLEARED":
            for blocker_id in event.get("entity_refs", []):
                blockers.pop(str(blocker_id), None)
    milestones = [
        {
            "kind": event["kind"],
            "headline": event["headline"],
            "occurred_at": event["occurred_at"],
            "evidence_ids": event.get("evidence_ids", []),
            "source_event_ids": [event["event_id"]],
        }
        for event in selected
        if event["kind"] in {"TASK_COMPLETED", "TASK_ACCEPTED", "ARTIFACT_CREATED"}
    ]
    detours = [
        {
            "headline": event["headline"],
            "reason": event.get("details", {}).get("reason"),
            "source_event_ids": [event["event_id"]],
        }
        for event in selected
        if event.get("details", {}).get("signal") == "detour"
    ]
    human_actions = [
        {
            "headline": event["headline"],
            "action": event.get("details", {}).get("human_action"),
            "blocking": event["importance"] == "blocking",
            "source_event_ids": [event["event_id"]],
        }
        for event in selected
        if event.get("details", {}).get("human_action")
    ]
    next_actions: list[str] = []
    for event in reversed(selected):
        candidates = event.get("details", {}).get("next", [])
        if isinstance(candidates, str):
            candidates = [candidates]
        if isinstance(candidates, list):
            for item in candidates:
                if isinstance(item, str) and item not in next_actions:
                    next_actions.append(item)
                if len(next_actions) == 3:
                    break
        if len(next_actions) == 3:
            break
    relations = derive_relations(selected)
    drift = derive_drift(selected)
    if until is not None:
        newer = [
            event
            for event in observed_events
            if _timestamp(event["occurred_at"]) > _timestamp(until)
        ]
        if newer:
            stale = {
                "category": "stale-report",
                "severity": "needs-attention",
                "expected": "The report includes all material events through its ledger cutoff",
                "observed": f"{len(newer)} newer material event(s) fall after the report window",
                "basis": "derived",
                "source_event_ids": [event["event_id"] for event in newer],
                "rule_id": "drift.report-window-stale",
                "human_action": "Generate a current Chronicle snapshot",
                "disposition": "open",
            }
            stale["drift_id"] = "bsdft-" + sha256_bytes(canonical_json_bytes(stale))[:24]
            drift.append(stale)
            drift.sort(key=lambda item: item["drift_id"])
    evidence_records: dict[tuple[str, str, str], dict[str, Any]] = {}
    evidence_access: dict[str, str] = {}
    for event in selected:
        details = event.get("details", {})
        reference_details: dict[str, list[dict[str, Any]]] = {}
        references = details.get("evidence")
        for reference in references if isinstance(references, list) else ():
            if isinstance(reference, dict) and isinstance(reference.get("evidence_id"), str):
                reference_details.setdefault(reference["evidence_id"], []).append(reference)
        for evidence_id in event.get("evidence_ids", []):
            for reference in reference_details.get(evidence_id) or (details,):
                access = reference.get("access", event["access"])
                if not isinstance(access, str) or access not in _EVIDENCE_ACCESS_ORDER:
                    access = "private"
                prior_access = evidence_access.get(evidence_id, access)
                evidence_access[evidence_id] = max(
                    prior_access, access, key=_EVIDENCE_ACCESS_ORDER.__getitem__
                )
                content_sha256 = reference.get("content_sha256")
                expires_at = reference.get("expires_at")
                key = (evidence_id, str(content_sha256 or ""), str(expires_at or ""))
                record = evidence_records.get(key)
                if record is None:
                    record = {
                        "evidence_id": evidence_id,
                        "access": access,
                        "expires_at": expires_at,
                        "content_sha256": content_sha256,
                        "source_event_ids": [],
                    }
                    evidence_records[key] = record
                if event["event_id"] not in record["source_event_ids"]:
                    record["source_event_ids"].append(event["event_id"])
    for record in evidence_records.values():
        record["access"] = evidence_access[record["evidence_id"]]
    snapshot = {
        "schema_version": "brief-spec-chronicle/1.0",
        "kind": "brief-spec-project-chronicle",
        "chronicle_version": __version__,
        "project": {
            "project_id": registration["project_id"],
            "name": registration["name"],
            "root_sha256": registration["root_sha256"],
        },
        "window": {
            "since": since or selected[0]["occurred_at"],
            "until": until or selected[-1]["occurred_at"],
            "created_at": canonical_created_at,
            "ledger_cutoff_event_id": observed_events[-1]["event_id"],
        },
        "ledger_head_hash": observed_events[-1]["event_hash"],
        "source_event_ids": [event["event_id"] for event in selected],
        "intent_anchors": intents,
        "current_state": {
            "method": selected[-1]["method_context"]["method"],
            "phase": phases[-1] if phases else None,
            "headline": selected[-1]["headline"],
            "latest_event_kind": selected[-1]["kind"],
            "source_event_ids": [selected[-1]["event_id"]],
        },
        "material_changes": [
            {
                "kind": event["kind"],
                "headline": event["headline"],
                "occurred_at": event["occurred_at"],
                "observed_at": event["observed_at"],
                "late_arrival": _timestamp(event["observed_at"]) > _timestamp(event["occurred_at"]),
                "source_event_ids": [event["event_id"]],
            }
            for event in selected
        ],
        "milestones": milestones,
        "detours": detours,
        "drift": drift,
        "decisions": decisions,
        "blockers": list(blockers.values()),
        "lessons": lessons,
        "human_actions": human_actions,
        "next_actions": next_actions,
        "relations": relations,
        "evidence_ids": sorted(
            {str(item) for event in selected for item in event.get("evidence_ids", [])}
        ),
        "evidence": [evidence_records[key] for key in sorted(evidence_records)],
    }
    snapshot["canonical_sha256"] = sha256_bytes(canonical_json_bytes(snapshot))
    return snapshot


def validate_snapshot(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["Chronicle snapshot must be an object"]
    errors: list[str] = []
    if value.get("schema_version") != "brief-spec-chronicle/1.0":
        errors.append("Chronicle schema_version is invalid")
    if value.get("kind") != "brief-spec-project-chronicle":
        errors.append("Chronicle kind is invalid")
    allowed = {
        "schema_version",
        "kind",
        "chronicle_version",
        "project",
        "window",
        "ledger_head_hash",
        "source_event_ids",
        "intent_anchors",
        "current_state",
        "material_changes",
        "milestones",
        "detours",
        "drift",
        "decisions",
        "blockers",
        "lessons",
        "human_actions",
        "next_actions",
        "relations",
        "evidence_ids",
        "evidence",
        "canonical_sha256",
    }
    unexpected = sorted(set(value) - allowed)
    if unexpected:
        errors.append("Chronicle contains unknown field(s): " + ", ".join(unexpected))
    for field in (
        "project",
        "window",
        "current_state",
        "source_event_ids",
        "intent_anchors",
        "material_changes",
        "milestones",
        "detours",
        "drift",
        "decisions",
        "blockers",
        "lessons",
        "human_actions",
        "next_actions",
        "relations",
        "evidence_ids",
        "evidence",
    ):
        if field not in value:
            errors.append(f"Chronicle {field} is required")
    project = value.get("project")
    if not isinstance(project, dict) or not re.fullmatch(
        r"bscp-[0-9a-f]{24}", str(project.get("project_id", ""))
    ):
        errors.append("Chronicle project.project_id is invalid")
    window = value.get("window")
    if not isinstance(window, dict):
        errors.append("Chronicle window must be an object")
    else:
        for field in ("since", "until", "created_at"):
            try:
                _timestamp(str(window[field]))
            except (KeyError, ValueError):
                errors.append(f"Chronicle window.{field} is invalid")
        if not re.fullmatch(r"bse-[0-9a-f]{24}", str(window.get("ledger_cutoff_event_id", ""))):
            errors.append("Chronicle window.ledger_cutoff_event_id is invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", str(value.get("ledger_head_hash", ""))):
        errors.append("Chronicle ledger_head_hash is invalid")
    for field in (
        "source_event_ids",
        "intent_anchors",
        "material_changes",
        "milestones",
        "detours",
        "drift",
        "decisions",
        "blockers",
        "lessons",
        "human_actions",
        "next_actions",
        "relations",
        "evidence_ids",
        "evidence",
    ):
        if field in value and not isinstance(value[field], list):
            errors.append(f"Chronicle {field} must be an array")
    material_fields = (
        "intent_anchors",
        "material_changes",
        "milestones",
        "detours",
        "drift",
        "decisions",
        "blockers",
        "lessons",
        "human_actions",
        "relations",
        "evidence",
    )
    for field in material_fields:
        for index, item in enumerate(value.get(field, [])):
            if not isinstance(item, dict) or not item.get("source_event_ids"):
                errors.append(f"Chronicle {field}[{index}] has no source event IDs")
    for index, item in enumerate(value.get("evidence", [])):
        if not isinstance(item, dict):
            continue
        if item.get("access") not in {"local", "private", "public"}:
            errors.append(f"Chronicle evidence[{index}] access is invalid")
        expires_at = item.get("expires_at")
        if expires_at:
            try:
                _timestamp(str(expires_at))
            except ValueError:
                errors.append(f"Chronicle evidence[{index}] expiry is invalid")
        content_hash = item.get("content_sha256")
        if content_hash is not None and not re.fullmatch(r"[0-9a-f]{64}", str(content_hash)):
            errors.append(f"Chronicle evidence[{index}] content hash is invalid")
    current_state = value.get("current_state")
    if not isinstance(current_state, dict) or not current_state.get("source_event_ids"):
        errors.append("Chronicle current_state has no source event IDs")
    expected = dict(value)
    actual = expected.pop("canonical_sha256", None)
    if actual != sha256_bytes(canonical_json_bytes(expected)):
        errors.append("Chronicle canonical hash does not match its fields")
    return errors
